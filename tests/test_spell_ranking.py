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
    Amount,
    BestOf,
    CardFacts,
    CardSource,
    OneOf,
    expected_value,
    rank_facts,
    ranker_for,
)

AOE = ranker_for(TemplateSpell([SpellType.type_aoe]))
DAMAGE = ranker_for(TemplateSpell([SpellType.type_damage]))
BLADE = ranker_for(TemplateSpell([SpellType.type_blade]))
WARD = ranker_for(TemplateSpell([SpellType.type_ward]))
MOD_DAMAGE = ranker_for(TemplateSpell([SpellType.type_mod_damage]))


def hit(*params: int) -> AllOf:
    """A card whose effects all land."""
    return AllOf(tuple(Amount(p) for p in params))


def spread(*params: int) -> AllOf:
    """A card that rolls one of `params`, as a random-damage spell does."""
    return AllOf((OneOf(tuple(Amount(p) for p in params)),))


def applies(*params: int) -> BestOf:
    """A buff card, whose effects land together but help one attack each."""
    return BestOf(tuple(Amount(p) for p in params))


def order(names: list[str], facts: list[CardFacts], ranker=AOE) -> list[str]:
    return [names[i] for i in rank_facts(facts, ranker)]


class TestExpectedValue:
    def test_effects_that_all_land_are_summed(self):
        # Fire Dragon: 440 on impact plus 330 over 3 rounds. effect_param holds
        # the DoT total, not the per-round tick, so the two just add.
        assert expected_value(hit(440, 330)) == 770

    def test_a_random_spread_is_averaged_not_summed(self):
        # Enchanted Humongofrog rolls 575-615 in steps of 10. Summing these
        # scored it 2975 and ranked it above spells it loses to.
        assert expected_value(spread(575, 585, 595, 605, 615)) == 595

    def test_a_card_with_nothing_that_counts_scores_zero(self):
        assert expected_value(AllOf()) == 0

    def test_an_empty_spread_scores_zero_rather_than_dividing_by_zero(self):
        assert expected_value(OneOf()) == 0

    def test_a_spread_nested_under_effects_that_land_together(self):
        # An initial fixed hit plus a random tail: 400 + mean(100, 200) = 550.
        node = AllOf((Amount(400), OneOf((Amount(100), Amount(200)))))
        assert expected_value(node) == 550

    def test_effects_that_land_together_but_help_separately_take_the_best(self):
        # Sharpened Epiphany Blade puts 55% on Myth and 55% on Storm. Whatever
        # attack follows is helped by one of them, never both, so the card is
        # worth 55 and not the 110 that summing scored it.
        assert expected_value(applies(55, 55)) == 55

    def test_the_best_of_nothing_scores_zero(self):
        assert expected_value(BestOf()) == 0

    def test_a_non_node_is_rejected_rather_than_scored_as_zero(self):
        with pytest.raises(TypeError):
            expected_value(755)


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

    def test_equal_damage_breaks_on_the_cheaper_spell(self):
        names = ["six pips", "four pips"]
        facts = [CardFacts(False, hit(500), pip_cost=6), CardFacts(False, hit(500), pip_cost=4)]
        assert order(names, facts) == ["four pips", "six pips"]

    def test_ties_keep_the_order_they_were_given(self):
        # get_cards() puts enchanted cards first; a tie must not disturb that.
        facts = [CardFacts(False, hit(500)), CardFacts(False, hit(500))]
        assert rank_facts(facts, AOE) == [0, 1]

    def test_an_empty_hand_ranks_to_nothing(self):
        assert rank_facts([], AOE) == []


class TestBuffRanking:
    def test_the_strongest_blade_goes_first(self):
        # Blades stack, so there is no reason to hold the big one back.
        names = ["Storm Blade", "Sharpened Storm Blade", "Balanceblade"]
        facts = [CardFacts(score=applies(35)), CardFacts(score=applies(45)), CardFacts(score=applies(25))]
        assert order(names, facts, BLADE) == ["Sharpened Storm Blade", "Storm Blade", "Balanceblade"]

    def test_wards_rank_on_magnitude_not_signed_value(self):
        # is_ward() requires param < 0, and the caller stores the magnitude, so
        # a -70 shield must outrank a -50 one rather than the reverse.
        names = ["Tower Shield -50", "Storm Shield -70"]
        facts = [CardFacts(score=applies(50)), CardFacts(score=applies(70))]
        assert order(names, facts, WARD) == ["Storm Shield -70", "Tower Shield -50"]

    def test_flat_buffs_rank_below_percentage_buffs_whatever_the_number(self):
        # +225 flat and +35% both read as a bare int; comparing them by value
        # would hand every contest to the flat one.
        names = ["+225 flat", "+35%"]
        facts = [CardFacts(score=applies(225), is_flat=True), CardFacts(score=applies(35))]
        assert order(names, facts, BLADE) == ["+35%", "+225 flat"]

    def test_flat_buffs_still_rank_against_each_other(self):
        names = ["+100 flat", "+225 flat"]
        facts = [CardFacts(score=applies(100), is_flat=True), CardFacts(score=applies(225), is_flat=True)]
        assert order(names, facts, BLADE) == ["+225 flat", "+100 flat"]

    def test_equal_buffs_break_on_the_cheaper_card(self):
        names = ["one pip", "zero pip"]
        facts = [CardFacts(score=applies(35), pip_cost=1), CardFacts(score=applies(35), pip_cost=0)]
        assert order(names, facts, BLADE) == ["zero pip", "one pip"]

    def test_pip_cost_does_not_override_the_buff_value(self):
        names = ["free but weak", "costly and strong"]
        facts = [CardFacts(score=applies(20), pip_cost=0), CardFacts(score=applies(40), pip_cost=2)]
        assert order(names, facts, BLADE) == ["costly and strong", "free but weak"]

    def test_the_item_card_is_spent_before_the_deck_copy(self):
        # An item card cannot be enchanted and cannot be saved, so holding it
        # gains nothing.
        names = ["deck", "item"]
        facts = [
            CardFacts(score=applies(35), source=CardSource.deck),
            CardFacts(score=applies(35), source=CardSource.item),
        ]
        assert order(names, facts, BLADE) == ["item", "deck"]

    def test_the_treasure_card_is_kept_back(self):
        names = ["treasure", "deck"]
        facts = [
            CardFacts(score=applies(35), source=CardSource.treasure),
            CardFacts(score=applies(35), source=CardSource.deck),
        ]
        assert order(names, facts, BLADE) == ["deck", "treasure"]

    def test_the_enchanted_copy_is_spent_first(self):
        # It cannot be improved further; the plain one can still take an
        # enchant next round.
        names = ["plain", "enchanted"]
        facts = [
            CardFacts(score=applies(35), is_enchanted=False),
            CardFacts(score=applies(35), is_enchanted=True),
        ]
        assert order(names, facts, BLADE) == ["enchanted", "plain"]

    def test_source_outranks_the_enchanted_tiebreak(self):
        # An item card is still the one to burn, even against an enchanted
        # deck card that also cannot be improved.
        names = ["enchanted deck", "item"]
        facts = [
            CardFacts(score=applies(35), source=CardSource.deck, is_enchanted=True),
            CardFacts(score=applies(35), source=CardSource.item),
        ]
        assert order(names, facts, BLADE) == ["item", "enchanted deck"]

    def test_a_multi_school_blade_is_worth_one_of_its_blades(self):
        # A sharpened Epiphany Blade (55% Myth + 55% Storm) still beats a
        # sharpened Mythblade (45%), just not by the margin summing gave it.
        names = ["Epiphany Blade (sharpened)", "Mythblade (sharpened)"]
        facts = [
            CardFacts(score=applies(55, 55), pip_cost=2, is_enchanted=True),
            CardFacts(score=applies(45), pip_cost=0, is_enchanted=True),
        ]
        assert order(names, facts, BLADE) == ["Epiphany Blade (sharpened)", "Mythblade (sharpened)"]

    def test_a_multi_school_blade_does_not_outrank_an_equal_cheaper_one(self):
        # This is the inversion the sum caused: a plain Epiphany Blade is two
        # 45% blades, summed to 90, which buried a sharpened Mythblade offering
        # the same 45% for no pips at all.
        names = ["Epiphany Blade", "Mythblade (sharpened)"]
        facts = [
            CardFacts(score=applies(45, 45), pip_cost=2),
            CardFacts(score=applies(45), pip_cost=0, is_enchanted=True),
        ]
        assert order(names, facts, BLADE) == ["Mythblade (sharpened)", "Epiphany Blade"]

    def test_the_hand_from_the_live_run_ranks_end_to_end(self):
        # The full probe output from BartleBot#6, which is what caught this:
        # Spirit Blade puts 35% on three schools, Epiphany 55% on two.
        names = [
            "Epiphany Blade (sharpened)",
            "Spirit Blade - Amulet",
            "Epiphany Blade",
            "Mythblade (sharpened)",
            "Mythblade - Amulet",
            "Mythblade",
        ]
        facts = [
            CardFacts(score=applies(55, 55), pip_cost=2, is_enchanted=True),
            CardFacts(score=applies(35, 35, 35), pip_cost=1, source=CardSource.item),
            CardFacts(score=applies(45, 45), pip_cost=2),
            CardFacts(score=applies(45), pip_cost=0, is_enchanted=True),
            CardFacts(score=applies(40), pip_cost=0, source=CardSource.item),
            CardFacts(score=applies(35), pip_cost=0),
        ]
        assert order(names, facts, BLADE) == [
            "Epiphany Blade (sharpened)",  # 55
            "Mythblade (sharpened)",  # 45, free
            "Epiphany Blade",  # 45, two pips
            "Mythblade - Amulet",  # 40
            "Mythblade",  # 35, free
            "Spirit Blade - Amulet",  # 35, one pip
        ]

    def test_the_biggest_enchantment_wins(self):
        # mod_damage has no edge cases: higher is always better.
        names = ["Strong +30%", "Gargantuan +225", "Monstrous +20%"]
        facts = [
            CardFacts(score=applies(30)),
            CardFacts(score=applies(225), is_flat=True),
            CardFacts(score=applies(20)),
        ]
        assert order(names, facts, MOD_DAMAGE) == ["Strong +30%", "Monstrous +20%", "Gargantuan +225"]


class TestRankerLookup:
    def test_aoe_scores_only_team_targeted_effects(self):
        assert AOE.aoe_only is True

    def test_aoe_scores_damage_rather_than_its_own_predicate(self):
        # type_aoe's predicate only asks about targeting, so scoring it would
        # count every team-targeted effect including the blade half of a hybrid.
        assert AOE.score_type is SpellType.type_damage

    def test_damage_scores_every_enemy_targeted_effect(self):
        assert DAMAGE.aoe_only is False

    def test_a_template_asking_for_both_ranks_as_the_narrower_one(self):
        ranker = ranker_for(TemplateSpell([SpellType.type_damage, SpellType.type_aoe]))
        assert ranker.aoe_only is True

    def test_a_blade_outranks_the_effect_family_it_belongs_to(self):
        ranker = ranker_for(TemplateSpell([SpellType.type_out_damage, SpellType.type_blade]))
        assert ranker.score_type is SpellType.type_blade

    @pytest.mark.parametrize(
        "spell_type",
        [
            SpellType.type_blade,
            SpellType.type_trap,
            SpellType.type_charm,
            SpellType.type_ward,
            SpellType.type_mod_damage,
            SpellType.type_mod_heal,
            SpellType.type_mod_pierce,
            SpellType.type_inc_damage,
            SpellType.type_out_damage,
            SpellType.type_inc_heal,
            SpellType.type_out_heal,
            SpellType.type_pierce,
        ],
    )
    def test_every_buff_category_scores_its_own_predicate(self, spell_type):
        ranker = ranker_for(TemplateSpell([spell_type]))
        assert ranker.score_type is spell_type
        assert ranker.aoe_only is False

    @pytest.mark.parametrize(
        "spell_type",
        [
            SpellType.type_heal,
            SpellType.type_heal_self,
            SpellType.type_aura,
            SpellType.type_global,
            SpellType.type_polymorph,
            SpellType.type_shadow,
            SpellType.type_prism,
            SpellType.type_dispel,
            SpellType.type_enchant,
        ],
    )
    def test_an_unranked_category_keeps_the_callers_order(self, spell_type):
        # Heals want the target's missing health, which a sort key cannot see;
        # the rest are not ordered by magnitude in any useful way.
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
