"""A game client is started DPI-aware, and a user may make every start of it so.

Without either, Windows stretched a client on a scaled display past the size it was
launched at, and it never fit its tile: 2560x1440 at 150% filled a 4K screen, and the
embedded window showed nothing.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from bartlebot.shared import game_dpi
from bartlebot.shared.game_dpi import (
    COMPAT_LAYER_ENV,
    HIGH_DPI_LAYER,
    has_high_dpi_layer,
    has_high_dpi_override,
    is_scaled,
    launch_environment,
    set_high_dpi_override,
    window_is_scaled,
    with_high_dpi_layer,
)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows compatibility layers")


@pytest.fixture
def exe() -> Path:
    """A game exe of this test's own: the scratch key the suite writes under lives all session."""
    return Path(rf"C:\Games\{uuid.uuid4().hex}\Bin\WizardGraphicalClient.exe")


@pytest.mark.parametrize(
    ("layers", "expected"),
    [
        (None, "HIGHDPIAWARE"),
        ("", "HIGHDPIAWARE"),
        ("RUNASADMIN", "RUNASADMIN HIGHDPIAWARE"),
        ("~ RUNASADMIN WIN7RTM", "~ RUNASADMIN WIN7RTM HIGHDPIAWARE"),
        ("~ DPIUNAWARE", "~ HIGHDPIAWARE"),
        ("GDIDPISCALING DPIUNAWARE", "HIGHDPIAWARE"),
        ("~ HighDpiAware", "~ HIGHDPIAWARE"),
    ],
)
def test_the_layer_is_added_once_and_replaces_any_other_dpi_layer(layers, expected):
    assert with_high_dpi_layer(layers) == expected


@pytest.mark.parametrize(
    ("layers", "expected"),
    [(None, False), ("~ RUNASADMIN", False), ("~ HIGHDPIAWARE", True), ("highdpiaware", True)],
)
def test_the_layer_is_found_in_any_case(layers, expected):
    assert has_high_dpi_layer(layers) is expected


def test_the_launch_environment_adds_the_layer_and_keeps_everything_else():
    base = {"PATH": r"C:\Windows", COMPAT_LAYER_ENV: "RUNASINVOKER"}

    env = launch_environment(base)

    assert env == {"PATH": r"C:\Windows", COMPAT_LAYER_ENV: f"RUNASINVOKER {HIGH_DPI_LAYER}"}
    assert base[COMPAT_LAYER_ENV] == "RUNASINVOKER"


@pytest.mark.parametrize(
    ("awareness", "dpi", "expected"),
    [(0, 144, True), (0, 120, True), (0, 96, False), (1, 144, False), (2, 144, False)],
)
def test_only_an_unaware_window_on_a_scaled_monitor_is_scaled(awareness, dpi, expected):
    assert is_scaled(awareness, dpi) is expected


@windows_only
def test_a_window_that_is_not_there_is_not_scaled():
    assert window_is_scaled(0) is False


_REPORT_AWARENESS = (
    "import ctypes; u = ctypes.windll.user32; "
    "u.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p; "
    "u.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]; "
    "print(u.GetAwarenessFromDpiAwarenessContext(u.GetThreadDpiAwarenessContext()))"
)


def _awareness_started_with(env: dict[str, str]) -> int:
    done = subprocess.run(
        [sys.executable, "-I", "-c", _REPORT_AWARENESS], env=env, capture_output=True, text=True, timeout=30
    )
    return int(done.stdout.strip())


@windows_only
def test_windows_starts_a_process_aware_in_the_launch_environment():
    """Python declares no awareness either, so it stands in for the game client."""
    plain = {key: value for key, value in launch_environment().items() if key != COMPAT_LAYER_ENV}

    assert _awareness_started_with(plain) == 0
    assert _awareness_started_with(launch_environment(plain)) != 0


@windows_only
def test_the_override_is_off_until_it_is_set(exe):
    assert has_high_dpi_override(exe) is False

    set_high_dpi_override(exe)

    assert has_high_dpi_override(exe) is True


@windows_only
def test_setting_the_override_keeps_the_users_other_layers(exe):
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, game_dpi.LAYERS_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, str(exe), 0, winreg.REG_SZ, "~ RUNASADMIN")

    set_high_dpi_override(exe)

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, game_dpi.LAYERS_KEY) as key:
        assert winreg.QueryValueEx(key, str(exe))[0] == "~ RUNASADMIN HIGHDPIAWARE"


def test_the_suite_never_writes_the_real_layers_key():
    assert game_dpi.LAYERS_KEY != r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
