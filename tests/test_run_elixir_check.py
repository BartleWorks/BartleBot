"""The run request's elixir read (#141): a level scaling player who starts a run with an elixir already active.

The read is async and the warning is a blocking dialog, so the run sequence is split around
it: the read runs as a task and the dialogs run from its done callback, outside any task.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bartlebot.bot.schema import BotConfig
from bartlebot.gui import safety_warning_dialog
from bartlebot.nodes.utility import handle_level_scaling
from tests.conftest import FakeClient


def _config(node_type: str) -> BotConfig:
    return BotConfig.model_validate(
        {
            "id": "test-bot",
            "name": "Test Bot",
            "initial": "s0",
            "recovery_state": "s0",
            "states": [{"id": "s0", "node_type": node_type}],
            "transitions": [],
            "options": [{"name": "Level Scaling Players", "type": "player_list", "default": [1, 2]}],
        }
    )


class _Controller:
    """Hosts the three AppController methods under test, with every other pre-run check passing."""

    def __init__(self, node_type: str) -> None:
        from bartlebot.app_controller import AppController  # noqa: PLC0415

        for name in ("_on_run_requested", "_on_elixir_probe_done", "_finish_run_request"):
            setattr(self, name, getattr(AppController, name).__get__(self))
        self._client_manager = SimpleNamespace(clients=[FakeClient(), FakeClient()])
        self._loaded_config = _config(node_type)
        self._window = None
        self._elixir_probe = None
        self._bot_task = None
        self._recompose_before_run = lambda: True
        self._check_restricted_zones = lambda: True
        self._check_player_realms = lambda: True
        self._check_variables = lambda: True
        self._collect_combat_warnings = lambda: []
        self._confirm_recovery_gaps = lambda: True
        self.started = 0

    async def _start_bot(self) -> None:
        self.started += 1

    async def settle(self) -> None:
        """Let the read, its done callback and the run task all finish."""
        if self._elixir_probe is not None:
            await asyncio.wait([self._elixir_probe])
            await asyncio.sleep(0)
        if self._bot_task is not None:
            await self._bot_task


@pytest.fixture
def dialogs(monkeypatch):
    """Every safety dialog opened: its warnings, and whether a task was running when it blocked."""
    opened = SimpleNamespace(warnings=[], in_task=[], accept=True)

    class FakeDialog:
        def __init__(self, warnings, parent=None):
            opened.warnings.append(warnings)

        def exec(self) -> bool:
            opened.in_task.append(asyncio.current_task() is not None)
            return opened.accept

    monkeypatch.setattr(safety_warning_dialog, "SafetyWarningDialog", FakeDialog)
    return opened


def _elixirs(monkeypatch, result) -> AsyncMock:
    read = AsyncMock(side_effect=result) if isinstance(result, Exception) else AsyncMock(return_value=result)
    monkeypatch.setattr(handle_level_scaling, "players_with_elixir", read)
    return read


async def test_an_elixir_already_active_warns_before_the_run_and_accepting_starts_it(dialogs, monkeypatch):
    read = _elixirs(monkeypatch, [2])
    controller = _Controller("handle_level_scaling")

    controller._on_run_requested()
    await controller.settle()

    assert read.await_args.args[1] == [1, 2]
    assert [w.details for w in dialogs.warnings[0]] == [[("Elixir already active", "P2")]]
    assert controller.started == 1


async def test_the_warning_blocks_outside_any_task(dialogs, monkeypatch):
    """A blocking dialog inside a task strands every other task under Python 3.13 (gui/CLAUDE.md)."""
    _elixirs(monkeypatch, [1])
    controller = _Controller("handle_level_scaling")

    controller._on_run_requested()
    await controller.settle()

    assert dialogs.in_task == [False]


async def test_cancelling_the_warning_does_not_start_the_run(dialogs, monkeypatch):
    _elixirs(monkeypatch, [1])
    dialogs.accept = False
    controller = _Controller("handle_level_scaling")

    controller._on_run_requested()
    await controller.settle()

    assert controller.started == 0


async def test_no_elixir_active_starts_the_run_with_no_dialog(dialogs, monkeypatch):
    _elixirs(monkeypatch, [])
    controller = _Controller("handle_level_scaling")

    controller._on_run_requested()
    await controller.settle()

    assert dialogs.warnings == []
    assert controller.started == 1


async def test_a_bot_that_never_level_scales_reads_no_elixir(dialogs, monkeypatch):
    read = _elixirs(monkeypatch, [1])
    controller = _Controller("sleep")

    controller._on_run_requested()
    await controller.settle()

    read.assert_not_awaited()
    assert controller._elixir_probe is None
    assert controller.started == 1


async def test_a_read_that_fails_still_starts_the_run(dialogs, monkeypatch):
    """The warning is advice. A client that cannot be read must not make the bot unrunnable."""
    _elixirs(monkeypatch, RuntimeError("memory read failed"))
    controller = _Controller("handle_level_scaling")

    controller._on_run_requested()
    await controller.settle()

    assert dialogs.warnings == []
    assert controller.started == 1


async def test_a_second_press_during_the_read_does_not_start_two_runs(dialogs, monkeypatch):
    _elixirs(monkeypatch, [])
    controller = _Controller("handle_level_scaling")

    controller._on_run_requested()
    controller._on_run_requested()
    await controller.settle()

    assert controller.started == 1
