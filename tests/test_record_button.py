"""Tests for ``RecordButton`` (issue #106): the red dot idle, a plain square while stopping."""

from PyQt6.QtGui import QColor

from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.action_button import ActionStyle
from bartlebot.gui.widgets.record_button import IDLE_TEXT, RECORD_RED, RecordButton


def _glyph_color(button: RecordButton) -> str:
    image = button.icon().pixmap(button.iconSize()).toImage()
    return QColor(image.pixel(image.width() // 2, image.height() // 2)).name()


def test_idle_shows_record_with_a_red_dot(qapp):
    button = RecordButton()

    assert button.text() == IDLE_TEXT
    assert button.idle
    assert _glyph_color(button) == RECORD_RED


def test_the_dot_stays_red_on_an_accent_style(qapp):
    """The glyph is the one ``ActionButton`` icon that does not take the text color."""
    button = RecordButton(style=ActionStyle.SECONDARY)

    assert _glyph_color(button) == RECORD_RED


def test_stop_drops_the_red(qapp):
    """A red dot beside *Stop* would say the opposite of the label."""
    button = RecordButton()

    button.set_label("Stop")

    assert not button.idle
    assert _glyph_color(button) == QColor(THEME.text).name()


def test_returning_to_idle_restores_the_dot(qapp):
    button = RecordButton()
    button.set_label("Stop")

    button.set_label(IDLE_TEXT)

    assert button.idle
    assert _glyph_color(button) == RECORD_RED
