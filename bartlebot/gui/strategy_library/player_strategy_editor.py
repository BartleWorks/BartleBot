"""Editor widget for a single PlayerCombatStrategy (name + DSL text)."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.strategy_models import PlayerCombatStrategy
from bartlebot.gui.theme import THEME

_DEBOUNCE_MS = 500


class PlayerStrategyEditor(QWidget):
    """Edit the name and DSL of a PlayerCombatStrategy.  Auto-saves after ``_DEBOUNCE_MS`` ms."""

    changed = pyqtSignal()  # emitted when any field changes (after debounce)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._strategy: PlayerCombatStrategy | None = None
        self._suppress = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.textChanged.connect(self._on_changed)
        layout.addWidget(self._name_edit)

        layout.addWidget(QLabel("DSL:"))
        self._dsl_edit = QPlainTextEdit()
        self._dsl_edit.setFont(THEME.font_mono())
        self._dsl_edit.textChanged.connect(self._on_changed)
        layout.addWidget(self._dsl_edit, 1)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._flush)

        self._apply_style()

    def load(self, strategy: PlayerCombatStrategy) -> None:
        self._strategy = strategy
        self._suppress = True
        self._name_edit.setText(strategy.name)
        self._dsl_edit.setPlainText(strategy.dsl)
        self._suppress = False

    def flush(self) -> None:
        """Immediately commit any pending changes."""
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._flush()

    def _on_changed(self) -> None:
        if self._suppress or self._strategy is None:
            return
        self._save_timer.stop()
        self._save_timer.start()

    def _flush(self) -> None:
        if self._strategy is None:
            return
        self._strategy.name = self._name_edit.text().strip() or self._strategy.name
        self._strategy.dsl = self._dsl_edit.toPlainText()
        self.changed.emit()

    def _apply_style(self) -> None:
        self._dsl_edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {THEME.bg}; color: {THEME.text}; border: 1px solid {THEME.border}; }}"
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._dsl_edit.setFont(THEME.font_mono())
            self._apply_style()
