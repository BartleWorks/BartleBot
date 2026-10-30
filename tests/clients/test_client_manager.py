"""BartleBot's client manager: what it adds to core's, wired as the app wires it.

Core's half is tested in bartleby-core's ``tests/runtime/clients/``. Recovery and the relog
cycle have files of their own here (``test_recovery.py``, ``test_relog_cycle.py``).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bartleby.runtime.clients.client_manager import Removal
from bartleby.runtime.launcher import auto_select

from bartlebot.clients.client_manager import ClientManager
from bartlebot.settings_manager import SettingsManager


class FakeClient:
    def __init__(self, hwnd: int) -> None:
        self.window_handle = hwnd
        self.close = AsyncMock()

    def __repr__(self) -> str:
        return f"FakeClient({self.window_handle})"


def _manager() -> ClientManager:
    return ClientManager(
        handler=MagicMock(clients=[]), game_view=MagicMock(), drops_widget=MagicMock(), get_recovery_state=lambda: None
    )


@pytest.mark.parametrize(("realm", "expected"), [("Ambrose", True), ("", False)])
async def test_a_drive_turns_remember_realm_on_only_with_a_favorite_realm(realm, expected):
    SettingsManager.instance().auto_select_character = True
    SettingsManager.instance().favorite_realm = realm
    driver = AsyncMock(return_value=True)
    with patch.object(auto_select, "drive_client_in", driver):
        await _manager().drive_client_in("[Alpha] Wizard101")
    assert driver.await_args.kwargs["remember_realm"] is expected


async def test_the_setting_a_drive_reads_is_this_app_s():
    """Core's drive asks the settings it was handed, which here are the singleton's."""
    SettingsManager.instance().auto_select_character = False
    driver = AsyncMock(return_value=True)
    with patch.object(auto_select, "drive_to_character_select", driver):
        assert await _manager().drive_client_in("[Alpha] Wizard101") is False
    driver.assert_awaited_once()


async def test_a_running_bot_sees_the_emptied_client_list():
    manager = _manager()
    manager._clients = [FakeClient(10), FakeClient(20)]
    manager._machine = MagicMock()

    await manager.unhook_all()

    manager._machine.set_clients.assert_called_once_with([])


async def test_the_machine_is_handed_the_live_list_before_any_other_listener():
    """Connected first in the constructor, as when the loop handed the list over itself."""
    manager = _manager()
    manager._clients = [FakeClient(10)]
    machine = MagicMock()
    manager._machine = machine
    heard: list = []
    manager.clients_changed.connect(lambda clients: heard.append(machine.set_clients.call_count))

    await manager.unhook_client(0)

    assert heard == [1], "the machine had the list before the app's own listener ran"
    (handed,) = machine.set_clients.call_args.args
    assert handed is manager.clients


def test_the_signals_are_qt_signals_on_a_qobject_of_their_own():
    """Every connection the app makes behaves as it did when the manager was a QObject."""
    manager = _manager()
    heard: list = []
    manager.all_clients_changed.connect(lambda active, unhooked: heard.append((active, unhooked)))

    manager.all_clients_changed.emit([1], [2])

    assert heard == [([1], [2])]
    assert manager.clients_changed.signal  # a bound pyqtSignal, not core's Listeners


async def test_a_lost_client_mid_run_is_queued_for_recovery_through_core_s_report():
    """Core empties the slot and reports it; BartleBot's answer holds it and pauses the bot."""
    manager = _manager()
    manager._machine = MagicMock(is_stopped=False)
    manager._run_recovery_queue = AsyncMock()

    manager.clients_removed.emit([Removal(hwnd=10, username="user1", slot=0, title=None)])

    assert manager._pending_recovery == {0: "user1"}
    manager._machine.pause.assert_called_once()
    await manager._recovery_task
    manager._run_recovery_queue.assert_awaited_once()
