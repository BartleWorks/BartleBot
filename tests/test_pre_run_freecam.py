"""Tests for AppController._exit_pre_run_freecam: cameras come home before the FSM starts.

The run locks Exit Freecam, so a camera left detached at Start would stay detached until
the run ended. The step must exit freecam, never teleport, and never raise -- a setup
problem degrades the run rather than cancelling it.

No QApplication: the method is bound onto a plain harness, as test_pre_run_decks does.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from bartlebot.app_controller import AppController


class FreecamHarness:
    def __init__(self, exit_freecam: AsyncMock) -> None:
        self._window = SimpleNamespace(
            right_sidebar=SimpleNamespace(clients=SimpleNamespace(exit_freecam=exit_freecam))
        )
        self._exit_pre_run_freecam = AppController._exit_pre_run_freecam.__get__(self)


async def test_players_in_freecam_are_brought_out_before_the_run():
    exit_freecam = AsyncMock(return_value=2)

    await FreecamHarness(exit_freecam)._exit_pre_run_freecam()

    exit_freecam.assert_awaited_once_with()


async def test_a_failure_to_exit_freecam_does_not_stop_the_run():
    exit_freecam = AsyncMock(side_effect=RuntimeError("boom"))

    await FreecamHarness(exit_freecam)._exit_pre_run_freecam()  # must not raise


async def test_exiting_freecam_is_the_first_pre_run_step():
    """Every later step drives clients, and assumes the camera follows the player."""
    calls: list[str] = []

    async def record(name, result=None):
        calls.append(name)
        return result

    harness = SimpleNamespace(
        _exit_pre_run_freecam=lambda: record("freecam"),
        _apply_pre_run_decks=lambda: record("decks"),
        _form_pre_run_group=lambda: record("group"),
        _survey_pre_run_ports=lambda states: record("ports"),
    )

    await AppController._run_pre_run_setup(harness)

    assert calls == ["freecam", "decks", "group", "ports"]
