"""Tests for bartlebot.deck_io.

The memory offsets these rely on are game-build specific (see the module
docstring); what is tested here is the logic layered on top of them -- the grid
arithmetic that replaced the unbounded page-discovery loop, and the toggle
planning that decides which item card slots to click.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bartlebot.bot.strategy_models import DeckData
from bartlebot.deck_io import (
    CARD_CLICK_ATTEMPTS,
    SLOTS_PER_PAGE,
    ItemSlot,
    _settle_deck,
    _share_shortfall,
    active_item_counts,
    apply_deck,
    count_names,
    deck_matches,
    deck_page_open,
    expected_normal_deck,
    page_count,
    parse_tiered_name,
    partition_addable,
    plan_item_toggles,
    plan_normal_cards,
    read_deck,
    resolve_tier,
    resolved_at_click_time,
    suggest_alternatives,
    write_deck,
)
from bartlebot.nodes.timing import DECK_APPLY_TIMEOUT
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
    monkeypatch.setattr("bartlebot.deck_io.DECK_SETTLE_TIMEOUT", 0.0)
    monkeypatch.setattr("bartlebot.deck_io.DECK_SETTLE_POLL", 0.001)
    # Likewise for the per-card confirm: a zero ceiling still takes one reading,
    # so a fake deck that reflects its clicks confirms immediately and one that
    # never grows gives up at once instead of waiting out 2s per attempt.
    monkeypatch.setattr("bartlebot.deck_io.DECK_CARD_CONFIRM_TIMEOUT", 0.0)
    monkeypatch.setattr("bartlebot.deck_io.DECK_CARD_CONFIRM_POLL", 0.001)


def _async_returning(value):
    """Build a zero-arg coroutine function that yields ``value``.

    The memory objects this module reads are async all the way down, so the
    fakes have to be too.
    """

    async def _call():
        return value

    return _call


class FakeDeckBuilder:
    """Minimal DeckBuilder stand-in that records calls and rejects the unsafe ones."""

    #: Template names the fake account's spell list contains. Anything a deck
    #: asks for outside this is treated as not owned, exactly as in the game.
    spell_list: list[str] = ["Tempest", "Frenzy", "Epic", "Sharpened Blade", "Storm Lord"]

    def __init__(self):
        self.calls: list[str] = []
        self.added: dict[str, int] | None = None
        self.client = SimpleNamespace(root_window=object())
        # Real DeckBuilder constructs this False and open_deck_page() never sets
        # it; whoever opens the page is responsible for saying so. Direct
        # write_deck tests stand in for a properly-opened page, so True here â€”
        # _CtxDeckBuilder resets it to False to test that apply_deck does its part.
        self._on_deck_page = True

    async def refresh_deck_page(self) -> None:
        self.calls.append("refresh_deck_page")

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
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()
    await write_deck(db, DeckData(normal={"Tempest": 4}))
    assert db.calls == [
        "view_deck_cards",
        "get_graphical_spell_cards",
        "clear_deck",
        "view_deck_cards",
        "_add_all_normal_cards",
    ]
    assert db.added == {"Tempest": 4}


async def test_write_deck_reads_the_spell_list_before_clearing(monkeypatch):
    """The ordering invariant: nothing that can fail on a card name runs after the clear.

    _add_all_normal_cards raises on the first name it cannot place, so planning
    afterwards left the wizard with an emptied deck.
    """
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()

    await write_deck(db, DeckData(normal={"Tempest": 4}))

    assert db.calls.index("get_graphical_spell_cards") < db.calls.index("clear_deck")


async def test_write_deck_skips_unowned_cards_instead_of_emptying_the_deck(monkeypatch):
    """One card the account lacks must cost that card, not the entire deck."""
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()

    await write_deck(db, DeckData(normal={"Tempest": 2, "Firestorm Blade": 4}))

    assert db.added == {"Tempest": 2}


async def test_write_deck_adds_nothing_when_no_card_is_owned(monkeypatch):
    """Degrades to leaving the deck alone rather than raising out of the apply."""
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()

    await write_deck(db, DeckData(normal={"Firestorm Blade": 4}))

    assert db.added is None
    assert "clear_deck" not in db.calls


async def test_write_deck_empty_clears_without_adding(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()
    await write_deck(db, DeckData())
    assert db.calls == ["clear_deck", "view_deck_cards"]
    assert db.added is None


async def test_write_deck_ignores_legacy_treasure_cards(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()
    await write_deck(db, DeckData(normal={"Tempest": 1}, tc={"Feint": 3}))
    assert db.added == {"Tempest": 1}


async def test_write_deck_skips_when_current_deck_already_matches(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _fake_item_slots)
    db = FakeDeckBuilder()
    deck = DeckData(normal={"Tempest": 2}, item={"Sharpened Blade - Tear": 1})
    await write_deck(db, deck)
    # No builder calls at all: the matching deck is recognised from two memory
    # reads, without touching the UI.
    assert db.calls == []
    assert db.added is None


async def test_write_deck_rewrites_when_item_cards_differ(monkeypatch):
    """Even with matching normal cards, a mismatched item loadout must still trigger a rewrite."""
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _fake_item_slots)
    db = FakeDeckBuilder()
    deck = DeckData(normal={"Tempest": 2}, item={"Sharpened Blade - Tear": 2})
    await write_deck(db, deck)
    assert "clear_deck" in db.calls
    assert db.added == {"Tempest": 2}


async def test_read_deck_never_reports_treasure_cards(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_cards", _fake_deck_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _fake_item_slots)
    db = FakeDeckBuilder()
    result = await read_deck(db)
    assert result["normal"] == {"Tempest": 2}
    assert result["item"] == {"Sharpened Blade - Tear": 1}
    assert result["tc"] == {}


async def test_write_deck_propagates_failures(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)

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
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    # _fake_window reports the page as permanently open, so deck_page_open takes
    # its "would not close" bail-out. These tests are about the write path, not
    # that wait — keep it from costing five real seconds apiece.
    monkeypatch.setattr("bartlebot.deck_io.DECK_PAGE_OPEN_TIMEOUT", 0.01)
    return _CtxDeckBuilder


async def test_apply_deck_opens_the_builder_and_writes(fake_deck_builder):
    await apply_deck(FakeClient(), DeckData(normal={"Tempest": 4}))

    db = fake_deck_builder.instances[0]
    assert db.entered and db.exited
    assert db.calls == [
        "view_deck_cards",
        "get_graphical_spell_cards",
        "clear_deck",
        "view_deck_cards",
        "_add_all_normal_cards",
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

    with pytest.raises(TimeoutError):
        await apply_deck(FakeClient(), DeckData(normal={"Tempest": 1}), timeout=0.05)

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
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", settling.read_names)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    monkeypatch.setattr("bartlebot.deck_io.DECK_SETTLE_TIMEOUT", 5.0)
    monkeypatch.setattr("bartlebot.deck_io.DECK_SETTLE_POLL", 0.001)

    settled = await _settle_deck(FakeClient(), object(), {"Tempest": 2, "Epic": 1}, {})

    assert settled is True
    assert settling.reads == 4  # three short reads, then the full one


async def test_settle_returns_immediately_when_the_deck_is_already_right(monkeypatch):
    settling = _SettlingDeck(["Tempest"], reads_until_settled=0)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", settling.read_names)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    monkeypatch.setattr("bartlebot.deck_io.DECK_SETTLE_TIMEOUT", 5.0)

    assert await _settle_deck(FakeClient(), object(), {"Tempest": 1}, {}) is True
    assert settling.reads == 1


async def test_settle_gives_up_and_warns_when_the_deck_never_matches(monkeypatch, caplog):
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    monkeypatch.setattr("bartlebot.deck_io.DECK_SETTLE_TIMEOUT", 0.05)
    monkeypatch.setattr("bartlebot.deck_io.DECK_SETTLE_POLL", 0.001)

    with caplog.at_level(logging.WARNING, logger="bartlebot.deck_io"):
        settled = await _settle_deck(FakeClient(), object(), {"Tempest": 4}, {})

    assert settled is False
    assert "did not settle" in caplog.text


async def test_settle_ignores_item_cards_the_account_does_not_grant(monkeypatch):
    """Gear changes, so a saved deck naming absent item cards is expected."""
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _fake_item_slots)
    monkeypatch.setattr("bartlebot.deck_io.DECK_SETTLE_TIMEOUT", 0.05)
    monkeypatch.setattr("bartlebot.deck_io.DECK_SETTLE_POLL", 0.001)

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

    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _flaky)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    monkeypatch.setattr("bartlebot.deck_io.DECK_SETTLE_TIMEOUT", 5.0)
    monkeypatch.setattr("bartlebot.deck_io.DECK_SETTLE_POLL", 0.001)

    assert await _settle_deck(FakeClient(), object(), {"Tempest": 1}, {}) is True


async def test_write_deck_settles_before_returning(monkeypatch):
    """write_deck must not hand control back — and so let the page close — until
    the deck reads right."""
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    monkeypatch.setattr("bartlebot.deck_io.DECK_SETTLE_TIMEOUT", 5.0)
    monkeypatch.setattr("bartlebot.deck_io.DECK_SETTLE_POLL", 0.001)

    settling = _SettlingDeck(["Tempest", "Tempest"], reads_until_settled=2)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", settling.read_names)

    db = FakeDeckBuilder()
    await write_deck(db, DeckData(normal={"Tempest": 2}))

    assert "_add_all_normal_cards" in db.calls
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
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", state)
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
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", state)
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
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", state)
    monkeypatch.setattr("bartlebot.deck_io.DECK_PAGE_OPEN_TIMEOUT", 0.05)
    client = _page_client(state)

    async with deck_page_open(client) as window:
        assert window is None

    assert client.send_key.await_count == 1


async def test_deck_page_open_yields_none_when_the_page_never_appears(monkeypatch):
    """And must not press P a second time — that would open it, not close it."""
    state = _PageState(open_now=False, ever_opens=False)
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", state)
    monkeypatch.setattr("bartlebot.deck_io.DECK_PAGE_OPEN_TIMEOUT", 0.05)
    client = _page_client(state)

    async with deck_page_open(client) as window:
        assert window is None

    assert client.send_key.await_count == 1


async def test_deck_page_open_closes_even_when_the_body_raises(monkeypatch):
    state = _PageState(open_now=False)
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", state)
    client = _page_client(state)

    with pytest.raises(RuntimeError):
        async with deck_page_open(client):
            raise RuntimeError("read blew up")

    assert not state.is_open


async def test_deck_page_open_takes_no_mouse_handler(monkeypatch):
    """send_key is a plain window message; the mouseless hook costs ~180ms and
    governs only cursor position."""
    state = _PageState(open_now=False)
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", state)
    client = _page_client(state)

    async with deck_page_open(client):
        pass

    client.mouse_handler.__aenter__.assert_not_awaited()


async def test_deck_matches_true_when_the_deck_is_identical(monkeypatch):
    state = _PageState(open_now=False)
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", state)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _fake_item_slots)

    deck = DeckData(normal={"Tempest": 2}, item={"Sharpened Blade - Tear": 1})
    assert await deck_matches(_page_client(state), deck) is True


async def test_deck_matches_false_when_the_deck_differs(monkeypatch):
    state = _PageState(open_now=False)
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", state)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _fake_item_slots)

    assert await deck_matches(_page_client(state), DeckData(normal={"Tempest": 4})) is False


async def test_deck_matches_false_when_the_read_fails(monkeypatch):
    """A failed read means "cannot confirm", which must send the caller down the
    full path rather than propagate."""
    state = _PageState(open_now=False)
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", state)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("memory read failed")

    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _boom)

    assert await deck_matches(_page_client(state), DeckData(normal={"Tempest": 2})) is False


async def test_deck_matches_false_when_the_page_will_not_open(monkeypatch):
    state = _PageState(open_now=False, ever_opens=False)
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", state)
    monkeypatch.setattr("bartlebot.deck_io.DECK_PAGE_OPEN_TIMEOUT", 0.05)

    assert await deck_matches(_page_client(state), DeckData(normal={"Tempest": 2})) is False


# ---------------------------------------------------------------------------
# apply_deck fast path
# ---------------------------------------------------------------------------


async def test_apply_deck_skips_the_deck_ui_entirely_when_it_already_matches(monkeypatch):
    """The whole point: no DeckBuilder, so no tab clicks and no mouse hook."""
    state = _PageState(open_now=False)
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", state)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _fake_item_slots)
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
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", state)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _fake_item_slots)
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

    monkeypatch.setattr("bartlebot.deck_io.deck_matches", _spy)

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
    def __init__(self):
        self.clicks: list[tuple[int, int]] = []
        self.window_clicks: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def click(self, x, y):
        self.clicks.append((x, y))

    async def click_window(self, window=None, *_args, **_kwargs):
        self.window_clicks.append(getattr(window, "window_name", "?"))
        return None


class FusionRootDeckBuilder(FakeDeckBuilder):
    """A wizard with several variants unlocked: the spellbook shows the root."""

    spell_list = ["Tempest", "Frenzy", "Novus Storm - T01 - Base"]
    #: What the flyout lists once the root is clicked.
    owned_tiers = ["Novus Storm - T05 - A", "Novus Storm - T05 - B"]

    def __init__(self):
        super().__init__()
        self._deck_config_window = object()
        self.client = SimpleNamespace(root_window=object(), mouse_handler=_FakeMouse())

    def calcuate_position_of_card_in_page(self, cards: list, name: str) -> tuple[int, int]:
        return 0, cards.index(name) + 1

    async def calculate_card_position(self, card_index: int) -> tuple[int, int]:
        return (card_index, 0)

    async def calculate_tiered_spell_card_position(self, card_number: int) -> tuple[int, int]:
        return (_VARIANT_CLICK_BASE + card_number, 0)

    async def _go_to_spell_page(self, page: int) -> None:
        self.calls.append(f"_go_to_spell_page:{page}")

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
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)


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
# A base-form entry has two spellings and two meanings (#109)
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
    """The #109 regression: this used to land in unresolvable and read as untrained."""
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

    with caplog.at_level(logging.INFO, logger="bartlebot.deck_io"):
        await write_deck(db, DeckData(normal={"Bunyips Rage - T05 - A": 1}))

    assert "no upgrade path unlocked" in caplog.text


async def test_a_failed_flyout_read_still_skips(monkeypatch, caplog):
    """Only a missing window means "nothing unlocked" — a read error is still our bug."""
    _tier_patches(monkeypatch)
    db = NoPathDeckBuilder()

    async def _boom() -> list:
        raise MemoryError("read failed")

    db.get_graphical_tiered_spell_cards = _boom

    with caplog.at_level(logging.WARNING, logger="bartlebot.deck_io"):
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

    with caplog.at_level(logging.INFO, logger="bartlebot.deck_io"):
        await write_deck(db, DeckData(normal={"Novus Storm - T05 - B": 4}))

    assert "Novus Storm - T04 - B" in caplog.text
    assert "Novus Storm - T05 - B" in caplog.text
    assert "in place of" in caplog.text


async def test_fusion_substitution_is_logged(monkeypatch, caplog):
    _tier_patches(monkeypatch)
    db = FusionRootDeckBuilder()
    db.owned_tiers = ["Novus Storm - T03 - B"]

    with caplog.at_level(logging.INFO, logger="bartlebot.deck_io"):
        await write_deck(db, DeckData(normal={"Novus Storm - T05 - B": 2}))

    assert "Novus Storm - T03 - B" in caplog.text
    assert "in place of" in caplog.text


async def test_both_paths_word_the_substitution_identically(monkeypatch, caplog):
    """One wording, so a reader does not have to learn that two lines mean the same."""
    _tier_patches(monkeypatch)

    with caplog.at_level(logging.INFO, logger="bartlebot.deck_io"):
        await write_deck(SingleVariantDeckBuilder(), DeckData(normal={"Novus Storm - T05 - B": 1}))
    direct_line = next(m for m in caplog.messages if "in place of" in m)

    caplog.clear()
    fusion_db = FusionRootDeckBuilder()
    fusion_db.owned_tiers = ["Novus Storm - T04 - B"]
    with caplog.at_level(logging.INFO, logger="bartlebot.deck_io"):
        await write_deck(fusion_db, DeckData(normal={"Novus Storm - T05 - B": 1}))
    fusion_line = next(m for m in caplog.messages if "in place of" in m)

    assert direct_line == fusion_line


async def test_an_exact_match_logs_no_substitution(monkeypatch, caplog):
    """No crying wolf on a wizard whose spells already match the strategy."""
    _tier_patches(monkeypatch)
    db = FusionRootDeckBuilder()

    with caplog.at_level(logging.INFO, logger="bartlebot.deck_io"):
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
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)

    report = await write_deck(FakeDeckBuilder(), DeckData(normal={"Tempest": 4}))

    assert report.is_clean
    assert report.applied is True


async def test_skipped_cards_reach_the_caller(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)

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
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)

    report = await write_deck(FakeDeckBuilder(), DeckData(normal={"Inferno Blade": 3}))

    assert report.applied is False
    assert report.skipped == ["Inferno Blade"]


async def test_a_matching_deck_reports_unchanged(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)

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
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    deck = DeckData(normal={"Tempest": 2, "Inferno Blade": 3}, card_kinds={"Inferno Blade": "blade"})

    report = await write_deck(FakeDeckBuilder(), deck)

    assert report.kinds == {"Inferno Blade": "blade"}


async def test_a_deck_saved_before_kinds_existed_still_applies(monkeypatch):
    """card_kinds is absent from every deck saved so far."""
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()

    report = await write_deck(db, DeckData(normal={"Tempest": 2, "Inferno Blade": 3}))

    assert db.added == {"Tempest": 2}
    assert report.kinds == {}
    assert report.alternatives == {}


# ---------------------------------------------------------------------------
# The fusion pass sweeps the spellbook forward once
# ---------------------------------------------------------------------------
#
# _go_to_spell_page rewinds to page one before advancing, so calling it per base
# re-walked the whole spellbook each time — and in deck order, so it bounced
# 0 -> 11 -> 0 -> 9 -> 0 -> 7. Bases are visited in page order now, and the page
# only ever advances.


async def _named_window(_parent, name, *_args, **_kwargs):
    return SimpleNamespace(window_name=name)


class MultiPageDeckBuilder(FusionRootDeckBuilder):
    """A spellbook long enough to page through, with three fusion spells on it."""

    CARDS_PER_PAGE = 6
    spell_list = (
        [f"Filler {i}" for i in range(7)]
        + ["Thunder Snake"]  # page 1
        + [f"Filler {i}" for i in range(7, 13)]
        + ["Lightning Bats"]  # page 2
        + [f"Filler {i}" for i in range(13, 19)]
        + ["Bunyips Rage"]  # page 3
    )

    def calcuate_position_of_card_in_page(self, cards: list, name: str) -> tuple[int, int]:
        """The real 6-per-page arithmetic, so page indices are not all zero."""
        index = cards.index(name) + 1
        slot = index % self.CARDS_PER_PAGE or self.CARDS_PER_PAGE
        return (index - 1) // self.CARDS_PER_PAGE, slot

    async def get_graphical_tiered_spell_cards(self) -> list:
        """Every base offers the same two paths, so the flyout is always populated."""
        base = self.last_clicked_base
        self.flyout_order.append(base)
        self.owned_tiers = [f"{base} - T05 - A", f"{base} - T05 - B"]
        return await super().get_graphical_tiered_spell_cards()

    @property
    def last_clicked_base(self) -> str:
        """Which spellbook entry the most recent page-click landed on."""
        page = self._rewound_to + self.page_downs
        for name in ("Thunder Snake", "Lightning Bats", "Bunyips Rage"):
            if self.calcuate_position_of_card_in_page(self.spell_list, name)[0] == page:
                return name
        raise AssertionError(f"no fusion base on page {page}")

    def __init__(self):
        super().__init__()
        self._rewound_to = 0
        #: Bases whose flyout was opened, in the order the sweep reached them.
        self.flyout_order: list[str] = []

    async def _go_to_spell_page(self, page: int) -> None:
        self.calls.append(f"rewind:{page}")
        self._rewound_to = page

    @property
    def rewinds(self) -> list[str]:
        return [c for c in self.calls if c.startswith("rewind:")]

    @property
    def page_downs(self) -> int:
        return self.client.mouse_handler.window_clicks.count("PageDown")


def _sweep_patches(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _named_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)


async def test_fusion_pass_rewinds_only_once(monkeypatch):
    _sweep_patches(monkeypatch)
    db = MultiPageDeckBuilder()

    await write_deck(
        db,
        DeckData(
            normal={
                "Bunyips Rage - T05 - A": 1,
                "Lightning Bats - T05 - A": 1,
                "Thunder Snake - T05 - A": 1,
            }
        ),
    )

    assert db.rewinds == ["rewind:1"]  # to the first base's page, then forward only


async def test_fusion_pass_advances_one_page_at_a_time(monkeypatch):
    """Pages 1, 2 and 3 in order: two PageDown clicks, not a walk per base."""
    _sweep_patches(monkeypatch)
    db = MultiPageDeckBuilder()

    await write_deck(
        db,
        DeckData(
            normal={
                "Bunyips Rage - T05 - A": 1,
                "Lightning Bats - T05 - A": 1,
                "Thunder Snake - T05 - A": 1,
            }
        ),
    )

    assert db.page_downs == 2


async def test_fusion_pass_visits_bases_in_page_order(monkeypatch):
    """Deck order is arbitrary; the sweep must follow the spellbook."""
    _sweep_patches(monkeypatch)
    db = MultiPageDeckBuilder()

    await write_deck(
        db,
        DeckData(normal={"Bunyips Rage - T05 - A": 1, "Thunder Snake - T05 - A": 1}),
    )

    assert db.flyout_order == ["Thunder Snake", "Bunyips Rage"]  # pages 1 then 3
    assert db.rewinds == ["rewind:1"]  # Thunder Snake's page, the earlier of the two


async def test_a_reopened_deck_page_forces_another_rewind(monkeypatch):
    """The close fallback reopens the page, so the tracked position is a guess."""
    _sweep_patches(monkeypatch)
    db = MultiPageDeckBuilder()

    async def _no_close_button(parent, name, *args, **kwargs):
        if name == "CloseTSMPUnlockedPageButton":
            raise ValueError("no close button")
        return SimpleNamespace(window_name=name)

    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _no_close_button)

    await write_deck(
        db,
        DeckData(normal={"Thunder Snake - T05 - A": 1, "Lightning Bats - T05 - A": 1}),
    )

    assert db.calls.count("refresh_deck_page") == 2  # the fallback fired for each base
    assert db.rewinds == ["rewind:1", "rewind:2"]  # so each base re-sought its page


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

    async def count() -> int:
        landed = sum(1 for x, _y in mouse.clicks if (x >= _VARIANT_CLICK_BASE) is at_variant)
        landed = max(0, landed - lost)
        return min(landed, cap) if cap is not None else landed

    return count


def _use_deck(monkeypatch, counter):
    monkeypatch.setattr("bartlebot.deck_io.card_counter", lambda *_args: counter)


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

    async def _spy_settle(_client, _window, expected_normal, _desired_item):
        expected.update(expected_normal)
        return True

    monkeypatch.setattr("bartlebot.deck_io._settle_deck", _spy_settle)

    await write_deck(db, DeckData(normal={"Novus Storm - T05 - A": 3}))

    assert expected == {"Novus Storm - T05 - A": 2}


async def test_an_unreadable_count_falls_back_to_clicking_once(monkeypatch):
    """A broken read is not evidence of a failed click; retrying would duplicate."""
    _tier_patches(monkeypatch)
    db = _two_path_builder()

    async def _unreadable() -> None:
        return None

    _use_deck(monkeypatch, _unreadable)

    report = await write_deck(db, DeckData(normal={"Novus Storm - T05 - A": 2}))

    assert db.fusion_added == {"Novus Storm - T05 - A": 2}  # clicked once each, as before
    assert report.is_clean


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
