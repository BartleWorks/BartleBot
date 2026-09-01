"""Tests for the game-tile pill caption.

The tiles themselves are QWidgets that need a QApplication, so the caption logic is a plain
function and only that is tested here.
"""

from bartlebot.gui.game_embed.client_tile import pill_text
from bartlebot.gui.game_embed.game_view import GameView


def test_shows_the_label_alone_before_a_name_is_known():
    assert pill_text("Player 1", None) == "Player 1"


def test_appends_the_wizard_name():
    assert pill_text("Player 1", "Luke SkullHunter") == "Player 1 · Luke SkullHunter"


def test_an_empty_name_is_treated_as_unknown():
    # A blank string would otherwise render as a trailing separator with nothing after it.
    assert pill_text("Player 2", "") == "Player 2"


def test_label_and_name_stay_independent():
    """Reordering renumbers the slot; a character switch replaces the wizard. Neither
    update may clobber the other, which is why the caption is composed at paint time."""
    assert pill_text("Player 3", "Luke SkullHunter") == "Player 3 · Luke SkullHunter"
    assert pill_text("Player 1", "Luke SkullHunter") == "Player 1 · Luke SkullHunter"
    assert pill_text("Player 3", "Misha SkullSeer") == "Player 3 · Misha SkullSeer"


def test_streamer_mode_drops_the_name_and_keeps_the_label():
    """The slot label is already a position, so the caption does not gain a second one —
    see pill_text's docstring."""
    from bartlebot.settings_manager import SettingsManager

    SettingsManager.instance().streamer_mode = True
    assert pill_text("Player 1", "Luke SkullHunter") == "Player 1"
    assert pill_text("Player 3", "Misha SkullSeer") == "Player 3"


# ---------------------------------------------------------------------------
# GameView name bookkeeping
#
# GameView is a QWidget, so only the index-to-name bookkeeping is exercised here —
# unbound, against a stub. That bookkeeping is what decides whether a tile created after
# the names arrived gets one, which is exactly what went wrong the first time.
#
# Names arrive keyed by **player** index and tiles are keyed by **slot**, which are only
# the same number while every account is in game (see bartlebot.tile_slots). _tile_players
# is the join between them, so every stub here has to state it.
# ---------------------------------------------------------------------------


class _FakeTile:
    def __init__(self, name=None):
        self._wizard_name = name

    def set_wizard_name(self, name):
        self._wizard_name = name

    @property
    def wizard_name(self):
        return self._wizard_name


class _StubView:
    def __init__(self, tiles=None, detached=None, players=None):
        self._tiles = tiles or {}
        self._detached_tiles = detached or {}
        self._wizard_names: list = []
        # Slot → player. Defaults to the two agreeing, which is the steady state; a test
        # about a window that is not in game says so explicitly.
        if players is None:
            players = {slot: slot for slot in (*self._tiles, *self._detached_tiles)}
        self._tile_players = players

    set_wizard_names = GameView.set_wizard_names
    _wizard_name_for = GameView._wizard_name_for


def test_names_are_applied_to_existing_tiles():
    view = _StubView(tiles={0: _FakeTile(), 1: _FakeTile()})
    view.set_wizard_names(["Luke SkullHunter", "Misha SkullSeer"])
    assert view._tiles[0].wizard_name == "Luke SkullHunter"
    assert view._tiles[1].wizard_name == "Misha SkullSeer"


def test_names_are_remembered_for_tiles_created_later():
    """The bug: only the first pill populated, because the rest had no tile yet at emit."""
    view = _StubView(tiles={0: _FakeTile()})
    view.set_wizard_names(["Luke SkullHunter", "Misha SkullSeer"])
    assert view._wizard_name_for(1) == "Misha SkullSeer", "a tile made now must still get its name"


def test_detached_tiles_receive_names_too():
    view = _StubView(tiles={0: _FakeTile()}, detached={1: _FakeTile()})
    view.set_wizard_names(["Luke SkullHunter", "Misha SkullSeer"])
    assert view._detached_tiles[1].wizard_name == "Misha SkullSeer"


def test_unknown_slot_has_no_name():
    view = _StubView()
    view.set_wizard_names(["Luke SkullHunter"])
    assert view._wizard_name_for(5) is None
    assert view._wizard_name_for(-1) is None


def test_a_cleared_name_is_pushed_through():
    """After a character switch the tile must drop the old name, not keep showing it."""
    view = _StubView(tiles={0: _FakeTile("Luke SkullHunter")})
    view.set_wizard_names([None])
    assert view._tiles[0].wizard_name is None


def test_a_name_follows_its_player_rather_than_its_slot():
    """A crashed slot 0 leaves slot 1 holding player 0 — the name must follow the player."""
    view = _StubView(tiles={1: _FakeTile()}, players={1: 0})
    view.set_wizard_names(["Luke SkullHunter"])
    assert view._tiles[1].wizard_name == "Luke SkullHunter"


def test_a_window_that_is_not_in_game_gets_no_name():
    """It has no player index, so nothing has read a name for it and none is invented."""
    view = _StubView(tiles={0: _FakeTile()}, players={0: None})
    view.set_wizard_names(["Luke SkullHunter"])
    assert view._tiles[0].wizard_name is None
