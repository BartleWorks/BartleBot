"""The properties panel's node page: a node's label, type, params, connections, notes and role.

Since #142 a page of cards like the subgraph node's: the label as the title, one gray
line for the node type and id, then Parameters, Connections, Notes and Role, the whole
page scrolling as one. It owns the Record button and delegates recording to
``recording.py``. ``param_panel._ParamPanel`` shows it in its stack.
"""

import asyncio
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
from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPen, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import StateModel, TransitionModel, VariableModel
from bartlebot.game.wiki_pages import WIKI_PAGE_PARAM
from bartlebot.gui.canvas import recording
from bartlebot.gui.canvas.node_cards import PageFoldBar, TitleEdit, ToFillLabel, record_line
from bartlebot.gui.canvas.param_form import DynamicParamForm, write_show_descriptions
from bartlebot.gui.sidebar.option_card import CardFolds, OptionCard, UiStateFoldStore
from bartlebot.gui.widgets.buttons.record_button import IDLE_TEXT, RecordButton
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


class NodePage(ThemedMixin, QWidget):
    """One node's page. Edits the shown ``StateModel`` in place; asks the canvas for the rest.

    The signals are the panel's own, under the same names, which forwards each one: see
    ``_ParamPanel`` for what each means.
    """

    edit_starting = pyqtSignal(str)
    properties_changed = pyqtSignal()
    label_committed = pyqtSignal()
    structural_property_changed = pyqtSignal(str)
    structural_property_applied = pyqtSignal()
    wiki_import_completed = pyqtSignal(list)
    select_edge_requested = pyqtSignal(object)  # TransitionModel
    select_state_requested = pyqtSignal(str)  # state ID
    initial_state_requested = pyqtSignal(str)  # state ID
    recovery_state_requested = pyqtSignal(str)  # state ID
    link_input_requested = pyqtSignal(str, str)
    unlink_input_requested = pyqtSignal(str, str)
    promote_node_requested = pyqtSignal(str, bool)
    add_setup_target_requested = pyqtSignal(str, str)
    remove_setup_target_requested = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: StateModel | None = None
        self._recovery_id: str | None = None
        self._suppress: bool = False
        self._get_clients: Callable[[], list] = lambda: []
        # The player Ctrl+R records from; AppController points it at the last-focused game window.
        self._preferred_player: Callable[[], int] = lambda: 0
        # The open bot's Safety Delay, for Record; BotCanvas resolves it as a run would.
        self._safety_delay: Callable[[], float] = lambda: SAFETY_DELAY_OPTION.default
        # All states/transitions for connections panel; populated by show_state()
        self._all_states_for_node: list = []
        self._all_transitions_for_node: list[TransitionModel] = []

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

        node_col = QVBoxLayout(self)
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

        # Visibility only once everything is parented: see "Never show a widget before
        # it has a parent" in gui/CLAUDE.md.
        self._promote_btn.setVisible(False)
        self._record_btn.setVisible(False)
        self._record_status_lbl.setVisible(False)

        self._node_label_edit.textChanged.connect(self._on_node_label_changed)
        self._node_label_edit.editingFinished.connect(self._on_label_editing_finished)
        self._node_type_combo.currentIndexChanged.connect(self._on_node_type_changed)
        self._initial_check.clicked.connect(self._on_initial_clicked)
        self._recovery_check.clicked.connect(self._on_recovery_clicked)
        self._param_form.changed.connect(self._on_param_form_changed)
        self._notes_edit.textChanged.connect(self._on_notes_changed)
        self._param_form.link_requested.connect(self._on_form_link_requested)
        self._param_form.step_add_requested.connect(self._on_form_step_add_requested)
        self._param_form.step_remove_requested.connect(self._on_form_step_remove_requested)
        self._param_form.unlink_requested.connect(self._on_form_unlink_requested)
        self._param_form.required_shown_changed.connect(self._refresh_params_card)

        # The connection rows are the one part of this page a theme switch has to
        # rebuild rather than restyle, and rebuilding deletes widgets — so it is
        # deferred off the StyleChange handler. See apply_theme_styles.
        self._conn_theme_timer = QTimer(self)
        self._conn_theme_timer.setSingleShot(True)
        self._conn_theme_timer.timeout.connect(self._rebuild_connections_for_theme)

        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        for header in self._node_type_headers:
            header.setForeground(QColor(THEME.text_muted))
        self._node_help_btn.refresh_theme_styles()
        # The status line owns its color, but a switch delivered to this page alone
        # reaches no child, so it is asked here as the title and id are.
        self._record_status_lbl.refresh_theme_styles()
        self._node_id_lbl.refresh_theme_styles()
        self._node_label_edit.refresh_theme_styles()
        self._notes_edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {THEME.surface}; border: 1px solid {THEME.border}; "
            f"border-radius: 4px; color: {THEME.text}; font-size: {THEME.font_small_px}px; padding: 3px; }}"
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

    def set_clients_getter(self, fn: Callable[[], list]) -> None:
        """Pass a callable returning the currently hooked clients."""
        self._get_clients = fn
        self._param_form.set_clients_getter(fn)

    def set_default_scales_getter(self, fn: Callable[[], Any]) -> None:
        """Pass a callable returning the open bot's default Scales, for the node form."""
        self._param_form.set_default_scales_getter(fn)

    def set_variables_getter(self, fn: Callable[[], list[VariableModel]]) -> None:
        """Pass a callable returning the open bot's variable declarations, for a set_variables node's rows."""
        self._param_form.set_variables_getter(fn)

    def set_zone_suggestions_getter(self, fn: Callable[[], list[str]]) -> None:
        """Pass a callable returning zone suggestions, for the node form."""
        self._param_form.set_zone_suggestions_getter(fn)

    @property
    def state(self) -> StateModel | None:
        """The state on the page, or None once the panel has moved to another page."""
        return self._state

    def clear(self) -> None:
        """Let go of the shown state, and cancel a recording into it: the panel is showing another page."""
        self._cancel_record()
        self._state = None

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
        """Draw every empty required field on the page red: a save was refused for them."""
        self._param_form.show_required_errors()

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
