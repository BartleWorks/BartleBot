"""BartleBot's panes, and the canvas and sidebar sizes setup fits to the launches.

The arithmetic that left core's ``game_sizing`` for ``game/pane_sizing.py`` (Stage 9 of
#167), with its tests, and the check that core's arithmetic, now reading a list of panes
and tiles, answers what it answered when it read BartleBot's constants.
"""

from __future__ import annotations

import pytest
from bartleby.runtime.game import game_sizing as gs
from bartleby.runtime.game.game_sizing import VALID_RESOLUTIONS

from bartlebot.game import pane_sizing as ps

# -- the same answers as before the panes were a list ------------------------------------
#
# The formulas below are core's ``required_window_size`` and ``screen_budget`` as they
# stood before Stage 9, written out with BartleBot's constants, which is what they read.


def _old_required_window_size(resolution, client_count, dpr, right, left):
    game_w, game_h = gs.required_game_area(resolution, client_count, dpr)
    width = left + right + game_w + 2 * gs.SPLITTER_HANDLE_PX
    height = (
        game_h + gs.GAME_PANEL_CHROME_PX + ps.BOTCANVAS_MIN_HEIGHT + gs.TITLE_BAR_PX + gs.BOTTOM_EDGE_PX
    ) + gs.SPLITTER_HANDLE_PX
    return width, height


def _old_screen_budget(screen_size, left, right, bottom):
    width = screen_size[0] - left - right - 2 * gs.SPLITTER_HANDLE_PX
    height = screen_size[1] - bottom - gs.WINDOW_CHROME_ESTIMATE_PX
    return max(0, width), max(0, height)


_LADDER_CASES = [
    (resolution, count, dpr)
    for resolution in VALID_RESOLUTIONS
    for count in range(1, 9)
    for dpr in (1.0, 1.25, 1.5, 2.0)
]
_FLOORS = [(404, 300), (437, 319)]  # the reference floors, and a machine with larger text


@pytest.mark.parametrize(("left", "right"), _FLOORS)
def test_the_window_floor_is_what_it_was_for_every_resolution_on_the_ladder(left, right):
    panes = ps.panes(left, right)
    for resolution, count, dpr in _LADDER_CASES:
        assert gs.required_window_size(resolution, count, panes, dpr) == _old_required_window_size(
            resolution, count, dpr, right, left
        ), (resolution, count, dpr)


@pytest.mark.parametrize("screen", [(1920, 1032), (2560, 1392), (3840, 2112), (1366, 728)])
@pytest.mark.parametrize(
    "sizes",
    [
        {"left_sidebar_width": 404, "right_sidebar_width": 300, "bottom_section_height": 220},
        {"left_sidebar_width": 520, "right_sidebar_width": 410, "bottom_section_height": 500},
        {"left_sidebar_width": 10, "right_sidebar_width": 10, "bottom_section_height": 10},
    ],
)
def test_the_budget_and_every_fit_on_the_ladder_are_what_they_were(screen, sizes):
    """The budget as ``SettingsManager.layout_budget`` held it, and each fit judged in it."""
    panes = ps.panes(404, 300)
    old = _old_screen_budget(
        screen,
        max(sizes["left_sidebar_width"], 404),
        max(sizes["right_sidebar_width"], 300),
        max(sizes["bottom_section_height"], ps.BOTCANVAS_MIN_HEIGHT),
    )
    assert gs.layout_budget(screen, panes, sizes) == old
    for resolution, count, dpr in _LADDER_CASES:
        need_w, need_h = gs.required_game_area(resolution, count, dpr)
        assert gs.fits(resolution, count, old, dpr) == (need_w <= old[0] and need_h <= old[1])
        assert gs.fits(resolution, [gs.TileSize.MAIN] * count, old, dpr) == gs.fits(resolution, count, old, dpr)


def test_bartlebot_s_panes_are_two_sidebars_and_the_canvas_below():
    assert [(p.name, p.side) for p in ps.panes()] == [
        ("left_sidebar_width", gs.LEFT),
        ("right_sidebar_width", gs.RIGHT),
        ("bottom_section_height", gs.BOTTOM),
    ]
    assert [p.floor for p in ps.panes()] == [ps.LEFT_SIDEBAR_MIN_WIDTH, gs.RIGHT_SIDEBAR_MIN_WIDTH, 180]


def test_the_left_floor_is_the_measured_row_plus_its_chrome_never_below_the_reference():
    assert ps.left_sidebar_floor() == ps.LEFT_SIDEBAR_MIN_WIDTH
    assert ps.left_sidebar_floor(ps.REFERENCE_FILTER_ROW_PX) == ps.LEFT_SIDEBAR_MIN_WIDTH
    assert ps.left_sidebar_floor(320) == 320 + ps.LEFT_SIDEBAR_CHROME_PX
    assert ps.left_sidebar_floor(100) == ps.LEFT_SIDEBAR_MIN_WIDTH


# -- the canvas setup fits to the launches ---------------------------------------------


def test_the_canvas_takes_what_the_tallest_grid_leaves():
    """1080p less its taskbar, one client at 800x600: 1032 - 60 of chrome - a 651px tile."""
    assert ps.canvas_height_for(1032, [((800, 600), 1)]) == 321


def test_the_tallest_of_several_launches_decides():
    launches = [((800, 600), 1), ((560, 420), 2), ((534, 300), 4)]

    assert ps.canvas_height_for(1032, launches) == 1032 - gs.WINDOW_CHROME_ESTIMATE_PX - 2 * (300 + 51)


def test_a_screen_too_small_for_its_launches_keeps_the_default_canvas():
    assert ps.canvas_height_for(700, [((800, 600), 1)]) == ps.BOTCANVAS_DEFAULT_HEIGHT


def test_no_launch_keeps_the_default_canvas():
    assert ps.canvas_height_for(1032, []) == ps.BOTCANVAS_DEFAULT_HEIGHT


def test_the_canvas_is_measured_in_logical_pixels():
    """At 150%, a 1200px-tall client is an 800-point tile."""
    assert ps.canvas_height_for(1400, [((1600, 1200), 1)], dpr=1.5) == 1400 - gs.WINDOW_CHROME_ESTIMATE_PX - 851


def test_the_sidebars_take_what_the_widest_grid_leaves_evened_up():
    """One 960x540 client on 1080p: 1920 - 2 handles - a 972px tile leaves 932 to share."""
    assert ps.sidebar_widths_for(1920, 404, 300, [((960, 540), 1)]) == (466, 466)


def test_too_little_to_even_up_goes_to_the_narrower_sidebar():
    assert ps.sidebar_widths_for(1920, 404, 300, [((560, 420), 2)]) == (404, 300 + 1920 - 704 - 16 - 1144)


def test_a_sidebar_that_cannot_grow_leaves_all_of_it_to_the_other():
    assert ps.sidebar_widths_for(1920, 404, 300, [((960, 540), 1)], grow_right=False) == (404 + 228, 300)
    assert ps.sidebar_widths_for(1920, 404, 300, [((960, 540), 1)], grow_left=False) == (404, 300 + 228)


def test_no_spare_width_or_no_launch_leaves_the_sidebars_alone():
    assert ps.sidebar_widths_for(1920, 404, 300, [((800, 600), 2)]) == (404, 300)
    assert ps.sidebar_widths_for(1920, 404, 300, []) == (404, 300)


def test_one_client_leaves_the_canvas_its_share_of_the_screen():
    assert ps.single_client_canvas_floor(1392) == 321
    assert ps.single_client_canvas_floor(700) == ps.BOTCANVAS_DEFAULT_HEIGHT
