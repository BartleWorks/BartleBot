"""The small widgets subgraph authoring adds to the canvas.

``CrumbBar`` is the overlay that says where the canvas is -- Focus Mode on a region, a
subgraph node's body viewed in Run mode, a definition open as a document, or a template
draft -- with the path back out, and in a definition or a draft the verbs that save it. ``ViewFrame`` is the border
round the viewport that goes with it. ``SubgraphMetaDialog`` asks for a name,
description and categories when a region becomes a definition, or a definition is saved
as a new one, and for the first two when one is renamed from the library.
``LinkInputDialog`` links one node param to an existing input of a compatible type, a
promoted node's param, or a new input. All plain Qt; the policy they drive lives in
``BotCanvas`` and the controller's document stack.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QRegion
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import SubgraphInput
from bartlebot.gui.canvas.call_node_item import canvas_edge_color
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.theming.themed import ThemedMixin, set_style_sheet
from bartlebot.gui.widgets.buttons.action_button import ActionButton, ActionStyle
from bartlebot.gui.widgets.buttons.icon_button import IconButton, IconTint
from bartlebot.gui.widgets.layout.dialog_footer import dialog_footer
from bartlebot.gui.widgets.pickers.tag_picker import SUBGRAPH_VOCABULARY
from bartlebot.gui.widgets.pills.tags_field import TagsField
from bartlebot.nodes.param_schema import narrowest_type

#: The combo entry that means "declare a new input" rather than bind to one.
NEW_INPUT = "— New input —"

#: The crumb bar's link glyph, the two squares of a subgraph node's badge.
_GLYPH_PX = 14

#: The ViewFrame's border: double for a subgraph, single for a focused region.
_DOUBLE_BORDER_PX = 6
_SINGLE_BORDER_PX = 3


@dataclass(frozen=True)
class Crumb:
    """One segment of the crumb bar's path. ``region_color`` is a palette slot: a focused region's segment wears it.

    ``text`` is the name of the thing, which the bar cuts short when it is long
    (:func:`elided_name`). ``prefix`` is what the bar says before it ("Template draft · ")
    and is never cut.
    """

    text: str
    region_color: int | None = None
    prefix: str = ""

    @property
    def full(self) -> str:
        """The segment in full: what its tooltip says, and what ``CrumbBar.text`` joins."""
        return f"{self.prefix}{self.text}"

    @property
    def shown(self) -> str:
        """The segment as the bar draws it, a long name cut short."""
        return f"{self.prefix}{elided_name(self.text)}"


#: How many characters of a name the bar shows. It sits over the canvas, and every
#: character it grows by hides the graph.
CRUMB_NAME_LIMIT = 25


def elided_name(name: str, limit: int = CRUMB_NAME_LIMIT) -> str:
    """*name*, or its first ``limit - 1`` characters and an ellipsis when it is longer than *limit*."""
    return name if len(name) <= limit else f"{name[: limit - 1].rstrip()}\u2026"


class CrumbMode(Enum):
    """The four things the crumb bar can be saying.

    ``FOCUS`` is Focus Mode on a region of a bot: a view filter, in the region's own
    color, with a single-line frame. ``VIEW`` is a subgraph node's body in Run mode,
    read-only. ``DEFINITION`` is a definition open as a document, with the verbs that
    end the visit. Those two wear the accent, the link glyph and a double-line frame,
    which the call node's double side bars echo: double means shared.

    ``TEMPLATE`` is a template draft (dev mode only): a whole bot, so no frame and no
    link glyph -- a template is copied, never linked -- but the accent bar stays up the
    whole time, with *Save draft* and *Release…*, so a draft is never mistaken for a bot.
    """

    FOCUS = "focus"
    VIEW = "view"
    DEFINITION = "definition"
    TEMPLATE = "template"


class _LinkGlyph(QWidget):
    """The two overlapping squares of a subgraph node's link badge, in the crumb's text color.

    Read from THEME at paint time, so it holds nothing a theme switch could stale.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(_GLYPH_PX, _GLYPH_PX)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sq = _GLYPH_PX * 0.62
        p.setPen(QPen(THEME.qcolor("pill_text"), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(1, _GLYPH_PX - sq - 1, sq, sq), 2, 2)
        p.drawRoundedRect(QRectF(_GLYPH_PX - sq - 1, 1, sq, sq), 2, 2)
        p.end()


class _Segment(QLabel):
    """A path segment; an earlier one is a way back to it."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event) -> None:
        if self.cursor().shape() == Qt.CursorShape.PointingHandCursor:
            self.clicked.emit()
            return
        super().mousePressEvent(event)


class CrumbBar(ThemedMixin, QWidget):
    """The one overlay that says where the canvas is, and the way back out, pinned over the view.

    A path of segments -- the documents under the open one, the open one, then any
    subgraph node opened or region focused inside it -- and any earlier segment can be
    clicked to step back to it (``segment_clicked`` carries its index). The close
    button steps out one level. In a definition it carries the three verbs that save
    it: *Save* is the one this surface expects (``PRIMARY``), *Save as…* and, for a
    modified Official one, *Revert*. Their labels are short on purpose, since the bar sits
    over the canvas and every character it grows by hides the graph.

    The bar only draws; which segments, and what a click does, is ``BotCanvas``'s.
    """

    segment_clicked = pyqtSignal(int)
    close_requested = pyqtSignal()
    save_requested = pyqtSignal()
    save_as_requested = pyqtSignal()
    revert_requested = pyqtSignal()
    release_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._mode = CrumbMode.FOCUS
        self._color_slot: int | None = None
        self._segments: list[Crumb] = []
        self._segment_labels: list[_Segment] = []
        self._seps: list[QLabel] = []

        self._glyph = _LinkGlyph()
        self._path = QWidget()
        self._path.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._path_row = QHBoxLayout(self._path)
        self._path_row.setContentsMargins(0, 0, 0, 0)
        self._path_row.setSpacing(6)
        self._hint_lbl = QLabel()
        self._save_btn = ActionButton("save_done", "Save", style=ActionStyle.PRIMARY, fill=False, on_accent=True)
        self._save_btn.setToolTip("Save this subgraph to the library")
        self._save_as_btn = ActionButton("save_as", "Save as…", fill=False)
        self._save_as_btn.setToolTip("Save a copy as a new subgraph, and go on editing the copy")
        self._revert_btn = ActionButton("revert", "Revert", fill=False)
        self._revert_btn.setToolTip("Revert to the Official version of this subgraph")
        self._release_btn = ActionButton("upload", "Release…", fill=False)
        self._release_btn.setToolTip("Release the saved draft as the template's next version, which New Bot offers")
        self._close_btn = IconButton("dismiss", tooltip="Step out", size=22, icon_size=12, tint=IconTint.ON_ACCENT)
        self._save_btn.clicked.connect(self.save_requested)
        self._save_as_btn.clicked.connect(self.save_as_requested)
        self._revert_btn.clicked.connect(self.revert_requested)
        self._release_btn.clicked.connect(self.release_requested)
        self._close_btn.clicked.connect(self.close_requested)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 5, 6, 5)
        row.setSpacing(8)
        row.addWidget(self._glyph)
        row.addWidget(self._path)
        row.addWidget(self._hint_lbl)
        row.addWidget(self._save_btn)
        row.addWidget(self._save_as_btn)
        row.addWidget(self._revert_btn)
        row.addWidget(self._release_btn)
        row.addWidget(self._close_btn)
        self.show_state(CrumbMode.FOCUS, [])

    def show_state(
        self,
        mode: CrumbMode,
        segments: list[Crumb],
        *,
        hint: str = "",
        color_slot: int | None = None,
        can_revert: bool = False,
        close_tip: str = "Step out",
    ) -> None:
        """Draw ``segments`` in ``mode``. ``color_slot`` is the region color a ``FOCUS`` bar is painted in."""
        self._mode = mode
        self._color_slot = color_slot if mode == CrumbMode.FOCUS else None
        self._segments = list(segments)
        self._rebuild_path()
        self._glyph.setVisible(mode in (CrumbMode.VIEW, CrumbMode.DEFINITION))
        self._hint_lbl.setText(hint)
        self._hint_lbl.setVisible(bool(hint))
        editing = mode == CrumbMode.DEFINITION
        drafting = mode == CrumbMode.TEMPLATE
        self._save_btn.setText("Save draft" if drafting else "Save")
        self._save_btn.setToolTip(
            "Save the draft. Nothing reaches New Bot until it is released"
            if drafting
            else "Save this subgraph to the library"
        )
        self._save_btn.setVisible(editing or drafting)
        self._save_as_btn.setVisible(editing)
        self._revert_btn.setVisible(editing and can_revert)
        self._release_btn.setVisible(drafting)
        self._close_btn.setToolTip(close_tip)
        self.refresh_theme_styles()
        # The theme pass has just restyled the new segments, bold ones included, and a
        # label only takes its style sheet's font in when it is polished, which is later.
        # Measured before that, the bar came out narrower than its path, which then ran
        # on under the buttons until something else happened to measure it again.
        for label in (*self._segment_labels, *self._seps, self._hint_lbl):
            label.ensurePolished()
            label.updateGeometry()
        self._path_row.activate()
        self.layout().activate()
        self.adjustSize()

    def _rebuild_path(self) -> None:
        while self._path_row.count():
            item = self._path_row.takeAt(0)
            if item.widget() is not None:
                # Hidden first: deleteLater leaves it painting until the loop comes round.
                item.widget().hide()
                item.widget().deleteLater()
        self._segment_labels = []
        self._seps = []
        last = len(self._segments) - 1
        for i, crumb in enumerate(self._segments):
            if i:
                sep = QLabel("›", self._path)
                self._path_row.addWidget(sep)
                # Shown by hand: a layout shows a new child of a visible parent only on
                # a queued event, after this bar has measured itself without it.
                sep.show()
                self._seps.append(sep)
            seg = _Segment(crumb.shown, self._path)
            if i < last:
                seg.setCursor(Qt.CursorShape.PointingHandCursor)
                seg.setToolTip(f"Back to {crumb.full}")
                seg.clicked.connect(lambda i=i: self.segment_clicked.emit(i))
            elif crumb.shown != crumb.full:
                seg.setToolTip(crumb.full)
            self._path_row.addWidget(seg)
            seg.show()
            self._segment_labels.append(seg)

    @property
    def mode(self) -> CrumbMode:
        return self._mode

    @property
    def text(self) -> str:
        """The path as one string, ``A › B › C``, every name in full."""
        return " › ".join(c.full for c in self._segments)

    @property
    def shown_text(self) -> str:
        """The path as drawn: a long name cut short."""
        return " › ".join(seg.text() for seg in self._segment_labels)

    @property
    def hint_text(self) -> str:
        return self._hint_lbl.text()

    @property
    def segment_labels(self) -> list[QLabel]:
        return list(self._segment_labels)

    @property
    def glyph_visible(self) -> bool:
        return self._glyph.isVisibleTo(self)

    @property
    def save_button(self) -> ActionButton:
        return self._save_btn

    @property
    def save_as_button(self) -> ActionButton:
        return self._save_as_btn

    @property
    def revert_button(self) -> ActionButton:
        return self._revert_btn

    @property
    def release_button(self) -> ActionButton:
        return self._release_btn

    @property
    def close_button(self) -> IconButton:
        return self._close_btn

    def background(self) -> QColor:
        """The bar's own color: the focused region's, or the accent for a subgraph."""
        if self._color_slot is not None:
            return THEME.region_color(self._color_slot)
        return THEME.qcolor("accent")

    def apply_theme_styles(self) -> None:
        bg = self.background()
        # An edge in the canvas's own text color, not a self-colored one: the bar floats
        # over the graph, and an Entry/Exit pill in the same accent otherwise merges into it.
        edge = canvas_edge_color()
        edge_rgba = f"rgba({edge.red()}, {edge.green()}, {edge.blue()}, {edge.alpha()})"
        set_style_sheet(
            self, f"CrumbBar {{ background-color: {bg.name()}; border: 1px solid {edge_rgba}; border-radius: 6px; }}"
        )
        set_style_sheet(self._path, "background: transparent;")
        text = (
            f"border: none; background: transparent; color: {THEME.pill_text};"
            f" font-size: {THEME.font_body_px}px; padding: 0;"
        )
        faint = QColor(THEME.pill_text)
        faint.setAlpha(190)
        faint_rgba = f"rgba({faint.red()}, {faint.green()}, {faint.blue()}, {faint.alpha()})"
        last = len(self._segment_labels) - 1
        for i, seg in enumerate(self._segment_labels):
            slot = self._segments[i].region_color
            if slot is not None and self._mode != CrumbMode.FOCUS:
                # A region focused inside a subgraph: its segment wears the region's
                # color, the one thing on the bar that says a filter is on as well.
                region = THEME.region_color(slot).name()
                set_style_sheet(
                    seg,
                    f"border: none; color: {THEME.pill_text}; font-size: {THEME.font_body_px}px; font-weight: 600;"
                    f" background-color: {region}; border-radius: 4px; padding: 1px 6px;",
                )
            elif i < last:
                set_style_sheet(seg, text + " font-weight: 500; text-decoration: underline;")
            else:
                set_style_sheet(seg, text + " font-weight: 600;")
        for sep in self._seps:
            set_style_sheet(
                sep, f"border: none; background: transparent; color: {faint_rgba}; font-size: {THEME.font_body_px}px;"
            )
        set_style_sheet(
            self._hint_lbl,
            f"border: none; background: transparent; color: {faint_rgba}; font-size: {THEME.font_small_px}px;",
        )


class ViewFrame(ThemedMixin, QWidget):
    """The border round the whole viewport while the canvas is anywhere but the plain bot.

    Single, in a region's color, for Focus Mode; double, in the accent, for a
    subgraph -- its body viewed in Run mode, or its definition open. It holds the
    palette slot, not the color, so a theme switch repaints it.

    It must never take a click meant for the canvas, and it is kept out of the way
    twice. It is transparent to the mouse, which steers Qt's own hit test, and it is
    masked to its border, which is the only thing that stops Windows when the frame
    has a native window of its own. A native widget gets the click straight from
    Windows, wherever its HWND is, and the transparency is never consulted: that was
    #126, a canvas that looked read-only in Edit mode (see ``native_widgets``).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._slot: int | None = None
        self._double = False
        self.refresh_theme_styles()
        self._apply_mask()

    def set_region(self, slot: int) -> None:
        self._slot, self._double = slot, False
        self.refresh_theme_styles()
        self._apply_mask()

    def set_shared(self) -> None:
        self._slot, self._double = None, True
        self.refresh_theme_styles()
        self._apply_mask()

    @property
    def is_double(self) -> bool:
        return self._double

    @property
    def border_px(self) -> int:
        """How wide the drawn border is, and so how much of the frame its mask keeps."""
        return _DOUBLE_BORDER_PX if self._double else _SINGLE_BORDER_PX

    def color(self) -> QColor:
        return THEME.region_color(self._slot) if self._slot is not None else THEME.qcolor("accent")

    def apply_theme_styles(self) -> None:
        border = (
            f"{self.border_px}px double {self.color().name()}"
            if self._double
            else f"{self.border_px}px solid {self.color().name()}"
        )
        set_style_sheet(self, f"ViewFrame {{ border: {border}; border-radius: 2px; background: transparent; }}")

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._apply_mask()

    def _apply_mask(self) -> None:
        """Keep only the border: everything inside it belongs to the canvas."""
        outer = self.rect()
        w = self.border_px
        self.setMask(QRegion(outer).subtracted(QRegion(outer.adjusted(w, w, -w, -w))))


class SubgraphMetaDialog(QDialog):
    """Name and description for a definition: one about to be created, or one being renamed.

    ``categories`` adds a Category row (#172), starting with those: a definition about to
    be created is the moment to say what it does, since every list of subgraphs filters
    by it. ``None`` leaves the row out, as Rename does; the open definition's header
    edits its categories.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        name: str = "",
        description: str = "",
        categories: list[str] | None = None,
        title: str | None = None,
        commit_text: str = "Save",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title or "Save Region as Reusable Subgraph")
        self._name = QLineEdit(name)
        self._name.setPlaceholderText("What this subgraph does, in a few words")
        self._description = QPlainTextEdit(description)
        self._description.setPlaceholderText("When to use it, and what it expects of the bot around it (optional)")
        self._description.setFixedHeight(80)
        form = QFormLayout()
        form.addRow("Name", self._name)
        form.addRow("Description", self._description)
        self._categories: TagsField | None = None
        if categories is not None:
            self._categories = TagsField(self, vocabulary=SUBGRAPH_VOCABULARY)
            self._categories.set_editable(True)
            self._categories.set_tags(categories)
            form.addRow("Category", self._categories)
        footer = dialog_footer(self, commit_text=commit_text)
        self._ok = footer.commit
        self._name.textChanged.connect(lambda t: self._ok.setEnabled(bool(t.strip())))
        self._ok.setEnabled(bool(name.strip()))
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(footer.layout)
        self.resize(420, self.sizeHint().height())

    @property
    def name(self) -> str:
        return self._name.text().strip()

    @property
    def description(self) -> str | None:
        text = self._description.toPlainText().strip()
        return text or None

    @property
    def categories_field(self) -> TagsField | None:
        return self._categories

    @property
    def categories(self) -> list[str] | None:
        """The categories chosen, in vocabulary order; ``None`` when the dialog has no Category row."""
        return self._categories.tags() if self._categories is not None else None


def compatible_inputs(inputs: list[SubgraphInput], param_type: str) -> list[SubgraphInput]:
    """The inputs a param of ``param_type`` can share a value with: one type narrows the other."""
    return [i for i in inputs if narrowest_type([i.type, param_type]) is not None]


class LinkInputDialog(QDialog):
    """Link one node param to a subgraph input of the definition being edited.

    Offers the existing inputs whose type is compatible with the param's (one narrows
    the other, so a ``zone`` and a ``nav_zone`` can share), plus *New input*, which
    asks for a name, label and help; the param's current value becomes the new
    input's default. The dialog only collects the choice -- ``choice`` names an
    existing input, or ``new_input()`` builds the declared one -- and the canvas
    applies it, so the same code path serves a test that never opens a dialog.
    """

    def __init__(
        self,
        inputs: list[SubgraphInput],
        *,
        param_key: str,
        param_type: str,
        param_label: str,
        current_value: object,
        shared: list[tuple[str, str, str]] | None = None,
        groups: list[tuple[str, str]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Link to Subgraph Input")
        self._inputs = inputs
        self._key = param_key
        self._type = param_type
        self._value = current_value
        self._compatible = compatible_inputs(inputs, param_type)

        self._combo = QComboBox()
        for inp in self._compatible:
            self._combo.addItem(f"{inp.label or inp.name} ({inp.type})", ("input", inp.name))
        # A promoted node's params can be linked to too: (label, group id, key), already
        # filtered to the compatible ones by the caller.
        for label, panel_id, key in shared or []:
            self._combo.addItem(f"{label} (input group)", ("shared", panel_id, key))
        self._combo.addItem(NEW_INPUT, None)
        self._name = QLineEdit(param_key)
        self._label = QLineEdit(param_label)
        self._help = QLineEdit()
        self._help.setPlaceholderText("Shown as the input's tooltip (optional)")
        # (group id, label) of the input groups a new input can be listed under.
        self._group = QComboBox()
        self._group.addItem("None", None)
        for gid, glabel in groups or []:
            self._group.addItem(glabel, gid)

        form = QFormLayout()
        form.addRow(QLabel(f"Link <b>{param_label}</b> ({param_type}) to:"))
        form.addRow("Input", self._combo)
        form.addRow("Name", self._name)
        form.addRow("Label", self._label)
        form.addRow("Help", self._help)
        form.addRow("Input group", self._group)
        footer = dialog_footer(self, on_commit=self.accept)
        self._ok = footer.commit
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(footer.layout)
        self._combo.currentIndexChanged.connect(self._on_choice_changed)
        self._name.textChanged.connect(self._validate)
        self._combo.setCurrentIndex(0)
        self._on_choice_changed(self._combo.currentIndex())
        # Room for an input's label and help without the fields scrolling sideways.
        self.resize(max(self.sizeHint().width(), 520), self.sizeHint().height())

    @property
    def choice(self) -> str | None:
        """The existing input's name, or None for a new one or a promoted node's param."""
        data = self._combo.currentData()
        return data[1] if data and data[0] == "input" else None

    @property
    def shared_choice(self) -> tuple[str, str] | None:
        """``(group id, key)`` of the promoted node's param chosen, or None."""
        data = self._combo.currentData()
        return (data[1], data[2]) if data and data[0] == "shared" else None

    def select(self, text_prefix: str) -> None:
        """Pick the first entry whose text starts with *text_prefix* -- for tests."""
        for i in range(self._combo.count()):
            if self._combo.itemText(i).startswith(text_prefix):
                self._combo.setCurrentIndex(i)
                return

    def select_new(self) -> None:
        self._combo.setCurrentIndex(self._combo.count() - 1)

    def set_new_input(self, name: str, label: str = "", help_text: str = "") -> None:
        self.select_new()
        self._name.setText(name)
        self._label.setText(label)
        self._help.setText(help_text)

    def _on_choice_changed(self, _index: int) -> None:
        new = self._combo.currentData() is None
        for w in (self._name, self._label, self._help, self._group):
            w.setEnabled(new)
        self._validate()

    def _validate(self, *_args) -> None:
        if self._combo.currentData() is not None:
            self._ok.setEnabled(True)
            return
        name = self._name.text().strip()
        taken = {i.name for i in self._inputs}
        self._ok.setEnabled(bool(name) and name not in taken and "." not in name)

    def new_input(self) -> SubgraphInput:
        """The input the *New input* fields describe, unbound; the param's value is its default."""
        return SubgraphInput(
            name=self._name.text().strip(),
            label=self._label.text().strip() or None,
            type=self._type,
            help=self._help.text().strip() or None,
            default=self._value,
            panel=self._group.currentData(),
        )

    def set_group(self, group_id: str | None) -> None:
        """Pick the new input's group by id -- for tests."""
        idx = self._group.findData(group_id)
        self._group.setCurrentIndex(idx if idx >= 0 else 0)
