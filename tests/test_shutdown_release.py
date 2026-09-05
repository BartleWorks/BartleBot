"""Releasing the clients on the way out (#155).

Closing BartleBot used to destroy every embedded game window: ``ClientTile`` re-parents
each one under the Qt window, so Qt took its children down with it, and nothing unhooked
on the way out. The fix is a quit gate — the window asks, the release runs as a task, and
the task closes the window afterwards — so these cover both halves: what
``ClientManager.shutdown`` does to the clients, and what the gate decides.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from bartlebot.app_controller import AppController
from bartlebot.client_manager import ClientManager


def _client(hwnd: int, pid: int, log: list) -> MagicMock:
    client = MagicMock()
    client.window_handle = hwnd
    client.process_id = pid
    client.close = AsyncMock(side_effect=lambda: log.append(("unhook", hwnd)))
    return client


def _manager(hooked: list, unhooked: list, game_view: MagicMock | None = None) -> ClientManager:
    manager = ClientManager(
        handler=MagicMock(),
        game_view=game_view or MagicMock(),
        drops_widget=MagicMock(),
        get_recovery_state=lambda: None,
    )
    manager.clients.extend(hooked)
    manager.unhooked_clients.extend(unhooked)
    return manager


# ------------------------------------------------------------------ ClientManager.shutdown


async def test_leaving_the_windows_open_unhooks_and_vacates_every_tile(monkeypatch):
    log: list = []
    closed: list = []
    monkeypatch.setattr(
        "bartlebot.client_manager.force_close_client",
        AsyncMock(side_effect=lambda hwnd, pid=None: closed.append(hwnd) or True),
    )
    game_view = MagicMock()
    manager = _manager([_client(1, 11, log), _client(2, 22, log)], [_client(3, 33, log)], game_view)

    survivors = await manager.shutdown(close_windows=False)

    assert survivors == []
    assert closed == [], "leaving the windows open must not close any of them"
    # Only the hooked pair had hooks to release; the third was already unhooked.
    assert sorted(log) == [("unhook", 1), ("unhook", 2)]
    assert manager.clients == []
    # The embed is what kills the windows, so releasing it is the point of the whole path.
    game_view.unembed_all.assert_called_once()


async def test_closing_the_windows_unhooks_first_and_takes_the_unhooked_ones_too(monkeypatch):
    log: list = []
    monkeypatch.setattr(
        "bartlebot.client_manager.force_close_client",
        AsyncMock(side_effect=lambda hwnd, pid=None: log.append(("close", hwnd, pid)) or True),
    )
    manager = _manager([_client(1, 11, log)], [_client(3, 33, log)])

    survivors = await manager.shutdown(close_windows=True)

    assert survivors == []
    # Hooks come off before the window goes: a terminated process with hooks still
    # installed is the state the release exists to avoid.
    assert log[0] == ("unhook", 1)
    assert sorted(log[1:]) == [("close", 1, 11), ("close", 3, 33)]


async def test_a_window_that_will_not_close_is_reported_rather_than_waited_on(monkeypatch):
    monkeypatch.setattr(
        "bartlebot.client_manager.force_close_client",
        AsyncMock(side_effect=lambda hwnd, pid=None: hwnd != 2),
    )
    manager = _manager([_client(1, 11, []), _client(2, 22, [])], [])

    assert await manager.shutdown(close_windows=True) == [2]


async def test_the_detection_loop_is_stopped_before_anything_is_released(monkeypatch):
    """A tick landing mid-shutdown would re-hook and re-embed what was just let go."""
    order: list = []
    manager = _manager([_client(1, 11, order)], [])
    monkeypatch.setattr("bartlebot.client_manager.force_close_client", AsyncMock(return_value=True))

    async def _loop():
        await asyncio.Event().wait()

    task = asyncio.ensure_future(_loop())
    manager._client_loop_task = task

    await manager.shutdown(close_windows=False)

    assert task.cancelled() or task.cancelling()
    assert order == [("unhook", 1)]


async def test_a_failing_unhook_still_releases_the_windows(monkeypatch):
    game_view = MagicMock()
    manager = _manager([_client(1, 11, [])], [], game_view)
    monkeypatch.setattr(manager, "unhook_all", AsyncMock(side_effect=RuntimeError("hook gone")))

    assert await manager.shutdown(close_windows=False) == []
    game_view.unembed_all.assert_called_once()


# ------------------------------------------------------------------ the quit gate


class _GateStub:
    """The attributes AppController._confirm_shutdown reads on self."""

    def __init__(self, hooked: int = 0, unhooked: int = 0, choice: bool | None = False):
        self._clients_released = False
        self._shutdown_task = None
        self._client_manager = MagicMock()
        self._client_manager.clients = [MagicMock() for _ in range(hooked)]
        self._client_manager.unhooked_clients = [MagicMock() for _ in range(unhooked)]
        self._choice = choice
        self.asked: list[int] = []
        self.released: list[bool] = []

    def _ask_shutdown_choice(self, total: int) -> bool | None:
        self.asked.append(total)
        return self._choice

    async def _release_clients(self, *, close_windows: bool) -> None:
        self.released.append(close_windows)


def _confirm(stub: _GateStub) -> bool:
    return AppController._confirm_shutdown(stub)


async def test_a_quit_with_no_clients_is_not_worth_a_dialog():
    stub = _GateStub()
    assert _confirm(stub) is True
    assert stub.asked == []


async def test_cancelling_the_dialog_refuses_the_close_and_releases_nothing():
    stub = _GateStub(hooked=2, choice=None)
    assert _confirm(stub) is False
    assert stub.asked == [2]
    assert stub.released == []
    assert stub._shutdown_task is None


async def test_a_choice_refuses_this_close_and_starts_the_release():
    stub = _GateStub(hooked=2, unhooked=1, choice=True)
    assert _confirm(stub) is False
    assert stub.asked == [3], "unhooked windows still have to be answered for"
    assert stub._shutdown_task is not None
    await stub._shutdown_task
    assert stub.released == [True]


async def test_closing_again_while_the_release_runs_does_not_ask_twice():
    stub = _GateStub(hooked=1, choice=False)
    assert _confirm(stub) is False
    assert _confirm(stub) is False
    assert stub.asked == [1]
    await stub._shutdown_task


async def test_the_close_the_release_asks_for_is_let_through():
    stub = _GateStub(hooked=1)
    stub._clients_released = True
    assert _confirm(stub) is True
    assert stub.asked == []


# ------------------------------------------------------------------ the release task


class _ReleaseStub:
    """The attributes AppController._release_clients reads on self."""

    def __init__(self, survivors: list[int] | None = None, raises: bool = False):
        self.stopped = False
        self._window = MagicMock()
        self._shutdown_task = MagicMock()
        self._clients_released = False
        self._client_manager = MagicMock()
        if raises:
            self._client_manager.shutdown = AsyncMock(side_effect=RuntimeError("boom"))
        else:
            self._client_manager.shutdown = AsyncMock(return_value=survivors or [])

    def _stop_bot(self) -> None:
        self.stopped = True


async def test_the_release_stops_the_bot_and_then_lets_the_window_close(qapp):
    stub = _ReleaseStub()
    await AppController._release_clients(stub, close_windows=True)

    assert stub.stopped, "a running bot would keep reading hooks that are being torn down"
    stub._client_manager.shutdown.assert_awaited_once_with(close_windows=True)
    assert stub._clients_released is True
    assert stub._shutdown_task is None


async def test_a_release_that_throws_still_lets_the_window_close(qapp):
    """Otherwise a fault in the release path leaves the app unquittable."""
    stub = _ReleaseStub(raises=True)
    await AppController._release_clients(stub, close_windows=False)
    assert stub._clients_released is True
