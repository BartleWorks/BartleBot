"""The template page: what Properties shows in Edit mode with nothing selected, for anything a template is behind.

Two documents get it, and it does a different job for each:

- **A bot made from a template** sees its setup steps, a tick on each one that is done
  and "3 of 5 done" over them. Clicking a step selects the node it asks about (the
  subgraph node, for an input), or opens the Drops tab for the drop list. Nothing is
  edited here: a fill-in is a value in the bot, filled on its node's own page.
- **A template draft** edits what a bot made from it will see: the blurb the New Bot
  card shows, and the steps. A step is its text and its targets, and every target is a
  fill-in (:mod:`bartlebot.setup_steps`), so *Add target…* is how a param becomes one.

Progress is worked out from the bot each time and never stored, so the page is given
the statuses rather than keeping any.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from bartlebot import setup_steps
from bartlebot.bot.schema import PORT_NODE_TYPES, BotConfig, SetupStep, SetupTarget
from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.menu_utils import popup_menu
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin, set_style_sheet
from bartlebot.gui.widgets.discard import discard_widget
from bartlebot.gui.widgets.icon_button import IconButton, IconTint
from bartlebot.gui.widgets.link_button import LinkButton
from bartlebot.gui.widgets.rule_heading import RuleHeading
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS
from bartlebot.subgraphs import exposed_key, node_panels

#: The tick's size, and so the indent a step's text hangs from.
_TICK_PX = 14


def _icon(name: str, tip: str, *, danger: bool = False) -> IconButton:
    return IconButton(name, tooltip=tip, size=20, icon_size=12, tint=IconTint.DANGER if danger else IconTint.DEFAULT)


def progress_text(statuses: Sequence[setup_steps.StepStatus]) -> str:
    """ "3 of 5 done", or that every step is."""
    done = sum(1 for s in statuses if s.done)
    if not statuses:
        return "Nothing left to set up."
    if done == len(statuses):
        return f"All {len(statuses)} done. This bot is set up."
    return f"{done} of {len(statuses)} done"


def target_choices(config: BotConfig, library=None) -> list[tuple[str, list[tuple[str, SetupTarget]]]]:
    """What *Add target…* offers, as ``(heading, [(label, target)])``: node params, subgraph inputs, the drop list.

    A node inside a subgraph node is not offered by itself -- an instance sets inputs
    and nothing else -- so its params are reached as that subgraph node's inputs.
    """
    members = {m for r in config.regions if r.source is not None for m in r.members}
    groups: list[tuple[str, list[tuple[str, SetupTarget]]]] = []
    for state in config.states:
        if state.id in members or state.node_type in PORT_NODE_TYPES:
            continue
        params = [
            (f.label or f.key, SetupTarget(state=state.id, param=f.key))
            for f in NODE_PARAM_SCHEMAS.get(state.node_type, [])
        ]
        if params:
            groups.append((f"{state.label or state.id}  ({state.id})", params))
    for region in config.regions:
        if region.source is None:
            continue
        defn = library.get(region.source.subgraph_id) if library is not None else None
        if defn is None:
            continue
        inputs = [(i.label or i.name, SetupTarget(region=region.id, input=i.name)) for i in defn.inputs]
        types = {s.id: s.node_type for s in defn.states}
        for panel in node_panels(defn):
            member = panel.member or ""
            for f in NODE_PARAM_SCHEMAS.get(types.get(member, ""), []):
                name = f"{panel.label or member} › {f.label or f.key}"
                inputs.append((name, SetupTarget(region=region.id, input=exposed_key(member, f.key))))
        if inputs:
            groups.append((f"{region.label}  ({region.id})", inputs))
    groups.append(("This bot", [("Possible drops", SetupTarget(field=setup_steps.DROPS_FIELD))]))
    return groups


class _StepRow(ThemedMixin, QWidget):
    """One step as a bot sees it: a tick and its text, clickable as a whole."""

    clicked = pyqtSignal()

    def __init__(self, text: str, done: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._done = done
        self._hovered = False
        self._tick = QLabel(self)
        self._tick.setFixedSize(_TICK_PX, _TICK_PX)
        self._text = QLabel(text, self)
        self._text.setWordWrap(True)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 3, 4, 3)
        row.setSpacing(8)
        row.addWidget(self._tick, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self._text, 1)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("SetupStepRow")
        self.setToolTip("Done" if done else "Not done yet. Click to go to it.")
        self.refresh_theme_styles()

    @property
    def done(self) -> bool:
        return self._done

    @property
    def text(self) -> str:
        return self._text.text()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)

    def apply_theme_styles(self) -> None:
        if self._done:
            self._tick.setPixmap(svg_icon("checkmark", _TICK_PX, tint=THEME.success).pixmap(_TICK_PX, _TICK_PX))
        else:
            self._tick.setText("○")
        set_style_sheet(self._tick, f"color: {THEME.text_muted};")
        set_style_sheet(self._text, f"color: {THEME.text_muted if self._done else THEME.text};")
        set_style_sheet(
            self,
            f"#SetupStepRow {{ border-radius: 4px; }}"
            f"#SetupStepRow:hover, #SetupStepRow:focus {{ background: {THEME.surface}; }}",
        )


class TemplatePage(ThemedMixin, QWidget):
    """Shows a bot's setup steps, or edits a draft's ``template_meta`` in place and reports what it changed.

    ``edit_starting`` opens a debounced undo step for a typed edit, ``structural_change``
    an immediate one for a discrete edit; ``changed`` follows either. ``go_to_requested``
    names the node to select -- a state id, or a region id for a subgraph node -- and
    ``drops_requested`` asks for the Drops tab.
    """

    edit_starting = pyqtSignal(str)
    structural_change = pyqtSignal(str)
    changed = pyqtSignal()
    go_to_requested = pyqtSignal(str)
    drops_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._statuses: list[setup_steps.StepStatus] = []
        self._suppress = False
        self._get_library: Callable[[], object | None] = lambda: None
        self._step_rows: list[_StepRow] = []

        self._title = QLabel("Setup")
        self._intro = ThemedTextLabel("", color="text_muted")
        self._intro.setWordWrap(True)
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.addWidget(self._title)
        layout.addWidget(self._intro)
        layout.addWidget(self._body)
        layout.addStretch(1)
        self.refresh_theme_styles()

    def set_library_getter(self, fn: Callable[[], object | None]) -> None:
        """Pass a callable returning the subgraph library, for a subgraph node's inputs in *Add target…*."""
        self._get_library = fn

    @property
    def config(self) -> BotConfig | None:
        return self._config

    @property
    def step_rows(self) -> list[_StepRow]:
        """The rows a bot's steps are drawn as, in order. Empty on a draft."""
        return list(self._step_rows)

    @property
    def summary(self) -> str:
        return self._intro.text()

    # -- building ------------------------------------------------------

    def show_template(self, config: BotConfig, statuses: Sequence[setup_steps.StepStatus] = ()) -> None:
        """Show *config*'s page: the step editor for a draft, the tick list (*statuses*) for a bot."""
        self._config = config
        self._statuses = list(statuses)
        self._suppress = True
        try:
            self._rebuild()
        finally:
            self._suppress = False

    def _clear(self) -> None:
        self._step_rows = []
        layout = self._body_layout
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                discard_widget(item.widget())

    def _rebuild(self) -> None:
        self._clear()
        config = self._config
        if config is None:
            return
        if config.template_meta is not None:
            self._title.setText("Template")
            self._intro.setText(
                "What a bot made from this template is asked to fill in. Every target of a step is left "
                "blank in a release, and the bot can't run until it is filled."
            )
            self._build_draft()
        else:
            self._title.setText("Setup")
            self._intro.setText(progress_text(self._statuses))
            self._build_bot()

    def _row(self, *widgets: QWidget, indent: int = 0, stretch: int | None = 0) -> QWidget:
        """A row of *widgets*; the one at index *stretch* takes the slack, or none does."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(indent, 0, 0, 0)
        h.setSpacing(4)
        for i, w in enumerate(widgets):
            h.addWidget(w, 1 if i == stretch else 0)
        if stretch is None:
            h.addStretch(1)
        return row

    # -- a bot ---------------------------------------------------------

    def _build_bot(self) -> None:
        for status in self._statuses:
            row = _StepRow(status.step.text, status.done)
            row.clicked.connect(lambda s=status: self._go_to(s))
            self._body_layout.addWidget(row)
            self._step_rows.append(row)

    def _go_to(self, status: setup_steps.StepStatus) -> None:
        """Go to what a step asks about: the first target still to do, else the first."""
        if self._config is None:
            return
        library = self._get_library()
        pending = [t for t in status.targets if not setup_steps.target_done(self._config, t, library)]
        target = (pending or list(status.targets))[0]
        if target.field is not None:
            self.drops_requested.emit()
        else:
            self.go_to_requested.emit(target.state or target.region or "")

    # -- a draft -------------------------------------------------------

    def _build_draft(self) -> None:
        meta = self._meta()
        layout = self._body_layout
        layout.addWidget(RuleHeading("Blurb"))
        blurb = QPlainTextEdit()
        blurb.setPlainText(meta.blurb)
        blurb.setFixedHeight(84)
        blurb.setToolTip("What the New Bot dialog says under the template's name. Its first paragraph is the card.")
        blurb.textChanged.connect(lambda b=blurb: self._set_blurb(b.toPlainText()))
        layout.addWidget(blurb)

        layout.addWidget(RuleHeading("Setup Steps"))
        if not meta.setup:
            layout.addWidget(ThemedTextLabel("No steps yet.", color="text_muted"))
        for i, step in enumerate(meta.setup):
            number = ThemedTextLabel(f"{i + 1}.", color="text_muted")
            text = QLineEdit(step.text)
            text.setToolTip("What the user is asked to do, as an instruction")
            text.textChanged.connect(lambda value, n=i: self._set_text(n, value))
            up = _icon("arrow_up", "Move up")
            down = _icon("arrow_down", "Move down")
            up.setEnabled(i > 0)
            down.setEnabled(i < len(meta.setup) - 1)
            up.clicked.connect(lambda _=False, n=i: self._move_step(n, -1))
            down.clicked.connect(lambda _=False, n=i: self._move_step(n, 1))
            delete = _icon("trash", "Delete this step; what it targeted stops being a fill-in", danger=True)
            delete.clicked.connect(lambda _=False, n=i: self._delete_step(n))
            layout.addWidget(self._row(number, text, up, down, delete, stretch=1))
            for j, target in enumerate(step.targets):
                exists = setup_steps.target_exists(self._config, target)
                name = setup_steps.describe_target(self._config, target)
                label = ThemedTextLabel(name if exists else f"{name} (not in the draft)", color="text_muted")
                go = LinkButton("Show")
                go.setEnabled(exists)
                go.clicked.connect(lambda _=False, t=target: self._show_target(t))
                remove = _icon("dismiss", "Remove this target: it stops being a fill-in")
                remove.clicked.connect(lambda _=False, n=i, k=j: self._remove_target(n, k))
                layout.addWidget(self._row(label, go, remove, indent=22))
            add_target = LinkButton("+ Add target…")
            add_target.clicked.connect(lambda _=False, n=i, b=add_target: self._offer_targets(n, b))
            layout.addWidget(self._row(add_target, indent=22, stretch=None))
        add = LinkButton("+ Add step")
        add.clicked.connect(self._add_step)
        layout.addWidget(add)

    def _show_target(self, target: SetupTarget) -> None:
        if target.field is not None:
            self.drops_requested.emit()
        else:
            self.go_to_requested.emit(target.state or target.region or "")

    def _offer_targets(self, index: int, anchor: QWidget) -> None:
        assert self._config is not None
        taken = {t.model_dump_json() for step in self._meta().setup for t in step.targets}
        menu = popup_menu(anchor)
        for heading, choices in target_choices(self._config, self._get_library()):
            sub = menu.addMenu(heading)
            for label, target in choices:
                act = sub.addAction(label)
                # One step per fill-in: a param two steps asked for would tick both.
                act.setEnabled(target.model_dump_json() not in taken)
                act.triggered.connect(lambda _=False, n=index, t=target: self.add_target(n, t))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        menu.deleteLater()

    # -- edits ---------------------------------------------------------

    def _meta(self):
        assert self._config is not None and self._config.template_meta is not None
        return self._config.template_meta

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
            self.show_template(self._config)

    def _set_blurb(self, text: str) -> None:
        self._typed("Edit blurb", lambda: setattr(self._meta(), "blurb", text.strip()))

    def _set_text(self, index: int, text: str) -> None:
        self._typed("Edit setup step", lambda: setattr(self._meta().setup[index], "text", text.strip()))

    def _add_step(self) -> None:
        self._discrete("Add setup step", lambda: self._meta().setup.append(SetupStep(text="")))

    def _delete_step(self, index: int) -> None:
        self._discrete("Delete setup step", lambda: self._meta().setup.pop(index))

    def _move_step(self, index: int, step: int) -> None:
        def apply() -> None:
            steps = self._meta().setup
            other = index + step
            if 0 <= other < len(steps):
                steps[index], steps[other] = steps[other], steps[index]

        self._discrete("Reorder setup steps", apply)

    def add_target(self, index: int, target: SetupTarget) -> None:
        """Make *target* a fill-in of step *index* -- what a choice in *Add target…* does."""
        self._discrete("Add setup target", lambda: self._meta().setup[index].targets.append(target))

    def _remove_target(self, index: int, target_index: int) -> None:
        self._discrete("Remove setup target", lambda: self._meta().setup[index].targets.pop(target_index))

    def apply_theme_styles(self) -> None:
        font = self._title.font()
        font.setBold(True)
        font.setPixelSize(THEME.font_heading_px)
        self._title.setFont(font)
        set_style_sheet(self._title, f"color: {THEME.text};")
