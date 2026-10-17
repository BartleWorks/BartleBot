"""Tests for ClientManager.relog_clients — the scheduled relog cycle.

A relog is a healthy run being broken on purpose, so almost every test here is about a
reason *not* to do it. The one that matters most is the recovery pre-check: unlike a crash,
this is a failure we introduce, and ``_reconnect_client`` stops the bot when it cannot log a
client back in.

Real ClientManager methods are bound to a minimal harness, the same trick
``tests/clients/test_recovery.py`` uses — no QApplication, no WizSprinter, no game process.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from bartlebot.bot.machine import PauseHold
from bartlebot.clients.client_manager import ClientManager


async def _until(predicate, timeout: float = 2.0) -> None:
    """Wait for *predicate* to hold, rather than for a fixed slice of wall clock.

    The watch loop is compressed to a 10 ms cadence here, so "sleep 50 ms and
    expect it to have ticked five times" reads as generous -- right up until a
    two-core CI runner under ``-n auto`` delivers exactly one. That is the
    ceiling-versus-cadence rule in CLAUDE.md: the poll interval can be
    compressed as far as the timer tick allows, but the window the test races
    against has to stay an order of magnitude wider than the polls nominally
    cost, or the test stops being about the loop and starts being about the
    runner.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate() and loop.time() < deadline:
        await asyncio.sleep(0.005)


class FakeClient:
    def __init__(self, hwnd: int, pid: int) -> None:
        self.window_handle = hwnd
        self.process_id = pid


class RelogHarness:
    """Hosts the state relog_clients touches, with the real methods bound to it."""

    relog_clients = ClientManager.relog_clients
    _clients_near_gdi_quota = ClientManager._clients_near_gdi_quota
    _on_clients_removed = ClientManager._on_clients_removed
    _log_unrecoverable_removal = ClientManager._log_unrecoverable_removal
    # The cycle hands off to recovery here; recovery itself is covered by test_recovery.py.
    # Sync rather than async so nothing is left as an un-awaited coroutine when create_task
    # is patched out.
    _run_recovery_queue = MagicMock()

    def __init__(self) -> None:
        self._clients = [FakeClient(101, 1001), FakeClient(102, 1002)]
        self._machine = _make_machine()
        self._relog_in_progress = False
        self._recovery_in_progress = False
        self._pending_recovery: dict[int, str] = {}
        self._relogging_hwnds: set[int] = set()
        self._hwnd_account_map: dict[int, str] = {101: "acct_a", 102: "acct_b"}
        self._hwnd_titles: dict[int, str] = {}
        self._hwnd_user_ids: dict[int, int] = {}
        self._client_order: list[str | None] = ["acct_a", "acct_b"]
        self._vacated_slots: dict[str, int] = {}
        self._recovery_task = None
        self._gaps: list[tuple[int, str]] = []

    def recovery_gaps(self):
        return self._gaps


def _make_machine(*, parked: bool = True, ambient: bool = False) -> MagicMock:
    machine = MagicMock()
    machine.is_stopped = False
    machine.is_paused = False
    machine.is_ambient_combat_active = ambient
    machine.wait_until_parked = AsyncMock(return_value=parked)
    return machine


@pytest.fixture
def harness() -> RelogHarness:
    return RelogHarness()


def _closes_cleanly():
    """Patch force_close_client to succeed, recording what it was asked to close."""
    return patch("bartlebot.clients.client_manager.force_close_client", AsyncMock(return_value=True))


class TestRefusals:
    async def test_refuses_when_a_client_could_not_be_recovered(self, harness, caplog):
        harness._gaps = [(2, "no password saved for account 'b***b'")]
        with _closes_cleanly() as close, caplog.at_level(logging.WARNING):
            assert await harness.relog_clients(harness._clients, reason="scheduled") is False
        close.assert_not_awaited()
        harness._machine.pause.assert_not_called()
        assert "could not be brought back" in caplog.text
        assert "no password saved" in caplog.text

    async def test_refuses_while_a_recovery_is_already_running(self, harness):
        harness._recovery_in_progress = True
        with _closes_cleanly() as close:
            assert await harness.relog_clients(harness._clients, reason="scheduled") is False
        close.assert_not_awaited()

    async def test_refuses_while_clients_are_queued_for_recovery(self, harness):
        harness._pending_recovery = {0: "acct_a"}
        with _closes_cleanly() as close:
            assert await harness.relog_clients(harness._clients, reason="scheduled") is False
        close.assert_not_awaited()

    async def test_refuses_when_the_bot_is_not_running(self, harness):
        harness._machine = None
        with _closes_cleanly() as close:
            assert await harness.relog_clients(harness._clients, reason="scheduled") is False
        close.assert_not_awaited()

    async def test_refuses_when_the_machine_has_stopped(self, harness):
        harness._machine.is_stopped = True
        with _closes_cleanly() as close:
            assert await harness.relog_clients(harness._clients, reason="scheduled") is False
        close.assert_not_awaited()

    async def test_no_targets_is_not_a_cycle(self, harness):
        with _closes_cleanly() as close:
            assert await harness.relog_clients([], reason="scheduled") is False
        close.assert_not_awaited()


class TestQuietPoint:
    async def test_waits_for_a_node_boundary_before_closing_anything(self, harness):
        with _closes_cleanly():
            assert await harness.relog_clients(harness._clients, reason="scheduled") is True
        assert harness._machine.pause.call_args_list[0] == call(PauseHold.RELOG)
        harness._machine.wait_until_parked.assert_awaited_once()

    async def test_a_completed_relog_hands_its_hold_to_recovery(self, harness):
        """Recovery resumes the bot once the clients are back; it must not run in between."""
        with _closes_cleanly():
            assert await harness.relog_clients(harness._clients, reason="scheduled") is True
        holds = [c for c in harness._machine.mock_calls if c[0] in ("pause", "resume")]
        assert holds == [call.pause(PauseHold.RELOG), call.pause(PauseHold.RECOVERY), call.resume(PauseHold.RELOG)]

    async def test_a_relog_that_closes_nothing_gives_the_bot_back(self, harness, caplog):
        """Nothing closed means nothing for recovery to resume, which left the bot paused for good."""
        with patch("bartlebot.clients.client_manager.force_close_client", AsyncMock(return_value=False)):
            assert await harness.relog_clients(harness._clients, reason="scheduled") is False
        harness._machine.pause.assert_called_once_with(PauseHold.RELOG)
        harness._machine.resume.assert_called_once_with(PauseHold.RELOG)

    async def test_gives_up_and_resumes_when_the_bot_never_parks(self, harness, caplog):
        harness._machine.wait_until_parked = AsyncMock(return_value=False)
        with _closes_cleanly() as close, caplog.at_level(logging.WARNING):
            assert await harness.relog_clients(harness._clients, reason="scheduled") is False
        close.assert_not_awaited()
        harness._machine.resume.assert_called_once_with(PauseHold.RELOG)  # the run must not be left paused
        assert "did not reach a node boundary" in caplog.text

    async def test_defers_while_ambient_combat_is_being_handled_without_touching_the_pause(self, harness):
        """The ambient watcher paused the bot for its fight; a resume here would run a node beside it (#154)."""
        harness._machine.is_ambient_combat_active = True
        with _closes_cleanly() as close:
            assert await harness.relog_clients(harness._clients, reason="scheduled") is False
        close.assert_not_awaited()
        harness._machine.pause.assert_not_called()
        harness._machine.resume.assert_not_called()

    async def test_leaves_a_paused_bot_paused(self, harness):
        """The user paused it, perhaps to play by hand: the relog must neither close clients nor resume (#154)."""
        harness._machine.is_paused = True
        with _closes_cleanly() as close:
            assert await harness.relog_clients(harness._clients, reason="scheduled") is False
        close.assert_not_awaited()
        harness._machine.resume.assert_not_called()

    async def test_a_fight_that_starts_while_parking_defers_and_resumes_its_own_pause(self, harness, caplog):
        async def _parks_into_a_fight(_timeout):
            harness._machine.is_ambient_combat_active = True
            return True

        harness._machine.wait_until_parked = _parks_into_a_fight
        with _closes_cleanly() as close, caplog.at_level(logging.INFO):
            assert await harness.relog_clients(harness._clients, reason="scheduled") is False
        close.assert_not_awaited()
        harness._machine.resume.assert_called_once_with(PauseHold.RELOG)  # the relog's own pause, given back
        assert "ambient combat" in caplog.text


class TestTheCycle:
    async def test_closes_every_target_with_its_own_pid(self, harness):
        with _closes_cleanly() as close:
            assert await harness.relog_clients(harness._clients, reason="scheduled") is True
        assert sorted(c.args[0] for c in close.await_args_list) == [101, 102]
        assert sorted(c.kwargs["pid"] for c in close.await_args_list) == [1001, 1002]

    async def test_the_targets_are_closed_together(self, harness):
        """Closing in turn spends the process-exit wait per client, and spreads the
        removals across several _client_loop polls -- which is what decides whether they
        come back as one batch or one at a time."""
        in_flight = 0
        peak = 0

        async def _close(hwnd, **_kwargs):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0)
            in_flight -= 1
            return True

        with patch("bartlebot.clients.client_manager.force_close_client", _close):
            assert await harness.relog_clients(harness._clients, reason="scheduled") is True
        assert peak == 2

    async def test_suspends_health_checks_for_the_targets(self, harness):
        with _closes_cleanly():
            await harness.relog_clients(harness._clients, reason="scheduled")
        (hwnds,) = harness._machine.suspend_health_checks_for.call_args.args
        assert list(hwnds) == [101, 102]

    async def test_marks_the_windows_as_intentional_before_they_die(self, harness):
        seen: list[set[int]] = []

        async def _close(hwnd, **_kwargs):
            seen.append(set(harness._relogging_hwnds))
            return True

        with patch("bartlebot.clients.client_manager.force_close_client", _close):
            await harness.relog_clients(harness._clients, reason="scheduled")
        # Both hwnds are marked before the first close, because a window can be reaped by
        # _client_loop the moment it dies.
        assert seen[0] == {101, 102}

    async def test_reports_failure_when_a_client_will_not_close(self, harness, caplog):
        closed: list[int] = []

        async def _close(hwnd, **_kwargs):
            closed.append(hwnd)
            return hwnd == 101  # the second one survives everything

        with patch("bartlebot.clients.client_manager.force_close_client", _close), caplog.at_level(logging.ERROR):
            assert await harness.relog_clients(harness._clients, reason="scheduled") is False
        assert "would not close" in caplog.text
        # The healthy client is still closed: recovery brings back whatever died, and the
        # cycle only has to be honest about the one that did not.
        assert sorted(closed) == [101, 102]

    async def test_a_raising_close_is_a_failure_rather_than_an_escape(self, harness, caplog):
        """One client that cannot be closed must not take the other close with it, nor the
        relog watch loop that called this."""
        with (
            patch("bartlebot.clients.client_manager.force_close_client", AsyncMock(side_effect=OSError("boom"))),
            caplog.at_level(logging.ERROR),
        ):
            assert await harness.relog_clients(harness._clients, reason="scheduled") is False
        assert "raised" in caplog.text
        assert harness._relog_in_progress is False

    async def test_one_cycle_at_a_time(self, harness):
        started = asyncio.Event()
        release = asyncio.Event()

        async def _slow_close(hwnd, **_kwargs):
            started.set()
            await release.wait()
            return True

        with patch("bartlebot.clients.client_manager.force_close_client", _slow_close):
            first = asyncio.create_task(harness.relog_clients(harness._clients, reason="scheduled"))
            await started.wait()
            assert await harness.relog_clients(harness._clients, reason="GDI quota") is False
            release.set()
            assert await first is True


class TestRemovalIsLoggedAsARelog:
    """The relog goes through the disconnect path, and must not be filed as a fault."""

    def test_a_relogged_client_is_reported_as_a_relog(self, harness, caplog):
        harness._relogging_hwnds = {101}
        with patch("asyncio.create_task", MagicMock()), caplog.at_level(logging.DEBUG):
            harness._on_clients_removed({101})
        assert "closed for a relog" in caplog.text
        assert "disconnected" not in caplog.text
        assert harness._pending_recovery == {0: "acct_a"}  # recovery is queued either way

    def test_a_genuine_disconnect_still_warns(self, harness, caplog):
        with patch("asyncio.create_task", MagicMock()), caplog.at_level(logging.DEBUG):
            harness._on_clients_removed({101})
        assert "disconnected — queuing recovery" in caplog.text

    def test_the_mark_is_consumed_so_a_later_crash_reads_as_one(self, harness, caplog):
        harness._relogging_hwnds = {101}
        with patch("asyncio.create_task", MagicMock()):
            harness._on_clients_removed({101})
        assert harness._relogging_hwnds == set()

        # Same client comes back and later dies for real.
        harness._client_order = ["acct_a", "acct_b"]
        harness._pending_recovery.clear()
        with patch("asyncio.create_task", MagicMock()), caplog.at_level(logging.DEBUG):
            caplog.clear()
            harness._on_clients_removed({101})
        assert "disconnected — queuing recovery" in caplog.text


class TestGdiSampling:
    def test_a_healthy_fleet_needs_no_relog(self, harness):
        with patch("bartlebot.clients.client_manager.gdi_object_count", return_value=3_200):
            assert harness._clients_near_gdi_quota() == []

    def test_only_the_starved_client_is_returned(self, harness, caplog):
        counts = {1001: 3_200, 1002: 9_400}
        with patch("bartlebot.clients.client_manager.gdi_object_count", side_effect=lambda pid: counts[pid]):
            with caplog.at_level(logging.WARNING):
                starved = harness._clients_near_gdi_quota()
        assert [c.window_handle for c in starved] == [102]
        assert "9400 GDI objects" in caplog.text.replace(",", "")

    def test_a_failed_read_is_not_a_trigger(self, harness):
        with patch("bartlebot.clients.client_manager.gdi_object_count", return_value=0):
            assert harness._clients_near_gdi_quota() == []


class TestWatchLoop:
    """The loop that owns the clock. Its cadence is compressed on the importing module."""

    @pytest.fixture
    def watching(self, harness, monkeypatch):
        harness._start_relog_watch = ClientManager._start_relog_watch.__get__(harness)
        harness._stop_relog_watch = ClientManager._stop_relog_watch.__get__(harness)
        harness._relog_watch_loop = ClientManager._relog_watch_loop.__get__(harness)
        harness._relog_watch_task = None
        monkeypatch.setattr("bartlebot.clients.client_manager.RELOG_WATCH_INTERVAL", 0.01)
        return harness

    def _settings(self, hours: float):
        settings = MagicMock()
        settings.relog_interval_hours = hours
        return patch("bartlebot.clients.client_manager.SettingsManager.instance", return_value=settings)

    async def test_relogs_once_the_interval_has_elapsed(self, watching):
        watching.relog_clients = AsyncMock(return_value=True)
        # 0.01 hours is 36 s of wall clock, so the clock is what has to move, not the test.
        with (
            self._settings(hours=0.0),
            patch("bartlebot.clients.client_manager.gdi_object_count", return_value=3_200) as gdi,
        ):
            watching._start_relog_watch()
            # Waiting on the ticks themselves: "not relogged" only means something
            # once the loop has run and declined to.
            await _until(lambda: gdi.call_count > 1)
            watching._stop_relog_watch()
        assert gdi.call_count > 1
        watching.relog_clients.assert_not_awaited()  # off means off

    async def test_a_tiny_interval_fires_and_names_the_reason(self, watching):
        watching.relog_clients = AsyncMock(return_value=True)
        with self._settings(hours=1e-9), patch("bartlebot.clients.client_manager.gdi_object_count", return_value=3_200):
            watching._start_relog_watch()
            await _until(lambda: watching.relog_clients.await_args is not None)
            watching._stop_relog_watch()
        assert watching.relog_clients.await_args.kwargs["reason"] == "scheduled"

    async def test_a_starved_client_is_relogged_without_the_timer(self, watching):
        watching.relog_clients = AsyncMock(return_value=True)
        with self._settings(hours=0.0), patch("bartlebot.clients.client_manager.gdi_object_count", return_value=9_999):
            watching._start_relog_watch()
            await _until(lambda: watching.relog_clients.await_args is not None)
            watching._stop_relog_watch()
        assert watching.relog_clients.await_args.kwargs["reason"] == "GDI quota"

    async def test_a_failing_tick_does_not_kill_the_timer(self, watching, caplog):
        calls: list[int] = []

        def _explode(_pid):
            calls.append(1)
            raise OSError("no such process")

        with self._settings(hours=0.0), patch("bartlebot.clients.client_manager.gdi_object_count", _explode):
            with caplog.at_level(logging.ERROR):
                watching._start_relog_watch()
                await _until(lambda: len(calls) > 1)
                watching._stop_relog_watch()
        assert len(calls) > 1  # it kept ticking after the first failure
        assert "Relog watch loop error" in caplog.text

    async def test_stopping_the_watch_cancels_the_task(self, watching):
        with self._settings(hours=0.0), patch("bartlebot.clients.client_manager.gdi_object_count", return_value=3_200):
            watching._start_relog_watch()
            task = watching._relog_watch_task
            watching._stop_relog_watch()
            await asyncio.sleep(0)
        assert task.cancelled() or task.done()
        assert watching._relog_watch_task is None
