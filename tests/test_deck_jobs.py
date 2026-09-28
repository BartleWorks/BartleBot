"""Deck import and apply from the Combat tab: one slot, and every slot at once (#109).

What Import all has to get right is the save. Each single-slot import loads the
strategy, sets its own slot and saves, so running four of them side by side keeps
only whichever saved last. So the reads here run concurrently and the store is one
load and one save, and most of the import tests pin that down.

What Apply all has to get right is the reporting: one notice however many slots
went on with changes or failed, and none when every deck went on clean.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import MagicMock, call

import pytest

from bartlebot.bot.strategy_models import DeckData
from bartlebot.deck_io import DeckApplyReport

_BOUND = (
    "_start_deck_job",
    "_on_deck_job_done",
    "_active_deck_strategy_id",
    "_import_deck_for_slot",
    "_import_all_decks",
    "_read_slot_deck",
    "_store_imported_decks",
    "_saved_decks",
    "_apply_deck_for_slot",
    "_apply_all_decks",
    "_apply_slot_deck",
    "_report_deck_apply",
    "_report_apply_all",
    "_deck_apply_lines",
    "_describe_skipped",
    "_warn_deck_failed",
    "_deck_failure_lines",
)


class DeckJobHarness:
    """Hosts the deck methods without constructing a real AppController.

    ``notices`` collects every ``_show_deck_notice`` as (title, body, warn), which is
    the dialog the user would have seen.
    """

    def __init__(self, clients: list) -> None:
        from bartlebot.app_controller import AppController  # noqa: PLC0415

        for name in _BOUND:
            attr = inspect.getattr_static(AppController, name)
            setattr(self, name, attr.__func__ if isinstance(attr, staticmethod) else attr.__get__(self))
        self._client_manager = MagicMock()
        self._client_manager.clients = clients
        self._loaded_config = MagicMock(id="bot-1")
        self._window = MagicMock()
        self._on_config_changed = MagicMock()
        self._deck_task = None
        self.notices: list[tuple[str, str, bool]] = []

    def _show_deck_notice(self, title: str, body: str, *, warn: bool) -> None:
        self.notices.append((title, body, warn))

    @property
    def combat_tab(self) -> MagicMock:
        return self._window.left_sidebar.combat_tab


@pytest.fixture
def strategy(monkeypatch):
    """The active strategy, and a record of every save as a snapshot of its decks."""
    import bartlebot.app_controller as ac  # noqa: PLC0415
    import bartlebot.strategy_store as store  # noqa: PLC0415

    settings = MagicMock()
    settings.get_active_strategy = MagicMock(return_value="bcs-1")
    monkeypatch.setattr(ac.SettingsManager, "instance", staticmethod(lambda: settings))

    bcs = MagicMock()
    bcs.player_decks = {}
    bcs.saves = []
    monkeypatch.setattr(store, "load_bot_strategy", lambda _id: bcs)
    monkeypatch.setattr(store, "save_bot_strategy", lambda b: b.saves.append(dict(b.player_decks)))
    return bcs


@pytest.fixture
def client_decks(monkeypatch):
    """What each client's deck reads as, keyed by client; an exception is raised instead."""
    import bartlebot.app_controller as ac  # noqa: PLC0415

    decks: dict[str, object] = {}

    async def _read(client):
        result = decks[client]
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(ac, "read_deck", _read)
    return decks


def _deck(n: int = 1) -> DeckData:
    return DeckData(normal={"Tempest": n})


def _preset(n: int) -> dict:
    return {"normal": {"Tempest": n}}


# ---------------------------------------------------------------------------
# Import all
# ---------------------------------------------------------------------------


async def test_import_all_saves_once_with_every_slot_set(strategy, client_decks):
    client_decks.update({"c1": _preset(1), "c2": _preset(2), "c3": _preset(3)})
    harness = DeckJobHarness(["c1", "c2", "c3"])

    await harness._import_all_decks()

    assert len(strategy.saves) == 1
    assert {slot: deck.normal["Tempest"] for slot, deck in strategy.saves[0].items()} == {1: 1, 2: 2, 3: 3}
    [redraw] = harness.combat_tab.update_deck_slots.call_args_list
    assert sorted(redraw.args[0]) == [1, 2, 3]  # one redraw for every slot, never one per slot
    harness._on_config_changed.assert_called_once()
    assert harness.notices == []


async def test_import_all_keeps_a_deck_it_did_not_read(strategy, client_decks):
    """A slot with no hooked client keeps whatever deck it had saved."""
    strategy.player_decks = {4: _deck(9)}
    client_decks.update({"c1": _preset(1)})

    await DeckJobHarness(["c1"])._import_all_decks()

    assert set(strategy.saves[0]) == {1, 4}


async def test_one_slot_failing_still_saves_the_others_and_reports_it(strategy, client_decks):
    client_decks.update({"c1": _preset(1), "c2": RuntimeError("deck window not open"), "c3": TimeoutError()})
    harness = DeckJobHarness(["c1", "c2", "c3"])

    await harness._import_all_decks()

    assert len(strategy.saves) == 1
    assert set(strategy.saves[0]) == {1}
    [(title, body, warn)] = harness.notices
    assert title == "Deck import failed"
    assert "P2 failed" in body and "P3 timed out" in body
    assert warn


async def test_import_all_with_every_slot_failing_saves_nothing(strategy, client_decks):
    client_decks.update({"c1": RuntimeError("boom")})
    harness = DeckJobHarness(["c1"])

    await harness._import_all_decks()

    assert strategy.saves == []
    assert len(harness.notices) == 1


async def test_import_all_reads_concurrently(strategy, monkeypatch, client_decks):
    """Every read must be in flight together: under a sequential loop the first one
    would wait forever on a barrier only all four can release."""
    import bartlebot.app_controller as ac  # noqa: PLC0415

    started = asyncio.Barrier(4)

    async def _read(_client):
        await started.wait()
        return _preset(1)

    monkeypatch.setattr(ac, "read_deck", _read)
    async with asyncio.timeout(5):
        await DeckJobHarness(["c1", "c2", "c3", "c4"])._import_all_decks()

    assert set(strategy.saves[0]) == {1, 2, 3, 4}


async def test_import_all_reads_no_client_past_the_fourth(strategy, client_decks):
    client_decks.update({f"c{i}": _preset(i) for i in range(1, 6)})

    await DeckJobHarness([f"c{i}" for i in range(1, 6)])._import_all_decks()

    assert set(strategy.saves[0]) == {1, 2, 3, 4}


async def test_import_all_without_an_active_strategy_reads_nothing(strategy, client_decks, monkeypatch):
    import bartlebot.app_controller as ac  # noqa: PLC0415

    ac.SettingsManager.instance().get_active_strategy.return_value = None

    await DeckJobHarness(["c1"])._import_all_decks()  # c1 has no entry: a read would KeyError

    assert strategy.saves == []


async def test_single_slot_import_still_stores_its_deck(strategy, client_decks):
    client_decks.update({"c1": _preset(1), "c2": _preset(2)})
    harness = DeckJobHarness(["c1", "c2"])

    await harness._import_deck_for_slot(2)

    assert set(strategy.saves[0]) == {2}
    harness.combat_tab.update_deck_slots.assert_called_once()


async def test_single_slot_import_failure_is_reported(strategy, client_decks):
    client_decks.update({"c1": TimeoutError()})
    harness = DeckJobHarness(["c1"])

    await harness._import_deck_for_slot(1)

    assert strategy.saves == []
    [(title, body, _warn)] = harness.notices
    assert title == "Deck import failed" and "P1 timed out" in body


# ---------------------------------------------------------------------------
# Apply all
# ---------------------------------------------------------------------------


@pytest.fixture
def apply_results(monkeypatch):
    """What apply_deck returns per client (clean by default); an exception is raised instead.

    Also records which clients were applied to, under the key ``"applied"``.
    """
    import bartlebot.app_controller as ac  # noqa: PLC0415

    results: dict[str, object] = {"applied": []}

    async def _apply(client, _deck, slot=None):
        results["applied"].append(client)
        result = results.get(client, DeckApplyReport())
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(ac, "apply_deck", _apply)
    return results


async def test_apply_all_shows_nothing_when_every_deck_is_clean(strategy, apply_results):
    strategy.player_decks = {1: _deck(), 2: _deck()}
    harness = DeckJobHarness(["c1", "c2"])

    await harness._apply_all_decks()

    assert sorted(apply_results["applied"]) == ["c1", "c2"]
    assert harness.notices == []


async def test_apply_all_combines_every_slot_into_one_notice(strategy, apply_results):
    strategy.player_decks = {1: _deck(), 2: _deck(), 3: _deck()}
    apply_results["c1"] = DeckApplyReport(substitutions={"Tempest": "Storm Lord"})
    apply_results["c3"] = DeckApplyReport(skipped=["Tempest"], applied=False)
    harness = DeckJobHarness(["c1", "c2", "c3"])

    await harness._apply_all_decks()

    [(title, body, warn)] = harness.notices
    assert title == "Some decks were not applied"
    assert "P1: applied, with changes" in body
    assert "Storm Lord" in body
    assert "P3: not applied" in body
    assert "P2" not in body
    assert warn


async def test_apply_all_with_only_substitutions_is_not_a_warning(strategy, apply_results):
    strategy.player_decks = {1: _deck(), 2: _deck()}
    apply_results["c2"] = DeckApplyReport(substitutions={"Tempest": "Storm Lord"})
    harness = DeckJobHarness(["c1", "c2"])

    await harness._apply_all_decks()

    [(title, _body, warn)] = harness.notices
    assert title == "Decks applied, with changes"
    assert not warn


async def test_apply_all_reports_a_failed_slot_alongside_the_others(strategy, apply_results):
    strategy.player_decks = {1: _deck(), 2: _deck()}
    apply_results["c1"] = TimeoutError()
    apply_results["c2"] = RuntimeError("boom")
    harness = DeckJobHarness(["c1", "c2"])

    await harness._apply_all_decks()

    [(title, body, warn)] = harness.notices
    assert title == "Some decks were not applied"
    assert "Deck apply for P1 timed out." in body
    assert "Deck apply for P2 failed" in body
    assert "deck window may be left open" in body
    assert warn


async def test_apply_all_skips_empty_decks_and_slots_without_a_client(strategy, apply_results):
    strategy.player_decks = {1: DeckData(), 2: _deck(), 4: _deck()}

    await DeckJobHarness(["c1", "c2"])._apply_all_decks()

    assert apply_results["applied"] == ["c2"]


async def test_single_slot_apply_still_reports_on_its_own(strategy, apply_results):
    strategy.player_decks = {1: _deck()}
    apply_results["c1"] = DeckApplyReport(short={"Tempest": (1, 2)})
    harness = DeckJobHarness(["c1"])

    await harness._apply_deck_for_slot(1)

    [(title, body, _warn)] = harness.notices
    assert title == "Deck applied for P1, with changes"
    assert "(1 of 2)" in body


# ---------------------------------------------------------------------------
# One deck job at a time
# ---------------------------------------------------------------------------


async def test_a_deck_job_holds_the_tab_busy_until_it_finishes():
    harness = DeckJobHarness(["c1"])
    gate = asyncio.Event()

    async def _job() -> None:
        await gate.wait()

    harness._start_deck_job(_job())
    task = harness._deck_task
    assert harness.combat_tab.set_deck_busy.call_args_list == [call(True)]

    gate.set()
    await task
    await asyncio.sleep(0)  # done callbacks run on the next loop turn

    assert harness.combat_tab.set_deck_busy.call_args_list == [call(True), call(False)]
    assert harness._deck_task is None


async def test_a_second_deck_job_is_refused_while_one_runs():
    """The tab disables its verbs, but a card's menu still reaches Import and Apply."""
    harness = DeckJobHarness(["c1"])
    gate = asyncio.Event()
    ran: list[str] = []

    async def _job(name: str) -> None:
        ran.append(name)
        await gate.wait()

    harness._start_deck_job(_job("first"))
    task = harness._deck_task
    harness._start_deck_job(_job("second"))
    gate.set()
    await task

    assert ran == ["first"]


async def test_a_deck_job_that_raises_still_releases_the_tab():
    harness = DeckJobHarness(["c1"])

    async def _job() -> None:
        raise RuntimeError("boom")

    harness._start_deck_job(_job())
    task = harness._deck_task
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)

    assert harness.combat_tab.set_deck_busy.call_args_list[-1] == call(False)
    assert harness._deck_task is None
