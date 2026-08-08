"""Tests for bartlebot.card_kinds.

Classification mirrors SprintyCombat's own predicates, so these pin the four
distinctions that matter: a blade and a charm sit on the same effect types and
differ only by who is targeted and the sign of the param, and the same is true
of a ward and a trap. Getting either pair backwards would label a deck's cards
wrongly and suggest the wrong replacements.

No client needed -- the classifier takes plain (effect_type, target, param,
rounds) tuples, which is exactly what deck_io reads off a SpellTemplate.
"""

from __future__ import annotations

import pytest
from wizwalker.extensions.wizsprinter.sprinty_combat import (
    ally_targets,
    aoe_targets,
    charm_effects,
    enemy_targets,
    ward_effects,
)
from wizwalker.memory import EffectTarget, SpellEffects

from bartlebot.card_kinds import (
    AURA,
    BLADE,
    CHARM,
    TRAP,
    WARD,
    classify_effect,
    classify_effects,
    describe_kind,
)

_CHARM = SpellEffects.modify_outgoing_damage
_WARD = SpellEffects.modify_incoming_damage
_SELF = EffectTarget.self
_ENEMY = EffectTarget.enemy_single
_ALLY_TEAM = EffectTarget.friendly_team
_ENEMY_TEAM = EffectTarget.enemy_team


def test_the_sets_we_classify_against_are_the_dsls_own():
    """If these drift, a card we call a blade stops being one the DSL would pick."""
    assert _CHARM in charm_effects
    assert _WARD in ward_effects
    assert _SELF in ally_targets
    assert _ENEMY in enemy_targets
    assert _ALLY_TEAM in aoe_targets


# ---------------------------------------------------------------------------
# The four single-target categories
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("effect_type", "target", "param", "expected"),
    [
        (_CHARM, _SELF, 35, BLADE),  # boosts my outgoing damage
        (_CHARM, _ENEMY, -35, CHARM),  # weakness on them
        (_WARD, _SELF, -50, WARD),  # shield on me
        (_WARD, _ENEMY, 30, TRAP),  # trap on them
    ],
)
def test_the_four_categories(effect_type, target, param, expected):
    assert classify_effect(effect_type, target, param, 0) == expected


def test_a_blade_and_a_charm_differ_only_by_target_and_sign():
    """Same effect type; flipping both is what turns one into the other."""
    assert classify_effect(_CHARM, _SELF, 35, 0) == BLADE
    assert classify_effect(_CHARM, _ENEMY, -35, 0) == CHARM


def test_a_ward_and_a_trap_differ_only_by_target_and_sign():
    assert classify_effect(_WARD, _SELF, -50, 0) == WARD
    assert classify_effect(_WARD, _ENEMY, 30, 0) == TRAP


def test_a_positive_charm_on_an_enemy_is_neither():
    """Nonsense combinations get no label rather than a guess."""
    assert classify_effect(_CHARM, _ENEMY, 35, 0) is None


def test_a_negative_charm_on_an_ally_is_neither():
    assert classify_effect(_CHARM, _SELF, -35, 0) is None


# ---------------------------------------------------------------------------
# AoE and duration
# ---------------------------------------------------------------------------


def test_a_team_blade_is_not_a_single_target_blade():
    """The DSL's <blade> excludes AoE targets, so this must too."""
    assert classify_effect(_CHARM, _ALLY_TEAM, 35, 0) is None


def test_a_team_trap_is_not_a_single_target_trap():
    assert classify_effect(_WARD, _ENEMY_TEAM, 30, 0) is None


def test_a_lasting_self_buff_is_an_aura_not_a_blade():
    """Rounds > 0 is the whole difference; the effect type is identical."""
    assert classify_effect(_CHARM, _SELF, 35, 4) == AURA


def test_a_ward_effect_can_also_be_an_aura():
    assert classify_effect(_WARD, _SELF, -20, 4) == AURA


def test_a_lasting_effect_on_an_enemy_is_not_an_aura():
    assert classify_effect(_CHARM, _ENEMY, -35, 4) is None


# ---------------------------------------------------------------------------
# Whole spells
# ---------------------------------------------------------------------------


def test_a_spell_is_named_for_its_first_classifiable_effect():
    kind = classify_effects([(_CHARM, _ENEMY, 35, 0), (_CHARM, _SELF, 35, 0)])

    assert kind == BLADE


def test_a_spell_with_no_classifiable_effect_is_unlabelled():
    """An attack spell -- and there is no stand-in for one, so saying nothing is right."""
    assert classify_effects([(SpellEffects.damage, _ENEMY, 500, 0)]) is None


def test_a_spell_with_no_effects_at_all_is_unlabelled():
    assert classify_effects([]) is None


# ---------------------------------------------------------------------------
# Wording
# ---------------------------------------------------------------------------


def test_kinds_are_described_for_a_message():
    assert describe_kind(BLADE) == "a blade"
    assert describe_kind(AURA) == "an aura"


def test_an_unlabelled_card_describes_as_nothing():
    """Callers concatenate this, so it must be empty rather than "None"."""
    assert describe_kind(None) == ""
    assert describe_kind("nonsense") == ""
