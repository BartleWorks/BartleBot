"""`?(deck.remaining ...)`: cards left in the main pile, and the label it is read from.

Lives in wizsprinter (`combat_backends`, `sprinty_combat`), which is pinned by exact commit
here and has no suite of its own, so this is where it gets covered -- the same arrangement
as `test_optional_enchant.py`.

`tools/probe_deck_counts.py` has the live runs these rules come from. The `CountText` label
reads `13 of 20` in English and `13 sur 20` in French, and counts the treasure pile along
with the main one. Nothing on the client counts the main pile alone, but the refill at a
round's start draws only from it, so a hand that began the round short of full means the
main pile is empty.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from lark import Lark
from wizwalker.extensions.wizsprinter import sprinty_combat
from wizwalker.extensions.wizsprinter.combat_backends.combat_api import (
    ComparisonOp,
    Condition,
    ConditionTarget,
    TargetType,
)
from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (
    TreeToConfig,
    get_sprinty_grammar,
)
from wizwalker.extensions.wizsprinter.combat_backends.config_backend import CombatConfigProvider
from wizwalker.extensions.wizsprinter.sprinty_combat import SprintyCombat, parse_card_count


@pytest.fixture(scope="module")
def condition():
    """The condition on the first priority of a one-line DSL. One grammar per module."""
    parser = Lark(get_sprinty_grammar())

    def _parse(dsl: str):
        return TreeToConfig().transform(parser.parse(dsl)).infinite_rounds[0].priorities[0].condition

    return _parse


# ---------------------------------------------------------------------------
# The label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("<center>Cards</center>\n<center>13 of 20</center>", (13, 20)),
        ("<center>Cartes</center>\n<center>13 sur 20</center>", (13, 20)),
        ("<center>Cards</center>\n<center>0 of 11</center>", (0, 11)),
        ("<center>Karten</center>\n<center>4 von 11</center>\n", (4, 11)),
    ],
)
def test_the_label_is_read_by_its_numbers_in_any_language(text, expected):
    """The old parse deleted the word "of", so every other language read (0, 0)."""
    assert parse_card_count(text) == expected


@pytest.mark.parametrize("text", ["", "<center>Cards</center>", "<center>Cards</center>\n<center>13</center>"])
def test_a_label_without_two_numbers_is_no_reading(text):
    assert parse_card_count(text) is None


class _Window:
    """A UI window: a name, its children, and the text it shows."""

    def __init__(self, name: str, *children: _Window, text: str = ""):
        self._name = name
        self._children = list(children)
        self.text = text
        self.text_reads = 0

    async def name(self):
        return self._name

    async def children(self):
        return self._children

    async def maybe_text(self):
        self.text_reads += 1
        return self.text

    async def get_windows_with_name(self, name):
        raise AssertionError(f"the whole UI was searched for {name}")


def _ui(label: str, *, decoy: str = "<center>Pets</center>"):
    """The planning phase's UI, with a decoy `CountText` ahead of the deck counter."""
    counter = _Window("CountText", text=label)
    hand = _Window("Hand", _Window("Card1"), _Window("DeckCounter", counter))
    root = _Window(
        "root",
        _Window("PetCard", _Window("CountText", text=decoy)),
        _Window(
            "WorldView",
            _Window(
                "PlanningPhase",
                _Window("Alignment", _Window("PlanningPhaseSubWindow", _Window("SpellSelection", hand))),
            ),
        ),
    )
    return root, counter


def _combat_over(root) -> SprintyCombat:
    return SprintyCombat(SimpleNamespace(root_window=root), SimpleNamespace(cast_time=0))


async def test_get_card_counts_reads_a_french_label():
    """The regression end to end, through the window lookup get_card_counts makes."""
    root, _counter = _ui("<center>Cartes</center>\n<center>13 sur 20</center>")

    assert await _combat_over(root).get_card_counts() == (13, 20)


async def test_the_count_is_read_from_the_deck_counter_and_not_another_count_text():
    """A search of the whole UI read the first `CountText`, which held no count, and every
    read waited two seconds for (0, 0). "2 of 9" is a 7-card deck with two treasure cards."""
    root, counter = _ui("<center>Cards</center>\n<center>2 of 9</center>")
    combat = _combat_over(root)

    assert await combat.get_card_counts() == (2, 9)
    counter.text = "<center>Cards</center>\n<center>1 of 9</center>"
    assert await combat.get_card_counts() == (1, 9)
    assert counter.text_reads == 2


async def test_a_label_that_will_not_read_answers_at_once_and_warns_once(caplog, monkeypatch):
    monkeypatch.setattr(sprinty_combat, "CARD_COUNT_RETRY_INTERVAL", 0)
    root, counter = _ui("<center>Cards</center>")
    combat = _combat_over(root)

    assert await combat.get_card_counts() == (0, 0)
    assert await combat.get_card_counts() == (0, 0)
    assert counter.text_reads == 2 * sprinty_combat.CARD_COUNT_READS
    assert len([r for r in caplog.records if "card count" in r.getMessage()]) == 1


async def test_a_path_that_branches_backs_out_of_the_branch_that_does_not_complete_it():
    target = _Window("C")
    root = _Window("root", _Window("A", _Window("B")), _Window("A", _Window("B", target)))

    assert await sprinty_combat.window_at_path(root, ("A", "B", "C")) is target
    assert await sprinty_combat.window_at_path(root, ("A", "X")) is None


def test_a_config_text_is_parsed_once():
    text = "{1} pass\npass"
    first = CombatConfigProvider.from_string(text)
    second = CombatConfigProvider.from_string(text, cast_time=0.5)

    assert first.config is second.config
    assert second.cast_time == 0.5
    assert CombatConfigProvider.from_string("pass").config is not first.config


# ---------------------------------------------------------------------------
# The main pile
# ---------------------------------------------------------------------------


def _combat(*, round_start_hand, max_hand_size, label_left):
    combat = SprintyCombat.__new__(SprintyCombat)
    combat.round_start_hand = round_start_hand
    combat.max_hand_size = max_hand_size

    async def get_card_counts():
        if label_left is None:
            raise AssertionError("the label was read")
        return label_left, 20

    combat.get_card_counts = get_card_counts
    return combat


async def test_a_full_hand_at_round_start_reads_the_label():
    combat = _combat(round_start_hand=7, max_hand_size=7, label_left=13)

    assert await combat.deck_remaining() == 13


async def test_a_short_hand_at_round_start_means_the_main_pile_is_empty():
    """The 7-card deck carrying 4 treasure cards: the label reads 4, all of them treasure,
    and the refill came up short. The label is not read at all."""
    combat = _combat(round_start_hand=3, max_hand_size=7, label_left=None)

    assert await combat.deck_remaining() == 0


@pytest.mark.parametrize("round_start_hand, max_hand_size", [(None, 7), (3, None), (3, 0)])
async def test_an_unknown_hand_or_limit_falls_back_to_the_label(round_start_hand, max_hand_size):
    combat = _combat(round_start_hand=round_start_hand, max_hand_size=max_hand_size, label_left=4)

    assert await combat.deck_remaining() == 4


# ---------------------------------------------------------------------------
# The condition
# ---------------------------------------------------------------------------


def test_deck_parses_as_a_condition_target(condition):
    parsed = condition("?(deck.remaining >= 2) pass")

    assert parsed.target.target_type is TargetType.type_deck
    assert parsed.attribute == "remaining"


@pytest.mark.parametrize("left, holds", [(1, False), (2, True), (13, True)])
async def test_deck_remaining_compares_like_any_attribute(condition, left, holds):
    """`>= 2`: an 8-card deck holding one card back does not count as worth cycling."""
    combat = _combat(round_start_hand=7, max_hand_size=7, label_left=left)

    assert await combat.evaluate_condition(condition("?(deck.remaining >= 2) pass")) is holds


@pytest.mark.parametrize("attribute, is_percent", [("remaining", True), ("size", False)])
async def test_a_percentage_or_an_unknown_deck_attribute_is_false(attribute, is_percent):
    """The parser refuses both (`test_condition_attributes.py`), so only a condition
    built in code reaches here; one that cannot be read does not hold."""
    combat = _combat(round_start_hand=7, max_hand_size=7, label_left=13)
    built = Condition(ConditionTarget(TargetType.type_deck), attribute, ComparisonOp.ge, 2.0, is_percent)

    assert await combat.evaluate_condition(built) is False


async def test_a_deck_clause_joins_others_with_and(condition):
    combat = _combat(round_start_hand=3, max_hand_size=7, label_left=None)

    async def get_client_member():
        return SimpleNamespace(health=_value(5000), max_health=_value(5000))

    combat.get_client_member = get_client_member

    assert await combat.evaluate_condition(condition("?(self.health > 0 && deck.remaining >= 2) pass")) is False


def _value(v):
    async def _read():
        return v

    return _read
