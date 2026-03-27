"""Log panel — owned by LeftSidebar, displayed in the Log tab."""

from __future__ import annotations

import logging

from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.theme import THEME
from bartlebot.settings_manager import SettingsManager

_NAME_TO_LEVEL: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

_LOG_COLORS: dict[int, str] = {
    logging.DEBUG: THEME.log_debug,
    logging.INFO: THEME.log_info,
    logging.WARNING: THEME.log_warning,
    logging.ERROR: THEME.log_error,
}


class LogPanel(QWidget):
    """Log output panel with level filter and clear button."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(THEME.font_mono())

        self._log_level_combo = QComboBox()
        self._log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self._log_level_combo.setCurrentText("INFO")
        self._log_level_combo.setFixedWidth(90)

        _clear_btn = QPushButton("Clear")
        _clear_btn.setFixedWidth(52)
        _clear_btn.clicked.connect(self._log.clear)

        header = QWidget()
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(4, 2, 4, 2)
        header_row.addWidget(QLabel("Min level:"))
        header_row.addWidget(self._log_level_combo)
        header_row.addStretch()
        header_row.addWidget(_clear_btn)

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
            self._log.clear()

    # ------------------------------------------------------------------
    # Log entry points
    # ------------------------------------------------------------------

    def append_log(self, msg: str, level: int = logging.INFO) -> None:
        """Append a plain log message (from BotMachine.on_log callback)."""
        threshold = _NAME_TO_LEVEL.get(self._log_level_combo.currentText(), logging.DEBUG)
        if level < threshold:
            return
        color = _LOG_COLORS.get(level, "#55cc55")
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(f" {msg}\n", fmt)
        self._log.moveCursor(QTextCursor.MoveOperation.End)

    def append_log_formatted(self, formatted_text: str, level: int) -> None:
        """Append a pre-formatted log line from the Python logging bridge."""
        threshold = _NAME_TO_LEVEL.get(self._log_level_combo.currentText(), logging.DEBUG)
        if level < threshold:
            return
        color = _LOG_COLORS.get(level, "#55cc55")
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(formatted_text + "\n", fmt)
        self._log.moveCursor(QTextCursor.MoveOperation.End)
