"""The call node's properties page: a subgraph instance's signature, filled in.

The page is the signature. Label, roles, inputs, status and two actions: an instance
sets inputs and nothing else, so there is nothing else to show. Input rows are built
by the same form code as a node's params, so a zone input has Fill current and Go
and a coordinate input has Copy, Paste and TP, exactly as on a node.

Panels are collapsible sections. An *inputs* panel groups inputs under a heading; a
*node* panel is a member exposed whole, built from the member's own ``ParamField``\\s
and keyed by its own keys -- which is what lets the recorder run on it unchanged, so
a call node carries a Record button exactly when its author exposed a recordable node.
A panel holding an empty required input says so on its header even while collapsed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from bartlebot.bot.schema import (
    SUBGRAPH_ENTRY,
    BotConfig,
    RegionModel,
    SubgraphDefinition,
    SubgraphPanel,
)
from bartlebot.gui.canvas import recording
from bartlebot.gui.sidebar.sidebar_section import disclosure_pixmap
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin, set_style_sheet
from bartlebot.gui.widgets.action_button import ActionButton, ActionStyle
from bartlebot.gui.widgets.rule_heading import RuleHeading
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS
from bartlebot.subgraph_store import source_label
from bartlebot.subgraphs import RegionStatus, exposed_key, local_id, member_id, port_name

#: How the page names where a definition comes from.

_STATUS_WORD = {"linked": "linked", "held": "update held", "missing": "missing", "invalid": "invalid", "cycle": "cycle"}

_NO_ROLE = "—"


class PanelSection(ThemedMixin, QWidget):
    """One panel on the page: a disclosure header over a form.

    The header carries the panel's label, an error mark while any required field in
    it is empty -- so collapsing a panel can never hide an unfilled input -- and, for
    an exposed recordable node, its Record button.
    """

    def __init__(self, title: str, form: QWidget, *, closed: bool = False, record: QWidget | None = None) -> None:
        super().__init__()
        self._form = form
        self._expanded = not closed
        self._arrow = QLabel()
        self._title = QLabel(title)
        self._error = QLabel("● required input empty")
        self._error.setVisible(False)
        head = QWidget()
        head.setCursor(Qt.CursorShape.PointingHandCursor)
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(0, 6, 0, 2)
        head_row.setSpacing(6)
        head_row.addWidget(self._arrow)
        head_row.addWidget(self._title)
        head_row.addWidget(self._error)
        head_row.addStretch(1)
        if record is not None:
            head_row.addWidget(record)
        head.mousePressEvent = lambda _ev: self.set_expanded(not self._expanded)  # type: ignore[method-assign]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(head)
        layout.addWidget(form)
        form.setVisible(self._expanded)
        self.refresh_theme_styles()

    @property
    def expanded(self) -> bool:
        return self._expanded

    @property
    def form(self) -> QWidget:
        return self._form

    @property
    def shows_error(self) -> bool:
        return not self._error.isHidden()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._form.setVisible(expanded)
        self.refresh_theme_styles()

    def set_error(self, error: bool) -> None:
        self._error.setVisible(error)

    def apply_theme_styles(self) -> None:
        self._arrow.setPixmap(disclosure_pixmap(self._expanded))
        font = self._title.font()
        font.setBold(True)
        font.setPixelSize(THEME.font_body_px)
        self._title.setFont(font)
        set_style_sheet(self._title, f"color: {THEME.text};")
        set_style_sheet(self._error, f"color: {THEME.error}; font-size: {THEME.font_small_px}px;")


def _empty_required(form) -> bool:
    """Whether a form built by ``_DynamicParamForm`` has a required field left empty."""
    from bartlebot.gui.canvas.edit_scene import _required_field_empty  # noqa: PLC0415

    params = form.get_params()
    return any(_required_field_empty(f, params) for f in form.required_fields())


class CallNodePage(ThemedMixin, QWidget):
    """The call node's page. Edits ``region`` in place and reports what it changed.

    ``edit_starting`` opens the debounced undo step for a typed edit before it lands;
    ``structural_change`` pushes an immediate one; ``changed`` follows either. Roles are
    asked for, not assigned: ``role_requested(field, state id)`` names the member the
    chosen entry port leads to.
    """

    edit_starting = pyqtSignal(str)
    structural_change = pyqtSignal(str)
    changed = pyqtSignal()
    role_requested = pyqtSignal(str, str)
    open_definition_requested = pyqtSignal(str)
    detach_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._region: RegionModel | None = None
        self._defn: SubgraphDefinition | None = None
        self._config: BotConfig | None = None
        self._suppress = False
        self._forms: dict[str | None, object] = {}
        self._sections: dict[str, PanelSection] = {}
        self._initial_values: dict[str | None, dict] = {}
        self._get_clients: Callable[[], list] = lambda: []
        self._get_zones: Callable[[], list[str]] = lambda: []
        self._record_status: dict[str, QLabel] = {}
        self._status: RegionStatus | None = None

        self._name_lbl = QLabel()
        self._sub_lbl = ThemedTextLabel("", color="text_muted")
        self._sub_lbl.setWordWrap(True)
        self._desc_lbl = ThemedTextLabel("", color="text_muted")
        self._desc_lbl.setWordWrap(True)
        self._status_lbl = ThemedTextLabel("", color="warning")
        self._status_lbl.setWordWrap(True)
        self._label_edit = QLineEdit()
        self._initial_combo = QComboBox()
        self._recovery_combo = QComboBox()
        self._initial_combo.setToolTip("Start the bot at this entry port")
        self._recovery_combo.setToolTip("Resume here after a crashed client reconnects")
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Label", self._label_edit)
        form.addRow("Initial", self._initial_combo)
        form.addRow("Recovery", self._recovery_combo)

        self._inputs_heading = RuleHeading("Subgraph Inputs")
        self._inputs_note = ThemedTextLabel("This subgraph has no inputs.", color="text_muted")
        self._inputs_box = QWidget()
        self._inputs_layout = QVBoxLayout(self._inputs_box)
        self._inputs_layout.setContentsMargins(0, 0, 0, 0)
        self._inputs_layout.setSpacing(2)

        self._combat_heading = RuleHeading("Combat")
        self._combat_note = ThemedTextLabel("", color="text_muted")
        self._combat_note.setWordWrap(True)

        self._open_btn = ActionButton("pencil", "Open definition", style=ActionStyle.SECONDARY, fill=False)
        self._open_btn.setToolTip("Edit this subgraph's definition; saving it updates every bot that uses it")
        self._detach_btn = ActionButton("detach", "Detach from library", fill=False)
        self._detach_btn.setToolTip(
            "Drop the link to the library: the states stay exactly as they are, as ordinary states, "
            "and stop following updates"
        )
        actions = QWidget()
        actions_row = QHBoxLayout(actions)
        actions_row.setContentsMargins(0, 8, 0, 0)
        actions_row.addStretch(1)
        actions_row.addWidget(self._open_btn)
        actions_row.addWidget(self._detach_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.addWidget(self._name_lbl)
        layout.addWidget(self._sub_lbl)
        layout.addWidget(self._desc_lbl)
        layout.addWidget(self._status_lbl)
        layout.addLayout(form)
        layout.addWidget(self._inputs_heading)
        layout.addWidget(self._inputs_note)
        layout.addWidget(self._inputs_box)
        layout.addWidget(self._combat_heading)
        layout.addWidget(self._combat_note)
        layout.addWidget(actions)
        layout.addStretch(1)

        self._label_edit.textChanged.connect(self._on_label_changed)
        self._initial_combo.currentIndexChanged.connect(lambda _i: self._on_role_chosen("initial"))
        self._recovery_combo.currentIndexChanged.connect(lambda _i: self._on_role_chosen("recovery_state"))
        self._open_btn.clicked.connect(lambda: self._region and self.open_definition_requested.emit(self._region.id))
        self._detach_btn.clicked.connect(lambda: self._region and self.detach_requested.emit(self._region.id))
        self.refresh_theme_styles()

    # -- wiring --------------------------------------------------------

    def set_clients_getter(self, fn: Callable[[], list]) -> None:
        self._get_clients = fn

    def set_zone_suggestions_getter(self, fn: Callable[[], list[str]]) -> None:
        self._get_zones = fn

    @property
    def region(self) -> RegionModel | None:
        return self._region

    def form(self, panel_id: str | None = None):
        """The form for the top-level inputs (``None``) or for one panel, by id."""
        return self._forms.get(panel_id)

    def section(self, panel_id: str) -> PanelSection | None:
        return self._sections.get(panel_id)

    @property
    def label_edit(self) -> QLineEdit:
        return self._label_edit

    @property
    def initial_combo(self) -> QComboBox:
        return self._initial_combo

    @property
    def open_button(self) -> ActionButton:
        return self._open_btn

    @property
    def status_text(self) -> str:
        return self._status_lbl.text()

    @property
    def combat_text(self) -> str:
        return self._combat_note.text()

    # -- showing -------------------------------------------------------

    def show_call(
        self,
        region: RegionModel,
        defn: SubgraphDefinition | None,
        status: RegionStatus | None,
        config: BotConfig,
    ) -> None:
        from bartlebot.gui.canvas.param_panel import _DynamicParamForm, input_param_field  # noqa: PLC0415

        recording.cancel_active_recording()
        self._region, self._defn, self._config = region, defn, config
        self._status = status
        source = region.source
        assert source is not None
        self._suppress = True
        try:
            self._name_lbl.setText(region.label)
            name = defn.name if defn is not None else source.subgraph_id
            parts = [f"Subgraph · {name}"]
            if status is not None:
                parts.append(source_label(status.origin))
            parts.append(f"v{source.version}")
            if status is not None and status.library_version not in (None, source.version):
                parts.append(f"library v{status.library_version}")
            parts.append(_STATUS_WORD.get(status.kind if status is not None else "linked", "linked"))
            self._sub_lbl.setText(" · ".join(parts))
            desc = (defn.description or "") if defn is not None else ""
            self._desc_lbl.setText(desc)
            self._desc_lbl.setVisible(bool(desc))
            reasons = list(status.reasons) if status is not None else []
            if defn is None and not reasons:
                reasons = ["The library has no definition for this subgraph; the stored snapshot runs."]
            self._status_lbl.setText("\n".join(reasons))
            self._status_lbl.setVisible(bool(reasons))
            self._label_edit.setText(region.label)
            self._fill_role_combos()

            while self._inputs_layout.count():
                item = self._inputs_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._forms.clear()
            self._sections.clear()
            self._initial_values.clear()
            self._record_status.clear()
            inputs = list(defn.inputs) if defn is not None else []
            panels = list(defn.panels) if defn is not None else []

            def make_form() -> _DynamicParamForm:
                f = _DynamicParamForm()
                f.set_clients_getter(self._get_clients)
                f.set_zone_suggestions_getter(self._get_zones)
                return f

            # What this instance set. An unset input follows the definition's default, so
            # the form shows that (the field's default) and offers no Reset.
            chosen = {k: v for k, v in source.inputs.items() if v is not None}
            top = [i for i in inputs if i.panel is None or not any(p.id == i.panel for p in panels)]
            if top:
                form = make_form()
                form.load_schema(
                    [input_param_field(defn, i) for i in top], chosen, resettable={i.name for i in top} & set(chosen)
                )
                self._add_form(None, form)
                self._inputs_layout.addWidget(form)
            for panel in panels:
                if panel.kind == "inputs":
                    members = [i for i in inputs if i.panel == panel.id]
                    form = make_form()
                    form.load_schema(
                        [input_param_field(defn, i) for i in members],
                        chosen,
                        resettable={i.name for i in members} & set(chosen),
                    )
                    self._add_form(panel.id, form)
                    section = PanelSection(panel.label or panel.id, form, closed=panel.default_closed)
                else:
                    form, section = self._node_panel(panel, make_form)
                    if form is None:
                        continue
                self._sections[panel.id] = section
                self._inputs_layout.addWidget(section)
            self._inputs_note.setVisible(not self._forms)
            self._inputs_box.setVisible(bool(self._forms))
            self._refresh_panel_errors()

            fights = [
                s.label or (local_id(region.id, s.id) or s.id)
                for s in config.states
                if s.id in set(region.members) and s.node_type == "handle_combat"
            ]
            self._combat_heading.setVisible(bool(fights))
            self._combat_note.setVisible(bool(fights))
            if fights:
                self._combat_note.setText(
                    f"Fights inside: {', '.join(fights)}. Their strategies are set on the Combat tab, "
                    "like any fight's, and stay with this instance through updates."
                )
            self._open_btn.setEnabled(defn is not None)
        finally:
            self._suppress = False

    def _add_form(self, key: str | None, form) -> None:
        self._forms[key] = form
        self._initial_values[key] = form.get_params()
        form.changed.connect(lambda k=key: self._on_form_changed(k))
        form.reset_requested.connect(lambda field_key, k=key: self.reset_value(k, field_key))

    def _node_panel(self, panel: SubgraphPanel, make_form):
        """A member exposed whole: its own fields, keyed by its own keys, valued from ``source.inputs``."""
        assert self._region is not None and self._config is not None and self._region.source is not None
        member = member_id(self._region.id, panel.member or "")
        state = next((s for s in self._config.states if s.id == member), None)
        if state is None:
            return None, None
        schema = NODE_PARAM_SCHEMAS.get(state.node_type, [])
        # What the member runs with is what the form shows: its composed params,
        # which already carry any value this instance set.
        form = make_form()
        prefix = exposed_key(panel.member or "", "")
        changed = {
            k[len(prefix) :] for k, v in self._region.source.inputs.items() if k.startswith(prefix) and v is not None
        }
        form.load_schema(schema, dict(state.params), resettable=changed)
        self._add_form(panel.id, form)
        record = None
        if state.node_type in recording.RECORDABLE_NODES:
            record = ActionButton("", "Record", fill=False)
            record.setToolTip("Record this node's values from the game, as on the node itself")
            status = QLabel("")
            self._record_status[panel.id] = status
            record.clicked.connect(lambda _=False, p=panel.id, nt=state.node_type, b=record: self._record(p, nt, b))
        section = PanelSection(
            state.label or panel.member or panel.id, form, closed=panel.default_closed, record=record
        )
        if record is not None:
            section.layout().insertWidget(1, self._record_status[panel.id])
        return form, section

    def _record(self, panel_id: str, node_type: str, button: ActionButton) -> None:
        """Run the node's own recorder against its panel's form -- the recorder needs no changes."""
        if recording.is_recording():
            recording.cancel_active_recording()
            button.setText("Record")
            return
        status = self._record_status.get(panel_id)
        clients = self._get_clients()
        if not clients:
            if status is not None:
                status.setText("No player connected.")
            return
        form = self._forms.get(panel_id)
        section = self._sections.get(panel_id)
        if section is not None and not section.expanded:
            section.set_expanded(True)
        asyncio.ensure_future(
            recording.run_recording(
                node_type,
                clients[0],
                form,
                status.setText if status is not None else (lambda _t: None),
                button.setText,
                lambda _label: None,  # the member's label belongs to the definition
            )
        )

    def _fill_role_combos(self) -> None:
        """Both roles are chosen by entry port; underneath they are still member ids."""
        assert self._region is not None and self._config is not None and self._region.source is not None
        rid = self._region.id
        entries = [
            (s.id, port_name(s))
            for s in (self._defn.states if self._defn is not None else [])
            if s.node_type == SUBGRAPH_ENTRY
        ]
        ports = self._region.source.ports
        if not entries:
            entries = [(pid, pid) for pid, p in ports.items() if p.member is not None]
        for combo, role in (
            (self._initial_combo, self._config.initial),
            (self._recovery_combo, self._config.recovery_state),
        ):
            combo.clear()
            combo.addItem(_NO_ROLE, None)
            current = 0
            for pid, name in entries:
                port = ports.get(pid)
                target = member_id(rid, port.member) if port is not None and port.member is not None else None
                combo.addItem(name, target)
                if target is not None and target == role:
                    current = combo.count() - 1
            combo.setCurrentIndex(current)
            # The role cannot be vacated, only moved: "none" is offered only to show
            # that the role lives elsewhere, never as a choice.
            model = combo.model()
            model.item(0).setEnabled(current == 0)

    def refresh_roles(self) -> None:
        if self._region is None or self._config is None:
            return
        self._suppress = True
        try:
            self._fill_role_combos()
        finally:
            self._suppress = False

    def _refresh_panel_errors(self) -> None:
        for pid, section in self._sections.items():
            form = self._forms.get(pid)
            section.set_error(form is not None and _empty_required(form))

    # -- edits ---------------------------------------------------------

    def _on_label_changed(self, text: str) -> None:
        if self._suppress or self._region is None or not text.strip():
            return
        self.edit_starting.emit("Rename subgraph")
        self._region.label = text.strip()
        self._name_lbl.setText(self._region.label)
        self.changed.emit()

    def _on_role_chosen(self, field: str) -> None:
        if self._suppress:
            return
        combo = self._initial_combo if field == "initial" else self._recovery_combo
        target = combo.currentData()
        if target is not None:
            self.role_requested.emit(field, target)

    def _on_form_changed(self, key: str | None) -> None:
        """Write the values the user has touched into ``source.inputs``.

        Only a value that was already set, or whose widget moved from what it loaded
        with, is written: an unset input is ``None`` and leaves the definition's value
        in place, and a widget always has *some* value, so writing every key would turn
        every untouched input into an empty choice.
        """
        if self._suppress or self._region is None or self._region.source is None:
            return
        source = self._region.source
        form = self._forms.get(key)
        if form is None:
            return
        panel = next((p for p in (self._defn.panels if self._defn else []) if p.id == key), None)
        prefix = exposed_key(panel.member or "", "") if panel is not None and panel.kind == "node" else ""
        current = form.get_params()
        initial = self._initial_values.get(key, {})
        self.edit_starting.emit("Edit subgraph inputs")
        for k, value in current.items():
            name = prefix + k
            if name in source.inputs or value != initial.get(k):
                source.inputs[name] = value
        # A blank coordinate is absent from the form's params rather than empty; clearing one unsets it.
        for k in list(initial.keys() - current.keys()):
            if prefix + k in source.inputs:
                source.inputs[prefix + k] = None
        self._refresh_panel_errors()
        self.changed.emit()

    def reset_value(self, panel_id: str | None, key: str) -> None:
        """Forget this instance's value for one field, so it follows the definition's default again."""
        if self._region is None or self._region.source is None or self._config is None:
            return
        panel = next((p for p in (self._defn.panels if self._defn else []) if p.id == panel_id), None)
        name = exposed_key(panel.member or "", key) if panel is not None and panel.kind == "node" else key
        if name not in self._region.source.inputs:
            return
        self.structural_change.emit("Reset to default")
        del self._region.source.inputs[name]
        self.changed.emit()
        # The canvas has re-resolved the members; show what they now run with.
        self.show_call(self._region, self._defn, self._status, self._config)

    def apply_theme_styles(self) -> None:
        font = self._name_lbl.font()
        font.setBold(True)
        font.setPixelSize(THEME.font_heading_px)
        self._name_lbl.setFont(font)
        set_style_sheet(self._name_lbl, f"color: {THEME.text};")
