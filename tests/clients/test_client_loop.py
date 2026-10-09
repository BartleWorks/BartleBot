"""Tests for ClientManager's hook-activation path in _client_loop.

The point of these tests is the one property the loop has to keep: a client whose
activate_hooks() never returns must not hold up anything else. WizWalker's
activate_hooks() waits for the player struct with no timeout, and a window parked on the
login or character-select screen never writes it — which is the normal state of every
window auto-select is driving. Before the fix the loop awaited that inline and stopped
ticking entirely.

Like tests/clients/test_recovery.py, the harness binds real ClientManager methods to a minimal
fake object so the production code runs without a QApplication, WizSprinter, or any
Wizard101 process.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bartlebot.clients.client_manager import ClientManager

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------


class FakeHookClient:
    """Stand-in for a WizWalker Client whose hook activation we control."""

    def __init__(self, hwnd: int) -> None:
        self.window_handle = hwnd
        self.window_rectangle = MagicMock(x1=0, y1=hwnd)
        # Resolve this to let activate_hooks() return; leave it pending to model a client
        # sitting at character select; set an exception to model a failed install.
        self.hooks_ready: asyncio.Future = asyncio.get_event_loop().create_future()
        self.activate_calls = 0

    async def activate_hooks(self) -> None:
        self.activate_calls += 1
        await self.hooks_ready


class FakeHandler:
    """Stand-in for WizSprinter: hands out new clients once each."""

    def __init__(self) -> None:
        self.clients: list = []
        self._announced: set[int] = set()

    def add(self, client) -> None:
        self.clients.append(client)

    def remove_dead_clients(self) -> None:
        pass

    def get_new_clients(self) -> list:
        new = [c for c in self.clients if c.window_handle not in self._announced]
        self._announced.update(c.window_handle for c in new)
        return new


class LoopHarness:
    """Binds the real loop and activation methods; stubs the parts under no test."""

    _client_loop = ClientManager._client_loop
    _start_activation = ClientManager._start_activation
    _build_ordered_clients = ClientManager._build_ordered_clients
    _assign_slot = ClientManager._assign_slot
    _place_account = ClientManager._place_account
    _forget_closed_windows = ClientManager._forget_closed_windows
    stop = ClientManager.stop
    has_client_titled = ClientManager.has_client_titled
    client_titled = ClientManager.client_titled

    def __init__(self) -> None:
        self._handler = FakeHandler()
        self._clients: list = []
        self._unhooked_clients: list = []
        self._unhooked_hwnds: set[int] = set()
        self._activating_hwnds: set[int] = set()
        self._failed_hook_hwnds: set[int] = set()
        self._activation_tasks: set[asyncio.Task] = set()
        self._client_loop_task: asyncio.Task | None = None
        self._hwnd_account_map: dict[int, str] = {}
        self._hwnd_titles: dict[int, str] = {}
        self._hwnd_user_ids: dict[int, int] = {}
        self._client_order: list[str | None] = []
        self._pending_recovery: dict[int, str] = {}
        self._vacated_slots: dict[str, int] = {}
        self._machine = None

        self.clients_changed = MagicMock()
        self.all_clients_changed = MagicMock()
        self._refresh_account_map = MagicMock()
        self._refresh_account_identity = AsyncMock()
        self._game_view = MagicMock()
        self._sync_tiles = MagicMock()
        self._place_pending_accounts = MagicMock()
        self._on_clients_removed = MagicMock()
        self._refresh_wizard_names = AsyncMock()
        self._learn_character_roster = MagicMock()

    def start(self) -> None:
        self._client_loop_task = asyncio.create_task(self._client_loop())

    @property
    def published_hwnds(self) -> set[int]:
        return {c.window_handle for c in self._clients}


# Real cadences are 1 s / 3 s; compressed here so the suite does not spend half a minute
# sleeping. The loop reads these as module globals at each call, so patching lands.
_TEST_INTERVAL = 0.01


async def _tick(cycles: float = 4) -> None:
    """Let the loop run for a few of its detection cycles."""
    await asyncio.sleep(_TEST_INTERVAL * cycles)


@pytest.fixture(autouse=True)
def _fast_loop(monkeypatch):
    for name in (
        "CLIENT_DETECT_INTERVAL",
        "CLIENT_DETECT_INTERVAL_IDLE",
        "CLIENT_DETECT_ERROR_BACKOFF",
    ):
        monkeypatch.setattr(f"bartlebot.clients.client_manager.{name}", _TEST_INTERVAL)


@pytest.fixture
def harness() -> LoopHarness:
    return LoopHarness()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_pending_client_is_not_published(harness):
    """A client whose hooks never come up stays out of the live client list."""
    stuck = FakeHookClient(100)
    harness._handler.add(stuck)
    harness.start()
    try:
        await _tick()
        assert harness.published_hwnds == set()
        assert harness._activating_hwnds == {100}
    finally:
        harness.stop()


async def test_pending_client_does_not_block_another(harness):
    """The stall regression: one client stuck at character select must not gate the rest.

    Before the fix these two were awaited together in a single gather, so the ready
    client was invisible to the app until the stuck one logged a character in.
    """
    stuck = FakeHookClient(100)
    ready = FakeHookClient(200)
    harness._handler.add(stuck)
    harness._handler.add(ready)
    harness.start()
    try:
        ready.hooks_ready.set_result(None)
        await _tick()
        assert harness.published_hwnds == {200}
        assert harness._activating_hwnds == {100}
    finally:
        harness.stop()


async def test_client_is_published_once_hooks_come_up(harness):
    """The auto-select completion signal: the client appears when its character loads in."""
    client = FakeHookClient(100)
    harness._handler.add(client)
    harness.start()
    try:
        await _tick(3)
        assert harness.published_hwnds == set()

        client.hooks_ready.set_result(None)
        await _tick(3)
        assert harness.published_hwnds == {100}
        assert harness._activating_hwnds == set()
    finally:
        harness.stop()


async def test_failed_activation_is_not_published_or_retried(harness):
    """A failed hook install is abandoned, not retried.

    activate_all_hooks() installs every hook before waiting, so a client that failed
    mid-wait has left jmps pointing into the autobot region. Building a fresh Client for
    the same process would zero that region out from under them, so the client is left
    registered with the handler and simply never published.
    """
    client = FakeHookClient(100)
    harness._handler.add(client)
    harness.start()
    try:
        client.hooks_ready.set_exception(RuntimeError("pattern scan failed"))
        await _tick()
        assert harness.published_hwnds == set()
        assert harness._failed_hook_hwnds == {100}
        assert client.activate_calls == 1
    finally:
        harness.stop()


async def test_failed_hwnd_is_forgotten_when_window_closes(harness):
    """_failed_hook_hwnds must not grow without bound across a long session."""
    client = FakeHookClient(100)
    harness._handler.add(client)
    harness.start()
    try:
        client.hooks_ready.set_exception(RuntimeError("boom"))
        await _tick()
        assert harness._failed_hook_hwnds == {100}

        harness._handler.clients.clear()
        await _tick()
        assert harness._failed_hook_hwnds == set()
    finally:
        harness.stop()


async def test_a_closed_window_leaves_the_account_map(harness):
    """Issue #65: nothing unwrote the account map, so a closed window kept its account
    in active_accounts for the rest of the session and begin_launch refused to relaunch it."""
    client = FakeHookClient(100)
    harness._handler.add(client)
    harness._hwnd_account_map[100] = "alpha"
    harness.start()
    try:
        client.hooks_ready.set_result(None)
        await _tick()
        assert harness._hwnd_account_map == {100: "alpha"}

        harness._handler.clients.clear()
        await _tick()
        assert harness._hwnd_account_map == {}
    finally:
        harness.stop()


async def test_a_closed_window_is_still_reported_as_removed_first(harness):
    """The map is what names the account recovery relaunches, so it is dropped after
    _on_clients_removed has read it, never before."""
    client = FakeHookClient(100)
    harness._handler.add(client)
    harness._hwnd_account_map[100] = "alpha"
    harness.start()
    try:
        client.hooks_ready.set_result(None)
        await _tick()
        seen: list[dict] = []
        harness._on_clients_removed = lambda hwnds: seen.append(dict(harness._hwnd_account_map))

        harness._handler.clients.clear()
        await _tick()
        assert seen == [{100: "alpha"}]
    finally:
        harness.stop()


async def test_stop_cancels_in_flight_activation(harness):
    """A never-resolving activation must not outlive the manager."""
    stuck = FakeHookClient(100)
    harness._handler.add(stuck)
    harness.start()
    await _tick(2)
    assert harness._activation_tasks

    tasks = list(harness._activation_tasks)
    harness.stop()
    await asyncio.sleep(0)
    assert all(t.cancelled() or t.done() for t in tasks)
    assert harness._activating_hwnds == set()


async def test_has_client_titled(harness, monkeypatch):
    """The completion predicate auto-select and recovery both wait on."""
    monkeypatch.setattr(
        "bartlebot.clients.client_manager.get_window_title",
        lambda hwnd: {100: "[Alpha] Wizard101"}.get(hwnd, ""),
    )
    harness._clients = [FakeHookClient(100)]
    assert harness.has_client_titled("[Alpha] Wizard101") is True
    assert harness.has_client_titled("[Beta] Wizard101") is False
