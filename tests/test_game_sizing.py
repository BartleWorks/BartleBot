"""Tests for the (resolution, UI size) ladder and the screen-fit arithmetic (#101).

No QApplication and no settings: bartlebot/game_sizing.py is pure on purpose, so the
numbers behind "two clients do not fit on a 1080p screen" can be asserted directly.
"""

from __future__ import annotations

import pytest

from bartlebot import game_sizing as gs
from bartlebot.settings_manager import VALID_RESOLUTIONS

# The layout the worked examples in #101 were computed against: a 1080p screen and the
# default sidebar widths and canvas height, which give a 1304x800 game budget.
_1080P = (1920, 1080)
_DEFAULT_BUDGET = gs.screen_budget(_1080P, left_sidebar=300, right_sidebar=300, bottom_section=220, splitter_handle=8)


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


def test_the_ladder_is_in_ascending_visual_order_and_not_in_value_order():
    """Micro was appended after the original five, so the stored values do not sort.

    The whole reason nothing may compare these numerically — a sort by value would put
    Micro, the *smallest*, last.
    """
    resolutions = [rung.min_resolution for rung in gs.UI_SIZE_LADDER]
    assert resolutions == sorted(resolutions)
    assert [rung.value for rung in gs.UI_SIZE_LADDER] != sorted(rung.value for rung in gs.UI_SIZE_LADDER)
    assert gs.UI_SIZE_LADDER[0] == gs.UiSize("Micro", 5, (400, 300))


def test_every_rung_is_a_step_of_eighty_by_sixty_and_four_by_three():
    steps = [rung.min_resolution for rung in gs.UI_SIZE_LADDER]
    for (prev_w, prev_h), (next_w, next_h) in zip(steps, steps[1:]):
        assert (next_w - prev_w, next_h - prev_h) == (80, 60)
    for width, height in steps:
        assert width * 3 == height * 4


@pytest.mark.parametrize(
    ("resolution", "expected"),
    [
        ((400, 300), "Micro"),
        ((480, 360), "Tiny"),
        ((560, 420), "Small"),
        ((640, 480), "Regular"),
        ((720, 540), "Large"),
        ((800, 600), "Huge"),
        ((1920, 1080), "Huge"),  # nothing above the top rung asks for more than Huge
    ],
)
def test_max_ui_size_is_the_largest_scale_the_resolution_can_display(resolution, expected):
    assert gs.max_ui_size(resolution).name == expected


def test_a_resolution_that_carries_the_preference_leaves_it_alone():
    """Never raised: a user who plays at Small does not want Huge for having the room."""
    small = gs.UI_SIZE_LADDER[2].value
    assert gs.ui_size_for(small, (1920, 1080)) == small


def test_a_resolution_too_small_for_the_preference_clamps_it_down():
    huge = gs.UI_SIZE_LADDER[-1].value
    assert gs.ui_size_for(huge, (400, 300)) == gs.UI_SIZE_LADDER[0].value  # Micro


def test_the_preference_comes_back_when_a_later_launch_can_carry_it():
    """The case that makes ui_size_for return a value even when nothing needs clamping.

    Returning None for "no change needed" would leave Micro in config.xml for every
    launch after the first small one, because nothing else ever writes the tag.
    """
    huge = gs.UI_SIZE_LADDER[-1].value
    assert gs.ui_size_for(huge, (400, 300)) != huge
    assert gs.ui_size_for(huge, (800, 600)) == huge


def test_no_capture_means_no_write():
    """Nothing read the user's value, so there is nothing to clamp and nothing to give back."""
    assert gs.ui_size_for(None, (400, 300)) is None


def test_a_value_outside_the_enum_takes_the_ceiling():
    assert gs.ui_size_for(99, (400, 300)) == gs.UI_SIZE_LADDER[0].value


def test_the_offered_resolutions_start_at_the_ladder_floor():
    """The five sub-800x600 steps are what this issue adds, and 400x300 is the bottom."""
    assert VALID_RESOLUTIONS[:6] == [(400, 300), (480, 360), (560, 420), (640, 480), (720, 540), (800, 600)]
    assert min(VALID_RESOLUTIONS) == (400, 300)


def test_every_offered_resolution_below_the_stock_floor_is_a_ladder_rung():
    """No freeform sizes: each one maps to exactly one maximum UI size."""
    rungs = {rung.min_resolution for rung in gs.UI_SIZE_LADDER}
    assert {r for r in VALID_RESOLUTIONS if r < (800, 600)} <= rungs


# ---------------------------------------------------------------------------
# Grid and fit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "grid"),
    [(1, (1, 1)), (2, (1, 2)), (3, (2, 2)), (4, (2, 2))],
)
def test_the_grid_agrees_with_the_lookup_table_it_replaced(count, grid):
    assert gs.grid_for(count) == grid


def test_a_fifth_client_gets_its_own_cell_rather_than_sharing_one():
    """The old table fell back to 2x2 for anything over four, overlapping the extras."""
    rows, cols = gs.grid_for(5)
    assert rows * cols >= 5


def test_the_budget_is_the_screen_less_the_user_s_own_layout():
    assert _DEFAULT_BUDGET == (1304, 800)


def test_a_layout_larger_than_the_screen_leaves_no_budget_rather_than_a_negative_one():
    assert gs.screen_budget((800, 600), 600, 600, 900, 8) == (0, 0)


@pytest.mark.parametrize(
    ("clients", "expected"),
    [(1, (1280, 720)), (2, (640, 480)), (3, (400, 300)), (4, (400, 300))],
)
def test_the_largest_tier_that_fits_1080p_at_the_default_layout(clients, expected):
    """The table from #101: Micro is what a four-client 1080p user actually runs.

    Solo differs from that table, which ranked the six ladder rungs alone and so stopped
    at 800x600. The offered list is not only the ladder — one client on a 1080p screen
    has room for 1280x720, which is the answer a user would expect to be given.
    """
    assert gs.largest_fitting(VALID_RESOLUTIONS, clients, _DEFAULT_BUDGET) == expected


@pytest.mark.parametrize(
    ("clients", "expected"),
    [(1, "Huge"), (2, "Regular"), (3, "Micro"), (4, "Micro")],
)
def test_the_ui_scale_that_comes_with_the_largest_fitting_ladder_rung(clients, expected):
    """#101's table verbatim, ranked over the ladder rungs it was actually about."""
    rungs = [rung.min_resolution for rung in gs.UI_SIZE_LADDER]
    largest = gs.largest_fitting(rungs, clients, _DEFAULT_BUDGET)
    assert gs.max_ui_size(largest).name == expected


def test_the_bug_this_issue_is_about():
    """Two clients at the stock 800x600 floor do not fit a 1080p screen."""
    assert not gs.fits((800, 600), 2, _DEFAULT_BUDGET)
    assert gs.fits((640, 480), 2, _DEFAULT_BUDGET)


def test_a_tile_is_the_game_window_plus_its_chrome():
    assert gs.tile_size((800, 600)) == (800 + 12, 600 + 39 + 12)


def test_a_scaled_display_measures_the_game_area_in_logical_pixels():
    """The border and the label strip are logical; a resolution is physical."""
    plain_w, plain_h = gs.tile_size((800, 600), dpr=1.0)
    scaled_w, scaled_h = gs.tile_size((800, 600), dpr=2.0)
    assert scaled_w == 400 + 12
    assert scaled_h == 300 + 39 + 12
    assert (scaled_w, scaled_h) < (plain_w, plain_h)


def test_nothing_fits_a_screen_with_no_room_left():
    assert gs.largest_fitting(VALID_RESOLUTIONS, 4, (0, 0)) is None


# ---------------------------------------------------------------------------
# The reason
# ---------------------------------------------------------------------------


def test_a_fitting_configuration_has_no_problem_to_report():
    assert gs.fit_problem((640, 480), 2, _DEFAULT_BUDGET) is None


def test_the_reason_names_both_sizes_and_both_ways_out():
    reason = gs.fit_problem((800, 600), 2, _DEFAULT_BUDGET)
    assert reason is not None
    assert "1624×651" in reason  # what 2 clients at 800x600 need
    assert "1304×800" in reason  # what this screen has
    assert "lower the resolution" in reason
    assert "fewer clients" in reason


def test_one_client_is_not_described_as_one_clients():
    reason = gs.fit_problem((1920, 1080), 1, _DEFAULT_BUDGET)
    assert reason is not None
    assert reason.startswith("1 client at")
