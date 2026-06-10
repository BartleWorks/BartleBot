"""Editor widget for a single DuelCombatStrategy."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.strategy_models import (
    DuelCombatStrategy,
    PlayerAssignment,
    PlayerCombatStrategy,
)
from bartlebot.gui.sidebar.sections.combat_order_widget import CombatOrderWidget
from bartlebot.gui.strategy_library.strategy_picker_dialog import StrategyPickerDialog
from bartlebot.gui.widgets.player_list_widget import PlayerListWidget

_DEBOUNCE_MS = 500


class _AssignmentRow(QWidget):
    """One player-assignment row: player-list checkboxes + PCS chip."""

    remove_requested = pyqtSignal(object)  # emits self
    changed = pyqtSignal()
    # Emits (self, newly_added_set) after a user-driven player change.
    # Only newly added players are reported so exclusivity only evicts what was just claimed.
    players_changed = pyqtSignal(object, object)

    def __init__(self, assignment: PlayerAssignment, all_pcs: list[PlayerCombatStrategy], parent=None):
        super().__init__(parent)
        self._assignment = assignment
        self._all_pcs = all_pcs
        self._updating = False  # suppresses re-entrant enforcement

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._player_widget = PlayerListWidget()
        self._player_widget.set_value(assignment.players)
        self._player_widget.changed.connect(self._on_players_changed)
        layout.addWidget(self._player_widget, 1)

        self._pcs_chip = QPushButton()
        self._pcs_chip.setMaximumWidth(160)
        self._pcs_chip.clicked.connect(self._on_pick_pcs)
        layout.addWidget(self._pcs_chip)

        remove_btn = QPushButton("×")
        remove_btn.setFixedWidth(24)
        remove_btn.setToolTip("Remove this assignment")
        remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        layout.addWidget(remove_btn)

        self._refresh_chip()

    def assignment(self) -> PlayerAssignment:
        return self._assignment

    def set_pcs_list(self, all_pcs: list[PlayerCombatStrategy]) -> None:
        self._all_pcs = all_pcs
        self._refresh_chip()

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

    def _on_pick_pcs(self) -> None:
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
                self._refresh_chip()
                self.changed.emit()

    def _refresh_chip(self) -> None:
        name = "(none)"
        for pcs in self._all_pcs:
            if pcs.id == self._assignment.player_combat_strategy_id:
                name = pcs.name
                break
        self._pcs_chip.setText(f"{name} ↗")
        self._pcs_chip.setToolTip("Click to change player strategy")


class DuelStrategyEditor(QWidget):
    """Edit a DuelCombatStrategy: name, min/max players, combat order, player assignments."""

    changed = pyqtSignal()

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
        if not self._all_pcs or self._strategy is None:
            return
        max_p = self._max_spin.value()
        if len(self._rows) >= max_p:
            return
        # Default to players not yet claimed by any existing row
        claimed = {p for row in self._rows for p in row.assignment().players}
        unclaimed = [p for p in range(1, max_p + 1) if p not in claimed]
        assignment = PlayerAssignment(
            players=unclaimed,
            player_combat_strategy_id=self._all_pcs[0].id,
        )
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
        self.changed.emit()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        # No stylesheet-dependent styling in this widget currently.
