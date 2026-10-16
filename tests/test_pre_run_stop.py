"""Tests for stopping a bot during its pre-run setup.

A user who presses Stop seconds after Start has usually spotted something to fix, such as
the client order or a gear set. Stop used to reach only the machine, which nothing reads
until the first node, so every deck apply and the whole group formation ran to the end
before the bot stopped. Now Stop cancels the setup and the run never begins.

No QApplication: _start_bot and _stop_bot are bound onto a harness whose window, sidebars
and the rest are mocks.
"""

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bartlebot import app_controller
from bartlebot.app_controller import AppController


class _Machine:
    def __init__(self, **_kwargs):
        self.run = AsyncMock()
        self.is_stopped = False

    def stop(self) -> None:
        self.is_stopped = True


class _Harness:
    """Any attribute _start_bot touches that a test does not set is a mock."""

    def __init__(self) -> None:
        self._start_bot = AppController._start_bot.__get__(self)
        self._stop_bot = AppController._stop_bot.__get__(self)
        self._pre_run = None
        self._current_machine = None
        self._client_manager = MagicMock(clients=[MagicMock()])
        self._authoring = MagicMock(suspend_for_run=AsyncMock())
        self.setup_started = asyncio.Event()
        self.setup_release = asyncio.Event()
        self.setup_cancelled = False
        self.setup_finished = False

    def __getattr__(self, name):
        mock = MagicMock()
        setattr(self, name, mock)
        return mock

    async def _run_pre_run_setup(self) -> None:
        self.setup_started.set()
        try:
            await self.setup_release.wait()
        except asyncio.CancelledError:
            self.setup_cancelled = True
            raise
        self.setup_finished = True


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """The machine, the wake lock and the log bridge, none of which this is about."""
    monkeypatch.setattr(app_controller, "BotMachine", _Machine)
    # wakepy is imported as the run starts, so the patch is on the module it comes from.
    monkeypatch.setattr("wakepy.keep", SimpleNamespace(running=lambda **_k: contextlib.nullcontext()))
    monkeypatch.setattr(app_controller, "add_gui_callback", lambda _cb: None)
    monkeypatch.setattr(app_controller, "remove_gui_callback", lambda _cb: None)


async def test_stop_during_the_setup_cancels_it_and_the_run_never_begins():
    harness = _Harness()
    task = asyncio.create_task(harness._start_bot())
    await harness.setup_started.wait()
    machine = harness._current_machine

    harness._stop_bot()
    await asyncio.wait_for(task, timeout=1.0)

    assert harness.setup_cancelled
    assert not harness.setup_finished
    machine.run.assert_not_awaited()
    assert harness._current_machine is None  # torn down as after any run
    harness._client_manager.set_machine.assert_called_with(None)


async def test_without_a_stop_the_setup_finishes_and_the_run_begins():
    harness = _Harness()
    task = asyncio.create_task(harness._start_bot())
    await harness.setup_started.wait()
    machine = harness._current_machine

    harness.setup_release.set()
    await asyncio.wait_for(task, timeout=1.0)

    assert harness.setup_finished
    machine.run.assert_awaited_once()


async def test_a_stop_after_the_setup_but_before_the_run_still_stops():
    harness = _Harness()
    task = asyncio.create_task(harness._start_bot())
    await harness.setup_started.wait()
    machine = harness._current_machine

    harness.setup_release.set()
    harness._current_machine.stop()  # lands while the setup is returning
    await asyncio.wait_for(task, timeout=1.0)

    machine.run.assert_not_awaited()


async def test_cancelling_the_bot_task_itself_still_propagates():
    """Only the setup's own cancel is swallowed; a cancel of the whole run is not."""
    harness = _Harness()
    task = asyncio.create_task(harness._start_bot())
    await harness.setup_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert harness._current_machine is None
