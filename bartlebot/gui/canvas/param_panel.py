"""The properties panel: a stack of pages, one per kind of selection.

_ParamPanel — the node, edge, region, call node and signature pages, and the idle one;
owns the Record button and delegates recording to ``recording.py``. A node's param rows
are ``param_form.DynamicParamForm``.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from bartleby.qt.sidebar.option_gutter import (
    HelpToggle,
    OverrideBar,
    RevertButton,
    build_gutter_row,
    keep_slot_when_hidden,
)
from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin, set_style_sheet
from bartleby.qt.widgets.buttons.action_button import ActionButton, ActionStyle
from bartleby.qt.widgets.buttons.icon_button import IconButton, IconTint
from bartleby.qt.widgets.labels.elided_label import ElidedLabel
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from bartleby.qt.widgets.layout.discard import discard_widget
from bartleby.qt.widgets.layout.scroll_gutter import reserve_scrollbar_gutter
from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QPen,
    QStandardItem,
    QStandardItemModel,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import (
    AssignmentModel,
    FlagCondition,
    FlagGroup,
    RegionModel,
    StateModel,
    TransitionModel,
    VariableModel,
)
from bartlebot.game.wiki_pages import WIKI_PAGE_PARAM
from bartlebot.gui.canvas import recording
from bartlebot.gui.canvas.call_page import CallNodePage
from bartlebot.gui.canvas.param_form import DynamicParamForm, write_show_descriptions
from bartlebot.gui.canvas.signature_page import SignaturePage
from bartlebot.gui.canvas.variable_rows import AssignmentsField, ConditionRow
from bartlebot.gui.widgets.buttons.record_button import IDLE_TEXT, RecordButton
from bartlebot.gui.widgets.pickers.player_list_widget import PlayerListWidget, PlayerSlotWidget
from bartlebot.gui.widgets.pickers.player_picker import pick_player
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION
from bartlebot.nodes.registry import (
    NODE_CATEGORIES,
    NODE_PARAM_SCHEMAS,
    NODE_REGISTRY,
    NODE_SPECS,
    node_display_name,
)
from bartlebot.options import param_cards

logger = logging.getLogger(__name__)


class _NodeHelpButton(IconButton):
    """The node type's ``?``: the node browser's page for it.

    Framed, in the accent: the same affordance as a row's ``?`` one panel over
    (``HelpToggle``), only larger. It styles itself rather than being styled by the
    panel, because an ``IconButton`` answers every ``StyleChange`` with its own flat
    sheet, and whichever is applied last wins.
    """

    def __init__(self, parent=None) -> None:
        super().__init__("help", tooltip="View node documentation", size=22, icon_size=14, parent=parent)

    def apply_theme_styles(self) -> None:
        super().apply_theme_styles()
        set_style_sheet(
            self,
            f"QPushButton {{ background: {THEME.surface}; border: 1px solid {THEME.border}; "
            f"border-radius: 4px; color: {THEME.accent}; font-weight: bold; font-size: {THEME.font_small_px}px; }}"
            f"QPushButton:hover {{ background: {THEME.accent}; color: #ffffff; }}",
        )


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


# ---------------------------------------------------------------------------
# _ParamPanel
# ---------------------------------------------------------------------------

_PAGE_IDLE = 0
_PAGE_NODE = 1
_PAGE_EDGE = 2
_PAGE_REGION = 3
_PAGE_CALL = 4
_PAGE_SIGNATURE = 5


class _RegionColorRow(ThemedMixin, QWidget):
    """The six region swatches inline, the selected one ringed in the accent."""

    changed = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._key: int = THEME.REGION_PALETTE[0]
        self._btns: dict[int, QPushButton] = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for key in THEME.REGION_PALETTE:
            btn = QPushButton()
            btn.setFixedSize(22, 22)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"Region color {key}")
            btn.clicked.connect(lambda _=None, k=key: self._on_clicked(k))
            self._btns[key] = btn
            row.addWidget(btn)
        row.addStretch()
        self.refresh_theme_styles()

    @property
    def key(self) -> int:
        return self._key

    def set_key(self, key: int) -> None:
        self._key = key
        self.refresh_theme_styles()

    def _on_clicked(self, key: int) -> None:
        if key == self._key:
            return
        self.set_key(key)
        self.changed.emit(key)

    def apply_theme_styles(self) -> None:
        for key, btn in self._btns.items():
            color_hex = THEME.region_color(key).name()
            border = THEME.accent if key == self._key else "transparent"
            btn.setStyleSheet(
                f"QPushButton {{ background: {color_hex}; border: 2px solid {border}; border-radius: 11px; }}"
                f"QPushButton:hover {{ border-color: {THEME.text_muted}; }}"
            )


class _NodeTypeDelegate(QStyledItemDelegate):
    """Custom delegate that renders separator items as a consistent 1px line in THEME.border."""

    def paint(self, painter, option, index):
        if index.data(Qt.ItemDataRole.AccessibleDescriptionRole) == "separator":
            painter.save()
            mid = option.rect.center().y()
            painter.setPen(QPen(QColor(THEME.border), 1))
            painter.drawLine(option.rect.left() + 4, mid, option.rect.right() - 4, mid)
            painter.restore()
            return
        super().paint(painter, option, index)

    def sizeHint(self, option, index):
        if index.data(Qt.ItemDataRole.AccessibleDescriptionRole) == "separator":
            return QSize(0, 9)
        return super().sizeHint(option, index)


class _ParamPanel(ThemedMixin, QWidget):
    # Emitted BEFORE a typed edit mutates the model -- a param, label, notes, flag
    # conditions, or a region's label or inputs -- carrying the undo description.
    # The canvas opens its debounced undo step here, which captures a before-snapshot
    # only on the first edit of a burst; opened from properties_changed instead, that
    # snapshot already held the edit and undo restored it.
    edit_starting = pyqtSignal(str)
    properties_changed = pyqtSignal()
    label_committed = pyqtSignal()
    # Emitted for discrete structural property changes (node type switch, edge
    # rewire) that need an immediate undo snapshot rather than the debounced
    # param-edit path.  Emit it BEFORE mutating the model — UndoManager.push()
    # captures a before-snapshot.
    structural_property_changed = pyqtSignal(str)
    # Emitted after such a change has been applied to the model.  Unlike
    # properties_changed this does not start a debounced undo snapshot (the
    # immediate one above already covers the edit) and it asks the canvas for a
    # full structural refresh, which is what redraws node type sub-labels,
    # output ports and rewired edges.
    structural_property_applied = pyqtSignal()
    # Emitted when the user changes source or dest via the dropdowns.
    # Args: old_source, old_when (str|None), old_dest, new_source, new_dest
    edge_endpoints_changed = pyqtSignal(str, object, str, str, str)
    # Emitted when the user changes the When condition via the dropdown.
    # Args: source, old_when (str|None), dest, new_when (str|None)
    edge_when_changed = pyqtSignal(str, object, str, object)
    # Emitted when the user completes a wiki import from the handle_combat node panel.
    # Carries the list of selected DropModel entries to be added to the bot's drops.
    wiki_import_completed = pyqtSignal(list)
    # Emitted when the user clicks a connection row to navigate to an edge or state.
    select_edge_requested = pyqtSignal(object)  # TransitionModel
    select_state_requested = pyqtSignal(str)  # state ID
    # Emitted when the user gives the shown state one of the two bot-wide FSM roles.
    # The panel holds no BotConfig, so it asks rather than assigns -- the canvas owns
    # the mutation, which is also what gets it an undo entry and a badge refresh.
    # Both roles are exactly-one, so there is no "clear" counterpart: the request
    # always names the state taking the role.
    initial_state_requested = pyqtSignal(str)  # state ID
    recovery_state_requested = pyqtSignal(str)  # state ID
    # The region or call node page wrote into the shown RegionModel (label, color,
    # inputs). Its undo step is already open: a typed edit announced itself with
    # edit_starting, a discrete one pushed through structural_property_changed.
    region_property_changed = pyqtSignal()
    # (region id, collapse): the region page's Collapse / Expand button (#67).
    region_collapse_requested = pyqtSignal(str, bool)
    detach_requested = pyqtSignal(str)  # region ID
    open_definition_requested = pyqtSignal(str)  # region ID
    # (role field, state id): the call node's page moved a bot role onto the member
    # the chosen entry port leads to.
    role_requested = pyqtSignal(str, str)
    # (state id, param key): Link to subgraph input, and Unlink, on a definition node's page.
    link_input_requested = pyqtSignal(str, str)
    unlink_input_requested = pyqtSignal(str, str)
    # (region id, input name): the same two on a nested subgraph node's page, in a definition.
    link_nested_input_requested = pyqtSignal(str, str)
    unlink_nested_input_requested = pyqtSignal(str, str)
    # (state id, promoted): the node page's Promote to input group / Demote button.
    promote_node_requested = pyqtSignal(str, bool)
    # The signature page wrote into the definition document's signature.
    definition_changed = pyqtSignal()
    # (state id, param key), on a node of a template draft: make the param a setup step's
    # fill-in, or stop it being one. The panel only reports; the canvas edits the draft.
    add_setup_target_requested = pyqtSignal(str, str)
    remove_setup_target_requested = pyqtSignal(str, str)
    add_port_requested = pyqtSignal(str)  # SUBGRAPH_ENTRY / SUBGRAPH_EXIT
    delete_port_requested = pyqtSignal(str)  # state ID
    # The signature page reaching into the canvas without selecting anything (#155): the
    # nodes its selected item sets, to light (empty when it no longer shows), and one node
    # to bring into view. State ids, or a nested subgraph node's region id.
    link_highlight_requested = pyqtSignal(list)
    center_node_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: StateModel | None = None
        self._transition: TransitionModel | None = None
        self._region: RegionModel | None = None
        self._recovery_id: str | None = None
        self._suppress: bool = False
        self._get_clients: Callable[[], list] = lambda: []
        # The player Ctrl+R records from; AppController points it at the last-focused game window.
        self._preferred_player: Callable[[], int] = lambda: 0
        # The open bot's Safety Delay, for Record; BotCanvas resolves it as a run would.
        self._safety_delay: Callable[[], float] = lambda: SAFETY_DELAY_OPTION.default
        # All states for the source/dest dropdowns; populated by show_transition()
        self._all_states: list = []
        # Other transitions in the config — for duplicate-check in endpoint change
        self._all_transitions: list[TransitionModel] = []
        # All states/transitions for connections panel; populated by show_state()
        self._all_states_for_node: list = []
        self._all_transitions_for_node: list[TransitionModel] = []

        self._stack = QStackedWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._stack)

        # Page 0 — idle
        self._idle_lbl = QLabel("Select a node or edge to edit its properties.")
        self._idle_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack.addWidget(self._idle_lbl)

        # Page 1 — node. Since #142 a page of cards like the subgraph node's: the label
        # as the title, one gray line for the node type and id, then Parameters,
        # Connections, Notes and Role, the whole page scrolling as one.
        from bartlebot.gui.canvas.node_cards import PageFoldBar, TitleEdit, ToFillLabel, record_line  # noqa: PLC0415
        from bartlebot.gui.sidebar.option_card import CardFolds, OptionCard, UiStateFoldStore  # noqa: PLC0415

        self._node_id_lbl = ThemedTextLabel("", color="text_muted")
        self._node_id_lbl.setToolTip("The node's id: what edges and the bot file call it")
        self._node_label_edit = TitleEdit()
        self._node_type_combo = QComboBox()
        self._node_type_combo.setToolTip("The node type. Changing it clears the params.")
        # Not as wide as the longest type: it shares a line with the id and the ?.
        self._node_type_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._node_type_combo.setMinimumContentsLength(12)
        # The category headers carry a THEME color on the model item rather than a
        # stylesheet, so apply_theme_styles re-sets their foreground by hand.
        self._node_type_headers: list[QStandardItem] = []
        _model = QStandardItemModel()
        for _i, (_category, _node_types) in enumerate(NODE_CATEGORIES):
            if _i > 0:
                _sep = QStandardItem()
                _sep.setData("separator", Qt.ItemDataRole.AccessibleDescriptionRole)
                _sep.setEnabled(False)
                _sep.setSelectable(False)
                _model.appendRow(_sep)
            _header = QStandardItem(_category)
            _header.setEnabled(False)
            _header.setSelectable(False)
            _font = _header.font()
            _font.setBold(True)
            _font.setPixelSize(THEME.font_small_px)
            _header.setFont(_font)
            _model.appendRow(_header)
            self._node_type_headers.append(_header)
            for _node_type in _node_types:
                _item = QStandardItem("    " + _node_type)
                _item.setData(_node_type, Qt.ItemDataRole.UserRole)
                _model.appendRow(_item)
        self._node_type_combo.setModel(_model)
        self._node_type_combo.setItemDelegate(_NodeTypeDelegate(self._node_type_combo))
        self._node_help_btn = _NodeHelpButton()
        self._node_help_btn.clicked.connect(self._on_node_help)

        node_page = QWidget()
        node_col = QVBoxLayout(node_page)
        node_col.setContentsMargins(6, 6, 6, 6)
        node_col.setSpacing(4)
        node_col.addWidget(self._node_label_edit)
        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(6)
        node_col.addLayout(meta)
        meta.addWidget(self._node_type_combo, 1)
        meta.addWidget(self._node_id_lbl)
        meta.addWidget(self._node_help_btn)
        node_col.addSpacing(4)
        self._node_fold_bar = PageFoldBar(self._set_node_descriptions_visible)
        node_col.addWidget(self._node_fold_bar)

        # Parameters: the form, with Record (and, in a definition, Promote) on the band.
        self._params_card = OptionCard(param_cards.PARAMS, "Parameters")
        self._params_count = ToFillLabel()
        self._params_card.add_header_widget(self._params_count)
        # Definition documents only: put this node whole on the subgraph node as an
        # input group, or take it off. A toggle on the band, with "Promoted" in the
        # summary; its tooltip names what a click does next.
        self._promote_btn = IconButton("chevrons_up", tooltip="Promote to input group", size=24, icon_size=16)
        self._params_card.add_header_widget(self._promote_btn, gap_before=2)
        self._node_promoted = False
        self._promote_btn.clicked.connect(self._on_promote_clicked)
        # Recording is how most params here get filled, so the button is drawn to be
        # found (#106): SECONDARY's accent and the red dot, on the band so it works with
        # the card folded (#142).
        self._record_btn = RecordButton(
            style=ActionStyle.SECONDARY, fill=False, get_clients=lambda: self._get_clients()
        )
        self._record_btn.setToolTip(
            "Fill this node's values from the game (Ctrl+R records from the game window you were last in)"
        )
        self._record_btn.clicked.connect(self._on_record_clicked)
        self._params_card.add_header_widget(self._record_btn, gap_before=2)
        self._record_status_lbl = record_line()
        self._params_card.body_layout.addWidget(self._record_status_lbl)
        self._param_form = DynamicParamForm()
        self._params_card.body_layout.addWidget(self._param_form)
        self._recording_player: int | None = None

        # Connections: every route off the node by return value, including the ones the
        # canvas never draws, then what comes in.
        self._conn_card = OptionCard(param_cards.CONNECTIONS, "Connections")
        self._conn_body = QWidget()
        self._conn_body_layout = QVBoxLayout(self._conn_body)
        self._conn_body_layout.setContentsMargins(0, 0, 0, 0)
        self._conn_body_layout.setSpacing(2)
        self._conn_card.body_layout.addWidget(self._conn_body)

        # Notes — freeform per-node annotation, folded to its first line.
        self._notes_card = OptionCard(param_cards.NOTES, "Notes")
        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setPlaceholderText("Add a note…")
        self._notes_edit.setFixedHeight(72)
        self._notes_card.body_layout.addWidget(self._notes_edit)

        # FSM role checkboxes. Both roles are exactly-one and bot-wide, so these read
        # as checkboxes but behave as radios across nodes: checking one moves the role
        # here, and the box for the state that already holds it is disabled, because
        # unchecking would leave the bot with no answer and BotConfig requires one.
        self._role_card = OptionCard(param_cards.ROLE, "Role")
        self._initial_check = QCheckBox("Initial state")
        self._initial_check.setToolTip(
            "Start the bot in this state.\n\nExactly one state is the initial state; "
            "checking this moves the role here from whichever state holds it now."
        )
        self._recovery_check = QCheckBox("Recovery state")
        self._recovery_check.setToolTip(
            "Resume here after a crashed client reconnects.\n\nExactly one state is the "
            "recovery state; checking this moves the role here from whichever state holds it now."
        )
        self._role_card.body_layout.addWidget(self._initial_check)
        self._role_card.body_layout.addWidget(self._recovery_check)

        node_cards_box = QWidget()
        node_cards = QVBoxLayout(node_cards_box)
        node_cards.setContentsMargins(0, 0, 0, 0)
        node_cards.setSpacing(8)
        for card in (self._params_card, self._conn_card, self._notes_card, self._role_card):
            node_cards.addWidget(card)
        node_col.addWidget(node_cards_box)
        node_col.addStretch(1)
        self._node_cards = {
            param_cards.PARAMS: self._params_card,
            param_cards.CONNECTIONS: self._conn_card,
            param_cards.NOTES: self._notes_card,
            param_cards.ROLE: self._role_card,
        }
        self._node_folds = CardFolds(
            self._node_fold_bar.heading_actions,
            store=UiStateFoldStore(param_cards.NODE_PAGE_SURFACE, param_cards.node_page_fold_defaults()),
        )
        self._node_folds.set_cards(self._node_cards)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        # Every inspector page reserves its gutter, so the panel keeps one content width
        # whichever page is up, and a fold does not jolt every card sideways (#112).
        reserve_scrollbar_gutter(scroll)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(node_page)
        # Visibility only once everything is parented: see "Never show a widget before
        # it has a parent" in gui/CLAUDE.md.
        self._promote_btn.setVisible(False)
        self._record_btn.setVisible(False)
        self._record_status_lbl.setVisible(False)
        self._stack.addWidget(scroll)

        # Page 2 — edge
        edge_outer = QWidget()
        edge_vbox = QVBoxLayout(edge_outer)
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
        self._stack.addWidget(edge_outer)

        # Page 3 — region. Label and color for every region; the subgraph section
        # for a linked one.
        region_outer = QWidget()
        region_vbox = QVBoxLayout(region_outer)
        region_vbox.setContentsMargins(0, 0, 0, 0)
        region_vbox.setSpacing(0)
        region_form_w = QWidget()
        rf = QFormLayout(region_form_w)
        rf.setContentsMargins(6, 6, 6, 6)
        self._region_label_edit = QLineEdit()
        self._region_color_row = _RegionColorRow()
        rf.addRow("Label", self._region_label_edit)
        rf.addRow("Color", self._region_color_row)
        region_vbox.addWidget(region_form_w)
        # Collapsing draws the region as one node and closes up the space it used.
        self._region_fold_note = ThemedTextLabel("", color="text_muted")
        self._region_fold_note.setWordWrap(True)
        self._region_fold_note.setContentsMargins(6, 0, 6, 0)
        self._region_collapse_btn = ActionButton("region_collapse", "Collapse", fill=False)
        region_actions = QWidget()
        region_actions_row = QHBoxLayout(region_actions)
        region_actions_row.setContentsMargins(6, 8, 6, 0)
        region_actions_row.addStretch(1)
        region_actions_row.addWidget(self._region_collapse_btn)
        region_vbox.addWidget(self._region_fold_note)
        region_vbox.addWidget(region_actions)
        self._region_collapse_btn.clicked.connect(self._on_region_collapse_clicked)
        self._region_collapsed = False  # as drawn, for the button's next click

        region_vbox.addStretch()
        region_scroll = QScrollArea()
        region_scroll.setWidgetResizable(True)
        region_scroll.setFrameShape(region_scroll.Shape.NoFrame)
        reserve_scrollbar_gutter(region_scroll)
        region_scroll.setWidget(region_outer)
        self._stack.addWidget(region_scroll)

        # Page 4 -- a call node; page 5 -- a definition's signature, nothing selected.

        self._call_page = CallNodePage()
        call_scroll = QScrollArea()
        call_scroll.setWidgetResizable(True)
        call_scroll.setFrameShape(call_scroll.Shape.NoFrame)
        reserve_scrollbar_gutter(call_scroll)
        call_scroll.setWidget(self._call_page)
        self._stack.addWidget(call_scroll)
        self._signature_page = SignaturePage()
        sig_scroll = QScrollArea()
        sig_scroll.setWidgetResizable(True)
        sig_scroll.setFrameShape(sig_scroll.Shape.NoFrame)
        reserve_scrollbar_gutter(sig_scroll)
        sig_scroll.setWidget(self._signature_page)
        self._stack.addWidget(sig_scroll)
        self._call_page.edit_starting.connect(self.edit_starting)
        self._call_page.structural_change.connect(self.structural_property_changed)
        self._call_page.changed.connect(self.region_property_changed)
        self._call_page.role_requested.connect(self.role_requested)
        self._call_page.open_definition_requested.connect(self.open_definition_requested)
        self._call_page.detach_requested.connect(self.detach_requested)
        self._call_page.link_input_requested.connect(self.link_nested_input_requested)
        self._call_page.unlink_input_requested.connect(self.unlink_nested_input_requested)
        self._signature_page.edit_starting.connect(self.edit_starting)
        self._signature_page.structural_change.connect(self.structural_property_changed)
        self._signature_page.changed.connect(self.definition_changed)
        self._signature_page.add_port_requested.connect(self.add_port_requested)
        self._signature_page.delete_port_requested.connect(self.delete_port_requested)
        self._signature_page.highlight_requested.connect(self.link_highlight_requested)
        self._signature_page.center_requested.connect(self.center_node_requested)
        # The signature page is the only one that lights anything, so leaving it for any
        # other page puts the lights out; coming back, it names its selection again.
        self._stack.currentChanged.connect(
            lambda index: index != _PAGE_SIGNATURE and self.link_highlight_requested.emit([])
        )

        self._node_label_edit.textChanged.connect(self._on_node_label_changed)
        self._node_label_edit.editingFinished.connect(self._on_label_editing_finished)
        self._node_type_combo.currentIndexChanged.connect(self._on_node_type_changed)
        self._initial_check.clicked.connect(self._on_initial_clicked)
        self._recovery_check.clicked.connect(self._on_recovery_clicked)
        self._param_form.changed.connect(self._on_param_form_changed)
        self._notes_edit.textChanged.connect(self._on_notes_changed)
        self._edge_when_combo.currentTextChanged.connect(self._on_edge_when_changed)
        self._edge_src_combo.currentTextChanged.connect(self._on_edge_src_changed)
        self._edge_dst_combo.currentTextChanged.connect(self._on_edge_dst_changed)
        self._flag_conditions_widget.changed.connect(self._on_flag_conditions_changed)
        self._edge_assign_field.changed.connect(self._on_edge_assign_changed)
        self._param_form.link_requested.connect(self._on_form_link_requested)
        self._param_form.step_add_requested.connect(self._on_form_step_add_requested)
        self._param_form.step_remove_requested.connect(self._on_form_step_remove_requested)
        self._param_form.unlink_requested.connect(self._on_form_unlink_requested)
        self._param_form.required_shown_changed.connect(self._refresh_params_card)
        self._region_label_edit.textChanged.connect(self._on_region_label_changed)
        self._region_color_row.changed.connect(self._on_region_color_changed)

        # The connection rows are the one part of this panel a theme switch has to
        # rebuild rather than restyle, and rebuilding deletes widgets — so it is
        # deferred off the StyleChange handler. See apply_theme_styles.
        self._conn_theme_timer = QTimer(self)
        self._conn_theme_timer.setSingleShot(True)
        self._conn_theme_timer.timeout.connect(self._rebuild_connections_for_theme)

        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self._idle_lbl.setStyleSheet(f"color: {THEME.text_muted};")
        for header in self._node_type_headers:
            header.setForeground(QColor(THEME.text_muted))
        self._node_help_btn.refresh_theme_styles()
        # The status line owns its color, but a switch delivered to this panel alone
        # reaches no child, so it is asked here as the title and id are.
        self._record_status_lbl.refresh_theme_styles()
        self._node_id_lbl.refresh_theme_styles()
        self._node_label_edit.refresh_theme_styles()
        self._notes_edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {THEME.surface}; border: 1px solid {THEME.border}; "
            f"border-radius: 4px; color: {THEME.text}; font-size: {THEME.font_small_px}px; padding: 3px; }}"
        )
        for header in (self._flags_lbl, self._assign_lbl):
            header.setStyleSheet(
                f"color: {THEME.text_muted}; font-size: {THEME.font_small_px}px; font-weight: 600; margin-top: 4px;"
            )
        # Connection rows are built per selection and hold four THEME colors each
        # (chip background, accent link, hover surface), so replaying the build is
        # both shorter and more complete than holding a reference to every label.
        # It deletes the old rows, which must not happen while
        # QApplication.setStyleSheet is iterating over every widget.
        if self._state is not None:
            self._conn_theme_timer.start(0)

    def _rebuild_connections_for_theme(self) -> None:
        """Replay the connections build for the shown node under the new theme."""
        if self._state is not None:
            self._rebuild_connections(self._state.id)

    @property
    def node_type_combo(self) -> QComboBox:
        """The node type dropdown on the node page."""
        return self._node_type_combo

    @property
    def node_label_edit(self) -> QLineEdit:
        """The label field on the node page."""
        return self._node_label_edit

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

    def set_clients_getter(self, fn: Callable[[], list]) -> None:
        """Pass a callable returning the currently hooked clients."""
        self._get_clients = fn
        self._param_form.set_clients_getter(fn)
        self._call_page.set_clients_getter(fn)
        self._signature_page.set_clients_getter(fn)
        self.refresh_player_availability()

    def set_default_scales_getter(self, fn: Callable[[], Any]) -> None:
        """Pass a callable returning the open bot's default Scales, for the node form."""
        self._param_form.set_default_scales_getter(fn)

    def set_variables_getter(self, fn: Callable[[], list[VariableModel]]) -> None:
        """Pass a callable returning the open bot's variable declarations, for a set_variables node's rows."""
        self._param_form.set_variables_getter(fn)

    def set_zone_suggestions_getter(self, fn: Callable[[], list[str]]) -> None:
        """Pass a callable returning zone suggestions, for the node form and the region inputs form alike."""
        self._param_form.set_zone_suggestions_getter(fn)
        self._call_page.set_zone_suggestions_getter(fn)
        self._signature_page.set_zone_suggestions_getter(fn)

    def set_subgraph_library(self, library) -> None:
        """The library an input linked into a nested instance is looked up in, to draw its field."""
        self._call_page.set_subgraph_library(library)
        self._signature_page.set_subgraph_library(library)

    def refresh_player_availability(self) -> None:
        """Re-gray the player checkboxes — call when clients connect or disconnect.

        findChildren rather than a widget registry: player checkboxes appear at several
        depths inside the param form, and this needs no bookkeeping for any of them.
        """
        connected = len(self._get_clients())
        for w in self.findChildren(PlayerListWidget) + self.findChildren(PlayerSlotWidget):
            w.set_available_players(connected)
        for button in self.findChildren(RecordButton):
            button.refresh_players()

    def show_idle(self) -> None:
        self._cancel_record()
        self._state = None
        self._transition = None
        self._region = None
        self._stack.setCurrentIndex(_PAGE_IDLE)

    @property
    def shown_state(self) -> StateModel | None:
        """The state on the node page, or None when another page is up."""
        return self._state

    @property
    def shown_transition(self) -> TransitionModel | None:
        """The transition on the edge page, or None when another page is up."""
        return self._transition

    @property
    def shown_region(self) -> RegionModel | None:
        """The region on the region page, or None when another page is up."""
        return self._region

    @property
    def call_page(self):
        """The call node's page (``CallNodePage``)."""
        return self._call_page

    @property
    def signature_page(self):
        """A definition's signature page (``SignaturePage``)."""
        return self._signature_page

    @property
    def promote_button(self) -> ActionButton:
        """The node page's Promote to input group / Demote button, shown in a definition document."""
        return self._promote_btn

    def show_state(
        self,
        state: StateModel,
        all_states: list | None = None,
        all_transitions: list[TransitionModel] | None = None,
        initial_id: str | None = None,
        recovery_id: str | None = None,
        *,
        in_definition: bool = False,
        links: dict[str, str] | None = None,
        promoted_group: str | None = None,
        field_types: dict[str, str] | None = None,
        fill_hints: dict[str, str] | None = None,
        step_chips: dict[str, str] | None = None,
        step_addable: bool = False,
    ) -> None:
        """Show *state*. *in_definition* is a node of a definition document open for editing.

        There, each param row can be linked to a subgraph input -- its Link button shows
        on hover -- and a row already linked (*links*, key -> what it is linked to) shows
        a chip that unlinks it. The page offers *Promote to input group* for the whole
        node; a node promoted to *promoted_group* has every row linked to it, locked.
        """
        self._cancel_record()
        self._state = state
        self._transition = None
        self._region = None
        self._all_states_for_node = list(all_states or [])
        self._all_transitions_for_node = list(all_transitions or [])
        self._recovery_id = recovery_id
        self._suppress = True
        self._node_id_lbl.setText(state.id)
        self._node_label_edit.setText(state.label or "")
        self._node_label_edit.setPlaceholderText(node_display_name(state.node_type))
        idx = self._node_type_combo.findData(state.node_type, Qt.ItemDataRole.UserRole)
        if idx >= 0:
            self._node_type_combo.setCurrentIndex(idx)
        schema = NODE_PARAM_SCHEMAS.get(state.node_type, [])
        self._param_form.load_schema(
            schema,
            state.params or {},
            linkable=in_definition,
            links=links,
            promoted_group=promoted_group,
            field_types=field_types,
            fill_hints=fill_hints,
            step_chips=step_chips,
            step_addable=step_addable,
            row_actions=self._row_actions_for(state.node_type, schema),
        )
        self._rebuild_connections(state.id)
        self._notes_edit.blockSignals(True)
        self._notes_edit.setPlainText(state.notes or "")
        self._notes_edit.blockSignals(False)
        self._notes_card.set_summary(param_cards.notes_summary(state.notes))
        self._node_promoted = promoted_group is not None
        self._promote_btn.set_icon_name("chevrons_down" if self._node_promoted else "chevrons_up")
        self._promote_btn.set_tint(IconTint.ACCENT if self._node_promoted else IconTint.DEFAULT)
        self._promote_btn.setToolTip(
            "Demote: take this node's input group off the subgraph node"
            if self._node_promoted
            else "Promote to input group: put every param of this node on the subgraph node, with its Record "
            "button. It follows the node: a param added to the node type later appears there too."
        )
        self._suppress = False
        self._set_role_checks(state.id, initial_id, recovery_id)
        self._promote_btn.setVisible(in_definition and bool(schema))
        # A definition's roles are a formality -- every Entry is a way in -- so there
        # is nothing for the boxes to mean there.
        self._role_card.setVisible(not in_definition)
        recordable = state.node_type in recording.RECORDABLE_NODES
        self._record_btn.setVisible(recordable)
        self._record_status_lbl.setVisible(recordable)
        self._refresh_record_hint()
        self._refresh_params_card()
        self._apply_node_folds(state.id in (initial_id, recovery_id))
        self._stack.setCurrentIndex(_PAGE_NODE)

    def _row_actions_for(self, node_type: str, schema) -> dict:
        """The wiki import, at the end of a fight's ``wiki_page`` row: the field it fills (#142)."""
        if node_type != "handle_combat" or not any(f.key == WIKI_PAGE_PARAM for f in schema):
            return {}
        return {WIKI_PAGE_PARAM: ("download", "Import drops from wiki…", self._on_wiki_import)}

    @property
    def wiki_import_button(self) -> QPushButton | None:
        """The wiki import at the end of a fight's ``wiki_page`` row, or None on any other node."""
        return self._param_form.row_action(WIKI_PAGE_PARAM)

    def _apply_node_folds(self, holds_role: bool) -> None:
        """Fold the node page's cards as the reader left them; Role opens on a node that holds one."""
        from bartlebot.gui.sidebar.option_card import UiStateFoldStore  # noqa: PLC0415

        defaults = param_cards.node_page_fold_defaults(holds_role=holds_role)
        self._node_folds.set_store(UiStateFoldStore(param_cards.NODE_PAGE_SURFACE, defaults))
        self._node_folds.reload()

    def _refresh_params_card(self) -> None:
        """The Parameters band's count and summary, and the Connections and Role summaries."""
        if self._state is None:
            return
        form = self._param_form
        self._params_count.set_count(form.to_fill(), red=form.required_errors_shown())
        params = form.get_params()
        if self._state.node_type in recording.RECORDABLE_NODES:
            parts = param_cards.recording_summary(form.schema, params, recording.recorded_keys(self._state.node_type))
        else:
            parts = param_cards.params_summary(form.schema, params)
        if self._node_promoted:
            parts = [param_cards.SummaryPart("Promoted"), *parts]
        self._params_card.set_summary(parts)

    @property
    def node_cards(self) -> dict:
        """The node page's four cards by group: ``params``, ``connections``, ``notes``, ``role``."""
        return dict(self._node_cards)

    @property
    def node_folds(self):
        return self._node_folds

    @property
    def params_to_fill(self):
        """The Parameters band's "N to fill"."""
        return self._params_count

    def _set_node_descriptions_visible(self, shown: bool) -> None:
        write_show_descriptions(shown)
        self._param_form.set_descriptions_visible(shown)

    def show_required_errors(self) -> None:
        """Draw every empty required field on the shown page red: a save was refused for them."""
        if self._stack.currentIndex() == _PAGE_NODE:
            self._param_form.show_required_errors()
        elif self._stack.currentIndex() == _PAGE_CALL:
            self._call_page.show_required_errors()

    def show_region(
        self,
        region: RegionModel,
        *,
        collapsed: bool | None = None,
        collapse_blocker: str | None = None,
        fights: list[str] | None = None,
    ) -> None:
        """Show a plain region's page: its label, color, and Collapse / Expand. Edits ``region`` in place.

        ``collapsed`` is whether it is drawn collapsed, which in Run mode can differ
        from its saved flag; it defaults to the flag. ``collapse_blocker`` is why the
        region cannot collapse, shown on the disabled button. ``fights`` are the fights a collapsed region holds, named because they
        are out of sight on the canvas.
        """
        self._cancel_record()
        self._state = None
        self._transition = None
        self._region = region
        self._suppress = True
        try:
            self._region_label_edit.setText(region.label)
            self._region_color_row.set_key(region.color)
            collapsed = bool(region.collapsed) if collapsed is None else collapsed
            self._region_collapsed = collapsed
            self._region_collapse_btn.setText("Expand" if collapsed else "Collapse")
            self._region_collapse_btn.set_icon_name("region_expand" if collapsed else "region_collapse")
            self._region_collapse_btn.setEnabled(collapsed or collapse_blocker is None)
            self._region_collapse_btn.setToolTip(
                "Draw the region's nodes again"
                if collapsed
                else (collapse_blocker or "Draw the region as one node and close up the space it uses")
            )
            lines = []
            if collapsed:
                lines.append("Drawn as one node. Double-click it to step in.")
                if fights:
                    lines.append(f"Fights inside: {', '.join(fights)}. Their strategies are set on the Combat tab.")
            self._region_fold_note.setText("\n".join(lines))
            self._region_fold_note.setVisible(bool(lines))
        finally:
            self._suppress = False
        self._stack.setCurrentIndex(_PAGE_REGION)

    def _on_region_collapse_clicked(self) -> None:
        if self._suppress or self._region is None:
            return
        self.region_collapse_requested.emit(self._region.id, not self._region_collapsed)

    @property
    def region_collapse_button(self) -> ActionButton:
        return self._region_collapse_btn

    def show_call(self, region: RegionModel, defn, status, config) -> None:
        """Show a call node's page for the subgraph instance *region*."""
        self._cancel_record()
        self._state = None
        self._transition = None
        self._region = region
        self._call_page.show_call(region, defn, status, config)
        self._stack.setCurrentIndex(_PAGE_CALL)

    def show_signature(self, config) -> None:
        """Show a definition document's signature: its page when nothing is selected."""
        self._cancel_record()
        self._state = None
        self._transition = None
        self._region = None
        self._signature_page.show_signature(config)
        self._stack.setCurrentIndex(_PAGE_SIGNATURE)

    @property
    def showing_call(self) -> bool:
        return self._stack.currentIndex() == _PAGE_CALL

    @property
    def showing_signature(self) -> bool:
        return self._stack.currentIndex() == _PAGE_SIGNATURE

    def _on_form_link_requested(self, key: str) -> None:
        if self._state is not None:
            self.link_input_requested.emit(self._state.id, key)

    def _on_form_step_add_requested(self, key: str) -> None:
        if self._state is not None:
            self.add_setup_target_requested.emit(self._state.id, key)

    def _on_form_step_remove_requested(self, key: str) -> None:
        if self._state is not None:
            self.remove_setup_target_requested.emit(self._state.id, key)

    def _on_form_unlink_requested(self, key: str) -> None:
        if self._state is not None:
            self.unlink_input_requested.emit(self._state.id, key)

    def _on_promote_clicked(self) -> None:
        if self._suppress or self._state is None:
            return
        self.promote_node_requested.emit(self._state.id, not self._node_promoted)

    def _on_region_label_changed(self, text: str) -> None:
        if self._suppress or self._region is None:
            return
        label = text.strip()
        if not label:
            return
        self.edit_starting.emit("Edit region")
        self._region.label = label
        self.region_property_changed.emit()

    def _on_region_color_changed(self, key: int) -> None:
        if self._suppress or self._region is None:
            return
        self.structural_property_changed.emit("Change Region Color")
        self._region.color = key
        self.region_property_changed.emit()

    @property
    def region_label_edit(self) -> QLineEdit:
        return self._region_label_edit

    @property
    def region_color_row(self) -> _RegionColorRow:
        return self._region_color_row

    @property
    def param_form(self) -> DynamicParamForm:
        return self._param_form

    @property
    def initial_check(self) -> QCheckBox:
        """The Initial state checkbox on the node page."""
        return self._initial_check

    @property
    def recovery_check(self) -> QCheckBox:
        """The Recovery state checkbox on the node page."""
        return self._recovery_check

    def request_initial_role(self) -> None:
        """Ask for the shown state to become the initial state, as ticking the box does."""
        if self._state is not None:
            self.initial_state_requested.emit(self._state.id)

    def request_recovery_role(self) -> None:
        """Ask for the shown state to become the recovery state, as ticking the box does."""
        if self._state is not None:
            self.recovery_state_requested.emit(self._state.id)

    def refresh_role_checks(self, initial_id: str | None, recovery_id: str | None) -> None:
        """Re-sync the role checkboxes for the state already on screen.

        Needed because moving a role changes *two* nodes: the one gaining it and the
        one losing it.  The canvas calls this after applying the move so the panel
        does not keep showing a stale tick if the shown node was the loser.
        """
        if self._state is None:
            return
        self._set_role_checks(self._state.id, initial_id, recovery_id)

    def _set_role_checks(self, state_id: str, initial_id: str | None, recovery_id: str | None) -> None:
        """Tick and enable the role boxes for *state_id*.

        The box is disabled exactly when this state already holds the role: there is
        nothing a click could mean there, since the role cannot be vacated.
        """
        was_suppressed = self._suppress
        self._suppress = True
        try:
            is_initial = state_id == initial_id
            is_recovery = state_id == recovery_id
            self._initial_check.setChecked(is_initial)
            self._initial_check.setEnabled(not is_initial)
            self._recovery_check.setChecked(is_recovery)
            self._recovery_check.setEnabled(not is_recovery)
            self._role_card.set_summary(param_cards.role_summary(initial=is_initial, recovery=is_recovery))
        finally:
            self._suppress = was_suppressed

    def _on_initial_clicked(self, checked: bool) -> None:
        """Ask the canvas to move the initial-state role to the shown state.

        ``clicked`` rather than ``toggled`` so programmatic setChecked() in
        ``show_state`` never reaches here.  An unchecking click cannot happen --
        the box is disabled once this state holds the role -- but it is refused
        rather than ignored, so a future change that enables it cannot silently
        leave the bot with no initial state.
        """
        if self._suppress or self._state is None:
            return
        if not checked:
            self._initial_check.setChecked(True)
            return
        self.request_initial_role()

    def _on_recovery_clicked(self, checked: bool) -> None:
        """Ask the canvas to move the recovery-state role to the shown state."""
        if self._suppress or self._state is None:
            return
        if not checked:
            self._recovery_check.setChecked(True)
            return
        self.request_recovery_role()

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
        self._cancel_record()
        self._transition = transition
        self._state = None
        self._region = None
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
        self._stack.setCurrentIndex(_PAGE_EDGE)

    def _on_node_label_changed(self, text: str) -> None:
        if self._suppress or self._state is None:
            return
        self.edit_starting.emit("Edit params")
        self._state.label = text or None
        self.properties_changed.emit()

    def _on_label_editing_finished(self) -> None:
        if self._suppress or self._state is None:
            return
        # An emptied label goes back to the type's name, the placeholder it showed:
        # left empty, the canvas would draw the node's id instead.
        if not (self._state.label or "").strip():
            self.edit_starting.emit("Edit params")
            self._state.label = node_display_name(self._state.node_type)
            self._suppress = True
            self._node_label_edit.setText(self._state.label)
            self._suppress = False
            self.properties_changed.emit()
        self.label_committed.emit()

    def _on_node_help(self) -> None:
        """Open the node browser in view mode for the currently displayed node type."""
        from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog

        node_type: str | None = None
        if self._state is not None:
            node_type = self._state.node_type
        else:
            node_type = self._node_type_combo.currentData(Qt.ItemDataRole.UserRole)
        NodeBrowserDialog(self, mode="view", initial_node_type=node_type).exec()

    def _on_node_type_changed(self, _index: int) -> None:
        node_type: str = self._node_type_combo.currentData(Qt.ItemDataRole.UserRole)
        if self._suppress or self._state is None or node_type not in NODE_REGISTRY:
            return
        self.structural_property_changed.emit("Change Node Type")
        self._cancel_record()
        # A label still at its type's name was never chosen, so it follows the type;
        # one the author typed stays.
        if (self._state.label or "").strip() in ("", node_display_name(self._state.node_type)):
            self._state.label = node_display_name(node_type)
            self._suppress = True
            self._node_label_edit.setText(self._state.label)
            self._suppress = False
        self._node_label_edit.setPlaceholderText(node_display_name(node_type))
        self._state.node_type = node_type
        self._state.params = {}
        schema = NODE_PARAM_SCHEMAS.get(node_type, [])
        self._param_form.load_schema(schema, {}, row_actions=self._row_actions_for(node_type, schema))
        self._state.params = self._param_form.get_params()
        self._rebuild_connections(self._state.id)
        recordable = node_type in recording.RECORDABLE_NODES
        self._record_btn.setVisible(recordable)
        self._record_status_lbl.setVisible(recordable)
        self._refresh_record_hint()
        self._refresh_params_card()
        self.structural_property_applied.emit()

    def _on_param_form_changed(self) -> None:
        if self._state is None:
            return
        self.edit_starting.emit("Edit params")
        self._state.params = self._param_form.get_params()
        self._refresh_params_card()
        if not recording.is_recording():
            self._refresh_record_hint()
        self.properties_changed.emit()

    def _on_notes_changed(self) -> None:
        if self._suppress or self._state is None:
            return
        text = self._notes_edit.toPlainText().strip()
        self.edit_starting.emit("Edit params")
        self._state.notes = text if text else None
        self._notes_card.set_summary(param_cards.notes_summary(self._state.notes))
        self.properties_changed.emit()

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

    # ------------------------------------------------------------------
    # Wiki import
    # ------------------------------------------------------------------

    def _on_wiki_import(self) -> None:
        from bartlebot.gui.dialogs.wiki_import_dialog import WikiImportDialog

        dialog = WikiImportDialog(parent=self.window())
        if dialog.exec():
            self.apply_wiki_import(dialog.selected_entries(), dialog.selected_page())

    def apply_wiki_import(self, entries: list, page: str | None) -> None:
        """Add imported drops and record the source page on the shown handle_combat node."""
        if entries:
            self.wiki_import_completed.emit(entries)
        # Record where they came from on the node that imported them. The panel
        # is only shown for handle_combat, so this is the fight whose boss page
        # was just fetched — the association the import flow already implied and
        # previously discarded.
        if not page:
            return
        self._param_form.set_param_value(WIKI_PAGE_PARAM, page)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def set_preferred_player_getter(self, fn: Callable[[], int]) -> None:
        """Pass a callable naming the player index Ctrl+R records from."""
        self._preferred_player = fn

    def set_safety_delay_getter(self, fn: Callable[[], float]) -> None:
        """Pass a callable returning the open bot's Safety Delay, how long Record watches for dialogue."""
        self._safety_delay = fn
        self._call_page.set_safety_delay_getter(fn)

    def toggle_record(self) -> None:
        """Ctrl+R: start or cancel recording the shown node.

        A key has no menu to open, so it records from the preferred player -- the game
        window last in focus, the rule Bar A's buttons follow -- rather than asking.
        """
        if recording.is_recording():
            self._cancel_record()
            return
        if not self._shows_recordable_node():
            return
        clients = self._get_clients()
        if not clients:
            self._set_record_status("No player connected.")
            return
        index = self._preferred_player()
        self.start_record(clients[index if 0 <= index < len(clients) else 0])

    def start_record(self, client) -> bool:
        """Record the shown node from *client*.

        False, and nothing started, when the shown node has no recorder or another
        recording is already live -- speed-authoring (#85) says so rather than cancelling it.
        """
        if recording.is_recording() or not self._shows_recordable_node():
            return False
        clients = self._get_clients()
        self._recording_player = clients.index(client) + 1 if client in clients else None
        if self._params_card.is_collapsed:
            # Opened to watch the fields fill; not a fold the reader chose, so nothing is remembered.
            self._params_card.set_collapsed(False)
        asyncio.ensure_future(
            recording.run_recording(
                self._state.node_type,
                client,
                self._param_form,
                self._set_record_status,
                self._record_btn.set_label,
                self._try_set_label,
                player=clients.index(client) + 1 if client in clients else None,
                safety_delay=self._safety_delay(),
            )
        )
        return True

    def _shows_recordable_node(self) -> bool:
        return self._state is not None and self._state.node_type in recording.RECORDABLE_NODES

    def _cancel_record(self) -> None:
        recording.cancel_active_recording()
        self._record_btn.set_label(IDLE_TEXT)
        self._set_record_status("")

    def _set_record_status(self, text: str) -> None:
        """What the recorder says, at the top of the Parameters card; what Record fills while it says nothing.

        A line always on screen for a recordable node, so starting a recording moves
        nothing under the pointer.
        """
        if not text:
            self._recording_player = None
            self._refresh_record_hint()
            return
        player = self._recording_player
        self._record_status_lbl.setText(f"Player {player} · {text}" if player else text)

    def _refresh_record_hint(self) -> None:
        if self._state is None or self._state.node_type not in recording.RECORDABLE_NODES:
            self._record_status_lbl.setText("")
            return
        self._record_status_lbl.setText(
            recording.recording_hint(self._state.node_type, self._param_form.schema, self._param_form.get_params())
        )

    def _on_record_clicked(self) -> None:
        if recording.is_recording():
            self._cancel_record()
            return
        if not self._shows_recordable_node():
            return
        clients = self._get_clients()
        if not clients:
            self._set_record_status("No player connected.")
            return
        pick_player(self._record_btn, len(clients), "Record", self._record_from_player)

    def _record_from_player(self, index: int) -> None:
        clients = self._get_clients()
        if index < len(clients):
            self.start_record(clients[index])

    def _try_set_label(self, label: str) -> None:
        """Auto-populate the node label if it is empty, its type's name, or one an earlier recording wrote.

        A label the author typed is never replaced.
        """
        if self._state is None:
            return
        current = (self._state.label or "").strip()
        unchosen = not current or current == node_display_name(self._state.node_type)
        if current == label or not (unchosen or recording.is_recorded_label(current)):
            return
        self.edit_starting.emit("Edit params")
        self._state.label = label
        self._suppress = True
        self._node_label_edit.setText(label)
        self._suppress = False
        self.properties_changed.emit()
        # The field never had focus, so no focus-out will draw the new label on the canvas.
        self.label_committed.emit()

    # ------------------------------------------------------------------
    # Connections card: routes by return value, then what comes in
    # ------------------------------------------------------------------

    def _rebuild_connections(self, state_id: str) -> None:
        """Rebuild the Connections card for the selected node.

        Returns were a section of their own and Connections another, and neither said
        where an unrouted FAILURE goes -- to the recovery state, with no edge drawn
        (``docs/RETURN_ROUTING.md``). One card now lists each return with its route,
        the undrawn ones included, and each return's description behind its row's
        ``?`` (#142). ``param_cards.outgoing_routes`` decides the routes, by the
        engine's rules.
        """
        while self._conn_body_layout.count():
            item = self._conn_body_layout.takeAt(0)
            if item.widget():
                discard_widget(item.widget())

        state = self._state if self._state is not None and self._state.id == state_id else None
        all_t = self._all_transitions_for_node
        state_map = {s.id: (s.label or s.id) for s in self._all_states_for_node}
        spec = NODE_SPECS.get(state.node_type) if state is not None else None
        returns = [r.name for r in spec.returns] if spec and spec.returns else []
        descriptions = {r.name: r.description for r in spec.returns} if spec and spec.returns else {}
        routes = (
            param_cards.outgoing_routes(
                state,
                returns,
                all_t,
                recovery_state=self._recovery_id,
                state_ids=state_map,
            )
            if state is not None
            else []
        )
        incoming = [t for t in all_t if t.dest == state_id and t.source != state_id]

        if not routes and not incoming:
            placeholder = QLabel("No connections")
            placeholder.setStyleSheet(f"color: {THEME.text_muted}; font-size: {THEME.font_small_px}px;")
            self._conn_body_layout.addWidget(placeholder)
            self._conn_card.set_summary(param_cards.routes_summary([], state_map))
            return

        if routes:
            self._conn_body_layout.addWidget(self._conn_heading("Outgoing, by return"))
            for route in routes:
                self._conn_body_layout.addWidget(self._make_route_row(route, state_map, descriptions))
        if incoming:
            self._conn_body_layout.addWidget(self._conn_heading("Incoming", spaced=bool(routes)))
            for t in incoming:
                row = self._make_conn_row(
                    t,
                    badge_text=None,
                    chip_text=t.when or "always",
                    node_label=state_map.get(t.source, t.source),
                    node_id=t.source,
                    is_outgoing=False,
                )
                # Behind an empty gutter, so its chip lines up with the routes' above.
                revert, help_btn = RevertButton(), HelpToggle()
                self._conn_body_layout.addWidget(build_gutter_row(OverrideBar(), revert, help_btn, row))
                keep_slot_when_hidden(revert)
                keep_slot_when_hidden(help_btn)
        self._conn_card.set_summary(param_cards.routes_summary(routes, state_map))

    @staticmethod
    def _conn_heading(text: str, *, spaced: bool = False) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {THEME.text_muted}; font-size: {THEME.font_subtext_px}px; font-weight: 600;"
            f"{' margin-top: 4px;' if spaced else ''}"
        )
        return lbl

    def _make_route_row(self, route, state_map: dict[str, str], descriptions: dict[str, str]) -> QWidget:
        """One outgoing route: its priority, its return, and where it goes -- the undrawn ones say so."""
        if route.kind == param_cards.EDGE:
            body = self._make_conn_row(
                route.transition,
                badge_text=str(route.priority),
                chip_text=route.when or "always",
                node_label=state_map.get(route.dest, route.dest),
                node_id=route.dest,
                is_outgoing=True,
            )
        else:
            body = self._make_conn_row(
                None,
                badge_text="",
                chip_text=route.when or "always",
                node_label=state_map.get(route.dest, route.dest) if route.dest else "",
                node_id=route.dest,
                is_outgoing=True,
                note=self._route_note(route),
            )
        prose = descriptions.get(route.when or "", "")
        col_w = QWidget()
        col = QVBoxLayout(col_w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        col.addWidget(body)
        description = None
        if prose:
            description = ThemedTextLabel(prose, color="text_muted")
            description.setWordWrap(True)
            col.addWidget(description)
        help_btn = HelpToggle()
        help_btn.setToolTip("Show what this return means")
        revert = RevertButton()
        outer = build_gutter_row(OverrideBar(), revert, help_btn, col_w)
        # The Reset slot is kept, so these rows' ? lines up with the params' above.
        keep_slot_when_hidden(revert)
        if description is None:
            keep_slot_when_hidden(help_btn)
        else:
            shown = self._param_form.descriptions_visible
            help_btn.setChecked(shown)
            description.setVisible(shown)
            help_btn.toggled.connect(description.setVisible)
        return outer

    @staticmethod
    def _route_note(route) -> tuple[str, str]:
        """The chip a route that is not an edge wears: its text and its color token."""
        if route.kind == param_cards.RECOVERY:
            return "recovery · not drawn", "warning"
        if route.kind == param_cards.STOP:
            return (f"stops the bot: {route.reason}" if route.reason else "stops the bot"), "error"
        if route.kind == param_cards.END:
            return "ends the run", "text_muted"
        return "not routed", "warning"

    def _make_conn_row(
        self,
        transition: TransitionModel | None,
        badge_text: str | None,
        chip_text: str,
        node_label: str,
        node_id: str | None,
        is_outgoing: bool,
        note: tuple[str, str] | None = None,
    ) -> QWidget:
        """Build a single connection row widget: a route, or an edge coming in."""
        row = QWidget()
        row.setStyleSheet(f"QWidget {{ border-radius: 4px; }}QWidget:hover {{ background: {THEME.surface}; }}")
        if transition is not None:
            row.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        # Priority badge (outgoing only): the order the engine tries edges in.
        if badge_text is not None:
            badge = QLabel(badge_text)
            badge.setFixedWidth(14)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setToolTip("Tried in this order: conditional edges first")
            badge.setStyleSheet(f"color: {THEME.text_muted}; font-size: {THEME.font_micro_px}px; font-weight: 600;")
            layout.addWidget(badge)

        # Condition chip
        chip_text_upper = chip_text.upper()
        if chip_text_upper in ("SUCCESS", "TRUE"):
            chip_bg = THEME.chip_success_bg
            chip_fg = "#ffffff"
        elif chip_text_upper in ("FAILURE", "FALSE"):
            chip_bg = THEME.chip_failure_bg
            chip_fg = "#ffffff"
        elif chip_text == "always":
            chip_bg = THEME.surface
            chip_fg = THEME.text_muted
        else:
            chip_bg = THEME.chip_neutral_bg
            chip_fg = "#ffffff"
        chip = QLabel(chip_text)
        chip.setStyleSheet(
            f"background: {chip_bg}; color: {chip_fg}; border-radius: 3px;"
            f" font-size: {THEME.font_subtext_px}px; font-weight: 600; padding: 1px 5px;"
        )
        arrow = QLabel("→" if is_outgoing else "←")
        arrow.setStyleSheet(f"color: {THEME.text_muted}; font-size: {THEME.font_small_px}px;")

        # Node name link. Elided: a long label beside a route's note would otherwise
        # widen the whole page past the sidebar.
        name_lbl = ElidedLabel(node_label)
        name_lbl.setStyleSheet(f"color: {THEME.accent}; font-size: {THEME.font_small_px}px;")
        name_lbl.setFont(THEME.ui_font(THEME.font_small_px))
        name_lbl.set_text_color(THEME.accent)
        name_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        name_lbl.setToolTip(f"Navigate to: {node_label}")

        layout.addWidget(chip)
        if node_label:
            layout.addWidget(arrow)
            layout.addWidget(name_lbl, 1 if note is None else 0)
        if note is not None:
            text, token = note
            color = getattr(THEME, token)
            note_lbl = QLabel(text)
            note_lbl.setStyleSheet(
                f"color: {color}; border: 1px solid {color}; border-radius: 3px;"
                f" font-size: {THEME.font_subtext_px}px; padding: 0px 5px;"
            )
            if text.startswith("recovery"):
                note_lbl.setToolTip("No edge is drawn: an unrouted FAILURE goes to the recovery state")
            layout.addWidget(note_lbl)
            layout.addStretch(1)

        # Clicking the node name navigates to that state
        def _on_name_clicked(ev, nid=node_id):
            ev.accept()
            if nid:
                self.select_state_requested.emit(nid)

        name_lbl.mousePressEvent = _on_name_clicked  # type: ignore[method-assign]

        # Clicking anywhere else on the row selects the edge
        if transition is not None:

            def _on_row_pressed(ev, t=transition):
                self.select_edge_requested.emit(t)

            row.mousePressEvent = _on_row_pressed  # type: ignore[method-assign]

        return row
