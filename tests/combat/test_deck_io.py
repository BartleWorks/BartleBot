"""Tests for bartlebot.combat.deck_io.

The memory offsets these rely on are game-build specific (see the module
docstring); what is tested here is the logic layered on top of them -- the grid
arithmetic that replaced the unbounded page-discovery loop, and the toggle
planning that decides which item card slots to click.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bartlebot.combat.deck_io import (
    ACTIVE_FLAG_OFFSET,
    ACTIVE_FLAG_ON,
    ARCHMASTERY_ATTEMPTS,
    CARD_CLICK_ATTEMPTS,
    ENTRY_STRIDE,
    SLOTS_PER_PAGE,
    DeckApplyReport,
    ItemSlot,
    _apply_item_toggles,
    _apply_slot,
    _click,
    _settle_deck,
    _share_shortfall,
    _spell_list_names,
    _trim_copy,
    active_item_counts,
    apply_deck,
    archmastery_menu_is_open,
    count_names,
    deck_matches,
    deck_page_open,
    direct_requesters,
    expected_normal_deck,
    page_count,
    parse_tiered_name,
    partition_addable,
    plan_item_toggles,
    plan_normal_cards,
    read_archmastery_school,
    read_deck,
    requested_per_card,
    resolve_apply_archmastery,
    resolve_tier,
    resolved_at_click_time,
    set_archmastery_school,
    suggest_alternatives,
    tally_against_request,
    write_deck,
)
from bartlebot.combat.deck_io import logger as deck_logger
from bartlebot.nodes.timing import DECK_APPLY_TIMEOUT
from bartlebot.shared.strategy_models import DeckData
from tests.conftest import FakeClient


def slot(name: str, index: int, active: bool = False) -> ItemSlot:
    return ItemSlot(name=name, index=index, active=active)


@pytest.fixture(autouse=True)
def _fast_settle(monkeypatch):
    """Stop the post-write settle from waiting out its real 15s ceiling.

    Most tests here drive fakes whose deck never matches what was 'written', so
    every write_deck call would otherwise sit through the full timeout. The
    settle still runs — it just gives up immediately. Tests about the settle
    itself override these.
    """
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_TIMEOUT", 0.0)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_POLL", 0.001)
    # Likewise for the per-card confirm: a zero ceiling still takes one reading,
    # so a fake deck that reflects its clicks confirms immediately and one that
    # never grows gives up at once instead of waiting out 2s per attempt.
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_CARD_CONFIRM_TIMEOUT", 0.0)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_CARD_CONFIRM_POLL", 0.001)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_CARD_STABLE_WINDOW", 0.0)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_CARD_LAND_TIMEOUT", 0.0)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_CARD_COUNT_POLL", 0.001)
    for name in (
        "DECK_ITEM_PAGE_SETTLE",
        "DECK_ITEM_TOGGLE_SETTLE",
        "DECK_FUSION_FLYOUT_SETTLE",
        "DECK_SPELL_PAGE_SETTLE",
        "DECK_FUSION_CLICK_SETTLE",
        "DECK_FUSION_CLOSE_SETTLE",
    ):
        monkeypatch.setattr(f"bartlebot.combat.deck_io.{name}", 0.0)


class _FakeSpellEntry:
    """One raw spellbook vector entry; a ``None`` name models one that cannot be read."""

    def __init__(self, name: str | None):
        self._name = name

    async def graphical_spell(self):
        if self._name is None:
            raise RuntimeError("unreadable entry")
        template = SimpleNamespace(name=_async_returning(self._name))
        return SimpleNamespace(spell_template=_async_returning(template))


@pytest.fixture(autouse=True)
def _unreadable_card_count(monkeypatch):
    """Place cards on the clicks alone unless a test models the deck's count.

    Most fakes here record clicks without keeping a deck, so a real per-name
    read would see no copies arrive and click every card three times.  An
    unreadable count is the fallback ``_place_copies`` already has for that.
    Tests about confirming copies install a counting deck with ``_use_deck``.
    """

    async def _unreadable(_name):
        return None

    monkeypatch.setattr("bartlebot.combat.deck_io.card_counter", lambda *_args: _unreadable)


@pytest.fixture(autouse=True)
def _fake_spell_list(monkeypatch):
    """Serve the raw spellbook read from the fake builder's ``spell_list``.

    The real control is a DynamicSpellListControl over live memory.  Only that
    lookup is replaced, so ``_spell_list_names``' entry-by-entry read stays under
    test.
    """

    async def _control(db):
        db.calls.append("read_spell_list")
        entries = [_FakeSpellEntry(name) for name in db.spell_list]
        return SimpleNamespace(spell_entries=_async_returning(entries))

    monkeypatch.setattr("bartlebot.combat.deck_io._spell_list_control", _control)


def _async_returning(value):
    """Build a zero-arg coroutine function that yields ``value``.

    The memory objects this module reads are async all the way down, so the
    fakes have to be too.
    """

    async def _call():
        return value

    return _call


class FakeDeckBuilder:
    """Minimal DeckBuilder stand-in that records calls and rejects the unsafe ones.

    Pages the spellbook the way the game does: ``set_page`` moves ``page``, and a
    click at a card position lands on whatever that slot holds on the page
    showing.  ``added`` reads the result back from the clicks, so a card clicked
    on the wrong page shows up as the wrong card.
    """

    #: Template names the fake account's spell list contains. Anything a deck
    #: asks for outside this is treated as not owned, exactly as in the game.
    spell_list: list[str] = ["Tempest", "Frenzy", "Epic", "Sharpened Blade", "Storm Lord"]
    CARDS_PER_PAGE = 6
    #: Entries clicked to open a flyout (or to add a base form) by the fusion
    #: pass.  Clicks on them are not direct placements, so ``added`` leaves them out.
    base_forms: frozenset[str] = frozenset()

    def __init__(self):
        self.calls: list[str] = []
        self.page = 0
        self._deck_config_window = object()
        # No deck behavior by default, so read_archmastery_school answers None
        # without a live client. Tests about the school replace this.
        self.client = SimpleNamespace(
            root_window=object(),
            hook_handler=None,
            mouse_handler=_FakeMouse(self),
            client_object=SimpleNamespace(try_get_deck_behavior=AsyncMock(return_value=None)),
        )
        # Real DeckBuilder constructs this False and open_deck_page() never sets
        # it; whoever opens the page is responsible for saying so. Direct
        # write_deck tests stand in for a properly-opened page, so True here â€”
        # _CtxDeckBuilder resets it to False to test that apply_deck does its part.
        self._on_deck_page = True

    @property
    def added(self) -> dict[str, int] | None:
        """Spellbook card -> copies clicked straight off the spellbook, or None if none were."""
        mouse = self.client.mouse_handler
        counts: dict[str, int] = {}
        for (x, _y), page in zip(mouse.clicks, mouse.pages):
            if x >= _VARIANT_CLICK_BASE:
                continue  # inside a flyout
            name = self.spell_list[page * self.CARDS_PER_PAGE + x - 1]
            if parse_tiered_name(name).is_fusion_root or name in self.base_forms:
                continue
            counts[name] = counts.get(name, 0) + 1
        return counts or None

    def calcuate_position_of_card_in_page(self, cards: list, name: str) -> tuple[int, int]:
        """The real 6-per-page arithmetic, so page indices are not all zero."""
        index = cards.index(name) + 1
        slot = index % self.CARDS_PER_PAGE or self.CARDS_PER_PAGE
        return (index - 1) // self.CARDS_PER_PAGE, slot

    async def calculate_card_position(self, card_index: int) -> tuple[int, int]:
        return (card_index, 0)

    async def set_page(self, page: int) -> None:
        pages = max(1, -(-len(self.spell_list) // self.CARDS_PER_PAGE))
        if not 0 <= page < pages:
            raise ValueError(f"page {page} is outside the spellbook's {pages} page(s)")
        self.calls.append(f"set_page:{page}")
        self.page = page

    async def refresh_deck_page(self) -> None:
        self.calls.append("refresh_deck_page")
        self.page = 0  # reopening the deck page puts the spellbook back on page one

    async def get_graphical_spell_cards(self) -> list:
        self.calls.append("get_graphical_spell_cards")

        def _spell(name: str):
            template = SimpleNamespace(name=_async_returning(name))
            return SimpleNamespace(spell_template=_async_returning(template))

        return [_spell(n) for n in self.spell_list]

    async def clear_deck(self) -> None:
        self.calls.append("clear_deck")

    async def view_deck_cards(self) -> None:
        # Mirrors the real implementation: a False flag here does not mean "do
        # nothing", it means "click through to the treasure card view".
        if not self._on_deck_page:
            raise AssertionError("view_deck_cards() with _on_deck_page False would switch to the treasure card view")
        self.calls.append("view_deck_cards")

    # -- forbidden: these hang or corrupt the spell list ----------------

    async def _add_all_normal_cards(self, section: dict[str, int]) -> None:
        raise AssertionError("must not page with PageUp/PageDown: a written page leaves them unusable")

    async def _go_to_spell_page(self, page: int) -> None:
        raise AssertionError("must not page with PageUp/PageDown: a written page leaves them unusable")

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
# Item card click confirmation
#
# A click that never landed used to be indistinguishable from one that did
# until the whole deck's post-write settle timed out 15s later, and even then
# nothing retried it. These confirm each click against the slot's own active
# flag instead, the same shape _place_copies already used for spell cards.
# ---------------------------------------------------------------------------


class _IndexCell:
    """A grid cell whose center encodes its own index, so a click is traceable."""

    def __init__(self, index: int):
        self.index = index

    def center(self):
        return (self.index, 0)


class _ItemMouse:
    """Records clicks and flips the clicked index's active flag -- unless that
    index is in ``misses``, which models a click the game swallowed."""

    def __init__(self, active: dict[int, bool]):
        self.active = active
        self.misses: dict[int, int] = {}
        self.clicks: list[int] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def click(self, x, _y):
        self.clicks.append(x)
        if self.misses.get(x, 0) > 0:
            self.misses[x] -= 1
            return
        self.active[x] = not self.active.get(x, False)

    async def click_window(self, *_args, **_kwargs):
        return None


@pytest.fixture
def item_toggle_env(monkeypatch):
    """Wires the confirm reader to a plain ``{index: active}`` dict.

    ``begin`` is fixed at 0, so the confirm reader's address arithmetic recovers
    a slot's index without a real ``ItemSpells`` DeckListControl behind it.
    """
    active: dict[int, bool] = {}
    page_writes: list[int] = []
    mouse = _ItemMouse(active)

    class _Control:
        async def read_bytes(self, address: int, _n: int) -> bytes:
            index = (address - ACTIVE_FLAG_OFFSET) // ENTRY_STRIDE
            return bytes([ACTIVE_FLAG_ON if active.get(index, False) else 1])

        async def write_start_index(self, start_index: int) -> None:
            page_writes.append(start_index)

    async def _fake_control_for(_client, _deck_window, window_name):
        assert window_name == "ItemSpells"
        return _Control()

    async def _fake_entry_vector(_control):
        return 0, 0

    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io._control_for", _fake_control_for)
    monkeypatch.setattr("bartlebot.combat.deck_io._entry_vector", _fake_entry_vector)

    db = FakeDeckBuilder()
    db.client = SimpleNamespace(root_window=object(), mouse_handler=mouse)
    db.get_item_spells_rectangle = _async_returning(object())
    db.divide_rectangle = lambda rect, columns, rows: [_IndexCell(i) for i in range(columns * rows)]

    return SimpleNamespace(db=db, mouse=mouse, active=active, page_writes=page_writes)


async def test_apply_item_toggles_confirms_a_clean_click(item_toggle_env):
    env = item_toggle_env
    await _apply_item_toggles(env.db, object(), [slot("Blade", 0)], total_pages=1)

    assert env.active[0] is True
    assert env.mouse.clicks == [0]


async def test_apply_item_toggles_retries_a_swallowed_click(item_toggle_env):
    """The first two clicks are swallowed; the fix is noticing and clicking again."""
    env = item_toggle_env
    env.mouse.misses[0] = 2

    await _apply_item_toggles(env.db, object(), [slot("Blade", 0)], total_pages=1)

    assert env.active[0] is True
    assert env.mouse.clicks == [0, 0, 0]


async def test_apply_item_toggles_gives_up_after_the_attempt_ceiling(item_toggle_env, caplog):
    """Bounded the same way CARD_CLICK_ATTEMPTS bounds a spell card's retries."""
    env = item_toggle_env
    env.mouse.misses[0] = 99

    with caplog.at_level(logging.WARNING, logger="bartlebot.combat.deck_io"):
        await _apply_item_toggles(env.db, object(), [slot("Blade", 0)], total_pages=1)

    assert env.mouse.clicks == [0] * CARD_CLICK_ATTEMPTS
    assert 0 not in env.active  # every click was swallowed; the flag never flipped
    assert "did not toggle" in caplog.text


async def test_apply_item_toggles_deactivates_an_active_slot(item_toggle_env):
    """The desired state is the click's target, not always 'turn it on'."""
    env = item_toggle_env
    env.active[3] = True

    await _apply_item_toggles(env.db, object(), [slot("Blade", 3, active=True)], total_pages=1)

    assert env.active[3] is False
    assert env.mouse.clicks == [3]


async def test_apply_item_toggles_writes_each_page_once(item_toggle_env):
    """Pages are written, never clicked to: Prev does nothing on a written page."""
    env = item_toggle_env

    await _apply_item_toggles(env.db, object(), [slot("A", 2), slot("B", 5), slot("C", 17)], total_pages=2)

    assert env.page_writes == [0, SLOTS_PER_PAGE]


async def test_apply_item_toggles_skips_a_slot_whose_page_cannot_be_written(item_toggle_env, caplog):
    """Clicking anyway would toggle whatever sits in that slot on the page still showing."""
    env = item_toggle_env

    with caplog.at_level(logging.WARNING, logger="bartlebot.combat.deck_io"):
        await _apply_item_toggles(env.db, object(), [slot("Blade", 17)], total_pages=1)

    assert env.page_writes == []
    assert env.mouse.clicks == []
    assert "not toggled" in caplog.text


async def test_write_deck_counts_item_pages_from_the_highest_index(monkeypatch):
    """read_item_slots skips unreadable entries, so the slot count can undercount pages."""
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _no_normal_cards)

    async def _gappy_slots(*_args, **_kwargs):
        return [slot("Blade", 0), slot("Trap", 17)]  # two readable slots, on two pages

    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _gappy_slots)
    seen: list[int] = []

    async def _spy(_db, _window, _plan, total_pages):
        seen.append(total_pages)

    monkeypatch.setattr("bartlebot.combat.deck_io._apply_item_toggles", _spy)

    await write_deck(FakeDeckBuilder(), DeckData(item={"Trap": 1}))

    assert seen == [2]


# ---------------------------------------------------------------------------
# write_deck orchestration
# ---------------------------------------------------------------------------


async def test_write_deck_clears_then_adds_normal_cards(monkeypatch):
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()
    await write_deck(db, DeckData(normal={"Tempest": 4}))
    assert db.calls == [
        "view_deck_cards",
        "read_spell_list",
        "clear_deck",
        "view_deck_cards",
        "set_page:0",
    ]
    assert db.added == {"Tempest": 4}


async def test_write_deck_reads_the_spell_list_before_clearing(monkeypatch):
    """The ordering invariant: nothing that can fail on a card name runs after the clear.

    _add_all_normal_cards raises on the first name it cannot place, so planning
    afterwards left the wizard with an emptied deck.
    """
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()

    await write_deck(db, DeckData(normal={"Tempest": 4}))

    assert db.calls.index("read_spell_list") < db.calls.index("clear_deck")


async def test_write_deck_skips_unowned_cards_instead_of_emptying_the_deck(monkeypatch):
    """One card the account lacks must cost that card, not the entire deck."""
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()

    await write_deck(db, DeckData(normal={"Tempest": 2, "Firestorm Blade": 4}))

    assert db.added == {"Tempest": 2}


async def test_write_deck_adds_nothing_when_no_card_is_owned(monkeypatch):
    """Degrades to leaving the deck alone rather than raising out of the apply."""
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()

    await write_deck(db, DeckData(normal={"Firestorm Blade": 4}))

    assert db.added is None
    assert "clear_deck" not in db.calls


async def test_write_deck_empty_clears_without_adding(monkeypatch):
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()
    await write_deck(db, DeckData())
    assert db.calls == ["clear_deck", "view_deck_cards"]
    assert db.added is None


async def test_write_deck_ignores_legacy_treasure_cards(monkeypatch):
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()
    await write_deck(db, DeckData(normal={"Tempest": 1}, tc={"Feint": 3}))
    assert db.added == {"Tempest": 1}


async def test_write_deck_skips_when_current_deck_already_matches(monkeypatch):
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _fake_item_slots)
    db = FakeDeckBuilder()
    deck = DeckData(normal={"Tempest": 2}, item={"Sharpened Blade - Tear": 1})
    await write_deck(db, deck)
    # No builder calls at all: the matching deck is recognized from two memory
    # reads, without touching the UI.
    assert db.calls == []
    assert db.added is None


async def test_write_deck_rewrites_when_item_cards_differ(monkeypatch):
    """Even with matching normal cards, a mismatched item loadout must still trigger a rewrite."""
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _fake_item_slots)
    db = FakeDeckBuilder()
    deck = DeckData(normal={"Tempest": 2}, item={"Sharpened Blade - Tear": 2})
    await write_deck(db, deck)
    assert "clear_deck" in db.calls
    assert db.added == {"Tempest": 2}


def _read_client(monkeypatch, *, open_now: bool = False) -> FakeClient:
    """A client whose deck page opens on P, with the deck's controls faked."""
    state = _PageState(open_now=open_now)
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", state)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_cards", _fake_deck_cards)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _fake_item_slots)
    client = _page_client(state)
    client.client_object.try_get_deck_behavior = AsyncMock(return_value=None)
    return client


async def test_read_deck_never_reports_treasure_cards(monkeypatch):
    result = await read_deck(_read_client(monkeypatch))
    assert result["normal"] == {"Tempest": 2}
    assert result["item"] == {"Sharpened Blade - Tear": 1}
    assert result["tc"] == {}


def _behavior(school_id: int) -> SimpleNamespace:
    return SimpleNamespace(archmastery_school=AsyncMock(return_value=school_id))


async def test_read_archmastery_school_names_the_school():
    client = SimpleNamespace(
        client_object=SimpleNamespace(try_get_deck_behavior=AsyncMock(return_value=_behavior(83375795)))
    )
    assert await read_archmastery_school(client) == "Storm"


async def test_read_archmastery_school_is_none_without_a_deck_behavior():
    """A wizard with no deck equipped has nothing to read — not an error."""
    client = SimpleNamespace(client_object=SimpleNamespace(try_get_deck_behavior=AsyncMock(return_value=None)))
    assert await read_archmastery_school(client) is None


async def test_read_archmastery_school_is_none_for_an_unknown_id():
    """An id outside wizwalker's table is not worth guessing at, and must not raise."""
    client = SimpleNamespace(
        client_object=SimpleNamespace(try_get_deck_behavior=AsyncMock(return_value=_behavior(1234)))
    )
    assert await read_archmastery_school(client) is None


async def test_read_archmastery_school_swallows_a_failed_read():
    client = SimpleNamespace(
        client_object=SimpleNamespace(try_get_deck_behavior=AsyncMock(side_effect=RuntimeError("no hook")))
    )
    assert await read_archmastery_school(client) is None


async def test_read_deck_carries_the_archmastery_school(monkeypatch):
    client = _read_client(monkeypatch)
    client.client_object.try_get_deck_behavior = AsyncMock(return_value=_behavior(72777))

    result = await read_deck(client)

    assert result["archmastery_school"] == "Ice"
    assert DeckData(**result).archmastery_school == "Ice"


async def test_read_deck_survives_an_unreadable_archmastery_school(monkeypatch):
    """A cosmetic field must never be able to fail a whole deck import."""
    client = _read_client(monkeypatch)
    client.client_object.try_get_deck_behavior = AsyncMock(side_effect=RuntimeError("boom"))

    result = await read_deck(client)

    assert result["archmastery_school"] is None
    assert result["normal"] == {"Tempest": 2}


async def test_read_deck_opens_the_page_once_and_leaves_it_closed(monkeypatch):
    """#120: one P to open and one to close -- no DeckBuilder reopen on top, no
    clicks, and no mouseless hook toggled per keypress."""
    client = _read_client(monkeypatch)

    await read_deck(client)

    assert client.send_key.await_count == 2
    assert not client.page_state.is_open
    client.mouse_handler.__aenter__.assert_not_awaited()


async def test_read_deck_raises_when_the_page_will_not_open(monkeypatch):
    """Failing the slot is what keeps an empty read from being saved over a real deck."""
    client = _read_client(monkeypatch)
    client.page_state.ever_opens = False
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_PAGE_OPEN_TIMEOUT", 0.05)

    with pytest.raises(RuntimeError, match="did not open"):
        await read_deck(client)


async def test_a_deck_holding_only_an_archmastery_school_is_still_empty():
    assert DeckData(archmastery_school="Storm").is_empty()


async def test_write_deck_propagates_failures(monkeypatch):
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)

    class Failing(FakeDeckBuilder):
        async def clear_deck(self) -> None:
            raise RuntimeError("deck window not open")

    with pytest.raises(RuntimeError, match="deck window not open"):
        await write_deck(Failing(), DeckData(normal={"Tempest": 1}))


# ---------------------------------------------------------------------------
# apply_deck: the single entry point shared by the manual and pre-run paths
# ---------------------------------------------------------------------------


class _CtxDeckBuilder(FakeDeckBuilder):
    """FakeDeckBuilder usable as `async with DeckBuilder(client)`."""

    instances: list[_CtxDeckBuilder] = []

    def __init__(self, client=None):
        super().__init__()
        self.entered = False
        self.exited = False
        # Set by _page_client, so entering can open the page the way the real
        # open_deck_page() does. Absent for clients that do not model a page.
        self._page_state = getattr(client, "page_state", None)
        _CtxDeckBuilder.instances.append(self)

    async def __aenter__(self):
        self.entered = True
        if self._page_state is not None:
            self._page_state.is_open = True
        # As the real open_deck_page() leaves it: on the deck page in fact, but
        # not in the flag. apply_deck must set it before write_deck runs.
        self._on_deck_page = False
        return self

    async def __aexit__(self, *_exc):
        self.exited = True
        return False


@pytest.fixture
def fake_deck_builder(monkeypatch):
    """Patch the DeckBuilder that apply_deck imports lazily."""
    _CtxDeckBuilder.instances = []
    monkeypatch.setattr(
        "wizwalker.extensions.scripting.deck_builder.DeckBuilder",
        _CtxDeckBuilder,
    )
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    # _fake_window reports the page as permanently open, so deck_page_open takes
    # its "would not close" bail-out. These tests are about the write path, not
    # that wait — keep it from costing five real seconds apiece.
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_PAGE_OPEN_TIMEOUT", 0.01)
    return _CtxDeckBuilder


async def test_apply_deck_opens_the_builder_and_writes(fake_deck_builder):
    await apply_deck(FakeClient(), DeckData(normal={"Tempest": 4}))

    db = fake_deck_builder.instances[0]
    assert db.entered and db.exited
    assert db.calls == [
        "view_deck_cards",
        "read_spell_list",
        "clear_deck",
        "view_deck_cards",
        "set_page:0",
    ]
    assert db.added == {"Tempest": 4}


async def test_apply_deck_marks_the_page_before_writing(fake_deck_builder):
    """open_deck_page() clicks the Deck tab but leaves _on_deck_page False, so
    apply_deck must set it. Without that, write_deck's view_deck_cards() call
    switches to the treasure card view and corrupts the spell list â€” which is
    exactly what dropping the redundant refresh_deck_page() exposed."""
    await apply_deck(FakeClient(), DeckData(normal={"Tempest": 1}))

    db = fake_deck_builder.instances[0]
    assert db._on_deck_page
    assert "view_deck_cards" in db.calls


async def test_apply_deck_never_uses_set_deck_preset(fake_deck_builder):
    """The pre-run path used to call DeckBuilder.set_deck_preset(), which reaches
    the treasure card view and can page forward forever. FakeDeckBuilder raises on
    it, so this passing means apply_deck goes through write_deck instead."""
    await apply_deck(FakeClient(), DeckData(normal={"Tempest": 1}, item={"Sharpened Blade - Tear": 1}))

    assert "set_deck_preset" not in fake_deck_builder.instances[0].calls


class _HangingDeckBuilder(_CtxDeckBuilder):
    """Stalls partway through the write, after the context has been entered."""

    async def view_deck_cards(self) -> None:
        await asyncio.sleep(60)


async def test_apply_deck_times_out(fake_deck_builder, monkeypatch):
    """A stuck apply must not hang the run forever â€” this is what the pre-run path
    was missing entirely."""
    _CtxDeckBuilder.instances = []
    monkeypatch.setattr("wizwalker.extensions.scripting.deck_builder.DeckBuilder", _HangingDeckBuilder)

    with pytest.raises(TimeoutError):
        await apply_deck(FakeClient(), DeckData(normal={"Tempest": 1}), timeout=0.05)


async def test_apply_deck_closes_the_builder_on_timeout(fake_deck_builder, monkeypatch):
    """The deck window must still be released when the apply is cancelled."""
    _CtxDeckBuilder.instances = []
    monkeypatch.setattr("wizwalker.extensions.scripting.deck_builder.DeckBuilder", _HangingDeckBuilder)

    # Longer than test_apply_deck_times_out needs, because this one reads the builder
    # afterwards: the timeout has to outlast constructing it, or on a loaded machine the
    # apply is cancelled before there is an instance and the assert below raises
    # IndexError instead of failing. The builder stalls for 60s either way, so the exact
    # ceiling does not change what is being tested.
    with pytest.raises(TimeoutError):
        await apply_deck(FakeClient(), DeckData(normal={"Tempest": 1}), timeout=0.3)

    assert _HangingDeckBuilder.instances[0].exited


async def test_apply_deck_propagates_write_failures(fake_deck_builder, monkeypatch):
    class Failing(_CtxDeckBuilder):
        async def clear_deck(self) -> None:
            raise RuntimeError("deck window not open")

    _CtxDeckBuilder.instances = []
    monkeypatch.setattr("wizwalker.extensions.scripting.deck_builder.DeckBuilder", Failing)

    with pytest.raises(RuntimeError, match="deck window not open"):
        await apply_deck(FakeClient(), DeckData(normal={"Tempest": 1}))

    assert Failing.instances[0].exited


async def test_apply_deck_defaults_to_the_shared_timeout(fake_deck_builder):
    """Both call sites rely on the default rather than passing their own."""
    import inspect

    default = inspect.signature(apply_deck).parameters["timeout"].default
    assert default == DECK_APPLY_TIMEOUT


# ---------------------------------------------------------------------------
# Expected end state after a write
# ---------------------------------------------------------------------------


def test_expected_deck_is_just_the_direct_cards_when_there_is_no_fusion():
    assert expected_normal_deck({"Tempest": 4, "Epic": 2}, {}) == {"Tempest": 4, "Epic": 2}


def test_expected_deck_includes_the_fusion_cards_that_were_counted_in():
    expected = expected_normal_deck({"Tempest": 2}, {"Novus Storm - T05 - B": 3})
    assert expected == {"Tempest": 2, "Novus Storm - T05 - B": 3}


def test_expected_deck_takes_fusion_cards_under_the_name_actually_clicked():
    """A wizard who only unlocked T02 gets T02, so that is what should be there."""
    expected = expected_normal_deck({}, {"Novus Storm - T02 - B": 3})
    assert expected == {"Novus Storm - T02 - B": 3}


def test_expected_deck_omits_fusion_cards_that_never_landed():
    """Known-missing cards are expected to be missing; waiting for them would
    guarantee a timeout on every apply. _add_fusion_cards leaves them out of
    ``placed`` rather than this having to filter them."""
    expected = expected_normal_deck({"Tempest": 2}, {})
    assert expected == {"Tempest": 2}


def test_expected_deck_expects_only_the_copies_that_went_in():
    """A card the deck took two of three copies of settles at two, not three."""
    expected = expected_normal_deck({}, {"Novus Storm - T05 - B": 2})
    assert expected == {"Novus Storm - T05 - B": 2}


def test_expected_deck_merges_a_substitution_onto_an_existing_direct_card():
    """Two routes can land on the same entry; the counts have to add up."""
    expected = expected_normal_deck({"Novus Storm - T02 - B": 1}, {"Novus Storm - T02 - B": 2})
    assert expected == {"Novus Storm - T02 - B": 3}


# ---------------------------------------------------------------------------
# Settling: do not close the page while the deck is still filling in
# ---------------------------------------------------------------------------


class _SettlingDeck:
    """Reports an incomplete deck for the first N reads, then the full one."""

    def __init__(self, final: list[str], reads_until_settled: int):
        self.final = final
        self.remaining = reads_until_settled
        self.reads = 0

    async def read_names(self, *_args, **_kwargs):
        self.reads += 1
        if self.remaining > 0:
            self.remaining -= 1
            return self.final[:-1]  # one card still missing
        return self.final


async def test_settle_waits_for_late_cards_to_appear(monkeypatch):
    """The game's card list lags the clicks that filled it."""
    settling = _SettlingDeck(["Tempest", "Tempest", "Epic"], reads_until_settled=3)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", settling.read_names)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_TIMEOUT", 5.0)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_POLL", 0.001)

    settled = await _settle_deck(FakeClient(), object(), {"Tempest": 2, "Epic": 1}, {})

    assert settled is True
    assert settling.reads == 4  # three short reads, then the full one


async def test_settle_returns_immediately_when_the_deck_is_already_right(monkeypatch):
    settling = _SettlingDeck(["Tempest"], reads_until_settled=0)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", settling.read_names)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_TIMEOUT", 5.0)

    assert await _settle_deck(FakeClient(), object(), {"Tempest": 1}, {}) is True
    assert settling.reads == 1


async def test_settle_gives_up_and_warns_when_the_deck_never_matches(monkeypatch, caplog):
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_TIMEOUT", 0.05)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_POLL", 0.001)

    with caplog.at_level(logging.WARNING, logger="bartlebot.combat.deck_io"):
        settled = await _settle_deck(FakeClient(), object(), {"Tempest": 4}, {})

    assert settled is False
    assert "did not settle" in caplog.text


async def test_settle_ignores_item_cards_the_account_does_not_grant(monkeypatch):
    """Gear changes, so a saved deck naming absent item cards is expected."""
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _fake_item_slots)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_TIMEOUT", 0.05)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_POLL", 0.001)

    desired = {"Sharpened Blade - Tear": 1, "Gone With The Gear": 2}
    assert await _settle_deck(FakeClient(), object(), {"Tempest": 2}, desired) is True


async def test_settle_tolerates_a_read_failing_mid_update(monkeypatch):
    """A read can fail while the game is rewriting the list — that is the state
    being waited out, not a reason to give up."""
    calls = {"n": 0}
    final = ["Tempest"]

    async def _flaky(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("memory read failed")
        return final

    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _flaky)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_TIMEOUT", 5.0)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_POLL", 0.001)

    assert await _settle_deck(FakeClient(), object(), {"Tempest": 1}, {}) is True


async def test_write_deck_settles_before_returning(monkeypatch):
    """write_deck must not hand control back — and so let the page close — until
    the deck reads right."""
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_TIMEOUT", 5.0)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_POLL", 0.001)

    settling = _SettlingDeck(["Tempest", "Tempest"], reads_until_settled=2)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", settling.read_names)

    db = FakeDeckBuilder()
    await write_deck(db, DeckData(normal={"Tempest": 2}))

    assert db.added == {"Tempest": 2}
    assert settling.reads > 1  # it waited rather than closing on the first read


# ---------------------------------------------------------------------------
# Bare-keypress deck page: the read-only fast path
# ---------------------------------------------------------------------------


class _PageState:
    """Stands in for _maybe_get_named_window, tracking whether the page is open.

    The real DeckConfiguration lookup raises ValueError when the page is closed,
    which is how deck_page_open decides whether it needs to press P.
    """

    def __init__(self, *, open_now: bool, ever_opens: bool = True):
        self.is_open = open_now
        self.ever_opens = ever_opens
        self.window = object()

    async def __call__(self, _parent, name, retries=4):
        if name == "DeckConfiguration" and not self.is_open:
            raise ValueError(f"No child window named {name}")
        return self.window


def _page_client(state: _PageState) -> FakeClient:
    """A client whose P keypress toggles the page, as the game's does."""
    client = FakeClient()
    client.page_state = state  # _CtxDeckBuilder opens the page on enter

    async def _send_key(_key, seconds=0):
        if state.ever_opens:
            state.is_open = not state.is_open

    client.send_key = AsyncMock(side_effect=_send_key)
    return client


async def test_deck_page_open_presses_p_and_closes_again(monkeypatch):
    state = _PageState(open_now=False)
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", state)
    client = _page_client(state)

    async with deck_page_open(client) as window:
        assert window is state.window
        assert state.is_open

    assert client.send_key.await_count == 2
    assert not state.is_open


async def test_deck_page_open_reopens_a_page_it_finds_already_open(monkeypatch):
    """A page someone left open could be on the treasure card view — which may
    reuse CardsInDeck — or on Gardening/Fishing/Cantrips/Castle Magic. Opening
    always lands on the deck itself, so a close/reopen makes the sub-page moot."""
    state = _PageState(open_now=True)
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", state)
    client = _page_client(state)

    async with deck_page_open(client) as window:
        assert window is state.window
        assert state.is_open

    # Closed, reopened, then closed again — an open deck page swallows clicks
    # meant for what is behind it, so it never survives the context.
    assert client.send_key.await_count == 3
    assert not state.is_open


async def test_deck_page_open_gives_up_when_an_open_page_will_not_close(monkeypatch):
    """Pressing P again would toggle something unpredictable, so it stops instead
    and lets the caller fall back to the full path."""
    state = _PageState(open_now=True, ever_opens=False)  # stuck open
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", state)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_PAGE_OPEN_TIMEOUT", 0.05)
    client = _page_client(state)

    async with deck_page_open(client) as window:
        assert window is None

    assert client.send_key.await_count == 1


async def test_deck_page_open_yields_none_when_the_page_never_appears(monkeypatch):
    """And must not press P a second time — that would open it, not close it."""
    state = _PageState(open_now=False, ever_opens=False)
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", state)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_PAGE_OPEN_TIMEOUT", 0.05)
    client = _page_client(state)

    async with deck_page_open(client) as window:
        assert window is None

    assert client.send_key.await_count == 1


async def test_deck_page_open_closes_even_when_the_body_raises(monkeypatch):
    state = _PageState(open_now=False)
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", state)
    client = _page_client(state)

    with pytest.raises(RuntimeError):
        async with deck_page_open(client):
            raise RuntimeError("read blew up")

    assert not state.is_open


async def test_deck_page_open_takes_no_mouse_handler(monkeypatch):
    """send_key is a plain window message; the mouseless hook costs ~180ms and
    governs only cursor position."""
    state = _PageState(open_now=False)
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", state)
    client = _page_client(state)

    async with deck_page_open(client):
        pass

    client.mouse_handler.__aenter__.assert_not_awaited()


async def test_deck_matches_true_when_the_deck_is_identical(monkeypatch):
    state = _PageState(open_now=False)
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", state)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _fake_item_slots)

    deck = DeckData(normal={"Tempest": 2}, item={"Sharpened Blade - Tear": 1})
    assert await deck_matches(_page_client(state), deck) is True


async def test_deck_matches_false_when_the_deck_differs(monkeypatch):
    state = _PageState(open_now=False)
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", state)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _fake_item_slots)

    assert await deck_matches(_page_client(state), DeckData(normal={"Tempest": 4})) is False


async def test_deck_matches_false_when_the_read_fails(monkeypatch):
    """A failed read means "cannot confirm", which must send the caller down the
    full path rather than propagate."""
    state = _PageState(open_now=False)
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", state)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("memory read failed")

    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _boom)

    assert await deck_matches(_page_client(state), DeckData(normal={"Tempest": 2})) is False


async def test_deck_matches_false_when_the_page_will_not_open(monkeypatch):
    state = _PageState(open_now=False, ever_opens=False)
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", state)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_PAGE_OPEN_TIMEOUT", 0.05)

    assert await deck_matches(_page_client(state), DeckData(normal={"Tempest": 2})) is False


# ---------------------------------------------------------------------------
# apply_deck fast path
# ---------------------------------------------------------------------------


async def test_apply_deck_skips_the_deck_ui_entirely_when_it_already_matches(monkeypatch):
    """The whole point: no DeckBuilder, so no tab clicks and no mouse hook."""
    state = _PageState(open_now=False)
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", state)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _fake_item_slots)
    _CtxDeckBuilder.instances = []
    monkeypatch.setattr("wizwalker.extensions.scripting.deck_builder.DeckBuilder", _CtxDeckBuilder)

    client = _page_client(state)
    deck = DeckData(normal={"Tempest": 2}, item={"Sharpened Blade - Tear": 1})
    report = await apply_deck(client, deck)

    assert report.unchanged
    assert _CtxDeckBuilder.instances == []
    client.mouse_handler.__aenter__.assert_not_awaited()


async def test_apply_deck_falls_through_to_the_full_path_on_a_mismatch(monkeypatch):
    """A real content mismatch, read from a page that opens and closes normally."""
    state = _PageState(open_now=False)
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", state)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _fake_item_slots)
    _CtxDeckBuilder.instances = []
    monkeypatch.setattr("wizwalker.extensions.scripting.deck_builder.DeckBuilder", _CtxDeckBuilder)

    # _fake_deck_names reports two Tempest; the deck wants four. The item cards
    # are given as already correct so the mismatch under test is the normal deck.
    deck = DeckData(normal={"Tempest": 4}, item={"Sharpened Blade - Tear": 1})
    report = await apply_deck(_page_client(state), deck)

    assert not report.unchanged
    assert _CtxDeckBuilder.instances[0].entered


async def test_apply_deck_does_not_fast_check_an_empty_deck(fake_deck_builder, monkeypatch):
    """Matching nothing is not a meaningful question; write_deck handles empty."""
    consulted = False

    async def _spy(*_args, **_kwargs):
        nonlocal consulted
        consulted = True
        return False

    monkeypatch.setattr("bartlebot.combat.deck_io.deck_matches", _spy)

    await apply_deck(FakeClient(), DeckData())

    assert not consulted
    assert fake_deck_builder.instances[0].entered


async def test_apply_deck_holds_one_mouse_handler_for_the_whole_write(fake_deck_builder):
    """Each `async with client.mouse_handler` inside DeckBuilder re-hooks
    MouselessCursorMoveHook at ~180ms a time; an outer hold collapses them via the
    handler's own ref counting."""
    client = FakeClient()
    await apply_deck(client, DeckData(normal={"Tempest": 4}))

    client.mouse_handler.__aenter__.assert_awaited_once()
    client.mouse_handler.__aexit__.assert_awaited_once()


# -- monkeypatch helpers ---------------------------------------------------


async def _fake_window(*_args, **_kwargs):
    return object()


async def _no_item_slots(*_args, **_kwargs):
    return []


async def _no_normal_cards(*_args, **_kwargs):
    return []


async def _fake_item_slots(*_args, **_kwargs):
    return [slot("Sharpened Blade - Tear", 0, active=True), slot("Wand-Storm-180", 5)]


async def _fake_deck_names(*_args, **_kwargs):
    return ["Tempest", "Tempest"]


async def _fake_deck_cards(*_args, **_kwargs):
    """(name, kind) pairs, as read_deck sees them â€” Tempest is an attack, so unclassified."""
    return [("Tempest", None), ("Tempest", None)]


# ---------------------------------------------------------------------------
# Tiered spell names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "base", "tier", "path"),
    [
        ("Storm Lord - T05 - A", "Storm Lord", 5, "A"),
        ("Storm Lord - T01 - B", "Storm Lord", 1, "B"),
        ("Tempest", "Tempest", None, None),
        ("Sharpened Blade", "Sharpened Blade", None, None),
    ],
)
def test_parse_tiered_name(name: str, base: str, tier: int | None, path: str | None):
    parsed = parse_tiered_name(name)
    assert (parsed.base, parsed.tier, parsed.path) == (base, tier, path)


def test_parse_tiered_name_treats_an_unparseable_tier_as_a_plain_name():
    """Guessing wrong would substitute an unrelated spell, so bail to 'plain'."""
    parsed = parse_tiered_name("Weird - Table - A")

    assert parsed.base == "Weird - Table - A"
    assert not parsed.is_tiered


def test_parse_tiered_name_handles_a_tier_with_no_path():
    parsed = parse_tiered_name("Something - T03")

    assert (parsed.base, parsed.tier, parsed.path) == ("Something", 3, None)


# ---------------------------------------------------------------------------
# resolve_tier â€” fall back to a lower owned tier, never across paths
# ---------------------------------------------------------------------------


def test_resolve_tier_prefers_an_exact_match():
    owned = ["Storm Lord - T03 - A", "Storm Lord - T05 - A"]

    assert resolve_tier("Storm Lord - T05 - A", owned) == "Storm Lord - T05 - A"


def test_resolve_tier_falls_back_to_the_highest_owned_tier_below():
    owned = ["Storm Lord - T01 - A", "Storm Lord - T03 - A", "Storm Lord - T02 - A"]

    assert resolve_tier("Storm Lord - T05 - A", owned) == "Storm Lord - T03 - A"


def test_resolve_tier_never_crosses_the_upgrade_path():
    """- A and - B are different spells, not stronger and weaker ones."""
    owned = ["Storm Lord - T05 - B"]

    assert resolve_tier("Storm Lord - T05 - A", owned) is None


def test_resolve_tier_does_not_upgrade():
    """The saved deck states what the strategy was tuned for, pips included."""
    owned = ["Storm Lord - T05 - A"]

    assert resolve_tier("Storm Lord - T03 - A", owned) is None


def test_resolve_tier_ignores_other_base_spells():
    owned = ["Glowbug Squall - T05 - A"]

    assert resolve_tier("Storm Lord - T05 - A", owned) is None


def test_resolve_tier_returns_none_for_a_plain_name_not_owned():
    assert resolve_tier("Tempest", ["Storm Lord - T05 - A"]) is None


def test_resolve_tier_with_nothing_owned():
    assert resolve_tier("Storm Lord - T05 - A", []) is None


# ---------------------------------------------------------------------------
# plan_normal_cards â€” filter before the deck is cleared
# ---------------------------------------------------------------------------


def test_plan_normal_keeps_cards_the_account_owns():
    plan = plan_normal_cards({"Tempest": 2, "Frenzy": 1}, ["Tempest", "Frenzy", "Epic"])

    assert plan.addable == {"Tempest": 2, "Frenzy": 1}
    assert plan.missing == []


def test_plan_normal_drops_cards_the_account_lacks():
    """The bug this exists for: one unowned card used to empty the whole deck."""
    plan = plan_normal_cards({"Tempest": 2, "Firestorm Blade": 4}, ["Tempest"])

    assert plan.addable == {"Tempest": 2}
    assert plan.missing == ["Firestorm Blade"]


def test_plan_normal_keeps_a_tiered_card_when_the_base_is_owned():
    """Which tiers exist is unreadable until the flyout opens, so keep and resolve later."""
    plan = plan_normal_cards({"Storm Lord - T05 - A": 1}, ["Storm Lord"])

    assert plan.addable == {"Storm Lord - T05 - A": 1}
    assert plan.missing == []


def test_plan_normal_drops_a_tiered_card_when_the_base_is_not_owned():
    plan = plan_normal_cards({"Storm Lord - T05 - A": 1}, ["Tempest"])

    assert plan.addable == {}
    assert plan.missing == ["Storm Lord - T05 - A"]


def test_plan_normal_skips_non_positive_counts():
    plan = plan_normal_cards({"Tempest": 0, "Frenzy": -1, "Epic": 2}, ["Tempest", "Frenzy", "Epic"])

    assert plan.addable == {"Epic": 2}
    assert plan.missing == []


def test_plan_normal_missing_is_sorted_for_a_stable_warning():
    plan = plan_normal_cards({"Zebra": 1, "Aardvark": 1}, [])

    assert plan.missing == ["Aardvark", "Zebra"]


def test_plan_normal_on_an_empty_deck():
    plan = plan_normal_cards({}, ["Tempest"])

    assert plan.addable == {}
    assert plan.missing == []


# ---------------------------------------------------------------------------
# Fusion spells: the two shapes a tiered spell takes in the spellbook
# ---------------------------------------------------------------------------
#
# Probed against a live client (tools/probe_spell_list.py).  The spellbook NEVER
# lists a bare base name, which is exactly the assumption wizwalker's tiered
# branch is built on:
#
#   several variants unlocked -> "Novus Storm - T01 - Base"
#       a fusion *root*; clicking it opens the variant flyout, the only place
#       the real variants are legible.
#   exactly one unlocked      -> "Novus Storm - T04 - B"
#       the variant itself, listed outright; there is no flyout to open, and
#       clicking it would add the card.

_VARIANT_CLICK_BASE = 1000  # keeps flyout click positions distinguishable


class _FakeMouse:
    def __init__(self, builder=None):
        self.builder = builder
        self.clicks: list[tuple[int, int]] = []
        #: The builder's spellbook page at each click, parallel to ``clicks``.
        self.pages: list[int] = []
        self.window_clicks: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def click(self, x, y):
        self.clicks.append((x, y))
        self.pages.append(self.builder.page if self.builder else 0)

    async def click_window(self, window=None, *_args, **_kwargs):
        name = getattr(window, "window_name", "?")
        self.window_clicks.append(name)
        if name == "CloseTSMPUnlockedPageButton" and self.builder:
            # As probed: the close restores the page from the button-driven
            # counter, which a written page never moved -- page one.
            self.builder.page = 0
        return None


class FusionRootDeckBuilder(FakeDeckBuilder):
    """A wizard with several variants unlocked: the spellbook shows the root."""

    spell_list = ["Tempest", "Frenzy", "Novus Storm - T01 - Base"]
    #: What the flyout lists once the root is clicked.
    owned_tiers = ["Novus Storm - T05 - A", "Novus Storm - T05 - B"]

    async def calculate_tiered_spell_card_position(self, card_number: int) -> tuple[int, int]:
        return (_VARIANT_CLICK_BASE + card_number, 0)

    async def get_graphical_tiered_spell_cards(self) -> list:
        self.calls.append("get_graphical_tiered_spell_cards")

        def _spell(name: str):
            template = SimpleNamespace(name=_async_returning(name))
            return SimpleNamespace(spell_template=_async_returning(template))

        return [_spell(n) for n in self.owned_tiers]

    @property
    def fusion_added(self) -> dict[str, int]:
        """Variant name -> copies clicked inside the flyout."""
        counts: dict[str, int] = {}
        for x, _y in self.client.mouse_handler.clicks:
            if x < _VARIANT_CLICK_BASE:
                continue
            name = self.owned_tiers[x - _VARIANT_CLICK_BASE - 1]
            counts[name] = counts.get(name, 0) + 1
        return counts


class SingleVariantDeckBuilder(FusionRootDeckBuilder):
    """A wizard with exactly one variant unlocked: the spellbook shows it directly."""

    spell_list = ["Tempest", "Frenzy", "Novus Storm - T04 - B"]

    async def get_graphical_tiered_spell_cards(self) -> list:
        raise AssertionError("must not open a flyout for a spell with only one variant")


def _tier_patches(monkeypatch):
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)


# -- single unlocked variant: added directly, no flyout ---------------------


async def test_single_variant_is_added_without_opening_a_flyout(monkeypatch):
    """The reported case: deck wants T05-B, this wizard has only T04-B.

    Clicking the spellbook entry of a one-variant spell ADDS it rather than
    opening anything, so the flyout must not be touched.
    """
    _tier_patches(monkeypatch)
    db = SingleVariantDeckBuilder()

    await write_deck(db, DeckData(normal={"Novus Storm - T05 - B": 4, "Tempest": 3}))

    assert db.added == {"Novus Storm - T04 - B": 4, "Tempest": 3}


async def test_single_variant_on_another_path_is_skipped(monkeypatch):
    _tier_patches(monkeypatch)
    db = SingleVariantDeckBuilder()

    await write_deck(db, DeckData(normal={"Novus Storm - T05 - A": 4, "Tempest": 3}))

    assert db.added == {"Tempest": 3}


async def test_single_variant_above_the_requested_tier_is_skipped(monkeypatch):
    """Never upgrade: the saved deck states what the strategy was tuned for."""
    _tier_patches(monkeypatch)
    db = SingleVariantDeckBuilder()

    await write_deck(db, DeckData(normal={"Novus Storm - T02 - B": 4, "Tempest": 3}))

    assert db.added == {"Tempest": 3}


# -- fusion root: resolved and clicked inside the flyout --------------------


async def test_fusion_root_adds_the_exact_variant_through_the_flyout(monkeypatch):
    """wizwalker cannot do this at all -- it looks up the bare "Novus Storm"."""
    _tier_patches(monkeypatch)
    db = FusionRootDeckBuilder()

    await write_deck(db, DeckData(normal={"Novus Storm - T05 - B": 4, "Tempest": 3}))

    assert db.fusion_added == {"Novus Storm - T05 - B": 4}
    # The root is not a placeable card, so it must never reach the deck builder.
    assert db.added == {"Tempest": 3}


async def test_fusion_root_substitutes_a_lower_unlocked_tier(monkeypatch):
    _tier_patches(monkeypatch)
    db = FusionRootDeckBuilder()
    db.owned_tiers = ["Novus Storm - T01 - B", "Novus Storm - T03 - B"]

    await write_deck(db, DeckData(normal={"Novus Storm - T05 - B": 2}))

    assert db.fusion_added == {"Novus Storm - T03 - B": 2}


async def test_fusion_root_never_crosses_paths(monkeypatch):
    _tier_patches(monkeypatch)
    db = FusionRootDeckBuilder()
    db.owned_tiers = ["Novus Storm - T05 - A", "Novus Storm - T05 - C"]

    await write_deck(db, DeckData(normal={"Novus Storm - T05 - B": 2, "Tempest": 1}))

    assert db.fusion_added == {}
    assert db.added == {"Tempest": 1}


async def test_fusion_root_opens_the_flyout_once_per_base_spell(monkeypatch):
    _tier_patches(monkeypatch)
    db = FusionRootDeckBuilder()

    await write_deck(db, DeckData(normal={"Novus Storm - T05 - A": 1, "Novus Storm - T05 - B": 2}))

    assert db.calls.count("get_graphical_tiered_spell_cards") == 1
    assert db.fusion_added == {"Novus Storm - T05 - A": 1, "Novus Storm - T05 - B": 2}


async def test_fusion_flyout_close_waits_out_the_settle_pause(monkeypatch):
    """The card-settling fix follow-up: a card confirmed in CardsInDeck could still be dropped by
    closing the flyout immediately -- reopening the deck page made it reappear,
    meaning the server hadn't caught up yet. DECK_FUSION_CLOSE_SETTLE buys it
    that time before every flyout close, confirmed cards or not.
    """
    _tier_patches(monkeypatch)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_FUSION_CLOSE_SETTLE", 1.0)
    db = FusionRootDeckBuilder()

    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def _spy_sleep(seconds):
        sleeps.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr("bartlebot.combat.deck_io.asyncio.sleep", _spy_sleep)

    await write_deck(db, DeckData(normal={"Novus Storm - T05 - B": 1}))

    assert 1.0 in sleeps
    assert db.fusion_added == {"Novus Storm - T05 - B": 1}


async def test_fusion_root_survives_an_unreadable_flyout(monkeypatch):
    """Runs after the clear, so it must never raise -- the deck is already gone."""
    _tier_patches(monkeypatch)

    class BrokenFlyout(FusionRootDeckBuilder):
        async def get_graphical_tiered_spell_cards(self):
            raise RuntimeError("variant list window missing")

    db = BrokenFlyout()

    await write_deck(db, DeckData(normal={"Novus Storm - T05 - B": 4, "Tempest": 3}))

    assert db.added == {"Tempest": 3}


async def test_no_flyout_is_opened_when_no_fusion_cards_are_wanted(monkeypatch):
    _tier_patches(monkeypatch)
    db = FusionRootDeckBuilder()

    await write_deck(db, DeckData(normal={"Tempest": 4}))

    assert "get_graphical_tiered_spell_cards" not in db.calls


async def test_a_fusion_only_deck_that_cannot_resolve_still_clears_once(monkeypatch):
    """Accepted edge case.

    Whether a fusion card resolves is only knowable with the flyout open, which
    is necessarily after the clear -- so this one case can still end with an
    empty deck. It is logged, and it costs one wasted clear rather than a
    silently wrong deck.
    """
    _tier_patches(monkeypatch)
    db = FusionRootDeckBuilder()
    db.owned_tiers = ["Novus Storm - T05 - A"]

    await write_deck(db, DeckData(normal={"Novus Storm - T05 - B": 4}))

    assert db.fusion_added == {}
    assert "clear_deck" in db.calls


# ---------------------------------------------------------------------------
# Never clear a deck we cannot refill
# ---------------------------------------------------------------------------


async def test_write_deck_leaves_the_deck_alone_when_nothing_matches(monkeypatch):
    """The reported failure: an unmatched deck cleared the wizard out entirely.

    Clearing is only the first half of a rewrite. With nothing to put back, the
    wizard is strictly better off with the deck they already had.
    """
    _tier_patches(monkeypatch)
    db = FakeDeckBuilder()

    await write_deck(db, DeckData(normal={"Novus Storm - T05 - B": 4}))

    assert "clear_deck" not in db.calls
    assert db.added is None


async def test_write_deck_leaves_the_deck_alone_when_the_spellbook_reads_empty(monkeypatch):
    """An empty spellbook is far more likely our failed read than a real account."""
    _tier_patches(monkeypatch)
    db = FakeDeckBuilder()
    db.spell_list = []

    await write_deck(db, DeckData(normal={"Tempest": 4}))

    assert "clear_deck" not in db.calls


async def test_write_deck_proceeds_when_at_least_one_card_matches(monkeypatch):
    _tier_patches(monkeypatch)
    db = FakeDeckBuilder()

    await write_deck(db, DeckData(normal={"Tempest": 2, "Novus Storm - T05 - B": 4}))

    assert "clear_deck" in db.calls
    assert db.added == {"Tempest": 2}


# ---------------------------------------------------------------------------
# Spellbook naming: match base-to-base, not name-to-base
# ---------------------------------------------------------------------------


def test_plan_normal_matches_a_spellbook_listing_a_tier_variant():
    """wizwalker assumes the spellbook lists the bare base name.

    If a build instead lists the wizard's current variant, that assumption makes
    an owned spell look unowned -- the reported Novus Storm failure.
    """
    plan = plan_normal_cards({"Novus Storm - T05 - B": 4}, ["Novus Storm - T04 - B"])

    assert plan.addable == {"Novus Storm - T05 - B": 4}
    assert plan.missing == []


def test_plan_normal_still_matches_a_spellbook_listing_a_bare_base():
    plan = plan_normal_cards({"Novus Storm - T05 - B": 4}, ["Novus Storm"])

    assert plan.addable == {"Novus Storm - T05 - B": 4}


def test_plan_normal_does_not_match_a_different_base():
    plan = plan_normal_cards({"Novus Storm - T05 - B": 4}, ["Storm Lord - T04 - B"])

    assert plan.missing == ["Novus Storm - T05 - B"]


@pytest.mark.parametrize("path", ["A", "B", "C"])
def test_resolve_tier_handles_any_number_of_upgrade_paths(path: str):
    """Paths are compared for equality, never ordered, so C works like A and B."""
    owned = [f"Novus Storm - T01 - {path}", f"Novus Storm - T03 - {path}"]

    assert resolve_tier(f"Novus Storm - T05 - {path}", owned) == f"Novus Storm - T03 - {path}"


def test_resolve_tier_does_not_cross_between_three_paths():
    owned = ["Novus Storm - T05 - A", "Novus Storm - T05 - C"]

    assert resolve_tier("Novus Storm - T05 - B", owned) is None


# ---------------------------------------------------------------------------
# Substitutions are reported, on both routing paths
# ---------------------------------------------------------------------------
#
# A substituted deck is not the deck that was saved. Reporting it was missing
# for the single-variant path, which is the one that swaps most often: it is
# what a wizard who has simply not maxed a spell hits on every apply.


def test_partition_reports_a_substitution_it_made():
    routing = partition_addable({"Novus Storm - T05 - B": 4}, ["Novus Storm - T04 - B"])

    assert routing.direct == {"Novus Storm - T04 - B": 4}
    assert routing.substitutions == {"Novus Storm - T05 - B": "Novus Storm - T04 - B"}


def test_partition_reports_no_substitution_for_an_exact_match():
    routing = partition_addable({"Tempest": 4}, ["Tempest"])

    assert routing.substitutions == {}


# ---------------------------------------------------------------------------
# A base-form entry has two spellings and two meanings
# ---------------------------------------------------------------------------
#
# "Novus Storm - T01 - Base" is the self-describing spelling; other spells name
# their base form plainly, so the spellbook lists a bare "Bunyips Rage". Reading
# only the first reported those spells as untrained.
#
# Either spelling then means one of two things, and the page looks the same for
# both: a flyout with the unlocked variants (two paths or more), or the base card
# itself (no path unlocked). Only the click tells them apart.


@pytest.mark.parametrize(
    ("requested", "entry"),
    [
        ("Novus Storm - T05 - B", "Novus Storm - T01 - Base"),  # the explicit spelling
        ("Bunyips Rage - T05 - A", "Bunyips Rage"),  # the plain one
        ("King Artorius - Storm - T05 - B", "King Artorius - Storm"),  # base name has its own " - "
    ],
)
def test_both_base_form_spellings_resolve_at_click_time(requested: str, entry: str):
    assert resolved_at_click_time(requested, entry)


@pytest.mark.parametrize(
    ("requested", "entry"),
    [
        ("Novus Storm - T05 - B", "Novus Storm - T04 - B"),  # the one unlocked path, clickable as-is
        ("Tempest", "Tempest"),  # an untiered spell asked for by its own name
    ],
)
def test_a_directly_clickable_entry_is_not_a_base_form(requested: str, entry: str):
    assert not resolved_at_click_time(requested, entry)


def test_partition_defers_a_bare_base_entry_to_click_time():
    """The multi-tier spell regression: this used to land in unresolvable and read as untrained."""
    routing = partition_addable({"Bunyips Rage - T05 - A": 3}, ["Tempest", "Bunyips Rage"])

    assert routing.fusion == {"Bunyips Rage - T05 - A": 3}
    assert routing.unresolvable == []


async def test_a_bare_base_form_is_added_through_its_flyout(monkeypatch):
    """Two paths unlocked: the spellbook shows only "Bunyips Rage", the flyout has both."""
    _tier_patches(monkeypatch)
    db = FusionRootDeckBuilder()
    db.spell_list = ["Tempest", "Frenzy", "Bunyips Rage"]
    db.owned_tiers = ["Bunyips Rage - T05 - A", "Bunyips Rage - T05 - B"]

    report = await write_deck(db, DeckData(normal={"Bunyips Rage - T05 - A": 2}))

    assert db.fusion_added == {"Bunyips Rage - T05 - A": 2}
    assert report.skipped == []
    assert report.is_clean


# --- no path unlocked: the click adds the base card and no flyout appears ---
#
# The spellbook entry is identical to the two-path case above, so this is only
# discoverable after clicking. Undoing it would be worse than finishing it: the
# base form is the same spell, weaker, and the only one this wizard has.


class NoPathDeckBuilder(FusionRootDeckBuilder):
    """A wizard with nothing unlocked: clicking the entry adds a card, no flyout."""

    spell_list = ["Tempest", "Frenzy", "Bunyips Rage"]
    base_forms = frozenset({"Bunyips Rage"})

    async def get_graphical_tiered_spell_cards(self) -> list:
        self.calls.append("get_graphical_tiered_spell_cards")
        raise ValueError("TieredSpellMPUnlockedList not found")

    @property
    def base_form_clicks(self) -> int:
        """Clicks that landed on the spellbook entry rather than inside a flyout."""
        return sum(1 for x, _y in self.client.mouse_handler.clicks if x < _VARIANT_CLICK_BASE)


async def test_no_unlocked_path_places_the_base_card(monkeypatch):
    _tier_patches(monkeypatch)
    db = NoPathDeckBuilder()

    report = await write_deck(db, DeckData(normal={"Bunyips Rage - T05 - A": 3}))

    assert db.base_form_clicks == 3
    assert report.skipped == []
    assert report.substitutions == {"Bunyips Rage - T05 - A": "Bunyips Rage"}


async def test_no_unlocked_path_collapses_every_variant_onto_one_card(monkeypatch):
    """Both requested paths become the same base card, so the copies are summed."""
    _tier_patches(monkeypatch)
    db = NoPathDeckBuilder()

    report = await write_deck(db, DeckData(normal={"Bunyips Rage - T05 - A": 2, "Bunyips Rage - T05 - B": 1}))

    assert db.base_form_clicks == 3
    assert report.substitutions == {
        "Bunyips Rage - T05 - A": "Bunyips Rage",
        "Bunyips Rage - T05 - B": "Bunyips Rage",
    }


async def test_no_unlocked_path_expects_the_base_card_when_settling(monkeypatch):
    """A skip here would leave _settle_deck waiting out its timeout on a correct deck."""
    _tier_patches(monkeypatch)
    db = NoPathDeckBuilder()

    report = await write_deck(db, DeckData(normal={"Bunyips Rage - T05 - A": 2}))

    assert expected_normal_deck({}, {"Bunyips Rage": 2}) == {"Bunyips Rage": 2}
    assert report.substitutions == {"Bunyips Rage - T05 - A": "Bunyips Rage"}


async def test_no_unlocked_path_is_reported_as_a_substitution(monkeypatch, caplog):
    _tier_patches(monkeypatch)
    db = NoPathDeckBuilder()

    with caplog.at_level(logging.INFO, logger="bartlebot.combat.deck_io"):
        await write_deck(db, DeckData(normal={"Bunyips Rage - T05 - A": 1}))

    assert "no upgrade path unlocked" in caplog.text


async def test_a_failed_flyout_read_still_skips(monkeypatch, caplog):
    """Only a missing window means "nothing unlocked" — a read error is still our bug."""
    _tier_patches(monkeypatch)
    db = NoPathDeckBuilder()

    async def _boom() -> list:
        raise MemoryError("read failed")

    db.get_graphical_tiered_spell_cards = _boom

    with caplog.at_level(logging.WARNING, logger="bartlebot.combat.deck_io"):
        report = await write_deck(db, DeckData(normal={"Bunyips Rage - T05 - A": 2}))

    assert report.skipped == ["Bunyips Rage - T05 - A"]
    assert report.substitutions == {}
    assert "Could not open the variant list" in caplog.text


def test_partition_does_not_pre_report_fusion_substitutions():
    """Which variant a fusion root resolves to is only known with the flyout open."""
    routing = partition_addable({"Novus Storm - T05 - B": 4}, ["Novus Storm - T01 - Base"])

    assert routing.fusion == {"Novus Storm - T05 - B": 4}
    assert routing.substitutions == {}


async def test_single_variant_substitution_is_logged(monkeypatch, caplog):
    """The reported gap: this path swapped the tier without saying so."""
    _tier_patches(monkeypatch)
    db = SingleVariantDeckBuilder()

    with caplog.at_level(logging.INFO, logger="bartlebot.combat.deck_io"):
        await write_deck(db, DeckData(normal={"Novus Storm - T05 - B": 4}))

    assert "Novus Storm - T04 - B" in caplog.text
    assert "Novus Storm - T05 - B" in caplog.text
    assert "in place of" in caplog.text


async def test_fusion_substitution_is_logged(monkeypatch, caplog):
    _tier_patches(monkeypatch)
    db = FusionRootDeckBuilder()
    db.owned_tiers = ["Novus Storm - T03 - B"]

    with caplog.at_level(logging.INFO, logger="bartlebot.combat.deck_io"):
        await write_deck(db, DeckData(normal={"Novus Storm - T05 - B": 2}))

    assert "Novus Storm - T03 - B" in caplog.text
    assert "in place of" in caplog.text


async def test_both_paths_word_the_substitution_identically(monkeypatch, caplog):
    """One wording, so a reader does not have to learn that two lines mean the same."""
    _tier_patches(monkeypatch)

    with caplog.at_level(logging.INFO, logger="bartlebot.combat.deck_io"):
        await write_deck(SingleVariantDeckBuilder(), DeckData(normal={"Novus Storm - T05 - B": 1}))
    direct_line = next(m for m in caplog.messages if "in place of" in m)

    caplog.clear()
    fusion_db = FusionRootDeckBuilder()
    fusion_db.owned_tiers = ["Novus Storm - T04 - B"]
    with caplog.at_level(logging.INFO, logger="bartlebot.combat.deck_io"):
        await write_deck(fusion_db, DeckData(normal={"Novus Storm - T05 - B": 1}))
    fusion_line = next(m for m in caplog.messages if "in place of" in m)

    assert direct_line == fusion_line


async def test_an_exact_match_logs_no_substitution(monkeypatch, caplog):
    """No crying wolf on a wizard whose spells already match the strategy."""
    _tier_patches(monkeypatch)
    db = FusionRootDeckBuilder()

    with caplog.at_level(logging.INFO, logger="bartlebot.combat.deck_io"):
        await write_deck(db, DeckData(normal={"Novus Storm - T05 - B": 4, "Tempest": 2}))

    assert "in place of" not in caplog.text


# ---------------------------------------------------------------------------
# DeckApplyReport
# ---------------------------------------------------------------------------
#
# A partial apply used to be indistinguishable from a clean one: write_deck
# returned None and logged, so a wizard missing three of a shared deck's cards
# got a half-built deck and no sign anything was wrong.


async def test_a_clean_apply_reports_nothing_to_say(monkeypatch):
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)

    report = await write_deck(FakeDeckBuilder(), DeckData(normal={"Tempest": 4}))

    assert report.is_clean
    assert report.applied is True


async def test_skipped_cards_reach_the_caller(monkeypatch):
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)

    report = await write_deck(FakeDeckBuilder(), DeckData(normal={"Tempest": 2, "Inferno Blade": 3}))

    assert report.skipped == ["Inferno Blade"]
    assert not report.is_clean


async def test_a_tier_substitution_reaches_the_caller(monkeypatch):
    _tier_patches(monkeypatch)

    report = await write_deck(SingleVariantDeckBuilder(), DeckData(normal={"Novus Storm - T05 - B": 4}))

    assert report.substitutions == {"Novus Storm - T05 - B": "Novus Storm - T04 - B"}
    assert not report.is_clean


async def test_a_fusion_substitution_reaches_the_caller(monkeypatch):
    """Decided inside the flyout, so nothing above can recompute it."""
    _tier_patches(monkeypatch)
    db = FusionRootDeckBuilder()
    db.owned_tiers = ["Novus Storm - T03 - B"]

    report = await write_deck(db, DeckData(normal={"Novus Storm - T05 - B": 2}))

    assert report.substitutions == {"Novus Storm - T05 - B": "Novus Storm - T03 - B"}


async def test_an_unresolvable_fusion_card_is_reported_as_skipped(monkeypatch):
    _tier_patches(monkeypatch)
    db = FusionRootDeckBuilder()
    db.owned_tiers = ["Novus Storm - T05 - A"]  # wrong path

    report = await write_deck(db, DeckData(normal={"Novus Storm - T05 - B": 2, "Tempest": 1}))

    assert "Novus Storm - T05 - B" in report.skipped


async def test_an_unreadable_flyout_reports_its_cards_as_skipped(monkeypatch):
    """Runs after the clear, so those cards really are gone from the deck."""
    _tier_patches(monkeypatch)

    class BrokenFlyout(FusionRootDeckBuilder):
        async def get_graphical_tiered_spell_cards(self):
            raise RuntimeError("variant list window missing")

    report = await write_deck(BrokenFlyout(), DeckData(normal={"Novus Storm - T05 - B": 4, "Tempest": 3}))

    assert report.skipped == ["Novus Storm - T05 - B"]


async def test_an_untouched_deck_reports_that_it_was_untouched(monkeypatch):
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)

    report = await write_deck(FakeDeckBuilder(), DeckData(normal={"Inferno Blade": 3}))

    assert report.applied is False
    assert report.skipped == ["Inferno Blade"]


async def test_a_matching_deck_reports_unchanged(monkeypatch):
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)

    report = await write_deck(FakeDeckBuilder(), DeckData(normal={"Tempest": 2}))

    assert report.unchanged is True
    assert report.is_clean


# ---------------------------------------------------------------------------
# Card kinds and alternatives
# ---------------------------------------------------------------------------


def test_alternatives_offer_this_wizards_cards_of_the_same_kind():
    deck = DeckData(normal={"Inferno Blade": 3}, card_kinds={"Inferno Blade": "blade"})

    out = suggest_alternatives(["Inferno Blade"], deck, {"Storm Blade": "blade", "Feint": "trap"})

    assert out == {"Inferno Blade": ["Storm Blade"]}


def test_alternatives_are_not_offered_for_an_unclassified_card():
    """An attack spell has no stand-in; silence beats a wrong suggestion."""
    deck = DeckData(normal={"Tempest": 3})

    assert suggest_alternatives(["Tempest"], deck, {"Storm Blade": "blade"}) == {}


def test_alternatives_skip_the_card_that_was_requested():
    deck = DeckData(normal={"Storm Blade": 3}, card_kinds={"Storm Blade": "blade"})

    out = suggest_alternatives(["Storm Blade"], deck, {"Storm Blade": "blade"})

    assert out == {}


def test_alternatives_are_empty_when_the_wizard_has_none_of_that_kind():
    deck = DeckData(normal={"Inferno Blade": 3}, card_kinds={"Inferno Blade": "blade"})

    assert suggest_alternatives(["Inferno Blade"], deck, {"Feint": "trap"}) == {}


def test_alternatives_are_sorted_so_the_message_is_stable():
    deck = DeckData(normal={"Inferno Blade": 1}, card_kinds={"Inferno Blade": "blade"})

    out = suggest_alternatives(["Inferno Blade"], deck, {"Storm Blade": "blade", "Balanceblade": "blade"})

    assert out["Inferno Blade"] == ["Balanceblade", "Storm Blade"]


async def test_a_skipped_card_carries_its_recorded_kind_into_the_report(monkeypatch):
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    deck = DeckData(normal={"Tempest": 2, "Inferno Blade": 3}, card_kinds={"Inferno Blade": "blade"})

    report = await write_deck(FakeDeckBuilder(), deck)

    assert report.kinds == {"Inferno Blade": "blade"}


async def test_a_deck_saved_before_kinds_existed_still_applies(monkeypatch):
    """card_kinds is absent from every deck saved so far."""
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()

    report = await write_deck(db, DeckData(normal={"Tempest": 2, "Inferno Blade": 3}))

    assert db.added == {"Tempest": 2}
    assert report.kinds == {}
    assert report.alternatives == {}


# ---------------------------------------------------------------------------
# Spellbook pages are written, never paged to
# ---------------------------------------------------------------------------
#
# Clicking through the spellbook cost a click per page plus a full rewind from an
# unknown position (#48). The page is now written directly, and because a flyout
# close restores the page from a counter a write never moves, every base writes
# its page again. The fake resets the page on close, so a pass that trusted the
# page it was left on would click a filler card.


async def _named_window(_parent, name, *_args, **_kwargs):
    return SimpleNamespace(window_name=name)


class MultiPageDeckBuilder(FusionRootDeckBuilder):
    """A spellbook long enough to page through, with three fusion spells on it."""

    spell_list = (
        [f"Filler {i}" for i in range(7)]
        + ["Thunder Snake"]  # page 1
        + [f"Filler {i}" for i in range(7, 13)]
        + ["Lightning Bats"]  # page 2
        + [f"Filler {i}" for i in range(13, 19)]
        + ["Bunyips Rage"]  # page 3
    )
    base_forms = frozenset({"Thunder Snake", "Lightning Bats", "Bunyips Rage"})

    def __init__(self):
        super().__init__()
        #: Bases whose flyout was opened, in the order the sweep reached them.
        self.flyout_order: list[str] = []

    async def get_graphical_tiered_spell_cards(self) -> list:
        """Every base offers the same two paths, so the flyout is always populated."""
        base = self.last_clicked_base
        self.flyout_order.append(base)
        self.owned_tiers = [f"{base} - T05 - A", f"{base} - T05 - B"]
        return await super().get_graphical_tiered_spell_cards()

    @property
    def last_clicked_base(self) -> str:
        """The fusion base the most recent spellbook click landed on, given the page it was on."""
        mouse = self.client.mouse_handler
        x, page = next(
            (x, page) for (x, _y), page in reversed(list(zip(mouse.clicks, mouse.pages))) if x < _VARIANT_CLICK_BASE
        )
        name = self.spell_list[page * self.CARDS_PER_PAGE + x - 1]
        if name not in self.base_forms:
            raise AssertionError(f"the click landed on {name!r}, not a fusion base")
        return name

    @property
    def page_writes(self) -> list[int]:
        return [int(call.split(":")[1]) for call in self.calls if call.startswith("set_page:")]


def _sweep_patches(monkeypatch):
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _named_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)


_THREE_BASES = {"Bunyips Rage - T05 - A": 1, "Lightning Bats - T05 - A": 1, "Thunder Snake - T05 - A": 1}


async def test_fusion_pass_writes_each_bases_page(monkeypatch):
    """Every base lands after a flyout close reset the page, so none can skip its write."""
    _sweep_patches(monkeypatch)
    db = MultiPageDeckBuilder()

    await write_deck(db, DeckData(normal=_THREE_BASES))

    assert db.page_writes == [1, 2, 3]
    assert db.flyout_order == ["Thunder Snake", "Lightning Bats", "Bunyips Rage"]


async def test_fusion_pass_never_clicks_the_page_buttons(monkeypatch):
    _sweep_patches(monkeypatch)
    db = MultiPageDeckBuilder()

    await write_deck(db, DeckData(normal=_THREE_BASES))

    assert not {"PageUp", "PageDown"} & set(db.client.mouse_handler.window_clicks)


async def test_fusion_pass_visits_bases_in_page_order(monkeypatch):
    """Deck order is arbitrary; the sweep follows the spellbook."""
    _sweep_patches(monkeypatch)
    db = MultiPageDeckBuilder()

    await write_deck(
        db,
        DeckData(normal={"Bunyips Rage - T05 - A": 1, "Thunder Snake - T05 - A": 1}),
    )

    assert db.flyout_order == ["Thunder Snake", "Bunyips Rage"]  # pages 1 then 3
    assert db.page_writes == [1, 3]


async def test_a_reopened_deck_page_is_written_back_to_the_next_bases_page(monkeypatch):
    """The close fallback reopens the page on page one; the next write puts it right."""
    _sweep_patches(monkeypatch)
    db = MultiPageDeckBuilder()

    async def _no_close_button(parent, name, *args, **kwargs):
        if name == "CloseTSMPUnlockedPageButton":
            raise ValueError("no close button")
        return SimpleNamespace(window_name=name)

    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _no_close_button)

    await write_deck(
        db,
        DeckData(normal={"Thunder Snake - T05 - A": 1, "Lightning Bats - T05 - A": 1}),
    )

    assert db.calls.count("refresh_deck_page") == 2  # the fallback fired for each base
    assert db.flyout_order == ["Thunder Snake", "Lightning Bats"]  # both clicks found their base
    assert db.page_writes == [1, 2]


async def test_direct_cards_are_placed_page_by_page(monkeypatch):
    _sweep_patches(monkeypatch)
    db = MultiPageDeckBuilder()

    await write_deck(db, DeckData(normal={"Filler 13": 2, "Filler 0": 1}))  # pages 2 and 0

    assert db.page_writes == [0, 2]
    assert db.added == {"Filler 0": 1, "Filler 13": 2}
    assert not {"PageUp", "PageDown"} & set(db.client.mouse_handler.window_clicks)


async def test_spell_list_names_keeps_an_unreadable_entry_as_a_blank():
    """wizwalker's own list drops it, which shifts every later card one slot early."""
    db = FakeDeckBuilder()
    db.spell_list = ["Tempest", None, "Frenzy"]

    assert await _spell_list_names(db) == ["Tempest", "", "Frenzy"]


def test_direct_requesters_undoes_the_tier_fold():
    addable = {"Novus Storm - T05 - B": 2, "Novus Storm - T04 - B": 1, "Tempest": 3}
    routing = partition_addable(addable, ["Tempest", "Novus Storm - T04 - B"])

    assert direct_requesters(addable, routing) == {
        "Novus Storm - T04 - B": {"Novus Storm - T05 - B": 2, "Novus Storm - T04 - B": 1},
        "Tempest": {"Tempest": 3},
    }


# ---------------------------------------------------------------------------
# Every fusion click is confirmed against the deck's card count
# ---------------------------------------------------------------------------
#
# The flyout used to be closed 50 ms after the last click of each base, which is
# long enough to lose it — the same bug _settle_deck was written for, one scope
# in. No fixed delay can tell a slow click from a dropped one, so the clicks are
# now counted in: click, poll CardsInDeck, re-click only what is still missing.


def _counting_deck(mouse, *, at_variant=True, lost=0, cap=None):
    """A deck whose card count follows the fake mouse's clicks.

    ``lost`` swallows that many clicks outright, modelling the reported bug.
    ``cap`` stops the deck growing past a point, modelling the 64-card limit and
    per-spell copy limits — a refusal the count cannot distinguish from a drop.
    """

    async def count(_name: str) -> int:
        landed = sum(1 for x, _y in mouse.clicks if (x >= _VARIANT_CLICK_BASE) is at_variant)
        landed = max(0, landed - lost)
        return min(landed, cap) if cap is not None else landed

    return count


def _use_deck(monkeypatch, counter):
    monkeypatch.setattr("bartlebot.combat.deck_io.card_counter", lambda *_args: counter)


def _two_path_builder():
    db = FusionRootDeckBuilder()
    db.owned_tiers = ["Novus Storm - T05 - A", "Novus Storm - T05 - B"]
    return db


async def test_a_dropped_variant_click_is_clicked_again(monkeypatch):
    """The reported symptom: a click that never landed used to be lost silently."""
    _tier_patches(monkeypatch)
    db = _two_path_builder()
    _use_deck(monkeypatch, _counting_deck(db.client.mouse_handler, lost=1))

    report = await write_deck(db, DeckData(normal={"Novus Storm - T05 - A": 2}))

    assert db.fusion_added == {"Novus Storm - T05 - A": 3}  # two asked for, one re-clicked
    assert report.is_clean


async def test_a_card_the_deck_never_takes_is_reported_skipped(monkeypatch):
    _tier_patches(monkeypatch)
    db = _two_path_builder()
    _use_deck(monkeypatch, _counting_deck(db.client.mouse_handler, cap=0))

    report = await write_deck(db, DeckData(normal={"Novus Storm - T05 - A": 2}))

    assert report.skipped == ["Novus Storm - T05 - A"]
    assert report.substitutions == {}  # nothing went in, so nothing was substituted
    assert report.short == {}  # reported as absent, not as partial


async def test_retries_are_bounded_when_the_deck_keeps_refusing(monkeypatch):
    """A full deck refuses every click; retrying forever would hang the apply."""
    _tier_patches(monkeypatch)
    db = _two_path_builder()
    _use_deck(monkeypatch, _counting_deck(db.client.mouse_handler, cap=0))

    await write_deck(db, DeckData(normal={"Novus Storm - T05 - A": 1}))

    assert sum(db.fusion_added.values()) == CARD_CLICK_ATTEMPTS


async def test_a_partly_placed_card_is_reported_not_hidden(monkeypatch):
    """Two of three copies is neither clean nor missing, and used to look clean."""
    _tier_patches(monkeypatch)
    db = _two_path_builder()
    _use_deck(monkeypatch, _counting_deck(db.client.mouse_handler, cap=2))

    report = await write_deck(db, DeckData(normal={"Novus Storm - T05 - A": 3}))

    assert report.short == {"Novus Storm - T05 - A": (2, 3)}
    assert not report.is_clean
    assert report.skipped == []  # the card is in the deck, just not in full


async def test_the_settle_expects_only_the_copies_that_landed(monkeypatch):
    """Expecting three when two went in would burn the full settle timeout."""
    _tier_patches(monkeypatch)
    db = _two_path_builder()
    _use_deck(monkeypatch, _counting_deck(db.client.mouse_handler, cap=2))
    expected: dict[str, int] = {}

    async def _spy_settle(_client, _window, expected_normal, _desired_item, **_kwargs):
        expected.update(expected_normal)
        return True

    monkeypatch.setattr("bartlebot.combat.deck_io._settle_deck", _spy_settle)

    await write_deck(db, DeckData(normal={"Novus Storm - T05 - A": 3}))

    assert expected == {"Novus Storm - T05 - A": 2}


async def test_an_unreadable_count_falls_back_to_clicking_once(monkeypatch):
    """A broken read is not evidence of a failed click; retrying would duplicate."""
    _tier_patches(monkeypatch)
    db = _two_path_builder()

    async def _unreadable(_name) -> None:
        return None

    _use_deck(monkeypatch, _unreadable)

    report = await write_deck(db, DeckData(normal={"Novus Storm - T05 - A": 2}))

    assert db.fusion_added == {"Novus Storm - T05 - A": 2}  # clicked once each, as before
    assert report.is_clean


# -- direct cards are confirmed the same way --------------------------------


async def test_a_swallowed_direct_click_is_clicked_again(monkeypatch):
    """wizwalker's sweep trusted every click after a fixed pause."""
    _tier_patches(monkeypatch)
    db = FakeDeckBuilder()
    _use_deck(monkeypatch, _counting_deck(db.client.mouse_handler, at_variant=False, lost=1))

    report = await write_deck(db, DeckData(normal={"Tempest": 2}))

    assert db.added == {"Tempest": 3}  # two asked for, one re-clicked
    assert report.is_clean


async def test_a_direct_card_the_deck_never_takes_is_skipped_not_substituted(monkeypatch):
    _tier_patches(monkeypatch)
    db = SingleVariantDeckBuilder()
    _use_deck(monkeypatch, _counting_deck(db.client.mouse_handler, at_variant=False, cap=0))

    report = await write_deck(db, DeckData(normal={"Novus Storm - T05 - B": 2}))

    assert report.skipped == ["Novus Storm - T05 - B"]  # the requested name, not the entry
    assert report.substitutions == {}


async def test_a_partly_placed_direct_card_is_reported_under_its_requested_name(monkeypatch):
    _tier_patches(monkeypatch)
    db = SingleVariantDeckBuilder()
    _use_deck(monkeypatch, _counting_deck(db.client.mouse_handler, at_variant=False, cap=1))

    report = await write_deck(db, DeckData(normal={"Novus Storm - T05 - B": 2}))

    assert report.short == {"Novus Storm - T05 - B": (1, 2)}
    assert report.substitutions == {"Novus Storm - T05 - B": "Novus Storm - T04 - B"}


async def test_the_settle_expects_only_the_direct_copies_that_landed(monkeypatch):
    _tier_patches(monkeypatch)
    db = FakeDeckBuilder()
    _use_deck(monkeypatch, _counting_deck(db.client.mouse_handler, at_variant=False, cap=2))
    expected: dict[str, int] = {}

    async def _spy_settle(_client, _window, expected_normal, _desired_item, **_kwargs):
        expected.update(expected_normal)
        return True

    monkeypatch.setattr("bartlebot.combat.deck_io._settle_deck", _spy_settle)

    await write_deck(db, DeckData(normal={"Tempest": 3}))

    assert expected == {"Tempest": 2}


# -- the base-form path counts its opening click too ------------------------


async def test_the_flyout_opening_click_is_counted_not_assumed(monkeypatch):
    """It adds a card only when no flyout opens, so whether it did is measured."""
    _tier_patches(monkeypatch)
    db = NoPathDeckBuilder()
    _use_deck(monkeypatch, _counting_deck(db.client.mouse_handler, at_variant=False))

    report = await write_deck(db, DeckData(normal={"Bunyips Rage - T05 - A": 3}))

    assert db.base_form_clicks == 3  # the opening click counted, then two more
    assert report.substitutions == {"Bunyips Rage - T05 - A": "Bunyips Rage"}
    assert report.short == {}  # all three copies in, even though each is a downgrade


async def test_a_base_form_that_never_lands_is_not_called_a_substitution(monkeypatch):
    _tier_patches(monkeypatch)
    db = NoPathDeckBuilder()
    _use_deck(monkeypatch, _counting_deck(db.client.mouse_handler, at_variant=False, cap=0))

    report = await write_deck(db, DeckData(normal={"Bunyips Rage - T05 - A": 2}))

    assert report.skipped == ["Bunyips Rage - T05 - A"]
    assert report.substitutions == {}


def test_shortfall_is_shared_out_across_the_names_that_asked():
    """Variants collapse onto one card, so the per-name counts must still total."""
    short = _share_shortfall({"X - T05 - A": 2, "X - T05 - B": 1}, landed=2)

    assert short == {"X - T05 - B": 0}  # the first request filled, the second did not


def test_shortfall_reports_a_partly_filled_request():
    short = _share_shortfall({"X - T05 - A": 3}, landed=1)

    assert short == {"X - T05 - A": 1}


# ---------------------------------------------------------------------------
# #107: a count that trails the clicks
# ---------------------------------------------------------------------------
#
# Epic x3 confirmed one copy, the retries clicked four more, the deck came out
# holding the copy limit, and the settle then waited 15s for the one copy the
# tally had confirmed. Copies are now counted by name, fresh, and clicked again
# only once the count has stopped moving; an extra copy is trimmed at the settle.


def _lagging_deck(mouse, *, prompt=1, lag_reads=5):
    """A deck that shows ``prompt`` copies at once and the rest ``lag_reads`` reads after the last click."""
    state = {"clicks": 0, "reads": 0}

    async def count(_name: str) -> int:
        clicks = sum(1 for x, _y in mouse.clicks if x < _VARIANT_CLICK_BASE)
        if clicks != state["clicks"]:
            state["clicks"], state["reads"] = clicks, 0
        state["reads"] += 1
        return clicks if state["reads"] > lag_reads else min(clicks, prompt)

    return count


def _scripted_deck(readings: list[int]):
    """A deck whose count reads back ``readings`` in turn, then holds the last one."""
    queue = list(readings)

    async def count(_name: str) -> int:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return count


async def test_copies_that_show_up_late_are_not_clicked_again(monkeypatch):
    """The reported bug: one of three copies showed at once and the rest were re-clicked."""
    _tier_patches(monkeypatch)
    # A ceiling the lag stays well inside, so the wait sees the copies arrive.
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_CARD_STABLE_WINDOW", 5.0)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_CARD_LAND_TIMEOUT", 5.0)
    db = FakeDeckBuilder()
    _use_deck(monkeypatch, _lagging_deck(db.client.mouse_handler))

    report = await write_deck(db, DeckData(normal={"Epic": 3}))

    assert db.added == {"Epic": 3}
    assert report.is_clean


async def test_a_copy_landing_between_rounds_is_not_clicked_again(monkeypatch):
    """The re-click is sized from a read taken just before it, not from the last round's tally."""
    _tier_patches(monkeypatch)
    db = FakeDeckBuilder()
    # The baseline, the round's wait giving up at one, then all three by the re-click.
    _use_deck(monkeypatch, _scripted_deck([0, 1, 3]))

    report = await write_deck(db, DeckData(normal={"Epic": 3}))

    assert db.added == {"Epic": 3}
    assert report.is_clean


async def test_a_stalled_count_is_clicked_up_only_by_what_is_missing(monkeypatch):
    _tier_patches(monkeypatch)
    db = FakeDeckBuilder()
    _use_deck(monkeypatch, _scripted_deck([0, 1, 1, 3]))

    await write_deck(db, DeckData(normal={"Epic": 3}))

    assert db.added == {"Epic": 5}  # three, then the two the deck still lacked


async def test_late_copies_count_in_the_report_and_the_settle(monkeypatch, caplog):
    """The settle used to wait on the confirmed count, and time out against a deck that was right."""
    _tier_patches(monkeypatch)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_TIMEOUT", 5.0)
    db = FakeDeckBuilder()
    _use_deck(monkeypatch, _counting_deck(db.client.mouse_handler, at_variant=False, cap=1))

    async def _all_three(*_args, **_kwargs):
        return ["Epic", "Epic", "Epic"]

    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _all_three)

    with caplog.at_level(logging.WARNING, logger="bartlebot.combat.deck_io"):
        report = await write_deck(db, DeckData(normal={"Epic": 3}))

    assert "did not settle" not in caplog.text
    assert report.short == {}
    assert report.is_clean


async def test_the_settle_is_told_how_many_copies_were_asked_for(monkeypatch):
    _tier_patches(monkeypatch)
    db = FakeDeckBuilder()
    _use_deck(monkeypatch, _counting_deck(db.client.mouse_handler, at_variant=False, cap=1))
    seen: dict = {}

    async def _spy_settle(_client, _window, expected_normal, _desired_item, **kwargs):
        seen.update(floor=expected_normal, ceiling=kwargs["ceiling"])
        return True

    monkeypatch.setattr("bartlebot.combat.deck_io._settle_deck", _spy_settle)

    await write_deck(db, DeckData(normal={"Epic": 3}))

    assert seen == {"floor": {"Epic": 1}, "ceiling": {"Epic": 3}}


async def test_an_extra_copy_that_cannot_be_trimmed_is_reported(monkeypatch):
    _tier_patches(monkeypatch)
    db = FakeDeckBuilder()
    _use_deck(monkeypatch, _counting_deck(db.client.mouse_handler, at_variant=False))

    async def _four(*_args, **_kwargs):
        return ["Epic"] * 4

    async def _no_trim(*_args, **_kwargs):
        return False

    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _four)
    monkeypatch.setattr("bartlebot.combat.deck_io._trim_copy", _no_trim)

    report = await write_deck(db, DeckData(normal={"Epic": 3}))

    assert report.over == {"Epic": (4, 3)}
    assert not report.is_clean


async def test_settle_takes_an_extra_copy_back_out(monkeypatch):
    deck = ["Epic"] * 4 + ["Tri Blade"] * 3
    trimmed: list[str] = []

    async def _names(*_args, **_kwargs):
        return list(deck)

    async def _trim(name):
        trimmed.append(name)
        deck.remove(name)
        return True

    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _names)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_TIMEOUT", 5.0)

    settled = await _settle_deck(
        FakeClient(), object(), {"Epic": 1, "Tri Blade": 3}, {}, ceiling={"Epic": 3, "Tri Blade": 3}, trim=_trim
    )

    assert settled is True
    assert trimmed == ["Epic"]


async def test_settle_stops_trimming_after_a_trim_fails(monkeypatch):
    """A failed trim may have taken out the wrong card; repeating it would take out more."""
    calls: list[str] = []

    async def _four(*_args, **_kwargs):
        return ["Epic"] * 4

    async def _trim(name):
        calls.append(name)
        return False

    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _four)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_TIMEOUT", 0.05)

    settled = await _settle_deck(FakeClient(), object(), {"Epic": 3}, {}, ceiling={"Epic": 3}, trim=_trim)

    assert settled is False
    assert calls == ["Epic"]


async def test_settle_accepts_a_steady_deck_short_of_the_request(monkeypatch):
    """Copies placement gave up on may yet arrive, but a deck that has stopped changing is done."""

    async def _two(*_args, **_kwargs):
        return ["Epic", "Epic"]

    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _two)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_TIMEOUT", 5.0)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_CARD_STABLE_WINDOW", 0.02)

    assert await _settle_deck(FakeClient(), object(), {"Epic": 1}, {}, ceiling={"Epic": 3}) is True


async def test_settle_without_a_ceiling_still_wants_an_exact_match(monkeypatch):
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _fake_deck_names)  # two Tempest
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    monkeypatch.setattr("bartlebot.combat.deck_io.DECK_SETTLE_TIMEOUT", 0.05)

    assert await _settle_deck(FakeClient(), object(), {"Tempest": 1}, {}) is False


def test_tally_reports_short_by_requested_name_and_over_by_card():
    asked = {"Epic": {"Epic": 3}, "X - T04 - B": {"X - T05 - B": 2, "X - T04 - B": 1}}

    short, over = tally_against_request(asked, {"Epic": 4, "X - T04 - B": 2})

    assert over == {"Epic": (4, 3)}
    assert short == {"X - T04 - B": (0, 1)}


def test_requested_per_card_folds_fusion_names_onto_what_they_resolved_to():
    asked = requested_per_card(
        {"Tempest": {"Tempest": 2}},
        {"Novus Storm - T05 - A": 1, "Bunyips Rage - T05 - A": 2},
        {"Bunyips Rage - T05 - A": "Bunyips Rage"},
    )

    assert asked == {
        "Tempest": {"Tempest": 2},
        "Novus Storm - T05 - A": {"Novus Storm - T05 - A": 1},
        "Bunyips Rage": {"Bunyips Rage - T05 - A": 2},
    }


class _TrimBuilder(FakeDeckBuilder):
    async def calculate_deck_card_position(self, card_number: int) -> tuple[int, int]:
        return (1000 + card_number, 0)


def _trim_patches(monkeypatch, entries: list[str], after_click: list[str]):
    async def _entries(*_args, **_kwargs):
        return list(entries)

    async def _names(*_args, **_kwargs):
        return list(after_click)

    monkeypatch.setattr("bartlebot.combat.deck_io._deck_entry_names", _entries)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _names)


async def test_trim_clicks_the_cards_last_copy_in_the_deck(monkeypatch):
    _trim_patches(monkeypatch, ["Epic", "Tri Blade", "Epic"], ["Epic", "Tri Blade"])
    db = _TrimBuilder()

    assert await _trim_copy(db, object(), "Epic") is True
    assert db.client.mouse_handler.clicks == [(1003, 0)]  # vector index 2, the grid's third slot


async def test_trim_that_takes_out_another_card_reports_failure(monkeypatch, caplog):
    _trim_patches(monkeypatch, ["Epic", "Tri Blade", "Epic"], ["Epic", "Epic"])
    db = _TrimBuilder()

    with caplog.at_level(logging.WARNING, logger="bartlebot.combat.deck_io"):
        assert await _trim_copy(db, object(), "Epic") is False
    assert "took out Tri Blade instead" in caplog.text


async def test_trim_of_a_card_not_in_the_deck_clicks_nothing(monkeypatch):
    _trim_patches(monkeypatch, ["Tri Blade"], ["Tri Blade"])
    db = _TrimBuilder()

    assert await _trim_copy(db, object(), "Epic") is False
    assert db.client.mouse_handler.clicks == []


async def test_log_lines_carry_the_slot_of_the_apply_they_belong_to(caplog):
    """Four applies run at once, and their lines interleave."""

    async def _one(slot: int) -> None:
        _apply_slot.set(slot)
        await asyncio.sleep(0)
        deck_logger.warning("placing")

    with caplog.at_level(logging.WARNING, logger="bartlebot.combat.deck_io"):
        await asyncio.gather(_one(1), _one(2))
        deck_logger.warning("outside")

    assert sorted(r.getMessage() for r in caplog.records) == ["[P1] placing", "[P2] placing", "outside"]


# ---------------------------------------------------------------------------
# Archmastery school
#
# The menu is invisible to the window tree, so the open/closed test is geometry:
# the icons are parked stacked behind the gauge and fan out when it opens.
# ---------------------------------------------------------------------------


class FakeRect:
    """Stands in for wizwalker's Rectangle.

    Deliberately does *not* implement __iter__: the real one yields
    (x1, x2, y1, y2), not the (x1, y1, x2, y2) the name suggests, and code that
    unpacks it is wrong. Leaving it out here keeps that mistake a test failure.
    """

    def __init__(self, x1, y1, x2, y2):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2


def _stacked(count: int = 7, size: int = 40) -> list[FakeRect]:
    """Every icon at the same place — the menu closed."""
    return [FakeRect(100, 200, 100 + size, 200 + size) for _ in range(count)]


def _fanned(count: int = 7, size: int = 40) -> list[FakeRect]:
    """Icons laid out in a row, each a full width apart — the menu open."""
    return [FakeRect(100 + i * size * 2, 200, 100 + i * size * 2 + size, 200 + size) for i in range(count)]


def test_stacked_icons_read_as_a_closed_menu():
    assert not archmastery_menu_is_open(_stacked())


def test_fanned_icons_read_as_an_open_menu():
    assert archmastery_menu_is_open(_fanned())


def test_icons_that_are_merely_close_still_read_as_closed():
    """Parked does not have to mean pixel-identical — the threshold is the icon's own width."""
    rects = _stacked()
    rects[3] = FakeRect(103, 202, 143, 242)
    assert not archmastery_menu_is_open(rects)


def test_a_vertical_fan_reads_as_open_too():
    """Nothing says the menu lays out horizontally; the spread is the larger axis."""
    rects = [FakeRect(100, 200 + i * 80, 140, 240 + i * 80) for i in range(7)]
    assert archmastery_menu_is_open(rects)


def test_one_icon_can_never_look_open():
    """Spread needs two points. Callers must not ask, and the answer is not 'open'."""
    assert not archmastery_menu_is_open(_stacked(count=1))
    assert not archmastery_menu_is_open([])


# -- the three-state opt-in -------------------------------------------------


def test_a_deck_with_no_saved_school_never_opts_in():
    """There is nothing to set, and "apply the school" cannot mean "clear it"."""
    deck = DeckData(normal={"Tempest": 1}, apply_archmastery=True)
    assert resolve_apply_archmastery(deck, app_default=True) == (False, "no school saved with this deck")


def test_a_deck_pin_of_true_wins_over_the_app_setting():
    deck = DeckData(archmastery_school="Storm", apply_archmastery=True)
    assert resolve_apply_archmastery(deck, app_default=False) == (True, "this deck")


def test_a_deck_pin_of_false_wins_over_the_app_setting():
    """The pin has to survive in both directions, or it is not a pin."""
    deck = DeckData(archmastery_school="Storm", apply_archmastery=False)
    assert resolve_apply_archmastery(deck, app_default=True) == (False, "this deck")


def test_an_unpinned_deck_follows_the_app_setting():
    deck = DeckData(archmastery_school="Storm")
    assert resolve_apply_archmastery(deck, app_default=True) == (True, "the app setting")
    assert resolve_apply_archmastery(deck, app_default=False) == (False, "the app setting")


def test_an_unpinned_deck_is_the_default():
    """A deck saved before this existed must not start setting anyone's school."""
    assert DeckData().apply_archmastery is None


# -- write_deck wiring ------------------------------------------------------


async def test_write_deck_leaves_the_school_alone_when_not_opted_in(monkeypatch):
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    called = []
    monkeypatch.setattr("bartlebot.combat.deck_io.set_archmastery_school", lambda *a: called.append(a))

    deck = DeckData(normal={"Tempest": 2}, archmastery_school="Storm", apply_archmastery=False)
    report = await write_deck(FakeDeckBuilder(), deck, set_archmastery=False)

    assert not called
    assert report.archmastery_set is None


async def test_write_deck_sets_the_school_before_the_already_matches_check(monkeypatch):
    """Cards right, school wrong: the school still has to be set.

    This is the case that would silently do nothing if the school were handled
    after the early-out, which is the whole reason it goes first.
    """
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_archmastery_school", AsyncMock(return_value="Death"))
    set_calls = []

    async def _fake_set(client, school):
        set_calls.append(school)
        return True

    monkeypatch.setattr("bartlebot.combat.deck_io.set_archmastery_school", _fake_set)

    db = FakeDeckBuilder()
    # Exactly what _fake_deck_names reports, with no item cards, so the cards match.
    deck = DeckData(normal={"Tempest": 2}, archmastery_school="Storm", apply_archmastery=True)
    report = await write_deck(db, deck, set_archmastery=True)

    assert set_calls == ["Storm"]
    assert report.archmastery_set == "Storm"
    assert report.unchanged is True  # the cards genuinely needed no rewrite
    assert "clear_deck" not in db.calls


async def test_write_deck_does_not_report_a_school_that_already_matched(monkeypatch):
    """archmastery_set means "a persistent setting moved", not "we checked"."""
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_archmastery_school", AsyncMock(return_value="Storm"))
    monkeypatch.setattr("bartlebot.combat.deck_io.set_archmastery_school", AsyncMock(return_value=True))

    deck = DeckData(normal={"Tempest": 2}, archmastery_school="Storm", apply_archmastery=True)
    report = await write_deck(FakeDeckBuilder(), deck, set_archmastery=True)

    assert report.archmastery_set is None
    assert report.archmastery_failed is None


async def test_write_deck_reports_a_school_it_could_not_set(monkeypatch):
    monkeypatch.setattr("bartlebot.combat.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_item_slots", _no_item_slots)
    monkeypatch.setattr("bartlebot.combat.deck_io.read_archmastery_school", AsyncMock(return_value="Death"))
    monkeypatch.setattr("bartlebot.combat.deck_io.set_archmastery_school", AsyncMock(return_value=False))

    deck = DeckData(normal={"Tempest": 2}, archmastery_school="Storm", apply_archmastery=True)
    report = await write_deck(FakeDeckBuilder(), deck, set_archmastery=True)

    assert report.archmastery_failed == "Storm"
    assert not report.is_clean  # the user asked for it and it did not happen


async def test_a_failed_school_is_the_only_archmastery_state_that_is_not_clean():
    """A successful set is not worth a dialog — opting in is what asked for it."""
    assert DeckApplyReport(archmastery_set="Storm").is_clean


# -- deck_matches wiring ----------------------------------------------------


def _fake_deck_door(opened: list):
    """Stand-in for deck_page_open — records that the page was opened at all."""

    @asynccontextmanager
    async def _door(client):
        opened.append(client)
        yield object()

    return _door


async def test_deck_matches_ignores_the_school_when_not_opted_in(monkeypatch):
    """Otherwise every apply takes the slow path over a difference we won't act on."""
    monkeypatch.setattr("bartlebot.combat.deck_io.read_archmastery_school", AsyncMock(return_value="Death"))
    monkeypatch.setattr("bartlebot.combat.deck_io._deck_already_matches", AsyncMock(return_value=True))
    monkeypatch.setattr("bartlebot.combat.deck_io.deck_page_open", _fake_deck_door([]))

    deck = DeckData(normal={"Tempest": 2}, archmastery_school="Storm")
    assert await deck_matches(FakeClient(), deck) is True


async def test_deck_matches_fails_fast_on_a_school_mismatch_when_opted_in(monkeypatch):
    """Checked before the page is opened: it is a memory read, and a mismatch
    means the fast path cannot succeed however the cards look."""
    monkeypatch.setattr("bartlebot.combat.deck_io.read_archmastery_school", AsyncMock(return_value="Death"))
    monkeypatch.setattr("bartlebot.combat.deck_io._deck_already_matches", AsyncMock(return_value=True))
    opened: list = []
    monkeypatch.setattr("bartlebot.combat.deck_io.deck_page_open", _fake_deck_door(opened))

    deck = DeckData(normal={"Tempest": 2}, archmastery_school="Storm")
    assert await deck_matches(FakeClient(), deck, check_archmastery=True) is False
    assert not opened


# -- driving the selector ---------------------------------------------------


class FakeSchoolMenu:
    """The deck page's archmastery selector, as far as deck_io can see it.

    Models the one thing that actually distinguishes open from closed: the seven
    icons are parked stacked behind the gauge, and clicking it fans them out.
    Clicking an icon sets the school and puts them back.

    ``gauge_clicks_needed`` makes the first N gauge clicks land without opening
    anything, which is how a swallowed click looks from outside.

    ``anim_sweeps`` is how many reads of the icon rectangles it takes for them
    to finish moving after a state change.  Non-zero by default: the real move
    is animated, and a fake that snaps instantly cannot catch the class of bug
    where something decides from a rectangle read mid-animation.
    """

    SCHOOLS = ("Fire", "Ice", "Storm", "Myth", "Life", "Death", "Balance")

    def __init__(self, school="Death", *, gauge_clicks_needed=1, icon_sets=True, icon_count=7, anim_sweeps=3):
        self.school = school
        self.open = False
        self.clicks: list[str] = []
        self._gauge_clicks_needed = gauge_clicks_needed
        self._icon_sets = icon_sets
        self._icon_count = icon_count
        self._anim_sweeps = anim_sweeps
        # What the icons are *showing*, which lags self.open while animating.
        self._shown_open = False
        self._remaining = 0

    # -- the windows deck_io resolves --------------------------------

    def window_for(self, name: str):
        if name == "Guage":
            return SimpleNamespace(_menu_name="Guage")
        school = name.removeprefix("SC_")
        if school not in self.SCHOOLS[: self._icon_count]:
            return None
        return SimpleNamespace(_menu_name=name, _school=school)

    def sweep(self, windows) -> list:
        """One read of every icon rectangle, advancing the animation by a frame.

        Mid-animation the icons are at intermediate positions, so consecutive
        sweeps differ — which is exactly what stops anything from calling the
        state settled while they are still moving.
        """
        if self._remaining:
            self._remaining -= 1
            if self._remaining == 0:
                self._shown_open = self.open
        spread = 80 if self._shown_open else 0
        # A frame of travel, so a sweep taken mid-move never repeats the last.
        drift = self._remaining * 3
        return [
            FakeRect(
                100 + self.SCHOOLS.index(w._school) * spread + drift,
                200,
                100 + self.SCHOOLS.index(w._school) * spread + drift + 40,
                240,
            )
            for w in windows
        ]

    def _begin_animation(self) -> None:
        self._remaining = self._anim_sweeps
        if self._anim_sweeps == 0:
            self._shown_open = self.open

    # -- what a click does -------------------------------------------

    def click(self, window) -> bool:
        name = window._menu_name
        self.clicks.append(name)
        if name == "Guage":
            if self.clicks.count("Guage") >= self._gauge_clicks_needed or self.open:
                self.open = not self.open
                self._begin_animation()
        elif self.open:
            # Picking a school closes the menu by itself — no second click.
            if self._icon_sets:
                self.school = window._school
            self.open = False
            self._begin_animation()
        return True


@pytest.fixture
def school_menu(monkeypatch):
    """Wire a FakeSchoolMenu into every seam set_archmastery_school uses."""
    menu = FakeSchoolMenu()

    async def _get_window(_parent, path):
        return menu.window_for(path[-1])

    async def _click(_client, window):
        return menu.click(window)

    async def _rects(windows):
        return menu.sweep(windows)

    monkeypatch.setattr("bartlebot.nodes.node_helpers._get_window_from_path", _get_window)
    monkeypatch.setattr("bartlebot.combat.deck_io._click", _click)
    monkeypatch.setattr("bartlebot.combat.deck_io._icon_rects", _rects)
    monkeypatch.setattr(
        "bartlebot.combat.deck_io.read_archmastery_school", AsyncMock(side_effect=lambda _c: menu.school)
    )
    # Cadences only. The dwell and the ceilings keep their shape relative to the
    # polls, so the tests still take the branches they are named for.
    monkeypatch.setattr("bartlebot.combat.deck_io.ARCHMASTERY_MENU_POLL", 0.001)
    monkeypatch.setattr("bartlebot.combat.deck_io.ARCHMASTERY_CONFIRM_POLL", 0.001)
    monkeypatch.setattr("bartlebot.combat.deck_io.ARCHMASTERY_MENU_SETTLE", 0.005)
    monkeypatch.setattr("bartlebot.combat.deck_io.ARCHMASTERY_MENU_TIMEOUT", 0.5)
    monkeypatch.setattr("bartlebot.combat.deck_io.ARCHMASTERY_CONFIRM_TIMEOUT", 0.2)
    return menu


async def test_setting_the_school_opens_the_menu_picks_and_closes(school_menu):
    assert await set_archmastery_school(FakeClient(), "Storm") is True
    assert school_menu.school == "Storm"
    assert school_menu.clicks == ["Guage", "SC_Storm"]
    assert not school_menu.open


async def test_a_school_that_already_matches_clicks_nothing(school_menu):
    """The common case, and the one where clicking would be pure risk."""
    school_menu.school = "Storm"

    assert await set_archmastery_school(FakeClient(), "Storm") is True
    assert school_menu.clicks == []


async def test_a_swallowed_gauge_click_is_retried(school_menu):
    """A click the game drops looks exactly like one it declined, so bound and retry."""
    school_menu._gauge_clicks_needed = 2

    assert await set_archmastery_school(FakeClient(), "Storm") is True
    assert school_menu.school == "Storm"
    assert school_menu.clicks.count("Guage") == 2


async def test_giving_up_after_the_attempt_limit(school_menu):
    """The icon click lands every time and the school never moves."""
    school_menu._icon_sets = False

    assert await set_archmastery_school(FakeClient(), "Storm") is False
    assert school_menu.clicks.count("SC_Storm") == ARCHMASTERY_ATTEMPTS


async def test_the_menu_is_left_closed_when_the_school_cannot_be_set(school_menu):
    """An open menu overlays the deck page and swallows the card clicks that follow."""
    school_menu._icon_sets = False

    await set_archmastery_school(FakeClient(), "Storm")

    assert not school_menu.open


async def test_a_grayed_looking_sprite_is_still_clicked():
    """The gauge is a sprite, and is_control_grayed() is only a real field on the
    button classes — reading it there lands on whatever sits at the offset and
    can refuse a perfectly good click. Every other click in deck_io skips it too.
    """
    clicked = []

    class FakeMouseHandler:
        # A real class, not a SimpleNamespace: `async with` looks __aenter__ up
        # on the type, so instance attributes named that way are never called.
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def click_window(self, window):
            clicked.append(window)

    window = SimpleNamespace(is_control_grayed=AsyncMock(return_value=True))
    handler = FakeMouseHandler()

    assert await _click(SimpleNamespace(mouse_handler=handler), window) is True
    assert clicked == [window]
    window.is_control_grayed.assert_not_awaited()


async def test_the_gauge_is_not_clicked_again_while_the_menu_is_closing(school_menu):
    """Picking a school closes the menu itself, and the icons restack over
    several frames. A close check that read the rectangles once caught them
    still spread, called that "open", and clicked the gauge — reopening the
    menu the pick had just dismissed.
    """
    assert await set_archmastery_school(FakeClient(), "Storm") is True

    assert school_menu.clicks == ["Guage", "SC_Storm"]
    assert not school_menu.open


async def test_a_menu_left_open_is_still_closed_on_the_way_out(school_menu):
    """The other side of it: a genuinely open menu does get the extra click."""
    school_menu._icon_sets = False

    await set_archmastery_school(FakeClient(), "Storm")

    assert not school_menu.open


async def test_a_missing_selector_is_declined_rather_than_clicked_blind(school_menu, monkeypatch):
    async def _no_windows(_parent, _path):
        return None

    monkeypatch.setattr("bartlebot.nodes.node_helpers._get_window_from_path", _no_windows)

    assert await set_archmastery_school(FakeClient(), "Storm") is False
    assert school_menu.clicks == []


async def test_too_few_icons_to_read_the_menu_state_is_declined(school_menu):
    """Spread needs two icons; with one the wait could only ever time out."""
    school_menu._icon_count = 1

    assert await set_archmastery_school(FakeClient(), "Storm") is False
    assert school_menu.clicks == []


async def test_wizwalkers_clear_deck_lets_a_stop_through(monkeypatch):
    """Guards the pinned WizWalker, not deck_io: clear_deck caught CancelledError in a bare
    ``except:`` until a8811d0, so a Stop that landed between its Clear click and the confirm
    click was lost, and the apply went on clicking cards into the modal left up."""
    from wizwalker.extensions.scripting import deck_builder

    reached = asyncio.Event()

    async def _the_modal(_parent, _name):
        if reached.is_set():
            raise RuntimeError("no modal")  # a swallowed cancel's retry: let it end, not hang
        reached.set()
        await asyncio.Event().wait()  # the confirmation is still coming up when Stop lands

    monkeypatch.setattr(deck_builder, "_maybe_get_named_window", _the_modal)
    db = deck_builder.DeckBuilder(SimpleNamespace(root_window=object()))
    db.view_deck_cards = AsyncMock()
    db.get_deck_count = AsyncMock(side_effect=[3, 0])
    db.clear_deck_manual = AsyncMock()

    task = asyncio.create_task(db.clear_deck())
    await reached.wait()
    task.cancel()
    await asyncio.wait({task}, timeout=1.0)

    assert task.cancelled()
    db.clear_deck_manual.assert_not_awaited()  # the fallback is for failures, never a Stop
