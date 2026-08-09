"""Tests for ClientManager slot bookkeeping and game-view embed reconciliation.

Two bugs motivated these, both surfacing when several clients crash at once and
recover one at a time:

  - _sync_embedded_clients interleaved unembed/embed calls against index-keyed
    tiles, so a client's unembed could tear down a tile another client had just
    been moved into, leaving that game window floating unembedded.
  - _on_clients_removed always nulled a client's slot but recovery did not always
    run or succeed, so returning clients were appended past the end of the
    ordering — four clients ending up on slots 0-4.

FakeGameView models the part of GameView that matters here: tiles keyed by slot
index, and embed_client refusing to overwrite an occupied slot.
"""

from unittest.mock import patch

import pytest

from tests.test_recovery import RecoveryHarness, _make_machine, _mock_create_task

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeGameView:
    """Slot-index-keyed tile store mirroring GameView's embed/unembed semantics."""

    def __init__(self) -> None:
        self.tiles: dict[int, int] = {}  # slot index → hwnd

    def embed_client(self, hwnd: int, client_idx: int) -> None:
        if client_idx in self.tiles:
            return  # GameView early-returns on an occupied slot
        self.tiles[client_idx] = hwnd

    def unembed_client(self, client_idx: int) -> None:
        self.tiles.pop(client_idx, None)


class FakeClient:
    def __init__(self, hwnd: int) -> None:
        self.window_handle = hwnd

    def __repr__(self) -> str:
        return f"FakeClient({self.window_handle})"


def _clients(*hwnds: int) -> list[FakeClient]:
    return [FakeClient(h) for h in hwnds]


@pytest.fixture
def harness() -> RecoveryHarness:
    h = RecoveryHarness()
    h._game_view = FakeGameView()
    return h


def _expected_tiles(clients: list[FakeClient]) -> dict[int, int]:
    return {i: c.window_handle for i, c in enumerate(clients)}


# ---------------------------------------------------------------------------
# _sync_embedded_clients
# ---------------------------------------------------------------------------


class TestSyncEmbeddedClients:
    def test_initial_embed_fills_every_slot(self, harness: RecoveryHarness) -> None:
        new = _clients(10, 20, 30, 40)
        harness._sync_embedded_clients([], new)
        assert harness._game_view.tiles == _expected_tiles(new)

    def test_removed_clients_are_unembedded(self, harness: RecoveryHarness) -> None:
        prev = _clients(10, 20, 30, 40)
        harness._sync_embedded_clients([], prev)
        harness._sync_embedded_clients(prev, prev[:1])
        assert harness._game_view.tiles == {0: 10}

    def test_all_clients_removed_clears_view(self, harness: RecoveryHarness) -> None:
        prev = _clients(10, 20)
        harness._sync_embedded_clients([], prev)
        harness._sync_embedded_clients(prev, [])
        assert harness._game_view.tiles == {}

    def test_two_clients_swapping_slots_both_stay_embedded(self, harness: RecoveryHarness) -> None:
        """A pure swap is the minimal case the old interleaved loop dropped a client on."""
        a, b = FakeClient(10), FakeClient(20)
        harness._sync_embedded_clients([], [a, b])
        harness._sync_embedded_clients([a, b], [b, a])
        assert harness._game_view.tiles == {0: 20, 1: 10}

    def test_shift_left_keeps_every_client_embedded(self, harness: RecoveryHarness) -> None:
        prev = _clients(10, 20, 30, 40)
        harness._sync_embedded_clients([], prev)
        # Client at slot 0 disappears; everyone shifts down one.
        new = prev[1:]
        harness._sync_embedded_clients(prev, new)
        assert harness._game_view.tiles == _expected_tiles(new)

    def test_staggered_recovery_leaves_no_client_unembedded(self, harness: RecoveryHarness) -> None:
        """Reproduces the reported failure: 4 clients crash, then return one at a time.

        Each return renumbers the clients that already came back, which is exactly the
        churn that used to strand windows outside the main application view.
        """
        view = harness._game_view
        c0, c1, c2, c3 = _clients(10, 20, 30, 40)

        prev = [c0, c1, c2, c3]
        harness._sync_embedded_clients([], prev)

        # All four crash.
        harness._sync_embedded_clients(prev, [])
        prev = []
        assert view.tiles == {}

        # They come back in a different order than their slots — the compacted
        # ordering means indices shift on every step.
        for new in ([c2], [c2, c3], [c1, c2, c3], [c0, c1, c2, c3]):
            harness._sync_embedded_clients(prev, new)
            assert view.tiles == _expected_tiles(new), f"stranded client after {new}"
            prev = new

        assert view.tiles == {0: 10, 1: 20, 2: 30, 3: 40}

    def test_unchanged_ordering_does_not_touch_the_view(self, harness: RecoveryHarness) -> None:
        prev = _clients(10, 20, 30)
        harness._sync_embedded_clients([], prev)
        calls: list[tuple] = []
        harness._game_view.embed_client = lambda h, i: calls.append(("embed", h, i))
        harness._game_view.unembed_client = lambda i: calls.append(("unembed", i))
        harness._sync_embedded_clients(prev, list(prev))
        assert calls == []

    def test_view_errors_do_not_abort_the_reconcile(self, harness: RecoveryHarness) -> None:
        """One failing tile must not strand the clients queued behind it."""
        prev = _clients(10, 20)
        harness._sync_embedded_clients([], prev)
        real_unembed = harness._game_view.unembed_client

        def _flaky_unembed(idx: int) -> None:
            real_unembed(idx)
            if idx == 0:
                raise RuntimeError("window already destroyed")

        harness._game_view.unembed_client = _flaky_unembed
        new = _clients(30, 40)
        harness._sync_embedded_clients(prev, new)
        assert harness._game_view.tiles == _expected_tiles(new)


# ---------------------------------------------------------------------------
# _assign_slot
# ---------------------------------------------------------------------------


class TestAssignSlot:
    def test_appends_when_ordering_is_full(self, harness: RecoveryHarness) -> None:
        harness._client_order = ["user1"]
        harness._assign_slot("user2")
        assert harness._client_order == ["user1", "user2"]

    def test_already_present_username_is_a_no_op(self, harness: RecoveryHarness) -> None:
        harness._client_order = ["user1", "user2"]
        harness._assign_slot("user1")
        assert harness._client_order == ["user1", "user2"]

    def test_reclaims_the_slot_the_client_last_occupied(self, harness: RecoveryHarness) -> None:
        harness._client_order = ["user1", None, "user3"]
        harness._vacated_slots = {"user2": 1}
        harness._assign_slot("user2")
        assert harness._client_order == ["user1", "user2", "user3"]
        assert harness._vacated_slots == {}

    def test_falls_back_to_first_free_slot_when_original_is_taken(self, harness: RecoveryHarness) -> None:
        harness._client_order = [None, "someone_else", "user3"]
        harness._vacated_slots = {"user2": 1}
        harness._assign_slot("user2")
        assert harness._client_order == ["user2", "someone_else", "user3"]

    def test_falls_back_to_first_free_slot_when_original_is_out_of_range(self, harness: RecoveryHarness) -> None:
        harness._client_order = [None, "user3"]
        harness._vacated_slots = {"user2": 7}
        harness._assign_slot("user2")
        assert harness._client_order == ["user2", "user3"]

    def test_untracked_client_fills_a_hole_rather_than_growing(self, harness: RecoveryHarness) -> None:
        harness._client_order = ["user1", None, "user3"]
        harness._assign_slot("newcomer")
        assert harness._client_order == ["user1", "newcomer", "user3"]


# ---------------------------------------------------------------------------
# Slot drift — _on_clients_removed followed by _assign_slot
# ---------------------------------------------------------------------------


class TestSlotDrift:
    def test_crash_while_stopped_then_relaunch_reuses_the_slot(self, harness: RecoveryHarness) -> None:
        harness._hwnd_account_map = {100: "user1", 200: "user2"}
        harness._client_order = ["user1", "user2"]
        harness._machine = _make_machine(stopped=True)

        harness._on_clients_removed({100})
        assert harness._client_order == [None, "user2"]
        assert harness._pending_recovery == {}

        harness._assign_slot("user1")
        assert harness._client_order == ["user1", "user2"]

    def test_abandoned_recovery_then_late_arrival_reuses_the_slot(self, harness: RecoveryHarness) -> None:
        """Recovery times out and drops the client from the queue, but it logs in anyway.

        This is the path that produced slot 4 on a four-client run: the returning
        client was no longer pending, so it was appended past the end.
        """
        harness._hwnd_account_map = {100: "user1", 200: "user2", 300: "user3"}
        harness._client_order = ["user1", "user2", "user3"]
        harness._machine = _make_machine()

        with patch("asyncio.create_task", side_effect=_mock_create_task):
            harness._on_clients_removed({200})
        assert harness._client_order == ["user1", None, "user3"]

        # Recovery gives up and clears the queue (launch failure / login timeout).
        harness._pending_recovery.clear()

        # The client finishes logging in later and is picked up as a plain addition.
        harness._assign_slot("user2")
        assert harness._client_order == ["user1", "user2", "user3"]
        assert len(harness._client_order) == 3

    def test_repeated_crash_cycles_do_not_grow_the_ordering(self, harness: RecoveryHarness) -> None:
        harness._hwnd_account_map = {100: "user1", 200: "user2", 300: "user3", 400: "user4"}
        harness._client_order = ["user1", "user2", "user3", "user4"]
        harness._machine = _make_machine(stopped=True)

        for _ in range(5):
            harness._on_clients_removed({200, 300, 400})
            for username in ("user3", "user4", "user2"):  # return out of slot order
                harness._assign_slot(username)
            assert len(harness._client_order) == 4, harness._client_order

        assert harness._client_order == ["user1", "user2", "user3", "user4"]
