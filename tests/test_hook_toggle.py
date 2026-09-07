"""Tests for ClientManager unhook/rehook, including the bulk Unhook All / Rehook All paths.

The bulk methods exist so a developer can drop every client's hooks in one click while
iterating on the app, then pick them all back up. They must leave exactly the same state
as calling the single-client methods one at a time, but notify listeners only once — a
per-client emit rebuilds the whole sidebar and restarts its refresh loop.
"""

from unittest.mock import MagicMock

import pytest

from bartlebot.client_manager import ClientManager
from bartlebot.launch_tracker import LaunchTracker
from tests.test_client_slots import FakeGameView

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeClient:
    """Client whose close()/activate_hooks() record calls and can be told to fail."""

    def __init__(self, hwnd: int, *, rehook_fails: bool = False) -> None:
        self.window_handle = hwnd
        self.rehook_fails = rehook_fails
        self.closed = False
        self.hooked = True

    async def close(self) -> None:
        self.closed = True
        self.hooked = False

    async def activate_hooks(self) -> None:
        if self.rehook_fails:
            raise RuntimeError("hook activation failed")
        self.hooked = True

    def __repr__(self) -> str:
        return f"FakeClient({self.window_handle})"


class HookHarness:
    """Hosts hook-toggle state and binds the real ClientManager methods (see RecoveryHarness)."""

    unhook_client = ClientManager.unhook_client
    unhook_all = ClientManager.unhook_all
    rehook_client = ClientManager.rehook_client
    rehook_all = ClientManager.rehook_all
    _unhook_one = ClientManager._unhook_one
    _rehook_one = ClientManager._rehook_one
    _assign_rehooked_slots = ClientManager._assign_rehooked_slots
    _finish_hook_change = ClientManager._finish_hook_change
    _sync_tiles = ClientManager._sync_tiles
    tile_slots = ClientManager.tile_slots
    _embeddable_hwnds = ClientManager._embeddable_hwnds
    _account_label = ClientManager._account_label
    _assign_slot = ClientManager._assign_slot

    def __init__(self) -> None:
        self._clients: list = []
        self._unhooked_clients: list = []
        self._unhooked_hwnds: set[int] = set()
        self._failed_hook_hwnds: set[int] = set()
        self._hwnd_account_map: dict[int, str] = {}
        # Every window stays open through an unhook -- that is the point of it -- so the
        # handler keeps reporting all four regardless of which are hooked.
        self._handler = MagicMock(clients=[])
        self._client_order: list[str | None] = []
        self._launches = LaunchTracker()
        self._vacated_slots: dict[str, int] = {}
        self._machine = None
        self._game_view = FakeGameView()
        # Real _refresh_account_map reads window titles via ctypes; titles are set up
        # by the fixture instead so the map is already populated.
        self._refresh_account_map = lambda live_clients: None
        self.clients_changed = MagicMock()
        self.all_clients_changed = MagicMock()

    @property
    def active_hwnds(self) -> list[int]:
        return [c.window_handle for c in self._clients]

    @property
    def unhooked_hwnds(self) -> list[int]:
        return [c.window_handle for c in self._unhooked_clients]


@pytest.fixture
def harness() -> HookHarness:
    h = HookHarness()
    h._clients = [FakeClient(10), FakeClient(20), FakeClient(30), FakeClient(40)]
    h._hwnd_account_map = {10: "a", 20: "b", 30: "c", 40: "d"}
    h._client_order = ["a", "b", "c", "d"]
    h._handler.clients = list(h._clients)
    h._sync_tiles()
    return h


# ---------------------------------------------------------------------------
# Unhook
# ---------------------------------------------------------------------------


class TestUnhookAll:
    async def test_moves_every_client_to_unhooked(self, harness: HookHarness) -> None:
        await harness.unhook_all()
        assert harness.active_hwnds == []
        assert harness.unhooked_hwnds == [10, 20, 30, 40]
        assert harness._unhooked_hwnds == {10, 20, 30, 40}

    async def test_closes_hooks_on_every_client(self, harness: HookHarness) -> None:
        clients = list(harness._clients)
        await harness.unhook_all()
        assert all(c.closed for c in clients)

    async def test_clears_ordering_and_account_map(self, harness: HookHarness) -> None:
        await harness.unhook_all()
        assert harness._client_order == []
        assert harness._hwnd_account_map == {}

    async def test_unembeds_every_tile(self, harness: HookHarness) -> None:
        await harness.unhook_all()
        assert harness._game_view.tiles == {}

    async def test_notifies_listeners_once(self, harness: HookHarness) -> None:
        await harness.unhook_all()
        assert harness.clients_changed.emit.call_count == 1
        assert harness.all_clients_changed.emit.call_count == 1

    async def test_matches_repeated_single_unhooks(self, harness: HookHarness) -> None:
        other = HookHarness()
        other._clients = [FakeClient(h) for h in (10, 20, 30, 40)]
        other._hwnd_account_map = dict(harness._hwnd_account_map)
        other._client_order = ["a", "b", "c", "d"]
        other._handler.clients = list(other._clients)
        other._sync_tiles()
        for _ in range(4):
            await other.unhook_client(0)

        await harness.unhook_all()
        assert harness.unhooked_hwnds == other.unhooked_hwnds
        assert harness._unhooked_hwnds == other._unhooked_hwnds
        assert harness._client_order == other._client_order
        assert harness._game_view.tiles == other._game_view.tiles

    async def test_no_clients_is_a_silent_noop(self) -> None:
        empty = HookHarness()
        await empty.unhook_all()
        assert empty.clients_changed.emit.call_count == 0
        assert empty.all_clients_changed.emit.call_count == 0

    async def test_close_failure_still_unhooks(self, harness: HookHarness) -> None:
        async def boom() -> None:
            raise RuntimeError("close failed")

        harness._clients[1].close = boom
        await harness.unhook_all()
        assert harness.active_hwnds == []
        assert harness.unhooked_hwnds == [10, 20, 30, 40]

    async def test_machine_sees_the_emptied_client_list(self, harness: HookHarness) -> None:
        harness._machine = MagicMock()
        await harness.unhook_all()
        harness._machine.set_clients.assert_called_once_with([])


# ---------------------------------------------------------------------------
# Rehook
# ---------------------------------------------------------------------------


class TestRehookAll:
    async def test_round_trip_restores_all_clients(self, harness: HookHarness) -> None:
        await harness.unhook_all()
        harness._hwnd_account_map = {10: "a", 20: "b", 30: "c", 40: "d"}
        await harness.rehook_all()
        assert harness.active_hwnds == [10, 20, 30, 40]
        assert harness.unhooked_hwnds == []
        assert harness._unhooked_hwnds == set()

    async def test_round_trip_restores_ordering_and_tiles(self, harness: HookHarness) -> None:
        await harness.unhook_all()
        harness._hwnd_account_map = {10: "a", 20: "b", 30: "c", 40: "d"}
        await harness.rehook_all()
        assert harness._client_order == ["a", "b", "c", "d"]
        assert harness._game_view.tiles == {0: 10, 1: 20, 2: 30, 3: 40}

    async def test_notifies_listeners_once(self, harness: HookHarness) -> None:
        await harness.unhook_all()
        harness.clients_changed.reset_mock()
        harness.all_clients_changed.reset_mock()
        await harness.rehook_all()
        assert harness.clients_changed.emit.call_count == 1
        assert harness.all_clients_changed.emit.call_count == 1

    async def test_failed_client_stays_unhooked(self, harness: HookHarness) -> None:
        await harness.unhook_all()
        harness._unhooked_clients[1].rehook_fails = True
        await harness.rehook_all()
        assert harness.active_hwnds == [10, 30, 40]
        assert harness.unhooked_hwnds == [20]
        assert 20 in harness._unhooked_hwnds

    async def test_all_failures_leave_state_untouched(self, harness: HookHarness) -> None:
        await harness.unhook_all()
        for c in harness._unhooked_clients:
            c.rehook_fails = True
        harness.all_clients_changed.reset_mock()
        await harness.rehook_all()
        assert harness.active_hwnds == []
        assert harness.unhooked_hwnds == [10, 20, 30, 40]
        assert harness.all_clients_changed.emit.call_count == 0

    async def test_nothing_unhooked_is_a_silent_noop(self, harness: HookHarness) -> None:
        await harness.rehook_all()
        assert harness.clients_changed.emit.call_count == 0
        assert harness.all_clients_changed.emit.call_count == 0


# ---------------------------------------------------------------------------
# Single-client paths (unchanged behaviour, refactored onto shared helpers)
# ---------------------------------------------------------------------------


class TestSingleClient:
    async def test_unhook_client_removes_only_that_client(self, harness: HookHarness) -> None:
        await harness.unhook_client(1)
        assert harness.active_hwnds == [10, 30, 40]
        assert harness.unhooked_hwnds == [20]
        assert harness._client_order == ["a", "c", "d"]

    async def test_unhook_client_out_of_range_is_a_noop(self, harness: HookHarness) -> None:
        await harness.unhook_client(9)
        assert harness.active_hwnds == [10, 20, 30, 40]
        assert harness.all_clients_changed.emit.call_count == 0

    async def test_rehook_client_appends_and_reassigns_slot(self, harness: HookHarness) -> None:
        await harness.unhook_client(1)
        harness._hwnd_account_map[20] = "b"
        await harness.rehook_client(20)
        assert harness.active_hwnds == [10, 30, 40, 20]
        assert harness._client_order == ["a", "c", "d", "b"]

    async def test_rehook_client_unknown_hwnd_is_a_noop(self, harness: HookHarness) -> None:
        await harness.rehook_client(999)
        assert harness.all_clients_changed.emit.call_count == 0

    async def test_rehook_client_failure_leaves_it_unhooked(self, harness: HookHarness) -> None:
        await harness.unhook_client(1)
        harness.all_clients_changed.reset_mock()
        harness._unhooked_clients[0].rehook_fails = True
        await harness.rehook_client(20)
        assert harness.unhooked_hwnds == [20]
        assert harness.all_clients_changed.emit.call_count == 0
