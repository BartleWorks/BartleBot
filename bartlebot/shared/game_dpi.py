"""Whether Windows scales a game window, and the two ways the game is kept from it.

The client declares no DPI awareness, so on a display scaled above 100% Windows runs it
DPI-unaware and stretches its window: 2560x1440, asked for at 150%, is 3840x2160 on the
screen. The sizing arithmetic and embedding both take the game to draw at the pixels it was
given (``docs/WINDOW_SIZING.md``), which holds only while it is aware. Two things make it so,
each the compatibility layer :data:`HIGH_DPI_LAYER`:

- **in the environment it starts with**, :data:`COMPAT_LAYER_ENV`, which is how every launch
  of ours starts it (:func:`launch_environment`). It applies to that process alone, so the
  game the user plays outside the app is left as it was;
- **recorded for its exe** under :data:`LAYERS_KEY`, which is the "Override high DPI scaling
  behavior: Application" box on the exe's Compatibility tab. That covers every launch,
  however it is started, and changes how the game looks outside the app as well, so it is
  written only when the user asks (:func:`set_high_dpi_override`).

A process's awareness is fixed when it starts and cannot be changed from outside, so a
window that is already scaled stays so until its game is restarted.

Every registry function works under :data:`LAYERS_KEY`, read when it is called, so a test
points it at a scratch key and never changes the machine's compatibility settings.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

#: The environment variable Windows reads a process's compatibility layers from at start.
COMPAT_LAYER_ENV = "__COMPAT_LAYER"
#: The layer that makes a process DPI-aware: the Compatibility tab's "Application".
HIGH_DPI_LAYER = "HIGHDPIAWARE"
#: Where the Compatibility tab records each exe's layers for the current user.
LAYERS_KEY = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"

# The other two choices beside "Application" in the same box, "System" and "System
# (Enhanced)". A process takes one, so ours replaces either.
_DPI_LAYERS = frozenset({HIGH_DPI_LAYER, "DPIUNAWARE", "GDIDPISCALING"})
# Leads a value the Compatibility tab wrote, and stays first.
_TILDE = "~"
# DPI_AWARENESS_UNAWARE, from GetAwarenessFromDpiAwarenessContext.
_UNAWARE = 0
# The DPI of a display at 100%.
_UNSCALED_DPI = 96


def with_high_dpi_layer(layers: str | None) -> str:
    """*layers*, a space-separated list, with :data:`HIGH_DPI_LAYER` in it and no other DPI layer.

    Every other layer is kept, in its order, so a user's own "Run as administrator" or
    Windows-version layer survives.
    """
    tokens = (layers or "").split()
    lead = [_TILDE] if tokens[:1] == [_TILDE] else []
    rest = [token for token in tokens if token != _TILDE and token.upper() not in _DPI_LAYERS]
    return " ".join([*lead, *rest, HIGH_DPI_LAYER])


def has_high_dpi_layer(layers: str | None) -> bool:
    """Whether *layers* names :data:`HIGH_DPI_LAYER`, in any case."""
    return HIGH_DPI_LAYER in (layers or "").upper().split()


def launch_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment to start a game client in: *base*, or this process's, made DPI-aware."""
    env = dict(os.environ if base is None else base)
    env[COMPAT_LAYER_ENV] = with_high_dpi_layer(env.get(COMPAT_LAYER_ENV))
    return env


def is_scaled(awareness: int, monitor_dpi: int) -> bool:
    """Whether Windows stretches a window of this *awareness* on a monitor at *monitor_dpi*."""
    return awareness == _UNAWARE and monitor_dpi != _UNSCALED_DPI


def window_is_scaled(hwnd: int) -> bool:
    """Whether Windows stretches *hwnd*, whichever process it belongs to.

    False when the window is gone or the question cannot be asked, which leaves the window
    to be embedded as it always was. The monitor's DPI is the true one only because the
    app is per-monitor aware: an unaware caller is told 96 everywhere.
    """
    if sys.platform != "win32":
        return False
    import ctypes  # noqa: PLC0415 - Windows only
    from ctypes import wintypes as w  # noqa: PLC0415

    user32 = ctypes.WinDLL("user32")
    user32.GetWindowDpiAwarenessContext.argtypes = [w.HWND]
    user32.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
    user32.MonitorFromWindow.argtypes = [w.HWND, w.DWORD]
    user32.MonitorFromWindow.restype = w.HMONITOR
    shcore = ctypes.WinDLL("shcore")
    shcore.GetDpiForMonitor.argtypes = [w.HMONITOR, ctypes.c_int, ctypes.POINTER(w.UINT), ctypes.POINTER(w.UINT)]
    shcore.GetDpiForMonitor.restype = ctypes.c_long

    context = user32.GetWindowDpiAwarenessContext(hwnd)
    if not context:
        return False
    monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    dpi_x, dpi_y = w.UINT(), w.UINT()
    if not monitor or shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) != 0:
        return False
    return is_scaled(user32.GetAwarenessFromDpiAwarenessContext(context), dpi_x.value)


def window_executable(hwnd: int) -> Path | None:
    """The full path of the exe that owns *hwnd*, or None when it cannot be read."""
    if sys.platform != "win32":
        return None
    import ctypes  # noqa: PLC0415 - Windows only
    from ctypes import wintypes as w  # noqa: PLC0415

    from bartlebot.shared.game_install import process_executable  # noqa: PLC0415

    user32 = ctypes.WinDLL("user32")
    user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    user32.GetWindowThreadProcessId.restype = w.DWORD
    pid = w.DWORD()
    if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
        return None
    return process_executable(pid.value)


def _layers_of(exe: Path) -> str | None:
    import winreg  # noqa: PLC0415 - Windows only

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, LAYERS_KEY) as key:
            value, _ = winreg.QueryValueEx(key, str(exe))
    except OSError:
        return None
    return str(value)


def has_high_dpi_override(exe: Path) -> bool:
    """Whether the user's compatibility settings already make *exe* DPI-aware."""
    return has_high_dpi_layer(_layers_of(exe))


def set_high_dpi_override(exe: Path) -> None:
    """Make *exe* DPI-aware for this user, however it is started, keeping its other layers.

    The same value the Compatibility tab writes, so the user can see it and take it back
    there. Raises ``OSError`` when the key cannot be written.
    """
    import winreg  # noqa: PLC0415 - Windows only

    layers = with_high_dpi_layer(_layers_of(exe))
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, LAYERS_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, str(exe), 0, winreg.REG_SZ, layers)
    logger.info("Turned on the high-DPI override for %s", exe)
