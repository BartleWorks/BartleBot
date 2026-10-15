"""The game view's minimum size while a launch is still coming up.

A profile's windows open at the profile's resolution, and its pending tiles have no window
to measure. Sized at the global resolution instead, which setup fits to one client, a
four-account profile held room for four single-client windows, more than the screen has,
and the pending tiles ran off the edge of the game view.
"""

from __future__ import annotations

import pytest

from bartlebot.clients.tile_slots import TileSlot
from bartlebot.game.game_sizing import required_game_area
from bartlebot.gui.game_embed.game_view import GameView
from bartlebot.settings_manager import SettingsManager


@pytest.fixture
def game_view(qapp):
    """A GameView with its two app-wide hooks disarmed, as ``test_game_view_theme`` has it."""
    view = GameView()
    view._focus_timer.stop()
    qapp.removeEventFilter(view)
    yield view
    view.sync_tiles([])
    view.deleteLater()


def _pending(slot: int, username: str, resolution: tuple[int, int] | None) -> TileSlot:
    return TileSlot(slot=slot, hwnd=None, label=username, player=None, username=username, resolution=resolution)


def _area(view: GameView, resolution: tuple[int, int], count: int) -> tuple[int, int]:
    return required_game_area(resolution, count, view.devicePixelRatio() or 1.0)


def test_pending_tiles_hold_room_for_the_resolution_their_launch_opens_at(game_view):
    SettingsManager.instance().game_resolution = (960, 540)

    game_view.sync_tiles([_pending(slot, name, (534, 300)) for slot, name in enumerate("abcd")])

    minimum = game_view.minimumSize()
    assert (minimum.width(), minimum.height()) == _area(game_view, (534, 300), 4)


def test_a_launch_that_did_not_say_holds_room_for_the_global_resolution(game_view):
    SettingsManager.instance().game_resolution = (800, 600)

    game_view.sync_tiles([_pending(0, "a", None)])

    minimum = game_view.minimumSize()
    assert (minimum.width(), minimum.height()) == _area(game_view, (800, 600), 1)
