"""What setup fits to the screen: a new profile's resolution, the canvas and the sidebars.

``gui/window_panes.py``: BartleBot's part of the screen fit, which stayed when the rest
moved to core (Stage 9 of #167). Moved here from ``test_screen_fit.py`` with it.
"""

from __future__ import annotations

import pytest
from bartleby.qt import screen_fit
from bartleby.runtime.game import game_sizing

from bartlebot.game import pane_sizing
from bartlebot.gui import window_panes
from bartlebot.settings_manager import SettingsManager


@pytest.fixture
def screen(monkeypatch):
    """Pin the screen the fit is judged against, so the result is not the runner's."""

    def _use(metrics):
        monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: metrics)

    return _use


def _profile(accounts, resolution=None):
    profile = {"name": "Team", "accounts": list(accounts)}
    if resolution is not None:
        profile["resolution"] = list(resolution)
    return profile


def test_bartlebot_s_panes_are_its_two_sidebars_at_their_measured_floors_and_the_canvas(qapp):
    left, right, canvas = window_panes.panes()

    assert (left.name, left.floor, left.side) == ("left_sidebar_width", window_panes.left_sidebar_floor(), "left")
    assert (right.name, right.floor, right.side) == ("right_sidebar_width", screen_fit.right_sidebar_floor(), "right")
    assert (canvas.name, canvas.floor, canvas.side) == (
        "bottom_section_height",
        pane_sizing.BOTCANVAS_MIN_HEIGHT,
        "bottom",
    )


# -- what setup fits: a new profile's resolution, and the canvas -----------------------

_1080P_TASKBAR = ((1920, 1032), 1.0)


def test_setup_judges_a_new_profile_at_the_default_canvas(screen):
    """Not the canvas as configured: one fitted to a single account would leave four nothing."""
    screen(_1080P_TASKBAR)
    SettingsManager.instance().set_general("bottom_section_height", 500)

    assert window_panes.setup_area()[1] == 1032 - pane_sizing.BOTCANVAS_DEFAULT_HEIGHT - 60
    assert window_panes.setup_resolution(4) == (534, 300)


def test_one_client_gets_the_largest_resolution_that_fits(screen):
    """800x600 is a quarter of a 4K screen: setup gives a single account the room there is."""
    screen(_1080P_TASKBAR)

    assert window_panes.fitted_layout().resolution == (960, 540)


def test_a_resolution_chosen_in_settings_is_kept(screen):
    screen(_1080P_TASKBAR)
    SettingsManager.instance().game_resolution = (854, 480)

    assert window_panes.fitted_layout().resolution == (854, 480)


def test_the_canvas_takes_the_height_one_client_leaves(screen):
    screen(_1080P_TASKBAR)

    assert window_panes.fitted_layout().panes["bottom_section_height"] == 1032 - 60 - (540 + 51)


def test_the_canvas_leaves_room_for_the_tallest_launch_profile(screen):
    screen(_1080P_TASKBAR)
    SettingsManager.instance().accounts = [{"username": u, "alias": ""} for u in "abcd"]
    SettingsManager.instance().launch_profiles = [_profile("abcd", (534, 300))]

    assert window_panes.fitted_layout().panes["bottom_section_height"] == 1032 - 60 - 2 * (300 + 51)


def test_a_launch_profile_that_cannot_fit_shapes_no_pane(screen):
    """Two at 800x600 can't launch on 1080p whatever the panes do."""
    screen(_1080P_TASKBAR)
    alone = window_panes.fitted_layout().panes
    SettingsManager.instance().accounts = [{"username": u, "alias": ""} for u in "ab"]
    SettingsManager.instance().launch_profiles = [_profile("ab", (800, 600))]

    assert window_panes.fitted_layout().panes == alone


def test_the_sidebars_share_the_width_the_widest_grid_leaves_evenly(screen):
    screen(_1080P_TASKBAR)
    panes = window_panes.fitted_layout().panes
    left, right = panes["left_sidebar_width"], panes["right_sidebar_width"]

    assert abs(left - right) <= 1
    assert left + right == 1920 - 2 * game_sizing.SPLITTER_HANDLE_PX - (960 + 12)


def test_a_sidebar_sized_in_settings_keeps_its_size(screen):
    screen(_1080P_TASKBAR)
    SettingsManager.instance().set_general("left_sidebar_width", 450)
    panes = window_panes.fitted_layout().panes

    assert "left_sidebar_width" not in panes
    assert panes["right_sidebar_width"] == 1920 - 2 * game_sizing.SPLITTER_HANDLE_PX - 972 - 450


def test_an_unmeasurable_screen_fits_nothing(screen):
    screen((screen_fit.UNMEASURABLE, 1.0))

    assert window_panes.fitted_layout() is None


def test_a_launch_profile_decides_the_layout_and_one_client_fits_what_it_leaves(screen):
    """Two at 560x420 on 1080p keep a 471px-tall game area, and a single client fits in it."""
    screen(_1080P_TASKBAR)
    SettingsManager.instance().accounts = [{"username": u, "alias": ""} for u in "ab"]
    SettingsManager.instance().launch_profiles = [_profile("ab", (560, 420))]

    fitted = window_panes.fitted_layout()

    assert fitted.panes["bottom_section_height"] == 1032 - 60 - (420 + 51)
    assert fitted.resolution == (746, 420)


def test_one_client_is_fitted_to_a_profile_that_follows_it(screen):
    """A profile with no resolution of its own launches at the global one."""
    screen(_1080P_TASKBAR)
    SettingsManager.instance().accounts = [{"username": u, "alias": ""} for u in "ab"]
    SettingsManager.instance().launch_profiles = [_profile("ab")]

    assert window_panes.fitted_layout().resolution == (560, 420)


def test_one_client_on_1440p_leaves_the_canvas_its_share(screen):
    """1680x1050 is the largest that fits, but leaves the canvas 231px; 1600x900 leaves 381."""
    screen(((2560, 1392), 1.0))
    fitted = window_panes.fitted_layout()

    assert fitted.resolution == (1600, 900)
    assert fitted.panes["bottom_section_height"] == 1392 - 60 - (900 + 51)


def test_one_client_on_4k_at_150_percent_keeps_the_largest(screen):
    """2560x1440 leaves 329 of 1400, over the share: the bar is set so this stays."""
    screen(((2560, 1400), 1.5))

    assert window_panes.fitted_layout().resolution == (2560, 1440)
