"""The signature panel: a definition document's page when nothing is selected.

Everything a call node shows is authored here -- the Entry and Exit ports, the
inputs and their defaults, the panels they are grouped under, and the nodes exposed
whole. A port *is* its node on the canvas, so adding one places a node and deleting
one deletes the node; its name is the node's label, so renaming changes nothing
structural. An input is added from a node's own page (a param's *Link* button); this page
renames, re-describes, sets the default, reorders, groups and deletes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import SUBGRAPH_ENTRY, SUBGRAPH_EXIT, BotConfig, SubgraphInput, SubgraphPanel
from bartlebot.gui.menu_utils import popup_menu
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin, set_style_sheet
from bartlebot.gui.widgets.icon_button import IconButton, IconTint
from bartlebot.gui.widgets.link_button import LinkButton
from bartlebot.gui.widgets.rule_heading import RuleHeading
from bartlebot.gui.widgets.themed_label import ThemedTextLabel

#: The group dropdown's entry for an input listed in no group.
_NO_GROUP = "None"


def _icon(name: str, tip: str, *, danger: bool = False) -> IconButton:
    return IconButton(name, tooltip=tip, size=20, icon_size=12, tint=IconTint.DANGER if danger else IconTint.DEFAULT)


class SignaturePage(ThemedMixin, QWidget):
    """Edits ``config.definition`` (and the port nodes' labels and order) in place.

    ``edit_starting`` opens a debounced undo step for a typed edit, ``structural_change``
    an immediate one for a discrete edit; ``changed`` follows either. Adding a port is
    the canvas's to do (``add_port_requested``), as is deleting one
    (``delete_port_requested``), since each is a node on the canvas.
    """

    edit_starting = pyqtSignal(str)
    structural_change = pyqtSignal(str)
    changed = pyqtSignal()
    add_port_requested = pyqtSignal(str)  # SUBGRAPH_ENTRY / SUBGRAPH_EXIT
    delete_port_requested = pyqtSignal(str)  # state id
    select_state_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._suppress = False
        self._get_clients: Callable[[], list] = lambda: []
        self._get_zones: Callable[[], list[str]] = lambda: []
        self._headings: list[QLabel] = []

        self._title = QLabel("Signature")
        self._intro = ThemedTextLabel(
            "What every subgraph node of this subgraph shows: its ports, its inputs and how they are grouped. "
            "To add a subgraph input, hover a param on a node's page and click its link button.",
            color="text_muted",
        )
        self._intro.setWordWrap(True)
        self._description = QPlainTextEdit()
        self._description.setPlaceholderText("When to use it, and what it expects of the bot around it")
        self._description.setFixedHeight(64)

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.addWidget(self._title)
        layout.addWidget(self._intro)
        layout.addWidget(RuleHeading("Description"))
        layout.addWidget(self._description)
        layout.addWidget(self._body)
        layout.addStretch(1)
        self._description.textChanged.connect(self._on_description_changed)
        self.refresh_theme_styles()

    def set_clients_getter(self, fn: Callable[[], list]) -> None:
        self._get_clients = fn

    def set_zone_suggestions_getter(self, fn: Callable[[], list[str]]) -> None:
        self._get_zones = fn

    @property
    def config(self) -> BotConfig | None:
        return self._config

    # -- building ------------------------------------------------------

    def show_signature(self, config: BotConfig) -> None:
        self._config = config
        self._suppress = True
        try:
            if self._description.toPlainText() != (config.description or ""):
                self._description.setPlainText(config.description or "")
            self._rebuild()
        finally:
            self._suppress = False

    def _rebuild(self) -> None:
        layout = self._body_layout
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        config = self._config
        if config is None or config.definition is None:
            return
        self._port_section(SUBGRAPH_ENTRY, "Entries", "Add entry")
        self._port_section(SUBGRAPH_EXIT, "Exits", "Add exit")
        self._inputs_section()
        self._panels_section()

    def _row(self, *widgets, indent: int = 0, stretch_first: bool = True) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(indent, 0, 0, 0)
        h.setSpacing(4)
        for i, w in enumerate(widgets):
            h.addWidget(w, 1 if (i == 0 and stretch_first) else 0)
        return row

    def _port_section(self, node_type: str, heading: str, add_text: str) -> None:
        assert self._config is not None
        layout = self._body_layout
        layout.addWidget(RuleHeading(heading))
        ports = [s for s in self._config.states if s.node_type == node_type]
        for i, state in enumerate(ports):
            name = QLineEdit(state.label or "")
            name.setPlaceholderText(state.id)
            name.textChanged.connect(lambda text, s=state: self._rename_port(s.id, text))
            up = _icon("arrow_up", "Move up")
            down = _icon("arrow_down", "Move down")
            up.setEnabled(i > 0)
            down.setEnabled(i < len(ports) - 1)
            up.clicked.connect(lambda _=False, s=state.id: self._move_port(s, -1))
            down.clicked.connect(lambda _=False, s=state.id: self._move_port(s, 1))
            delete = _icon(
                "trash", f"Delete this {'entry' if node_type == SUBGRAPH_ENTRY else 'exit'} and its node", danger=True
            )
            delete.clicked.connect(lambda _=False, s=state.id: self.delete_port_requested.emit(s))
            layout.addWidget(self._row(name, up, down, delete))
        add = LinkButton(f"+ {add_text}")
        add.clicked.connect(lambda _=False, t=node_type: self.add_port_requested.emit(t))
        layout.addWidget(add)

    def _inputs_section(self) -> None:
        from bartlebot.gui.canvas.param_panel import _DynamicParamForm, input_param_field  # noqa: PLC0415

        assert self._config is not None and self._config.definition is not None
        meta = self._config.definition
        layout = self._body_layout
        layout.addWidget(RuleHeading("Subgraph Inputs"))
        if not meta.inputs:
            note = ThemedTextLabel("No inputs yet.", color="text_muted")
            layout.addWidget(note)
        labels = {s.id: (s.label or s.id) for s in self._config.states}
        inputs_panels = [p for p in meta.panels if p.kind == "inputs"]
        for i, inp in enumerate(meta.inputs):
            label = QLineEdit(inp.label or "")
            label.setPlaceholderText(inp.name)
            label.setToolTip(f"Shown on the subgraph node; the input's name is '{inp.name}'")
            label.textChanged.connect(lambda text, n=inp.name: self._set_input(n, "label", text.strip() or None))
            kind = ThemedTextLabel(inp.type, color="text_muted")
            up = _icon("arrow_up", "Move up")
            down = _icon("arrow_down", "Move down")
            up.setEnabled(i > 0)
            down.setEnabled(i < len(meta.inputs) - 1)
            up.clicked.connect(lambda _=False, n=inp.name: self._move_input(n, -1))
            down.clicked.connect(lambda _=False, n=inp.name: self._move_input(n, 1))
            delete = _icon("trash", "Delete this input; the params it set keep their own values", danger=True)
            delete.clicked.connect(lambda _=False, n=inp.name: self._delete_input(n))
            layout.addWidget(self._row(label, kind, up, down, delete))
            help_edit = QLineEdit(inp.help or "")
            help_edit.setPlaceholderText("Help, shown as the input's tooltip")
            help_edit.textChanged.connect(lambda text, n=inp.name: self._set_input(n, "help", text.strip() or None))
            layout.addWidget(self._row(help_edit, indent=16))
            group = QComboBox()
            group.addItem(_NO_GROUP, None)
            for p in inputs_panels:
                group.addItem(p.label or p.id, p.id)
            idx = group.findData(inp.panel)
            group.setCurrentIndex(idx if idx >= 0 else 0)
            group.setToolTip("The input group this input is listed under on the subgraph node")
            group.currentIndexChanged.connect(
                lambda _i, n=inp.name, c=group: self._set_input(n, "panel", c.currentData())
            )
            group_lbl = ThemedTextLabel("Input group:", color="text_muted")
            layout.addWidget(self._row(group_lbl, group, indent=16, stretch_first=False))
            default = _DynamicParamForm()
            default.set_clients_getter(self._get_clients)
            default.set_zone_suggestions_getter(self._get_zones)
            field = replace(input_param_field(self._config, inp), label="Default", required=False)
            default.load_schema([field], {inp.name: inp.default})
            default.changed.connect(lambda n=inp.name, f=default: self._set_default(n, f))
            default.setContentsMargins(10, 0, 0, 0)
            layout.addWidget(default)
            for b in inp.bindings:
                text = ThemedTextLabel(f"linked to {labels.get(b.state, b.state)} · {b.param}", color="text_muted")
                unbind = _icon("unhook", "Unlink that param from this input")
                unbind.clicked.connect(lambda _=False, n=inp.name, st=b.state, k=b.param: self._unbind(n, st, k))
                layout.addWidget(self._row(text, unbind, indent=16))

    def _panels_section(self) -> None:
        assert self._config is not None and self._config.definition is not None
        meta = self._config.definition
        layout = self._body_layout
        layout.addWidget(RuleHeading("Input Groups"))
        labels = {s.id: (s.label or s.id) for s in self._config.states}
        for i, panel in enumerate(meta.panels):
            if panel.kind == "node":
                title: QWidget = ThemedTextLabel(
                    f"⧉ {labels.get(panel.member or '', panel.member)} (promoted node)", color="text"
                )
                title.setToolTip("Every param of this node is on the subgraph node, with its Record button")
            else:
                title = QLineEdit(panel.label or "")
                title.setPlaceholderText("Group heading")
                title.textChanged.connect(lambda text, p=panel.id: self._set_panel(p, "label", text.strip() or None))
            closed = QCheckBox("Starts closed")
            closed.setChecked(panel.default_closed)
            closed.toggled.connect(lambda on, p=panel.id: self._set_panel(p, "default_closed", on))
            up = _icon("arrow_up", "Move up")
            down = _icon("arrow_down", "Move down")
            up.setEnabled(i > 0)
            down.setEnabled(i < len(meta.panels) - 1)
            up.clicked.connect(lambda _=False, p=panel.id: self._move_panel(p, -1))
            down.clicked.connect(lambda _=False, p=panel.id: self._move_panel(p, 1))
            if panel.kind == "node":
                remove = _icon("chevrons_down", "Demote this node: take its input group off the subgraph node")
            else:
                remove = _icon("trash", "Delete this group; its inputs stay, in no group", danger=True)
            remove.clicked.connect(lambda _=False, p=panel.id: self._delete_panel(p))
            layout.addWidget(self._row(title, closed, up, down, remove))
            if panel.kind == "inputs":
                self._group_members(panel)
            for key, extra in panel.bindings.items():
                for b in extra:
                    text = ThemedTextLabel(
                        f"{key} is linked to {labels.get(b.state, b.state)} · {b.param}", color="text_muted"
                    )
                    unbind = _icon("dismiss", "Stop sharing this value")
                    unbind.clicked.connect(
                        lambda _=False, p=panel.id, k=key, st=b.state, bp=b.param: self._unbind_shared(p, k, st, bp)
                    )
                    layout.addWidget(self._row(text, unbind, indent=16))
        add = LinkButton("+ Add input group")
        add.clicked.connect(self._add_panel)
        layout.addWidget(add)

    def _group_members(self, panel: SubgraphPanel) -> None:
        """The inputs in one group, each with a way out of it, and a way to add more."""
        meta = self._meta()
        layout = self._body_layout
        members = [i for i in meta.inputs if i.panel == panel.id]
        for inp in members:
            text = ThemedTextLabel(inp.label or inp.name, color="text")
            out = _icon("dismiss", "Take this input out of the group")
            out.clicked.connect(lambda _=False, n=inp.name: self._set_input(n, "panel", None))
            layout.addWidget(self._row(text, out, indent=16))
        others = [i for i in meta.inputs if i.panel != panel.id]
        add = LinkButton("+ Add input\u2026")
        add.setEnabled(bool(others))
        add.setToolTip(
            "Move an input into this group"
            if others
            else "Every input is in this group already; link a param to add one"
        )
        add.clicked.connect(lambda _=False, p=panel.id, b=add: self._offer_inputs(p, b))
        layout.addWidget(self._row(add, indent=16, stretch_first=False))

    def _offer_inputs(self, panel_id: str, anchor: QWidget) -> None:
        groups = {p.id: (p.label or p.id) for p in self._meta().panels}
        menu = popup_menu(anchor)
        for inp in self._meta().inputs:
            if inp.panel == panel_id:
                continue
            where = f"  ({groups[inp.panel]})" if inp.panel in groups else ""
            act = menu.addAction(f"{inp.label or inp.name}{where}")
            act.triggered.connect(lambda _=False, n=inp.name: self._set_input(n, "panel", panel_id))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        menu.deleteLater()

    def move_input_to_group(self, name: str, panel_id: str | None) -> None:
        """Put an input in a group, or in none -- what the group's menu and dismiss do."""
        self._set_input(name, "panel", panel_id)

    # -- edits ---------------------------------------------------------

    def _meta(self):
        assert self._config is not None and self._config.definition is not None
        return self._config.definition

    def _typed(self, desc: str, apply) -> None:
        """A typed edit: debounced undo, applied, reported -- the page itself is left alone."""
        if self._suppress:
            return
        self.edit_starting.emit(desc)
        apply()
        self.changed.emit()

    def _discrete(self, desc: str, apply) -> None:
        """A discrete edit: its own undo step, applied, reported, and the page rebuilt."""
        if self._suppress:
            return
        self.structural_change.emit(desc)
        apply()
        self.changed.emit()
        if self._config is not None:
            self.show_signature(self._config)

    def _on_description_changed(self) -> None:
        text = self._description.toPlainText().strip()
        self._typed("Edit description", lambda: setattr(self._config, "description", text or None))

    def _rename_port(self, state_id: str, text: str) -> None:
        def apply() -> None:
            state = next(s for s in self._config.states if s.id == state_id)
            state.label = text.strip() or None

        self._typed("Rename port", apply)

    def _move_port(self, state_id: str, step: int) -> None:
        def apply() -> None:
            states = self._config.states
            kind = next(s.node_type for s in states if s.id == state_id)
            same = [i for i, s in enumerate(states) if s.node_type == kind]
            pos = next(n for n, i in enumerate(same) if states[i].id == state_id)
            other = pos + step
            if 0 <= other < len(same):
                a, b = same[pos], same[other]
                states[a], states[b] = states[b], states[a]

        self._discrete("Reorder ports", apply)

    def _find_input(self, name: str) -> SubgraphInput:
        return next(i for i in self._meta().inputs if i.name == name)

    def _set_input(self, name: str, attr: str, value) -> None:
        if attr == "panel":
            self._discrete("Move input", lambda: setattr(self._find_input(name), attr, value))
        else:
            self._typed("Edit input", lambda: setattr(self._find_input(name), attr, value))

    def _set_default(self, name: str, form) -> None:
        params = form.get_params()
        self._typed("Edit input default", lambda: setattr(self._find_input(name), "default", params.get(name)))

    def _move_input(self, name: str, step: int) -> None:
        def apply() -> None:
            inputs = self._meta().inputs
            i = next(n for n, inp in enumerate(inputs) if inp.name == name)
            j = i + step
            if 0 <= j < len(inputs):
                inputs[i], inputs[j] = inputs[j], inputs[i]

        self._discrete("Reorder inputs", apply)

    def _delete_input(self, name: str) -> None:
        def apply() -> None:
            meta = self._meta()
            meta.inputs = [i for i in meta.inputs if i.name != name]

        self._discrete("Delete input", apply)

    def _unbind(self, name: str, state: str, key: str) -> None:
        def apply() -> None:
            inp = self._find_input(name)
            inp.bindings = [b for b in inp.bindings if not (b.state == state and b.param == key)]

        self._discrete("Unbind param", apply)

    def _find_panel(self, panel_id: str) -> SubgraphPanel:
        return next(p for p in self._meta().panels if p.id == panel_id)

    def _set_panel(self, panel_id: str, attr: str, value) -> None:
        self._typed("Edit input group", lambda: setattr(self._find_panel(panel_id), attr, value))

    def _move_panel(self, panel_id: str, step: int) -> None:
        def apply() -> None:
            panels = self._meta().panels
            i = next(n for n, p in enumerate(panels) if p.id == panel_id)
            j = i + step
            if 0 <= j < len(panels):
                panels[i], panels[j] = panels[j], panels[i]

        self._discrete("Reorder input groups", apply)

    def _delete_panel(self, panel_id: str) -> None:
        def apply() -> None:
            meta = self._meta()
            meta.panels = [p for p in meta.panels if p.id != panel_id]
            for inp in meta.inputs:
                if inp.panel == panel_id:
                    inp.panel = None

        self._discrete("Delete input group", apply)

    def _add_panel(self) -> None:
        def apply() -> None:
            meta = self._meta()
            taken = {p.id for p in meta.panels}
            n = 1
            while f"panel_{n}" in taken:
                n += 1
            meta.panels.append(SubgraphPanel(id=f"panel_{n}", kind="inputs", label=f"Group {n}"))

        self._discrete("Add input group", apply)

    def _unbind_shared(self, panel_id: str, key: str, state: str, param: str) -> None:
        def apply() -> None:
            panel = self._find_panel(panel_id)
            kept = [b for b in panel.bindings.get(key, []) if not (b.state == state and b.param == param)]
            if kept:
                panel.bindings[key] = kept
            else:
                panel.bindings.pop(key, None)

        self._discrete("Stop sharing value", apply)

    def apply_theme_styles(self) -> None:
        font = self._title.font()
        font.setBold(True)
        font.setPixelSize(THEME.font_heading_px)
        self._title.setFont(font)
        set_style_sheet(self._title, f"color: {THEME.text};")
