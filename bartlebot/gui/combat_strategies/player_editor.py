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

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.strategy_models import PlayerCombatStrategy
from bartlebot.dsl_syntax import DSL_HELP
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.dsl_edit import DslPlainTextEdit
from bartlebot.gui.widgets.school_picker import SchoolPickerButton
from bartlebot.nodes.combat_runner import dsl_error, dsl_warning_text, dsl_warnings, validate_dsl

#: How long after the last keystroke the DSL is re-validated. Not a save delay.
_VALIDATE_MS = 500


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

        # No name or description field. Both moved up into the detail pane's *About*
        # section, which every tab now shares — they identify the library entry rather
        # than describe the strategy, and three tabs each drawing them in a different
        # arrangement is what the About section exists to end. See
        # :class:`~bartlebot.gui.combat_strategies.dialog.AboutSection`.
        school_row = QHBoxLayout()
        school_row.addWidget(QLabel("Schools:"))
        # Authored, never inferred from the DSL — used as a fallback wherever an
        # assignment links this strategy without its own school set.
        self._school_picker = SchoolPickerButton()
        self._school_picker.setToolTip("The schools this strategy is written for, if any — descriptive only")
        self._school_picker.selection_changed.connect(self._on_changed)
        school_row.addWidget(self._school_picker)
        school_row.addStretch()
        layout.addLayout(school_row)

        dsl_lbl = QLabel("DSL:")
        dsl_lbl.setToolTip(DSL_HELP)
        layout.addWidget(dsl_lbl)
        self._dsl_edit = DslPlainTextEdit()
        self._dsl_edit.setFont(THEME.font_mono())
        self._dsl_edit.setToolTip(DSL_HELP)
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

    @property
    def school_picker(self) -> SchoolPickerButton:
        """The schools control. Public so a test can pick a school as a user would."""
        return self._school_picker

    def strategy(self) -> PlayerCombatStrategy | None:
        """The working copy being edited, or None."""
        return self._strategy

    def load(self, strategy: PlayerCombatStrategy) -> None:
        self._strategy = strategy
        self._suppress = True
        self._school_picker.set_value(strategy.schools)
        self._dsl_edit.setPlainText(strategy.dsl)
        self._suppress = False
        self._validate_timer.stop()
        self._update_dsl_validation()

    def focus_dsl(self) -> None:
        """Put the caret in the DSL box — the field someone arriving to edit a strategy wants."""
        self._dsl_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def clear(self) -> None:
        """Detach from the loaded strategy. Nothing is pending — every edit is
        already live on the working copy.
        """
        self._validate_timer.stop()
        self._strategy = None
        self._suppress = True
        self._school_picker.set_value([])
        self._dsl_edit.clear()
        self._suppress = False
        self._update_dsl_validation()

    def _on_changed(self) -> None:
        if self._suppress or self._strategy is None:
            return
        self._strategy.schools = self._school_picker.value()
        dsl = self._dsl_edit.toPlainText()
        if dsl != self._strategy.dsl:
            # The underlines mark columns in the text as it was validated; after an
            # edit they point at the wrong characters until the debounce re-checks.
            self._dsl_edit.set_dsl_error(None)
            self._dsl_edit.set_dsl_warnings(())
        self._strategy.dsl = dsl
        self._validate_timer.stop()
        self._validate_timer.start()
        self.changed.emit()

    def _update_dsl_validation(self) -> None:
        """Re-parse the DSL and reflect it as a border colour, a line of text and underlines.

        A parse error takes the line; otherwise it lists the spell names no card has.
        Only the error colours the border — an unknown name still runs.
        """
        dsl = self._dsl_edit.toPlainText()
        error = dsl_error(dsl)
        warnings = dsl_warnings(dsl)
        self._dsl_error_lbl.setText(error.message if error else dsl_warning_text(warnings))
        self._dsl_error_lbl.setVisible(bool(error or warnings))
        self._dsl_edit.set_dsl_error(error)
        self._dsl_edit.set_dsl_warnings(warnings)
        self._apply_style()

    def _apply_style(self) -> None:
        error = validate_dsl(self._dsl_edit.toPlainText())
        border = THEME.error if error else THEME.border
        self._dsl_edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {THEME.bg}; color: {THEME.text}; "
            f"border: 1px solid {THEME.border}; border-left: 3px solid {border}; }}"
        )
        self._dsl_error_lbl.setStyleSheet(f"color: {THEME.error if error else THEME.syntax_warning};")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._dsl_edit.setFont(THEME.font_mono())
            self._apply_style()
