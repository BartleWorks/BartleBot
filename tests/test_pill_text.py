"""Tests for the game-tile pill caption.

The tiles themselves are QWidgets that need a QApplication, so the caption logic is a plain
function and only that is tested here.
"""

from bartlebot.gui.game_embed.client_tile import pill_text


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
