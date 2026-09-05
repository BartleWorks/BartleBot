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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bartlebot.gui.game_embed.client_tile import launch_status_text
from bartlebot.launch_tracker import LaunchStage, LaunchTracker
from bartlebot.nodes.timing import LAUNCH_STAGE_ELAPSED_HINT
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


# ---------------------------------------------------------------------------
# Stage captions, and the elapsed-time hint
#
# The stages are coarse because the launcher is coarse -- a shared login-screen wait for
# the whole batch, then one drive call per client that reports nothing until it returns.
# What separates a stalled launch from a healthy one is therefore not a finer stage name
# but how long the current stage has run, which is what launch_status_text adds.
# ---------------------------------------------------------------------------


def _tiles_now(harness) -> list[TileSlot]:
    with patch("bartlebot.client_manager.SettingsManager") as sm:
        sm.instance.return_value = MagicMock(accounts=[])
        return harness.tile_slots()


class TestLaunchStages:
    def test_a_launch_starts_in_the_first_stage(self) -> None:
        tracker = LaunchTracker()
        tracker.begin(["alpha"])
        assert tracker.attempt("alpha").stage is LaunchStage.STARTING

    def test_moving_to_a_new_stage_restarts_the_stage_clock(self) -> None:
        """Elapsed is per stage, not per launch.

        A total that keeps climbing across a normal stage change does not answer the
        question a stalled launch raises, which is "stuck doing what, and for how long".
        """
        tracker = LaunchTracker()
        tracker.begin(["alpha"])
        attempt = tracker.attempt("alpha")
        attempt.stage_since -= 60.0

        assert tracker.set_stage("alpha", LaunchStage.LOGGING_IN) is True
        assert attempt.elapsed() < 1.0

    def test_repeating_a_stage_changes_nothing(self) -> None:
        """The return value is what keeps a repeated report off the redraw path."""
        tracker = LaunchTracker()
        tracker.begin(["alpha"])
        assert tracker.set_stage("alpha", LaunchStage.LOADING) is True
        assert tracker.set_stage("alpha", LaunchStage.LOADING) is False

    def test_a_report_for_an_account_no_longer_launching_is_dropped(self) -> None:
        """Reports come from the launcher, which does not know the caller's finally has run.

        A late one must not put a launch nobody is waiting on back on screen.
        """
        tracker = LaunchTracker()
        tracker.begin(["alpha"])
        tracker.end(["alpha"])
        assert tracker.set_stage("alpha", LaunchStage.LOADING) is False
        assert tracker.attempt("alpha") is None

    def test_the_stage_reaches_the_tile(self, harness) -> None:
        _begin(harness, ["alpha"])
        harness.report_launch_stage("alpha", LaunchStage.LOGGING_IN)
        (tile,) = _tiles_now(harness)
        assert tile.status == "Logging in"

    def test_a_tile_with_no_report_yet_still_says_something(self) -> None:
        """A bare collection of usernames carries no stages, and the tile must survive it."""
        (tile,) = _build(["alpha"], {}, [], launching=["alpha"])
        assert tile.status is None
        assert launch_status_text(tile.status, tile.status_since) == "Launching…"


class TestLaunchStatusText:
    def test_a_young_stage_shows_no_number(self) -> None:
        text = launch_status_text("Loading", 100.0, now=100.0 + LAUNCH_STAGE_ELAPSED_HINT - 1)
        assert text == "Loading…"

    def test_a_stage_that_has_run_long_shows_how_long(self) -> None:
        """The one thing that separates a stalled launch from a healthy one on screen."""
        assert launch_status_text("Logging in", 100.0, now=145.0) == "Logging in… 45s"

    def test_the_number_appears_once_the_stage_has_run_the_threshold(self) -> None:
        text = launch_status_text("Loading", 0.0, now=LAUNCH_STAGE_ELAPSED_HINT)
        assert text == f"Loading… {int(LAUNCH_STAGE_ELAPSED_HINT)}s"

    def test_a_stage_with_no_clock_never_shows_a_number(self) -> None:
        assert launch_status_text("Starting", None, now=9999.0) == "Starting…"


class TestLauncherReportsStages:
    async def test_a_single_account_launch_reports_each_stage_in_order(self) -> None:
        from bartlebot.launcher.launch_profile import launch_account

        seen: list[tuple[str, LaunchStage]] = []
        with (
            patch("bartlebot.launcher.launch_profile.patch_open_browser"),
            patch("bartlebot.launcher.launch_profile._patch_window_config"),
            patch("bartlebot.launcher.launch_profile.get_all_wizard_handles", return_value=[]),
            patch("bartlebot.launcher.launch_profile._start_and_claim_handle", AsyncMock(return_value=1)),
            patch("bartlebot.launcher.launch_profile._wait_for_login_screen", AsyncMock()),
            patch("bartlebot.launcher.launch_profile.instance_login"),
            patch("bartlebot.launcher.launch_profile.set_window_title"),
            patch("bartlebot.launcher.launch_profile.SettingsManager") as sm,
        ):
            sm.instance.return_value = MagicMock(game_resolution=(800, 600))
            await launch_account("alpha", "pw", "Alpha", progress=lambda u, s: seen.append((u, s)))

        assert seen == [
            ("alpha", LaunchStage.STARTING),
            ("alpha", LaunchStage.LOGGING_IN),
            ("alpha", LaunchStage.LOADING),
        ]

    async def test_a_batch_holds_every_account_at_starting_until_the_shared_wait_ends(self) -> None:
        """Not a reporting gap to paper over.

        The login screens really are waited for in one gather, so no account has diverged
        yet -- a per-client stage invented for that stretch would claim progress that has
        not been made.
        """
        from bartlebot.launcher.launch_profile import launch_accounts

        seen: list[tuple[str, LaunchStage]] = []

        async def _wait(_known, _n):
            assert {stage for _u, stage in seen} == {LaunchStage.STARTING}
            return [1, 2]

        with (
            patch("bartlebot.launcher.launch_profile.patch_open_browser"),
            patch("bartlebot.launcher.launch_profile._patch_window_config"),
            patch("bartlebot.launcher.launch_profile.get_all_wizard_handles", return_value=[]),
            patch("bartlebot.launcher.launch_profile._wait_for_n_new_handles", _wait),
            patch("bartlebot.launcher.launch_profile._wait_for_login_screen", AsyncMock()),
            patch("bartlebot.launcher.launch_profile.start_instance"),
            patch("bartlebot.launcher.launch_profile.instance_login"),
            patch("bartlebot.launcher.launch_profile.set_window_title"),
            patch("bartlebot.launcher.launch_profile.jitter", return_value=0.0),
            patch("bartlebot.launcher.launch_profile.SettingsManager") as sm,
        ):
            sm.instance.return_value = MagicMock(game_resolution=(800, 600))
            await launch_accounts(
                [("alpha", "pw", "Alpha"), ("beta", "pw", "Beta")],
                progress=lambda u, s: seen.append((u, s)),
            )

        assert seen[:2] == [("alpha", LaunchStage.STARTING), ("beta", LaunchStage.STARTING)]
        assert seen[-1] == ("beta", LaunchStage.LOADING)

    async def test_a_progress_callback_that_raises_does_not_strand_the_launch(self) -> None:
        """The callback lands in GUI code, and the window is already spawned by then.

        An exception escaping it would leave a real client with nobody logging into it.
        """
        from bartlebot.launcher.launch_profile import launch_account

        def _boom(_username, _stage):
            raise RuntimeError("caption failed to draw")

        with (
            patch("bartlebot.launcher.launch_profile.patch_open_browser"),
            patch("bartlebot.launcher.launch_profile._patch_window_config"),
            patch("bartlebot.launcher.launch_profile.get_all_wizard_handles", return_value=[]),
            patch("bartlebot.launcher.launch_profile._start_and_claim_handle", AsyncMock(return_value=1)),
            patch("bartlebot.launcher.launch_profile._wait_for_login_screen", AsyncMock()),
            patch("bartlebot.launcher.launch_profile.instance_login") as login,
            patch("bartlebot.launcher.launch_profile.set_window_title"),
            patch("bartlebot.launcher.launch_profile.SettingsManager") as sm,
        ):
            sm.instance.return_value = MagicMock(game_resolution=(800, 600))
            assert await launch_account("alpha", "pw", "Alpha", progress=_boom) == "[Alpha] Wizard101"

        login.assert_called_once()


class TestPendingTileCaption:
    def test_a_stage_change_redraws_a_tile_that_did_not_move(self, qapp) -> None:
        """The reconciler reports nothing here -- the only thing that changed is the words."""
        from bartlebot.gui.game_embed.game_view import GameView

        view = GameView()
        view.resize(800, 600)
        view.sync_tiles([_pending(0, "alpha")])
        view.sync_tiles([TileSlot(slot=0, hwnd=None, label="Alpha", player=None, username="alpha", status="Loading")])

        assert view.pending_status(0) == "Loading…"
        view.deleteLater()


# ---------------------------------------------------------------------------
# Failure, and dismissing it
#
# The failures that matter here are the quiet ones. A launch that raises already gets a
# message box; the auto-select drive returning False and a window that never reaches the
# login screen are timeouts, and until #186 nothing read either -- so the launch simply
# looked like it had finished, and the tile spun until the app was restarted.
# ---------------------------------------------------------------------------


class TestFailedAttempts:
    def test_a_failed_attempt_survives_the_end_of_its_batch(self) -> None:
        """The finally that ends a batch runs in the same breath as the failure that set it.

        Closing it there would take the explanation off screen in the tick it appeared,
        and a launch that gave up quietly has nothing else to say it.
        """
        tracker = LaunchTracker()
        tracker.begin(["alpha", "beta"])
        tracker.fail("alpha")
        tracker.end(["alpha", "beta"])

        assert tracker.failed() == ["alpha"]
        assert "beta" not in tracker

    def test_ending_every_attempt_still_keeps_the_failed_ones(self) -> None:
        tracker = LaunchTracker()
        tracker.begin(["alpha", "beta"])
        tracker.fail("alpha")
        tracker.end()
        assert tracker.failed() == ["alpha"]

    def test_dismissing_closes_an_attempt_end_would_not(self) -> None:
        tracker = LaunchTracker()
        tracker.begin(["alpha"])
        tracker.fail("alpha")
        assert tracker.dismiss("alpha") is True
        assert "alpha" not in tracker

    def test_a_failed_account_is_still_guarded_until_it_is_dismissed(self) -> None:
        """What makes the dismissal meaningful: the slot is still spoken for."""
        tracker = LaunchTracker()
        tracker.begin(["alpha"])
        tracker.fail("alpha")
        assert tracker.begin(["alpha"]) == []


class TestFailureOnScreen:
    def test_a_failed_launch_keeps_its_tile_after_the_batch_ends(self, harness) -> None:
        _begin(harness, ["alpha"])
        harness.report_launch_failed("alpha")
        _end(harness, ["alpha"])

        assert harness._game_view.pending == {0: "alpha"}
        (tile,) = _tiles_now(harness)
        assert tile.failed
        assert tile.status == "Launch failed"

    def test_a_failed_launch_holds_its_slot_until_dismissed(self, harness) -> None:
        """Releasing it early would move the tile out from under the explanation."""
        _begin(harness, ["alpha"])
        harness.report_launch_failed("alpha")
        _end(harness, ["alpha"])
        assert harness._client_order == ["alpha"]

        harness.dismiss_launch("alpha")
        assert harness._client_order == []
        assert harness._game_view.pending == {}

    def test_a_dismissed_account_can_be_launched_again(self, harness) -> None:
        _begin(harness, ["alpha"])
        harness.report_launch_failed("alpha")
        _end(harness, ["alpha"])
        assert _begin(harness, ["alpha"]) == [], "a failed launch stopped guarding its account"

        harness.dismiss_launch("alpha")
        assert _begin(harness, ["alpha"]) == ["alpha"]

    def test_a_drive_that_failed_after_the_window_came_up_is_not_left_undismissable(self, harness) -> None:
        """That account's slot holds a real client tile, so there is nowhere to draw the
        failure and nothing for the user to click -- it has to close itself."""
        _begin(harness, ["alpha"])
        _window_arrives(harness, "alpha", 100)
        harness.report_launch_failed("alpha")
        _end(harness, ["alpha"])

        assert harness.launching == []
        assert harness._client_order == ["alpha"]

    def test_dismissing_something_that_is_not_there_does_nothing(self, harness) -> None:
        harness.dismiss_launch("alpha")
        assert harness._client_order == []


class TestLauncherReportsFailure:
    async def test_a_drive_that_gives_up_is_reported(self) -> None:
        """The quiet failure: auto_select returns False on a timeout rather than raising."""
        from bartlebot.launcher.launch_profile import launch_account

        seen: list[tuple[str, LaunchStage]] = []
        with (
            patch("bartlebot.launcher.launch_profile.patch_open_browser"),
            patch("bartlebot.launcher.launch_profile._patch_window_config"),
            patch("bartlebot.launcher.launch_profile.get_all_wizard_handles", return_value=[]),
            patch("bartlebot.launcher.launch_profile._start_and_claim_handle", AsyncMock(return_value=1)),
            patch("bartlebot.launcher.launch_profile._wait_for_login_screen", AsyncMock(return_value=True)),
            patch("bartlebot.launcher.launch_profile.instance_login"),
            patch("bartlebot.launcher.launch_profile.set_window_title"),
            patch("bartlebot.launcher.launch_profile.SettingsManager") as sm,
        ):
            sm.instance.return_value = MagicMock(game_resolution=(800, 600))
            await launch_account(
                "alpha",
                "pw",
                "Alpha",
                drive=AsyncMock(return_value=False),
                progress=lambda u, s: seen.append((u, s)),
            )

        assert seen[-1] == ("alpha", LaunchStage.FAILED)

    async def test_a_window_that_never_reaches_the_login_screen_fails_alone(self) -> None:
        """One window failing to come up is not a reason to abandon the ones that did --
        and it must not be typed into at whatever screen it is actually showing."""
        from bartlebot.launcher.launch_profile import launch_accounts

        seen: list[tuple[str, LaunchStage]] = []
        logins: list[str] = []

        async def _login_screen(handle, *_args, **_kwargs):
            return handle != 1

        with (
            patch("bartlebot.launcher.launch_profile.patch_open_browser"),
            patch("bartlebot.launcher.launch_profile._patch_window_config"),
            patch("bartlebot.launcher.launch_profile.get_all_wizard_handles", return_value=[]),
            patch("bartlebot.launcher.launch_profile._wait_for_n_new_handles", AsyncMock(return_value=[1, 2])),
            patch("bartlebot.launcher.launch_profile._wait_for_login_screen", _login_screen),
            patch("bartlebot.launcher.launch_profile.start_instance"),
            patch(
                "bartlebot.launcher.launch_profile.instance_login",
                lambda _h, username, _pw: logins.append(username),
            ),
            patch("bartlebot.launcher.launch_profile.set_window_title"),
            patch("bartlebot.launcher.launch_profile.jitter", return_value=0.0),
            patch("bartlebot.launcher.launch_profile.SettingsManager") as sm,
        ):
            sm.instance.return_value = MagicMock(game_resolution=(800, 600))
            titles = await launch_accounts(
                [("alpha", "pw", "Alpha"), ("beta", "pw", "Beta")],
                progress=lambda u, s: seen.append((u, s)),
            )

        assert logins == ["beta"], "typed credentials into a window that was not at the login screen"
        assert titles == ["[Beta] Wizard101"]
        assert ("alpha", LaunchStage.FAILED) in seen
        assert ("beta", LaunchStage.FAILED) not in seen

    async def test_one_drive_raising_does_not_swallow_the_others_result(self) -> None:
        """Every account in the batch has a window by now and is owed an answer about it."""
        from bartlebot.launcher.launch_profile import launch_accounts

        seen: list[tuple[str, LaunchStage]] = []

        async def _drive(title):
            if title == "[Alpha] Wizard101":
                raise RuntimeError("hook handler died")
            return False

        with (
            patch("bartlebot.launcher.launch_profile.patch_open_browser"),
            patch("bartlebot.launcher.launch_profile._patch_window_config"),
            patch("bartlebot.launcher.launch_profile.get_all_wizard_handles", return_value=[]),
            patch("bartlebot.launcher.launch_profile._wait_for_n_new_handles", AsyncMock(return_value=[1, 2])),
            patch("bartlebot.launcher.launch_profile._wait_for_login_screen", AsyncMock(return_value=True)),
            patch("bartlebot.launcher.launch_profile.start_instance"),
            patch("bartlebot.launcher.launch_profile.instance_login"),
            patch("bartlebot.launcher.launch_profile.set_window_title"),
            patch("bartlebot.launcher.launch_profile.jitter", return_value=0.0),
            patch("bartlebot.launcher.launch_profile.SettingsManager") as sm,
        ):
            sm.instance.return_value = MagicMock(game_resolution=(800, 600))
            await launch_accounts(
                [("alpha", "pw", "Alpha"), ("beta", "pw", "Beta")],
                drive=_drive,
                progress=lambda u, s: seen.append((u, s)),
            )

        assert ("alpha", LaunchStage.FAILED) in seen
        assert ("beta", LaunchStage.FAILED) in seen

    async def test_a_login_screen_that_never_arrives_gives_up(self) -> None:
        """The wait was unbounded, so this window would have held its launch open forever."""
        from bartlebot.launcher.launch_profile import _wait_for_login_screen

        with patch("bartlebot.launcher.launch_profile.get_window_title", return_value="Wizard101 Patcher"):
            assert await _wait_for_login_screen(1, timeout=0.05) is False

    async def test_windows_that_never_spawn_fail_the_whole_batch(self) -> None:
        """Not attributable to an account — the handles have not been paired with anybody —
        and it means the windows were never started at all."""
        from bartlebot.launcher.launch_profile import _wait_for_n_new_handles

        with patch("bartlebot.launcher.launch_profile.get_all_wizard_handles", return_value=[]):
            with pytest.raises(TimeoutError):
                await _wait_for_n_new_handles(set(), 2, timeout=0.05)


class TestFailedTileRendering:
    def test_a_failed_caption_drops_the_ellipsis_and_the_clock(self) -> None:
        """It is not still doing the thing, and how long it has been finished is not news."""
        assert launch_status_text("Launch failed", 0.0, now=9999.0, failed=True) == "Launch failed"

    def test_a_failed_tile_stops_spinning_and_offers_a_way_out(self, qapp) -> None:
        from bartlebot.gui.game_embed.client_tile import PendingTile

        tile = PendingTile("Alpha")
        assert tile._spin_timer.isActive()

        tile.set_status("Launch failed", None, failed=True)
        assert tile.failed
        assert not tile._spin_timer.isActive(), "a stopped launch kept repainting itself"
        assert tile.status_text == "Launch failed"
        tile.deleteLater()

    def test_a_failed_tile_asks_the_view_to_dismiss_its_account(self, qapp) -> None:
        from bartlebot.gui.game_embed.game_view import GameView

        view = GameView()
        view.resize(800, 600)
        asked: list[str] = []
        view.dismiss_launch_requested.connect(asked.append)
        view.sync_tiles(
            [
                TileSlot(
                    slot=0,
                    hwnd=None,
                    label="Alpha",
                    player=None,
                    username="alpha",
                    status="Launch failed",
                    failed=True,
                )
            ]
        )

        assert view.pending_failed(0) is True
        view._pending_tiles[0].dismiss_requested.emit()
        assert asked == ["alpha"]
        view.deleteLater()
