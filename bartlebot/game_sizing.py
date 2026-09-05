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
offers. So 400x300 *renders* perfectly well paired with Micro and is broken at the
default UI size, and the two must never be independently selectable.

A rendering HUD is not the same as a reachable one, though. The interact prompt is
centred and the compass and teleporter buttons are anchored to an edge, so on a narrow
client the prompt covers them -- and those buttons are clicked by coordinate, so a
covered one swallows the click. Narrowness therefore costs ladder steps off the render
floor: none at 16:10 or wider, one at 4:3, two below that. It is why 400x300 is not
offered at all -- it is 4:3, and Micro has no step to give.

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
    """One rung: what the game calls this scale, what it stores, and its render floor.

    *min_resolution* is the smallest client area whose HUD at this scale still *renders*
    -- established by driving every control at each pairing in a live client. Below it the
    interface overflows the client area outright.

    That floor is not the whole answer, because a HUD that draws is not necessarily one
    that can be clicked; see ``ASPECT_BAND_STEPS`` for the second half.
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


# ---------------------------------------------------------------------------
# What narrowness costs
# ---------------------------------------------------------------------------
# The second floor, and the one that is not about rendering at all. The HUD anchors the
# compass and teleporter buttons to a screen edge and centres the interact prompt, so on a
# narrow client the prompt covers them -- and those buttons are clicked by coordinate
# (node_helpers.click_by_path -> wizwalker click_window), so a covered one swallows the
# click and the node runs out its timeout instead of failing loudly.
#
# The cost is measured in ladder steps off the render floor, and it depends on the aspect
# ratio *alone*. Not on size: 2048x1536 is 4:3 with 1536 lines of height and still will not
# take Huge, which rules out any model where a big enough client outgrows the problem. Ten
# pairings were driven in a live client on a sigil, and this reproduces all ten:
#
#     >= 16:10   0 steps   1128x634 Huge, 1680x1050 Huge, and every 16:9 rung
#     >= 4:3     1 step    480x360 Micro, 560x420 Tiny, 640x480 Small, 720x540 Regular,
#                          800x600 Large, 1024x768 Large, 1600x1200 Large, 2048x1536 Large
#     narrower   2 steps   1280x1024 Regular
#
# One step off the render floor is also, exactly, the next rung's render floor -- which is
# why this was first written as a per-rung "narrow floor" column and read as data. It was
# only ever one number wide, and the single entry that column could not derive was the one
# that was wrong: Huge was given a narrow floor of 1280x960, and 1600x1200 and 2048x1536
# both say no 4:3 size takes Huge at any size at all. There is no rung above Huge to step
# down from, which is the whole of it.
#
# Bands are held as integers and compared by cross-multiplication so a ratio sitting
# exactly on a boundary cannot be decided either way by a float. Ordered widest first.
#
# 16:10 is measured, not a judgement: 1680x1050 is 8:5 and takes Huge. Do not tighten it
# toward exact 16:9 -- ``widescreen_form``'s even-pixel rounding lands some rungs
# fractionally *below* 16:9 (746x420 is 1.7762), so they would misclassify as narrow. 7:4
# is the tightest safe boundary if this ever has to move.
ASPECT_BAND_STEPS: tuple[tuple[tuple[int, int], int], ...] = (
    ((16, 10), 0),
    ((4, 3), 1),
)

# What anything narrower than the last band costs. 5:4 is the only such ratio offered.
NARROW_BAND_STEPS = 2

# Retained as the name the rest of the code asks by; it is simply the first band.
WIDESCREEN_RATIO: tuple[int, int] = ASPECT_BAND_STEPS[0][0]


def scale_steps(resolution: tuple[int, int]) -> int:
    """How many ladder steps below its render floor *resolution* has to run.

    Zero for anything 16:10 or wider, where the centred prompt clears the edge-anchored
    buttons on its own and the render floor is the only thing that binds.
    """
    width, height = resolution
    for (num, den), steps in ASPECT_BAND_STEPS:
        if width * den >= height * num:
            return steps
    return NARROW_BAND_STEPS


def is_widescreen(resolution: tuple[int, int]) -> bool:
    """Whether *resolution* is wide enough for the centred prompt to clear the HUD edges."""
    return scale_steps(resolution) == 0


def widescreen_form(resolution: tuple[int, int]) -> tuple[int, int]:
    """The 16:9 form of *resolution*: the same height, widened to a 16:9 width.

    Widening rather than shortening is what lets this be offered without re-driving every
    control in a live client. A HUD cannot be broken by being handed more room on one axis
    and the same on the other, so the 16:9 form of a rung inherits the verification of that
    rung's render floor -- and, by moving the edge-anchored buttons away from the centred
    prompt, pays no narrowness steps rather than having to clear a higher floor. Holding
    the *width* and deriving the height instead (400x300 -> 400x225) would drop below the
    render floor, which is the broken pairing the ladder exists to prevent.

    The width is rounded to an even pixel. That is not only the convention every stock 16:9
    size in ``VALID_RESOLUTIONS`` follows -- 16:9 is not integral at these heights -- it is
    the game's own: fed the heights from its dropdown this returns its own entries exactly,
    634 -> 1128x634 and 990 -> 1760x990.
    """
    _, height = resolution
    return round(height * 16 / 9 / 2) * 2, height


# Every offered resolution below the stock floor: each ladder rung's 16:9 form, and the 4:3
# floors from Tiny up. Discrete rather than freeform -- every offered resolution maps to
# exactly one maximum UI size, which is what keeps the pairing testable.
#
# Micro's own 4:3 floor, 400x300, is deliberately not in here. It renders fine, but 4:3
# costs a ladder step and there is no scale below Micro to spend it, so no pairing at
# 400x300 works at all -- ``has_unoccluded_ui_size`` is the guard. The only way out at that
# height is width, and that is 534x300.
SUB_FLOOR_RESOLUTIONS: list[tuple[int, int]] = sorted(
    {rung.min_resolution for rung in UI_SIZE_LADDER[1:-1]}
    | {widescreen_form(rung.min_resolution) for rung in UI_SIZE_LADDER[:-1]}
)


def _render_floor_index(resolution: tuple[int, int]) -> int | None:
    """The highest rung *resolution* can render, or None if it is under every floor."""
    width, height = resolution
    best: int | None = None
    for index, rung in enumerate(UI_SIZE_LADDER):
        if width >= rung.min_resolution[0] and height >= rung.min_resolution[1]:
            best = index
    return best


def _reachable_index(resolution: tuple[int, int]) -> int | None:
    """The highest rung *resolution* can both draw and be clicked at, or None if there is none."""
    floor = _render_floor_index(resolution)
    if floor is None:
        return None
    index = floor - scale_steps(resolution)
    return index if index >= 0 else None


def max_ui_size(resolution: tuple[int, int]) -> UiSize:
    """The largest UI scale *resolution* can carry with the HUD both drawn and reachable.

    The render floor sets the ceiling and the aspect ratio takes steps off it, so a 4:3
    client runs one scale below what it could draw and 1024x768 takes Large rather than
    Huge. No 4:3 size takes Huge, at any size.

    Always returns a rung, including for a resolution that has no working pairing -- there
    is no scale below Micro to fall back to, so Micro is what is left. That case is not
    reachable from the offered list: ``has_unoccluded_ui_size`` is what keeps such a
    resolution out of ``VALID_RESOLUTIONS``.
    """
    index = _reachable_index(resolution)
    return UI_SIZE_LADDER[index] if index is not None else UI_SIZE_LADDER[0]


def has_unoccluded_ui_size(resolution: tuple[int, int]) -> bool:
    """Whether *any* UI scale works at *resolution*, prompt occlusion included.

    False is precisely why 400x300 is not offered -- 4:3 costs a step and Micro has none to
    give -- and it is the check a new entry in ``VALID_RESOLUTIONS`` has to pass.
    ``max_ui_size`` still answers Micro for such a resolution because it has to answer
    something; this is the question that tells the two cases apart.
    """
    return _reachable_index(resolution) is not None


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

    The sentence names both numbers and every way out. An entry that cannot launch is
    shown disabled carrying this rather than hidden: a profile that has vanished leaves
    the user hunting for it with no indication of why, which is worse than a greyed row
    that says what would bring it back.
    """
    if fits(resolution, client_count, budget, dpr):
        return None
    need_w, need_h = required_game_area(resolution, client_count, dpr)
    budget_w, budget_h = budget
    clients = f"{client_count} client{'s' if client_count != 1 else ''}"
    # Three ways out, and the third is not a screen-space one: the budget is the larger of
    # the configured layout and the live game area (see gui/screen_fit.budget_for), so
    # dragging a sidebar narrower widens it there and then.
    return (
        f"{clients} at {resolution[0]}×{resolution[1]} needs {need_w}×{need_h}; "
        f"there is {budget_w}×{budget_h} available — "
        f"lower the resolution, launch fewer clients, or make the game area larger."
    )
