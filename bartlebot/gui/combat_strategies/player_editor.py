"""Editor widget for a single PlayerCombatStrategy (name, description, school, DSL).

The 500ms autosave this widget used to own is gone. It wrote a shipped file
half a second after every keystroke, which is why Cancel could not mean anything
and why a half-typed DSL was persisted; it also needed the ``changed(id)``
signal to carry an id, because a debounced save could land after the user had
already selected a different strategy in the list.

What replaced it is a boundary rather than a better timer: the strategy handed
to :meth:`load` is a working copy out of
:class:`~bartlebot.strategy_workset.StrategyWorkingSet`, so this editor writes
through to it on every edit and nothing reaches disk until the dialog is saved.
``changed`` is emitted for whoever wants to redraw a card, and carries nothing —
there is no longer a delay across which the selection could have moved.

The one thing still debounced is DSL *validation*, which was never about saving:
a half-typed DSL is invalid by definition and must not nag.
"""

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

#: How long after the last keystroke the DSL is re-validated. Not a save delay.
_VALIDATE_MS = 500

_DSL_HELP = (
    "{round} move @ target | move @ target | pass — rounds optional, '|' is priority order.\n"
    'Moves: "Named Spell" or any<damage|heal|blade|trap|charm|ward|aura|...>[enchant]\n'
    "Targets: self, enemy, boss, ally, aoe, named:<name>\n"
    "Pick one out with ally(0) or enemy(1). ally(last) is the last player on the team, you included,\n"
    "so every client in the group can share the line."
)


class PlayerStrategyEditor(QWidget):
    """Edit a PlayerCombatStrategy in place. Writes through; never saves."""

    #: An edit landed on the loaded strategy. Carries no id on purpose — the edit
    #: is already applied by the time this fires, so there is no stale-selection
    #: window for an id to guard against.
    changed = pyqtSignal()

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
        # assignment links this strategy without its own school set.
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

        self._validate_timer = QTimer(self)
        self._validate_timer.setSingleShot(True)
        self._validate_timer.setInterval(_VALIDATE_MS)
        self._validate_timer.timeout.connect(self._update_dsl_validation)

        self._apply_style()

    def strategy(self) -> PlayerCombatStrategy | None:
        """The working copy being edited, or None."""
        return self._strategy

    def load(self, strategy: PlayerCombatStrategy) -> None:
        self._strategy = strategy
        self._suppress = True
        self._name_edit.setText(strategy.name)
        self._description_edit.setText(strategy.description)
        self._school_combo.set_value(strategy.school)
        self._dsl_edit.setPlainText(strategy.dsl)
        self._suppress = False
        self._validate_timer.stop()
        self._update_dsl_validation()

    def clear(self) -> None:
        """Detach from the loaded strategy. Nothing is pending — every edit is
        already live on the working copy.
        """
        self._validate_timer.stop()
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
        # An empty name would leave a nameless card in the list with no way back
        # to it, so the last non-empty name stands until another is typed.
        self._strategy.name = self._name_edit.text().strip() or self._strategy.name
        self._strategy.description = self._description_edit.text()
        self._strategy.school = self._school_combo.value()
        self._strategy.dsl = self._dsl_edit.toPlainText()
        self._validate_timer.stop()
        self._validate_timer.start()
        self.changed.emit()

    def _update_dsl_validation(self) -> None:
        """Re-run ``validate_dsl`` and reflect it as a border color plus an error line."""
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
