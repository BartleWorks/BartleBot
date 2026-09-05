"""Tests for the in-flight launch guard and the pending tiles it puts on screen (#186).

Three things were wrong before this, and the tests are grouped by them:

  - A click on a slow launch looked like nothing happened, so the natural response was to
    click again -- and a second click really did spawn a second batch, because the two
    launch routes reserved by different paths and neither could see the other.
  - A launch that failed left its reservation behind forever. Nothing takes a slot back,
    so the ordering grew a permanent hole per failed launch.
  - The reserved ordering already named every account a launch was about to bring up, and
    that information was thrown away rather than drawn.

The tiles themselves are QWidgets, so what is exercised here is the reconciler and the
bookkeeping: which tiles ``build_tile_slots`` asks for, and what the guard does with a
second click. ``FakeGameView`` in test_client_slots applies a real ``plan_tile_changes``
result, so the tile assertions are about where things ended up.
"""

from unittest.mock import MagicMock, patch

import pytest

from bartlebot.launch_tracker import LaunchTracker
from bartlebot.tile_slots import TileSlot, build_tile_slots, plan_tile_changes
from tests.test_client_slots import FakeClient, FakeGameView
from tests.test_recovery import RecoveryHarness


@pytest.fixture
def harness() -> RecoveryHarness:
    h = RecoveryHarness()
    h._game_view = FakeGameView()
    return h


def _sync(harness: RecoveryHarness) -> None:
    with patch("bartlebot.client_manager.SettingsManager") as sm:
        sm.instance.return_value = MagicMock(accounts=[])
        harness._sync_tiles()


def _begin(harness: RecoveryHarness, usernames: list[str]) -> list[str]:
    with patch("bartlebot.client_manager.SettingsManager") as sm:
        sm.instance.return_value = MagicMock(accounts=[])
        return harness.begin_launch(usernames)


def _end(harness: RecoveryHarness, usernames: list[str]) -> None:
    with patch("bartlebot.client_manager.SettingsManager") as sm:
        sm.instance.return_value = MagicMock(accounts=[])
        harness.end_launch(usernames)


def _window_arrives(harness: RecoveryHarness, username: str, hwnd: int) -> None:
    """Give *username* a titled, embeddable window, the way the launcher's login does."""
    harness._hwnd_account_map[hwnd] = username
    harness._handler.clients = [FakeClient(h) for h in harness._hwnd_account_map]


# ---------------------------------------------------------------------------
# LaunchTracker -- the in-flight set
# ---------------------------------------------------------------------------


class TestLaunchTracker:
    def test_an_account_not_yet_launching_is_accepted(self) -> None:
        tracker = LaunchTracker()
        assert tracker.begin(["alpha", "beta"]) == ["alpha", "beta"]
        assert "alpha" in tracker

    def test_an_account_already_launching_is_rejected(self) -> None:
        tracker = LaunchTracker()
        tracker.begin(["alpha"])
        assert tracker.begin(["alpha"]) == []

    def test_a_partly_busy_batch_launches_the_rest_of_itself(self) -> None:
        """The reason begin returns a subset rather than a bool.

        A profile of four with one account already up should bring up the other three;
        refusing the whole profile would make the guard worse than the double-launch.
        """
        tracker = LaunchTracker()
        tracker.begin(["beta"])
        assert tracker.begin(["alpha", "beta", "gamma"]) == ["alpha", "gamma"]

    def test_ending_a_launch_makes_the_account_launchable_again(self) -> None:
        tracker = LaunchTracker()
        tracker.begin(["alpha"])
        tracker.end(["alpha"])
        assert tracker.begin(["alpha"]) == ["alpha"]

    def test_ending_an_account_that_was_never_launching_is_not_an_error(self) -> None:
        """end runs from a finally, which can be reached without begin having accepted."""
        LaunchTracker().end(["alpha"])


# ---------------------------------------------------------------------------
# begin_launch / end_launch -- the guard, and the slot it hands back
# ---------------------------------------------------------------------------


class TestLaunchGuard:
    def test_a_launch_claims_its_slots_in_the_order_asked_for(self, harness) -> None:
        assert _begin(harness, ["alpha", "beta"]) == ["alpha", "beta"]
        assert harness._client_order == ["alpha", "beta"]

    def test_a_second_click_during_a_launch_is_rejected(self, harness) -> None:
        """The bug: nothing rejected a re-entrant launch, so a second click spawned again."""
        _begin(harness, ["alpha"])
        assert _begin(harness, ["alpha"]) == []
        assert harness._client_order == ["alpha"], "a rejected launch still moved the ordering"

    def test_a_launch_that_produced_no_window_gives_its_slot_back(self, harness) -> None:
        """Without this the ordering keeps a hole per failed launch for the whole session."""
        _begin(harness, ["alpha", "beta"])
        _end(harness, ["alpha", "beta"])
        assert harness._client_order == []

    def test_a_launch_that_reached_a_window_keeps_its_slot(self, harness) -> None:
        """A slot with a real window in it is not the launcher's to give back.

        The drive that follows login can still fail, and the window stays either way --
        moving it then would be exactly the tile shuffle the reserved ordering prevents.
        """
        _begin(harness, ["alpha", "beta"])
        _window_arrives(harness, "alpha", 100)
        _end(harness, ["alpha", "beta"])
        assert harness._client_order == ["alpha"]

    def test_a_released_slot_does_not_leave_the_ordering_longer_than_the_fleet(self, harness) -> None:
        """Trailing holes are what _assign_slot hands out from -- see its docstring."""
        _begin(harness, ["alpha", "beta", "gamma"])
        _window_arrives(harness, "alpha", 100)
        _end(harness, ["beta", "gamma"])
        assert harness._client_order == ["alpha"]

    def test_a_failed_launch_can_be_retried(self, harness) -> None:
        _begin(harness, ["alpha"])
        _end(harness, ["alpha"])
        assert _begin(harness, ["alpha"]) == ["alpha"]

    def test_an_interior_hole_is_kept_so_the_slots_after_it_do_not_shift(self, harness) -> None:
        """Releasing a middle slot must not renumber the accounts that came up beside it."""
        _begin(harness, ["alpha", "beta", "gamma"])
        _window_arrives(harness, "alpha", 100)
        _window_arrives(harness, "gamma", 120)
        _end(harness, ["alpha", "beta", "gamma"])
        assert harness._client_order == ["alpha", None, "gamma"]


# ---------------------------------------------------------------------------
# build_tile_slots -- which accounts get a pending tile
# ---------------------------------------------------------------------------


def _build(client_order, hwnd_accounts, live, clients=(), launching=()):
    return build_tile_slots(
        client_order=client_order,
        hwnd_accounts=hwnd_accounts,
        embeddable_hwnds=live,
        client_hwnds=list(clients),
        account_label=lambda u: u.title(),
        launching=launching,
    )


class TestPendingTiles:
    def test_a_launching_account_with_no_window_gets_a_pending_tile(self) -> None:
        tiles = _build(["alpha"], {}, [], launching=["alpha"])
        assert [(t.slot, t.hwnd, t.username) for t in tiles] == [(0, None, "alpha")]
        assert tiles[0].is_pending

    def test_a_pending_tile_is_captioned_by_its_account(self) -> None:
        """It has no player index and cannot have one -- there is no window, let alone a
        hooked client -- so the account name is the only honest caption."""
        (tile,) = _build(["alpha"], {}, [], launching=["alpha"])
        assert tile.label == "Alpha"
        assert tile.player is None

    def test_a_reserved_account_with_no_launch_running_gets_nothing(self) -> None:
        """The whole reason the pending set is not read out of the ordering.

        A reservation outlives the launch that made it, so drawing every reserved-but-
        absent account would put a permanent tile on screen for each one that failed.
        """
        assert _build(["alpha"], {}, [], launching=[]) == []

    def test_a_window_appearing_replaces_the_pending_tile_rather_than_adding_one(self) -> None:
        """The launch is still running at this point -- the entry stays open through the
        drive -- so the account is both launching and windowed, and must be drawn once."""
        tiles = _build(["alpha"], {100: "alpha"}, [100], launching=["alpha"])
        assert [(t.slot, t.hwnd) for t in tiles] == [(0, 100)]

    def test_a_batch_launch_draws_one_tile_per_account_in_profile_order(self) -> None:
        tiles = _build(["alpha", "beta", "gamma"], {}, [], launching=["alpha", "beta", "gamma"])
        assert [t.slot for t in tiles] == [0, 1, 2]
        assert [t.username for t in tiles] == ["alpha", "beta", "gamma"]

    def test_pending_and_live_tiles_share_the_grid_in_slot_order(self) -> None:
        """Mid-launch: one window is up, the rest are still coming."""
        tiles = _build(
            ["alpha", "beta", "gamma"],
            {110: "beta"},
            [110],
            clients=[110],
            launching=["alpha", "beta", "gamma"],
        )
        assert [(t.slot, t.hwnd, t.label) for t in tiles] == [
            (0, None, "Alpha"),
            (1, 110, "Player 1"),
            (2, None, "Gamma"),
        ]


# ---------------------------------------------------------------------------
# plan_tile_changes -- pending tiles are identified by account, not by hwnd
# ---------------------------------------------------------------------------


class TestPendingTileIdentity:
    def test_a_batch_of_pending_tiles_stays_a_batch(self) -> None:
        """The reason TileSlot.key exists.

        Matching on the bare hwnd collapsed every pending tile in a batch into one, because
        they all have the same hwnd: None.
        """
        wanted = [
            TileSlot(slot=0, hwnd=None, label="Alpha", player=None, username="alpha"),
            TileSlot(slot=1, hwnd=None, label="Beta", player=None, username="beta"),
        ]
        changes = plan_tile_changes({}, wanted)
        assert [t.username for t in changes.added] == ["alpha", "beta"]

    def test_a_pending_tile_already_on_screen_is_not_rebuilt(self) -> None:
        current = {0: ("pending", "alpha")}
        wanted = [TileSlot(slot=0, hwnd=None, label="Alpha", player=None, username="alpha")]
        assert plan_tile_changes(current, wanted).is_empty

    def test_a_window_arriving_replaces_the_pending_tile(self) -> None:
        """A remove and an add, deliberately, not a move: they are different widget kinds,
        and the pending one has no game window that re-parenting could flash."""
        current = {0: ("pending", "alpha")}
        wanted = [TileSlot(slot=0, hwnd=100, label="Player 1", player=0, username="alpha")]
        changes = plan_tile_changes(current, wanted)
        assert changes.removed == (0,)
        assert [t.hwnd for t in changes.added] == [100]
        assert changes.moved == ()

    def test_a_pending_tile_changing_slot_moves_rather_than_rebuilding(self) -> None:
        current = {0: ("pending", "alpha")}
        wanted = [TileSlot(slot=2, hwnd=None, label="Alpha", player=None, username="alpha")]
        changes = plan_tile_changes(current, wanted)
        assert changes.moved == ((0, 2),)
        assert changes.added == ()


# ---------------------------------------------------------------------------
# End to end through ClientManager and the fake view
# ---------------------------------------------------------------------------


class TestLaunchOnScreen:
    def test_clicking_launch_puts_tiles_up_before_any_window_exists(self, harness) -> None:
        """The whole point: the click has to show something, or it reads as a no-op."""
        _begin(harness, ["alpha", "beta"])
        assert harness._game_view.pending == {0: "alpha", 1: "beta"}
        assert harness._game_view.tiles == {}

    def test_a_window_takes_over_its_own_pending_tile_and_no_other(self, harness) -> None:
        _begin(harness, ["alpha", "beta"])
        _window_arrives(harness, "beta", 110)
        _sync(harness)
        assert harness._game_view.tiles == {1: 110}
        assert harness._game_view.pending == {0: "alpha"}

    def test_a_failed_launch_takes_its_tile_off_screen(self, harness) -> None:
        """A pending tile that nothing clears spins for the rest of the session."""
        _begin(harness, ["alpha"])
        assert harness._game_view.pending == {0: "alpha"}
        _end(harness, ["alpha"])
        assert harness._game_view.pending == {}


# ---------------------------------------------------------------------------
# The real GameView, against a QApplication
#
# Only the parts that need one: whether a pending tile is built, laid out and torn down.
# Everything above runs against the reconciler, which is where the rules live -- but the
# three dicts of tiles are consulted together in four places, and a pending tile missing
# from any one of them is a tile drawn on top of another.
# ---------------------------------------------------------------------------


def _pending(slot: int, username: str) -> TileSlot:
    return TileSlot(slot=slot, hwnd=None, label=username.title(), player=None, username=username)


class TestGameViewPendingTiles:
    def test_a_pending_tile_is_built_and_shows_instead_of_the_launch_screen(self, qapp) -> None:
        from bartlebot.gui.game_embed.game_view import GameView

        view = GameView()
        view.resize(800, 600)
        view.sync_tiles([_pending(0, "alpha"), _pending(1, "beta")])

        assert view.client_count == 2
        assert view.pending_slots == {0: "alpha", 1: "beta"}
        # Sharing the grid means having a share of it: a tile left out of the layout sits
        # at its default geometry, on top of the tile in slot 0.
        assert view.tile_geometry(0) != view.tile_geometry(1)
        view.deleteLater()

    def test_a_launch_that_ends_with_no_window_leaves_nothing_behind(self, qapp) -> None:
        from bartlebot.gui.game_embed.game_view import GameView

        view = GameView()
        view.resize(800, 600)
        view.sync_tiles([_pending(0, "alpha")])
        view.sync_tiles([])

        assert view.pending_slots == {}
        assert view.client_count == 0
        assert view.tile_geometry(0) is None
        assert view.minimumSize().width() == 0
        view.deleteLater()
