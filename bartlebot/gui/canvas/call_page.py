"""The call node's properties page: a subgraph instance's signature, filled in.

The page is the signature. Label, inputs, roles, status and two actions: an instance
sets inputs and nothing else, so there is nothing else to show. Input rows are built
by the same form code as a node's params, so a zone input has Fill current and Go and
a coordinate input has Fill current and TP, exactly as on a node.

Since #142 the page is cards, #138's ``OptionCard``: one for the inputs no input group
holds, one per input group, and one for the roles. A *node* group is a member exposed
whole, built from the member's own ``ParamField``\\s and keyed by its own keys -- which
is what lets the recorder run on it unchanged -- and when that node is recordable its
card carries Record on its band, where it can be pressed with the card folded.

A card's band says how many required fields it still needs, folded or not, and a
folded card summarizes its values in one line. Which cards are open is the reader's,
kept per definition in ``ui_state.json``; until the reader folds or opens one, a card
holding an empty required field starts open and a complete one folded
(``param_cards.call_page_fold_defaults``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from bartlebot import param_cards
from bartlebot.bot.schema import (
    SUBGRAPH_ENTRY,
    BotConfig,
    RegionModel,
    SubgraphDefinition,
    SubgraphPanel,
)
from bartlebot.gui.canvas import recording
from bartlebot.gui.canvas.node_cards import PageFoldBar, TitleEdit, ToFillLabel, record_line
from bartlebot.gui.sidebar.option_card import CardFolds, OptionCard, UiStateFoldStore
from bartlebot.gui.widgets.action_button import ActionButton, ActionStyle
from bartlebot.gui.widgets.discard import discard_widget
from bartlebot.gui.widgets.player_picker import pick_player
from bartlebot.gui.widgets.record_button import IDLE_TEXT, RecordButton
from bartlebot.gui.widgets.rule_heading import RuleHeading
from bartlebot.gui.widgets.themed_label import ThemedTextLabel, ThemedWrappingElidedLabel
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS
from bartlebot.subgraph_store import source_label
from bartlebot.subgraphs import (
    RegionStatus,
    exposed_key,
    input_targets,
    local_id,
    member_id,
    port_name,
    promoted_param_type,
)

_STATUS_WORD = {"linked": "linked", "held": "update held", "missing": "missing", "invalid": "invalid", "cycle": "cycle"}

_NO_ROLE = "—"

#: The cards' gap: the same as the option surfaces'.
_CARD_SPACING_PX = 8


class _NoFolds:
    """The fold store of a page showing nothing yet: nothing stored, nothing written."""

    def read(self) -> dict[str, bool]:
        return {}

    def default(self, group: str) -> bool:  # noqa: ARG002
        return False

    def write(self, folds, changes) -> None:  # noqa: ARG002
        return


class CallNodePage(QWidget):
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
    # (region id, key): on a subgraph node nested in a definition document, link one of
    # its inputs to an input of the definition around it, or unlink it. The key is the
    # one its ``source.inputs`` uses: an input's name, or a promoted node's
    # ``<member>.<key>``. The page only reports; the canvas edits the signature.
    link_input_requested = pyqtSignal(str, str)
    unlink_input_requested = pyqtSignal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library = None
        self._region: RegionModel | None = None
        self._defn: SubgraphDefinition | None = None
        # (state id, param) -> setup_steps.FillIn; see set_fill_ins.
        self._fill_ins: dict = {}
        self._fill_ins_draft = False
        self._config: BotConfig | None = None
        self._suppress = False
        self._forms: dict[str | None, object] = {}
        self._cards: dict[str, OptionCard] = {}
        self._counts: dict[str, ToFillLabel] = {}
        self._card_of_form: dict[str | None, str] = {}
        self._initial_values: dict[str | None, dict] = {}
        self._get_clients: Callable[[], list] = lambda: []
        self._get_zones: Callable[[], list[str]] = lambda: []
        self._record_status: dict[str, QLabel] = {}
        self._record_hint: dict[str, Callable[[], str]] = {}
        self._recorded_nodes: dict[str, str] = {}
        self._status: RegionStatus | None = None

        self._label_edit = TitleEdit()
        # Both carry user text -- the subgraph's name or id, its description -- and either
        # can hold a word too long for the panel, so they elide it rather than widen it.
        self._sub_lbl = ThemedWrappingElidedLabel("", color="text_muted")
        self._desc_lbl = ThemedWrappingElidedLabel("", color="text_muted")
        self._status_lbl = ThemedTextLabel("", color="warning")
        self._status_lbl.setWordWrap(True)
        self._initial_combo = QComboBox()
        self._recovery_combo = QComboBox()
        self._initial_combo.setToolTip("Start the bot at this entry port")
        self._recovery_combo.setToolTip("Resume here after a crashed client reconnects")

        self._fold_bar = PageFoldBar(self._set_descriptions_visible)
        # Given its real store by each show_call: the surface is the definition shown.
        self._folds = CardFolds(self._fold_bar.heading_actions, store=_NoFolds())
        self._inputs_note = ThemedTextLabel("This subgraph has no inputs.", color="text_muted")
        self._cards_box = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_box)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(_CARD_SPACING_PX)

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
        layout.addWidget(self._label_edit)
        layout.addWidget(self._sub_lbl)
        layout.addWidget(self._desc_lbl)
        layout.addWidget(self._status_lbl)
        layout.addSpacing(4)
        layout.addWidget(self._fold_bar)
        layout.addWidget(self._inputs_note)
        layout.addWidget(self._cards_box)
        layout.addWidget(self._combat_heading)
        layout.addWidget(self._combat_note)
        layout.addWidget(actions)
        layout.addStretch(1)

        self._label_edit.textChanged.connect(self._on_label_changed)
        self._label_edit.editingFinished.connect(self._on_label_editing_finished)
        self._initial_combo.currentIndexChanged.connect(lambda _i: self._on_role_chosen("initial"))
        self._recovery_combo.currentIndexChanged.connect(lambda _i: self._on_role_chosen("recovery_state"))
        self._open_btn.clicked.connect(lambda: self._region and self.open_definition_requested.emit(self._region.id))
        self._detach_btn.clicked.connect(lambda: self._region and self.detach_requested.emit(self._region.id))

    # -- wiring --------------------------------------------------------

    def set_clients_getter(self, fn: Callable[[], list]) -> None:
        self._get_clients = fn

    def set_zone_suggestions_getter(self, fn: Callable[[], list[str]]) -> None:
        self._get_zones = fn

    def set_subgraph_library(self, library) -> None:
        """Where an input linked into a nested instance finds that instance's definition."""
        self._library = library

    @property
    def region(self) -> RegionModel | None:
        return self._region

    def form(self, panel_id: str | None = None):
        """The form for the top-level inputs (``None``) or for one panel, by id."""
        return self._forms.get(panel_id)

    def card(self, group_id: str) -> OptionCard | None:
        """One card by group: ``"inputs"``, an input group's panel id, or ``"role"``."""
        return self._cards.get(group_id)

    def to_fill(self, group_id: str) -> int:
        """How many required fields *group_id*'s card still needs, as its band says."""
        count = self._counts.get(group_id)
        return count.count if count is not None else 0

    def to_fill_label(self, group_id: str) -> ToFillLabel | None:
        return self._counts.get(group_id)

    def record_status(self, panel_id: str) -> QLabel | None:
        """The line at the top of a recordable node's card."""
        return self._record_status.get(panel_id)

    @property
    def folds(self) -> CardFolds:
        return self._folds

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

    @property
    def meta_text(self) -> str:
        """The gray line under the title: what this node is, and where it comes from."""
        return self._sub_lbl.text()

    def set_fill_ins(self, fill_ins, *, draft: bool) -> None:
        """The setup fill-ins of the open document (``EditScene.fill_ins``), read at the next ``show_call``.

        Keyed by the member an input lands on. In a bot each is marked required with
        "Fill in: <step text>"; in a template *draft* it wears a "Setup step N" pill.
        """
        self._fill_ins = dict(fill_ins)
        self._fill_ins_draft = draft

    def _fill_marks(self, pairs) -> dict[str, dict[str, str]]:
        """``load_schema``'s fill-in keywords for form keys, given ``(form key, state id, param)`` triples."""
        found = {k: self._fill_ins[(sid, param)] for k, sid, param in pairs if (sid, param) in self._fill_ins}
        if self._fill_ins_draft:
            return {"step_chips": {k: f"Setup step {f.number}" for k, f in found.items()}}
        return {"fill_hints": {k: f.step.text for k, f in found.items()}}

    def _input_marks(self, inputs) -> dict[str, dict[str, str]]:
        assert self._region is not None and self._defn is not None
        return self._fill_marks(
            (i.name, sid, param)
            for i in inputs
            for sid, param in input_targets(self._defn, i, self._library, self._region.id)
        )

    def _link_marks(self, keys, prefix: str = "") -> dict:
        """``load_schema``'s link keywords: in a definition document, this node's inputs can be linked outward.

        *keys* are a form's own keys, and *prefix* what turns one into the key this
        node's inputs are stored under: nothing for a named input, ``<member>.`` for a
        promoted node's param. A row linked to one of the document's own inputs wears
        that input's name as a chip; every other row offers Link on hover, as a node's
        param does there.
        """
        meta = self._config.definition if self._config is not None else None
        if meta is None or self._region is None:
            return {}
        names = {prefix + k: k for k in keys}
        links = {
            names[b.param]: outer.label or outer.name
            for outer in meta.inputs
            for b in outer.bindings
            if b.region == self._region.id and b.param in names
        }
        return {"linkable": True, "links": links}

    @staticmethod
    def _owned_elsewhere(schema, link_marks: dict) -> list:
        """*schema* with each linked row no longer required: the input it is linked to asks for the value.

        Left blank here, a linked row is filled by whoever uses the definition around
        it, so it is not a gap in this node -- and must not count toward its card's
        "to fill", which would send the author looking for something to fill in.
        """
        linked = link_marks.get("links", {})
        return [replace(f, required=False) if f.key in linked else f for f in schema]

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
            name = defn.name if defn is not None else source.subgraph_id
            # The name once: the title is the label, and the line under it names the
            # definition only when the label no longer does.
            parts = ["Subgraph"] if region.label.strip() == name.strip() else [f"Subgraph · {name}"]
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
            self._label_edit.setPlaceholderText(name)

            while self._cards_layout.count():
                item = self._cards_layout.takeAt(0)
                if item.widget():
                    discard_widget(item.widget())
            self._forms.clear()
            self._cards.clear()
            self._counts.clear()
            self._card_of_form.clear()
            self._initial_values.clear()
            self._record_status.clear()
            self._record_hint.clear()
            self._recorded_nodes.clear()
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
                links = self._link_marks(i.name for i in top)
                form.load_schema(
                    self._owned_elsewhere([input_param_field(defn, i, self._library) for i in top], links),
                    chosen,
                    resettable={i.name for i in top} & set(chosen),
                    **self._input_marks(top),
                    **links,
                )
                self._add_form(None, form)
                self._add_card(param_cards.INPUTS, "Inputs", form, None)
            for panel in panels:
                if panel.kind == "inputs":
                    members = [i for i in inputs if i.panel == panel.id]
                    form = make_form()
                    links = self._link_marks(i.name for i in members)
                    form.load_schema(
                        self._owned_elsewhere([input_param_field(defn, i, self._library) for i in members], links),
                        chosen,
                        resettable={i.name for i in members} & set(chosen),
                        **self._input_marks(members),
                        **links,
                    )
                    self._add_form(panel.id, form)
                    self._add_card(panel.id, panel.label or panel.id, form, panel.id)
                else:
                    self._node_panel(panel, make_form)
            self._add_role_card()
            self._inputs_note.setVisible(not self._forms)
            self._refresh_cards()
            self._apply_folds()

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

    def _add_form(self, key: str | None, form, *, link_prefix: str = "") -> None:
        self._forms[key] = form
        self._initial_values[key] = form.get_params()
        form.changed.connect(lambda k=key: self._on_form_changed(k))
        form.required_shown_changed.connect(self._refresh_cards)
        form.reset_requested.connect(lambda field_key, k=key: self.reset_value(k, field_key))
        # Reported by the key this node's inputs store the value under, so the canvas
        # need not know which kind of form the row was on. See _link_marks.
        form.link_requested.connect(
            lambda name, p=link_prefix: self._region and self.link_input_requested.emit(self._region.id, p + name)
        )
        form.unlink_requested.connect(
            lambda name, p=link_prefix: self._region and self.unlink_input_requested.emit(self._region.id, p + name)
        )

    def _add_card(self, group_id: str, title: str, body: QWidget, form_key: str | None) -> OptionCard:
        card = OptionCard(group_id, title)
        self._cards_layout.addWidget(card)
        count = ToFillLabel()
        card.add_header_widget(count)
        self._counts[group_id] = count
        card.body_layout.addWidget(body)
        self._cards[group_id] = card
        self._card_of_form[form_key] = group_id
        return card

    def _node_panel(self, panel: SubgraphPanel, make_form) -> None:
        """A member exposed whole: its own fields, keyed by its own keys, valued from ``source.inputs``."""
        assert self._region is not None and self._config is not None and self._region.source is not None
        member = member_id(self._region.id, panel.member or "")
        state = next((s for s in self._config.states if s.id == member), None)
        if state is None:
            return
        schema = NODE_PARAM_SCHEMAS.get(state.node_type, [])
        # What the member runs with is what the form shows: its composed params,
        # which already carry any value this instance set.
        form = make_form()
        if self._defn is not None:
            schema = [
                replace(f, type=t)
                if (t := promoted_param_type(self._defn, panel, f.key, NODE_PARAM_SCHEMAS)) not in (None, f.type)
                else f
                for f in schema
            ]
        prefix = exposed_key(panel.member or "", "")
        changed = {
            k[len(prefix) :] for k, v in self._region.source.inputs.items() if k.startswith(prefix) and v is not None
        }
        marks = self._fill_marks((f.key, state.id, f.key) for f in schema)
        links = self._link_marks((f.key for f in schema), prefix)
        form.load_schema(self._owned_elsewhere(schema, links), dict(state.params), resettable=changed, **marks, **links)
        self._add_form(panel.id, form, link_prefix=prefix)
        card = self._add_card(panel.id, state.label or panel.member or panel.id, form, panel.id)
        if state.node_type in recording.RECORDABLE_NODES:
            # On the band, so it can be pressed with the card folded, and so it stays
            # easy to find among the rows rather than becoming one of them (#142).
            record = RecordButton(style=ActionStyle.SECONDARY, get_clients=lambda: self._get_clients())
            record.setToolTip("Record this node's values from the game, as on the node itself")
            record.clicked.connect(lambda _=False, p=panel.id, nt=state.node_type, b=record: self._record(p, nt, b))
            card.add_header_widget(record, gap_before=2)
            status = record_line()
            card.body_layout.insertWidget(0, status)
            self._record_status[panel.id] = status
            self._recorded_nodes[panel.id] = state.node_type
            self._record_hint[panel.id] = lambda f=form, nt=state.node_type: recording.recording_hint(
                nt, f.schema, f.get_params()
            )
            status.setText(self._record_hint[panel.id]())

    def _add_role_card(self) -> None:
        """Both roles, chosen by entry port, in a card of their own: folded unless the node holds one."""
        box = QWidget()
        form = QFormLayout(box)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Initial", self._initial_combo)
        form.addRow("Recovery", self._recovery_combo)
        card = OptionCard(param_cards.ROLE, "Role")
        self._cards_layout.addWidget(card)
        card.body_layout.addWidget(box)
        self._cards[param_cards.ROLE] = card
        self._fill_role_combos()

    def _holds_role(self) -> tuple[bool, bool]:
        assert self._region is not None and self._config is not None
        members = set(self._region.members)
        return self._config.initial in members, self._config.recovery_state in members

    def _apply_folds(self) -> None:
        """Fold the cards as the reader left them, or by what they still need."""
        assert self._region is not None and self._region.source is not None
        groups = [g for g in self._cards if g != param_cards.ROLE]
        incomplete = [g for g in groups if self.to_fill(g)]
        initial, recovery = self._holds_role()
        defaults = param_cards.call_page_fold_defaults(groups, incomplete=incomplete, holds_role=initial or recovery)
        self._folds.set_store(
            UiStateFoldStore(param_cards.call_page_surface(self._region.source.subgraph_id), defaults)
        )
        self._folds.set_cards(dict(self._cards))

    def _record(self, panel_id: str, node_type: str, button: RecordButton) -> None:
        """Run the node's own recorder against its panel's form -- the recorder needs no changes."""
        if recording.is_recording():
            recording.cancel_active_recording()
            button.set_label(IDLE_TEXT)
            self._refresh_record_hint(panel_id)
            return
        status = self._record_status.get(panel_id)
        clients = self._get_clients()
        if not clients:
            if status is not None:
                status.setText("No player connected.")
            return
        pick_player(button, len(clients), "Record", lambda i: self._record_from(panel_id, node_type, button, i))

    def _record_from(self, panel_id: str, node_type: str, button: RecordButton, index: int) -> None:
        clients = self._get_clients()
        if index >= len(clients):
            return
        status = self._record_status.get(panel_id)
        form = self._forms.get(panel_id)
        card = self._cards.get(panel_id)
        if card is not None and card.is_collapsed:
            # Opened to watch the fields fill, as before; not a fold the reader chose,
            # so nothing is remembered for it.
            card.set_collapsed(False)

        def report(text: str, p=panel_id, n=index + 1) -> None:
            line = self._record_status.get(p)
            if line is None:
                return
            if text:
                line.setText(f"Player {n} · {text}")
            else:
                self._refresh_record_hint(p)

        asyncio.ensure_future(
            recording.run_recording(
                node_type,
                clients[index],
                form,
                report if status is not None else (lambda _t: None),
                button.set_label,
                lambda _label: None,  # the member's label belongs to the definition
                player=index + 1,
            )
        )

    def _refresh_record_hint(self, panel_id: str) -> None:
        hint = self._record_hint.get(panel_id)
        line = self._record_status.get(panel_id)
        if hint is not None and line is not None:
            line.setText(hint())

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
        card = self._cards.get(param_cards.ROLE)
        if card is not None:
            initial, recovery = self._holds_role()
            card.set_summary(param_cards.role_summary(initial=initial, recovery=recovery))

    def refresh_roles(self) -> None:
        if self._region is None or self._config is None:
            return
        self._suppress = True
        try:
            self._fill_role_combos()
        finally:
            self._suppress = False

    def _refresh_cards(self) -> None:
        """Every card's count, stripe and summary, from the values its form holds now."""
        assert self._region is None or self._region.source is not None
        for form_key, group in self._card_of_form.items():
            form = self._forms.get(form_key)
            card = self._cards.get(group)
            count = self._counts.get(group)
            if form is None or card is None or count is None:
                continue
            count.set_count(form.to_fill(), red=form.required_errors_shown())
            params = form.get_params()
            node_type = self._recorded_nodes.get(group)
            if node_type is not None:
                card.set_summary(param_cards.recording_summary(form.schema, params, recording.recorded_keys(node_type)))
            else:
                card.set_summary(param_cards.params_summary(form.schema, params))
            card.set_marked(self._has_own_value(form_key))

    def _has_own_value(self, form_key: str | None) -> bool:
        """Whether this node set a value on *form_key*'s card: the stripe's meaning, as on Option Values."""
        if self._region is None or self._region.source is None:
            return False
        panel = next((p for p in (self._defn.panels if self._defn else []) if p.id == form_key), None)
        form = self._forms.get(form_key)
        if form is None:
            return False
        keys = {f.key for f in form.schema}
        prefix = exposed_key(panel.member or "", "") if panel is not None and panel.kind == "node" else ""
        return any(
            v is not None and k.startswith(prefix) and k[len(prefix) :] in keys
            for k, v in self._region.source.inputs.items()
        )

    def _set_descriptions_visible(self, shown: bool) -> None:
        from bartlebot.gui.canvas.param_panel import write_show_descriptions  # noqa: PLC0415

        write_show_descriptions(shown)
        for form in self._forms.values():
            form.set_descriptions_visible(shown)

    def show_required_errors(self) -> None:
        """Draw every empty required field red: a save was refused for them."""
        for form in self._forms.values():
            form.show_required_errors()

    # -- edits ---------------------------------------------------------

    def _on_label_changed(self, text: str) -> None:
        if self._suppress or self._region is None or not text.strip():
            return
        self.edit_starting.emit("Rename subgraph")
        self._region.label = text.strip()
        self.changed.emit()

    def _on_label_editing_finished(self) -> None:
        """An emptied label goes back to the definition's name, the placeholder it showed.

        Empty text is never written, so without this the region would keep whatever
        the label was one keystroke before it emptied.
        """
        if not self._suppress and self._region is not None and not self._label_edit.text().strip():
            self._label_edit.setText(self._label_edit.placeholderText())

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
        self._refresh_cards()
        if key in self._record_hint and not recording.is_recording():
            self._refresh_record_hint(key)
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
