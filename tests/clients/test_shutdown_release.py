"""Releasing the clients on the way out.

Closing BartleBot used to destroy every embedded game window: ``ClientTile`` re-parents
each one under the Qt window, so Qt took its children down with it, and nothing unhooked
on the way out. The fix is a quit gate — the window asks, the release runs as a task, and
the task closes the window afterwards. What ``ClientManager.shutdown`` does to the
clients is core's, tested in bartleby-core's
``tests/runtime/clients/test_client_manager_shutdown.py``; these cover what the gate decides.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from bartlebot.app_controller import AppController

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

    def _close_definitions(self) -> bool:
        return True

    def _confirm_unsaved(self, title: str) -> bool:
        return True  # the unsaved-edits question has tests of its own, in tests/test_autosave.py

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
        self._authoring = MagicMock()
        self._authoring.shutdown = AsyncMock()
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
    # Before the unhook: a boosted speed can only be written back while the hook is live.
    stub._authoring.shutdown.assert_awaited_once()
    assert stub._clients_released is True
    assert stub._shutdown_task is None


async def test_a_release_that_throws_still_lets_the_window_close(qapp):
    """Otherwise a fault in the release path leaves the app unquittable."""
    stub = _ReleaseStub(raises=True)
    await AppController._release_clients(stub, close_windows=False)
    assert stub._clients_released is True
