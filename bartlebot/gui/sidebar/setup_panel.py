"""The Setup tab: a template's setup steps, in the right sidebar, beside the node they point at.

A list that stays put while the inspector follows it -- Xcode's Issue navigator, VS
Code's Problems panel -- with an onboarding checklist's progress and wording. It was a
page of Properties at first, which is the one arrangement that cannot work: a step is
done on its node's page, and showing that page took the list away, so every step cost a
trip back. Over here the list and the node's page are on screen together, and nothing a
step asks for is editable in two places.

Two documents get the tab, and it does a different job for each:

**A bot made from a template** sees its steps as cards: a tick, the step's text and a
line saying how far along it is. Clicking a card selects the node it asks about (the
subgraph node, for an input), where Properties marks the rows to fill. Nothing is edited
on the card. The one exception is the drop list, which has no node: its card lists the
bot's fights, each with its own wiki import.

**A template draft** edits what a bot made from it will see: the blurb on the New Bot
card, and the steps. A step is a card in an ordered list, so it is the same card every
other ordered list here uses -- ``CardFrame`` with a grip in the gutter, a cross on its
face, and a dashed Add card ending the list. Every target of a step is a fill-in
(:mod:`bartlebot.setup_steps`), so *Add target…* is how a param becomes one; a node's own
page offers the same from each param row.

The panel holds no progress of its own: it is handed the statuses each time something
may have moved (``show_document``), and rebuilds only when what it shows has changed, so
a refresh never takes a field out from under the hand typing in it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bartlebot import setup_steps
from bartlebot.bot.schema import PORT_NODE_TYPES, BotConfig, SetupStep, SetupTarget
from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.menu_utils import popup_menu
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin, set_style_sheet
from bartlebot.gui.widgets.action_button import ActionButton
from bartlebot.gui.widgets.add_card import AddCard
from bartlebot.gui.widgets.card_frame import CardFrame, CardMode, CardVerb
from bartlebot.gui.widgets.discard import discard_widget
from bartlebot.gui.widgets.drag_reorder import DragReorder
from bartlebot.gui.widgets.elided_label import WrappingElidedLabel
from bartlebot.gui.widgets.link_button import LinkButton
from bartlebot.gui.widgets.rule_heading import RuleHeading
from bartlebot.gui.widgets.scroll_gutter import reserve_scrollbar_gutter
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS
from bartlebot.subgraphs import exposed_key, node_panels
from bartlebot.wiki_pages import WIKI_PAGE_PARAM, page_title

#: The tick's size.
_TICK_PX = 14

#: The draft list's terminator, in the Add card every other card list ends with.
ADD_STEP_TEXT = "Add step"
#: The cross on a draft's step card, and on each of its targets.
REMOVE_STEP_VERB = "Remove step"
#: What a new step's text field asks for.
_STEP_PLACEHOLDER = "What the user is asked to do"

FIGHT_NODE_TYPE = "handle_combat"


# ---------------------------------------------------------------------------
# What the cards say -- pure, so it is tested without a QApplication
# ---------------------------------------------------------------------------


def progress_text(statuses: Sequence[setup_steps.StepStatus]) -> str:
    """ "3 of 5 done", or that every step is."""
    done = sum(1 for s in statuses if s.done)
    if not statuses:
        return "Nothing left to set up."
    if done == len(statuses):
        return f"All {len(statuses)} done. This bot is set up."
    return f"{done} of {len(statuses)} done"


def step_summary(config: BotConfig, status: setup_steps.StepStatus, library=None) -> str:
    """A step card's second line: what is still to do, in one phrase."""
    targets = status.targets
    if status.done:
        return "Done"
    if all(t.field is not None for t in targets):
        return "No drops yet"
    filled = sum(1 for t in targets if setup_steps.target_filled(config, t, library))
    invalid = [t for t in targets if setup_steps.target_filled(config, t, library) and t.field is None]
    invalid = [t for t in invalid if not setup_steps.target_done(config, t, library)]
    if invalid:
        return f"{setup_steps.describe_target(config, invalid[0])} is not a destination on the zone map"
    if len(targets) == 1:
        return "Not filled in yet"
    return f"{filled} of {len(targets)} filled in"


@dataclass(frozen=True)
class Fight:
    """One fight node, as the drops step lists it."""

    state_id: str
    label: str
    #: The wiki page its drops were imported from, as a title; "" before the first import.
    page: str


def fights_of(config: BotConfig) -> list[Fight]:
    """Every fight node in *config*, in graph order: what the drop list is imported from."""
    return [
        Fight(s.id, s.label or s.id, page_title(str(s.params.get(WIKI_PAGE_PARAM) or "")) or "")
        for s in config.states
        if s.node_type == FIGHT_NODE_TYPE
    ]


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
            groups.append((state.label or state.id, params))
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
            groups.append((region.label, inputs))
    groups.append(("This bot", [("Possible drops", SetupTarget(field=setup_steps.DROPS_FIELD))]))
    return groups


# ---------------------------------------------------------------------------
# A bot's step
# ---------------------------------------------------------------------------


class StepCard(CardFrame):
    """One setup step as a bot sees it: a tick, the step, and how far along it is.

    ``CardMode.MANAGE``: the whole card is the verb, and the verb is *go to it*. No grip
    and no gutter, since the user does not order the steps, and no face buttons, since
    there is nothing else to do to one. A drops step unfolds its fights underneath, each
    with its own import, because the drop list has no node to go to.
    """

    FRAME_NAME = "StepCard"

    #: A fight's Import button, by the fight's state id.
    import_requested = pyqtSignal(str)

    def __init__(
        self, text: str, summary: str, done: bool, fights: Sequence[Fight] | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent, mode=CardMode.MANAGE)
        self._done = done
        self._import_buttons: dict[str, ActionButton] = {}
        inner = self._build_frame("setup steps", gutter=False)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self._tick = QLabel(self._body)
        self._tick.setFixedSize(_TICK_PX, _TICK_PX)
        head.addWidget(self._tick, 0, Qt.AlignmentFlag.AlignTop)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        head.addLayout(text_col, 1)
        inner.addLayout(head)

        self._title = QLabel(text, self._body)
        self._title.setWordWrap(True)
        self._summary = WrappingElidedLabel(summary, parent=self._body)
        text_col.addWidget(self._title)
        text_col.addWidget(self._summary)

        if fights is not None:
            self._build_fights(inner, fights)
        self.setToolTip("Done" if done else "Click to go to it")
        self.refresh_theme_styles()

    def _build_fights(self, inner: QVBoxLayout, fights: Sequence[Fight]) -> None:
        inner.addSpacing(4)
        if not fights:
            none = ThemedTextLabel(
                "This bot has no fights yet. Add one, then import its drops here.", parent=self._body
            )
            none.setWordWrap(True)
            inner.addWidget(none)
        for fight in fights:
            row = QHBoxLayout()
            row.setContentsMargins(_TICK_PX + 8, 0, 0, 0)
            row.setSpacing(6)
            inner.addLayout(row)
            name = WrappingElidedLabel(fight.label, parent=self._body)
            name.setToolTip(f"Imported from {fight.page}" if fight.page else "Nothing imported for this fight yet")
            button = ActionButton("download", "Import…", fill=False, parent=self._body)
            button.setToolTip(f"Import the drops of '{fight.label}' from its Wizard101 Central page")
            button.clicked.connect(lambda _=False, sid=fight.state_id: self.import_requested.emit(sid))
            self._import_buttons[fight.state_id] = button
            row.addWidget(name, 1)
            row.addWidget(button, 0)

    @property
    def done(self) -> bool:
        return self._done

    @property
    def text(self) -> str:
        return self._title.text()

    @property
    def summary(self) -> str:
        return self._summary.text()

    @property
    def import_buttons(self) -> dict[str, ActionButton]:
        """A drops step's Import buttons, by fight state id. Empty on any other step."""
        return dict(self._import_buttons)

    def _restyle_content(self) -> None:
        if self._done:
            self._tick.setPixmap(svg_icon("checkmark", _TICK_PX, tint=THEME.success).pixmap(_TICK_PX, _TICK_PX))
        else:
            self._tick.setText("○")
        set_style_sheet(self._tick, f"color: {THEME.text_muted}; background: transparent;")
        title = THEME.text_muted if self._done else THEME.text
        set_style_sheet(self._title, f"color: {title}; font-weight: 600; background: transparent;")
        set_style_sheet(
            self._summary,
            f"color: {THEME.text_muted}; font-size: {THEME.font_subtext_px}px; background: transparent;",
        )


# ---------------------------------------------------------------------------
# A draft's step
# ---------------------------------------------------------------------------


class DraftStepCard(CardFrame):
    """One setup step as its template's author edits it: its text, and the fill-ins it asks for.

    ``CardMode.MEMBER``: the body is not a button, because it holds the step's text
    field. Its verbs are the grip, which reorders, and the cross, which removes it.
    """

    FRAME_NAME = "DraftStepCard"

    text_edited = pyqtSignal(str)
    remove_requested = pyqtSignal()
    add_target_requested = pyqtSignal(object)  # the widget to hang the menu from
    remove_target_requested = pyqtSignal(int)
    show_target_requested = pyqtSignal(int)

    def __init__(
        self, number: int, text: str, targets: Sequence[tuple[str, bool]], parent: QWidget | None = None
    ) -> None:
        """*targets* is ``(what it is called, whether the draft still has it)`` per target, in order."""
        super().__init__(parent, mode=CardMode.MEMBER)
        self._target_labels: list[QLabel] = []
        inner = self._build_frame("setup steps")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self._number = QLabel(f"{number}.", self._body)
        header.addWidget(self._number, 0)
        self._edit = QLineEdit(text, self._body)
        self._edit.setPlaceholderText(_STEP_PLACEHOLDER)
        self._edit.setToolTip("What the user is asked to do, as an instruction")
        self._edit.textChanged.connect(self.text_edited)
        header.addWidget(self._edit, 1)
        self._add_face_verbs(header)
        inner.addLayout(header)

        for index, (name, exists) in enumerate(targets):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            inner.addLayout(row)
            label = QLabel(name if exists else f"{name} (not in the draft)", self._body)
            label.setProperty("missing", not exists)
            self._target_labels.append(label)
            show = self._make_face_icon_button("search", "Show this target's source node on the canvas")
            show.setEnabled(exists)
            show.clicked.connect(lambda _=False, i=index: self.show_target_requested.emit(i))
            remove = self._make_face_icon_button("dismiss", "Remove this target")
            remove.clicked.connect(lambda _=False, i=index: self.remove_target_requested.emit(i))
            row.addWidget(label, 1)
            row.addWidget(show, 0)
            row.addWidget(remove, 0)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        inner.addLayout(foot)
        self._add_target = LinkButton("+ Add target…", parent=self._body)
        self._add_target.setToolTip("Choose a param, a subgraph input or the drop list for the user to fill in")
        self._add_target.clicked.connect(lambda: self.add_target_requested.emit(self._add_target))
        foot.addWidget(self._add_target, 0)
        foot.addStretch(1)
        self.refresh_theme_styles()

    def _verbs(self) -> list[CardVerb]:
        return [
            CardVerb(
                REMOVE_STEP_VERB,
                "dismiss",
                "Remove this step and its associated targets",
                self.remove_requested.emit,
                on_face=True,
            )
        ]

    @property
    def text_edit(self) -> QLineEdit:
        return self._edit

    @property
    def add_target_button(self) -> LinkButton:
        return self._add_target

    @property
    def target_texts(self) -> list[str]:
        return [label.text() for label in self._target_labels]

    def _restyle_content(self) -> None:
        set_style_sheet(self._number, f"color: {THEME.text_muted}; background: transparent;")
        for label in self._target_labels:
            color = THEME.warning if label.property("missing") else THEME.text_muted
            set_style_sheet(label, f"color: {color}; background: transparent;")


# ---------------------------------------------------------------------------
# The tab
# ---------------------------------------------------------------------------


class SetupPanel(ThemedMixin, QWidget):
    """The Setup tab's content: a bot's step cards, or a draft's step editor.

    It edits a draft's ``template_meta`` in place and reports what it changed, the way
    the signature page does: ``edit_starting`` opens a debounced undo step for a typed
    edit, ``structural_change`` an immediate one for a discrete edit, and ``changed``
    follows either. ``go_to_requested`` names the node to select -- a state id, or a
    region id for a subgraph node -- ``drops_requested`` asks for the Drops tab, and
    ``import_requested`` for one fight's wiki import.
    """

    edit_starting = pyqtSignal(str)
    structural_change = pyqtSignal(str)
    changed = pyqtSignal()
    go_to_requested = pyqtSignal(str)
    drops_requested = pyqtSignal()
    import_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._statuses: list[setup_steps.StepStatus] = []
        self._library = None
        # What the cards on screen were built from: a refresh that would build the same
        # ones is skipped, so it cannot take a field from under the hand typing in it.
        self._shown: tuple | None = None
        self._suppress = False
        self._cards: list[CardFrame] = []
        self._blurb: QPlainTextEdit | None = None
        self._add_card: AddCard | None = None

        self._title = QLabel("Setup", self)
        self._intro = ThemedTextLabel("", color="text_muted", parent=self)
        self._intro.setWordWrap(True)

        self._body = QWidget(self)
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(6)
        self._drag = DragReorder(self._body_layout, self)
        self._drag.reordered.connect(self._on_reordered)
        self._drag.watch_for_show(self)

        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(8, 8, 8, 8)
        column.setSpacing(4)
        column.addWidget(self._title)
        column.addWidget(self._intro)
        column.addWidget(self._body)
        column.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # The cards wrap, and a draft's list grows under the author's hand.
        reserve_scrollbar_gutter(scroll, gap=0)
        scroll.setWidget(content)
        self._scroll = scroll
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.refresh_theme_styles()

    # -- reading -------------------------------------------------------

    @property
    def config(self) -> BotConfig | None:
        return self._config

    @property
    def cards(self) -> list[CardFrame]:
        """The step cards on screen, in order: ``StepCard`` for a bot, ``DraftStepCard`` for a draft."""
        return list(self._cards)

    @property
    def summary(self) -> str:
        return self._intro.text()

    @property
    def blurb_edit(self) -> QPlainTextEdit | None:
        """A draft's blurb field; None for a bot."""
        return self._blurb

    @property
    def add_card(self) -> AddCard | None:
        """The dashed card ending a draft's list; None for a bot."""
        return self._add_card

    @property
    def scroll_area(self) -> QScrollArea:
        return self._scroll

    # -- building ------------------------------------------------------

    def show_document(
        self, config: BotConfig | None, statuses: Sequence[setup_steps.StepStatus] = (), library=None
    ) -> None:
        """Show *config*'s steps: the editor for a draft, the cards (*statuses*) for a bot, nothing for neither."""
        self._config = config
        self._statuses = list(statuses)
        self._library = library
        shown = self._signature()
        if shown == self._shown:
            return
        self._shown = shown
        self._suppress = True
        try:
            self._rebuild()
        finally:
            self._suppress = False

    def _signature(self) -> tuple:
        """Everything the cards are drawn from, so two that match would draw the same cards."""
        config = self._config
        if config is None:
            return ()
        if config.template_meta is not None:
            meta = config.template_meta
            return (
                "draft",
                id(config),
                meta.blurb,
                tuple(
                    (s.text, tuple((self._target_name(t), setup_steps.target_exists(config, t)) for t in s.targets))
                    for s in meta.setup
                ),
            )
        return (
            "bot",
            tuple((s.step.text, s.done, step_summary(config, s, self._library)) for s in self._statuses),
            tuple(fights_of(config)) if any(self._is_drops(s) for s in self._statuses) else (),
        )

    @staticmethod
    def _is_drops(status: setup_steps.StepStatus) -> bool:
        return any(t.field is not None for t in status.targets)

    def _target_name(self, target: SetupTarget) -> str:
        assert self._config is not None
        return setup_steps.describe_target(self._config, target)

    def _clear(self) -> None:
        self._drag.set_cards([])
        self._cards = []
        self._blurb = None
        self._add_card = None
        layout = self._body_layout
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                discard_widget(item.widget())

    def _rebuild(self) -> None:
        self._clear()
        config = self._config
        if config is None:
            self._intro.setText("")
            return
        if config.template_meta is not None:
            self._intro.setText(
                "What a bot made from this template is asked to fill in. Every target of a step is left "
                "blank in a release, and the bot can't run until it is filled."
            )
            self._build_draft()
        else:
            self._intro.setText(progress_text(self._statuses))
            self._build_bot()

    # -- a bot ---------------------------------------------------------

    def _build_bot(self) -> None:
        assert self._config is not None
        fights = fights_of(self._config)
        for status in self._statuses:
            card = StepCard(
                status.step.text,
                step_summary(self._config, status, self._library),
                status.done,
                fights if self._is_drops(status) else None,
                parent=self._body,
            )
            card.activated.connect(lambda s=status: self._go_to(s))
            card.import_requested.connect(self.import_requested)
            self._body_layout.addWidget(card)
            self._cards.append(card)

    def _go_to(self, status: setup_steps.StepStatus) -> None:
        """Go to what a step asks about: the first target still to do, else the first."""
        if self._config is None:
            return
        pending = [t for t in status.targets if not setup_steps.target_done(self._config, t, self._library)]
        self._show_target((pending or list(status.targets))[0])

    def _show_target(self, target: SetupTarget) -> None:
        if target.field is not None:
            self.drops_requested.emit()
        else:
            self.go_to_requested.emit(target.state or target.region or "")

    # -- a draft -------------------------------------------------------

    def _build_draft(self) -> None:
        meta = self._meta()
        layout = self._body_layout
        layout.addWidget(RuleHeading("Blurb", self._body))
        self._blurb = QPlainTextEdit(self._body)
        self._blurb.setPlainText(meta.blurb)
        self._blurb.setFixedHeight(96)
        self._blurb.setToolTip(
            "What the New Bot dialog says under the template's name. Its first paragraph is the card."
        )
        self._blurb.textChanged.connect(self._on_blurb_edited)
        layout.addWidget(self._blurb)

        layout.addWidget(RuleHeading("Setup Steps", self._body))
        for index, step in enumerate(meta.setup):
            card = DraftStepCard(
                index + 1,
                step.text,
                [(self._target_name(t), setup_steps.target_exists(self._config, t)) for t in step.targets],
                parent=self._body,
            )
            card.text_edited.connect(lambda text, i=index: self._set_text(i, text))
            card.remove_requested.connect(lambda i=index: self.remove_step(i))
            card.add_target_requested.connect(lambda anchor, i=index: self._offer_targets(i, anchor))
            card.remove_target_requested.connect(lambda k, i=index: self.remove_target(i, k))
            card.show_target_requested.connect(lambda k, i=index: self._show_target(self._meta().setup[i].targets[k]))
            layout.addWidget(card)
            self._cards.append(card)
        self._drag.set_cards(list(self._cards))
        self._add_card = AddCard(ADD_STEP_TEXT, parent=self._body)
        self._add_card.add_requested.connect(self.add_step)
        layout.addWidget(self._add_card)

    def _offer_targets(self, index: int, anchor: QWidget) -> None:
        assert self._config is not None
        taken = {t.model_dump_json() for step in self._meta().setup for t in step.targets}
        menu = popup_menu(anchor)
        for heading, choices in target_choices(self._config, self._library):
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
        """A typed edit: debounced undo, applied, reported -- the cards themselves are left alone."""
        if self._suppress:
            return
        self.edit_starting.emit(desc)
        apply()
        # What is on screen already says it, so the refresh this causes builds nothing.
        self._shown = self._signature()
        self.changed.emit()

    def _discrete(self, desc: str, apply) -> None:
        """A discrete edit: its own undo step, applied, reported, and the cards rebuilt."""
        if self._suppress or self._config is None:
            return
        self.structural_change.emit(desc)
        apply()
        self.changed.emit()
        self.show_document(self._config, self._statuses, self._library)

    def _on_blurb_edited(self) -> None:
        text = self._blurb.toPlainText().strip() if self._blurb is not None else ""
        self._typed("Edit blurb", lambda: setattr(self._meta(), "blurb", text))

    def _set_text(self, index: int, text: str) -> None:
        self._typed("Edit setup step", lambda: setattr(self._meta().setup[index], "text", text.strip()))

    def add_step(self) -> None:
        """Append an empty step -- what the Add card does."""
        self._discrete("Add setup step", lambda: self._meta().setup.append(SetupStep(text="")))
        if self._cards and isinstance(self._cards[-1], DraftStepCard):
            self._cards[-1].text_edit.setFocus()

    def remove_step(self, index: int) -> None:
        self._discrete("Remove setup step", lambda: self._meta().setup.pop(index))

    def _on_reordered(self, order: list[int]) -> None:
        def apply() -> None:
            steps = self._meta().setup
            steps[:] = [steps[i] for i in order]

        if order != sorted(order):
            self._discrete("Reorder setup steps", apply)

    def reorder_steps(self, order: list[int]) -> None:
        """Put the steps in *order*, given as their current indices -- what a drag does on drop."""
        self._on_reordered(order)

    def add_target(self, index: int, target: SetupTarget) -> None:
        """Make *target* a fill-in of step *index* -- what a choice in *Add target…* does."""
        self._discrete("Add setup target", lambda: self._meta().setup[index].targets.append(target))

    def remove_target(self, index: int, target_index: int) -> None:
        self._discrete("Remove setup target", lambda: self._meta().setup[index].targets.pop(target_index))

    def apply_theme_styles(self) -> None:
        font = self._title.font()
        font.setBold(True)
        font.setPixelSize(THEME.font_heading_px)
        self._title.setFont(font)
        set_style_sheet(self._title, f"color: {THEME.text};")
