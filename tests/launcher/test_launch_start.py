"""Every game client the app starts is started DPI-aware.

wizwalker's ``start_instance`` started it in the app's own environment, so on a scaled
display Windows stretched it past the size it was launched at, and it never fit its tile.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from bartlebot.launcher import launch_profile
from bartlebot.shared.game_dpi import COMPAT_LAYER_ENV, HIGH_DPI_LAYER


def test_the_client_is_started_with_the_high_dpi_layer():
    install = Path(r"C:\Games\Wizard101")
    with (
        patch.object(launch_profile, "get_wiz_install", return_value=install),
        patch.object(launch_profile.subprocess, "Popen") as popen,
    ):
        launch_profile.start_instance()

    args, kwargs = popen.call_args
    assert args[0] == [str(install / "Bin" / "WizardGraphicalClient.exe"), "-L", "login.us.wizard101.com", "12000"]
    assert kwargs["cwd"] == install / "Bin"
    assert HIGH_DPI_LAYER in kwargs["env"][COMPAT_LAYER_ENV].split()
