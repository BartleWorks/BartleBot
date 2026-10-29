"""BartleBot's part of a reorder: refused while a bot runs, and the drop tables follow it.

Reordering itself is core's (``bartleby-core``'s ``tests/runtime/clients/test_client_reorder.py``).
What is BartleBot's is the running bot, which holds the very list a reorder rewrites in
place, and the drop tables, which are kept per player.
"""

from unittest.mock import MagicMock

import pytest

from bartlebot.clients.client_manager import ClientManager


class FakeClient:
    def __init__(self, hwnd: int) -> None:
        self.window_handle = hwnd

    def __repr__(self) -> str:
        return f"FakeClient({self.window_handle})"


class ReorderHarness:
    """Hosts just the state ``reorder_clients`` and BartleBot's refusal touch."""

    reorder_clients = ClientManager.reorder_clients
    swap_clients = ClientManager.swap_clients
    _refuse_reorder = ClientManager._refuse_reorder

    def __init__(self, hwnds: list[int], order: list[str | None]) -> None:
        self._machine = None
        self._clients = [FakeClient(h) for h in hwnds]
        self._client_order: list[str | None] = list(order)
        self._unhooked_clients: list = []
        self._hwnd_account_map: dict[int, str] = {}
        self._sync_tiles = MagicMock()
        self.clients_reordered = MagicMock()
        self.clients_changed = MagicMock()
        self.all_clients_changed = MagicMock()

    @property
    def hwnds(self) -> list[int]:
        return [c.window_handle for c in self._clients]


@pytest.fixture
def harness() -> ReorderHarness:
    return ReorderHarness([100, 110, 120, 130], ["a", "b", "c", "d"])


def test_a_reorder_is_refused_while_a_bot_is_running(harness):
    """The running node holds this very list; a reorder would move its players under it."""
    harness._machine = object()

    harness.reorder_clients([1, 0, 2, 3])

    assert harness.hwnds == [100, 110, 120, 130]
    assert harness._client_order == ["a", "b", "c", "d"]
    harness._sync_tiles.assert_not_called()
    harness.clients_changed.emit.assert_not_called()
    harness.clients_reordered.emit.assert_not_called()


def test_a_refused_reorder_redraws_the_sidebar_in_the_order_it_really_is(harness):
    """The column already shows the dropped order, and nothing else would put it back."""
    harness._machine = object()

    harness.reorder_clients([1, 0, 2, 3])

    hooked, unhooked = harness.all_clients_changed.emit.call_args.args
    assert [c.window_handle for c in hooked] == [100, 110, 120, 130]
    assert unhooked == []


def test_a_swap_is_refused_while_a_bot_is_running(harness):
    harness._machine = object()

    harness.swap_clients(0, 1)

    assert harness.hwnds == [100, 110, 120, 130]
    harness.clients_reordered.emit.assert_not_called()


def test_the_drop_tables_follow_a_reorder():
    """Wired in the constructor: the drop tables are per player, so they move with them."""
    drops = MagicMock()
    manager = ClientManager(
        handler=MagicMock(clients=[]), game_view=MagicMock(), drops_widget=drops, get_recovery_state=lambda: None
    )
    manager._clients = [FakeClient(h) for h in (100, 110, 120)]

    manager.reorder_clients([2, 0, 1])
    manager.swap_clients(0, 1)

    assert drops.reorder_clients.call_args_list[0].args == ([2, 0, 1],)
    assert drops.reorder_clients.call_args_list[1].args == ([1, 0, 2],)
