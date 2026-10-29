"""Tests for ClientManager slot bookkeeping and game-view tile reconciliation.

Slots and player indices are different numbers, and most of this file is about keeping
them apart -- see ``bartleby.runtime.clients.tile_slots`` for why they had to be separated at all. A slot
is a position in ``_client_order``, pinned before the windows exist and held for as long as
the account is running; a player index is a position in the live client list, which is
compacted and therefore moves as clients hook, crash and recover.

Three bugs motivated these:

  - Tiles keyed by the player index shuffled during a launch: with a reserved order of
    A, B, C and B hooking first, B took the first tile and was re-parented into the second
    when A arrived. Invisible with auto-select on, unmissable with it off.
  - The old reconcile interleaved unembed/embed calls against index-keyed tiles, so a
    client's unembed could tear down a tile another client had just been moved into,
    leaving that game window floating unembedded.
  - _on_clients_removed always nulled a client's slot but recovery did not always run or
    succeed, so returning clients were appended past the end of the ordering -- four
    clients ending up on slots 0-4.

FakeGameView applies a real ``plan_tile_changes`` result, so the assertions are about
where windows ended up rather than about call order.
"""

from unittest.mock import MagicMock, patch

import pytest
from bartleby.runtime.clients.tile_slots import TileSlot, plan_tile_changes

from tests.clients.test_recovery import RecoveryHarness, _make_machine, _mock_create_task

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeGameView:
    """Slot-keyed tile store mirroring the part of GameView that sync_tiles drives."""

    def __init__(self) -> None:
        self.tiles: dict[int, int] = {}  # slot → hwnd
        self.pending: dict[int, str] = {}  # slot → account still launching
        self.labels: dict[int, str] = {}  # slot → caption
        self.rebuilt: list[int] = []  # hwnds whose tile was created, i.e. re-parented

    def sync_tiles(self, wanted) -> None:
        # Keyed the way the real view keys it: an hwnd for a window, the account for a
        # launch that has not produced one. See TileSlot.key.
        current = {slot: ("hwnd", hwnd) for slot, hwnd in self.tiles.items()}
        current.update({slot: ("pending", name) for slot, name in self.pending.items()})
        changes = plan_tile_changes(current, wanted)
        for slot in changes.removed:
            self.tiles.pop(slot, None)
            self.pending.pop(slot, None)
            self.labels.pop(slot, None)
        # Vacate every moving slot before filling any, against the original mapping: a
        # swap moves two tiles at once and clearing them one at a time loses one.
        vacated = {old for old, _new in changes.moved}
        moved_tiles = {slot: hwnd for slot, hwnd in self.tiles.items() if slot not in vacated}
        moved_pending = {slot: name for slot, name in self.pending.items() if slot not in vacated}
        moved_labels = {slot: text for slot, text in self.labels.items() if slot not in vacated}
        for old, new_slot in changes.moved:
            if old in self.tiles:
                moved_tiles[new_slot] = self.tiles[old]
            if old in self.pending:
                moved_pending[new_slot] = self.pending[old]
            moved_labels[new_slot] = self.labels.get(old, "")
        self.tiles, self.pending, self.labels = moved_tiles, moved_pending, moved_labels
        for spec in changes.added:
            if spec.is_pending:
                self.pending[spec.slot] = spec.username or ""
            else:
                self.tiles[spec.slot] = spec.hwnd
                self.rebuilt.append(spec.hwnd)
        for spec in wanted:
            self.labels[spec.slot] = spec.label

    def unembed_all(self) -> None:
        self.tiles.clear()
        self.pending.clear()
        self.labels.clear()


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


def _fleet(harness: RecoveryHarness, *usernames: str) -> dict[str, int]:
    """Give each username a window and a reserved slot; return username → hwnd.

    Windows only, deliberately: nothing here is hooked yet, which is the state a launch
    spends most of its time in and the one the old index space could not describe.
    """
    hwnds = {username: 100 + 10 * i for i, username in enumerate(usernames)}
    harness._client_order = list(usernames)
    harness._hwnd_account_map = {hwnd: user for user, hwnd in hwnds.items()}
    harness._handler.clients = [FakeClient(h) for h in hwnds.values()]
    return hwnds


def _hook(harness: RecoveryHarness, *hwnds: int) -> None:
    """Publish these windows as live clients, in the order given."""
    harness._clients = [FakeClient(h) for h in hwnds]


def _tiles(harness: RecoveryHarness, accounts: list[dict] | None = None) -> list[TileSlot]:
    with patch("bartlebot.clients.client_manager.SettingsManager") as sm:
        sm.instance.return_value = MagicMock(accounts=accounts or [])
        return harness.tile_slots()


def _sync(harness: RecoveryHarness) -> None:
    with patch("bartlebot.clients.client_manager.SettingsManager") as sm:
        sm.instance.return_value = MagicMock(accounts=[])
        harness._sync_tiles()


# ---------------------------------------------------------------------------
# plan_tile_changes — the pure reconciler
# ---------------------------------------------------------------------------


def _spec(slot: int, hwnd: int, player: int | None = None) -> TileSlot:
    return TileSlot(slot=slot, hwnd=hwnd, label=f"tile {slot}", player=player)


def _pending_spec(slot: int, username: str) -> TileSlot:
    """A tile for an account whose launch has started but whose window has not appeared."""
    return TileSlot(slot=slot, hwnd=None, label=username, player=None, username=username)


class TestPlanTileChanges:
    def test_everything_new_is_added(self) -> None:
        changes = plan_tile_changes({}, [_spec(0, 10), _spec(1, 20)])
        assert [t.hwnd for t in changes.added] == [10, 20]
        assert changes.removed == ()
        assert changes.moved == ()

    def test_a_window_that_is_gone_is_removed(self) -> None:
        changes = plan_tile_changes({0: ("hwnd", 10), 1: ("hwnd", 20)}, [_spec(0, 10)])
        assert changes.removed == (1,)
        assert changes.added == ()
        assert changes.moved == ()

    def test_a_window_changing_slot_moves_rather_than_rebuilding(self) -> None:
        """The point of matching on hwnd: rebuilding a tile re-parents the game window."""
        changes = plan_tile_changes({0: ("hwnd", 10)}, [_spec(2, 10)])
        assert changes.moved == ((0, 2),)
        assert changes.added == ()
        assert changes.removed == ()

    def test_a_swap_is_two_moves_and_nothing_else(self) -> None:
        changes = plan_tile_changes({0: ("hwnd", 10), 1: ("hwnd", 20)}, [_spec(0, 20), _spec(1, 10)])
        assert sorted(changes.moved) == [(0, 1), (1, 0)]
        assert changes.added == ()
        assert changes.removed == ()

    def test_an_unchanged_view_reports_nothing_to_do(self) -> None:
        changes = plan_tile_changes({0: ("hwnd", 10), 1: ("hwnd", 20)}, [_spec(0, 10), _spec(1, 20)])
        assert changes.is_empty


# ---------------------------------------------------------------------------
# build_tile_slots — which window belongs where, and what it is called
# ---------------------------------------------------------------------------


class TestBuildTileSlots:
    def test_a_window_with_no_hooks_still_gets_its_reserved_slot(self, harness: RecoveryHarness) -> None:
        """The whole feature: a client at character select is on screen, in its own slot."""
        hwnds = _fleet(harness, "a", "b", "c")
        tiles = _tiles(harness)
        assert [(t.slot, t.hwnd) for t in tiles] == [(0, hwnds["a"]), (1, hwnds["b"]), (2, hwnds["c"])]
        assert all(t.player is None for t in tiles)

    def test_a_window_that_is_not_in_game_has_no_player_number(self, harness: RecoveryHarness) -> None:
        """It is not Player anything -- a bot cannot route to it -- so it shows its account."""
        _fleet(harness, "a")
        tiles = _tiles(harness, [{"username": "a", "alias": "Farmer"}])
        assert tiles[0].player is None
        assert tiles[0].label == "Farmer"

    def test_a_hooked_window_is_captioned_by_its_player_index(self, harness: RecoveryHarness) -> None:
        hwnds = _fleet(harness, "a", "b")
        _hook(harness, hwnds["a"], hwnds["b"])
        assert [t.label for t in _tiles(harness)] == ["Player 1", "Player 2"]

    def test_a_slot_held_for_recovery_leaves_a_hole_rather_than_shifting(self, harness: RecoveryHarness) -> None:
        """Compaction of the player list is what used to renumber the survivors of a crash."""
        hwnds = _fleet(harness, "a", "b", "c")
        harness._client_order[1] = None
        del harness._hwnd_account_map[hwnds["b"]]
        harness._handler.clients = _clients(hwnds["a"], hwnds["c"])
        _hook(harness, hwnds["a"], hwnds["c"])
        assert [(t.slot, t.player) for t in _tiles(harness)] == [(0, 0), (2, 1)]

    def test_an_unhooked_window_is_left_off_the_grid(self, harness: RecoveryHarness) -> None:
        hwnds = _fleet(harness, "a", "b")
        harness._unhooked_hwnds = {hwnds["b"]}
        assert [t.hwnd for t in _tiles(harness)] == [hwnds["a"]]

    def test_a_window_whose_hooks_failed_is_left_off_the_grid(self, harness: RecoveryHarness) -> None:
        """It can never become a player and is never retried, so a tile would promise a lie."""
        hwnds = _fleet(harness, "a", "b")
        harness._failed_hook_hwnds = {hwnds["b"]}
        assert [t.hwnd for t in _tiles(harness)] == [hwnds["a"]]

    def test_an_unidentified_window_waits_rather_than_guessing_a_slot(self, harness: RecoveryHarness) -> None:
        """A window that has not logged in yet has no title to match, so it has no slot.

        Placing it anyway means moving its tile once the title arrives, which is exactly
        the shuffle the slot index exists to prevent.
        """
        hwnds = _fleet(harness, "a")
        harness._handler.clients.append(FakeClient(999))
        assert [t.hwnd for t in _tiles(harness)] == [hwnds["a"]]

    def test_a_client_launched_elsewhere_takes_a_trailing_slot(self, harness: RecoveryHarness) -> None:
        """No account, but it is hooked, so it is a real player and needs somewhere to go."""
        hwnds = _fleet(harness, "a", "b")
        harness._handler.clients.append(FakeClient(999))
        _hook(harness, hwnds["a"], hwnds["b"], 999)
        tiles = _tiles(harness)
        assert [(t.slot, t.hwnd) for t in tiles] == [(0, hwnds["a"]), (1, hwnds["b"]), (2, 999)]


# ---------------------------------------------------------------------------
# _sync_tiles end to end
# ---------------------------------------------------------------------------


class TestSyncTiles:
    def test_a_launch_puts_every_window_on_screen_before_any_are_in_game(self, harness: RecoveryHarness) -> None:
        hwnds = _fleet(harness, "a", "b", "c", "d")
        _sync(harness)
        assert harness._game_view.tiles == {0: hwnds["a"], 1: hwnds["b"], 2: hwnds["c"], 3: hwnds["d"]}

    def test_the_caption_follows_the_player_while_the_slot_holds_still(self, harness: RecoveryHarness) -> None:
        """The bug this design exists to kill: B hooks first, then A arrives.

        B is player 0 for a moment and player 1 afterwards, so its caption changes -- but
        its slot never does, so its game window is never re-parented.
        """
        hwnds = _fleet(harness, "a", "b")
        view = harness._game_view

        _hook(harness, hwnds["b"])
        _sync(harness)
        assert view.tiles == {0: hwnds["a"], 1: hwnds["b"]}
        assert view.labels[1] == "Player 1"

        _hook(harness, hwnds["a"], hwnds["b"])
        _sync(harness)
        assert view.tiles == {0: hwnds["a"], 1: hwnds["b"]}
        assert view.labels[1] == "Player 2"
        assert view.rebuilt.count(hwnds["b"]) == 1, "B's window was re-parented when A arrived"

    def test_clients_loading_in_one_at_a_time_never_move_a_tile(self, harness: RecoveryHarness) -> None:
        """Staggered arrival is the normal case: 3-15 s apart, in no particular order."""
        hwnds = _fleet(harness, "a", "b", "c", "d")
        view = harness._game_view
        _sync(harness)
        expected = dict(view.tiles)

        for arrived in (["c"], ["c", "a"], ["c", "a", "d"], ["c", "a", "d", "b"]):
            _hook(harness, *[hwnds[u] for u in arrived])
            _sync(harness)
            assert view.tiles == expected, f"a tile moved after {arrived} loaded in"

        assert sorted(view.rebuilt) == sorted(hwnds.values()), "a window was re-parented"

    def test_a_closed_window_loses_its_tile(self, harness: RecoveryHarness) -> None:
        hwnds = _fleet(harness, "a", "b")
        _sync(harness)
        harness._handler.clients = _clients(hwnds["a"])
        _sync(harness)
        assert harness._game_view.tiles == {0: hwnds["a"]}

    def test_a_player_swap_re_keys_tiles_without_re_embedding(self, harness: RecoveryHarness) -> None:
        hwnds = _fleet(harness, "a", "b")
        _hook(harness, hwnds["a"], hwnds["b"])
        view = harness._game_view
        _sync(harness)
        before = list(view.rebuilt)

        harness._client_order = ["b", "a"]
        harness._clients.reverse()
        _sync(harness)

        assert view.tiles == {0: hwnds["b"], 1: hwnds["a"]}
        assert view.labels == {0: "Player 1", 1: "Player 2"}
        assert view.rebuilt == before, "a swap re-parented a game window"

    def test_a_view_error_does_not_escape_the_client_loop(self, harness: RecoveryHarness) -> None:
        """_sync_tiles runs on every tick; an exception here would kill client detection."""
        _fleet(harness, "a")
        harness._game_view.sync_tiles = MagicMock(side_effect=RuntimeError("window already destroyed"))
        _sync(harness)  # must not raise


# ---------------------------------------------------------------------------
# _place_pending_accounts — a slot for a window that is not a client yet
# ---------------------------------------------------------------------------


class TestPlacePendingAccounts:
    def test_an_account_launched_on_its_own_is_placed_before_it_hooks(self, harness: RecoveryHarness) -> None:
        """An account card states no order, so there is nothing for reserve_slots to pin."""
        harness._hwnd_account_map = {100: "solo"}
        harness._handler.clients = _clients(100)
        harness._place_pending_accounts()
        assert harness._client_order == ["solo"]

    def test_a_reserved_ordering_is_left_exactly_as_the_profile_asked(self, harness: RecoveryHarness) -> None:
        _fleet(harness, "a", "b", "c")
        harness._place_pending_accounts()
        assert harness._client_order == ["a", "b", "c"]

    def test_a_window_still_logging_in_is_not_placed(self, harness: RecoveryHarness) -> None:
        harness._handler.clients = _clients(100)
        harness._place_pending_accounts()
        assert harness._client_order == []

    def test_a_recovering_client_reclaims_the_slot_being_held_for_it(self, harness: RecoveryHarness) -> None:
        """Recovery is meant to be invisible in the ordering, so it must not append."""
        harness._client_order = ["a", None, "c"]
        harness._pending_recovery = {1: "b"}
        harness._hwnd_account_map = {200: "b"}
        harness._handler.clients = _clients(200)
        harness._place_pending_accounts()
        assert harness._client_order == ["a", "b", "c"]


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
