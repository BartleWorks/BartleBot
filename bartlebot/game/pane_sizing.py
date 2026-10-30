"""BartleBot's panes beside and below the game area, and the sizes setup fits them to.

Moved out of bartleby-core's ``game_sizing`` (Stage 9 of #167), which now reads a list of
``Pane``s where it read these constants: BartleBot's window is the game area with a left
sidebar, a right sidebar and the bot canvas below, and this module is that list
(:func:`panes`). The arithmetic stays core's. What is here is what only BartleBot's window
has: the canvas, the left sidebar's floor, and how setup shares out what the game grid
leaves of the screen.

Nothing here is a QWidget or reads settings, so the arithmetic can be tested on its own,
as core's can. ``gui/window_panes.py`` measures the floors and reads the settings.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from bartleby.runtime.game.game_sizing import (
    BOTTOM,
    LEFT,
    RIGHT,
    RIGHT_SIDEBAR_MIN_WIDTH,
    SPLITTER_HANDLE_PX,
    WINDOW_CHROME_ESTIMATE_PX,
    Pane,
    required_game_area,
)

#: Each pane's settings key, which is also its ``Pane.name``.
LEFT_SIDEBAR = "left_sidebar_width"
RIGHT_SIDEBAR = "right_sidebar_width"
BOT_CANVAS = "bottom_section_height"

# Minimum BotCanvas size (toolbar + enough graph space)
BOTCANVAS_MIN_WIDTH = 320
BOTCANVAS_MIN_HEIGHT = 180
# The canvas height a new install opens at, before setup fits it to the launches the user
# has (``canvas_height_for``). Also the height setup judges a new profile's resolution at,
# so a canvas already fitted to one account cannot crowd out the profile for four.
BOTCANVAS_DEFAULT_HEIGHT = 220
# The share of the screen's height a single client leaves the canvas, at least, when setup
# fits the global resolution with no launch profile deciding the layout. Without it the
# largest resolution one client fits wins however little it leaves: 1680x1050 on a 1440p
# screen kept a 231px canvas, where 1600x900 keeps 381. 23% rather than a round 25%
# because 4K at 150% keeps 2560x1440 with 329px of 1400 (23.5%); at 25% it fell to
# 1920x1200, a much smaller game for a canvas it did not need. The share is of the usable
# height, after the taskbar, and that margin is thin: 24%, or a taller taskbar, tips 4K at
# 150% down a rung. tests/gui/test_window_panes.py pins both screens, so retuning this
# means deciding what each of them should get.
SINGLE_CLIENT_CANVAS_SHARE = 0.23
# The left sidebar's floor is *measured*, as the right one's is (core's
# ``right_sidebar_floor``): its widest row is the quicksell keep-list picker's filter row --
# a search field, a category combobox and a "Kept only" checkbox side by side. Unlike the
# rest of the surface, whose labels are ``Fixed``-width but short and whose prose wraps,
# that row's three widgets neither wrap nor elide, so a narrow sidebar overlapped them
# instead of squeezing or scrolling to them -- what a 220px sidebar was doing in the report.
#
# The constant is the floor *at the reference metrics* (16px text under ``windowsvista``),
# and ``left_sidebar_floor`` re-derives it from what the panel measures here, never below
# the reference. Pinning a pixel count would mean choosing between clipping a machine with
# larger system text and charging every other one for it -- on a 1080p screen an extra
# 80px of sidebar costs a whole rung of the ladder. Core's "The panes and their floors"
# says why a floor is the forcing row plus the chrome around it, and only the row is
# measured.
LEFT_SIDEBAR_MIN_WIDTH = 404  # a measured 392 plus the 12px of headroom 390 had over 378:
# the filter row plus the gutter, the option card's border and padding, the
# scroll content margins, the scroll area's frame and its vertical scrollbar. The headroom
# is what keeps ``windows11`` and the classic ``Windows`` style, whose frames and
# scrollbars are wider than the reference style's, inside the floor -- the classic one
# with 2px to spare, which is why the card's 14px went onto the floor whole. It was 390 until the options moved into folding cards (#138),
# which added the card's 1px border and 6px padding on each side.
# Not the row's ``sizeHint``, though, and not its ``minimumSizeHint`` either: the
# combobox and the checkbox are what must not be squeezed, so they are taken at their
# *natural* width, while the search field beside them is the one holding the layout's
# stretch and is taken at its own minimum -- it is designed to be the part that gives.
# ``minimumSizeHint`` on the row would take the combobox down with it (a QComboBox elides
# its own text and reports far less than it needs), which is the clipped state.
# 293 is that sum at the reference metrics; see ``left_sidebar_floor``.
REFERENCE_FILTER_ROW_PX = 293
# What the left sidebar spends *around* its forcing row -- the gutter, the option card's
# inset, the scroll content margins, the frame and the scroll bar. Style metrics and fixed
# spacings, not text, so they are added rather than scaled: the difference between the
# reference floor and its reference row.
LEFT_SIDEBAR_CHROME_PX = LEFT_SIDEBAR_MIN_WIDTH - REFERENCE_FILTER_ROW_PX  # 111


def left_sidebar_floor(filter_row_px: float | None = None) -> int:
    """How wide the left sidebar has to be for the widest row it cannot shrink.

    Same shape as core's ``right_sidebar_floor``: the forcing content is not the whole
    panel, it is one row that neither wraps nor elides -- the quicksell keep-list picker's
    search field, category combobox and "Kept only" checkbox, composed as
    ``REFERENCE_FILTER_ROW_PX`` describes and measured by
    ``gui/window_panes.left_sidebar_floor``. ``LEFT_SIDEBAR_CHROME_PX`` is the gutter, the
    option card's inset, the scroll content margins, the frame and the scroll bar, added
    rather than scaled.

    Everything else on this surface elides or wraps by design, so it sets no floor. That
    is why there is no text-height term any more: it was a proxy for content that gives
    way, applied to chrome that does not move.

    ``None`` means "not measured" and returns the reference, as on the right.
    """
    if filter_row_px is None:
        return LEFT_SIDEBAR_MIN_WIDTH
    return max(LEFT_SIDEBAR_MIN_WIDTH, round(filter_row_px + LEFT_SIDEBAR_CHROME_PX))


def panes(left_floor: int = LEFT_SIDEBAR_MIN_WIDTH, right_floor: int = RIGHT_SIDEBAR_MIN_WIDTH) -> list[Pane]:
    """BartleBot's three panes, at the sidebar floors given: the reference ones when not measured."""
    return [
        Pane(LEFT_SIDEBAR, left_floor, LEFT),
        Pane(RIGHT_SIDEBAR, right_floor, RIGHT),
        Pane(BOT_CANVAS, BOTCANVAS_MIN_HEIGHT, BOTTOM),
    ]


def canvas_height_for(screen_h: int, launches: Iterable[tuple[tuple[int, int], int]], dpr: float = 1.0) -> int:
    """The bot canvas height that leaves *screen_h* exactly the tallest grid of *launches*.

    Each launch is ``(resolution, client_count)``. The inverse of ``screen_budget``'s
    height: everything the tallest grid does not need goes to the canvas rather than
    sitting empty around the tiles, which is most of a 1080p screen for the one or two
    accounts most users have. Never below ``BOTCANVAS_DEFAULT_HEIGHT``, so a screen too
    small for its launches keeps the layout it always had rather than losing canvas too,
    and so does an empty *launches*, which has no grid to measure.
    """
    tallest = max((required_game_area(resolution, count, dpr)[1] for resolution, count in launches), default=None)
    if tallest is None:
        return BOTCANVAS_DEFAULT_HEIGHT
    return max(BOTCANVAS_DEFAULT_HEIGHT, screen_h - WINDOW_CHROME_ESTIMATE_PX - tallest)


def single_client_canvas_floor(screen_h: int) -> int:
    """The canvas height setup leaves at least, fitting one client to *screen_h* (``SINGLE_CLIENT_CANVAS_SHARE``)."""
    return max(BOTCANVAS_DEFAULT_HEIGHT, math.ceil(SINGLE_CLIENT_CANVAS_SHARE * screen_h))


def sidebar_widths_for(
    screen_w: int,
    left: int,
    right: int,
    launches: Iterable[tuple[tuple[int, int], int]],
    dpr: float = 1.0,
    grow_left: bool = True,
    grow_right: bool = True,
) -> tuple[int, int]:
    """The sidebar widths that leave *screen_w* exactly the widest grid of *launches*.

    The width-wise twin of ``canvas_height_for``. *left* and *right* are the widths to
    start from, and the width the widest grid does not need goes to the sides allowed to
    grow. With both, it evens them up first, the narrower taking it until they match, and
    shares what is left equally: two sidebars of one width look finished where a wide and
    a narrow one look accidental. A screen with nothing to spare, or an empty *launches*,
    leaves both as they are.
    """
    widest = max((required_game_area(resolution, count, dpr)[0] for resolution, count in launches), default=None)
    if widest is None:
        return left, right
    spare = screen_w - left - right - 2 * SPLITTER_HANDLE_PX - widest
    if spare <= 0:
        return left, right
    if grow_left and grow_right:
        total = left + right + spare
        if total // 2 >= max(left, right):
            return total // 2, total - total // 2
        return (left + spare, right) if left < right else (left, right + spare)
    if grow_left:
        return left + spare, right
    if grow_right:
        return left, right + spare
    return left, right
