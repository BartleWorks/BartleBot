"""The signature page: a definition document's page when nothing is selected (#155).

Everything a subgraph node shows is authored here -- the Entry and Exit ports, the
inputs and their defaults, the input groups they are listed under, and the nodes
promoted whole. A port *is* its node on the canvas, so adding one places a node and
deleting one deletes the node; its name is the node's label, so renaming changes nothing
structural. An input is added from a node's own page (a param's *Link* button), or from
a nested subgraph node's page (one of its inputs' *Link* button); this page renames,
re-describes, sets the default, reorders, groups, unlinks and deletes.

THE PAGE IS #142's CARDS

Description, Ports and Inputs, as ``OptionCard``\\s under the node pages' fold bar. The
three are the same in every definition, so their folds are one surface in
``ui_state.json`` (``param_cards.SIGNATURE_PAGE_SURFACE``), as the node page's are.

INPUTS ARE A LIST WITH A DETAIL EDITOR

The pattern Blender uses for node group sockets and Unreal for Blueprint variables. The
Inputs card is one tree, ``signature_tree.tree_rows``: the ungrouped inputs, then each
input group with its members indented, then each promoted node as its heading alone. A
line is a label and a type. Selecting one opens its editor in place, under the line, and
only one is open at a time.

- **Membership lives in one place, the tree.** An input belongs to the nearest group
  heading above it, so dragging an input moves it into or out of a group and nothing
  else does -- there is no per-input group dropdown and no per-group member list.
  ``DragReorder`` draws the drag, with ``signature_tree`` as its ``accepts`` and
  ``carry``: no input under a promoted node, no move that changes another input's group,
  and a group heading travels with its members.
- **The keyboard route is the tree's own.** The tree is one Tab stop: Up and Down move
  between lines, Enter or Space opens one, Alt+Up and Alt+Down move it (by the same rule
  a drop follows), and the Menu key opens its menu -- Move up, Move down, Move to group,
  and Delete.
- **Pressing a grip closes the open editor**, so every drag moves one-line items.
- **Selecting reaches into the canvas without moving it.** The nodes the selected item
  is linked to are lit (``highlight_requested``) and a link row centers its node
  (``center_requested``), but nothing is *selected* there: selecting a node opens that
  node's page, and Setup taught us that a list which leaves on every act costs a trip
  back each time. The selection is the page's own and survives leaving it, and an input
  linked while away comes back selected.

Every edit is ``_typed`` (a debounced undo step, the page left alone, so typing is never
rebuilt under the cursor) or ``_discrete`` (an immediate one, then a rebuild). The order
and the membership are never copied here: they ride on ``BotConfig.definition``, so undo
and autosave carry them.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable
from dataclasses import replace

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QShortcut
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from bartlebot import param_cards
from bartlebot.bot.schema import SUBGRAPH_ENTRY, SUBGRAPH_EXIT, BotConfig, SubgraphInput, SubgraphPanel
from bartlebot.content.subgraphs import signature_tree as st
from bartlebot.content.subgraphs.signature_tree import GROUP, INPUT, NODE, TreeRow
from bartlebot.content.subgraphs.subgraphs import binding_type, nested_key
from bartlebot.gui.canvas.node_cards import PageFoldBar
from bartlebot.gui.menu_utils import popup_menu
from bartlebot.gui.sidebar.option_card import CardFolds, OptionCard, UiStateFoldStore
from bartlebot.gui.sidebar.option_gutter import BAR_WIDTH_PX, GUTTER_BTN_PX, GUTTER_GAP_PX, GUTTER_TO_BODY_PX
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin, set_style_sheet
from bartlebot.gui.widgets.action_button import ActionButton
from bartlebot.gui.widgets.add_card import AddCard
from bartlebot.gui.widgets.card_frame import GRIP_GUTTER, GripHandle
from bartlebot.gui.widgets.discard import discard_widget
from bartlebot.gui.widgets.drag_reorder import MOVE_DOWN_KEYS, MOVE_UP_KEYS, DragReorder, add_move_actions
from bartlebot.gui.widgets.icon_button import IconButton
from bartlebot.gui.widgets.link_button import LinkButton
from bartlebot.gui.widgets.themed_label import ThemedTextLabel, ThemedWrappingElidedLabel
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS

#: The cards' gap: the same as the option surfaces' and the node pages'.
_CARD_SPACING_PX = 8

#: One line of the tree, or one port.
_LINE_PX = 26

#: How far an input group's member is indented past its heading.
_INDENT_PX = 18

#: The width of a param row's ``[bar][reset][?]`` gutter, so a field the editor draws
#: itself (Label, Help) starts its head line where the Default row's does.
_GUTTER_PX = BAR_WIDTH_PX + GUTTER_GAP_PX + 2 * GUTTER_BTN_PX + GUTTER_TO_BODY_PX

#: The editor's Delete and the menu's, worded once.
DELETE_INPUT = "Delete input"
DELETE_GROUP = "Delete group"
DEMOTE = "Demote"
MOVE_TO_GROUP = "Move to group"
NO_GROUP = "No group"

_DELETE_INPUT_TIP = "Delete this input. The params it set keep their own values."
_DELETE_GROUP_TIP = "Delete this group. Its inputs stay, in no group."
_DEMOTE_TIP = "Demote: take this node's input group off the subgraph node. The node and its values stay."
_CENTER_TIP = "Center this node on the canvas. It stays unselected, so this page stays open."


def _input_key(name: str) -> str:
    return f"i:{name}"


def _panel_key(panel_id: str) -> str:
    return f"g:{panel_id}"


def _row_key(row: TreeRow) -> str:
    return _input_key(row.key) if row.kind == INPUT else _panel_key(row.key)


def _small(text: str = "", *, color: str = "text_muted") -> ThemedTextLabel:
    return ThemedTextLabel(text, color=color, css=f"font-size: {THEME.font_small_px}px;")


class _NoFolds:
    """The fold store of a page showing nothing yet: nothing stored, nothing written."""

    def read(self) -> dict[str, bool]:
        return {}

    def default(self, group: str) -> bool:  # noqa: ARG002
        return False

    def write(self, folds, changes) -> None:  # noqa: ARG002
        return


class _Grip(GripHandle):
    """A line's drag handle, which also says when it is pressed: that closes the open editor."""

    pressed = pyqtSignal()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.pressed.emit()
        super().mousePressEvent(event)


def _paint_lifted(widget: QWidget) -> None:
    """The picked-up look a ``CardFrame`` has: the selected node fill, an accent outline."""
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(THEME.accent), 1))
    painter.setBrush(QColor(THEME.node_fill_selected))
    painter.drawRoundedRect(widget.rect().adjusted(0, 0, -1, -1), 3, 3)
    painter.end()


class _PortLine(QWidget):
    """One Entry or Exit: a grip and its name, which is the port node's label.

    Its delete is in its right-click menu and nowhere on the line: a port has nothing
    else to edit, so it opens no editor, and a trash a few pixels from the grip is a
    mis-click during a drag. The name field gives up its own text menu for the line's,
    so a right-click anywhere on the line -- or the Menu key in the field -- opens it.
    """

    menu_requested = pyqtSignal(QPoint)

    def __init__(self, name: str, placeholder: str, what: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(_LINE_PX + 2)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 1, 0, 1)
        row.setSpacing(4)
        self._grip = _Grip(f"Drag to reorder the {what}, or Alt+Up / Alt+Down", self)
        row.addWidget(self._grip)
        self.name_edit = QLineEdit(name, self)
        self.name_edit.setPlaceholderText(placeholder)
        self.name_edit.setToolTip("The port's name, on every subgraph node. Right-click for more.")
        self.name_edit.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        row.addWidget(self.name_edit, 1)
        self.setFocusProxy(self.name_edit)
        self._dragging = False
        self._route: tuple[Callable[[int], object], Callable[[int], bool]] | None = None
        self._shortcuts: list[QShortcut] = []

    @property
    def grip(self) -> GripHandle:
        return self._grip

    @property
    def route(self) -> tuple[Callable[[int], object], Callable[[int], bool]] | None:
        return self._route

    def set_dragging(self, dragging: bool) -> None:
        self._dragging = dragging
        self.update()

    def set_reorder_route(self, move: Callable[[int], object], can_move: Callable[[int], bool]) -> None:
        """Alt+Up / Alt+Down while the name is being edited; see ``CardFrame.set_reorder_route``."""
        self._route = (move, can_move)
        if self._shortcuts:
            return
        for keys, step in ((MOVE_UP_KEYS, -1), (MOVE_DOWN_KEYS, 1)):
            shortcut = QShortcut(keys, self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda s=step: self._route is not None and self._route[0](s))
            self._shortcuts.append(shortcut)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.menu_requested.emit(event.globalPos())

    def paintEvent(self, event) -> None:  # noqa: ARG002, N802 - Qt override
        if self._dragging:
            _paint_lifted(self)


class _TreeItem(QWidget):
    """One line of the inputs tree, and its editor under it while it is selected.

    A ``DragReorder`` card: it has a ``grip`` and a ``set_dragging``. The editor is part
    of the item rather than a row of its own so the lines stay contiguous in the layout,
    which is what the engine needs -- and since pressing a grip closes the editor, the
    item is one line tall whenever it is dragged.
    """

    clicked = pyqtSignal()
    menu_requested = pyqtSignal(QPoint)

    def __init__(
        self,
        row: TreeRow,
        label: str,
        meta: str,
        *,
        warn: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.row = row
        self._selected = False
        self._current = False
        self._dragging = False
        self._route: tuple[Callable[[int], object], Callable[[int], bool]] | None = None
        self._detail: QWidget | None = None

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(1, 1, 1, 1)
        self._layout.setSpacing(0)
        self._line = QWidget(self)
        self._line.setFixedHeight(_LINE_PX)
        line = QHBoxLayout(self._line)
        line.setContentsMargins(0, 0, 6, 0)
        line.setSpacing(2)
        what = "this input" if row.kind == INPUT else "this group"
        self._grip = _Grip(f"Drag to reorder {what}, or Alt+Up / Alt+Down", self._line)
        line.addWidget(self._grip)
        if row.member:
            line.addSpacing(_INDENT_PX)
        if row.kind == NODE:
            line.addWidget(ThemedTextLabel("⧉", color="accent"))
        self._label = ThemedTextLabel(label, color="text")
        if row.is_heading:
            font = self._label.font()
            font.setBold(True)
            self._label.setFont(font)
        line.addWidget(self._label, 1)
        self._meta = _small(meta, color="warning" if warn else "text_muted")
        line.addWidget(self._meta)
        self._layout.addWidget(self._line)
        self._line.setCursor(Qt.CursorShape.PointingHandCursor)
        self._line.setToolTip("Click to edit. Drag the grip to reorder, or Alt+Up / Alt+Down. Right-click for more.")

    # -- what the engine and the page ask of it ------------------------

    @property
    def grip(self) -> GripHandle:
        return self._grip

    @property
    def route(self) -> tuple[Callable[[int], object], Callable[[int], bool]] | None:
        """The keyboard route ``DragReorder`` handed over: what Alt+Up/Down and the menu call."""
        return self._route

    @property
    def detail(self) -> QWidget | None:
        return self._detail

    @property
    def label_text(self) -> str:
        return self._label.text()

    @property
    def meta_text(self) -> str:
        return self._meta.text()

    def set_reorder_route(self, move: Callable[[int], object], can_move: Callable[[int], bool]) -> None:
        self._route = (move, can_move)

    def set_dragging(self, dragging: bool) -> None:
        self._dragging = dragging
        self.update()

    def set_label(self, text: str) -> None:
        self._label.setText(text)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.update()

    def set_current(self, current: bool) -> None:
        """Draw the keyboard's place: the tree's focus ring, on this line."""
        self._current = current
        self.update()

    def set_detail(self, detail: QWidget | None) -> None:
        if self._detail is not None:
            discard_widget(self._detail, self._layout)
        self._detail = detail
        if detail is not None:
            self._layout.addWidget(detail)

    # -- events -------------------------------------------------------

    def _on_line(self, event) -> bool:
        return event.button() == Qt.MouseButton.LeftButton and self._line.geometry().contains(
            event.position().toPoint()
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Kept, so the release comes back here rather than to whatever label was under it.
        if self._on_line(event):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._on_line(event):
            self.clicked.emit()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.menu_requested.emit(event.globalPos())

    def menu_position(self) -> QPoint:
        """Where a menu opened from the keyboard drops: under the line's text."""
        return self._line.mapToGlobal(QPoint(GRIP_GUTTER, self._line.height()))

    def paintEvent(self, event) -> None:  # noqa: ARG002, N802 - Qt override
        if self._dragging:
            _paint_lifted(self)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        line = self._line.geometry()
        if self.row.member:
            # The rule down the indent, which is what says the line belongs to the
            # heading above when the eye is not comparing two left edges.
            x = self._grip.geometry().right() + 2 + _INDENT_PX // 3
            painter.setPen(QPen(QColor(THEME.border), 1))
            painter.drawLine(x, 0, x, line.bottom() + 1)
        if self._selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(THEME.node_fill_selected))
            painter.drawRoundedRect(line.adjusted(0, 0, 0, 0), 2, 2)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(THEME.accent), 1))
            painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 3, 3)
        if self._current:
            pen = QPen(QColor(THEME.accent), 1, Qt.PenStyle.DotLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(line.adjusted(1, 1, -2, -2))
        painter.end()


class _InputTree(QWidget):
    """The tree's lines, as one Tab stop with the keys a list has.

    The keys are reported, not acted on: what a line *is* and what moving it means is
    the page's. Up and Down move the keyboard's place, Enter and Space open or close
    the line there, Alt+Up and Alt+Down move it, and the Menu key opens its menu.
    """

    step_requested = pyqtSignal(int)
    toggle_requested = pyqtSignal()
    move_requested = pyqtSignal(int)
    menu_requested = pyqtSignal()
    focus_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.rows_layout = QVBoxLayout(self)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(1)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        key = event.key()
        alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = -1 if key == Qt.Key.Key_Up else 1
            (self.move_requested if alt else self.step_requested).emit(step)
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.toggle_requested.emit()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt override
        # A right-click lands on a line, which takes it; what reaches here is the Menu key.
        self.menu_requested.emit()

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().focusInEvent(event)
        self.focus_changed.emit()

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().focusOutEvent(event)
        self.focus_changed.emit()


class SignaturePage(ThemedMixin, QWidget):
    """Edits ``config.definition`` (and the port nodes' labels and order) in place.

    ``edit_starting`` opens a debounced undo step for a typed edit, ``structural_change``
    an immediate one for a discrete edit; ``changed`` follows either. Adding a port is
    the canvas's to do (``add_port_requested``), as is deleting one
    (``delete_port_requested``), since each is a node on the canvas. ``highlight_requested``
    names what the selected item is linked to, for the canvas to light, and
    ``center_requested`` one node to bring into view; both are state ids, or a nested
    subgraph node's region id.
    """

    edit_starting = pyqtSignal(str)
    structural_change = pyqtSignal(str)
    changed = pyqtSignal()
    add_port_requested = pyqtSignal(str)  # SUBGRAPH_ENTRY / SUBGRAPH_EXIT
    delete_port_requested = pyqtSignal(str)  # state id
    highlight_requested = pyqtSignal(list)
    center_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._library = None
        self._suppress = False
        self._get_clients: Callable[[], list] = lambda: []
        self._get_zones: Callable[[], list[str]] = lambda: []
        # The page's own state, kept across rebuilds and across leaving the page: which
        # line is open, where the keyboard is, which definition the folds are for, and
        # the inputs it last saw (so one linked while away comes back selected).
        self._selected: str | None = None
        self._current: str | None = None
        self._shown_definition: str | None = None
        self._known_inputs: set[str] | None = None
        self._items: dict[str, _TreeItem] = {}
        self._rows: list[TreeRow] = []
        self._row_of: dict[QWidget, TreeRow] = {}
        self._forms: list = []

        self._title = QLabel("Signature")
        self._meta_lbl = ThemedWrappingElidedLabel("", color="text_muted")
        self._fold_bar = PageFoldBar(self._set_descriptions_visible)
        self._folds = CardFolds(self._fold_bar.heading_actions, store=_NoFolds())

        self._desc_card = OptionCard(param_cards.DESCRIPTION, "Description")
        self._description = QPlainTextEdit()
        self._description.setPlaceholderText("When to use it, and what it expects of the bot around it")
        self._description.setFixedHeight(64)
        self._desc_card.body_layout.addWidget(self._description)

        self._ports_card = OptionCard(param_cards.PORTS, "Ports")
        ports = QWidget()
        ports_layout = QVBoxLayout(ports)
        ports_layout.setContentsMargins(0, 0, 0, 0)
        ports_layout.setSpacing(2)
        self._port_boxes: dict[str, QVBoxLayout] = {}
        self._port_drags: dict[str, DragReorder] = {}
        for kind, heading, add_text in ((SUBGRAPH_ENTRY, "Entries", "Add entry"), (SUBGRAPH_EXIT, "Exits", "Add exit")):
            ports_layout.addWidget(_small(heading, color="text_subtle"))
            box = QWidget()
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(0, 0, 0, 0)
            box_layout.setSpacing(1)
            ports_layout.addWidget(box)
            drag = DragReorder(box_layout, self)
            drag.reordered.connect(lambda order, k=kind: self._on_ports_reordered(k, order))
            self._port_boxes[kind] = box_layout
            self._port_drags[kind] = drag
            add = LinkButton(f"+ {add_text}")
            add.clicked.connect(lambda _=False, k=kind: self.add_port_requested.emit(k))
            ports_layout.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
        self._ports_card.body_layout.addWidget(ports)

        self._inputs_card = OptionCard(param_cards.INPUTS, "Inputs")
        self._no_inputs = _small("No inputs yet.")
        self._tree = _InputTree()
        self._drag = DragReorder(self._tree.rows_layout, self)
        me = weakref.ref(self)
        # Weakly, for the reason DragReorder._route_for gives: the engine keeps its rules.
        self._drag.set_rules(
            accepts=lambda order, block: (page := me()) is not None and page._accepts(order, block),
            carry=lambda card: page._carry(card) if (page := me()) is not None else 0,
        )
        self._drag.reordered.connect(self._on_tree_reordered)
        self._drag.watch_for_show(self)
        self._add_group = AddCard("Add input group")
        self._add_group.setToolTip("Add an input group: a card of its own on every subgraph node")
        self._add_group.add_requested.connect(self._add_panel)
        self._hint = _small(
            "To add an input, hover a param on a node's page, or an input on a subgraph node's page, "
            "and click its link button."
        )
        self._hint.setWordWrap(True)
        self._inputs_card.body_layout.addWidget(self._no_inputs)
        self._inputs_card.body_layout.addWidget(self._tree)
        self._inputs_card.body_layout.addWidget(self._add_group)
        self._inputs_card.body_layout.addWidget(self._hint)

        self._tree.step_requested.connect(self._step_current)
        self._tree.toggle_requested.connect(self._toggle_current)
        self._tree.move_requested.connect(self._move_current)
        self._tree.menu_requested.connect(self._menu_current)
        self._tree.focus_changed.connect(self._paint_current)

        cards = QWidget()
        cards_layout = QVBoxLayout(cards)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(_CARD_SPACING_PX)
        for card in (self._desc_card, self._ports_card, self._inputs_card):
            cards_layout.addWidget(card)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.addWidget(self._title)
        layout.addWidget(self._meta_lbl)
        layout.addSpacing(4)
        layout.addWidget(self._fold_bar)
        layout.addWidget(cards)
        layout.addStretch(1)
        self._folds.set_cards(
            {
                param_cards.DESCRIPTION: self._desc_card,
                param_cards.PORTS: self._ports_card,
                param_cards.INPUTS: self._inputs_card,
            }
        )
        self._description.textChanged.connect(self._on_description_changed)
        self.refresh_theme_styles()

    # -- wiring ---------------------------------------------------------

    def set_clients_getter(self, fn: Callable[[], list]) -> None:
        self._get_clients = fn

    def set_zone_suggestions_getter(self, fn: Callable[[], list[str]]) -> None:
        self._get_zones = fn

    def set_subgraph_library(self, library) -> None:
        """Where an input linked into a nested subgraph node finds that node's definition."""
        self._library = library

    @property
    def config(self) -> BotConfig | None:
        return self._config

    @property
    def folds(self) -> CardFolds:
        return self._folds

    @property
    def tree(self) -> QWidget:
        """The inputs tree: one Tab stop, holding the lines."""
        return self._tree

    @property
    def selected(self) -> str | None:
        """The open line, as ``"i:<input name>"`` or ``"g:<panel id>"``; None when none is."""
        return self._selected

    def item(self, key: str) -> _TreeItem | None:
        """One line by key -- see :attr:`selected`."""
        return self._items.get(key)

    @property
    def lines(self) -> list[_TreeItem]:
        """The tree's lines, top to bottom."""
        return [self._items[_row_key(row)] for row in self._rows]

    def port_lines(self, kind: str) -> list[_PortLine]:
        """The Entry (``SUBGRAPH_ENTRY``) or Exit lines, top to bottom."""
        return list(self._port_drags[kind].cards)

    # -- showing --------------------------------------------------------

    def show_signature(self, config: BotConfig) -> None:
        self._config = config
        meta = config.definition
        self._suppress = True
        try:
            if self._description.toPlainText() != (config.description or ""):
                self._description.setPlainText(config.description or "")
            if meta is None:
                self._meta_lbl.setText("")
                self._rebuild()
                return
            self._meta_lbl.setText(f"{config.name} · subgraph definition · v{meta.version}")
            if self._shown_definition != meta.id:
                # A different definition: its own folds' defaults, and nothing carried over.
                self._shown_definition = meta.id
                self._known_inputs = None
                self._selected = self._current = None
                self._folds.set_store(
                    UiStateFoldStore(
                        param_cards.SIGNATURE_PAGE_SURFACE,
                        param_cards.signature_fold_defaults(has_description=bool(config.description)),
                    )
                )
                self._folds.reload()
            names = [i.name for i in meta.inputs]
            if self._known_inputs is not None:
                fresh = [n for n in names if n not in self._known_inputs]
                if len(fresh) == 1:
                    # Linked from a node's page while this one was away: open it to be named.
                    self._selected = self._current = _input_key(fresh[0])
            self._known_inputs = set(names)
            self._rebuild()
        finally:
            self._suppress = False
        self._emit_highlight()

    def _rebuild(self) -> None:
        config = self._config
        meta = config.definition if config is not None else None
        self._rebuild_ports()
        self._rebuild_tree()
        self._refresh_summaries()
        has_meta = meta is not None
        for card in (self._ports_card, self._inputs_card):
            card.setVisible(has_meta)

    def _rebuild_ports(self) -> None:
        config = self._config
        for kind, box in self._port_boxes.items():
            drag = self._port_drags[kind]
            old = drag.cards
            drag.set_cards([])
            for line in old:
                discard_widget(line, box)
            if config is None or config.definition is None:
                continue
            what = "entries" if kind == SUBGRAPH_ENTRY else "exits"
            lines = []
            for state in (s for s in config.states if s.node_type == kind):
                line = _PortLine(state.label or "", state.id, what, parent=box.parentWidget())
                box.addWidget(line)
                line.name_edit.textChanged.connect(lambda text, s=state.id: self._rename_port(s, text))
                line.menu_requested.connect(
                    lambda pos, k=kind, s=state.id, ln=line: self._port_menu(k, s, ln).exec(pos)
                )
                lines.append(line)
            drag.set_cards(lines)

    def _rebuild_tree(self) -> None:
        self._drag.set_cards([])
        for item in self._items.values():
            discard_widget(item, self._tree.rows_layout)
        self._items = {}
        self._row_of = {}
        self._forms = []
        config = self._config
        meta = config.definition if config is not None else None
        self._rows = st.tree_rows(meta.inputs, meta.panels) if meta is not None else []
        for row in self._rows:
            item = self._make_item(row)
            self._tree.rows_layout.addWidget(item)
            self._items[_row_key(row)] = item
            self._row_of[item] = row
        self._drag.set_cards(self.lines)
        if self._selected not in self._items:
            self._selected = None
        if self._current not in self._items:
            self._current = self._selected
        if self._selected is not None:
            item = self._items[self._selected]
            item.set_selected(True)
            item.set_detail(self._build_detail(self._selected, item))
        self._no_inputs.setVisible(not self._rows)
        self._tree.setVisible(bool(self._rows))
        self._paint_current()

    def _make_item(self, row: TreeRow) -> _TreeItem:
        config = self._config
        assert config is not None and config.definition is not None
        meta = config.definition
        if row.kind == INPUT:
            inp = self._find_input(row.key)
            kind = binding_type(config, inp.bindings, NODE_PARAM_SCHEMAS, self._library) or inp.type
            n = len(inp.bindings)
            text = kind + (f" · {n} links" if n > 1 else "") + (" · not linked" if n == 0 else "")
            item = _TreeItem(row, inp.label or inp.name, text, warn=n == 0, parent=self._tree)
        else:
            panel = self._find_panel(row.key)
            if row.kind == GROUP:
                count = sum(1 for i in meta.inputs if i.panel == panel.id)
                text = "empty" if count == 0 else f"{count} input{'' if count == 1 else 's'}"
                item = _TreeItem(row, panel.label or "Untitled group", text, parent=self._tree)
            else:
                state = self._member(panel)
                params = NODE_PARAM_SCHEMAS.get(state.node_type, []) if state is not None else []
                label = (state.label if state is not None else None) or panel.member or panel.id
                item = _TreeItem(row, label, f"{len(params)} params · promoted node", parent=self._tree)
        key = _row_key(row)
        item.clicked.connect(lambda k=key: self._on_item_clicked(k))
        item.menu_requested.connect(lambda pos, k=key: self._open_item_menu(k, pos))
        item.grip.pressed.connect(self._close_detail)
        return item

    def _refresh_summaries(self) -> None:
        config = self._config
        meta = config.definition if config is not None else None
        self._desc_card.set_summary(param_cards.description_summary(config.description if config else None))
        if config is None or meta is None:
            return
        names = {
            kind: [s.label or s.id for s in config.states if s.node_type == kind]
            for kind in (SUBGRAPH_ENTRY, SUBGRAPH_EXIT)
        }
        self._ports_card.set_summary(param_cards.ports_summary(names[SUBGRAPH_ENTRY], names[SUBGRAPH_EXIT]))
        groups = sum(1 for p in meta.panels if p.kind == "inputs")
        self._inputs_card.set_summary(param_cards.inputs_summary(len(meta.inputs), groups, len(meta.panels) - groups))

    # -- selection and the keyboard ---------------------------------------

    def select(self, key: str | None) -> None:
        """Open *key*'s editor (closing any other), or close the open one with None."""
        if key is not None and key not in self._items:
            return
        if key == self._selected:
            return
        old = self._items.get(self._selected) if self._selected is not None else None
        if old is not None:
            old.set_detail(None)
            old.set_selected(False)
        self._forms = []
        self._selected = key
        if key is not None:
            self._current = key
            item = self._items[key]
            item.set_selected(True)
            item.set_detail(self._build_detail(key, item))
        self._tree.rows_layout.activate()
        self._paint_current()
        self._emit_highlight()

    def _on_item_clicked(self, key: str) -> None:
        self._current = key
        self._tree.setFocus(Qt.FocusReason.MouseFocusReason)
        self.select(None if self._selected == key else key)

    def _close_detail(self) -> None:
        """A grip was pressed: close the open editor, so the drag moves one-line items."""
        self.select(None)

    def _step_current(self, step: int) -> None:
        keys = [_row_key(r) for r in self._rows]
        if not keys:
            return
        if self._current not in keys:
            self._current = keys[0]
        else:
            self._current = keys[max(0, min(len(keys) - 1, keys.index(self._current) + step))]
        self._paint_current()

    def _toggle_current(self) -> None:
        if self._current is not None:
            self.select(None if self._selected == self._current else self._current)

    def _move_current(self, step: int) -> None:
        item = self._items.get(self._current) if self._current is not None else None
        if item is not None and item.route is not None:
            item.route[0](step)

    def _menu_current(self) -> None:
        item = self._items.get(self._current) if self._current is not None else None
        if item is not None:
            self._open_item_menu(self._current, item.menu_position())

    def _paint_current(self) -> None:
        focused = self._tree.hasFocus()
        for key, item in self._items.items():
            item.set_current(focused and key == self._current)

    def _emit_highlight(self) -> None:
        self.highlight_requested.emit(self.linked_ids(self._selected))

    def linked_ids(self, key: str | None) -> list[str]:
        """What *key* reaches on the canvas: the nodes an input sets, a group's inputs', a promoted node."""
        if key is None or self._config is None or self._config.definition is None:
            return []
        meta = self._config.definition
        if key.startswith("i:"):
            inputs = [i for i in meta.inputs if i.name == key[2:]]
        else:
            panel = next((p for p in meta.panels if p.id == key[2:]), None)
            if panel is None:
                return []
            if panel.kind == "node":
                return [panel.member] if panel.member else []
            inputs = [i for i in meta.inputs if i.panel == panel.id]
        found: list[str] = []
        for inp in inputs:
            for b in inp.bindings:
                target = b.region or b.state
                if target and target not in found:
                    found.append(target)
        return found

    # -- the tree's drag ---------------------------------------------------

    def _accepts(self, order: list[QWidget], block: list[QWidget]) -> bool:
        return st.allowed(self._rows, [self._row_of[w] for w in order], [self._row_of[w] for w in block])

    def _carry(self, card: QWidget) -> int:
        row = self._row_of.get(card)
        return st.carried(self._rows, self._rows.index(row)) if row is not None else 0

    def _on_tree_reordered(self, order: list[int]) -> None:
        rows = [self._rows[index] for index in order]
        self._discrete("Reorder inputs", lambda: st.reorder(self._meta(), rows))

    # -- menus ---------------------------------------------------------------

    def item_menu(self, key: str) -> QMenu | None:
        """The menu a line's right-click opens, built but not shown."""
        item = self._items.get(key)
        if item is None:
            return None
        menu = popup_menu(item)
        if item.route is not None:
            add_move_actions(menu, *item.route)
        if item.row.kind == INPUT:
            name = item.row.key
            current = self._find_input(name).panel
            groups = [p for p in self._meta().panels if p.kind == "inputs"]
            sub = menu.addMenu(MOVE_TO_GROUP)
            sub.setEnabled(bool(groups))
            choices = [(None, NO_GROUP)] + [(p.id, p.label or "Untitled group") for p in groups]
            for panel_id, label in choices:
                action = sub.addAction(label)
                action.setEnabled(panel_id != current)
                action.triggered.connect(lambda _=False, p=panel_id: self.move_input_to_group(name, p))
            menu.addSeparator()
            menu.addAction(DELETE_INPUT).triggered.connect(lambda: self._delete_input(name))
        elif item.row.kind == GROUP:
            menu.addSeparator()
            menu.addAction(DELETE_GROUP).triggered.connect(lambda _=False, p=item.row.key: self._delete_panel(p))
        else:
            menu.addSeparator()
            menu.addAction(DEMOTE).triggered.connect(lambda _=False, p=item.row.key: self._delete_panel(p))
        return menu

    def _open_item_menu(self, key: str, pos: QPoint) -> None:
        self._current = key
        self._paint_current()
        menu = self.item_menu(key)
        if menu is not None:
            menu.exec(pos)

    def _port_menu(self, kind: str, state_id: str, line: _PortLine) -> QMenu:
        menu = popup_menu(line)
        if line.route is not None:
            add_move_actions(menu, *line.route)
        menu.addSeparator()
        what = "entry" if kind == SUBGRAPH_ENTRY else "exit"
        delete = menu.addAction(f"Delete {what} and its node")
        delete.triggered.connect(lambda: self.delete_port_requested.emit(state_id))
        return menu

    def port_menu(self, kind: str, index: int) -> QMenu:
        """A port line's right-click menu, built but not shown."""
        line = self.port_lines(kind)[index]
        state_id = [s.id for s in self._config.states if s.node_type == kind][index]
        return self._port_menu(kind, state_id, line)

    # -- the editors ---------------------------------------------------------

    def _build_detail(self, key: str, item: _TreeItem) -> QWidget:
        # One editor is open at a time, so the forms are only ever the open one's.
        self._forms = []
        detail = QWidget(item)
        layout = QVBoxLayout(detail)
        layout.setContentsMargins(GRIP_GUTTER, 6, 6, 8)
        layout.setSpacing(6)
        if key.startswith("i:"):
            self._input_detail(self._find_input(key[2:]), item, detail, layout)
        else:
            panel = self._find_panel(key[2:])
            if panel.kind == "node":
                self._node_detail(panel, detail, layout)
            else:
                self._group_detail(panel, item, detail, layout)
        return detail

    def _field_row(self, head: str, field: QWidget, parent: QWidget) -> QWidget:
        """A head line and a field, set in by the param rows' gutter so the heads line up."""
        row = QWidget(parent)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(_GUTTER_PX, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(ThemedTextLabel(head, color="text"))
        layout.addWidget(field)
        return row

    def _head(self, text: str, parent: QWidget) -> QLabel:
        """A head line with nothing under it but the rows that follow."""
        head = ThemedTextLabel(text, color="text", parent=parent)
        head.setContentsMargins(_GUTTER_PX, 0, 0, 0)
        return head

    def _footer(self, parent: QWidget, left: QWidget | None, right: QWidget) -> QWidget:
        row = QWidget(parent)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(_GUTTER_PX, 2, 0, 0)
        layout.setSpacing(6)
        if left is not None:
            layout.addWidget(left)
        layout.addStretch(1)
        layout.addWidget(right)
        return row

    def _input_detail(self, inp: SubgraphInput, item: _TreeItem, detail: QWidget, layout: QVBoxLayout) -> None:
        from bartlebot.gui.canvas.param_panel import (  # noqa: PLC0415 - param_panel imports this module
            _DynamicParamForm,
            input_param_field,
            instance_key_label,
        )

        config = self._config
        assert config is not None
        name = inp.name
        label = QLineEdit(inp.label or "", detail)
        label.setPlaceholderText(name)
        label.setToolTip(f"Shown on the subgraph node; the input's name is '{name}'")
        label.textChanged.connect(lambda text: self._set_input_label(name, text, item))
        layout.addWidget(self._field_row("Label", label, detail))
        help_edit = QLineEdit(inp.help or "", detail)
        help_edit.setPlaceholderText("Shown as the input's tooltip")
        help_edit.textChanged.connect(lambda text: self._set_input(name, "help", text.strip() or None))
        layout.addWidget(self._field_row("Help", help_edit, detail))

        # The one row builder: the default is drawn exactly as a param of its type is.
        default = _DynamicParamForm(detail)
        default.set_clients_getter(self._get_clients)
        default.set_zone_suggestions_getter(self._get_zones)
        field = replace(input_param_field(config, inp, self._library), label="Default", required=False)
        default.load_schema([field], {name: inp.default})
        default.changed.connect(lambda f=default: self._set_default(name, f))
        layout.addWidget(default)
        self._forms.append(default)

        layout.addWidget(self._head("Linked to", detail))
        labels = {s.id: (s.label or s.id) for s in config.states}
        regions = {r.id: r.label for r in config.regions if r.source is not None}
        for b in inp.bindings:
            if b.region is not None:
                found = nested_key(config, b, self._library)
                param = instance_key_label(found) if found is not None else b.param
                text = f"{regions.get(b.region, b.region)} · {param}"
                target = b.region
                tip = "Unlink. That subgraph node's input keeps the value it has now."
            else:
                text = f"{labels.get(b.state, b.state)} · {b.param}"
                target = b.state
                tip = f"Unlink. {labels.get(b.state, b.state)} keeps the value it has now."
            row = QWidget(detail)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(_GUTTER_PX, 0, 0, 0)
            row_layout.setSpacing(4)
            link = LinkButton(text)
            link.setToolTip(_CENTER_TIP)
            link.clicked.connect(lambda _=False, t=target: self.center_requested.emit(t))
            row_layout.addWidget(link, 1, Qt.AlignmentFlag.AlignLeft)
            unlink = IconButton("unhook", tooltip=tip, size=20, icon_size=12)
            unlink.clicked.connect(lambda _=False, st_=b.state, rg=b.region, k=b.param: self._unbind(name, st_, k, rg))
            row_layout.addWidget(unlink)
            layout.addWidget(row)
        if not inp.bindings:
            warn = _small(
                "Linked to nothing. Link a param to it from a node's page, or delete it: "
                "the definition cannot be saved while it is unlinked.",
                color="warning",
            )
            warn.setWordWrap(True)
            warn.setContentsMargins(_GUTTER_PX, 0, 0, 0)
            layout.addWidget(warn)

        delete = ActionButton("trash", DELETE_INPUT, fill=False)
        delete.setToolTip(_DELETE_INPUT_TIP)
        delete.clicked.connect(lambda: self._delete_input(name))
        name_lbl = _small(f"Name: {name}")
        layout.addWidget(self._footer(detail, name_lbl, delete))

    def _group_detail(self, panel: SubgraphPanel, item: _TreeItem, detail: QWidget, layout: QVBoxLayout) -> None:
        heading = QLineEdit(panel.label or "", detail)
        heading.setPlaceholderText("Group heading")
        heading.setToolTip("The card's title on the subgraph node")
        heading.textChanged.connect(lambda text: self._set_panel_label(panel.id, text, item))
        layout.addWidget(self._field_row("Heading", heading, detail))
        members = sum(1 for i in self._meta().inputs if i.panel == panel.id)
        note = _small(
            "Drag an input by its grip to move it into or out of this group, or right-click it and use Move to group."
            if members
            else "No inputs yet. Drag one here, or right-click an input and use Move to group."
        )
        note.setWordWrap(True)
        note.setContentsMargins(_GUTTER_PX, 0, 0, 0)
        layout.addWidget(note)
        delete = ActionButton("trash", DELETE_GROUP, fill=False)
        delete.setToolTip(_DELETE_GROUP_TIP)
        delete.clicked.connect(lambda: self._delete_panel(panel.id))
        layout.addWidget(self._footer(detail, None, delete))

    def _node_detail(self, panel: SubgraphPanel, detail: QWidget, layout: QVBoxLayout) -> None:
        note = _small(
            "Every param of this node is on the subgraph node, with its Record button. "
            "They follow the node type, so they are edited on the node's own page."
        )
        note.setWordWrap(True)
        note.setContentsMargins(_GUTTER_PX, 0, 0, 0)
        layout.addWidget(note)
        state = self._member(panel)
        fields = NODE_PARAM_SCHEMAS.get(state.node_type, []) if state is not None else []
        params = QWidget(detail)
        params_layout = QVBoxLayout(params)
        params_layout.setContentsMargins(0, 0, 0, 0)
        params_layout.setSpacing(1)
        for f in fields:
            params_layout.addWidget(_small(f"{f.label}{' *' if f.required else ''} · {f.type}"))
        layout.addWidget(self._field_row("Params", params, detail))
        labels = {s.id: (s.label or s.id) for s in self._config.states}
        own = {f.key: f.label for f in fields}
        shared = [(key, b) for key, extra in panel.bindings.items() for b in extra]
        if shared:
            layout.addWidget(self._head("Shared values", detail))
        for key, b in shared:
            row = QWidget(detail)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(_GUTTER_PX, 0, 0, 0)
            row_layout.setSpacing(4)
            row_layout.addWidget(_small(f"{own.get(key, key)} also sets {labels.get(b.state, b.state)} · {b.param}"), 1)
            stop = IconButton("dismiss", tooltip="Stop sharing this value", size=20, icon_size=12)
            stop.clicked.connect(lambda _=False, k=key, s=b.state, p=b.param: self._unbind_shared(panel.id, k, s, p))
            row_layout.addWidget(stop)
            layout.addWidget(row)
        show = LinkButton("Show on canvas")
        show.setToolTip(_CENTER_TIP)
        show.clicked.connect(lambda: panel.member and self.center_requested.emit(panel.member))
        demote = ActionButton("chevrons_down", DEMOTE, fill=False)
        demote.setToolTip(_DEMOTE_TIP)
        demote.clicked.connect(lambda: self._delete_panel(panel.id))
        layout.addWidget(self._footer(detail, show, demote))

    def _set_descriptions_visible(self, shown: bool) -> None:
        from bartlebot.gui.canvas.param_panel import write_show_descriptions  # noqa: PLC0415

        write_show_descriptions(shown)
        for form in self._forms:
            form.set_descriptions_visible(shown)

    # -- edits ---------------------------------------------------------------

    def _meta(self):
        assert self._config is not None and self._config.definition is not None
        return self._config.definition

    def _member(self, panel: SubgraphPanel):
        return next((s for s in self._config.states if s.id == panel.member), None) if self._config else None

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
        if not self._suppress:
            self._desc_card.set_summary(param_cards.description_summary(text))

    def _rename_port(self, state_id: str, text: str) -> None:
        def apply() -> None:
            state = next(s for s in self._config.states if s.id == state_id)
            state.label = text.strip() or None

        self._typed("Rename port", apply)

    def _on_ports_reordered(self, kind: str, order: list[int]) -> None:
        def apply() -> None:
            states = self._config.states
            slots = [i for i, s in enumerate(states) if s.node_type == kind]
            same = [states[i] for i in slots]
            for slot, index in zip(slots, order):
                states[slot] = same[index]

        self._discrete("Reorder ports", apply)

    def _find_input(self, name: str) -> SubgraphInput:
        return next(i for i in self._meta().inputs if i.name == name)

    def _set_input(self, name: str, attr: str, value) -> None:
        self._typed("Edit input", lambda: setattr(self._find_input(name), attr, value))

    def _set_input_label(self, name: str, text: str, item: _TreeItem) -> None:
        self._set_input(name, "label", text.strip() or None)
        item.set_label(text.strip() or name)

    def _set_default(self, name: str, form) -> None:
        params = form.get_params()
        self._typed("Edit input default", lambda: setattr(self._find_input(name), "default", params.get(name)))

    def move_input_to_group(self, name: str, panel_id: str | None) -> None:
        """Put an input at the end of a group, or of the ungrouped inputs -- the menu's Move to group."""
        rows = st.move_to_group(self._rows or st.tree_rows(self._meta().inputs, self._meta().panels), name, panel_id)
        self._discrete("Move input", lambda: st.reorder(self._meta(), rows))

    def _delete_input(self, name: str) -> None:
        def apply() -> None:
            meta = self._meta()
            meta.inputs = [i for i in meta.inputs if i.name != name]

        self._discrete("Delete input", apply)

    def _unbind(self, name: str, state: str | None, key: str, region: str | None = None) -> None:
        def apply() -> None:
            inp = self._find_input(name)
            inp.bindings = [b for b in inp.bindings if not (b.state == state and b.region == region and b.param == key)]

        self._discrete("Unbind param", apply)

    def _find_panel(self, panel_id: str) -> SubgraphPanel:
        return next(p for p in self._meta().panels if p.id == panel_id)

    def _set_panel_label(self, panel_id: str, text: str, item: _TreeItem) -> None:
        self._typed("Edit input group", lambda: setattr(self._find_panel(panel_id), "label", text.strip() or None))
        item.set_label(text.strip() or "Untitled group")

    def _delete_panel(self, panel_id: str) -> None:
        """Delete an input group, its inputs staying in no group -- or demote a promoted node."""

        def apply() -> None:
            meta = self._meta()
            rows = [r for r in st.tree_rows(meta.inputs, meta.panels) if not (r.is_heading and r.key == panel_id)]
            for inp in meta.inputs:
                if inp.panel == panel_id:
                    inp.panel = None
            # The group's inputs go to the end of the ungrouped ones, in their order.
            freed = [r for r in rows if r.kind == INPUT and self._find_input(r.key).panel is None]
            rest = [r for r in rows if r not in freed]
            first_heading = next((i for i, r in enumerate(rest) if r.is_heading), len(rest))
            st.reorder(meta, rest[:first_heading] + freed + rest[first_heading:])
            meta.panels = [p for p in meta.panels if p.id != panel_id]

        self._discrete("Delete input group", apply)

    def _add_panel(self) -> None:
        meta = self._meta()
        taken = {p.id for p in meta.panels}
        n = 1
        while f"panel_{n}" in taken:
            n += 1
        panel_id = f"panel_{n}"
        used = sum(1 for p in meta.panels if p.kind == "inputs")
        # Opened, so the heading can be typed straight away.
        self._selected = self._current = _panel_key(panel_id)
        self._discrete(
            "Add input group",
            lambda: meta.panels.append(SubgraphPanel(id=panel_id, kind="inputs", label=f"Group {used + 1}")),
        )

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
