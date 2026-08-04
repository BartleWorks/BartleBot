"""Editor widget for a single DuelCombatStrategy."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QSize, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.strategy_models import (
    DuelCombatStrategy,
    PlayerAssignment,
    PlayerCombatStrategy,
)
from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.sidebar.sections.combat_order_widget import CombatOrderWidget
from bartlebot.gui.strategy_library.strategy_picker_dialog import StrategyPickerDialog
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.player_list_widget import PlayerListWidget
from bartlebot.strategy_store import save_player_strategy

_DEBOUNCE_MS = 500
_ICON_BTN_SIZE = 16
_DSL_EDIT_HEIGHT = 56


class _AssignmentRow(QWidget):
    """One player-assignment row: player checkboxes over an inline DSL editor.

    Inline mode (the common case) edits ``assignment.dsl`` directly.  Linked
    mode shows the name of a shared PlayerCombatStrategy preset and renders its
    DSL read-only — edit it in the Player Strategies tab, or unlink to fork a
    private copy.
    """

    remove_requested = pyqtSignal(object)  # emits self
    changed = pyqtSignal()
    # Emits (self, newly_added_set) after a user-driven player change.
    # Only newly added players are reported so exclusivity only evicts what was just claimed.
    players_changed = pyqtSignal(object, object)
    # Emitted after this row promotes its inline DSL to a new library preset.
    promoted = pyqtSignal()

    def __init__(self, assignment: PlayerAssignment, all_pcs: list[PlayerCombatStrategy], parent=None):
        super().__init__(parent)
        self._assignment = assignment
        self._all_pcs = all_pcs
        self._updating = False  # suppresses re-entrant enforcement
        self._suppress_dsl = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Top row: player checkboxes | link label | menu | remove
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)

        self._player_widget = PlayerListWidget()
        self._player_widget.set_value(assignment.players)
        self._player_widget.changed.connect(self._on_players_changed)
        top.addWidget(self._player_widget)

        self._link_lbl = QLabel()
        self._link_lbl.setToolTip("This assignment uses a shared preset from the Player Strategies tab")
        top.addWidget(self._link_lbl, 1)

        self._menu_btn = QToolButton()
        self._menu_btn.setIcon(svg_icon("more_vert", _ICON_BTN_SIZE))
        self._menu_btn.setIconSize(QSize(_ICON_BTN_SIZE, _ICON_BTN_SIZE))
        self._menu_btn.setToolTip("Preset actions")
        self._menu_btn.setAutoRaise(True)
        self._menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu_btn.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        self._menu = QMenu(self._menu_btn)
        self._menu.aboutToShow.connect(self._rebuild_menu)
        self._menu_btn.setMenu(self._menu)
        top.addWidget(self._menu_btn)

        remove_btn = QPushButton("×")
        remove_btn.setFixedWidth(24)
        remove_btn.setToolTip("Remove this assignment")
        remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        top.addWidget(remove_btn)

        layout.addLayout(top)

        self._dsl_edit = QPlainTextEdit()
        self._dsl_edit.setFixedHeight(_DSL_EDIT_HEIGHT)
        self._dsl_edit.setPlaceholderText("Combat DSL for these players — blank uses the built-in default")
        self._dsl_edit.textChanged.connect(self._on_dsl_changed)
        layout.addWidget(self._dsl_edit)

        self._apply_style()
        self._refresh_mode()

    def assignment(self) -> PlayerAssignment:
        return self._assignment

    def set_pcs_list(self, all_pcs: list[PlayerCombatStrategy]) -> None:
        self._all_pcs = all_pcs
        self._refresh_mode()

    # -- mode / display ------------------------------------------------

    def _linked_pcs(self) -> PlayerCombatStrategy | None:
        pcs_id = self._assignment.player_combat_strategy_id
        if pcs_id is None:
            return None
        return next((p for p in self._all_pcs if p.id == pcs_id), None)

    def _refresh_mode(self) -> None:
        """Sync label, DSL text and read-only state to inline vs linked mode."""
        self._suppress_dsl = True
        try:
            if self._assignment.is_linked:
                pcs = self._linked_pcs()
                name = pcs.name if pcs else "(missing preset)"
                self._link_lbl.setText(f"🔗 {name}")
                self._link_lbl.show()
                self._dsl_edit.setPlainText(pcs.dsl if pcs else "")
                self._dsl_edit.setReadOnly(True)
            else:
                self._link_lbl.clear()
                self._link_lbl.hide()
                self._dsl_edit.setPlainText(self._assignment.dsl)
                self._dsl_edit.setReadOnly(False)
        finally:
            self._suppress_dsl = False
        self._apply_style()

    def _on_dsl_changed(self) -> None:
        if self._suppress_dsl or self._assignment.is_linked:
            return
        self._assignment.dsl = self._dsl_edit.toPlainText()
        self.changed.emit()

    # -- preset actions ------------------------------------------------

    def _rebuild_menu(self) -> None:
        self._menu.clear()
        if self._assignment.is_linked:
            self._menu.addAction("Change preset…", self._on_pick_pcs)
            self._menu.addAction("Unlink (edit a private copy)", self._on_unlink)
        else:
            self._menu.addAction("Save as preset…", self._on_promote)
            self._menu.addAction("Link existing preset…", self._on_pick_pcs)

    def _on_promote(self) -> None:
        """Copy this row's inline DSL into a new library preset and link to it."""
        dsl = self._assignment.dsl.strip()
        if not dsl:
            QMessageBox.information(self, "Nothing to save", "Enter some DSL before saving it as a preset.")
            return
        name, ok = QInputDialog.getText(self, "Save as Preset", "Preset name:")
        if not ok or not name.strip():
            return
        pcs = PlayerCombatStrategy(name=name.strip(), dsl=self._assignment.dsl)
        save_player_strategy(pcs)
        self._all_pcs.append(pcs)
        self._assignment.player_combat_strategy_id = pcs.id
        self._refresh_mode()
        self.changed.emit()
        self.promoted.emit()

    def _on_unlink(self) -> None:
        """Fork the linked preset's DSL back into this assignment as inline text."""
        pcs = self._linked_pcs()
        self._assignment.dsl = pcs.dsl if pcs else ""
        self._assignment.player_combat_strategy_id = None
        self._refresh_mode()
        self.changed.emit()

    def _on_pick_pcs(self) -> None:
        if not self._all_pcs:
            QMessageBox.information(
                self,
                "No presets",
                "No shared player strategies exist yet. Use “Save as preset…” to create one.",
            )
            return
        dlg = StrategyPickerDialog(
            title="Pick Player Strategy",
            mode="player",
            strategies=self._all_pcs,
            current_id=self._assignment.player_combat_strategy_id,
            allow_none=False,
            parent=self,
        )
        if dlg.exec():
            chosen = dlg.selected_id()
            if chosen is not None:
                self._assignment.player_combat_strategy_id = chosen
                self._refresh_mode()
                self.changed.emit()

    def _apply_style(self) -> None:
        bg = THEME.surface if self._dsl_edit.isReadOnly() else THEME.bg
        self._dsl_edit.setFont(THEME.font_mono())
        self._dsl_edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {bg}; color: {THEME.text}; border: 1px solid {THEME.border}; }}"
        )
        self._link_lbl.setStyleSheet(f"color: {THEME.text_muted};")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_style()

    def set_max_players(self, n: int) -> None:
        """Hide checkboxes beyond n and clamp assignment.players to 1..n."""
        self._player_widget.set_max_players(n)
        filtered = [p for p in self._assignment.players if p <= n]
        if filtered != self._assignment.players:
            self._assignment.players = filtered
            self.changed.emit()

    def update_players_silently(self, players: list[int]) -> None:
        """Apply an externally-enforced player list without triggering exclusivity enforcement."""
        self._updating = True
        self._assignment.players = players
        self._player_widget.set_value(players)
        self._updating = False
        self.changed.emit()

    def _on_players_changed(self) -> None:
        if self._updating:
            return
        old = set(self._assignment.players)
        self._assignment.players = self._player_widget.get_value()
        newly_added = set(self._assignment.players) - old
        self.players_changed.emit(self, newly_added)
        self.changed.emit()


class DuelStrategyEditor(QWidget):
    """Edit a DuelCombatStrategy: name, min/max players, combat order, player assignments."""

    # Emitted after debounce with the id of the strategy the edits belong to.
    # See PlayerStrategyEditor.changed — the id is what keeps a late-firing save
    # from being applied to whichever strategy the user selected in the meantime.
    changed = pyqtSignal(str)
    presets_changed = pyqtSignal()  # a row promoted its inline DSL to a new library preset

    def __init__(self, parent=None):
        super().__init__(parent)
        self._strategy: DuelCombatStrategy | None = None
        self._all_pcs: list[PlayerCombatStrategy] = []
        self._suppress = False
        self._rows: list[_AssignmentRow] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        # Name row
        outer.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.textChanged.connect(self._on_changed)
        outer.addWidget(self._name_edit)

        # Min/max players
        mm_row = QHBoxLayout()
        mm_row.setSpacing(8)
        mm_row.addWidget(QLabel("Min players:"))
        self._min_spin = QSpinBox()
        self._min_spin.setRange(1, 4)
        self._min_spin.valueChanged.connect(self._on_changed)
        mm_row.addWidget(self._min_spin)
        mm_row.addWidget(QLabel("Max players:"))
        self._max_spin = QSpinBox()
        self._max_spin.setRange(1, 4)
        self._max_spin.valueChanged.connect(self._on_changed)
        self._max_spin.valueChanged.connect(self._on_max_players_changed)
        mm_row.addWidget(self._max_spin)
        mm_row.addStretch()
        outer.addLayout(mm_row)

        # Combat order
        outer.addWidget(QLabel("Combat order:"))
        self._order_widget = CombatOrderWidget(empty_label="Simultaneous entry")
        self._order_widget.changed.connect(self._on_changed)
        outer.addWidget(self._order_widget)

        # Player assignments
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        outer.addWidget(sep)
        outer.addWidget(QLabel("Player assignments:"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(60)
        self._assignments_container = QWidget()
        self._assignments_layout = QVBoxLayout(self._assignments_container)
        self._assignments_layout.setContentsMargins(0, 0, 0, 0)
        self._assignments_layout.setSpacing(4)
        self._assignments_layout.addStretch()
        scroll.setWidget(self._assignments_container)
        outer.addWidget(scroll, 1)

        self._add_btn = QPushButton("+ Add assignment")
        self._add_btn.clicked.connect(self._on_add_assignment)
        outer.addWidget(self._add_btn)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._flush)

    def load(self, strategy: DuelCombatStrategy, all_pcs: list[PlayerCombatStrategy]) -> None:
        self._strategy = strategy
        self._all_pcs = all_pcs
        self._suppress = True
        self._name_edit.setText(strategy.name)
        self._min_spin.setValue(strategy.min_players)
        self._max_spin.setValue(strategy.max_players)
        self._order_widget.set_value(strategy.combat_order or [])
        self._rebuild_assignment_rows()
        self._suppress = False
        self._apply_max_players_constraints(strategy.max_players)
        self._update_add_btn()

    def set_pcs_list(self, all_pcs: list[PlayerCombatStrategy]) -> None:
        self._all_pcs = all_pcs
        for row in self._rows:
            row.set_pcs_list(all_pcs)

    def flush(self) -> None:
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
        self._rebuild_assignment_rows()
        self._suppress = False

    def _rebuild_assignment_rows(self) -> None:
        for row in self._rows:
            self._assignments_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        if self._strategy:
            for assignment in self._strategy.player_assignments:
                self._add_row(assignment)

    def _add_row(self, assignment: PlayerAssignment) -> None:
        row = _AssignmentRow(assignment, self._all_pcs, parent=self._assignments_container)
        row.changed.connect(self._on_changed)
        row.remove_requested.connect(self._on_remove_row)
        row.players_changed.connect(self._enforce_exclusivity)
        row.promoted.connect(self.presets_changed)
        # Insert before the stretch at end
        idx = self._assignments_layout.count() - 1
        self._assignments_layout.insertWidget(idx, row)
        self._rows.append(row)

    def _enforce_exclusivity(self, source_row: _AssignmentRow, newly_added: set) -> None:
        """Remove newly-claimed players from all other rows."""
        if not newly_added:
            return
        for row in self._rows:
            if row is source_row:
                continue
            current = row.assignment().players
            remaining = [p for p in current if p not in newly_added]
            if remaining != current:
                row.update_players_silently(remaining)

    def _on_add_assignment(self) -> None:
        if self._strategy is None:
            return
        max_p = self._max_spin.value()
        if len(self._rows) >= max_p:
            return
        # Default to players not yet claimed by any existing row
        claimed = {p for row in self._rows for p in row.assignment().players}
        unclaimed = [p for p in range(1, max_p + 1) if p not in claimed]
        # New assignments start inline and empty — no library entry required.
        assignment = PlayerAssignment(players=unclaimed)
        self._strategy.player_assignments.append(assignment)
        self._add_row(assignment)
        # Apply current max_players constraint to the new row
        self._rows[-1].set_max_players(max_p)
        self._update_add_btn()
        self._schedule_save()

    def _on_remove_row(self, row: _AssignmentRow) -> None:
        if self._strategy is None:
            return
        assignment = row.assignment()
        self._strategy.player_assignments = [a for a in self._strategy.player_assignments if a is not assignment]
        self._assignments_layout.removeWidget(row)
        row.deleteLater()
        self._rows.remove(row)
        self._update_add_btn()
        self._schedule_save()

    def _on_max_players_changed(self, value: int) -> None:
        if self._suppress:
            return
        self._apply_max_players_constraints(value)
        self._update_add_btn()

    def _apply_max_players_constraints(self, n: int) -> None:
        """Propagate max_players to order widget and all assignment rows."""
        self._order_widget.set_max_players(n)
        for row in self._rows:
            row.set_max_players(n)

    def _update_add_btn(self) -> None:
        max_p = self._max_spin.value()
        self._add_btn.setEnabled(len(self._rows) < max_p)

    def _on_changed(self) -> None:
        if self._suppress:
            return
        self._schedule_save()

    def _schedule_save(self) -> None:
        self._save_timer.stop()
        self._save_timer.start()

    def _flush(self) -> None:
        if self._strategy is None:
            return
        name = self._name_edit.text().strip()
        if name:
            self._strategy.name = name
        self._strategy.min_players = self._min_spin.value()
        self._strategy.max_players = self._max_spin.value()
        order = self._order_widget.get_value()
        self._strategy.combat_order = order if order else None
        self.changed.emit(self._strategy.id)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        # No stylesheet-dependent styling in this widget currently.
