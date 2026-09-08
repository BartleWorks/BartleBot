"""Ranking spells within a category (wizsprinter `combat_backends.spell_ranking`).

The policy lives upstream but is pinned by exact commit here, so this is where
it gets covered: the module is pure, so none of this needs a game attached.

The numbers are the ones from BartleBot#6 and its follow-up comment, read off a
live client with `tools/probe_spell_damage.py`.
"""

import pytest
from wizwalker.extensions.wizsprinter.combat_backends.combat_api import (
    SpellType,
    TemplateSpell,
)
from wizwalker.extensions.wizsprinter.combat_backends.spell_ranking import (
    AllOf,
    CardFacts,
    DamageAmount,
    OneOf,
    expected_damage,
    rank_facts,
    ranker_for,
)

AOE = ranker_for(TemplateSpell([SpellType.type_aoe]))
DAMAGE = ranker_for(TemplateSpell([SpellType.type_damage]))


def hit(*params: int) -> AllOf:
    """A card whose damage effects all land."""
    return AllOf(tuple(DamageAmount(p) for p in params))


def spread(*params: int) -> AllOf:
    """A card that rolls one of `params`, as a random-damage spell does."""
    return AllOf((OneOf(tuple(DamageAmount(p) for p in params)),))


def order(names: list[str], facts: list[CardFacts], ranker=AOE) -> list[str]:
    return [names[i] for i in rank_facts(facts, ranker)]


class TestExpectedDamage:
    def test_effects_that_all_land_are_summed(self):
        # Fire Dragon: 440 on impact plus 330 over 3 rounds. effect_param holds
        # the DoT total, not the per-round tick, so the two just add.
        assert expected_damage(hit(440, 330)) == 770

    def test_a_random_spread_is_averaged_not_summed(self):
        # Enchanted Humongofrog rolls 575-615 in steps of 10. Summing these
        # scored it 2975 and ranked it above spells it loses to.
        assert expected_damage(spread(575, 585, 595, 605, 615)) == 595

    def test_a_card_with_no_counting_damage_scores_zero(self):
        assert expected_damage(AllOf()) == 0

    def test_an_empty_spread_scores_zero_rather_than_dividing_by_zero(self):
        assert expected_damage(OneOf()) == 0

    def test_a_spread_nested_under_effects_that_land_together(self):
        # An initial fixed hit plus a random tail: 400 + mean(100, 200) = 550.
        node = AllOf((DamageAmount(400), OneOf((DamageAmount(100), DamageAmount(200)))))
        assert expected_damage(node) == 550

    def test_a_non_node_is_rejected_rather_than_scored_as_zero(self):
        with pytest.raises(TypeError):
            expected_damage(755)


class TestDamageRanking:
    def test_highest_damage_first(self):
        # The case the issue opens with: `storm lord | tempest` should not have
        # to be written out.
        names = ["Tempest", "Bunyip's Rage", "Storm Lord"]
        facts = [
            CardFacts(True, hit(80)),
            CardFacts(False, hit(645)),
            CardFacts(False, hit(755)),
        ]
        assert order(names, facts) == ["Storm Lord", "Bunyip's Rage", "Tempest"]

    def test_per_pip_spells_rank_last_however_large_their_param(self):
        # Held in reserve as a backup hit, since they cast at any pip count.
        names = ["X-pip", "small fixed"]
        facts = [CardFacts(True, hit(9999)), CardFacts(False, hit(1))]
        assert order(names, facts) == ["small fixed", "X-pip"]

    def test_a_random_spread_loses_to_the_fixed_hit_that_beats_it(self):
        # Humongofrog (expected 595) vs Drop Bear Fury (765), both enchanted.
        names = ["Humongofrog", "Drop Bear Fury"]
        facts = [
            CardFacts(False, spread(575, 585, 595, 605, 615)),
            CardFacts(False, hit(765)),
        ]
        assert order(names, facts) == ["Drop Bear Fury", "Humongofrog"]

    def test_an_enchanted_card_wins_on_its_own_param(self):
        # effect_param already includes the enchant, so enchanted cards need no
        # sort key of their own.
        names = ["Bunyip's Rage (enchanted)", "Storm Lord"]
        facts = [CardFacts(False, hit(870)), CardFacts(False, hit(755))]
        assert order(names, facts) == ["Bunyip's Rage (enchanted)", "Storm Lord"]

    def test_an_enchant_in_hand_pulls_the_bigger_base_spell_back_on_top(self):
        # Storm Lord can still take the Gargantuan; the enchanted Bunyip cannot.
        names = ["Bunyip's Rage (enchanted)", "Storm Lord"]
        facts = [
            CardFacts(False, hit(870), enchant_bonus=0),
            CardFacts(False, hit(755), enchant_bonus=225),
        ]
        assert order(names, facts) == ["Storm Lord", "Bunyip's Rage (enchanted)"]

    def test_ties_keep_the_order_they_were_given(self):
        # get_cards() puts enchanted cards first; a tie must not disturb that.
        facts = [CardFacts(False, hit(500)), CardFacts(False, hit(500))]
        assert rank_facts(facts, AOE) == [0, 1]

    def test_an_empty_hand_ranks_to_nothing(self):
        assert rank_facts([], AOE) == []


class TestRankerLookup:
    def test_aoe_scores_only_team_targeted_effects(self):
        assert AOE.aoe_only is True

    def test_damage_scores_every_enemy_targeted_effect(self):
        assert DAMAGE.aoe_only is False

    def test_a_template_asking_for_both_ranks_as_the_narrower_one(self):
        ranker = ranker_for(TemplateSpell([SpellType.type_damage, SpellType.type_aoe]))
        assert ranker.aoe_only is True

    @pytest.mark.parametrize(
        "spell_type",
        [SpellType.type_blade, SpellType.type_heal, SpellType.type_ward, SpellType.type_trap],
    )
    def test_an_unranked_category_keeps_the_callers_order(self, spell_type):
        assert ranker_for(TemplateSpell([spell_type])) is None

    def test_a_named_spell_is_not_ranked(self):
        # try_get_spell hands templates here, but a NamedSpell must pass through.
        assert ranker_for("storm lord") is None

    def test_post_selection_filters_alongside_a_category_do_not_confuse_lookup(self):
        # requirements is heterogeneous: SpellTypes plus Gambit/Clear/Echo/Swap.
        from wizwalker.extensions.wizsprinter.combat_backends.combat_api import (
            GambitSpec,
            HangingType,
        )

        template = TemplateSpell([SpellType.type_aoe, GambitSpec(HangingType.charms)])
        assert ranker_for(template) is AOE
