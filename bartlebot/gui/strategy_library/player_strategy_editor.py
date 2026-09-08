"""Editor widget for a single PlayerCombatStrategy (name + DSL text)."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.strategy_models import PlayerCombatStrategy
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.school_combo import SchoolCombo
from bartlebot.nodes.combat_runner import validate_dsl

_DEBOUNCE_MS = 500

_DSL_HELP = (
    "{round} move @ target | move @ target | pass — rounds optional, '|' is priority order.\n"
    'Moves: "Named Spell" or any<damage|heal|blade|trap|charm|ward|aura|...>[enchant]\n'
    "Targets: self, enemy, boss, ally, aoe, named:<name>"
)


class PlayerStrategyEditor(QWidget):
    """Edit the name and DSL of a PlayerCombatStrategy.  Auto-saves after ``_DEBOUNCE_MS`` ms."""

    # Emitted after debounce with the id of the strategy the edits belong to.
    # The id matters: a debounced edit lands *after* the user may have already
    # selected a different strategy in the list, so the receiver must save the
    # strategy that was being edited, not whatever is currently highlighted.
    changed = pyqtSignal(str)

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

        layout.addWidget(QLabel("Description:"))
        self._description_edit = QLineEdit()
        self._description_edit.setPlaceholderText("What this strategy does and why (optional)")
        self._description_edit.textChanged.connect(self._on_changed)
        layout.addWidget(self._description_edit)

        school_row = QHBoxLayout()
        school_row.addWidget(QLabel("School:"))
        # Authored, never inferred from the DSL — used as a fallback wherever an
        # assignment links this preset without its own school set.
        self._school_combo = SchoolCombo()
        self._school_combo.setToolTip("What school this strategy is written for, if any — descriptive only")
        self._school_combo.currentIndexChanged.connect(self._on_changed)
        school_row.addWidget(self._school_combo)
        school_row.addStretch()
        layout.addLayout(school_row)

        dsl_lbl = QLabel("DSL:")
        dsl_lbl.setToolTip(_DSL_HELP)
        layout.addWidget(dsl_lbl)
        self._dsl_edit = QPlainTextEdit()
        self._dsl_edit.setFont(THEME.font_mono())
        self._dsl_edit.setToolTip(_DSL_HELP)
        self._dsl_edit.textChanged.connect(self._on_changed)
        layout.addWidget(self._dsl_edit, 1)

        self._dsl_error_lbl = QLabel()
        self._dsl_error_lbl.setWordWrap(True)
        self._dsl_error_lbl.hide()
        layout.addWidget(self._dsl_error_lbl)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._flush)

        self._apply_style()

    def load(self, strategy: PlayerCombatStrategy) -> None:
        self._strategy = strategy
        self._suppress = True
        self._name_edit.setText(strategy.name)
        self._description_edit.setText(strategy.description)
        self._school_combo.set_value(strategy.school)
        self._dsl_edit.setPlainText(strategy.dsl)
        self._suppress = False
        self._update_dsl_validation()

    def flush(self) -> None:
        """Immediately commit any pending changes."""
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._flush()

    def clear(self) -> None:
        """Detach from the loaded strategy and drop any pending save.

        Call this before deleting the strategy being edited — otherwise the
        debounce timer fires afterwards and writes the file back out.
        """
        self._save_timer.stop()
        self._strategy = None
        self._suppress = True
        self._name_edit.clear()
        self._description_edit.clear()
        self._school_combo.set_value(None)
        self._dsl_edit.clear()
        self._suppress = False
        self._update_dsl_validation()

    def _on_changed(self) -> None:
        if self._suppress or self._strategy is None:
            return
        self._save_timer.stop()
        self._save_timer.start()

    def _flush(self) -> None:
        if self._strategy is None:
            return
        self._strategy.name = self._name_edit.text().strip() or self._strategy.name
        self._strategy.description = self._description_edit.text()
        self._strategy.school = self._school_combo.value()
        self._strategy.dsl = self._dsl_edit.toPlainText()
        self._update_dsl_validation()
        self.changed.emit(self._strategy.id)

    def _update_dsl_validation(self) -> None:
        """Re-run ``validate_dsl`` and reflect it as a border color plus an error line.

        Piggybacks on the same debounce as saving (called from ``_flush``, and
        once up front from ``load``/``clear``) rather than on every keystroke —
        a half-typed DSL is invalid by definition and must not nag.
        """
        error = validate_dsl(self._dsl_edit.toPlainText())
        self._dsl_error_lbl.setText(error or "")
        self._dsl_error_lbl.setVisible(bool(error))
        self._apply_style()

    def _apply_style(self) -> None:
        error = validate_dsl(self._dsl_edit.toPlainText())
        border = THEME.error if error else THEME.border
        self._dsl_edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {THEME.bg}; color: {THEME.text}; "
            f"border: 1px solid {THEME.border}; border-left: 3px solid {border}; }}"
        )
        self._dsl_error_lbl.setStyleSheet(f"color: {THEME.error};")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._dsl_edit.setFont(THEME.font_mono())
            self._apply_style()
