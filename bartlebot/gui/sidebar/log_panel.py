"""Log panel — owned by LeftSidebar, displayed in the Log tab."""

from __future__ import annotations

import logging
from collections import deque

from bartleby.qt.theming.theme import THEME
from bartleby.qt.widgets.buttons.action_button import ActionButton
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from bartlebot.settings_manager import SettingsManager

_NAME_TO_LEVEL: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


#: The header row's fixed control widths, and the caption beside them. Held here as
#: constants because ``header_row_floor`` measures this row on the right sidebar's
#: behalf: the row is what that panel's width floor is *made of*, so the two have to
#: read the same numbers or the floor drifts from the thing it is protecting.
#: Warning, because at Info a new user's log scrolls past the lines that matter (#259).
_DEFAULT_LEVEL = "WARNING"
_LEVEL_CAPTION = "Min level:"
_LEVEL_COMBO_W = 90
_EXPORT_BTN_W = 62
_CLEAR_BTN_W = 52
_HEADER_MARGINS = (4, 2, 4, 2)


def _build_header_row() -> tuple[QWidget, QComboBox, ActionButton, ActionButton]:
    """The Log tab's header row, unwired: caption, level combo, Export, Clear.

    Built by a function rather than inline so that :func:`header_row_floor` measures the
    row the panel actually shows, rather than a second copy of it written out in
    ``gui/screen_fit`` that a change here would silently leave behind.
    """
    combo = QComboBox()
    combo.addItems(list(_NAME_TO_LEVEL))
    combo.setCurrentText(_DEFAULT_LEVEL)
    combo.setFixedWidth(_LEVEL_COMBO_W)

    # fill=False because both are pinned to a fixed width, and glyphless because that
    # width is what the right sidebar's floor is measured from: a 16px icon inside a 62px
    # button leaves nothing for the label, and widening them to fit one would raise the
    # floor of the panel they sit in. A glyph is optional; the floor is not.
    export_btn = ActionButton("", "Export", fill=False)
    export_btn.setFixedWidth(_EXPORT_BTN_W)
    clear_btn = ActionButton("", "Clear", fill=False)
    clear_btn.setFixedWidth(_CLEAR_BTN_W)

    header = QWidget()
    row = QHBoxLayout(header)
    row.setContentsMargins(*_HEADER_MARGINS)
    row.addWidget(QLabel(_LEVEL_CAPTION))
    row.addWidget(combo)
    row.addStretch()
    row.addWidget(export_btn)
    row.addWidget(clear_btn)
    return header, combo, export_btn, clear_btn


def header_row_floor(font: QFont) -> int:
    """How wide this row has to be at *font* -- the right sidebar's width floor.

    The widest thing in that panel which neither elides nor wraps, so it is what the
    floor is measured from, exactly as the keep-list filter row is on the left. Most of
    the row is fixed: three controls at pinned widths that a larger font does not move.
    Only the caption and the controls' own text padding grow, which is why measuring
    beats scaling the whole panel by a text ratio -- see ``game_sizing.right_sidebar_floor``.

    The probe is parentless and never shown; see the identical note in
    ``gui/screen_fit.right_sidebar_floor`` for why "never shown" is load-bearing.
    """
    header, *_ = _build_header_row()
    for widget in [header, *header.findChildren(QWidget)]:
        widget.setFont(font)
    return header.minimumSizeHint().width()


def _log_color(level: int) -> str:
    """Return the theme color for a log level, using semantic tokens."""
    if level >= logging.ERROR:
        return THEME.error
    if level >= logging.WARNING:
        return THEME.warning
    if level >= logging.INFO:
        return THEME.success
    return THEME.accent  # DEBUG


class LogPanel(QWidget):
    """Log output panel with level filter, export, and clear buttons."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(THEME.font_mono())
        max_lines = SettingsManager.instance().get_general("log_max_lines", default=5000)
        self._log.setMaximumBlockCount(max_lines)
        # Every line received, whatever the filter, so that changing the level can
        # re-draw what is already there and bring hidden lines back.
        self._entries: deque[tuple[str, int]] = deque(maxlen=max_lines or None)

        header, self._log_level_combo, _export_btn, _clear_btn = _build_header_row()
        _export_btn.clicked.connect(self._export_log)
        _clear_btn.clicked.connect(self.clear)
        self._log_level_combo.currentTextChanged.connect(self._refilter)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)
        vbox.addWidget(header)
        vbox.addWidget(self._log, 1)

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def on_run_started(self) -> None:
        """Called by AppController before a bot run begins."""
        if SettingsManager.instance().get_general("clear_log_on_run", default=True):
            self.clear()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def apply_settings(self) -> None:
        """Re-read log_max_lines from SettingsManager and update the block limit."""
        max_lines = SettingsManager.instance().get_general("log_max_lines", default=5000)
        self._log.setMaximumBlockCount(max_lines)
        self._entries = deque(self._entries, maxlen=max_lines or None)

    # ------------------------------------------------------------------
    # Log entry point
    # ------------------------------------------------------------------

    def append_log_formatted(self, formatted_text: str, level: int) -> None:
        """Append a pre-formatted log line from the Python logging bridge."""
        self._entries.append((formatted_text, level))
        if level < self._threshold():
            return
        self._insert(formatted_text, level)
        self._log.moveCursor(QTextCursor.MoveOperation.End)

    def clear(self) -> None:
        """Drop every line, shown or filtered out."""
        self._entries.clear()
        self._log.clear()

    def _threshold(self) -> int:
        return _NAME_TO_LEVEL.get(self._log_level_combo.currentText(), logging.DEBUG)

    def _refilter(self) -> None:
        """Re-draw the kept lines at the new minimum level."""
        threshold = self._threshold()
        self._log.clear()
        for text, level in self._entries:
            if level >= threshold:
                self._insert(text, level)
        self._log.moveCursor(QTextCursor.MoveOperation.End)

    def _insert(self, formatted_text: str, level: int) -> None:
        color = _log_color(level)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(formatted_text + "\n", fmt)
        self._log.moveCursor(QTextCursor.MoveOperation.End)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _export_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Log", "", "Log Files (*.log *.txt);;All Files (*)")
        if not path:
            return
        if not path.endswith((".log", ".txt")):
            path += ".log"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self._log.toPlainText())
