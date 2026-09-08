"""Editor widgets for DuelConfig and the named DuelCombatStrategy that wraps it."""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, QEvent, QMimeData, QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QPainter, QPen, QTextLayout
from PyQt6.QtWidgets import (
    QApplication,
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
from bartlebot.gui.menu_utils import anchor_menu_to_window
from bartlebot.gui.sidebar.sections.combat_order_widget import CombatOrderWidget
from bartlebot.gui.strategy_library.strategy_picker_dialog import StrategyPickerDialog
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.elided_label import ElidedLabel, apply_muted_color
from bartlebot.gui.widgets.player_list_widget import PlayerListWidget
from bartlebot.gui.widgets.school_combo import SchoolCombo
from bartlebot.nodes.combat_runner import validate_dsl
from bartlebot.strategy_store import save_player_strategy

_ICON_BTN_SIZE = 16
_DSL_EDIT_MIN_HEIGHT = 44  # ~2 lines, so an empty/short DSL box doesn't collapse to nothing

_DSL_HELP = (
    "{round} move @ target | move @ target | pass — rounds optional, '|' is priority order.\n"
    'Moves: "Named Spell" or any<damage|heal|blade|trap|charm|ward|aura|...>[enchant]\n'
    "Targets: self, enemy, boss, ally, aoe, named:<name>"
)

#: Private to this widget, so a drag from anywhere else is ignored outright.
_ROW_MIME = "application/x-bartlebot-assignment-row"
_GRIP_GLYPH = "⠿"
_DROP_INDICATOR_WIDTH = 2
#: Cap on the drag ghost's height. Rows grow to fit their whole DSL, and a
#: full-height grab of a twenty-line strategy is a ghost the size of the panel.
_DRAG_PIXMAP_MAX_HEIGHT = 48


def move_item(items: list, src: int, dst: int) -> list:
    """``items`` with the element at ``src`` reinserted at position ``dst``.

    ``dst`` is interpreted against the list *as the caller sees it now* — the
    positions before the move — which is the only reading a drop indicator can
    mean. Removing the source first shifts everything after it down one, so a
    downward move has to compensate; that off-by-one is the whole function.

    Out-of-range or no-op moves return an unchanged copy rather than raising:
    a drop is a user gesture, not a contract.
    """
    if not (0 <= src < len(items)) or not (0 <= dst <= len(items)) or dst in (src, src + 1):
        return list(items)
    out = list(items)
    item = out.pop(src)
    out.insert(dst - 1 if dst > src else dst, item)
    return out


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

    Rows carry a grip on the left and can be dragged past one another to
    reorder; see :meth:`DuelConfigEditor.move_assignment` for why that is purely
    presentational.
    """

    remove_requested = pyqtSignal(object)  # emits self
    # Emits (source_row, self, before) when another row is dropped on this one.
    # ``before`` says which of this row's edges the indicator was showing.
    reorder_requested = pyqtSignal(object, object, bool)
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
        self._drag_start_pos: QPoint | None = None
        self._drop_edge: str | None = None  # "above" / "below" while a drop hovers
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Top row: player checkboxes | link label | menu | remove
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)

        self._grip = QLabel(_GRIP_GLYPH)
        self._grip.setToolTip("Drag to reorder this assignment")
        self._grip.setCursor(Qt.CursorShape.OpenHandCursor)
        top.addWidget(self._grip)

        self._player_widget = PlayerListWidget()
        self._player_widget.set_value(assignment.players)
        self._player_widget.changed.connect(self._on_players_changed)
        top.addWidget(self._player_widget)

        # Authored, never inferred from the DSL — see DuelConfig.school_requirements.
        self._school_combo = SchoolCombo()
        self._school_combo.setToolTip("What school this assignment needs, if any — descriptive only, never enforced")
        self._school_combo.set_value(assignment.school)
        self._school_combo.currentIndexChanged.connect(self._on_school_changed)
        top.addWidget(self._school_combo)

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
        self._menu = anchor_menu_to_window(QMenu(self._menu_btn))
        self._menu.aboutToShow.connect(self._rebuild_menu)
        self._menu_btn.setMenu(self._menu)
        top.addWidget(self._menu_btn)

        self._remove_btn = QPushButton("×")
        self._remove_btn.setFixedWidth(24)
        self._remove_btn.setToolTip("Remove this assignment")
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        top.addWidget(self._remove_btn)

        layout.addLayout(top)

        self._note_edit = QLineEdit()
        self._note_edit.setPlaceholderText('Note (optional) — e.g. "P2 passes to bank pips for round 3"')
        self._note_edit.setText(assignment.note)
        self._note_edit.editingFinished.connect(self._on_note_changed)
        layout.addWidget(self._note_edit)

        self._dsl_edit = _AutoHeightTextEdit()
        # The DSL box fills most of the row, and a QPlainTextEdit accepts drops
        # by default -- leaving that on would carve this row's drop target down
        # to a ~28px strip along the top. Dropping text *into* a DSL box is a
        # far rarer thing to want than dropping a row onto its neighbour.
        self._dsl_edit.setAcceptDrops(False)
        self._dsl_edit.setPlaceholderText("Combat DSL for these players — blank uses the built-in default")
        self._dsl_edit.setToolTip(_DSL_HELP)
        self._dsl_edit.textChanged.connect(self._on_dsl_changed)
        layout.addWidget(self._dsl_edit)

        self._dsl_error_lbl = QLabel()
        self._dsl_error_lbl.setWordWrap(True)
        self._dsl_error_lbl.hide()
        layout.addWidget(self._dsl_error_lbl)

        # Validation piggybacks on its own debounce rather than every keystroke —
        # a half-typed DSL is invalid by definition and must not nag. Kept
        # separate from any save-debounce further up the embedding chain, since
        # this row has no reliable access to that timer either way.
        self._validate_timer = QTimer(self)
        self._validate_timer.setSingleShot(True)
        self._validate_timer.setInterval(500)
        self._validate_timer.timeout.connect(self._update_dsl_validation)

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
        self._grip.setCursor(Qt.CursorShape.OpenHandCursor if editable else Qt.CursorShape.ArrowCursor)
        self._grip.setToolTip("Drag to reorder this assignment" if editable else "Locked while the bot is running")
        self._player_widget.setEnabled(editable)
        self._school_combo.setEnabled(editable)
        self._note_edit.setEnabled(editable)
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
        self._update_dsl_validation()

    def _on_dsl_changed(self) -> None:
        if self._suppress_dsl or self._assignment.is_linked:
            return
        self._assignment.dsl = self._dsl_edit.toPlainText()
        self._validate_timer.stop()
        self._validate_timer.start()
        self.changed.emit()

    def _update_dsl_validation(self) -> None:
        """Re-run ``validate_dsl`` and reflect it as a border color plus an error line."""
        error = validate_dsl(self._dsl_edit.toPlainText())
        self._dsl_error_lbl.setText(error or "")
        self._dsl_error_lbl.setVisible(bool(error))
        self._apply_style()

    def _on_school_changed(self) -> None:
        if self._suppress_dsl:
            return
        self._assignment.school = self._school_combo.value()
        self.changed.emit()

    def _on_note_changed(self) -> None:
        self._assignment.note = self._note_edit.text()
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
        # The assignment's own school (if set) already won over the preset's —
        # only fill it in here if nothing was authored on the assignment itself.
        if self._assignment.school is None and pcs is not None:
            self._assignment.school = pcs.school
            self._school_combo.set_value(self._assignment.school)
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
        error = validate_dsl(self._dsl_edit.toPlainText())
        border = THEME.error if error else THEME.border
        self._dsl_edit.setFont(THEME.font_mono())
        self._dsl_edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {bg}; color: {THEME.text}; "
            f"border: 1px solid {THEME.border}; border-left: 3px solid {border}; }}"
        )
        self._dsl_error_lbl.setStyleSheet(f"color: {THEME.error};")
        apply_muted_color(self._link_lbl)
        apply_muted_color(self._grip)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_style()

    # -- drag to reorder -----------------------------------------------
    #
    # Only the grip starts a drag.  Making the whole row the drag source would
    # take mouse-down inside the DSL box with it, and text selection there is
    # not worth trading away for a bigger handle.  The mechanics follow
    # _PlayerChip in combat_order_widget.py; what differs is that a drop here
    # *inserts* rather than swapping, so the target row also has to report
    # which of its edges was meant.

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.LeftButton and self._editable and self._over_grip(event.pos()):
            self._drag_start_pos = event.pos()
            # Accepted rather than passed on: a widget that ignores the press
            # does not go on to receive the moves, and the moves are what decide
            # whether this was a drag or a click.
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        if (
            self._drag_start_pos is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and self._editable
            and (event.pos() - self._drag_start_pos).manhattanLength() >= QApplication.startDragDistance()
        ):
            self._start_drag()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def _over_grip(self, pos) -> bool:  # noqa: ANN001
        """Whether a press at row-local ``pos`` landed on the grip.

        The grip sits in a nested layout but layouts do not reparent, so its
        geometry is already in this row's coordinates.  The press arrives here
        by propagation: a plain QLabel ignores mouse presses, and Qt re-sends
        what a child ignores to its parent.
        """
        return self._grip.geometry().contains(pos)

    def _start_drag(self) -> None:
        self._drag_start_pos = None
        mime = QMimeData()
        # The payload carries nothing: event.source() identifies the row, and
        # unlike a recorded index it cannot go stale across a move.  The format
        # is only there to make a foreign drag distinguishable.
        mime.setData(_ROW_MIME, QByteArray(b"1"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pixmap = self.grab()
        if pixmap.height() > _DRAG_PIXMAP_MAX_HEIGHT:
            # Rows grow to fit their whole DSL, and a full-height grab of a
            # twenty-line strategy is a ghost the size of the panel.
            pixmap = pixmap.copy(0, 0, pixmap.width(), _DRAG_PIXMAP_MAX_HEIGHT)
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.exec(Qt.DropAction.MoveAction)

    def accepts_drop_from(self, source) -> bool:
        """Whether ``source`` is a sibling row this one could accept a drop from."""
        return self._editable and isinstance(source, _AssignmentRow) and source is not self

    def _accepts(self, event) -> bool:  # noqa: ANN001
        return event.mimeData().hasFormat(_ROW_MIME) and self.accepts_drop_from(event.source())

    def drop_edge(self) -> str | None:
        """Which edge the drop indicator is currently drawn on, if any."""
        return self._drop_edge

    def _set_drop_edge(self, edge: str | None) -> None:
        if edge != self._drop_edge:
            self._drop_edge = edge
            self.update()

    def dragEnterEvent(self, event) -> None:  # noqa: ANN001
        if self._accepts(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: ANN001
        if not self._accepts(event):
            event.ignore()
            return
        # Rows are variable height -- an auto-growing DSL box can make one many
        # times the height of its neighbour -- so which edge is meant has to
        # come from the cursor against this row's own midpoint, not a fixed band.
        self._set_drop_edge("above" if event.position().y() < self.height() / 2 else "below")
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: ANN001, ARG002
        self._set_drop_edge(None)

    def dropEvent(self, event) -> None:  # noqa: ANN001
        edge = self._drop_edge
        self._set_drop_edge(None)
        if not self._accepts(event):
            event.ignore()
            return
        self.reorder_requested.emit(event.source(), self, edge != "below")
        event.acceptProposedAction()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        super().paintEvent(event)
        if self._drop_edge is None:
            return
        # Read at paint time, so a theme switch needs no changeEvent here.
        painter = QPainter(self)
        painter.setPen(QPen(QColor(THEME.accent), _DROP_INDICATOR_WIDTH))
        y = 1 if self._drop_edge == "above" else self.height() - 1
        painter.drawLine(0, y, self.width(), y)
        painter.end()

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

        # On DuelConfig itself, not the named wrapper — an inline one-off
        # embedded on a BotCombatStrategy gets a description too.
        self._description_edit = QLineEdit()
        self._description_edit.setPlaceholderText("Description (optional) — what this fight needs and why")
        self._description_edit.editingFinished.connect(self._on_description_changed)
        outer.addWidget(self._description_edit)

        # Min/max players
        mm_row = QHBoxLayout()
        mm_row.setSpacing(8)
        mm_row.addWidget(QLabel("Min players:"))
        self._min_spin = QSpinBox()
        self._min_spin.setRange(1, 4)
        self._min_spin.valueChanged.connect(self._on_min_players_changed)
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

        # Derived from the assignments below — not a second thing to maintain.
        self._requirements_lbl = QLabel()
        self._requirements_lbl.hide()
        apply_muted_color(self._requirements_lbl)
        outer.addWidget(self._requirements_lbl)

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
        self._description_edit.setText(config.description)
        self._min_spin.setValue(config.min_players)
        self._max_spin.setValue(config.max_players)
        self._order_widget.set_value(config.combat_order or [])
        self._rebuild_assignment_rows()
        self._suppress = False
        self._apply_max_players_constraints(config.max_players)
        self._update_add_btn()
        self._update_requirements_label()

    def set_pcs_list(self, all_pcs: list[PlayerCombatStrategy]) -> None:
        self._all_pcs = all_pcs
        for row in self._rows:
            row.set_pcs_list(all_pcs)
        self._update_requirements_label()

    def set_editable(self, editable: bool) -> None:
        """Lock or unlock every control, including rows added later.

        Needed because this editor is embedded in the Combat tab, which locks
        itself while a bot runs.  Without it, an expanded inline config stayed
        fully writable mid-run.
        """
        self._editable = editable
        self._description_edit.setEnabled(editable)
        self._min_spin.setEnabled(editable)
        self._max_spin.setEnabled(editable)
        self._order_widget.set_editable(editable)
        for row in self._rows:
            row.set_editable(editable)
        self._update_add_btn()

    def _update_requirements_label(self) -> None:
        """Refresh the derived school-requirement chips from the current assignments."""
        if self._config is None:
            self._requirements_lbl.hide()
            return
        pcs_by_id = {p.id: p for p in self._all_pcs}
        reqs = self._config.school_requirements(pcs_by_id)
        if not reqs:
            self._requirements_lbl.hide()
            return
        self._requirements_lbl.setText(" · ".join(f"{school} ×{n}" for school, n in reqs.items()))
        self._requirements_lbl.show()

    def _on_description_changed(self) -> None:
        if self._suppress or self._config is None:
            return
        self._config.description = self._description_edit.text()
        self.changed.emit()

    def clear(self) -> None:
        """Detach from the loaded config — no pending state to drop, unlike the
        debounced editors, since every edit here is already live on the config.
        """
        self._config = None
        self._suppress = True
        self._description_edit.clear()
        self._rebuild_assignment_rows()
        self._suppress = False
        self._update_requirements_label()

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
        row.reorder_requested.connect(self._on_reorder_row)
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
        self._update_requirements_label()
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
        self._update_requirements_label()
        self.changed.emit()

    def _on_reorder_row(self, source_row: _AssignmentRow, target_row: _AssignmentRow, before: bool) -> None:
        """Handle a row dropped on another: move it to the indicated edge."""
        if source_row not in self._rows or target_row not in self._rows:
            return
        dst = self._rows.index(target_row)
        self.move_assignment(self._rows.index(source_row), dst if before else dst + 1)

    def move_assignment(self, src: int, dst: int) -> None:
        """Move the assignment at index ``src`` to sit at index ``dst``.

        ``dst`` is a position in the list as it stands *before* the move, which
        is what a drop indicator between two rows means -- see :func:`move_item`.

        Ordering is presentational.  ``_enforce_exclusivity`` already guarantees
        a player appears in exactly one row, so ``resolve_dsl_for_player``'s
        first-match loop cannot come back with a different answer after a
        reorder; nothing about how the bot fights changes here.

        Persistence rides on ``changed`` like every other edit in this widget:
        ``DuelStrategyEditor`` debounces it into a library save, and an inline
        config's embedder runs its own timer off the same signal.
        """
        if self._config is None:
            return
        reordered = move_item(self._rows, src, dst)
        if reordered == self._rows:
            return
        self._rows = reordered
        self._config.player_assignments = [row.assignment() for row in self._rows]
        for index, row in enumerate(self._rows):
            # insertWidget moves a widget the layout already owns, and the
            # trailing stretch stays last as long as nothing is inserted past
            # the rows themselves.
            self._assignments_layout.insertWidget(index, row)
        self.changed.emit()

    def _on_min_players_changed(self, value: int) -> None:
        """Drag Max up when Min passes it, so the range can never invert.

        Pushing the other spin box rather than narrowing its range: a range narrowed
        here would still be narrow at the next load(), which silently clamps the
        stored value of whichever config is opened next.
        """
        if self._suppress:
            return
        if value > self._max_spin.value():
            self._max_spin.setValue(value)

    def _on_max_players_changed(self, value: int) -> None:
        if self._suppress:
            return
        if value < self._min_spin.value():
            self._min_spin.setValue(value)  # the mirror of _on_min_players_changed
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
        self._update_requirements_label()
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
