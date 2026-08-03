"""Tests for bartlebot.deck_io.

The memory offsets these rely on are game-build specific (see the module
docstring); what is tested here is the logic layered on top of them -- the grid
arithmetic that replaced the unbounded page-discovery loop, and the toggle
planning that decides which item card slots to click.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bartlebot.bot.strategy_models import DeckData
from bartlebot.deck_io import (
    SLOTS_PER_PAGE,
    ItemSlot,
    active_item_counts,
    count_names,
    page_count,
    plan_item_toggles,
    read_deck,
    write_deck,
)


def slot(name: str, index: int, active: bool = False) -> ItemSlot:
    return ItemSlot(name=name, index=index, active=active)


class FakeDeckBuilder:
    """Minimal DeckBuilder stand-in that records calls and rejects the unsafe ones."""

    def __init__(self):
        self.calls: list[str] = []
        self.added: dict[str, int] | None = None
        self.client = SimpleNamespace(root_window=object())

    async def refresh_deck_page(self) -> None:
        self.calls.append("refresh_deck_page")

    async def clear_deck(self) -> None:
        self.calls.append("clear_deck")

    async def view_deck_cards(self) -> None:
        self.calls.append("view_deck_cards")

    async def _add_all_normal_cards(self, section: dict[str, int]) -> None:
        self.calls.append("_add_all_normal_cards")
        self.added = dict(section)

    # -- forbidden: these hang or corrupt the spell list ----------------

    async def view_tc_cards(self) -> None:
        raise AssertionError("must not switch to the treasure card view")

    async def _scan_all_item_pages(self):
        raise AssertionError("must not scan item pages by clicking")

    async def clear_full_deck(self) -> None:
        raise AssertionError("must not call clear_full_deck")

    async def set_deck_preset(self, preset: dict) -> None:
        raise AssertionError("must not call set_deck_preset")

    async def get_deck_preset(self) -> dict:
        raise AssertionError("must not call get_deck_preset")


# ---------------------------------------------------------------------------
# Grid arithmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("index", "expected_page", "expected_slot"),
    [
        (0, 0, 0),  # first card, first page
        (7, 0, 7),  # end of row 1
        (8, 0, 8),  # start of row 2 -- the second enabled card in the probe run
        (15, 0, 15),  # last slot of page 0
        (16, 1, 0),  # first card of page 1
        (33, 2, 1),
    ],
)
def test_item_slot_grid_position(index: int, expected_page: int, expected_slot: int):
    s = slot("Card", index)
    assert s.page == expected_page
    assert s.slot_index == expected_slot


@pytest.mark.parametrize(
    ("total", "expected"),
    [(0, 1), (1, 1), (16, 1), (17, 2), (32, 2), (33, 3)],
)
def test_page_count(total: int, expected: int):
    """17 cards is the probe run's layout: one full page plus one."""
    assert page_count(total) == expected


def test_slots_per_page_is_the_observed_grid():
    assert SLOTS_PER_PAGE == 16


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def test_count_names_tallies_duplicates():
    assert count_names(["Fire", "Ice", "Fire"]) == {"Fire": 2, "Ice": 1}


def test_active_item_counts_ignores_inactive():
    slots = [
        slot("Sharpened Blade - Tear", 0, active=True),
        slot("Wand-Storm-180", 5),
        slot("Wand-Storm-180", 8, active=True),
    ]
    assert active_item_counts(slots) == {"Sharpened Blade - Tear": 1, "Wand-Storm-180": 1}


# ---------------------------------------------------------------------------
# Toggle planning
# ---------------------------------------------------------------------------


def test_plan_no_changes_when_already_correct():
    slots = [slot("Blade", 0, active=True), slot("Blade", 1)]
    assert plan_item_toggles(slots, {"Blade": 1}) == []


def test_plan_activates_missing_copies():
    slots = [slot("Blade", 0), slot("Blade", 1), slot("Blade", 2)]
    plan = plan_item_toggles(slots, {"Blade": 2})
    assert [s.index for s in plan] == [0, 1]


def test_plan_deactivates_extra_copies():
    slots = [slot("Blade", 0, active=True), slot("Blade", 1, active=True)]
    plan = plan_item_toggles(slots, {"Blade": 1})
    assert len(plan) == 1
    assert plan[0].active is True


def test_plan_deactivates_everything_not_wanted():
    slots = [slot("Blade", 0, active=True), slot("Trap", 1, active=True)]
    plan = plan_item_toggles(slots, {"Blade": 1})
    assert [s.index for s in plan] == [1]


def test_plan_skips_cards_the_account_lacks():
    """Gear changes, so a saved deck naming an absent card is expected."""
    slots = [slot("Blade", 0)]
    plan = plan_item_toggles(slots, {"Blade": 1, "Gone With The Gear": 2})
    assert [s.index for s in plan] == [0]


def test_plan_caps_at_available_copies():
    slots = [slot("Blade", 0), slot("Blade", 1)]
    plan = plan_item_toggles(slots, {"Blade": 5})
    assert [s.index for s in plan] == [0, 1]


def test_plan_is_ordered_by_index_for_a_forward_sweep():
    slots = [slot("A", 20), slot("B", 3), slot("C", 17)]
    plan = plan_item_toggles(slots, {"A": 1, "B": 1, "C": 1})
    assert [s.index for s in plan] == [3, 17, 20]
    assert [s.page for s in plan] == [0, 1, 1]


def test_plan_empty_desired_clears_all_active():
    slots = [slot("Blade", 0, active=True), slot("Trap", 9, active=True), slot("Shield", 4)]
    plan = plan_item_toggles(slots, {})
    assert [s.index for s in plan] == [0, 9]


# ---------------------------------------------------------------------------
# write_deck orchestration
# ---------------------------------------------------------------------------


async def test_write_deck_clears_then_adds_normal_cards(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()
    await write_deck(db, DeckData(normal={"Tempest": 4}))
    assert db.calls == ["refresh_deck_page", "clear_deck", "view_deck_cards", "_add_all_normal_cards"]
    assert db.added == {"Tempest": 4}


async def test_write_deck_empty_clears_without_adding(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()
    await write_deck(db, DeckData())
    assert db.calls == ["refresh_deck_page", "clear_deck", "view_deck_cards"]
    assert db.added is None


async def test_write_deck_ignores_legacy_treasure_cards(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()
    await write_deck(db, DeckData(normal={"Tempest": 1}, tc={"Feint": 3}))
    assert db.added == {"Tempest": 1}


async def test_read_deck_never_reports_treasure_cards(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _fake_item_slots)
    db = FakeDeckBuilder()
    result = await read_deck(db)
    assert result["normal"] == {"Tempest": 2}
    assert result["item"] == {"Sharpened Blade - Tear": 1}
    assert result["tc"] == {}


async def test_write_deck_propagates_failures(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)

    class Failing(FakeDeckBuilder):
        async def clear_deck(self) -> None:
            raise RuntimeError("deck window not open")

    with pytest.raises(RuntimeError, match="deck window not open"):
        await write_deck(Failing(), DeckData(normal={"Tempest": 1}))


# -- monkeypatch helpers ---------------------------------------------------


async def _fake_window(*_args, **_kwargs):
    return object()


async def _no_item_slots(*_args, **_kwargs):
    return []


async def _fake_item_slots(*_args, **_kwargs):
    return [slot("Sharpened Blade - Tear", 0, active=True), slot("Wand-Storm-180", 5)]


async def _fake_deck_names(*_args, **_kwargs):
    return ["Tempest", "Tempest"]
