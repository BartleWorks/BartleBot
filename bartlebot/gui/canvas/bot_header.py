"""Bar B — the header above the bot's graph.

Bar B sits above the graph, so it owns the *bot*: its name, version, file, author
and tags, plus the run controls and the run status.  Bar A sits above the game
windows and owns the *clients*.  Name and status used to live in Bar A essentially
by accident, which meant they vanished with the game view.

The metadata rows are the ones ``BotInfoSection`` used to hold, lifted rather than
rewritten: ``_Swappable`` and ``_TagsField`` are that section's mechanism, moved
here whole.  Everything else the old Bot tab carried is gone —

* **Start** (``start_location``) was never read by the engine, and ``go_to_world``
  plus the bot templates make it moot;
* **Initial State** and **Recovery State** are badges on the nodes themselves,
  so the labels only ever restated the graph;
* the **full description** moved to the top of Setup (read) and Options (edit),
  because eliding it here would hide the ``REQUIREMENTS:`` blocks four bots carry.

The header does not own the buttons.  ``BotCanvas`` builds, wires and *groups* them,
and hands them in as two opaque widgets — the transport, and everything that belongs
at the right-hand end — so this module holds no run semantics at all.  The right-hand
grouping is the caller's because which of those clusters exists depends on the mode,
which is knowledge the header does not have and should not acquire.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot_tags import BOT_TAGS, TAG_GROUPS, tag_group
from bartlebot.gui.sidebar.sidebar_section import value_label
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.flow_layout import flow_container
from bartlebot.gui.widgets.tag_picker import TagPickerButton
from bartlebot.gui.widgets.tag_pill import NeutralPill, TagChip, TagPill

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_PLACEHOLDER = "—"
_NO_BOT = "No bot loaded"
_ZERO_ELAPSED = "00:00:00"
# Every word ``BotCanvas._emit_bot_status`` can hand to ``set_status``.  The status
# slot is sized for the widest of them so that the transport buttons do not shuffle
# sideways when the run state changes; see ``_size_status_slots``.
_STATUS_WORDS = ("Editing", "Idle", "Running", "Paused")
# QLabel adds a pixel of its own around the text, so a hint of exactly the advance
# is one pixel short and the label elides a character off text that fits.
_LABEL_PADDING_PX = 2

# Grid columns.  The run controls are the middle one so that they centre.
_COL_META = 0
_COL_RUN = 1
_COL_TOOLS = 2


def _centred(w: QWidget) -> QWidget:
    """*w* in a host that centres it vertically and lets it fill horizontally.

    ``QStackedLayout`` has no alignment of its own -- it ignores both
    ``setAlignment`` overloads -- and gives a page its natural height at the *top* of
    the cell.  That is invisible for a page as tall as the cell, and wrong for one that
    is not: the version pill is 13px in a 22px slot (the slot being as tall as the
    editor it is not showing, which is what keeps the bar's height off the mode), so it
    sat four and a half pixels above the bot name beside it.

    A one-child box layout is the alignment the stack will not do.
    """
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
    return host


def _clamped_width(widget: QWidget, width: int) -> int:
    """*width* held to whatever explicit bounds *widget* carries.

    A hint is only ever advisory, and normally it is the parent layout that holds it to
    the widget's own minimum and maximum.  Reporting a page's width up through
    :class:`_Swappable` steps over that layout, so the clamping has to be done here or
    the bounds are silently dropped: the bot name's 240px minimum stopped applying, and
    the version field's ``setFixedWidth(60)`` lost to a ``QLineEdit``'s generous 210px
    natural hint.
    """
    return max(widget.minimumWidth(), min(width, widget.maximumWidth()))


class _Swappable(QWidget):
    """Container showing its label, its editor, or neither -- at one fixed height.

    A ``QStackedLayout`` rather than a box holding both, which is the whole point:
    a stack reserves ``max(label, editor)`` in *every* state, so swapping one for
    the other cannot change the header's height, and a mode switch cannot move the
    bar.  A ``QVBoxLayout`` could only collapse the hidden child to zero, which
    made the bar a different height in each mode -- and worse, a *third* height
    while the swap was half done, because ``setVisible`` on two children is two
    steps and the container asked for the sum of both in between.  The canvas laid
    the bar out at that intermediate height, and painted it there; leaving Edit with
    no tags it then stayed 18px too tall until an unrelated resize.  See
    ``_set_label_mode``.

    The cost is a Run-mode bar as tall as the editors it is not showing.  That is
    the trade: a bar that is 14px taller than it needs to be reads as deliberate,
    and a bar that changes height twice on the way between two modes does not.

    Three states, not two, because the version pill is hidden outright when the bot
    has no version -- a stack has no "neither" of its own, so the container hides.
    """

    def __init__(self, label_w: QWidget, edit_w: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label_w = label_w
        self._edit_w = edit_w
        self._showing_label = True
        lay = QStackedLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._label_page = _centred(label_w)
        self._edit_page = _centred(edit_w)
        lay.addWidget(self._label_page)
        lay.addWidget(self._edit_page)
        self._stack = lay
        # Not ``show_label``: this widget has no parent yet, and ``setVisible(True)``
        # on a parentless widget shows it as a top-level window.  The layout it is
        # about to be added to is what makes it visible.
        lay.setCurrentWidget(self._label_page)

    def show_label(self) -> None:
        """Show the read-only label, and be visible."""
        self._showing_label = True
        self._stack.setCurrentWidget(self._label_page)
        self.setVisible(True)
        self.updateGeometry()

    def show_editor(self) -> None:
        """Show the editor, and be visible."""
        self._showing_label = False
        self._stack.setCurrentWidget(self._edit_page)
        self.setVisible(True)
        self.updateGeometry()

    def hide_both(self) -> None:
        """Take no space at all.  For the version pill on a bot with no version."""
        self.setVisible(False)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """As wide as the page on show, and as tall as the taller of the two.

        The height is the whole reason this is a stack, and it has to come from both
        pages or the bar moves with the mode.  The *width* must not, and a plain
        ``QStackedLayout`` gives no way to ask for one without the other: unlike a box
        layout, which skips a hidden child, a stack reports the widest of *every* page
        whether or not it is showing.  So in Run mode each slot claimed the width of the
        editor it was not showing -- the name's 240px minimum, the author field's fixed
        180 -- which pushed the version pill and the tag pills right by that much and
        stretched the pill itself across a slot far wider than its text.
        """
        current = self._label_w if self._showing_label else self._edit_w
        width = _clamped_width(current, current.sizeHint().width())
        height = max(self._label_w.sizeHint().height(), self._edit_w.sizeHint().height())
        return QSize(width, height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """The same split as ``sizeHint``: width from the page on show, height from both.

        Needed as well as the hint, because a minimum is what a layout honours when it
        is short of room -- and the stack's own minimum carries the hidden page's width
        for exactly the same reason.
        """
        current = self._label_w if self._showing_label else self._edit_w
        width = _clamped_width(current, current.minimumSizeHint().width())
        height = max(self._label_w.minimumSizeHint().height(), self._edit_w.minimumSizeHint().height())
        return QSize(width, height)


def _let_shrink(w: QWidget) -> None:
    """Let *w* be narrower than its contents want, so it cannot widen its column.

    The metadata column is capped at its share of the bar by the equal stretch on
    the two side columns -- but a stretch factor only divides what is *left over*
    after every column's minimum, and a column's minimum is its contents'.  In Edit
    mode the metadata grows two things that cannot shrink at all: the author field
    is a fixed-width ``QLineEdit``, and the tag picker buttons are one atomic item
    in the pill flow.  Together they claimed about 440px, which is more than half of
    a bar under ~1030px wide -- so on a narrow bar the metadata pushed the centre
    column, and the transport moved right by the overflow when the mode changed.
    That is exactly the drift the centre column exists to prevent.

    ``Ignored`` is what drops the minimum: the column is then sized by stretch alone
    and the metadata elides and wraps inside whatever it is given, which is how this
    bar is meant to degrade.  It is safe here and not on ``_ElidedLabel`` because a
    grid column carries the stretch; a widget in a row of stretch-0 siblings that
    asks for nothing gets nothing.
    """
    policy = w.sizePolicy()
    policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
    w.setSizePolicy(policy)


class _ElidedLabel(ThemedMixin, QLabel):
    """A one-line label that shortens its text rather than widening the bar.

    The author and the filename share line 1 with the bot name, and either of them
    long enough to push the rest off the end would make the whole header claim a
    minimum width the canvas has to honour.  ``QLabel`` has no elide mode of its own,
    so the text is elided against the width Qt actually gave us, on every resize.
    """

    def __init__(self, text: str = "", *, color: str = "text_subtle", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = text
        self._color_token = color
        # Maximum, not Ignored: the label asks for the width its full text wants and
        # settles for less. Ignored asks for nothing, which in a row of stretch-0
        # widgets is exactly what it gets -- the author line vanished that way.
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.refresh_theme_styles()
        self._apply_elide()

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        self._full = text
        self._apply_elide()
        self.updateGeometry()

    def full_text(self) -> str:
        """The unelided text, which is what a test should assert against."""
        return self._full

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Sized by the *full* text, never the elided one.

        QLabel derives its hint from ``text()``, which here is already shortened --
        so a label that once elided would keep asking for the shorter width and never
        grow back when the window did.

        The margins are added back in because ``set_baseline_shift`` puts the label's
        vertical nudge there: a hint that ignored them would have the layout hand the
        label its text height exactly, and the nudge would then squeeze the text
        rather than move it.
        """
        metrics = QFontMetrics(self.font())
        pad = self.contentsMargins()
        return QSize(
            metrics.horizontalAdvance(self._full) + _LABEL_PADDING_PX + pad.left() + pad.right(),
            metrics.height() + pad.top() + pad.bottom(),
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Zero width: eliding is only possible for a label allowed to be narrow."""
        pad = self.contentsMargins()
        return QSize(0, QFontMetrics(self.font()).height() + pad.top() + pad.bottom())

    def set_baseline_shift(self, pixels: int) -> None:
        """Move the text *pixels* down (negative: up) without moving the box's centre.

        Padding one side and letting the layout re-centre the taller box moves the
        text by half of what was added, so the padding is twice the shift wanted.
        """
        pad = 2 * pixels
        self.setContentsMargins(0, max(pad, 0), 0, max(-pad, 0))
        self.updateGeometry()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        metrics = QFontMetrics(self.font())
        pad = self.contentsMargins()
        room = max(0, self.width() - pad.left() - pad.right())
        super().setText(metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, room))
        self.setToolTip(self._full if super().text() != self._full else "")

    def apply_theme_styles(self) -> None:
        self.setStyleSheet(f"color: {getattr(THEME, self._color_token)}; font-size: {THEME.font_small_px}px;")


class _TagsField(QWidget):
    """The bot's tags as pills, and one button per group that edits them.

    Unlike the other fields this is not a label swapped for an editor. The pills are
    the value in both modes — what changes is whether they carry a ✕ and whether the
    picker buttons are shown — because a tag list reads the same either way and building
    a second, differently-shaped view of it just to be read-only is how the two drift.

    The buttons are captioned "Rewards" / "Activity" with no count, unlike the
    library's, where the button *is* the filter and its text has to report the
    selection.  Here the chips beside them already do that.  Nor "Add Rewards…": the
    popup toggles, so the same button takes a tag off again, and a caption saying
    "Add" mislabels half of what it does.
    """

    tags_changed = pyqtSignal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tags: list[str] = []
        self._editable = False

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self._empty_lbl = value_label()
        self._empty_lbl.setText(_PLACEHOLDER)
        root.addWidget(self._empty_lbl)

        # Centred, because the field reserves the editable form's height in both modes
        # -- that is what keeps the bar's height off the mode -- so in Run mode the
        # pills are shorter than the rect they are handed, and the grid row is taller
        # still while the button clusters span it.  A flow hangs its rows from the top
        # of what it was given, which put the pills above the row's own centre.
        self._pill_host, self._pill_layout = flow_container(centre_rows=True)
        root.addWidget(self._pill_host)

        # The picker buttons ride *inside* the pill flow, as one item, rather than in a
        # flow of their own beside it.  Two flows side by side each wrap on their own
        # share of the width, so a row short of a hundred pixels came back two rows
        # of chips *and* two rows of buttons; one flow wraps once, and the buttons
        # move down whole rather than splitting.
        self._buttons = QWidget()
        button_layout = QHBoxLayout(self._buttons)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(4)
        self._pickers: dict[str, TagPickerButton] = {}
        for group in TAG_GROUPS:
            btn = TagPickerButton(title=group, group=group, caption=group)
            btn.setToolTip(f"Choose which {group.lower()} tags this bot carries")
            btn.selection_changed.connect(self._on_picker_changed)
            self._pickers[group] = btn
            button_layout.addWidget(btn)
        self._pill_layout.addWidget(self._buttons)

        self._rebuild()

    # -- public API ----------------------------------------------------

    def tags(self) -> list[str]:
        """The selected tags, in ``BOT_TAGS`` order."""
        return list(self._tags)

    def set_tags(self, tags: list[str]) -> None:
        """Show *tags* without emitting ``tags_changed``."""
        self._tags = [t for t in BOT_TAGS if t in set(tags)]
        for group, picker in self._pickers.items():
            picker.set_selection([t for t in self._tags if tag_group(t) == group])
        self._rebuild()

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._rebuild()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """As tall as the editable form, whichever form is actually on show.

        The picker buttons are a pixel taller than the pills and the placeholder
        they replace, so without this floor the whole bar was a pixel taller in Edit
        mode -- and the canvas resolves the bar's height from a hint that is one
        event-loop pass stale, its own ``LayoutRequest`` arriving before the bar's.  So
        it measured the mode being *left*, and the bar's later re-measure never
        propagated back up: returning from Edit left the bar a pixel too tall and the
        graph a pixel low until an unrelated resize.

        A constant height is the fix rather than a re-measure after the fact -- a
        height that never changes cannot be read at the wrong moment, and forcing the
        re-measure was not even reliable, landing on the right pixel only some of the
        time.

        The floor is declared twice, here and as a ``setMinimumHeight`` in ``_rebuild``,
        and both are load-bearing.  ``QWidgetItem`` answers ``heightForWidth`` from this
        widget's *layout* rather than from the widget, and the pill flow inside reports
        height-for-width -- so a parent sizing the field reads straight past this hint,
        and only the minimum reaches it.  The hint is what keeps ``height()`` and
        ``sizeHint()`` in agreement, which is how the tag row tells a field it should
        *centre* from one it should stretch.

        Wrapping still grows the field past this floor, which is the one height change
        Edit mode genuinely makes: the picker buttons join the flow and a heavily
        tagged bot's tag row takes a second line.
        """
        base = super().sizeHint()
        return QSize(base.width(), max(base.height(), self._floor()))

    # -- internals -----------------------------------------------------

    def _floor(self) -> int:
        """The height the editable form needs, reserved in both modes.

        Read live rather than cached at construction: the buttons are sized by the
        theme's font, and a theme switch changes it.
        """
        return self._buttons.sizeHint().height()

    def _on_picker_changed(self, _keys: list) -> None:
        self._tags = [t for t in BOT_TAGS if any(t in p.tags() for p in self._pickers.values())]
        self._rebuild()
        self.tags_changed.emit(list(self._tags))

    def _remove(self, tag: str) -> None:
        self.set_tags([t for t in self._tags if t != tag])
        self.tags_changed.emit(list(self._tags))

    def _rebuild(self) -> None:
        # The picker buttons live in this layout too and are long-lived, so they come out
        # before the sweep and go back in after it, always last.
        self._pill_layout.removeWidget(self._buttons)
        while (item := self._pill_layout.takeAt(0)) is not None:
            if w := item.widget():
                w.hide()
                w.deleteLater()

        for tag in self._tags:
            if self._editable:
                chip = TagChip(tag)
                chip.removed.connect(self._remove)
                self._pill_layout.addWidget(chip)
            else:
                self._pill_layout.addWidget(TagPill(tag))
        self._pill_layout.addWidget(self._buttons)

        # The placeholder stands in for the pills, not for the whole row: with the Add
        # buttons showing, an untagged bot already reads as untagged.
        self._empty_lbl.setVisible(not self._tags and not self._editable)
        self._pill_host.setVisible(bool(self._tags) or self._editable)
        self._buttons.setVisible(self._editable)
        self._pill_host.updateGeometry()
        # Both halves of the floor ``sizeHint`` documents.  ``minimumHeight`` is the one
        # a wrapping widget's parent actually honours: the pill flow reports
        # height-for-width, and ``QWidgetItem`` answers that from this widget's *layout*
        # rather than from the widget, so the hint alone was read straight past.  Set
        # here rather than once in ``__init__`` because the buttons are sized by the
        # theme's font, and this runs on every rebuild.
        self.setMinimumHeight(self._floor())
        self.updateGeometry()


class BotHeader(ThemedMixin, QWidget):
    """Three columns above the graph; only the leftmost is two rows deep.

    ::

        Name  v1.0 · file.json    00:00:00 [play][stop] * Idle    [fit][edges]  [edit]
        by Author  <tag pills>

    The grouping is by *subject*, which is what makes the bar readable at a glance:
    everything about the bot is one block on the left, the run controls own the
    centre with the clock and the run state either side of them, and the tools that
    act on the *view* sit with the mode toggle on the right.

    The status used to be a caption *beneath* the transport.  That read well on its
    own, but it left the right-hand cluster as the only thing in the bar with an
    empty row under it, so those buttons centred themselves in row 1 and sat visibly
    high.  Stacking nothing is what lets both clusters span the full height and come
    out level with each other; ``_size_status_slots`` is what stops the status word
    dragging the transport off centre now that it shares the row.

    Still a ``QGridLayout`` rather than boxes: the columns have very different
    natural heights -- 32 px buttons against 11 px captions -- and the spanning
    clusters have to centre against the metadata block as a whole.

    Both button clusters are ``BotCanvas``'s; they are placed but never touched.
    """

    metadata_changed = pyqtSignal()

    def __init__(
        self,
        run_controls: QWidget,
        tools: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._suppress = False
        # Which half of every metadata slot is on show.  Run mode is the label side,
        # and the header starts there with no bot loaded.
        self._label_mode = True
        # Last status handed to set_status, replayed on a theme switch: the dot's
        # colour is a THEME value the caller resolved, so this widget cannot
        # re-derive it without being told again.
        self._status = (THEME.text_muted, "Idle", "")

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        # -- Left column, row 1: name, version, file, author ----------
        self._name_lbl = QLabel(_NO_BOT)
        name_font = self._name_lbl.font()
        name_font.setBold(True)
        name_font.setPixelSize(THEME.font_body_px + 1)
        self._name_lbl.setFont(name_font)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Bot name")
        self._name_edit.setFont(name_font)
        # Sized for a name, not for the label it stands in for: a QLineEdit inside a
        # _Swappable takes the container's width, and the container is as wide as
        # whichever of the pair is bigger.
        self._name_edit.setMinimumWidth(240)

        self._version_pill = NeutralPill("")
        self._version_edit = QLineEdit()
        self._version_edit.setPlaceholderText("1.0")
        self._version_edit.setFixedWidth(60)

        # Elided, and the only thing in the row that stretches: a long filename has
        # to shorten rather than shove the run buttons off centre.
        self._file_lbl = _ElidedLabel(color="text_subtle")

        title = QWidget()
        title_row = QHBoxLayout(title)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        self._name_slot = _Swappable(self._name_lbl, self._name_edit)
        self._version_slot = _Swappable(self._version_pill, self._version_edit)
        title_row.addWidget(self._name_slot, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(self._version_slot, 0, Qt.AlignmentFlag.AlignVCenter)
        self._authors_lbl = _ElidedLabel()
        self._authors_edit = QLineEdit()
        self._authors_edit.setPlaceholderText("Author(s)")
        self._authors_edit.setFixedWidth(180)

        # The author rides up here rather than beside the tags, and the reason is the
        # width it takes off the row below.  Measured against the 87 shipped bots at
        # 1600px -- an ordinary window, not a narrow one -- 83 of them wrapped their tag
        # row in Edit mode with the author in it, and 13 without.  At 1400 it is 86
        # against 35, and Run-mode wrapping goes away entirely above 1200.  The tag row
        # is the one that wraps because it is the only thing in the bar that grows with
        # the bot; everything else is fixed or elides, so the author is exactly the
        # 190px the tags were short of.
        #
        # The cost is that this row is now the crowded one: below about 1300px in Edit
        # mode the name field starts shrinking (218px at 1200, 124px at 900) where it
        # held its 240 before.  That is the right way round -- a name field with room
        # for twenty characters instead of thirty still reads, and a bar that grows a
        # row every time you open the editor does not.
        self._authors_slot = _Swappable(self._authors_lbl, self._authors_edit)

        title_row.addWidget(self._file_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(self._authors_slot, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        _let_shrink(title)

        # -- Left column, row 2: tags ---------------------------------
        self._tags_field = _TagsField()

        self._tag_row = QWidget()
        tag_row = QHBoxLayout(self._tag_row)
        tag_row.setContentsMargins(0, 0, 0, 0)
        tag_row.setSpacing(6)
        # Centred rather than stretched.  A box layout hands a stretched child the
        # whole row, and ``FlowLayout`` then lays its rows from the top of what it was
        # given -- so whenever the grid row is taller than the byline itself (the
        # spanning button clusters are what make it so), the pills sat against the
        # row's ceiling with the slack all underneath.
        tag_row.addWidget(self._tags_field, 0, Qt.AlignmentFlag.AlignVCenter)
        tag_row.addStretch(1)
        _let_shrink(self._tag_row)

        # -- Centre column: clock, transport, status — one row --------
        # Always shown, and zeroed rather than hidden while idle.  A clock that
        # appeared on Run would shove the transport sideways at the one moment the
        # user is watching it, and the placeholder says what the field will hold.
        self._elapsed_lbl = QLabel(_ZERO_ELAPSED)
        self._elapsed_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._dot_lbl = QLabel("●")
        self._status_lbl = QLabel("Idle")
        status_font = self._status_lbl.font()
        # Set on the widget rather than through the stylesheet: a stylesheet
        # ``font-size`` never reaches ``QWidget.font()``, so the metrics
        # ``_size_status_slots`` measures with would be the wrong size entirely.
        status_font.setPixelSize(THEME.font_small_px)
        self._status_lbl.setFont(status_font)

        self._status_side = QWidget()
        status_row = QHBoxLayout(self._status_side)
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(6)
        status_row.addWidget(self._dot_lbl)
        status_row.addWidget(self._status_lbl)
        status_row.addStretch(1)

        run = QWidget()
        run_row = QHBoxLayout(run)
        run_row.setContentsMargins(0, 0, 0, 0)
        run_row.setSpacing(8)
        run_row.addWidget(self._elapsed_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        run_row.addWidget(run_controls, 0, Qt.AlignmentFlag.AlignVCenter)
        run_row.addWidget(self._status_side, 0, Qt.AlignmentFlag.AlignVCenter)

        # -- Assemble -------------------------------------------------
        centred = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        grid = QGridLayout(self)
        grid.setContentsMargins(8, 3, 8, 3)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(0)
        grid.addWidget(title, 0, _COL_META)
        grid.addWidget(self._tag_row, 1, _COL_META)
        # Both control clusters span the two metadata rows, which is what puts them on
        # the bar's own vertical centre rather than on the centre of row 1.  Nothing is
        # stacked in either column any more, so they are level with each other -- which
        # they would not be if only one of them spanned.
        grid.addWidget(run, 0, _COL_RUN, 2, 1, centred)
        grid.addWidget(tools, 0, _COL_TOOLS, 2, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # The two side columns take equal stretch, which is the only arrangement that
        # centres the Run button exactly: the centre lands in the middle of the bar
        # only while the columns either side of it are the same width.  Stretch alone
        # is not enough to guarantee that -- it divides what is left after each
        # column's minimum -- which is why both metadata rows went through
        # ``_let_shrink`` above.  The cost is that the metadata is capped at half the
        # bar however much of it there is -- so a long filename elides, and in Edit
        # mode a heavily tagged bot wraps its tag row.  Both degrade quietly; a Run
        # button that drifts with the length of the bot's name does not.
        grid.setColumnStretch(_COL_META, 1)
        grid.setColumnStretch(_COL_TOOLS, 1)

        self._name_edit.textChanged.connect(self._on_name_changed)
        self._version_edit.textChanged.connect(self._on_version_changed)
        self._authors_edit.textChanged.connect(self._on_authors_changed)
        self._tags_field.tags_changed.connect(self._on_tags_changed)

        self.refresh_theme_styles()
        self.set_config(None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self._suppress = True
        if config is None:
            self._name_lbl.setText(_NO_BOT)
            self._name_edit.setText("")
            self._version_edit.setText("")
            self._authors_edit.setText("")
            self._authors_lbl.setText("")
            self._tags_field.set_tags([])
            self._version_pill.setText("")
            self._apply_version_slot()
            self._tag_row.setVisible(False)
            self._set_label_mode(True)
        else:
            self._name_lbl.setText(config.name or _PLACEHOLDER)
            self._name_edit.setText(config.name or "")
            self._version_edit.setText(config.version or "")
            self._authors_edit.setText(config.authors or "")
            self._authors_lbl.setText(f"by {config.authors}" if config.authors else "")
            self._tags_field.set_tags(config.tags)
            self._apply_version(config.version)
            self._tag_row.setVisible(True)
        self._suppress = False

    def set_file(self, filename: str | None) -> None:
        """Name the file the bot came from, shown beside the version."""
        self._file_lbl.setText(f"· {filename}" if filename else "")

    def set_editable(self, editable: bool) -> None:
        if self._config is None:
            return
        self._set_label_mode(not editable)

    def set_status(self, color: str, status: str, elapsed: str) -> None:
        """Show the run state.  *color* is already resolved against the theme."""
        self._status = (color, status, elapsed)
        self._apply_status()

    # -- read-only accessors, for tests and for the window title -------

    @property
    def name_text(self) -> str:
        return self._name_lbl.text()

    @property
    def byline_text(self) -> str:
        return self._authors_lbl.full_text()

    @property
    def file_text(self) -> str:
        return self._file_lbl.full_text()

    @property
    def version_text(self) -> str:
        return self._version_pill.text()

    @property
    def status_text(self) -> str:
        return self._status_lbl.text()

    @property
    def elapsed_text(self) -> str:
        return self._elapsed_lbl.text()

    # ------------------------------------------------------------------
    # Change handlers
    # ------------------------------------------------------------------

    def _on_name_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.name = text
        self._name_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    def _on_version_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.version = text
        self._apply_version(text)
        self.metadata_changed.emit()

    def _on_authors_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.authors = text or None
        self._authors_lbl.setText(f"by {text}" if text else "")
        self.metadata_changed.emit()

    def _on_tags_changed(self, tags: list) -> None:
        if self._suppress or self._config is None:
            return
        # _TagsField hands them back in BOT_TAGS order, which is what gets saved.
        self._config.tags = list(tags)
        self.metadata_changed.emit()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply_version(self, version: str | None) -> None:
        """Show the version pill, or hide it — an empty pill is a grey smudge."""
        self._version_pill.setText(f"v{version}" if version else "")
        self._apply_version_slot()

    def _set_label_mode(self, label_mode: bool) -> None:
        """Swap every metadata field between its label and its editor.

        Recorded on the instance because ``_apply_version`` has to know which of the
        two the version slot should settle on: it is called while the user types into
        the version field, and a slot that read the text and switched to the pill
        would take the field out from under them.
        """
        self._label_mode = label_mode
        for slot in (self._name_slot, self._authors_slot):
            if label_mode:
                slot.show_label()
            else:
                slot.show_editor()
        self._apply_version_slot()
        self._tags_field.set_editable(not label_mode)

    def _apply_version_slot(self) -> None:
        """Put the version slot in the state the mode and the version together call for.

        The pill is hidden outright when there is no version, and a mode switch must
        not bring an empty one back -- but the *editor* is shown whether or not there
        is a version to edit, because an empty field is how a version gets typed.
        Before this was routed through the slot, typing a version in Edit mode brought
        the pill back *beside* the field it was being typed into.
        """
        if not self._label_mode:
            self._version_slot.show_editor()
        elif self._version_pill.text():
            self._version_slot.show_label()
        else:
            self._version_slot.hide_both()

    def _apply_status(self) -> None:
        color, status, elapsed = self._status
        self._dot_lbl.setStyleSheet(f"color: {color};")
        self._status_lbl.setText(status)
        # Subtle rather than muted while idle: the zeroed placeholder is there to
        # reserve the space and name the field, not to be read.  Set here rather than
        # in apply_theme_styles because it changes with the run, not with the theme --
        # and that method calls this one, so both paths reach it.
        self._elapsed_lbl.setText(elapsed or _ZERO_ELAPSED)
        self._elapsed_lbl.setStyleSheet(f"color: {THEME.text_muted if elapsed else THEME.text_subtle};")

    def _size_status_slots(self) -> None:
        """Give the clock and the status the same width, either side of the transport.

        Two things rest on this.  The status word cycles through ``_STATUS_WORDS`` as
        the run goes on, and a slot sized to whichever word is showing would shuffle
        the play and stop buttons sideways every time it changed -- the same drift the
        equal-stretch side columns exist to keep out of the bar.  And equal slots are
        what put the transport in the middle of its own column, so that centring the
        column centres the buttons.

        A minimum rather than a fixed width: a status word longer than any we know of
        should widen its slot rather than be clipped by it.  Re-measured on a theme
        switch, because the clock is in ``THEME.font_mono()``.
        """
        clock = QFontMetrics(self._elapsed_lbl.font()).horizontalAdvance(_ZERO_ELAPSED)
        dot = QFontMetrics(self._dot_lbl.font()).horizontalAdvance(self._dot_lbl.text())
        words = QFontMetrics(self._status_lbl.font())
        status = dot + 6 + max(words.horizontalAdvance(w) for w in _STATUS_WORDS)
        slot = max(clock, status) + _LABEL_PADDING_PX
        self._elapsed_lbl.setMinimumWidth(slot)
        self._status_side.setMinimumWidth(slot)

    def _align_file_baseline(self) -> None:
        """Sit the filename on the bot name's baseline rather than on the row's centre.

        Two texts of different sizes centred in the same row do not share a baseline.
        Centring matches the *boxes*, and a font box is mostly ascent -- so the smaller
        of the two carries its baseline higher, and the filename read as a pixel above
        the line the name sets.  The eye compares baselines, not boxes.

        The shift is the difference between the two fonts' ascents, less the
        difference their box heights already account for.  The row's height cancels
        out of that, which is what makes one number right in both modes: in Edit mode
        the name is a QLineEdit, but its text is the same font box centred in the same
        row, so it sets the same baseline.

        The half-pixel is rounded *away from zero* rather than with ``round``, which
        rounds a half to even: the shift is exactly 0.5 whenever the two font boxes
        differ by an odd number of pixels, and ``round`` turned that into no shift at
        all -- the very pixel this method exists to remove. A half has to land on one
        side or the other, and the side it is already on is the one that reads as
        floating.
        """
        name = QFontMetrics(self._name_lbl.font())
        stamp = QFontMetrics(self._file_lbl.font())
        shift = (name.ascent() - stamp.ascent()) - (name.height() - stamp.height()) / 2
        self._file_lbl.set_baseline_shift(math.floor(abs(shift) + 0.5) * (1 if shift >= 0 else -1))

    def apply_theme_styles(self) -> None:
        self.setStyleSheet(f"BotHeader {{ background: {THEME.surface}; }}")
        self._name_lbl.setStyleSheet(f"color: {THEME.text};")
        self._status_lbl.setStyleSheet(f"color: {THEME.text_muted};")
        self._elapsed_lbl.setFont(THEME.font_mono())
        self._size_status_slots()
        # After the labels above have their own styles: the shift is measured from the
        # fonts a stylesheet may just have changed.
        self._align_file_baseline()
        self._apply_status()
