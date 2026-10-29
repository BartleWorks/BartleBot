"""Pre-run deck apply: slot routing, failure isolation, and concurrency.

The decks are applied concurrently because doing them in sequence made a
four-client run wait four times over for what is mostly sleeping (concurrent,
four clients measured 1.88x faster). Concurrency is only safe because each
client's failures stay its own, so most of what is tested here is that one bad
client cannot take the others down with it.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from bartleby.shared.strategy_models import DeckData


class PreRunDeckHarness:
    """Hosts the pre-run deck methods without constructing a real AppController."""

    def __init__(self, clients: list) -> None:
        from bartlebot.app_controller import AppController  # noqa: PLC0415

        self._apply_pre_run_decks = AppController._apply_pre_run_decks.__get__(self)
        self._apply_one_pre_run_deck = AppController._apply_one_pre_run_deck.__get__(self)
        self._client_manager = MagicMock()
        self._client_manager.clients = clients
        self._loaded_config = MagicMock(id="bot-1")


@pytest.fixture
def strategy(monkeypatch):
    """Wire up the settings + strategy lookups that _apply_pre_run_decks does first."""
    import bartlebot.app_controller as ac  # noqa: PLC0415
    import bartlebot.combat.strategy_store as store  # noqa: PLC0415

    settings = MagicMock()
    settings.get_active_strategy = MagicMock(return_value="bcs-1")
    monkeypatch.setattr(ac.SettingsManager, "instance", staticmethod(lambda: settings))

    bcs = MagicMock()
    bcs.player_decks = {}
    monkeypatch.setattr(store, "load_bot_strategy", lambda _id: bcs)
    return bcs


@pytest.fixture
def applies(monkeypatch):
    """Record every apply_deck call as (client, deck). Returns the list."""
    import bartlebot.app_controller as ac  # noqa: PLC0415

    recorded: list[tuple[object, DeckData]] = []

    async def _apply(client, deck, slot=None):
        recorded.append((client, deck))

    monkeypatch.setattr(ac, "apply_deck", _apply)
    return recorded


def _deck(n: int = 1) -> DeckData:
    return DeckData(normal={"Tempest": n})


# ---------------------------------------------------------------------------
# Slot routing
# ---------------------------------------------------------------------------


async def test_each_slot_goes_to_its_own_client(strategy, applies):
    clients = ["c1", "c2", "c3"]
    strategy.player_decks = {1: _deck(1), 2: _deck(2), 3: _deck(3)}

    await PreRunDeckHarness(clients)._apply_pre_run_decks()

    assert sorted((c, d.normal["Tempest"]) for c, d in applies) == [("c1", 1), ("c2", 2), ("c3", 3)]


async def test_empty_decks_are_skipped(strategy, applies):
    strategy.player_decks = {1: DeckData(), 2: _deck(2)}

    await PreRunDeckHarness(["c1", "c2"])._apply_pre_run_decks()

    assert [c for c, _ in applies] == ["c2"]


async def test_slot_without_a_connected_client_is_skipped(strategy, applies):
    strategy.player_decks = {1: _deck(), 4: _deck()}

    await PreRunDeckHarness(["c1"])._apply_pre_run_decks()

    assert [c for c, _ in applies] == ["c1"]


async def test_no_decks_configured_does_nothing(strategy, applies):
    await PreRunDeckHarness(["c1"])._apply_pre_run_decks()

    assert applies == []


async def test_each_apply_is_told_its_slot_for_the_log(strategy, monkeypatch):
    """Concurrent applies interleave their deck_io lines; the slot tells them apart (#107)."""
    import bartlebot.app_controller as ac  # noqa: PLC0415

    slots: dict[str, int | None] = {}

    async def _apply(client, _deck, slot=None):
        slots[client] = slot

    monkeypatch.setattr(ac, "apply_deck", _apply)
    strategy.player_decks = {1: _deck(), 2: _deck()}

    await PreRunDeckHarness(["c1", "c2"])._apply_pre_run_decks()

    assert slots == {"c1": 1, "c2": 2}


# ---------------------------------------------------------------------------
# Failure isolation — what makes the gather safe
# ---------------------------------------------------------------------------


async def test_one_client_failing_does_not_stop_the_others(strategy, monkeypatch):
    import bartlebot.app_controller as ac  # noqa: PLC0415

    finished: list[str] = []

    async def _apply(client, _deck, slot=None):
        if client == "c2":
            raise RuntimeError("deck window not open")
        finished.append(client)

    monkeypatch.setattr(ac, "apply_deck", _apply)
    strategy.player_decks = {1: _deck(), 2: _deck(), 3: _deck()}

    await PreRunDeckHarness(["c1", "c2", "c3"])._apply_pre_run_decks()

    assert sorted(finished) == ["c1", "c3"]


async def test_one_client_timing_out_does_not_stop_the_others(strategy, monkeypatch):
    import bartlebot.app_controller as ac  # noqa: PLC0415

    finished: list[str] = []

    async def _apply(client, _deck, slot=None):
        if client == "c1":
            raise TimeoutError
        finished.append(client)

    monkeypatch.setattr(ac, "apply_deck", _apply)
    strategy.player_decks = {1: _deck(), 2: _deck()}

    await PreRunDeckHarness(["c1", "c2"])._apply_pre_run_decks()

    assert finished == ["c2"]


async def test_a_failed_apply_never_propagates(strategy, monkeypatch):
    """The caller (_run_pre_run_setup) treats a bad deck as a degraded run, not a
    cancelled one, so nothing here may escape."""
    import bartlebot.app_controller as ac  # noqa: PLC0415

    async def _apply(_client, _deck, slot=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(ac, "apply_deck", _apply)
    strategy.player_decks = {1: _deck()}

    await PreRunDeckHarness(["c1"])._apply_pre_run_decks()  # must not raise


async def test_timeout_is_logged_as_an_error(strategy, monkeypatch, caplog):
    import bartlebot.app_controller as ac  # noqa: PLC0415

    async def _apply(_client, _deck, slot=None):
        raise TimeoutError

    monkeypatch.setattr(ac, "apply_deck", _apply)
    strategy.player_decks = {2: _deck()}

    with caplog.at_level("ERROR", logger="bartlebot.app_controller"):
        await PreRunDeckHarness(["c1", "c2"])._apply_pre_run_decks()

    assert "slot 2" in caplog.text


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


async def test_decks_are_applied_concurrently(strategy, monkeypatch):
    """All four applies must be in flight together, not run end to end.

    Asserted by having every apply wait on a barrier that only releases once all
    four have arrived: under the old sequential loop the first one would wait
    forever and this test would time out.
    """
    import bartlebot.app_controller as ac  # noqa: PLC0415

    started = asyncio.Barrier(4)

    async def _apply(_client, _deck, slot=None):
        await started.wait()

    monkeypatch.setattr(ac, "apply_deck", _apply)
    strategy.player_decks = {1: _deck(), 2: _deck(), 3: _deck(), 4: _deck()}

    harness = PreRunDeckHarness(["c1", "c2", "c3", "c4"])
    async with asyncio.timeout(5):
        await harness._apply_pre_run_decks()


async def test_all_applies_complete_before_returning(strategy, monkeypatch):
    """The gather must be awaited — the FSM starts the moment this returns, so a
    still-running apply would be clicking the deck UI during the run."""
    import bartlebot.app_controller as ac  # noqa: PLC0415

    done: list[str] = []

    async def _apply(client, _deck, slot=None):
        await asyncio.sleep(0.01)
        done.append(client)

    monkeypatch.setattr(ac, "apply_deck", _apply)
    strategy.player_decks = {1: _deck(), 2: _deck(), 3: _deck()}

    await PreRunDeckHarness(["c1", "c2", "c3"])._apply_pre_run_decks()

    assert sorted(done) == ["c1", "c2", "c3"]
