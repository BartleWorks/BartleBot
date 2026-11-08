"""F1 and a dialog's ``?`` open the site's page for what is in front of the user (epic #25, Stage 5).

A surface names itself with :func:`set_help_topic`, a key of
``packaging.help_topics.HELP_TOPICS``. A surface whose page changes with what it shows
(the main window by the canvas's mode, the node browser by the node it shows, Settings
by its tab) has a ``context_help_url()`` method as well, asked first, which answers
``None`` to fall back on its topic.

**F1** is :class:`ContextHelp`, one filter on the *application*: a ``QShortcut`` on the
main window is blocked while a modal dialog is open, which is when F1 is wanted most. It
walks up from the widget the key went to, to the first with a page, and stops at that
widget's window, so a dialog with no topic opens the guide's home rather than whatever the
window behind it is about. While a game window has the keyboard, F1 is the game's: Qt
never sees the key then, and the filter checks too, since the authoring keys take F1 then
(``bartleby.qt.global_hotkeys``). In a key binding being typed, F1 is the key the user is
binding, so it goes to the field.

**The ``?``** is Windows' own context help button, beside the frame's close button, which a
dialog given a topic asks for with ``WindowContextHelpButtonHint``. Windows draws it only
on a window with no minimize or maximize button, which every dialog here is. A click
reaches Qt as ``EnterWhatsThisMode`` sent to the application, and the filter opens the
page F1 would open from the dialog's focus widget instead of entering What's This mode.

``bartlebot/gui/CLAUDE.md`` says a new surface sets a topic.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QKeySequence
from PyQt6.QtWidgets import QApplication, QDialog, QKeySequenceEdit, QWidget

from bartlebot.packaging.help_topics import HELP_TOPICS, help_url

logger = logging.getLogger(__name__)

#: The dynamic property a surface's topic is stored in.
HELP_TOPIC = "help_topic"


def open_url(url: str | QUrl) -> None:
    """Open *url* in the user's browser, or a folder in File Explorer."""
    if not QDesktopServices.openUrl(url if isinstance(url, QUrl) else QUrl(url)):
        logger.warning("Windows didn't open %s", url.toString() if isinstance(url, QUrl) else url)


def open_folder(folder: Path) -> None:
    """Open *folder* in File Explorer, making it first if nothing has written there yet."""
    folder.mkdir(parents=True, exist_ok=True)
    open_url(QUrl.fromLocalFile(str(folder)))


def set_help_topic(widget: QWidget, topic: str) -> None:
    """Name *widget*'s surface for F1, and give a dialog the ``?`` in its title bar.

    Called before the dialog is first shown: a window's flags set after that recreate it.
    """
    if topic not in HELP_TOPICS:
        raise KeyError(f"{topic!r} is not in HELP_TOPICS")
    widget.setProperty(HELP_TOPIC, topic)
    if isinstance(widget, QDialog):
        widget.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, True)


def help_topic(widget: QWidget) -> str | None:
    """The topic *widget* itself names, if any."""
    topic = widget.property(HELP_TOPIC)
    return topic if isinstance(topic, str) and topic else None


def help_url_for(widget: QWidget | None) -> str:
    """The page for *widget*: its own, else the nearest one up to its window, else the guide's home."""
    while widget is not None:
        answer = getattr(widget, "context_help_url", None)
        if callable(answer) and (url := answer()):
            return url
        if (topic := help_topic(widget)) is not None:
            return help_url(topic)
        if widget.isWindow():
            break
        widget = widget.parentWidget()
    return help_url(None)


def help_from_title_bar(window: QWidget) -> str:
    """What the ``?`` on *window* opens: the page F1 opens from the widget with focus in it."""
    focus = window.focusWidget()
    return help_url_for(focus if focus is not None and focus.window() is window else window)


def _records_keys(widget: QWidget) -> bool:
    """Whether *widget* is a key binding being typed, where F1 is the key the user means.

    Settings › Authoring binds Copy XYZ to F1 by default, through a ``QKeySequenceEdit``.
    """
    while widget is not None and not isinstance(widget, QKeySequenceEdit):
        widget = None if widget.isWindow() else widget.parentWidget()
    return widget is not None


def _active_window() -> QWidget | None:
    """The window whose ``?`` was clicked: Windows makes it active on the press."""
    return QApplication.activeWindow()


class ContextHelp(QObject):
    """F1 and every dialog's ``?``, for the whole application. Removed with its parent."""

    def __init__(self, parent: QObject, *, game_has_keyboard: Callable[[], bool] = lambda: False) -> None:
        super().__init__(parent)
        self._game_has_keyboard = game_has_keyboard
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt's name
        kind = event.type()
        if kind == QEvent.Type.KeyPress:
            # The widget the key was sent to, not the QWindow that forwards it there.
            if not isinstance(obj, QWidget) or not event.matches(QKeySequence.StandardKey.HelpContents):  # type: ignore[attr-defined]
                return False
            if event.isAutoRepeat() or self._game_has_keyboard() or _records_keys(obj):  # type: ignore[attr-defined]
                return False
            open_url(help_url_for(obj))
            return True
        if kind == QEvent.Type.EnterWhatsThisMode:
            window = _active_window()
            if window is None or help_topic(window) is None:
                return False
            open_url(help_from_title_bar(window))
            return True
        return False
