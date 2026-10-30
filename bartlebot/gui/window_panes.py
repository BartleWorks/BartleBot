"""BartleBot's panes as the fit sees them, and the layout setup fits them to the screen.

Core's ``bartleby.qt.screen_fit`` asks the window's panes of a function each product hands
it (``screen_fit.configure``); :func:`panes` is BartleBot's, with the left sidebar's floor
measured here and the right one's measured by core. The rest was BartleBot's own part of
``gui/screen_fit.py`` before that moved to core (Stage 9 of #167): setup's game area, the
resolution it starts a launch at, and the layout it fits to what the user can launch, all
three about BartleBot's bot canvas and its two sidebars.
"""

from __future__ import annotations

from typing import NamedTuple

from bartleby.qt import screen_fit
from bartleby.runtime.game import game_sizing
from bartleby.runtime.game.game_sizing import VALID_RESOLUTIONS, Pane
from PyQt6.QtWidgets import QApplication, QCheckBox, QComboBox, QWidget

from bartlebot.game import pane_sizing
from bartlebot.game.pane_sizing import BOT_CANVAS, LEFT_SIDEBAR, RIGHT_SIDEBAR
from bartlebot.gui.widgets.inputs.search_field import SearchLineEdit
from bartlebot.settings_manager import SettingsManager


def panes() -> list[Pane]:
    """BartleBot's three panes, each sidebar at the floor it measures on this machine."""
    return pane_sizing.panes(left_sidebar_floor(), screen_fit.right_sidebar_floor())


#: Spacing between the three widgets of the filter row, from ``_build_filter_row``.
_FILTER_ROW_SPACING_PX = 4


def left_sidebar_floor() -> int:
    """How wide the left sidebar has to be here for the keep-list picker's filter row to fit.

    The row is ``gui/canvas/drop_keep_list.py``'s search field, category combobox and
    "Kept only" checkbox side by side — rebuilt here from the same widget types
    rather than instantiated, because the real one needs a live bot's drop table to
    populate, and a probe that needs a bot is a probe the window cannot measure itself
    against at construction.

    Faithful to the real widgets rather than approximating them, because the whole point
    of measuring is that the answer differs by style: a bare ``QLineEdit`` stood in for the
    search field at first and reported 27px less than the real one, which was enough for
    the scale factor to stay at 1.0 under ``windows11`` and leave the floor unscaled — the
    same class of bug core's ``right_sidebar_floor`` records. So: the real ``SearchLineEdit`` with
    its clear button, and the widest label the combobox can carry — the longest shipped
    category name with a three-digit count, the biggest shipped bot listing 597 drops.

    The combobox and the checkbox are taken at their *natural* width and the search field
    at its minimum, which is the row's real floor rather than either size hint on its own
    — see ``REFERENCE_FILTER_ROW_PX`` for why neither hint answers this.

    Measured from the application font, never a particular widget's, for the same reason
    as core's ``right_sidebar_floor``: the width ``LeftSidebar`` enforces and the width
    the left ``Pane`` lists have to be the same number.
    """
    app = QApplication.instance()
    if app is None:
        return pane_sizing.LEFT_SIDEBAR_MIN_WIDTH
    return screen_fit.measured_floor("left", app, lambda: _measure_left_sidebar_floor(app))


def _measure_left_sidebar_floor(app: QApplication) -> int:
    # Parentless and never shown -- see the identical note on core's right_sidebar_floor probe.
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
    return pane_sizing.left_sidebar_floor(row)


def setup_area(widget: QWidget | None = None) -> tuple[int, int]:
    """The game area setup judges a resolution against: the panes at their smallest setup size.

    The sidebars at their floors and the canvas no taller than ``BOTCANVAS_DEFAULT_HEIGHT``.
    Not the panes as configured: setup fits those to the launches it knows
    (``fitted_layout``), so panes already fitted to one account would otherwise leave a
    four-account profile no resolution at all. Passed to the fit questions as their live
    area, the larger term ``budget_for`` takes.
    """
    screen_size, _ = screen_fit.screen_metrics(widget)
    sm = SettingsManager.instance()
    height = min(
        int(sm.get_general(BOT_CANVAS, default=pane_sizing.BOTCANVAS_DEFAULT_HEIGHT)),
        pane_sizing.BOTCANVAS_DEFAULT_HEIGHT,
    )
    # The sidebars are left out of the sizes, so they are taken at their floors.
    canvas = max(height, pane_sizing.BOTCANVAS_MIN_HEIGHT)
    return game_sizing.screen_budget(screen_size, panes(), {BOT_CANVAS: canvas})


def setup_resolution(client_count: int, widget: QWidget | None = None) -> tuple[int, int] | None:
    """The resolution setup gives a launch of *client_count* accounts: the largest that fits.

    A starting value the user sees and can change, not a nudge toward the biggest (see
    ``largest_fitting_resolution``): 800x600 is a profile that two accounts on a 1080p
    screen cannot launch at all, and a single client a quarter of a 4K screen.
    """
    return screen_fit.largest_fitting_resolution(client_count, widget, setup_area(widget))


class FittedLayout(NamedTuple):
    """What setup fits to the screen: the global resolution, and each pane's size by its settings key."""

    resolution: tuple[int, int]
    panes: dict[str, int]


def fitted_layout(widget: QWidget | None = None) -> FittedLayout | None:
    """The global resolution and pane sizes that fit the screen to what the user can launch.

    The game area keeps the tallest and widest grid the user can launch, and the panes
    setup may still fit take the rest: the canvas the height (``canvas_height_for``), the
    sidebars the width, evenly (``sidebar_widths_for``). A pane the user sized in Settings
    keeps its size. A launch that does not fit ``setup_area`` is left out, since it cannot
    launch whatever the panes do.

    The launch profiles with a resolution of their own decide the layout first, since a
    user with one runs it more than a single account. The global resolution, while it is
    still setup's to choose, is then the largest that fits in the game area they leave, for
    one client and for every profile following it. With no such profile it is the largest
    that fits and still leaves the canvas ``single_client_canvas_floor``, and decides the
    layout itself; on a screen where none does, simply the largest that fits. Fitted the
    other way round, the
    largest single client would be the tallest grid on every screen and squeeze the canvas
    of every user with a profile. None for a screen that cannot be measured.
    """
    screen_size, dpr = screen_fit.screen_metrics(widget)
    if screen_size == screen_fit.UNMEASURABLE:
        return None
    sm = SettingsManager.instance()
    area = setup_area(widget)
    pinned: list[tuple[tuple[int, int], int]] = []
    following = {1}
    for profile in sm.account_store.launch_profiles:
        count = len(profile.get("accounts") or [])
        if not count:
            continue
        if profile.get("resolution"):
            resolution = screen_fit.profile_resolution(profile)
            if game_sizing.fits(resolution, count, area, dpr):
                pinned.append((resolution, count))
        else:
            following.add(count)

    grow_canvas = sm.layout_follows_setup(BOT_CANVAS)
    grow_left = sm.layout_follows_setup(LEFT_SIDEBAR)
    grow_right = sm.layout_follows_setup(RIGHT_SIDEBAR)

    def panes_for(launches: list[tuple[tuple[int, int], int]]) -> dict[str, int]:
        canvas = int(sm.get_general(BOT_CANVAS))
        if grow_canvas:
            canvas = pane_sizing.canvas_height_for(screen_size[1], launches, dpr)
        left = left_sidebar_floor() if grow_left else int(sm.get_general(LEFT_SIDEBAR))
        right = screen_fit.right_sidebar_floor() if grow_right else int(sm.get_general(RIGHT_SIDEBAR))
        left, right = pane_sizing.sidebar_widths_for(screen_size[0], left, right, launches, dpr, grow_left, grow_right)
        return {BOT_CANVAS: canvas, LEFT_SIDEBAR: left, RIGHT_SIDEBAR: right}

    def largest_for_following(budget: tuple[int, int]) -> tuple[int, int] | None:
        fitting = [r for r in VALID_RESOLUTIONS if all(game_sizing.fits(r, n, budget, dpr) for n in following)]
        return max(fitting, key=lambda r: (r[0] * r[1], r), default=None)

    resolution = sm.game_resolution
    if sm.resolution_follows_setup:
        chosen = None
        if pinned:
            left_by_pinned = game_sizing.screen_budget(screen_size, panes(), panes_for(pinned))
            chosen = largest_for_following(left_by_pinned)
        else:
            room_for_canvas = game_sizing.screen_budget(
                screen_size, panes(), {BOT_CANVAS: pane_sizing.single_client_canvas_floor(screen_size[1])}
            )
            chosen = largest_for_following(room_for_canvas)
        resolution = chosen or largest_for_following(area) or resolution
    launches = pinned + [(resolution, n) for n in sorted(following) if game_sizing.fits(resolution, n, area, dpr)]
    sizes = panes_for(launches)
    keys = [(BOT_CANVAS, grow_canvas), (LEFT_SIDEBAR, grow_left), (RIGHT_SIDEBAR, grow_right)]
    return FittedLayout(resolution, {key: sizes[key] for key, grows in keys if grows})
