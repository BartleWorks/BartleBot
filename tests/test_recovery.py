"""Comprehensive tests for the ClientManager client recovery system.

Tests cover:
  - _on_clients_removed   — disconnect detection, slot marking, recovery task dispatch
  - _run_recovery_queue   — batch processing, error containment, FSM resume
  - _reconnect_clients    — full reconnect pipeline (launch, wait, crowns shop dismiss)
  - _build_ordered_clients — slot-ordered client list assembly

The RecoveryHarness binds real ClientManager methods to a minimal fake object so
tests exercise actual production code paths without needing a QApplication, WizSprinter,
or any Wizard101 process.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bartlebot.clients.client_manager import ClientManager
from bartlebot.clients.launch_tracker import LaunchTracker

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------


class RecoveryHarness:
    """Minimal stub that hosts recovery state and binds ClientManager methods.

    Python's descriptor protocol makes class-level function references behave as
    bound methods when accessed on an instance, so the real ClientManager methods
    run with `self` pointing at this harness object.  Tests that need to replace
    _reconnect_clients with a mock can set an instance attribute directly:
        harness._reconnect_clients = AsyncMock()
    The instance attribute shadows the class-level binding for that test.
    """

    # Bind real methods directly — no copying, no duplication.
    _on_clients_removed = ClientManager._on_clients_removed
    _log_unrecoverable_removal = ClientManager._log_unrecoverable_removal
    _forget_closed_windows = ClientManager._forget_closed_windows
    _run_recovery_queue = ClientManager._run_recovery_queue
    _settle_recovery_batch = ClientManager._settle_recovery_batch
    _reconnect_clients = ClientManager._reconnect_clients
    _recovery_target = ClientManager._recovery_target
    _warn_awaiting_manual_login = ClientManager._warn_awaiting_manual_login
    _alias_for = ClientManager._alias_for
    _build_ordered_clients = ClientManager._build_ordered_clients
    _assign_slot = ClientManager._assign_slot
    _place_account = ClientManager._place_account
    _place_pending_accounts = ClientManager._place_pending_accounts
    _embeddable_hwnds = ClientManager._embeddable_hwnds
    _account_label = ClientManager._account_label
    tile_slots = ClientManager.tile_slots
    begin_launch = ClientManager.begin_launch
    end_launch = ClientManager.end_launch
    report_launch_stage = ClientManager.report_launch_stage
    report_launch_failed = ClientManager.report_launch_failed
    dismiss_launch = ClientManager.dismiss_launch
    launching = ClientManager.launching
    active_accounts = ClientManager.active_accounts
    _release_unarrived = ClientManager._release_unarrived
    reserve_slots = ClientManager.reserve_slots
    _sync_tiles = ClientManager._sync_tiles
    _regroup_after_recovery = ClientManager._regroup_after_recovery
    # _reconnect_clients drives each relaunched window in through these, the same path
    # a normal launch takes.
    drive_client_in = ClientManager.drive_client_in
    await_client_in = ClientManager.await_client_in
    has_client_titled = ClientManager.has_client_titled
    client_titled = ClientManager.client_titled
    pending_client_titled = ClientManager.pending_client_titled

    def __init__(self) -> None:
        self._hwnd_account_map: dict[int, str] = {}
        self._hwnd_titles: dict[int, str] = {}
        self._hwnd_user_ids: dict[int, int] = {}
        self._client_order: list[str | None] = []
        self._launches = LaunchTracker()
        self._pending_recovery: dict[int, str] = {}
        self._vacated_slots: dict[str, int] = {}
        self._recovery_in_progress: bool = False
        self._recovery_task = None
        self._relogging_hwnds: set[int] = set()
        self._relog_in_progress: bool = False
        self._clients: list = []
        self._unhooked_hwnds: set[int] = set()
        self._failed_hook_hwnds: set[int] = set()
        self._game_view = MagicMock()
        # The driver borrows the handler's client to read the character-select screen.
        self._handler = MagicMock(clients=[])
        self._client_resolutions: dict[str, tuple[int, int]] = {}
        self._machine = None
        self._get_recovery_state = lambda: None
        # Simulate pyqtSignal without requiring a QApplication.
        self.recovery_error = MagicMock()


def _make_machine(*, stopped: bool = False) -> MagicMock:
    m = MagicMock()
    m.is_stopped = stopped
    return m


@pytest.fixture
def harness() -> RecoveryHarness:
    return RecoveryHarness()


def _mock_create_task(coro: object) -> MagicMock:
    """Replacement for asyncio.create_task that closes the coroutine instead of scheduling it.

    When tests patch create_task, the coroutine argument is created but never scheduled.
    Python emits a RuntimeWarning for unawaited coroutines; closing it here suppresses that.
    """
    if hasattr(coro, "close"):
        coro.close()
    return MagicMock()


# ---------------------------------------------------------------------------
# _on_clients_removed
# ---------------------------------------------------------------------------


class TestAccountMapTitleCache:
    """_refresh_account_map records every title it reads, matched or not.

    An unmatched title is the only description of the window that will still exist by the
    time the client disconnects — the window is gone by then, so it cannot be read again.
    """

    def _run(self, harness: RecoveryHarness, title: str, accounts: list[dict]):
        client = MagicMock(window_handle=100)
        settings = MagicMock(accounts=accounts)
        with (
            patch("bartlebot.clients.client_manager.SettingsManager") as sm,
            patch("bartlebot.clients.client_manager.get_window_title", return_value=title),
        ):
            sm.instance.return_value = settings
            ClientManager._refresh_account_map(harness, [client])

    def test_unmatched_title_is_remembered(self, harness: RecoveryHarness) -> None:
        self._run(harness, "Wizard101", [{"username": "user1", "alias": "Jeff"}])
        assert harness._hwnd_account_map == {}, "an adopted window matches no account"
        assert harness._hwnd_titles == {100: "Wizard101"}

    def test_matched_title_is_remembered_and_mapped(self, harness: RecoveryHarness) -> None:
        self._run(harness, "[Jeff] Wizard101", [{"username": "user1", "alias": "Jeff"}])
        assert harness._hwnd_account_map == {100: "user1"}
        assert harness._hwnd_titles == {100: "[Jeff] Wizard101"}

    def test_unreadable_title_records_nothing(self, harness: RecoveryHarness) -> None:
        client = MagicMock(window_handle=100)
        with (
            patch("bartlebot.clients.client_manager.SettingsManager") as sm,
            patch("bartlebot.clients.client_manager.get_window_title", side_effect=OSError("gone")),
        ):
            sm.instance.return_value = MagicMock(accounts=[])
            ClientManager._refresh_account_map(harness, [client])
        assert harness._hwnd_titles == {}


class TestOnClientsRemoved:
    def test_unknown_hwnd_is_a_no_op(self, harness: RecoveryHarness) -> None:
        harness._hwnd_account_map = {100: "user1"}
        harness._client_order = ["user1"]
        harness._on_clients_removed({999})
        assert harness._client_order == ["user1"]
        assert harness._pending_recovery == {}

    def test_unmapped_hwnd_warns_that_recovery_is_skipped(self, harness: RecoveryHarness, caplog) -> None:
        """The overnight-silence bug: an adopted window disconnects and nothing is said.

        Only clients BartleBot launched carry the '[account] Wizard101' title the account
        map is keyed on, so a window adopted from another session can never be recovered.
        That has to be audible, or a stalled run looks identical to a healthy one.
        """
        harness._hwnd_titles = {999: "Wizard101"}
        harness._machine = _make_machine(stopped=False)

        with caplog.at_level("WARNING", logger="bartlebot.clients.client_manager"):
            harness._on_clients_removed({999})

        assert len(caplog.records) == 1, "exactly one warning per unmapped client"
        message = caplog.records[0].getMessage()
        assert "999" in message and "'Wizard101'" in message, "the window must be identifiable"
        assert "SKIPPED" in message, "the message has to say recovery is not happening"
        assert harness._pending_recovery == {}

    def test_unmapped_hwnd_is_quiet_when_no_bot_is_running(self, harness: RecoveryHarness, caplog) -> None:
        """Closing a client by hand with no bot running is routine, not a fault."""
        harness._hwnd_titles = {999: "Wizard101"}
        harness._machine = None

        with caplog.at_level("WARNING", logger="bartlebot.clients.client_manager"):
            harness._on_clients_removed({999})

        assert caplog.records == []

    def test_unmapped_hwnd_with_no_known_title_still_warns(self, harness: RecoveryHarness, caplog) -> None:
        """A window gone before its title was ever read is the case that most needs saying."""
        harness._machine = _make_machine(stopped=False)

        with caplog.at_level("WARNING", logger="bartlebot.clients.client_manager"):
            harness._on_clients_removed({999})

        assert len(caplog.records) == 1
        assert "unreadable" in caplog.records[0].getMessage()

    def test_mapped_client_holding_no_slot_warns(self, harness: RecoveryHarness, caplog) -> None:
        """The other silent skip: an account is known but holds no slot in the ordering."""
        harness._hwnd_account_map = {100: "user1"}
        harness._client_order = ["user2"]
        harness._machine = _make_machine(stopped=False)

        with caplog.at_level("WARNING", logger="bartlebot.clients.client_manager"):
            harness._on_clients_removed({100})

        assert len(caplog.records) == 1
        assert "SKIPPED" in caplog.records[0].getMessage()
        assert harness._pending_recovery == {}

    def test_queued_recovery_does_not_warn_about_being_skipped(self, harness: RecoveryHarness, caplog) -> None:
        """The healthy path must stay distinguishable from the skipped one."""
        harness._hwnd_account_map = {100: "user1"}
        harness._client_order = ["user1"]
        harness._machine = _make_machine(stopped=False)

        with (
            caplog.at_level("WARNING", logger="bartlebot.clients.client_manager"),
            patch("asyncio.create_task", _mock_create_task),
        ):
            harness._on_clients_removed({100})

        assert len(caplog.records) == 1
        assert "queuing recovery" in caplog.records[0].getMessage()
        assert "SKIPPED" not in caplog.records[0].getMessage()

    def test_title_cache_is_released_on_removal(self, harness: RecoveryHarness) -> None:
        """Titles are kept only until the removal they explain, so the dict cannot grow forever."""
        harness._hwnd_titles = {999: "Wizard101"}
        harness._machine = None
        harness._on_clients_removed({999})
        assert harness._hwnd_titles == {}

    def test_username_absent_from_client_order_is_ignored(self, harness: RecoveryHarness) -> None:
        harness._hwnd_account_map = {100: "user1"}
        harness._client_order = ["user2"]  # user1 not tracked
        harness._on_clients_removed({100})
        assert harness._client_order == ["user2"]
        assert harness._pending_recovery == {}

    def test_no_machine_clears_slot_but_skips_recovery(self, harness: RecoveryHarness) -> None:
        harness._hwnd_account_map = {100: "user1"}
        harness._client_order = ["user1"]
        harness._machine = None
        harness._on_clients_removed({100})
        assert harness._client_order == [None]
        assert harness._pending_recovery == {}

    def test_stopped_machine_clears_slot_but_skips_recovery(self, harness: RecoveryHarness) -> None:
        harness._hwnd_account_map = {100: "user1"}
        harness._client_order = ["user1"]
        harness._machine = _make_machine(stopped=True)
        harness._on_clients_removed({100})
        assert harness._client_order == [None]
        assert harness._pending_recovery == {}

    def test_running_machine_queues_recovery_pauses_and_spawns_task(self, harness: RecoveryHarness) -> None:
        harness._hwnd_account_map = {100: "user1"}
        harness._client_order = ["user1"]
        harness._machine = _make_machine()
        with patch("asyncio.create_task", side_effect=_mock_create_task) as mock_create_task:
            harness._on_clients_removed({100})
        assert harness._client_order == [None]
        assert harness._pending_recovery == {0: "user1"}
        harness._machine.pause.assert_called_once()
        mock_create_task.assert_called_once()
        assert harness._recovery_in_progress is True

    def test_multiple_clients_removed_at_once_all_queued(self, harness: RecoveryHarness) -> None:
        harness._hwnd_account_map = {100: "user1", 200: "user2"}
        harness._client_order = ["user1", "user2"]
        harness._machine = _make_machine()
        with patch("asyncio.create_task", side_effect=_mock_create_task):
            harness._on_clients_removed({100, 200})
        assert harness._client_order == [None, None]
        assert harness._pending_recovery == {0: "user1", 1: "user2"}

    def test_no_second_task_when_recovery_already_in_progress(self, harness: RecoveryHarness) -> None:
        harness._hwnd_account_map = {100: "user1"}
        harness._client_order = ["user1"]
        harness._machine = _make_machine()
        harness._recovery_in_progress = True
        with patch("asyncio.create_task") as mock_create_task:
            harness._on_clients_removed({100})
        mock_create_task.assert_not_called()

    def test_no_task_spawned_when_nothing_queued(self, harness: RecoveryHarness) -> None:
        # Hwnd is unknown so _pending_recovery stays empty after removal.
        harness._hwnd_account_map = {}
        harness._client_order = ["user1"]
        harness._machine = _make_machine()
        with patch("asyncio.create_task") as mock_create_task:
            harness._on_clients_removed({999})
        mock_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# _run_recovery_queue
# ---------------------------------------------------------------------------


class TestRunRecoveryQueue:
    async def test_single_client_success_jumps_fsm_and_resumes(self, harness: RecoveryHarness) -> None:
        machine = _make_machine()
        harness._machine = machine
        harness._pending_recovery = {0: "user1"}
        harness._get_recovery_state = lambda: "recovery_zone"
        harness._reconnect_clients = AsyncMock()

        await harness._run_recovery_queue()

        harness._reconnect_clients.assert_awaited_once_with({0: "user1"})
        assert harness._pending_recovery == {}
        machine.force_state.assert_called_once_with("recovery_zone")
        machine.resume.assert_called_once()
        assert harness._recovery_in_progress is False

    async def test_multiple_clients_recovered_in_one_batch_single_resume(self, harness: RecoveryHarness) -> None:
        """Every pending slot goes out together: relaunching them in turn costs a full
        launch each, which is the whole reason a fleet relog is affordable."""
        machine = _make_machine()
        harness._machine = machine
        harness._pending_recovery = {0: "user1", 1: "user2"}
        harness._get_recovery_state = lambda: "start"

        call_log: list[dict] = []

        async def _fake_reconnect(slots: dict) -> None:
            call_log.append(dict(slots))

        harness._reconnect_clients = _fake_reconnect

        await harness._run_recovery_queue()

        assert call_log == [{0: "user1", 1: "user2"}]
        assert harness._pending_recovery == {}
        machine.resume.assert_called_once()

    async def test_a_slot_queued_late_is_picked_up_by_a_second_pass(self, harness: RecoveryHarness) -> None:
        """A client noticed missing while the first batch is in flight still comes back —
        the queue drains until it is empty rather than once."""
        machine = _make_machine()
        harness._machine = machine
        harness._pending_recovery = {0: "user1"}
        harness._get_recovery_state = lambda: "start"

        call_log: list[dict] = []

        async def _fake_reconnect(slots: dict) -> None:
            call_log.append(dict(slots))
            if len(call_log) == 1:
                harness._pending_recovery[1] = "user2"

        harness._reconnect_clients = _fake_reconnect

        await harness._run_recovery_queue()

        assert call_log == [{0: "user1"}, {1: "user2"}]
        machine.resume.assert_called_once()

    async def test_reconnect_exception_clears_queue_stops_machine_no_resume(self, harness: RecoveryHarness) -> None:
        machine = _make_machine()
        harness._machine = machine
        harness._pending_recovery = {0: "user1", 1: "user2"}
        harness._get_recovery_state = lambda: "start"
        harness._reconnect_clients = AsyncMock(side_effect=RuntimeError("network error"))

        await harness._run_recovery_queue()

        # Queue fully cleared; machine stopped; FSM never resumed.
        assert harness._pending_recovery == {}
        machine.stop.assert_called_once()
        machine.resume.assert_not_called()
        assert harness._recovery_in_progress is False

    async def test_a_later_batch_failing_stops_after_the_first_is_already_done(self, harness: RecoveryHarness) -> None:
        machine = _make_machine()
        harness._machine = machine
        harness._pending_recovery = {0: "user1"}
        harness._get_recovery_state = lambda: "start"

        call_count = 0

        async def _flaky_reconnect(slots: dict) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                harness._pending_recovery[1] = "user2"
                return
            raise RuntimeError("second batch failed")

        harness._reconnect_clients = _flaky_reconnect

        await harness._run_recovery_queue()

        machine.stop.assert_called_once()
        machine.resume.assert_not_called()
        assert harness._pending_recovery == {}

    async def test_no_recovery_state_stops_machine_instead_of_resuming(self, harness: RecoveryHarness) -> None:
        machine = _make_machine()
        harness._machine = machine
        harness._pending_recovery = {0: "user1"}
        harness._get_recovery_state = lambda: None
        harness._reconnect_clients = AsyncMock()

        await harness._run_recovery_queue()

        machine.stop.assert_called_once()
        machine.force_state.assert_not_called()
        machine.resume.assert_not_called()

    async def test_no_loaded_config_stops_machine(self, harness: RecoveryHarness) -> None:
        machine = _make_machine()
        harness._machine = machine
        harness._pending_recovery = {0: "user1"}
        harness._get_recovery_state = lambda: None
        harness._reconnect_clients = AsyncMock()

        await harness._run_recovery_queue()

        machine.stop.assert_called_once()
        machine.resume.assert_not_called()

    async def test_machine_externally_stopped_mid_recovery_no_force_state(self, harness: RecoveryHarness) -> None:
        machine = _make_machine()
        harness._machine = machine
        harness._pending_recovery = {0: "user1"}
        harness._get_recovery_state = lambda: "start"

        async def _stop_machine(slots: dict) -> None:
            machine.is_stopped = True

        harness._reconnect_clients = _stop_machine

        await harness._run_recovery_queue()

        machine.force_state.assert_not_called()
        machine.resume.assert_not_called()

    async def test_recovery_in_progress_flag_cleared_even_on_exception(self, harness: RecoveryHarness) -> None:
        harness._machine = _make_machine()
        harness._pending_recovery = {0: "user1"}
        harness._get_recovery_state = lambda: "start"
        harness._recovery_in_progress = True
        harness._reconnect_clients = AsyncMock(side_effect=RuntimeError("crash"))

        await harness._run_recovery_queue()

        assert harness._recovery_in_progress is False


# ---------------------------------------------------------------------------
# _reconnect_clients
# ---------------------------------------------------------------------------


def _fake_launch_accounts() -> AsyncMock:
    """A launch_accounts stand-in that titles each window and runs its drive.

    The drive moved inside the launcher when recovery started launching in batches, so a
    mock that only records its arguments would leave every client un-driven and every
    recovery reading as a timeout.
    """

    async def _launch(accounts, *, resolution=None, drive=None):
        titles = [f"[{display}] Wizard101" for _username, _password, display in accounts]
        if drive is not None:
            await asyncio.gather(*[drive(title) for title in titles])
        return titles

    return AsyncMock(side_effect=_launch)


def _base_reconnect_patches(display_name: str = "j***n"):
    """Return a stacked context manager with the external deps every reconnect test needs."""
    mock_settings = MagicMock()
    mock_settings.accounts = []
    mock_settings.favorite_realm = ""  # a MagicMock is truthy; it would ask the cleanup for Remember Realm
    mock_windll = MagicMock()
    mock_windll.user32.FindWindowW.return_value = 0  # no crashed window by default

    return (
        patch("bartlebot.clients.client_manager.CredentialStore"),
        patch("bartlebot.clients.client_manager.SettingsManager"),
        patch("bartlebot.launcher.launch_profile.launch_accounts", _fake_launch_accounts()),
        patch("ctypes.windll", mock_windll),
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch("bartlebot.launcher.auto_select.is_enabled_by_path", new_callable=AsyncMock, return_value=False),
        patch("bartlebot.launcher.auto_select.poll_until", new_callable=AsyncMock, return_value=False),
    )


class TestReconnectClients:
    async def test_no_machine_returns_immediately(self, harness: RecoveryHarness) -> None:
        harness._machine = None
        # Should return without touching any external deps — no patches needed.
        await harness._reconnect_clients({0: "user1"})

    async def test_missing_password_emits_error_signal_and_stops(self, harness: RecoveryHarness, caplog) -> None:
        machine = _make_machine()
        harness._machine = machine

        with patch("bartlebot.clients.client_manager.CredentialStore") as mock_creds:
            mock_creds.retrieve.return_value = None
            mock_creds.display_name.return_value = "u***1"
            await harness._reconnect_clients({0: "user1"})

        harness.recovery_error.emit.assert_called_once()
        emitted_msg: str = harness.recovery_error.emit.call_args[0][0]
        # The account's display name, never its username — in the dialog or the log (#121).
        assert "u***1" in emitted_msg
        assert "user1" not in emitted_msg + caplog.text
        assert "P1's account" in caplog.text
        assert "Settings" in emitted_msg  # actionable hint
        machine.stop.assert_called_once()

    async def test_launch_failure_stops_machine(self, harness: RecoveryHarness) -> None:
        machine = _make_machine()
        harness._machine = machine
        mock_settings = MagicMock()
        mock_settings.accounts = []
        mock_settings.favorite_realm = ""  # a MagicMock is truthy; it would ask the cleanup for Remember Realm

        with (
            patch("bartlebot.clients.client_manager.CredentialStore") as mock_creds,
            patch("bartlebot.clients.client_manager.SettingsManager") as mock_sm,
            patch(
                "bartlebot.launcher.launch_profile.launch_accounts",
                new_callable=AsyncMock,
                side_effect=RuntimeError("launcher error"),
            ),
            patch("ctypes.windll", MagicMock()),
        ):
            mock_creds.retrieve.return_value = "s3cr3t"
            mock_creds.display_name.return_value = "j***n"
            mock_sm.instance.return_value = mock_settings
            await harness._reconnect_clients({0: "user1"})

        machine.stop.assert_called_once()
        machine.force_state.assert_not_called()

    async def test_success_restores_slot_and_does_not_stop_machine(self, harness: RecoveryHarness) -> None:
        machine = _make_machine()
        harness._machine = machine
        harness._client_order = [None]

        fake_client = MagicMock()
        fake_client.window_handle = 999
        harness._clients = [fake_client]

        mock_settings = MagicMock()
        mock_settings.accounts = []
        mock_settings.favorite_realm = ""  # a MagicMock is truthy; it would ask the cleanup for Remember Realm

        with (
            patch("bartlebot.clients.client_manager.CredentialStore") as mock_creds,
            patch("bartlebot.clients.client_manager.SettingsManager") as mock_sm,
            patch("bartlebot.launcher.launch_profile.launch_accounts", _fake_launch_accounts()),
            patch("ctypes.windll", MagicMock()),
            patch("bartlebot.clients.client_manager.get_window_title", return_value="[j***n] Wizard101"),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("bartlebot.launcher.auto_select.is_enabled_by_path", new_callable=AsyncMock, return_value=False),
            patch("bartlebot.launcher.auto_select.poll_until", new_callable=AsyncMock, return_value=False),
        ):
            mock_creds.retrieve.return_value = "s3cr3t"
            mock_creds.display_name.return_value = "j***n"
            mock_sm.instance.return_value = mock_settings
            await harness._reconnect_clients({0: "user1"})

        assert harness._client_order[0] == "user1"
        machine.stop.assert_not_called()

    async def test_client_timeout_stops_machine_and_leaves_slot_empty(self, harness: RecoveryHarness) -> None:
        machine = _make_machine()
        harness._machine = machine
        harness._client_order = [None]
        harness._clients = []  # client never appears

        mock_settings = MagicMock()
        mock_settings.accounts = []
        mock_settings.favorite_realm = ""  # a MagicMock is truthy; it would ask the cleanup for Remember Realm

        # Make time run away, so whatever the drive sets its deadline from is already past
        # by its next check. A fixed second value cannot do that: the batch launch hands
        # the drives to the event loop, which reads the clock itself, so which call the
        # drive gets is not ours to decide.
        clock = 100.0

        def _fake_time() -> float:
            nonlocal clock
            clock += 1000.0
            return clock

        loop = asyncio.get_event_loop()
        original_time = loop.time
        loop.time = _fake_time
        try:
            with (
                patch("bartlebot.clients.client_manager.CredentialStore") as mock_creds,
                patch("bartlebot.clients.client_manager.SettingsManager") as mock_sm,
                patch("bartlebot.launcher.launch_profile.launch_accounts", _fake_launch_accounts()),
                patch("ctypes.windll", MagicMock()),
                patch("asyncio.sleep", new_callable=AsyncMock),
            ):
                mock_creds.retrieve.return_value = "s3cr3t"
                mock_creds.display_name.return_value = "j***n"
                mock_sm.instance.return_value = mock_settings
                await harness._reconnect_clients({0: "user1"})
        finally:
            loop.time = original_time

        machine.stop.assert_called_once()
        assert harness._client_order[0] is None  # slot not restored

    async def test_old_window_resolution_passed_to_launch(self, harness: RecoveryHarness) -> None:
        machine = _make_machine()
        harness._machine = machine
        harness._client_order = [None]
        harness._client_resolutions["user1"] = (800, 600)

        fake_client = MagicMock()
        fake_client.window_handle = 999
        harness._clients = [fake_client]

        mock_settings = MagicMock()
        mock_settings.accounts = []
        mock_settings.favorite_realm = ""  # a MagicMock is truthy; it would ask the cleanup for Remember Realm

        mock_launch = _fake_launch_accounts()

        with (
            patch("bartlebot.clients.client_manager.CredentialStore") as mock_creds,
            patch("bartlebot.clients.client_manager.SettingsManager") as mock_sm,
            patch("bartlebot.launcher.launch_profile.launch_accounts", mock_launch),
            patch("ctypes.windll", MagicMock()),
            patch("bartlebot.clients.client_manager.get_window_title", return_value="[j***n] Wizard101"),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("bartlebot.launcher.auto_select.is_enabled_by_path", new_callable=AsyncMock, return_value=False),
            patch("bartlebot.launcher.auto_select.poll_until", new_callable=AsyncMock, return_value=False),
        ):
            mock_creds.retrieve.return_value = "s3cr3t"
            mock_creds.display_name.return_value = "j***n"
            mock_sm.instance.return_value = mock_settings
            await harness._reconnect_clients({0: "user1"})

        _, kwargs = mock_launch.call_args
        assert kwargs.get("resolution") == (800, 600)

    async def test_no_old_window_passes_none_resolution_to_launch(self, harness: RecoveryHarness) -> None:
        machine = _make_machine()
        harness._machine = machine
        harness._client_order = [None]

        fake_client = MagicMock()
        fake_client.window_handle = 999
        harness._clients = [fake_client]

        mock_settings = MagicMock()
        mock_settings.accounts = []
        mock_settings.favorite_realm = ""  # a MagicMock is truthy; it would ask the cleanup for Remember Realm

        mock_windll = MagicMock()
        mock_windll.user32.FindWindowW.return_value = 0  # no crashed window

        mock_launch = _fake_launch_accounts()

        with (
            patch("bartlebot.clients.client_manager.CredentialStore") as mock_creds,
            patch("bartlebot.clients.client_manager.SettingsManager") as mock_sm,
            patch("bartlebot.launcher.launch_profile.launch_accounts", mock_launch),
            patch("ctypes.windll", mock_windll),
            patch("bartlebot.clients.client_manager.get_window_title", return_value="[j***n] Wizard101"),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("bartlebot.launcher.auto_select.is_enabled_by_path", new_callable=AsyncMock, return_value=False),
            patch("bartlebot.launcher.auto_select.poll_until", new_callable=AsyncMock, return_value=False),
        ):
            mock_creds.retrieve.return_value = "s3cr3t"
            mock_creds.display_name.return_value = "j***n"
            mock_sm.instance.return_value = mock_settings
            await harness._reconnect_clients({0: "user1"})

        _, kwargs = mock_launch.call_args
        assert kwargs.get("resolution") is None

    async def test_crowns_shop_dismissed_click_by_path_called(self, harness: RecoveryHarness) -> None:
        machine = _make_machine()
        harness._machine = machine
        harness._client_order = [None]

        fake_client = MagicMock()
        fake_client.window_handle = 999
        harness._clients = [fake_client]

        mock_settings = MagicMock()
        mock_settings.accounts = []
        mock_settings.favorite_realm = ""  # a MagicMock is truthy; it would ask the cleanup for Remember Realm

        # The driver reads and tidies up through the handler's client, so it has to be
        # registered there as well as published.
        harness._handler = MagicMock(clients=[fake_client])

        # ESC cleanup loops each call is_enabled_by_path once (BACKPACK_CLOSE_BUTTON_PATH):
        #   loop 1: True  → break immediately
        #   loop 2: False → not False = True → break immediately
        # Then the crowns shop loop calls it with CROWNS_SHOP_PATH:
        #   iteration 1: True  → enter dismiss flow
        #   iteration 2: False → not False = True → break
        crowns_check = AsyncMock(side_effect=[True, False, True, False])
        mock_click = AsyncMock()

        with (
            patch("bartlebot.clients.client_manager.CredentialStore") as mock_creds,
            patch("bartlebot.clients.client_manager.SettingsManager") as mock_sm,
            patch("bartlebot.launcher.launch_profile.launch_accounts", _fake_launch_accounts()),
            patch("ctypes.windll", MagicMock()),
            patch("bartlebot.clients.client_manager.get_window_title", return_value="[j***n] Wizard101"),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("bartlebot.launcher.auto_select.is_enabled_by_path", crowns_check),
            patch("bartlebot.launcher.auto_select.poll_until", new_callable=AsyncMock, return_value=True),
            patch("bartlebot.launcher.auto_select.click_by_path", mock_click),
        ):
            mock_creds.retrieve.return_value = "s3cr3t"
            mock_creds.display_name.return_value = "j***n"
            mock_sm.instance.return_value = mock_settings
            await harness._reconnect_clients({0: "user1"})

        mock_click.assert_awaited_once()
        assert harness._client_order[0] == "user1"


# ---------------------------------------------------------------------------
# Batch recovery
# ---------------------------------------------------------------------------


class TestBatchRecovery:
    """A relog closes the whole fleet, so recovery has to bring it back as one batch.

    Relaunching in turn costs a full launch per client -- spawn, two loading screens,
    character select, load-in -- where the batch costs roughly one.
    """

    def _prepare(self, harness: RecoveryHarness, *, auto_select: bool = True) -> MagicMock:
        """Two slots down, and two windows that come back under their own titles."""
        harness._machine = _make_machine()
        harness._client_order = [None, None]
        clients = []
        for hwnd in (991, 992):
            client = MagicMock()
            client.window_handle = hwnd
            clients.append(client)
        harness._clients = clients
        harness._handler = MagicMock(clients=clients)
        harness._titles = {991: "[user1] Wizard101", 992: "[user2] Wizard101"}
        settings = MagicMock()
        settings.accounts = []
        settings.auto_select_character = auto_select
        return settings

    async def _run(
        self,
        harness: RecoveryHarness,
        settings: MagicMock,
        launch: AsyncMock,
        slots: dict,
        *,
        no_password: set[str] | None = None,
    ) -> None:
        """Run a reconnect with every external dependency stubbed."""
        no_password = no_password or set()
        with (
            patch("bartlebot.clients.client_manager.CredentialStore") as mock_creds,
            patch("bartlebot.clients.client_manager.SettingsManager") as mock_sm,
            patch("bartlebot.launcher.launch_profile.launch_accounts", launch),
            patch("bartlebot.clients.client_manager.get_window_title", side_effect=harness._titles.get),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("bartlebot.launcher.auto_select.is_enabled_by_path", new_callable=AsyncMock, return_value=False),
            patch("bartlebot.launcher.auto_select.poll_until", new_callable=AsyncMock, return_value=False),
        ):
            mock_creds.retrieve.side_effect = lambda username: None if username in no_password else "s3cr3t"
            mock_creds.display_name.side_effect = lambda username, _alias=None: username
            mock_sm.instance.return_value = settings
            await harness._reconnect_clients(slots)

    async def test_a_whole_fleet_is_launched_in_one_batch(self, harness: RecoveryHarness) -> None:
        settings = self._prepare(harness)
        launch = _fake_launch_accounts()

        await self._run(harness, settings, launch, {0: "user1", 1: "user2"})

        launch.assert_awaited_once()
        (accounts,) = launch.await_args.args
        assert [username for username, _password, _display in accounts] == ["user1", "user2"]
        assert harness._client_order == ["user1", "user2"]
        harness._machine.stop.assert_not_called()

    async def test_every_drive_is_given_the_window_it_belongs_to(self, harness: RecoveryHarness) -> None:
        """The launcher drives each window, so recovery has to hear back about each one.

        Whether those drives overlap is the launcher's business — see
        test_launch_batch.py, which tests it against the real launch_accounts.
        """
        settings = self._prepare(harness)
        driven: list[str] = []

        async def _drive(title: str) -> bool:
            driven.append(title)
            return True

        harness.drive_client_in = _drive
        await self._run(harness, settings, _fake_launch_accounts(), {0: "user1", 1: "user2"})

        assert driven == ["[user1] Wizard101", "[user2] Wizard101"]

    async def test_a_client_that_never_arrives_stops_the_bot(self, harness: RecoveryHarness) -> None:
        """A run cannot continue a player short, batch or no batch."""
        settings = self._prepare(harness)

        async def _drive(title: str) -> bool:
            return title != "[user2] Wizard101"  # the second one never loads in

        harness.drive_client_in = _drive
        await self._run(harness, settings, _fake_launch_accounts(), {0: "user1", 1: "user2"})

        harness._machine.stop.assert_called_once()
        assert harness._client_order == [None, None]  # no slot restored on a failed batch

    async def test_clients_wanting_different_resolutions_are_launched_separately(
        self, harness: RecoveryHarness
    ) -> None:
        """config.xml holds one resolution and the game reads it at startup, so a batch
        can only hold clients that want the same one."""
        settings = self._prepare(harness)
        harness._client_resolutions = {"user1": (800, 600), "user2": (1280, 720)}
        launch = _fake_launch_accounts()

        await self._run(harness, settings, launch, {0: "user1", 1: "user2"})

        assert launch.await_count == 2
        assert [call.kwargs["resolution"] for call in launch.await_args_list] == [(800, 600), (1280, 720)]

    async def test_clients_sharing_a_resolution_stay_in_one_batch(self, harness: RecoveryHarness) -> None:
        settings = self._prepare(harness)
        harness._client_resolutions = {"user1": (800, 600), "user2": (800, 600)}
        launch = _fake_launch_accounts()

        await self._run(harness, settings, launch, {0: "user1", 1: "user2"})

        launch.assert_awaited_once()
        assert launch.await_args.kwargs["resolution"] == (800, 600)

    async def test_an_unrecoverable_slot_launches_nothing(self, harness: RecoveryHarness) -> None:
        """Half a fleet relaunched is worse than none: the run is over either way."""
        settings = self._prepare(harness)
        launch = _fake_launch_accounts()

        await self._run(harness, settings, launch, {0: "user1", 1: "user2"}, no_password={"user2"})

        launch.assert_not_awaited()
        harness._machine.stop.assert_called_once()

    async def test_manual_login_is_asked_for_once_for_the_batch(self, harness: RecoveryHarness) -> None:
        """With auto-select off, asking per client raises one dialog per player, each
        saying the same thing."""
        settings = self._prepare(harness, auto_select=False)
        launch = _fake_launch_accounts()

        await self._run(harness, settings, launch, {0: "user1", 1: "user2"})

        harness.recovery_error.emit.assert_called_once()
        message: str = harness.recovery_error.emit.call_args[0][0]
        assert "user1" in message and "user2" in message
        # The drive still runs — it clears the loading screens so the windows arrive at
        # character select — it just stops there rather than picking anyone.
        assert launch.await_args.kwargs["drive"] is not None
        assert harness._client_order == ["user1", "user2"]


class TestSettleRecoveryBatch:
    async def test_a_crash_is_not_waited_on(self, harness: RecoveryHarness) -> None:
        """Nothing more is coming after a crash, so recovery starts immediately."""
        harness._relog_in_progress = False
        with patch("bartlebot.clients.client_manager.asyncio.sleep", new_callable=AsyncMock) as slept:
            await harness._settle_recovery_batch()
        slept.assert_not_awaited()

    async def test_a_relog_in_flight_is_waited_out(self, harness: RecoveryHarness) -> None:
        harness._relog_in_progress = True

        with (
            patch("bartlebot.clients.client_manager.CLIENT_DETECT_INTERVAL", 0.01),
            patch("bartlebot.clients.client_manager.RECOVERY_BATCH_SETTLE", 5.0),
        ):
            task = asyncio.create_task(harness._settle_recovery_batch())
            await asyncio.sleep(0.05)
            assert not task.done()  # still closing clients
            harness._relog_in_progress = False
            await task

    async def test_a_relog_that_never_finishes_does_not_block_recovery(self, harness: RecoveryHarness) -> None:
        """The budget is a batching optimisation, never the difference between recovering
        and not — the stragglers are picked up on the queue's next pass."""
        harness._relog_in_progress = True

        with (
            patch("bartlebot.clients.client_manager.CLIENT_DETECT_INTERVAL", 0.01),
            patch("bartlebot.clients.client_manager.RECOVERY_BATCH_SETTLE", 0.02),
        ):
            await harness._settle_recovery_batch()


# ---------------------------------------------------------------------------
# _build_ordered_clients
# ---------------------------------------------------------------------------


class TestBuildOrderedClients:
    def _client(self, hwnd: int) -> MagicMock:
        c = MagicMock()
        c.window_handle = hwnd
        return c

    def test_clients_returned_in_slot_order(self, harness: RecoveryHarness) -> None:
        c1 = self._client(100)
        c2 = self._client(200)
        harness._hwnd_account_map = {100: "user1", 200: "user2"}
        harness._client_order = ["user2", "user1"]
        result = harness._build_ordered_clients([c1, c2])
        assert result == [c2, c1]

    def test_none_slots_excluded_from_result(self, harness: RecoveryHarness) -> None:
        c1 = self._client(100)
        c2 = self._client(200)
        harness._hwnd_account_map = {100: "user1", 200: "user2"}
        harness._client_order = ["user1", None, "user2"]
        result = harness._build_ordered_clients([c1, c2])
        assert result == [c1, c2]

    def test_unknown_clients_appended_after_known(self, harness: RecoveryHarness) -> None:
        c_known = self._client(100)
        c_unknown = self._client(999)
        harness._hwnd_account_map = {100: "user1"}
        harness._client_order = ["user1"]
        result = harness._build_ordered_clients([c_known, c_unknown])
        assert result == [c_known, c_unknown]

    def test_empty_client_list_returns_empty(self, harness: RecoveryHarness) -> None:
        harness._client_order = ["user1"]
        harness._hwnd_account_map = {100: "user1"}
        result = harness._build_ordered_clients([])
        assert result == []
