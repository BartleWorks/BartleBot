"""Whether a launch will fit on screen, asked from a widget.

``bartlebot/game_sizing.py`` owns the arithmetic and knows nothing about Qt or settings.
This is the thin layer that supplies it with the three things only the running app has:
which screen the window is on, what that screen's scale factor is, and how wide the user
has made their sidebars.

WHY THE ANSWER IS A REFUSAL RATHER THAN A WARNING

A game window is embedded at 1:1 and cannot scale, and the user must never have to
scroll to see one, so a grid that does not fit is not a degraded layout -- it is a
clipped one. Warning and proceeding, auto-detaching the overflow, or relaunching the
whole fleet smaller were all considered and dropped in favour of making the unfittable
state unreachable: nothing has to degrade gracefully into a state that cannot happen.

Both launch surfaces ask the same question against the *resulting* client count, so
adding a third client to a fitting pair is refused when the 2x2 grid it forces would not
fit at the resolution those two are already running at.

The entry that cannot launch stays visible and disabled carrying its reason. Hiding it
leaves the user hunting for a profile that has vanished with no indication why, which is
strictly worse than a greyed row that says what would bring it back.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QApplication, QWidget

from bartlebot import game_sizing
from bartlebot.settings_manager import VALID_RESOLUTIONS, SettingsManager

# Stands in for a screen that cannot be measured. Big enough that nothing is refused:
# an app that cannot see a screen must not be one that refuses to launch anything.
_UNMEASURABLE = (1 << 30, 1 << 30)


def screen_metrics(widget: QWidget | None = None) -> tuple[tuple[int, int], float]:
    """The available size and scale factor of the screen *widget* is on.

    ``availableGeometry`` rather than the full geometry, so the taskbar is not counted as
    space the window could use. A widget that is not on a screen yet — a card built
    before its window is shown — falls back to the primary one, which is where an
    un-positioned window opens anyway.
    """
    screen = widget.screen() if widget is not None else None
    if screen is None:
        screen = QApplication.primaryScreen()
    if screen is None:
        # No QScreen at all: headless, or between screen changes. An unmeasurable screen
        # must not be reported as a screen nothing fits on, so nothing is refused.
        return _UNMEASURABLE, 1.0
    available = screen.availableGeometry()
    return (available.width(), available.height()), screen.devicePixelRatio() or 1.0


def fit_problem(resolution: tuple[int, int], client_count: int, widget: QWidget | None = None) -> str | None:
    """Why *client_count* clients at *resolution* will not fit here, or None if they will."""
    screen_size, dpr = screen_metrics(widget)
    budget = SettingsManager.instance().layout_budget(screen_size)
    return game_sizing.fit_problem(resolution, client_count, budget, dpr)


def profile_resolution(profile: dict) -> tuple[int, int]:
    """The resolution *profile* will actually launch at.

    A profile may pin its own; one that does not follows the app setting, so that is
    what its fit has to be judged against rather than a default.
    """
    pinned = profile.get("resolution")
    if pinned and len(pinned) == 2:
        return int(pinned[0]), int(pinned[1])
    return SettingsManager.instance().game_resolution


def profile_fit_problem(profile: dict, widget: QWidget | None = None) -> str | None:
    """Why *profile* cannot be launched on this screen, or None if it can.

    An empty profile has nothing to fit and is refused elsewhere, on its own grounds —
    ``launch_profile`` raises for it, and the card already says so — so it is not
    reported as a sizing failure here.
    """
    accounts = profile.get("accounts") or []
    if not accounts:
        return None
    return fit_problem(profile_resolution(profile), len(accounts), widget)


def largest_fitting_resolution(client_count: int, widget: QWidget | None = None) -> tuple[int, int] | None:
    """The biggest offered resolution *client_count* clients fit at, or None if none do.

    Only ever used to *say* what would work. Nothing picks a resolution on the user's
    behalf: running four clients at 400x300 on a 4K screen to save the machine some work
    is a legitimate choice, so small resolutions are a performance lever rather than only
    a screen-space workaround, and nothing should nudge users toward the biggest that fits.
    """
    screen_size, dpr = screen_metrics(widget)
    budget = SettingsManager.instance().layout_budget(screen_size)
    return game_sizing.largest_fitting(VALID_RESOLUTIONS, client_count, budget, dpr)
