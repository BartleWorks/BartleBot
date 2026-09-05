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


# Every (resolution, UI size) pairing driven in a live client on a sigil, checking whether
# the centred interact prompt covered the compass and teleporter buttons. This is the table
# the model exists to reproduce; if a change here and a measurement disagree, the
# measurement wins. Do not add a row that was reasoned rather than driven -- 1280x1024 was
# recorded as Huge on the reasoning that 5:4 was "close enough to 4:3", and it is Regular.
MEASURED_PAIRINGS = [
    # 16:10 and wider: no step, the render floor is the whole answer.
    ((534, 300), "Micro"),
    ((640, 360), "Tiny"),
    ((746, 420), "Small"),
    ((854, 480), "Regular"),
    ((960, 540), "Large"),
    ((1128, 634), "Huge"),  # the smallest 16:9 the game's own dropdown offers
    ((1280, 720), "Huge"),
    ((1680, 1050), "Huge"),  # 8:5 -- what makes the 16:10 band boundary a measurement
    ((1920, 1080), "Huge"),  # nothing above the top rung asks for more than Huge
    # 4:3: one step.
    ((480, 360), "Micro"),
    ((560, 420), "Tiny"),
    ((640, 480), "Small"),
    ((720, 540), "Regular"),
    ((800, 600), "Large"),
    ((1024, 768), "Large"),
    ((1600, 1200), "Large"),
    ((2048, 1536), "Large"),
    # Narrower than 4:3: two steps.
    ((1280, 1024), "Regular"),
]


@pytest.mark.parametrize(("resolution", "expected"), MEASURED_PAIRINGS)
def test_the_model_reproduces_every_measured_pairing(resolution, expected):
    assert gs.max_ui_size(resolution).name == expected


@pytest.mark.parametrize(
    ("resolution", "expected_steps"),
    [
        ((1920, 1080), 0),  # 16:9
        ((1680, 1050), 0),  # 8:5, measured
        ((1024, 768), 1),  # 4:3
        ((1280, 1024), 2),  # 5:4
    ],
)
def test_narrowness_is_priced_in_ladder_steps(resolution, expected_steps):
    assert gs.scale_steps(resolution) == expected_steps


def test_a_four_by_three_client_runs_one_scale_below_what_it_could_draw():
    """The occlusion step, stated on its own: 640x480 *renders* Regular perfectly well."""
    for resolution in [(480, 360), (560, 420), (640, 480), (720, 540), (800, 600)]:
        drawn = gs.max_ui_size(gs.widescreen_form(resolution)).name
        clicked = gs.max_ui_size(resolution).name
        names = [rung.name for rung in gs.UI_SIZE_LADDER]
        assert names.index(clicked) == names.index(drawn) - 1


def test_no_four_by_three_resolution_reaches_huge_at_any_size():
    """2048x1536 is the one that settles it: the cap is the aspect ratio, not the size.

    This is what the old per-rung "narrow floor" column got wrong. Huge was given a floor
    of 1280x960, which made Huge look reachable at 4:3 given enough pixels. There is no
    rung above Huge to step down from, so there is nothing to reach it with.
    """
    for resolution in [(800, 600), (1024, 768), (1280, 960), (1600, 1200), (2048, 1536), (4096, 3072)]:
        assert gs.max_ui_size(resolution).name != "Huge", resolution


def test_sixteen_by_ten_counts_as_widescreen():
    """Measured, not assumed: 1680x1050 is 8:5 and takes Huge with the prompt clear.

    The boundary must not be tightened toward exact 16:9. widescreen_form rounds to an
    even pixel, which lands 746x420 at 1.7762 -- fractionally *under* 16:9 -- so an exact
    16:9 test would price it as narrow and cost it a scale it does not owe.
    """
    assert gs.is_widescreen((1680, 1050))
    assert gs.max_ui_size((1680, 1050)).name == "Huge"
    assert gs.is_widescreen((746, 420))
    assert not gs.is_widescreen((1280, 1024))


def test_a_resolution_with_no_working_pairing_is_recognised_as_such():
    """400x300 renders at Micro and is still unusable: nothing steps down far enough."""
    assert not gs.has_unoccluded_ui_size((400, 300))
    assert gs.has_unoccluded_ui_size((534, 300))  # the same height, widened until it clears
    assert gs.has_unoccluded_ui_size((480, 360))
    # It still answers something, because it has to -- which is why the guard is separate.
    assert gs.max_ui_size((400, 300)).name == "Micro"


def test_a_resolution_that_carries_the_preference_leaves_it_alone():
    """Never raised: a user who plays at Small does not want Huge for having the room."""
    small = gs.UI_SIZE_LADDER[2].value
    assert gs.ui_size_for(small, (1920, 1080)) == small


def test_a_resolution_too_small_for_the_preference_clamps_it_down():
    huge = gs.UI_SIZE_LADDER[-1].value
    assert gs.ui_size_for(huge, (534, 300)) == gs.UI_SIZE_LADDER[0].value  # Micro


def test_the_preference_comes_back_when_a_later_launch_can_carry_it():
    """The case that makes ui_size_for return a value even when nothing needs clamping.

    Returning None for "no change needed" would leave Micro in config.xml for every
    launch after the first small one, because nothing else ever writes the tag.
    """
    huge = gs.UI_SIZE_LADDER[-1].value
    assert gs.ui_size_for(huge, (534, 300)) != huge
    assert gs.ui_size_for(huge, (1920, 1080)) == huge


def test_no_capture_means_no_write():
    """Nothing read the user's value, so there is nothing to clamp and nothing to give back."""
    assert gs.ui_size_for(None, (534, 300)) is None


def test_a_value_outside_the_enum_takes_the_ceiling():
    assert gs.ui_size_for(99, (534, 300)) == gs.UI_SIZE_LADDER[0].value


def test_every_offered_resolution_has_a_ui_scale_that_actually_works():
    """The bar a new entry has to clear, and what removed 400x300 from the list.

    Rendering is not enough: the scale it renders at must also leave the teleporter
    buttons clear of the interact prompt.
    """
    assert (400, 300) not in VALID_RESOLUTIONS
    for resolution in VALID_RESOLUTIONS:
        assert gs.has_unoccluded_ui_size(resolution), resolution


def test_the_offered_resolutions_start_at_the_smallest_that_works():
    assert min(VALID_RESOLUTIONS, key=lambda r: (r[0] * r[1], r)) == (534, 300)
    for rung in gs.UI_SIZE_LADDER[1:]:
        assert rung.min_resolution in VALID_RESOLUTIONS


def test_every_offered_sub_floor_resolution_is_a_ladder_pairing():
    """No freeform sizes: each one maps to exactly one maximum UI size.

    Stated over ``SUB_FLOOR_RESOLUTIONS`` rather than over "smaller than 800x600",
    because a rung's 16:9 form is *wider* than the stock floor while still taking a UI
    scale below Huge -- 854x480 is sub-floor in the sense that matters here.
    """
    for resolution in gs.SUB_FLOOR_RESOLUTIONS:
        assert resolution in VALID_RESOLUTIONS
        assert gs.max_ui_size(resolution).name != "Huge"


# ---------------------------------------------------------------------------
# The 16:9 forms
#
# Their reason for existing is not screen space: the HUD anchors the compass and
# teleporter buttons to an edge and centres the interact prompt, so at 400x300 the prompt
# covers the hub and dorm buttons, and those are clicked by coordinate.
# ---------------------------------------------------------------------------


def test_the_widescreen_form_keeps_the_height_and_only_widens():
    """The whole basis for offering these without re-driving every control in a client.

    A rung's floor is verified; more width at the same height cannot take it back below
    that floor, so the 16:9 form inherits the 4:3 form's verification.
    """
    for rung in gs.UI_SIZE_LADDER:
        wide_w, wide_h = gs.widescreen_form(rung.min_resolution)
        floor_w, floor_h = rung.min_resolution
        assert wide_h == floor_h
        assert wide_w > floor_w


def test_the_widescreen_form_is_sixteen_by_nine_on_an_even_pixel():
    for rung in gs.UI_SIZE_LADDER:
        width, height = gs.widescreen_form(rung.min_resolution)
        assert width % 2 == 0
        assert abs(width / height - 16 / 9) < 0.01


def test_a_widescreen_form_takes_the_same_ui_scale_as_the_rung_it_came_from():
    """It must not buy a larger UI scale by being wider -- the height is what was verified."""
    for rung in gs.UI_SIZE_LADDER:
        assert gs.max_ui_size(gs.widescreen_form(rung.min_resolution)).name == rung.name


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
    [(1, (1280, 720)), (2, (640, 480)), (3, (534, 300)), (4, (534, 300))],
)
def test_the_largest_tier_that_fits_1080p_at_the_default_layout(clients, expected):
    """The table from #101: Micro is what a four-client 1080p user actually runs.

    Solo differs from that table, which ranked the six ladder rungs alone and so stopped
    at 800x600. The offered list is not only the ladder — one client on a 1080p screen
    has room for 1280x720, which is the answer a user would expect to be given.

    Three and four now land on Micro's 16:9 form rather than its 4:3 one: same height,
    same UI scale, and it still fits the 2x2 grid. Two is unchanged, because 640x480
    shows more game than 640x360 and ranking is by area.
    """
    assert gs.largest_fitting(VALID_RESOLUTIONS, clients, _DEFAULT_BUDGET) == expected


def test_the_widescreen_micro_rung_costs_a_four_client_user_nothing():
    """It is the case the 16:9 forms were added for, so it must not shrink the grid."""
    wide = gs.widescreen_form((400, 300))
    assert gs.fits(wide, 4, _DEFAULT_BUDGET)
    assert wide[0] * wide[1] > 400 * 300


@pytest.mark.parametrize(
    ("clients", "expected"),
    [(1, "Huge"), (2, "Small"), (3, "Micro"), (4, "Micro")],
)
def test_the_ui_scale_that_comes_with_the_largest_fitting_resolution(clients, expected):
    """#101's table, moved on twice.

    Ranked over the offered list rather than the ladder rungs alone, so solo gets 1280x720
    rather than stopping at 800x600. And two clients now read Small rather than Regular:
    the resolution is still 640x480, but a 4:3 client takes one scale less than its render
    floor allows, or the prompt covers the teleporter buttons.
    """
    largest = gs.largest_fitting(VALID_RESOLUTIONS, clients, _DEFAULT_BUDGET)
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


def test_the_reason_names_both_sizes_and_every_way_out():
    reason = gs.fit_problem((800, 600), 2, _DEFAULT_BUDGET)
    assert reason is not None
    assert "1624×651" in reason  # what 2 clients at 800x600 need
    assert "1304×800" in reason  # what is available
    assert "lower the resolution" in reason
    assert "fewer clients" in reason
    # The third way out is real: the budget is the larger of the configured layout and
    # the live game area, so widening the game area by dragging a sidebar is a fix.
    assert "make the game area larger" in reason


def test_one_client_is_not_described_as_one_clients():
    reason = gs.fit_problem((1920, 1080), 1, _DEFAULT_BUDGET)
    assert reason is not None
    assert reason.startswith("1 client at")


def test_every_offered_resolution_is_one_the_game_offers_or_a_widescreen_form_of_a_rung():
    """The list is not freeform, and the provenance of each entry is one of exactly two.

    Either the game's own dropdown offers it, or it is a ladder rung widened to 16:9 by
    ``widescreen_form`` -- which is the same even-pixel rule the dropdown's own 16:9 sizes
    follow, so the derived ones are that series continued below the game's 800x600 floor
    rather than an aspect ratio of our invention.
    """
    # The dropdown, transcribed in full; the offered list is a subset of this plus the
    # derived forms, never a superset.
    game_dropdown = {
        (800, 600),
        (1024, 768),
        (1128, 634),
        (1280, 720),
        (1280, 1024),
        (1366, 768),
        (1600, 900),
        (1600, 1200),
        (1680, 1050),
        (1760, 990),
        (1920, 1080),
        (1920, 1200),
        (2048, 1536),
        (2560, 1440),
        (3072, 1728),
        (3200, 1800),
        (3840, 2160),
    }
    derived = {gs.widescreen_form(rung.min_resolution) for rung in gs.UI_SIZE_LADDER}
    ladder_floors = {rung.min_resolution for rung in gs.UI_SIZE_LADDER}
    for resolution in VALID_RESOLUTIONS:
        assert resolution in game_dropdown | derived | ladder_floors, resolution


def test_the_widescreen_form_reproduces_the_game_s_own_sixteen_by_nine_entries():
    """Not a coincidence worth relying on silently: it is why the derived sizes are safe.

    Fed the heights the game's own dropdown uses, ``widescreen_form`` returns the game's
    own widths. The rule below 800x600 is the rule the game already applies above it.
    """
    assert gs.widescreen_form((0, 634)) == (1128, 634)
    assert gs.widescreen_form((0, 990)) == (1760, 990)
    assert gs.widescreen_form((0, 720)) == (1280, 720)
    assert gs.widescreen_form((0, 1080)) == (1920, 1080)
