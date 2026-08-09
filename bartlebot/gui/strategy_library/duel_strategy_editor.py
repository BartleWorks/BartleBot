"""Editor widgets for DuelConfig and the named DuelCombatStrategy that wraps it."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QTextLayout
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
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.strategy_models import (
    DuelCombatStrategy,
    DuelConfig,
    PlayerAssignment,
    PlayerCombatStrategy,
)
from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.sidebar.sections.combat_order_widget import CombatOrderWidget
from bartlebot.gui.strategy_library.strategy_picker_dialog import StrategyPickerDialog
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.elided_label import ElidedLabel, apply_muted_color
from bartlebot.gui.widgets.player_list_widget import PlayerListWidget
from bartlebot.strategy_store import save_player_strategy

_ICON_BTN_SIZE = 16
_DSL_EDIT_MIN_HEIGHT = 44  # ~2 lines, so an empty/short DSL box doesn't collapse to nothing


def _wrapped_line_count(text: str, width: int, font) -> int:
    """Count the visual lines ``text`` occupies when word-wrapped to ``width``.

    ``QPlainTextEdit``'s own ``document().size()`` is unreliable for this before
    the widget has gone through a real paint cycle — it reports a placeholder
    height regardless of content until then.  Laying the text out directly with
    ``QTextLayout`` (the same primitive Qt uses internally for word wrap) sidesteps
    that: it depends only on font metrics and the text, not on paint timing.
    """
    total = 0
    for paragraph in text.split("\n"):
        layout = QTextLayout(paragraph, font)
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            total += 1
        layout.endLayout()
    return max(total, 1)


class _AutoHeightTextEdit(QPlainTextEdit):
    """QPlainTextEdit that always grows to fit its full wrapped content.

    A fixed-height box hid all but the first line or two of most real DSLs —
    this shows everything up front, at the cost of the row taking however much
    vertical space its text actually needs.  Collapse the panel around it (or
    the SidebarSection it lives in) to reclaim room instead of relying on an
    inner scrollbar nobody could see was there.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.textChanged.connect(self._sync_height)
        self._sync_height()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_height()

    def _sync_height(self) -> None:
        width = max(self.viewport().width(), 1)
        lines = _wrapped_line_count(self.toPlainText(), width, self.font())
        margins = self.contentsMargins()
        frame = 2 * self.frameWidth()
        height = lines * self.fontMetrics().lineSpacing() + margins.top() + margins.bottom() + frame + 8
        self.setFixedHeight(max(height, _DSL_EDIT_MIN_HEIGHT))


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
        self._editable = True

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

        # Elided: preset names are user-supplied, and a plain QLabel would make
        # a long one the minimum width of every panel this row sits in.
        self._link_lbl = ElidedLabel()
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

        self._remove_btn = QPushButton("×")
        self._remove_btn.setFixedWidth(24)
        self._remove_btn.setToolTip("Remove this assignment")
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        top.addWidget(self._remove_btn)

        layout.addLayout(top)

        self._dsl_edit = _AutoHeightTextEdit()
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

    def set_editable(self, editable: bool) -> None:
        """Lock or unlock every control in this row (used while a bot is running)."""
        self._editable = editable
        self._player_widget.setEnabled(editable)
        self._menu_btn.setEnabled(editable)
        self._menu_btn.setToolTip("Preset actions" if editable else "Locked while the bot is running")
        self._remove_btn.setEnabled(editable)
        self._remove_btn.setToolTip("Remove this assignment" if editable else "Locked while the bot is running")
        # Goes through _refresh_mode rather than setReadOnly directly: read-only
        # is also how *linked* rows are rendered, and _refresh_mode is the single
        # place that reconciles the two reasons a row can be non-writable.
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
                # Set after setText: ElidedLabel defaults the tooltip to the full
                # text, and this says the same thing plus where to go to edit it.
                self._link_lbl.setToolTip(f"{name} — shared preset from the Player Strategies tab")
                self._link_lbl.show()
                self._dsl_edit.setPlainText(pcs.dsl if pcs else "")
                self._dsl_edit.setReadOnly(True)
            else:
                self._link_lbl.clear()
                self._link_lbl.hide()
                self._dsl_edit.setPlainText(self._assignment.dsl)
                self._dsl_edit.setReadOnly(not self._editable)
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
        apply_muted_color(self._link_lbl)

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


class DuelConfigEditor(QWidget):
    """Edit a DuelConfig: min/max players, combat order, player assignments.

    Mutates the loaded ``DuelConfig`` directly as the user edits — there is no
    separate "apply" step, and no autosave of its own.  ``changed`` fires on
    every edit so the embedder decides whether and when to persist: either
    ``DuelStrategyEditor`` debounce-saving a named library entry that IS-A
    ``DuelConfig``, or an inline config embedded directly on a
    ``BotCombatStrategy`` with its own debounce timer.
    """

    changed = pyqtSignal()
    presets_changed = pyqtSignal()  # a row promoted its inline DSL to a new library preset

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: DuelConfig | None = None
        self._all_pcs: list[PlayerCombatStrategy] = []
        self._suppress = False
        self._rows: list[_AssignmentRow] = []
        self._editable = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

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

        # No inner QScrollArea: this widget already lives inside the Combat
        # tab's single outer scroll area (or the Strategy Library window's), so
        # nesting a second scrollable region here just hid rows behind a tiny
        # inner scrollbar nobody could see. Assignment rows render at their
        # full natural height and the page around them scrolls instead.
        self._assignments_container = QWidget()
        self._assignments_layout = QVBoxLayout(self._assignments_container)
        self._assignments_layout.setContentsMargins(0, 0, 0, 0)
        self._assignments_layout.setSpacing(4)
        self._assignments_layout.addStretch()
        outer.addWidget(self._assignments_container)

        self._add_btn = QPushButton("+ Add assignment")
        self._add_btn.clicked.connect(self._on_add_assignment)
        outer.addWidget(self._add_btn)

    def load(self, config: DuelConfig, all_pcs: list[PlayerCombatStrategy]) -> None:
        self._config = config
        self._all_pcs = all_pcs
        self._suppress = True
        self._min_spin.setValue(config.min_players)
        self._max_spin.setValue(config.max_players)
        self._order_widget.set_value(config.combat_order or [])
        self._rebuild_assignment_rows()
        self._suppress = False
        self._apply_max_players_constraints(config.max_players)
        self._update_add_btn()

    def set_pcs_list(self, all_pcs: list[PlayerCombatStrategy]) -> None:
        self._all_pcs = all_pcs
        for row in self._rows:
            row.set_pcs_list(all_pcs)

    def set_editable(self, editable: bool) -> None:
        """Lock or unlock every control, including rows added later.

        Needed because this editor is embedded in the Combat tab, which locks
        itself while a bot runs.  Without it, an expanded inline config stayed
        fully writable mid-run.
        """
        self._editable = editable
        self._min_spin.setEnabled(editable)
        self._max_spin.setEnabled(editable)
        self._order_widget.set_editable(editable)
        for row in self._rows:
            row.set_editable(editable)
        self._update_add_btn()

    def clear(self) -> None:
        """Detach from the loaded config — no pending state to drop, unlike the
        debounced editors, since every edit here is already live on the config.
        """
        self._config = None
        self._suppress = True
        self._rebuild_assignment_rows()
        self._suppress = False

    def _rebuild_assignment_rows(self) -> None:
        for row in self._rows:
            self._assignments_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        if self._config:
            for assignment in self._config.player_assignments:
                self._add_row(assignment)

    def _add_row(self, assignment: PlayerAssignment) -> None:
        row = _AssignmentRow(assignment, self._all_pcs, parent=self._assignments_container)
        row.set_editable(self._editable)
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
        if self._config is None:
            return
        max_p = self._max_spin.value()
        if len(self._rows) >= max_p:
            return
        # Default to players not yet claimed by any existing row
        claimed = {p for row in self._rows for p in row.assignment().players}
        unclaimed = [p for p in range(1, max_p + 1) if p not in claimed]
        # New assignments start inline and empty — no library entry required.
        assignment = PlayerAssignment(players=unclaimed)
        self._config.player_assignments.append(assignment)
        self._add_row(assignment)
        # Apply current max_players constraint to the new row
        self._rows[-1].set_max_players(max_p)
        self._update_add_btn()
        self.changed.emit()

    def _on_remove_row(self, row: _AssignmentRow) -> None:
        if self._config is None:
            return
        assignment = row.assignment()
        self._config.player_assignments = [a for a in self._config.player_assignments if a is not assignment]
        self._assignments_layout.removeWidget(row)
        row.deleteLater()
        self._rows.remove(row)
        self._update_add_btn()
        self.changed.emit()

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
        """Enable the add button, and name the reason when it is off.

        There are only two, and neither is guessable from a button that simply
        stops responding: the player slots are all spoken for, or the whole
        editor is locked because a bot is running.
        """
        max_p = self._max_spin.value()
        room = len(self._rows) < max_p
        self._add_btn.setEnabled(self._editable and room)
        if not self._editable:
            self._add_btn.setToolTip("Locked while the bot is running")
        elif not room:
            self._add_btn.setToolTip(
                f"Every player slot (P1–P{max_p}) already has an assignment — "
                f"raise Max players, or remove an assignment first"
            )
        else:
            self._add_btn.setToolTip("Add a player assignment to this duel config")

    def _on_changed(self) -> None:
        if self._suppress or self._config is None:
            return
        self._config.min_players = self._min_spin.value()
        self._config.max_players = self._max_spin.value()
        order = self._order_widget.get_value()
        self._config.combat_order = order if order else None
        self.changed.emit()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        # No stylesheet-dependent styling in this widget currently.


class DuelStrategyEditor(QWidget):
    """Edit a DuelCombatStrategy: name, plus the shared DuelConfig fields.

    A thin wrapper around DuelConfigEditor that adds the library entry's
    identity (name) and owns the debounce/autosave that a named, shared entry
    needs but a bare DuelConfig does not.
    """

    # Emitted after debounce with the id of the strategy the edits belong to.
    # See PlayerStrategyEditor.changed — the id is what keeps a late-firing save
    # from being applied to whichever strategy the user selected in the meantime.
    changed = pyqtSignal(str)
    presets_changed = pyqtSignal()  # a row promoted its inline DSL to a new library preset

    _DEBOUNCE_MS = 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self._strategy: DuelCombatStrategy | None = None
        self._suppress = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        outer.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.textChanged.connect(self._on_changed)
        outer.addWidget(self._name_edit)

        self._config_editor = DuelConfigEditor()
        self._config_editor.changed.connect(self._on_changed)
        self._config_editor.presets_changed.connect(self.presets_changed)
        outer.addWidget(self._config_editor, 1)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self._DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._flush)

    def load(self, strategy: DuelCombatStrategy, all_pcs: list[PlayerCombatStrategy]) -> None:
        self._strategy = strategy
        self._suppress = True
        self._name_edit.setText(strategy.name)
        self._config_editor.load(strategy, all_pcs)  # DuelCombatStrategy IS-A DuelConfig
        self._suppress = False

    def set_pcs_list(self, all_pcs: list[PlayerCombatStrategy]) -> None:
        self._config_editor.set_pcs_list(all_pcs)

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
        self._config_editor.clear()
        self._suppress = False

    def _on_changed(self) -> None:
        if self._suppress:
            return
        self._save_timer.stop()
        self._save_timer.start()

    def _flush(self) -> None:
        if self._strategy is None:
            return
        # min_players / max_players / combat_order / player_assignments are
        # already live on self._strategy — DuelConfigEditor mutates it directly.
        name = self._name_edit.text().strip()
        if name:
            self._strategy.name = name
        self.changed.emit(self._strategy.id)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        # No stylesheet-dependent styling in this widget currently.
