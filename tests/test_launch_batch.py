"""launch_accounts: one batch of instances, logged in and driven together.

The shape is the reason a team launch — and, since #163, a fleet relog's recovery — costs
roughly one launch rather than one per client. Each drive spends minutes waiting on
loading screens and character select, so a batch that advanced them in turn would give
back everything the batch is for.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bartlebot.launcher.launch_profile import launch_accounts

ACCOUNTS = [
    ("alpha", "pw1", "a***a"),
    ("beta", "pw2", "b***a"),
    ("gamma", "pw3", "g***a"),
]


@pytest.fixture
def launcher():
    """Patch out everything that touches Windows or the game install."""
    events: list[str] = []
    settings = MagicMock(game_resolution=(1280, 720))
    with (
        patch("bartlebot.launcher.launch_profile.patch_open_browser"),
        patch("bartlebot.launcher.launch_profile._patch_window_config") as config,
        patch("bartlebot.launcher.launch_profile.get_all_wizard_handles", return_value=[]),
        patch(
            "bartlebot.launcher.launch_profile._wait_for_n_new_handles",
            AsyncMock(return_value=[11, 12, 13]),
        ),
        patch("bartlebot.launcher.launch_profile._wait_for_login_screen", AsyncMock()),
        patch("bartlebot.launcher.launch_profile.start_instance", lambda: events.append("start")),
        patch("bartlebot.launcher.launch_profile.instance_login") as login,
        patch("bartlebot.launcher.launch_profile.set_window_title"),
        patch("bartlebot.launcher.launch_profile.jitter", return_value=0.0),
        patch("bartlebot.launcher.launch_profile.SettingsManager") as sm,
    ):
        sm.instance.return_value = settings
        yield MagicMock(events=events, config=config, login=login)


async def test_no_accounts_launches_nothing(launcher) -> None:
    assert await launch_accounts([]) == []
    assert launcher.events == []
    launcher.config.assert_not_called()


async def test_every_account_gets_an_instance_and_a_title(launcher) -> None:
    titles = await launch_accounts(ACCOUNTS)

    assert launcher.events == ["start", "start", "start"]
    assert titles == ["[a***a] Wizard101", "[b***a] Wizard101", "[g***a] Wizard101"]
    assert [call.args[1] for call in launcher.login.call_args_list] == ["alpha", "beta", "gamma"]


async def test_the_batch_shares_one_config_patch(launcher) -> None:
    """The game reads config.xml at its own startup, and there is only one of it."""
    await launch_accounts(ACCOUNTS, resolution=(800, 600))

    launcher.config.assert_called_once()
    assert launcher.config.call_args.args[:2] == (800, 600)


async def test_no_resolution_falls_back_to_the_app_setting(launcher) -> None:
    await launch_accounts(ACCOUNTS)

    assert launcher.config.call_args.args[:2] == (1280, 720)


async def test_the_drives_overlap(launcher) -> None:
    """Started as tasks, so each window advances while the others are still waiting."""
    in_flight = 0
    peak = 0

    async def _drive(title: str) -> bool:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return True

    await launch_accounts(ACCOUNTS, drive=_drive)

    assert peak == 3


async def test_every_drive_is_awaited_before_returning(launcher) -> None:
    """A drive still running when this returns would have the caller believe a client is
    in-game while it is still on a loading screen."""
    finished: list[str] = []

    async def _drive(title: str) -> bool:
        await asyncio.sleep(0.01)
        finished.append(title)
        return True

    await launch_accounts(ACCOUNTS, drive=_drive)

    assert len(finished) == 3
