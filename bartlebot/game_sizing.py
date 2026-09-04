"""The (resolution, UI size) ladder, and whether a grid of clients fits the screen.

Wizard101's own options dropdown will not go below 800x600, but that is a UI
restriction rather than an engine limit: ``Resolution`` is a free-form ``WxH`` string
and the engine renders whatever is in it, verified down to 400x300 (#101). Two clients
at the stock floor do not fit side by side on a 1080p screen, which is the whole reason
this module exists.

WHY RESOLUTION AND UI SIZE ARE ONE SETTING

The HUD does not scale with the window -- shrink the resolution alone and the interface
stays at its original pixel size and overflows the client area. The compensation is the
game's own UI scale, which goes down to Micro, one step below anything the in-game menu
offers. So 400x300 is perfectly usable *paired with Micro* and broken at the default UI
size, and the two must never be independently selectable.

The game does not clamp UI size against resolution itself: a UI too large for the client
area is reachable in-game and simply renders broken. That very likely explains the stock
800x600 floor -- it is the lowest resolution at which the *largest* UI size still works,
so the game never has to police the pairing. Going lower is not fighting the engine; it
is relaxing a conservative floor by controlling the pairing here instead.

WHERE THE TWO VALUES LIVE

Not guessable, and the reason for the comment in ``_patch_window_config``:

    Resolution  -> preferences.xml   (it appears in config.xml too, but that copy is
                                      only a mirror and is rewritten at launch)
    UISize      -> config.xml        (absent from preferences.xml entirely)

SCREEN SPACE CAPS THE GAME SIZE

The user must never have to scroll to see a game window, so the available screen space
sets a *maximum* game size and only resolutions at or below it may be launched. That
runs screen -> game size, the inverse of the ``minimum_window_size`` chain, which
derives a window minimum from a game size already chosen. Both are kept: once the
resolution is capped to fit the screen the minimum it implies is always satisfiable, so
that chain stops being able to refuse to open and does its real job -- keeping the
window and the splitters from being dragged down to where the game gets clipped.

Nothing here is a QWidget or reads settings, so the arithmetic can be tested on its own.
"""

from __future__ import annotations

import math
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Tile chrome
# ---------------------------------------------------------------------------
# These live here rather than in client_tile.py because they are as much a term in the
# fit arithmetic as they are a drawing constant, and this module must not import a
# QWidget to know how big a tile is. client_tile.py imports them back.

FOCUS_BORDER = 6  # logical pixels of accent fill around the game window when focused
LABEL_GAP = 3  # logical pixels between the bottom of the pill and the top of the focus border
LABEL_PILL_H = 26  # conservative upper bound on pill height (max(text_h, ICON_SIZE) + 6)
LABEL_RESERVE = FOCUS_BORDER + LABEL_GAP + LABEL_PILL_H + 4  # = 39; keeps the pill clear of the border

# Menu bar plus status bar: the two strips of the main window that are neither the game
# area nor the bot canvas.
MENU_AND_STATUS_PX = 60


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


class UiSize(NamedTuple):
    """One rung: what the game calls this scale, what it stores, and its floor.

    *min_resolution* is the smallest client area whose HUD at this scale still works,
    established by driving every control at each pairing in a live client.
    """

    name: str
    value: int
    min_resolution: tuple[int, int]


# In ascending *visual* order, which is the only order anything here may use. The stored
# values are not ordered by size: Micro was appended after the original five and got the
# next free number. Nothing may sort or compare these numerically.
UI_SIZE_LADDER: tuple[UiSize, ...] = (
    UiSize("Micro", 5, (400, 300)),
    UiSize("Tiny", 0, (480, 360)),
    UiSize("Small", 1, (560, 420)),
    UiSize("Regular", 2, (640, 480)),
    UiSize("Large", 3, (720, 540)),
    UiSize("Huge", 4, (800, 600)),
)

# The five ladder steps below the stock floor. Discrete rather than freeform: every
# offered resolution maps to exactly one maximum UI size, which is what keeps the
# pairing testable.
SUB_FLOOR_RESOLUTIONS: list[tuple[int, int]] = [rung.min_resolution for rung in UI_SIZE_LADDER[:-1]]


def max_ui_size(resolution: tuple[int, int]) -> UiSize:
    """The largest UI scale whose HUD fits inside *resolution*.

    Always returns a rung: 400x300 is the floor of the resolution list, so the smallest
    one always qualifies, and anything at or above 800x600 gets Huge.
    """
    width, height = resolution
    best = UI_SIZE_LADDER[0]
    for rung in UI_SIZE_LADDER:
        min_w, min_h = rung.min_resolution
        if width >= min_w and height >= min_h:
            best = rung
    return best


def ui_size_for(preferred_value: int | None, resolution: tuple[int, int]) -> int | None:
    """The ``UISize`` to write for *resolution*, given the user's own preference.

    Clamps **down** only -- a resolution that can carry the user's preferred scale gets
    it untouched, and one that cannot gets the largest that fits. Never raises it: a
    user who plays at Small does not want Huge because their screen would allow it.

    *preferred_value* is the user's setting, not whatever is in config.xml right now,
    which after one clamped session is our own value. None (nothing has captured it)
    returns None, meaning leave the file alone: with no preference to clamp there is
    also nothing to restore, and writing a value we never read would silently replace a
    setting we would then have no way to give back.

    Returning the preferred value unchanged is load-bearing rather than a no-op. It is
    what puts the scale *back* when a later launch picks a resolution that can carry it
    -- otherwise one launch at 400x300 leaves Micro behind for every launch after it.
    """
    if preferred_value is None:
        return None
    allowed = max_ui_size(resolution)
    order = {rung.value: index for index, rung in enumerate(UI_SIZE_LADDER)}
    preferred_index = order.get(preferred_value)
    if preferred_index is None:
        # A value outside the enum: the file is not saying anything that can be honoured,
        # so take the ceiling rather than pass an unknown number back to the game.
        return allowed.value
    return preferred_value if preferred_index <= order[allowed.value] else allowed.value


# ---------------------------------------------------------------------------
# Fit
# ---------------------------------------------------------------------------


def grid_for(client_count: int) -> tuple[int, int]:
    """(rows, cols) for *client_count* tiles: 1x1, 1x2, then square-ish.

    Squarest-that-fits rather than a lookup table, so a fifth client gets a grid rather
    than sharing a cell with the fourth. It agrees with the table it replaced for every
    count that table had an entry for.
    """
    if client_count <= 0:
        return 0, 0
    cols = math.ceil(math.sqrt(client_count))
    rows = math.ceil(client_count / cols)
    return rows, cols


def tile_size(resolution: tuple[int, int], dpr: float = 1.0) -> tuple[int, int]:
    """The logical pixels one tile occupies: the game window plus its chrome.

    The game area is divided by *dpr* because a resolution is physical pixels while the
    border and the label strip are logical ones -- on a 150% display a 400px-wide game
    window is 267 logical pixels of tile.
    """
    width, height = resolution
    dpr = dpr or 1.0
    return int(width / dpr) + 2 * FOCUS_BORDER, int(height / dpr) + LABEL_RESERVE + 2 * FOCUS_BORDER


def required_game_area(resolution: tuple[int, int], client_count: int, dpr: float = 1.0) -> tuple[int, int]:
    """The logical pixels a grid of *client_count* tiles at *resolution* needs."""
    rows, cols = grid_for(client_count)
    tile_w, tile_h = tile_size(resolution, dpr)
    return cols * tile_w, rows * tile_h


def screen_budget(
    screen_size: tuple[int, int],
    left_sidebar: int,
    right_sidebar: int,
    bottom_section: int,
    splitter_handle: int,
) -> tuple[int, int]:
    """What is left of *screen_size* for the game grid once the rest of the app is placed.

    Measured against the user's *configured* layout sizes, never the minimums those
    panels can be dragged down to. Sizing against the minimums assumes the user wants
    their sidebars and bot canvas crushed to the floor to make room for a bigger game
    window, which nobody does -- it would offer a resolution that only fits as long as
    they never touch a splitter.
    """
    screen_w, screen_h = screen_size
    width = screen_w - left_sidebar - right_sidebar - 2 * splitter_handle
    height = screen_h - bottom_section - MENU_AND_STATUS_PX
    return max(0, width), max(0, height)


def fits(resolution: tuple[int, int], client_count: int, budget: tuple[int, int], dpr: float = 1.0) -> bool:
    """Whether *client_count* clients at *resolution* fit inside *budget*."""
    need_w, need_h = required_game_area(resolution, client_count, dpr)
    budget_w, budget_h = budget
    return need_w <= budget_w and need_h <= budget_h


def largest_fitting(
    resolutions: list[tuple[int, int]],
    client_count: int,
    budget: tuple[int, int],
    dpr: float = 1.0,
) -> tuple[int, int] | None:
    """The biggest of *resolutions* that fits, or None if not even the smallest does.

    "Biggest" is by area, so a wide 16:9 tier and a tall 4:3 one are ranked by how much
    game they actually show rather than by whichever axis is listed first.
    """
    fitting = [r for r in resolutions if fits(r, client_count, budget, dpr)]
    if not fitting:
        return None
    return max(fitting, key=lambda r: (r[0] * r[1], r))


def fit_problem(
    resolution: tuple[int, int],
    client_count: int,
    budget: tuple[int, int],
    dpr: float = 1.0,
) -> str | None:
    """Why *client_count* clients at *resolution* will not fit, or None if they will.

    The sentence names both numbers and both ways out. An entry that cannot launch is
    shown disabled carrying this rather than hidden: a profile that has vanished leaves
    the user hunting for it with no indication of why, which is worse than a greyed row
    that says what would bring it back.
    """
    if fits(resolution, client_count, budget, dpr):
        return None
    need_w, need_h = required_game_area(resolution, client_count, dpr)
    budget_w, budget_h = budget
    clients = f"{client_count} client{'s' if client_count != 1 else ''}"
    return (
        f"{clients} at {resolution[0]}×{resolution[1]} needs {need_w}×{need_h}; "
        f"this screen has {budget_w}×{budget_h} available — "
        f"lower the resolution or launch fewer clients."
    )
