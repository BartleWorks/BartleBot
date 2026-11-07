"""The properties panel's edge page: an edge's ends, its When, its Conditions and what it sets.

``param_panel._ParamPanel`` shows it in its stack.
"""

from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin
from bartleby.qt.widgets.buttons.action_button import ActionButton
from bartleby.qt.widgets.buttons.icon_button import IconButton, IconTint
from bartleby.qt.widgets.layout.discard import discard_widget
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import (
    AssignmentModel,
    FlagCondition,
    FlagGroup,
    TransitionModel,
    VariableModel,
)
from bartlebot.gui.canvas.variable_rows import AssignmentsField, ConditionRow
from bartlebot.nodes.registry import NODE_SPECS

# ---------------------------------------------------------------------------
# _FlagConditionsWidget  — dynamic AND/OR/NOT flag condition list for edges
# ---------------------------------------------------------------------------


class _FlagConditionsWidget(ThemedMixin, QWidget):
    """The edge panel's Conditions: rows of options and variables, in AND/OR groups.

    Two levels of boolean logic:
    - Each group combines its rows internally with AND or OR.
    - Multiple groups are combined with the outer AND/OR selector.

    Single-group display (one group, outer selector hidden) matches the old
    flat-list layout for simple cases and backward compatibility.

    Each row is a :class:`~bartlebot.gui.canvas.variable_rows.ConditionRow`, which picks
    an option or a variable from one grouped list and offers only what the variable's
    type takes (#187).
    """

    changed = pyqtSignal()

    _REM_W = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self._option_names: list[str] = []
        self._decls: list[VariableModel] = []
        self._updating: bool = False
        # List of (group_frame, inner_op_combo, rows_layout, rows)
        self._groups: list[tuple[QFrame, QComboBox, QVBoxLayout, list[ConditionRow]]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # Outer operator row (hidden when only one group)
        self._outer_op_row = QWidget()
        _oor = QHBoxLayout(self._outer_op_row)
        _oor.setContentsMargins(0, 0, 0, 0)
        _oor.setSpacing(4)
        _oor_lbl = QLabel("Groups combine with:")
        _oor_lbl.setObjectName("FlagMutedBold")
        self._groups_op_combo = QComboBox()
        self._groups_op_combo.addItems(["OR", "AND"])
        self._groups_op_combo.setFixedWidth(64)
        _oor.addWidget(_oor_lbl)
        _oor.addWidget(self._groups_op_combo)
        _oor.addStretch()
        outer.addWidget(self._outer_op_row)
        self._outer_op_row.setVisible(False)

        # Groups container
        self._groups_widget = QWidget()
        self._groups_layout = QVBoxLayout(self._groups_widget)
        self._groups_layout.setContentsMargins(0, 0, 0, 0)
        self._groups_layout.setSpacing(6)
        outer.addWidget(self._groups_widget)

        # Add group button
        self._add_group_btn = ActionButton("plus", "Add condition group")
        self._add_group_btn.clicked.connect(self._on_add_group)
        outer.addWidget(self._add_group_btn)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("FlagSep")
        outer.addWidget(sep)

        self._groups_op_combo.currentTextChanged.connect(self._on_any_changed)
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        """One sheet on self, matching descendants by objectName.

        Groups and rows are added and removed as the user edits, so a list of
        references would need clearing and refilling in step with them. ID selectors
        need none of that: a row built *after* a theme switch is styled by the same
        sheet, with no bookkeeping to forget.
        """
        self.setStyleSheet(
            f"#FlagMutedBold {{ color: {THEME.text_muted}; font-size: {THEME.font_small_px}px;"
            f"  font-weight: 600; border: none; }}"
            f"#FlagSep {{ color: {THEME.border}; }}"
            f"#FlagGroupFrame {{ border: 1px solid {THEME.border}; border-radius: 4px; }}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_flag_groups(
        self,
        flag_groups: list[FlagGroup],
        flag_groups_op: str,
        option_names: list[str],
        variable_decls: list[VariableModel] | None = None,
    ) -> None:
        """Load from the new grouped FlagGroup format."""
        self._updating = True
        self._option_names = list(option_names)
        self._decls = list(variable_decls or [])
        self._clear_all_groups()
        idx = self._groups_op_combo.findText(flag_groups_op)
        self._groups_op_combo.setCurrentIndex(idx if idx >= 0 else 0)
        for g in flag_groups:
            self._add_group(flags=g.flags, op=g.op)
        if not self._groups:
            self._add_group()
        self._refresh_outer_op_visibility()
        self._updating = False

    def set_conditions_flat(
        self,
        flags: list[FlagCondition],
        flag_op: str,
        option_names: list[str],
        variable_decls: list[VariableModel] | None = None,
    ) -> None:
        """Load from the legacy flat flags+flag_op format (single group)."""
        self._updating = True
        self._option_names = list(option_names)
        self._decls = list(variable_decls or [])
        self._clear_all_groups()
        self._groups_op_combo.setCurrentIndex(0)  # OR default
        self._add_group(flags=flags, op=flag_op)
        self._refresh_outer_op_visibility()
        self._updating = False

    def get_flag_groups(self) -> tuple[list[FlagGroup], str]:
        """Return the current state as (flag_groups, flag_groups_op)."""
        result: list[FlagGroup] = []
        for _frame, inner_op, _rows_layout, rows in self._groups:
            flags = [fc for row in rows if (fc := row.condition()) is not None]
            if flags:
                result.append(FlagGroup(flags=flags, op=inner_op.currentText()))
        return result, self._groups_op_combo.currentText()

    @property
    def rows(self) -> list[ConditionRow]:
        """Every condition row, group by group."""
        return [row for *_rest, rows in self._groups for row in rows]

    def add_condition(self, group: int = 0) -> ConditionRow:
        """Add a row to group *group*, as its Add condition button does."""
        return self._on_add_condition(self._groups[group])

    # ------------------------------------------------------------------
    # Group management
    # ------------------------------------------------------------------

    def _clear_all_groups(self) -> None:
        for frame, _, _, _ in self._groups:
            discard_widget(frame, self._groups_layout)
        self._groups.clear()

    def _add_group(self, flags: list[FlagCondition] | None = None, op: str = "AND") -> None:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setObjectName("FlagGroupFrame")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(6, 4, 6, 4)
        frame_layout.setSpacing(3)

        # Group header: inner-op selector + remove button
        header = QWidget()
        # By name, never "QWidget": that selector reaches every descendant, and took the
        # borders off the combo inside, and the spin box's arrows with them (#154).
        header.setObjectName("FlagGroupHeader")
        header.setStyleSheet("#FlagGroupHeader { border: none; }")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(4)

        op_lbl = QLabel("Combine with:")
        op_lbl.setObjectName("FlagMutedBold")
        inner_op = QComboBox()
        inner_op.addItems(["AND", "OR"])
        inner_op.setFixedWidth(64)
        inner_op_idx = inner_op.findText(op)
        inner_op.setCurrentIndex(inner_op_idx if inner_op_idx >= 0 else 0)

        rem_group_btn = IconButton(
            "dismiss", tooltip="Remove this group", size=self._REM_W, icon_size=12, tint=IconTint.DANGER
        )

        h_layout.addWidget(op_lbl)
        h_layout.addWidget(inner_op)
        h_layout.addStretch()
        h_layout.addWidget(rem_group_btn)
        frame_layout.addWidget(header)

        # Rows container for this group
        rows_widget = QWidget()
        rows_widget.setObjectName("FlagGroupRows")
        rows_widget.setStyleSheet("#FlagGroupRows { border: none; }")
        rows_layout = QVBoxLayout(rows_widget)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(2)
        frame_layout.addWidget(rows_widget)

        # Add condition button
        add_cond_btn = ActionButton("plus", "Add condition")
        frame_layout.addWidget(add_cond_btn)

        rows: list[ConditionRow] = []
        group_entry = (frame, inner_op, rows_layout, rows)
        self._groups.append(group_entry)
        self._groups_layout.addWidget(frame)

        for fc in flags or []:
            self._add_condition_row(group_entry, fc)

        # Wire signals
        inner_op.currentTextChanged.connect(self._on_any_changed)
        add_cond_btn.clicked.connect(lambda _, ge=group_entry: self._on_add_condition(ge))
        rem_group_btn.clicked.connect(lambda _, f=frame, ge=group_entry: self._on_remove_group(f, ge))

    def _add_condition_row(self, group_entry: tuple, condition: FlagCondition | None = None) -> ConditionRow:
        _frame, _inner_op, rows_layout, rows = group_entry
        row = ConditionRow(self._option_names, self._decls, condition)
        rows_layout.addWidget(row)
        rows.append(row)
        row.changed.connect(self._on_any_changed)
        row.remove_requested.connect(lambda r, ge=group_entry: self._on_remove_condition(r, ge))
        return row

    def _refresh_outer_op_visibility(self) -> None:
        self._outer_op_row.setVisible(len(self._groups) > 1)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_add_group(self) -> None:
        self._add_group()
        self._refresh_outer_op_visibility()
        if not self._updating:
            self.changed.emit()

    def _on_remove_group(self, frame: QFrame, group_entry: tuple) -> None:
        if len(self._groups) <= 1:
            return  # always keep at least one group
        if group_entry in self._groups:
            self._groups.remove(group_entry)
        discard_widget(frame, self._groups_layout)
        self._refresh_outer_op_visibility()
        if not self._updating:
            self.changed.emit()

    def _on_add_condition(self, group_entry: tuple) -> ConditionRow:
        # Picks the first thing listed: an option when the bot has one, else its first variable.
        row = self._add_condition_row(group_entry)
        if not self._updating:
            self.changed.emit()
        return row

    def _on_remove_condition(self, row: ConditionRow, group_entry: tuple) -> None:
        _frame, _inner_op, rows_layout, rows = group_entry
        if row in rows:
            rows.remove(row)
        discard_widget(row, rows_layout)
        if not self._updating:
            self.changed.emit()

    def _on_any_changed(self, *_) -> None:
        if not self._updating:
            self.changed.emit()


class EdgePage(ThemedMixin, QWidget):
    """One edge's page. Edits the shown ``TransitionModel`` in place.

    The signals are the panel's own, under the same names, which forwards each one: see
    ``_ParamPanel`` for what each means.
    """

    edit_starting = pyqtSignal(str)
    properties_changed = pyqtSignal()
    structural_property_changed = pyqtSignal(str)
    structural_property_applied = pyqtSignal()
    edge_endpoints_changed = pyqtSignal(str, object, str, str, str)
    edge_when_changed = pyqtSignal(str, object, str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._transition: TransitionModel | None = None
        self._suppress: bool = False
        # All states for the source/dest dropdowns; populated by show_transition()
        self._all_states: list = []
        # Other transitions in the config — for duplicate-check in endpoint change
        self._all_transitions: list[TransitionModel] = []

        edge_vbox = QVBoxLayout(self)
        edge_vbox.setContentsMargins(0, 0, 0, 0)
        edge_vbox.setSpacing(0)

        edge_form_w = QWidget()
        ef = QFormLayout(edge_form_w)
        ef.setContentsMargins(6, 6, 6, 6)

        self._edge_src_combo = QComboBox()
        self._edge_src_combo.setToolTip("Source state — the state that runs before this transition.")
        self._edge_dst_combo = QComboBox()
        self._edge_dst_combo.setToolTip("Destination state — the state to enter when this transition fires.")
        self._edge_when_combo = QComboBox()

        ef.addRow("Source", self._edge_src_combo)
        ef.addRow("Dest", self._edge_dst_combo)
        ef.addRow("When", self._edge_when_combo)

        self._flags_lbl = QLabel("Conditions")
        self._flags_lbl.setToolTip(
            "What must hold for this edge to be taken: a bot option the user sets before the run,\n"
            "or a variable the bot sets during it, compared with a value.\n"
            "Combine conditions with AND (all must be true) or OR (any must be true).\n"
            "Check NOT to invert an option; compare a variable with != instead."
        )

        self._flag_conditions_widget = _FlagConditionsWidget()

        # What the edge writes when it is the one taken (#189): the rows a set_variables
        # node holds, in the same editor, read against the declarations show_transition
        # was handed.
        self._assign_lbl = QLabel("When taken, set")
        self._assign_lbl.setToolTip(
            "Variables this edge sets when the run takes it, in order:\n"
            "set one to a value, add to a whole number, or toggle a true/false.\n"
            "Nothing is set when another edge is taken, or when the bot recovers."
        )
        self._edge_decls: list[VariableModel] = []
        self._edge_assign_field = AssignmentsField(lambda: self._edge_decls)

        edge_vbox.addWidget(edge_form_w)
        edge_vbox.addWidget(self._flags_lbl)
        edge_vbox.addWidget(self._flag_conditions_widget)
        edge_vbox.addWidget(self._assign_lbl)
        edge_vbox.addWidget(self._edge_assign_field)
        edge_vbox.addStretch()

        self._edge_when_combo.currentTextChanged.connect(self._on_edge_when_changed)
        self._edge_src_combo.currentTextChanged.connect(self._on_edge_src_changed)
        self._edge_dst_combo.currentTextChanged.connect(self._on_edge_dst_changed)
        self._flag_conditions_widget.changed.connect(self._on_flag_conditions_changed)
        self._edge_assign_field.changed.connect(self._on_edge_assign_changed)

        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        for header in (self._flags_lbl, self._assign_lbl):
            header.setStyleSheet(
                f"color: {THEME.text_muted}; font-size: {THEME.font_small_px}px; font-weight: 600; margin-top: 4px;"
            )

    @property
    def transition(self) -> TransitionModel | None:
        """The transition on the page, or None once the panel has moved to another page."""
        return self._transition

    def clear(self) -> None:
        """Let go of the shown transition: the panel is showing another page."""
        self._transition = None

    @property
    def edge_src_combo(self) -> QComboBox:
        """The Source dropdown on the edge page; item data is the state ID."""
        return self._edge_src_combo

    @property
    def edge_dst_combo(self) -> QComboBox:
        """The Dest dropdown on the edge page; item data is the state ID."""
        return self._edge_dst_combo

    @property
    def when_combo(self) -> QComboBox:
        """The When dropdown on the edge page."""
        return self._edge_when_combo

    @property
    def when_choices(self) -> list[str]:
        """The return values currently offered by the When dropdown, in order."""
        return [self._edge_when_combo.itemText(i) for i in range(self._edge_when_combo.count())]

    def _reload_when_combo(self, src_returns: list[str] | None = None) -> None:
        """Repopulate the When dropdown from the source node's return values.

        Called on show and again whenever the source endpoint changes — the
        offered returns belong to the source node, so a rewire that is not
        followed by this leaves the list describing the node the edge came from.
        A stored ``when`` the new source cannot produce is kept as an entry
        rather than silently rewritten; the node's uncovered-returns warning is
        what surfaces the mismatch.

        When *src_returns* is None the returns are derived from the transition's
        current source state.
        """
        if self._transition is None:
            return
        if src_returns is None:
            src_state = next((st for st in self._all_states if st.id == self._transition.source), None)
            spec = NODE_SPECS.get(src_state.node_type) if src_state else None
            src_returns = [r.name for r in spec.returns] if spec else []
        when = self._transition.when
        was_suppressed = self._suppress
        self._suppress = True
        try:
            self._edge_when_combo.clear()
            self._edge_when_combo.addItem("(none)")
            for name in src_returns:
                self._edge_when_combo.addItem(name)
            if when and self._edge_when_combo.findText(when) < 0:
                self._edge_when_combo.addItem(when)
            idx = self._edge_when_combo.findText(when) if when else 0
            self._edge_when_combo.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._suppress = was_suppressed

    def show_transition(
        self,
        transition: TransitionModel,
        src_returns: list[str] | None = None,
        option_names: list[str] | None = None,
        states: list | None = None,
        all_transitions: list[TransitionModel] | None = None,
        *,
        rewire: bool = True,
        variable_decls: list[VariableModel] | None = None,
    ) -> None:
        """Show *transition*. Without *rewire* its ends and ``when`` are read-only: a call node's edge is rewired on the canvas."""
        self._transition = transition
        self._all_states = list(states or [])
        self._all_transitions = list(all_transitions or [])
        self._suppress = True

        # Source / dest dropdowns — display label (or ID as fallback), store ID as item data
        self._edge_src_combo.clear()
        for st in self._all_states:
            self._edge_src_combo.addItem(st.label or st.id, st.id)
        src_idx = self._edge_src_combo.findData(transition.source)
        self._edge_src_combo.setCurrentIndex(src_idx if src_idx >= 0 else 0)

        self._edge_dst_combo.clear()
        for st in self._all_states:
            self._edge_dst_combo.addItem(st.label or st.id, st.id)
        dst_idx = self._edge_dst_combo.findData(transition.dest)
        self._edge_dst_combo.setCurrentIndex(dst_idx if dst_idx >= 0 else 0)

        # When combo from source node's possible return values
        self._reload_when_combo(src_returns)

        # Flag conditions widget — prefer grouped format, fall back to flat legacy format
        if transition.flag_groups:
            self._flag_conditions_widget.set_flag_groups(
                transition.flag_groups,
                transition.flag_groups_op,
                option_names or [],
                variable_decls,
            )
        else:
            self._flag_conditions_widget.set_conditions_flat(
                transition.flags,
                transition.flag_op,
                option_names or [],
                variable_decls,
            )
        self._edge_decls = list(variable_decls or [])
        self._edge_assign_field.set_rows(transition.assign)

        for combo in (self._edge_src_combo, self._edge_dst_combo, self._edge_when_combo):
            combo.setEnabled(rewire)
            combo.setToolTip("" if rewire else "Rewire an edge on a subgraph node by dragging it on the canvas")
        self._suppress = False

    def _on_edge_when_changed(self, text: str) -> None:
        if self._suppress or self._transition is None:
            return
        new_when = None if text == "(none)" else text
        old_when = self._transition.when
        if new_when == old_when:
            return
        src = self._transition.source
        dst = self._transition.dest
        # Duplicate check: another transition with the same (source, dest, new_when)
        # must not exist — the same guard the source/dest dropdowns apply.
        if any(
            t is not self._transition and t.source == src and t.dest == dst and t.when == new_when
            for t in self._all_transitions
        ):
            QMessageBox.warning(
                self,
                "Duplicate Transition",
                f"A transition from '{src}' to '{dst}' with that When condition already exists.",
            )
            self._suppress = True
            idx = self._edge_when_combo.findText(old_when) if old_when else 0
            self._edge_when_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self._suppress = False
            return
        self.structural_property_changed.emit("Change Edge Condition")
        self._transition.when = new_when
        self.edge_when_changed.emit(src, old_when, dst, new_when)
        self.structural_property_applied.emit()

    def _on_edge_src_changed(self, _text: str) -> None:
        if self._suppress or self._transition is None:
            return
        new_src_id = self._edge_src_combo.currentData()
        if not new_src_id:
            return
        old_src = self._transition.source
        if new_src_id == old_src:
            return
        old_when = self._transition.when
        old_dst = self._transition.dest
        # Duplicate check: another transition with the same (new_src, dest, when) must not exist
        if any(
            t is not self._transition and t.source == new_src_id and t.dest == old_dst and t.when == old_when
            for t in self._all_transitions
        ):
            QMessageBox.warning(
                self,
                "Duplicate Transition",
                f"A transition from '{new_src_id}' to '{old_dst}' with the same When condition already exists.",
            )
            self._suppress = True
            self._edge_src_combo.setCurrentIndex(self._edge_src_combo.findData(old_src))
            self._suppress = False
            return
        self.structural_property_changed.emit("Rewire Edge")
        self._transition.source = new_src_id
        self.edge_endpoints_changed.emit(old_src, old_when, old_dst, new_src_id, old_dst)
        self._reload_when_combo()
        self.structural_property_applied.emit()

    def _on_edge_dst_changed(self, _text: str) -> None:
        if self._suppress or self._transition is None:
            return
        new_dst_id = self._edge_dst_combo.currentData()
        if not new_dst_id:
            return
        old_dst = self._transition.dest
        if new_dst_id == old_dst:
            return
        old_src = self._transition.source
        old_when = self._transition.when
        # Duplicate check
        if any(
            t is not self._transition and t.source == old_src and t.dest == new_dst_id and t.when == old_when
            for t in self._all_transitions
        ):
            QMessageBox.warning(
                self,
                "Duplicate Transition",
                f"A transition from '{old_src}' to '{new_dst_id}' with the same When condition already exists.",
            )
            self._suppress = True
            self._edge_dst_combo.setCurrentIndex(self._edge_dst_combo.findData(old_dst))
            self._suppress = False
            return
        self.structural_property_changed.emit("Rewire Edge")
        self._transition.dest = new_dst_id
        self.edge_endpoints_changed.emit(old_src, old_when, old_dst, old_src, new_dst_id)
        self.structural_property_applied.emit()

    def _on_flag_conditions_changed(self) -> None:
        if self._suppress or self._transition is None:
            return
        flag_groups, flag_groups_op = self._flag_conditions_widget.get_flag_groups()
        self.edit_starting.emit("Edit transition")
        self._transition.flag_groups = flag_groups
        self._transition.flag_groups_op = flag_groups_op
        # Clear legacy flat fields — the grouped format takes precedence in to_machine_config()
        self._transition.flags = []
        self.properties_changed.emit()

    def _on_edge_assign_changed(self) -> None:
        if self._suppress or self._transition is None:
            return
        self.edit_starting.emit("Edit transition")
        self._transition.assign = [AssignmentModel.model_validate(row) for row in self._edge_assign_field.value()]
        self.properties_changed.emit()
