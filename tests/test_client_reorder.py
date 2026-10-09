"""Tests for reordering the live client list in one update.

``swap_clients`` already moves two clients, and a drag could in principle be decomposed
into calls to it. What these cover is the reason it is not: ``reorder_clients`` runs the
tile sync and both signals exactly once for the whole gesture, and refuses a permutation
that no longer describes the list rather than applying it to a list the user never dragged.

No QApplication: the methods under test are bound onto a plain harness object, the same
descriptor trick ``RecoveryHarness`` uses.
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
    """Hosts just the state ``reorder_clients`` and ``account_for`` touch."""

    reorder_clients = ClientManager.reorder_clients
    swap_clients = ClientManager.swap_clients
    account_for = ClientManager.account_for
    _refuse_reorder_mid_run = ClientManager._refuse_reorder_mid_run

    def __init__(self, hwnds: list[int], order: list[str | None] | None = None) -> None:
        self._machine = None
        self._clients = [FakeClient(h) for h in hwnds]
        self._client_order: list[str | None] = [] if order is None else list(order)
        self._unhooked_clients: list = []
        self._hwnd_account_map: dict[int, str] = {}
        self._drops_widget = MagicMock()
        self._sync_tiles = MagicMock()
        self.clients_changed = MagicMock()
        self.all_clients_changed = MagicMock()

    @property
    def hwnds(self) -> list[int]:
        return [c.window_handle for c in self._clients]


@pytest.fixture
def harness() -> ReorderHarness:
    return ReorderHarness([100, 110, 120, 130], ["a", "b", "c", "d"])


# ---------------------------------------------------------------------------
# reorder_clients
# ---------------------------------------------------------------------------


def test_permutation_is_applied(harness):
    harness.reorder_clients([2, 0, 3, 1])

    assert harness.hwnds == [120, 100, 130, 110]


def test_client_order_follows_the_same_permutation(harness):
    harness.reorder_clients([2, 0, 3, 1])

    assert harness._client_order == ["c", "a", "d", "b"]


def test_one_gesture_is_one_update(harness):
    """The whole point of the method: each of these fires once, not once per transposition."""
    harness.reorder_clients([3, 2, 1, 0])

    assert harness._sync_tiles.call_count == 1
    assert harness.clients_changed.emit.call_count == 1
    assert harness.all_clients_changed.emit.call_count == 1
    assert harness._drops_widget.reorder_clients.call_count == 1
    harness._drops_widget.reorder_clients.assert_called_once_with([3, 2, 1, 0])


def test_a_reorder_is_refused_while_a_bot_is_running(harness):
    """The running node holds this very list; a reorder would move its players under it."""
    harness._machine = object()

    harness.reorder_clients([1, 0, 2, 3])

    assert harness.hwnds == [100, 110, 120, 130]
    assert harness._client_order == ["a", "b", "c", "d"]
    harness._sync_tiles.assert_not_called()
    harness.clients_changed.emit.assert_not_called()
    harness._drops_widget.reorder_clients.assert_not_called()


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
    harness._drops_widget.swap_clients.assert_not_called()


def test_the_signals_carry_the_new_order(harness):
    harness.reorder_clients([1, 0, 2, 3])

    (published,) = harness.clients_changed.emit.call_args.args
    assert [c.window_handle for c in published] == [110, 100, 120, 130]
    hooked, unhooked = harness.all_clients_changed.emit.call_args.args
    assert [c.window_handle for c in hooked] == [110, 100, 120, 130]
    assert unhooked == []


@pytest.mark.parametrize(
    "order",
    [
        [0, 1, 2],  # stale: a client unhooked out from under the drag
        [0, 1, 2, 3, 4],  # stale: a client hooked during the drag
        [0, 1, 1, 2],  # not a permutation
        [0, 1, 2, 4],  # out of range
    ],
)
def test_a_stale_permutation_is_refused(harness, order):
    harness.reorder_clients(order)

    assert harness.hwnds == [100, 110, 120, 130]
    assert harness._client_order == ["a", "b", "c", "d"]
    assert harness._sync_tiles.call_count == 0
    assert harness.clients_changed.emit.call_count == 0
    assert harness.all_clients_changed.emit.call_count == 0
    assert harness._drops_widget.reorder_clients.call_count == 0


def test_the_identity_permutation_changes_nothing(harness):
    harness.reorder_clients([0, 1, 2, 3])

    assert harness.hwnds == [100, 110, 120, 130]
    assert harness._sync_tiles.call_count == 0
    assert harness.clients_changed.emit.call_count == 0


def test_the_client_list_object_is_reordered_in_place(harness):
    """Listeners holding the list ClientManager published see the new order too."""
    held = harness._clients

    harness.reorder_clients([1, 0, 2, 3])

    assert held is harness._clients
    assert [c.window_handle for c in held] == [110, 100, 120, 130]


def test_a_longer_client_order_keeps_its_tail():
    """A slot awaiting recovery past the end of the live list is not dragged into the shuffle."""
    harness = ReorderHarness([100, 110], ["a", "b", "c"])

    harness.reorder_clients([1, 0])

    assert harness._client_order == ["b", "a", "c"]


def test_a_shorter_client_order_is_left_alone():
    """Positional, exactly as swap_clients is -- out of range means untouched, not partly applied."""
    harness = ReorderHarness([100, 110, 120], ["a", "b"])

    harness.reorder_clients([2, 1, 0])

    assert harness.hwnds == [120, 110, 100]
    assert harness._client_order == ["a", "b"]


# ---------------------------------------------------------------------------
# account_for
# ---------------------------------------------------------------------------


def test_account_for_names_the_launching_account(harness):
    harness._hwnd_account_map = {110: "someaccount"}

    assert harness.account_for(harness._clients[1]) == "someaccount"


def test_account_for_is_none_for_a_client_the_map_has_never_seen(harness):
    assert harness.account_for(harness._clients[0]) is None


def test_account_for_follows_the_client_not_the_position(harness):
    """The answer is keyed by window, so a reorder moves it with the client."""
    harness._hwnd_account_map = {100: "first", 130: "last"}

    harness.reorder_clients([3, 0, 1, 2])

    assert harness.account_for(harness._clients[0]) == "last"
    assert harness.account_for(harness._clients[1]) == "first"
