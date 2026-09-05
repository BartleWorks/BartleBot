"""A stored game resolution that is no longer offered is mapped onto one that is.

400x300 was offered until the interact prompt was found to cover the compass and
teleporter buttons at that width, with no UI scale below Micro to escape to. Settings
files written before that still hold it, and those buttons are clicked by coordinate --
so a client launched at the stored value would have its hub and dorm teleports swallowed
by the prompt, silently, with the node running out its timeout instead of failing.
"""

from __future__ import annotations

import pytest

from bartlebot import game_sizing
from bartlebot.settings_manager import VALID_RESOLUTIONS, SettingsManager


@pytest.fixture
def settings():
    return SettingsManager.instance()


def test_a_stored_resolution_that_is_still_offered_is_left_alone(settings):
    settings.game_resolution = (640, 480)
    assert settings.game_resolution == (640, 480)


def test_a_stored_four_hundred_by_three_hundred_moves_to_the_width_that_works(settings):
    """Same height, so the tile grid is unchanged, and the smallest step that clears."""
    settings.set("game_embed", "resolution", value=[400, 300])
    assert settings.game_resolution == (534, 300)
    assert game_sizing.has_unoccluded_ui_size(settings.game_resolution)


def test_the_replacement_is_never_smaller_than_what_was_stored(settings):
    """Shrinking a user's window to fix it would be a worse surprise than growing it."""
    for stored in [(400, 300), (320, 240), (500, 400)]:
        settings.set("game_embed", "resolution", value=list(stored))
        chosen = settings.game_resolution
        assert chosen in VALID_RESOLUTIONS
        assert chosen[0] >= stored[0] and chosen[1] >= stored[1]


def test_a_stored_resolution_larger_than_anything_offered_falls_back_to_the_default(settings):
    settings.set("game_embed", "resolution", value=[7680, 4320])
    assert settings.game_resolution == (800, 600)


def test_an_unreadable_stored_value_falls_back_to_the_default(settings):
    settings.set("game_embed", "resolution", value="not a resolution")
    assert settings.game_resolution == (800, 600)
