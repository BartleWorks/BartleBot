"""Real widget tests for the Bot Info tags picker (QApplication required).

The crash this file guards against had no traceback — the process died with
0xC0000409 (stack overflow) before anything reached the session log — so it is
worth an actual widget test rather than a pure-function extraction.
"""

import sys

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication

from bartlebot.bot_tags import BOT_TAGS
from bartlebot.gui.sidebar.sections.bot_info_section import _PLACEHOLDER, _TagButton, tags_summary


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def test_summary_of_no_tags_is_the_placeholder():
    assert tags_summary([]) == _PLACEHOLDER


def test_summary_joins_tags():
    assert tags_summary(["Gear", "Boss"]) == "Gear, Boss"


def test_constructing_the_button_does_not_recurse(qapp):
    """_apply_style() sets a stylesheet on *self*, and Qt answers that with a
    StyleChange delivered back to self. Without the re-entrancy guard this
    recursed until the stack overflowed and the app died at startup with no
    error message at all."""
    btn = _TagButton()

    assert btn.text() == _PLACEHOLDER


def test_a_theme_change_reapplies_the_stylesheet_without_recursing(qapp):
    """What AppController._apply_theme() does on every theme switch."""
    btn = _TagButton()
    btn.setStyleSheet("")  # simulate the stylesheet going stale

    qapp.sendEvent(btn, QEvent(QEvent.Type.StyleChange))

    assert "QToolButton" in btn.styleSheet()


def test_toggling_an_action_emits_tags_in_vocabulary_order(qapp):
    btn = _TagButton()
    seen = []
    btn.tags_changed.connect(seen.append)

    btn._actions["Boss"].setChecked(True)
    btn._actions["Gear"].setChecked(True)

    assert seen[-1] == ["Gear", "Boss"]
    assert btn.text() == "Gear, Boss"


def test_set_tags_does_not_emit(qapp):
    """Loading a bot must not mark it dirty."""
    btn = _TagButton()
    seen = []
    btn.tags_changed.connect(seen.append)

    btn.set_tags(["Boss", "Gear"])

    assert seen == []
    assert btn.tags() == ["Gear", "Boss"]


def test_set_tags_replaces_rather_than_merges(qapp):
    btn = _TagButton()
    btn.set_tags(["Boss", "Gear"])

    btn.set_tags(["Reagents"])

    assert btn.tags() == ["Reagents"]
    assert not btn._actions["Boss"].isChecked()


def test_every_tag_in_the_vocabulary_has_an_action(qapp):
    btn = _TagButton()

    assert set(btn._actions) == set(BOT_TAGS)
