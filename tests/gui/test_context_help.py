"""F1 and a dialog's ? open the page for what is in front of the user (epic #25, Stage 5)."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, QObject, Qt, QUrl
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QKeySequenceEdit,
    QLineEdit,
    QVBoxLayout,
    QWhatsThis,
    QWidget,
)

from bartlebot.gui import context_help
from bartlebot.gui.context_help import ContextHelp, help_from_title_bar, help_url_for, set_help_topic
from bartlebot.packaging.help_topics import help_url

HINT = Qt.WindowType.WindowContextHelpButtonHint


@pytest.fixture
def opened(monkeypatch) -> list[str]:
    """Every URL the app asks the browser for, instead of opening one."""
    urls: list[str] = []
    monkeypatch.setattr(
        context_help, "open_url", lambda url: urls.append(url.toString() if isinstance(url, QUrl) else url)
    )
    return urls


@pytest.fixture
def game_keyboard() -> list[bool]:
    """Whether a game window holds the keyboard, as the filter asks it."""
    return [False]


@pytest.fixture
def f1(qapp, game_keyboard):
    owner = QObject()
    ContextHelp(owner, game_has_keyboard=lambda: game_keyboard[0])
    yield
    owner.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _dialog(qtbot, topic: str | None, parent: QWidget | None = None) -> tuple[QDialog, QLineEdit]:
    dialog = QDialog(parent)
    field = QLineEdit()
    QVBoxLayout(dialog).addWidget(field)
    if topic is not None:
        set_help_topic(dialog, topic)
    qtbot.addWidget(dialog)
    return dialog, field


def test_a_dialog_with_a_topic_asks_for_the_title_bars_question_mark(qtbot):
    with_topic, _ = _dialog(qtbot, "new_bot")
    without, _ = _dialog(qtbot, None)

    assert with_topic.windowFlags() & HINT
    assert not without.windowFlags() & HINT


def test_a_widget_inside_a_window_gets_no_window_flag(qapp):
    panel = QWidget()
    set_help_topic(panel, "options")

    assert not panel.windowFlags() & HINT
    assert help_url_for(panel) == help_url("options")


def test_a_topic_not_in_the_map_is_refused(qapp):
    with pytest.raises(KeyError):
        set_help_topic(QWidget(), "no_such_surface")


def test_f1_inside_a_dialog_opens_its_topic(qtbot, f1, opened):
    _dialog_, field = _dialog(qtbot, "import")

    QTest.keyClick(field, Qt.Key.Key_F1)

    assert opened == [help_url("import")]


def test_f1_in_a_dialog_with_no_topic_opens_the_guides_home_not_the_window_behind_it(qtbot, f1, opened):
    behind = QWidget()
    set_help_topic(behind, "options")
    qtbot.addWidget(behind)
    _dialog_, field = _dialog(qtbot, None, parent=behind)

    QTest.keyClick(field, Qt.Key.Key_F1)

    assert opened == [help_url(None)]


def test_the_nearest_page_up_from_the_focus_wins(qtbot, f1, opened):
    dialog, field = _dialog(qtbot, "settings_general")
    set_help_topic(field, "settings_game")

    QTest.keyClick(field, Qt.Key.Key_F1)

    assert opened == [help_url("settings_game")]


def test_a_surfaces_own_answer_comes_before_its_topic(qtbot, f1, opened):
    dialog, field = _dialog(qtbot, "node_browser")
    dialog.context_help_url = lambda: "https://example.invalid/shown"

    QTest.keyClick(field, Qt.Key.Key_F1)
    dialog.context_help_url = lambda: None
    QTest.keyClick(field, Qt.Key.Key_F1)

    assert opened == ["https://example.invalid/shown", help_url("node_browser")]


def test_f1_while_a_game_has_the_keyboard_opens_nothing(qtbot, f1, opened, game_keyboard):
    _dialog_, field = _dialog(qtbot, "import")
    game_keyboard[0] = True

    QTest.keyClick(field, Qt.Key.Key_F1)

    assert opened == []


def test_a_held_f1_opens_one_page(qtbot, f1, opened):
    _dialog_, field = _dialog(qtbot, "import")
    repeat = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F1, Qt.KeyboardModifier.NoModifier, "", True)

    QApplication.sendEvent(field, repeat)

    assert opened == []


def test_f1_in_a_key_binding_field_is_the_key_being_bound(qtbot, f1, opened):
    dialog, _field = _dialog(qtbot, "settings_authoring")
    binding = QKeySequenceEdit()
    dialog.layout().addWidget(binding)

    QTest.keyClick(binding, Qt.Key.Key_F1)

    assert opened == []
    assert binding.keySequence().toString() == "F1"


def test_other_keys_go_to_the_widget(qtbot, f1, opened):
    _dialog_, field = _dialog(qtbot, "import")

    QTest.keyClicks(field, "abc")

    assert opened == []
    assert field.text() == "abc"


def test_the_question_mark_opens_what_f1_opens_from_the_focus(qtbot, f1, opened, monkeypatch):
    dialog, field = _dialog(qtbot, "settings_general")
    set_help_topic(field, "settings_bot")
    dialog.show()
    field.setFocus()
    monkeypatch.setattr(context_help, "_active_window", lambda: dialog)

    QApplication.sendEvent(QApplication.instance(), QEvent(QEvent.Type.EnterWhatsThisMode))
    QTest.keyClick(field, Qt.Key.Key_F1)

    assert opened == [help_url("settings_bot"), help_url("settings_bot")]


def test_the_question_mark_with_focus_elsewhere_opens_the_dialogs_page(qtbot):
    dialog, _field = _dialog(qtbot, "templates")

    assert help_from_title_bar(dialog) == help_url("templates")


def test_whats_this_is_left_alone_on_a_window_with_no_topic(qtbot, f1, opened, monkeypatch):
    dialog, _field = _dialog(qtbot, None)
    monkeypatch.setattr(context_help, "_active_window", lambda: dialog)

    QApplication.sendEvent(QApplication.instance(), QEvent(QEvent.Type.EnterWhatsThisMode))
    entered = QWhatsThis.inWhatsThisMode()
    QWhatsThis.leaveWhatsThisMode()

    assert opened == []
    assert entered, "Qt's own What's This mode, untouched"


def test_open_folder_makes_the_folder_and_opens_it(tmp_path, opened):
    folder = tmp_path / "logs"

    context_help.open_folder(folder)

    assert folder.is_dir()
    assert opened == [QUrl.fromLocalFile(str(folder)).toString()]
