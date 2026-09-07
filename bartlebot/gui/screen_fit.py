"""Whether a launch will fit on screen, asked from a widget.

``bartlebot/game_sizing.py`` owns the arithmetic and knows nothing about Qt or settings.
This is the thin layer that supplies it with the things only the running app has: which
screen the window is on, that screen's scale factor, how wide the user has configured
their sidebars, and — where a caller can see it — how big the game area actually is right
now. ``budget_for`` explains why the last of those is needed and why it is a *maximum*
taken with the others rather than a replacement for them.

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

That reason comes in two halves — see ``game_sizing.FitProblem``. A surface showing many
entries at once puts the numbers on each and the advice once for the lot; a surface
showing one at a time takes the whole sentence.
"""

from __future__ import annotations

from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QApplication, QCheckBox, QComboBox, QPushButton, QWidget

from bartlebot import game_sizing
from bartlebot.gui.widgets.search_field import SearchLineEdit
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


def right_sidebar_floor() -> int:
    """How wide the right sidebar has to be *on this machine* for its player card to fit.

    Always measured from the application font, never from a particular widget's, because
    two numbers derived here have to be the same number: the width ``RightSidebar``
    enforces, and the width ``layout_budget`` subtracts before deciding what may launch.
    A floor the arithmetic does not know about is the whole failure this guards against
    -- see "The floor is only a floor if the panes honour it" in docs/WINDOW_SIZING.md.

    Measured rather than a constant because the card is buttons and a scrollbar, which
    grow with the system text size. A user at 125% needs 375px for the row that fits in
    300 at 100%, and charging every 1080p user that 75px would cost them a rung of the
    ladder for a screen they are not on.

    Two measurements, because the text size is only half of it: the three tool buttons sit
    at the *style's* minimum width, which is not a font metric — the same 16px text needs
    288px of panel under ``windowsvista`` and 303 under ``windows11``. A throwaway button
    carrying the widest of the three labels reports that minimum for whatever style is in
    force, including one no Windows version here has yet.
    """
    app = QApplication.instance()
    if app is None:
        return game_sizing.RIGHT_SIDEBAR_MIN_WIDTH
    # Parentless and never shown: it exists only to be asked what a tool button costs.
    # "Never shown" is load-bearing, not incidental — a parentless QWidget *is* a
    # top-level window, so anything here that made it visible (a setVisible, a show(), a
    # style that needs it realised) would put a button-sized window on the user's screen
    # for as long as this function runs. That is exactly the flash the launch cards had:
    # see _make_quiet_label in gui/game_embed/launch_cards.py. sizeHint() alone does not
    # realise it — verified: WA_WState_Created stays False, with and without the app
    # stylesheet. If this probe ever has to be shown or realised, give it a parent.
    probe = QPushButton("Unhook")
    probe.setFont(app.font())
    return game_sizing.right_sidebar_floor(QFontMetrics(app.font()).height(), probe.sizeHint().width())


#: Spacing between the three widgets of the filter row, from ``_build_filter_row``.
_FILTER_ROW_SPACING_PX = 4


def left_sidebar_floor() -> int:
    """How wide the left sidebar has to be here for the keep-list picker's filter row to fit.

    The row is ``gui/canvas/drop_keep_list.py``'s search field, category combobox and
    "Kept only" checkbox side by side (#199) — rebuilt here from the same widget types
    rather than instantiated, because the real one needs a live bot's drop table to
    populate, and a probe that needs a bot is a probe the window cannot measure itself
    against at construction.

    Faithful to the real widgets rather than approximating them, because the whole point
    of measuring is that the answer differs by style: a bare ``QLineEdit`` stood in for the
    search field at first and reported 27px less than the real one, which was enough for
    the scale factor to stay at 1.0 under ``windows11`` and leave the floor unscaled — the
    same class of bug ``right_sidebar_floor`` records. So: the real ``SearchLineEdit`` with
    its clear button, and the widest label the combobox can carry — the longest shipped
    category name with a three-digit count, the biggest shipped bot listing 597 drops.

    The combobox and the checkbox are taken at their *natural* width and the search field
    at its minimum, which is the row's real floor rather than either size hint on its own
    — see ``REFERENCE_FILTER_ROW_PX`` for why neither hint answers this.

    Measured from the application font, never a particular widget's, for the same reason
    as ``right_sidebar_floor``: the width ``LeftSidebar`` enforces and the width
    ``layout_budget`` subtracts have to be the same number.
    """
    app = QApplication.instance()
    if app is None:
        return game_sizing.LEFT_SIDEBAR_MIN_WIDTH
    # Parentless and never shown -- see the identical note on the probe above.
    font = app.font()
    search, combo, check = SearchLineEdit("Search drops…"), QComboBox(), QCheckBox("Kept only")
    search.setClearButtonEnabled(True)
    combo.addItem("Treasure Cards (999)")
    for probe in (search, combo, check):
        probe.setFont(font)
    row = (
        search.minimumSizeHint().width()
        + combo.sizeHint().width()
        + check.sizeHint().width()
        + 2 * _FILTER_ROW_SPACING_PX
    )
    return game_sizing.left_sidebar_floor(QFontMetrics(font).height(), row)


# Left clear of the screen edges when a window is fitted to it, so the frame and the
# taskbar are never what a dialog's buttons end up behind.
WINDOW_SCREEN_MARGIN = 40


def size_within_screen(
    preferred: tuple[int, int],
    minimum: tuple[int, int],
    available: tuple[int, int],
    margin: int = WINDOW_SCREEN_MARGIN,
) -> tuple[int, int]:
    """*preferred*, shrunk to fit *available* less *margin*, but never below *minimum*.

    The minimum wins over the screen on purpose. A window squeezed under its own minimum
    is one Qt will hand back a size it cannot honour anyway, and clamping past it is a
    second way to lose the buttons rather than a fix for the first.
    """
    return (
        max(minimum[0], min(preferred[0], available[0] - margin)),
        max(minimum[1], min(preferred[1], available[1] - margin)),
    )


def budget_for(
    widget: QWidget | None = None, live_area: tuple[int, int] | None = None
) -> tuple[tuple[int, int], float]:
    """The space a game grid may use here, and the scale factor to measure it at.

    Two terms, and the larger of each axis wins:

    - the **screen** term, derived from the user's configured layout. This is the floor
      of what is always achievable rather than a description of the window right now,
      because ``GameView._update_minimum_size`` raises the view's minimum when clients
      embed and Qt grows the top-level window to satisfy it. So a launch bigger than the
      current window corrects itself, provided it fits the screen — which is exactly what
      this term guarantees.
    - the **live** term, the game area as it currently is. Sidebar widths and the canvas
      height are startup *seeds* written only by the settings dialog — nothing persists a
      splitter drag — so the configured layout can describe something the user moved away
      from hours ago. Dragging a sidebar narrower has to unlock the tier it just made room
      for, and only a live measurement sees that.

    The live term is also better arithmetic where it applies: it has the sidebars, the bot
    canvas, the menu and status bars and the game panel's own header subtracted out
    exactly, where the screen term subtracts ``MENU_AND_STATUS_PX``, an estimate.

    Taking the max rather than the live term alone is what stops a merely *small* window
    refusing a resolution the screen could carry — including at construction, before the
    window has been shown and the game view still holds a default size. Dragging a sidebar
    *wider* therefore costs nothing, which is correct: the window minimum pushes back at
    launch anyway.
    """
    screen_size, dpr = screen_metrics(widget)
    budget = SettingsManager.instance().layout_budget(
        screen_size, left_sidebar_floor=left_sidebar_floor(), right_sidebar_floor=right_sidebar_floor()
    )
    if live_area is None:
        return budget, dpr
    return (max(budget[0], live_area[0]), max(budget[1], live_area[1])), dpr


def fit_details(
    resolution: tuple[int, int],
    client_count: int,
    widget: QWidget | None = None,
    live_area: tuple[int, int] | None = None,
) -> game_sizing.FitProblem | None:
    """Why *client_count* clients at *resolution* will not fit here, or None if they will.

    Cheap enough to call on a resize: a few settings lookups and some integer arithmetic,
    with the answer only built on the failing path. Callers on a drag re-ask rather than
    caching, so it cannot go stale.

    The structured form is what the launch screen wants: it shows the numbers on the card
    and the advice once per column, rather than the whole sentence once per card. A
    surface that shows one refusal at a time wants ``fit_problem`` instead.
    """
    budget, dpr = budget_for(widget, live_area)
    return game_sizing.fit_details(resolution, client_count, budget, dpr)


def fit_problem(
    resolution: tuple[int, int],
    client_count: int,
    widget: QWidget | None = None,
    live_area: tuple[int, int] | None = None,
) -> str | None:
    """``fit_details`` as one sentence, for a tooltip or a disabled menu entry."""
    problem = fit_details(resolution, client_count, widget, live_area)
    return None if problem is None else problem.sentence


def profile_resolution(profile: dict) -> tuple[int, int]:
    """The resolution *profile* will actually launch at.

    A profile may pin its own; one that does not follows the app setting, so that is
    what its fit has to be judged against rather than a default.
    """
    pinned = profile.get("resolution")
    if pinned and len(pinned) == 2:
        return int(pinned[0]), int(pinned[1])
    return SettingsManager.instance().game_resolution


def profile_fit_details(
    profile: dict,
    widget: QWidget | None = None,
    live_area: tuple[int, int] | None = None,
) -> game_sizing.FitProblem | None:
    """Why *profile* cannot be launched on this screen, or None if it can.

    An empty profile has nothing to fit and is refused elsewhere, on its own grounds —
    ``launch_profile`` raises for it, and the card already says so — so it is not
    reported as a sizing failure here.
    """
    accounts = profile.get("accounts") or []
    if not accounts:
        return None
    return fit_details(profile_resolution(profile), len(accounts), widget, live_area)


def profile_fit_problem(
    profile: dict,
    widget: QWidget | None = None,
    live_area: tuple[int, int] | None = None,
) -> str | None:
    """``profile_fit_details`` as one sentence, for a tooltip or a disabled menu entry."""
    problem = profile_fit_details(profile, widget, live_area)
    return None if problem is None else problem.sentence


def largest_fitting_resolution(
    client_count: int,
    widget: QWidget | None = None,
    live_area: tuple[int, int] | None = None,
) -> tuple[int, int] | None:
    """The biggest offered resolution *client_count* clients fit at, or None if none do.

    Only ever used to *say* what would work. Nothing picks a resolution on the user's
    behalf: running four clients at 534x300 on a 4K screen to save the machine some work
    is a legitimate choice, so small resolutions are a performance lever rather than only
    a screen-space workaround, and nothing should nudge users toward the biggest that fits.
    """
    budget, dpr = budget_for(widget, live_area)
    return game_sizing.largest_fitting(VALID_RESOLUTIONS, client_count, budget, dpr)
