"""Bar B — the header above the bot's graph.

Bar B sits above the graph, so it owns the *bot*: its name, version, file, author
and tags, plus the run controls and the run status.  Bar A sits above the game
windows and owns the *clients*.  Name and status used to live in Bar A essentially
by accident, which meant they vanished with the game view (#80).

The metadata rows are the ones ``BotInfoSection`` used to hold, lifted rather than
rewritten: ``_swappable`` and ``_TagsField`` are that section's mechanism, moved
here whole.  Everything else the old Bot tab carried is gone —

* **Start** (``start_location``) was never read by the engine, and ``go_to_world``
  plus the bot templates make it moot;
* **Initial State** and **Recovery State** are badges on the nodes themselves
  (#71), so the labels only ever restated the graph;
* the **full description** moved to the top of Setup (read) and Options (edit),
  because eliding it here would hide the ``REQUIREMENTS:`` blocks four bots carry.

The header does not own the buttons.  ``BotCanvas`` builds, wires and *groups* them,
and hands them in as two opaque widgets — the transport, and everything that belongs
at the right-hand end — so this module holds no run semantics at all.  The right-hand
grouping is the caller's because which of those clusters exists depends on the mode,
which is knowledge the header does not have and should not acquire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
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
_STATUS_FONT_PX = 11
# QLabel adds a pixel of its own around the text, so a hint of exactly the advance
# is one pixel short and the label elides a character off text that fits.
_LABEL_PADDING_PX = 2

# Grid columns.  The run controls are the middle one so that they centre.
_COL_META = 0
_COL_RUN = 1
_COL_TOOLS = 2


def _swappable(label_w: QWidget, edit_w: QWidget) -> QWidget:
    """Container that shows either *label_w* or *edit_w* (not both).

    The label is shown by default.  Call ``setVisible`` on each child to switch
    modes; the layout collapses the hidden widget to zero.
    """
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(label_w)
    lay.addWidget(edit_w)
    edit_w.setVisible(False)
    return w


class _ElidedLabel(ThemedMixin, QLabel):
    """A one-line label that shortens its text rather than widening the bar.

    The byline shares line 2 with the tag pills, and an author list long enough to
    push them off the end would make the whole header claim a minimum width the
    canvas has to honour.  ``QLabel`` has no elide mode of its own, so the text is
    elided against the width Qt actually gave us, on every resize.
    """

    def __init__(self, text: str = "", *, color: str = "text_subtle", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = text
        self._color_token = color
        # Maximum, not Ignored: the label asks for the width its full text wants and
        # settles for less. Ignored asks for nothing, which in a row of stretch-0
        # widgets is exactly what it gets -- the byline vanished that way.
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
        """
        metrics = QFontMetrics(self.font())
        return QSize(metrics.horizontalAdvance(self._full) + _LABEL_PADDING_PX, metrics.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Zero width: eliding is only possible for a label allowed to be narrow."""
        return QSize(0, QFontMetrics(self.font()).height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        metrics = QFontMetrics(self.font())
        super().setText(metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, max(0, self.width())))
        self.setToolTip(self._full if super().text() != self._full else "")

    def apply_theme_styles(self) -> None:
        self.setStyleSheet(f"color: {getattr(THEME, self._color_token)}; font-size: 11px;")


class _TagsField(QWidget):
    """The bot's tags as pills, and two buttons that add more.

    Unlike the other fields this is not a label swapped for an editor. The pills are
    the value in both modes — what changes is whether they carry a ✕ and whether the
    Add buttons are shown — because a tag list reads the same either way and building
    a second, differently-shaped view of it just to be read-only is how the two drift.

    The buttons are captioned "Add Rewards…" / "Add Activity…" rather than the
    library's "Rewards" / "Activity": there, the button *is* the filter and its text
    has to report the selection; here the chips beside them already do that, so the
    text is free to say what pressing it does.
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

        self._pill_host, self._pill_layout = flow_container()
        root.addWidget(self._pill_host)

        # The Add buttons ride *inside* the pill flow, as one item, rather than in a
        # flow of their own beside it.  Two flows side by side each wrap on their own
        # share of the width, so a byline short of a hundred pixels came back two rows
        # of chips *and* two rows of buttons; one flow wraps once, and the buttons
        # move down whole rather than splitting.
        self._buttons = QWidget()
        button_layout = QHBoxLayout(self._buttons)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(4)
        self._pickers: dict[str, TagPickerButton] = {}
        for group in TAG_GROUPS:
            btn = TagPickerButton(title=group, group=group, caption=f"Add {group}…")
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

    # -- internals -----------------------------------------------------

    def _on_picker_changed(self, _keys: list) -> None:
        self._tags = [t for t in BOT_TAGS if any(t in p.tags() for p in self._pickers.values())]
        self._rebuild()
        self.tags_changed.emit(list(self._tags))

    def _remove(self, tag: str) -> None:
        self.set_tags([t for t in self._tags if t != tag])
        self.tags_changed.emit(list(self._tags))

    def _rebuild(self) -> None:
        # The Add buttons live in this layout too and are long-lived, so they come out
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
        # Last status handed to set_status, replayed on a theme switch: the dot's
        # colour is a THEME value the caller resolved, so this widget cannot
        # re-derive it without being told again.
        self._status = (THEME.text_muted, "Idle", "")

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        # -- Left column, row 1: name, version, file ------------------
        self._name_lbl = QLabel(_NO_BOT)
        name_font = self._name_lbl.font()
        name_font.setBold(True)
        name_font.setPointSize(name_font.pointSize() + 1)
        self._name_lbl.setFont(name_font)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Bot name")
        self._name_edit.setFont(name_font)
        # Sized for a name, not for the label it stands in for: a QLineEdit inside a
        # _swappable takes the container's width, and the container is as wide as
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
        title_row.addWidget(_swappable(self._name_lbl, self._name_edit), 0)
        title_row.addWidget(_swappable(self._version_pill, self._version_edit), 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(self._file_lbl, 0)
        title_row.addStretch(1)

        # -- Left column, row 2: byline and tags ----------------------
        self._authors_lbl = _ElidedLabel()
        self._authors_edit = QLineEdit()
        self._authors_edit.setPlaceholderText("Author(s)")
        self._authors_edit.setFixedWidth(180)

        self._tags_field = _TagsField()

        self._byline = QWidget()
        byline_row = QHBoxLayout(self._byline)
        byline_row.setContentsMargins(0, 0, 0, 0)
        byline_row.setSpacing(6)
        byline_row.addWidget(_swappable(self._authors_lbl, self._authors_edit), 0)
        byline_row.addSpacing(4)
        byline_row.addWidget(self._tags_field, 0)
        byline_row.addStretch(1)

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
        status_font.setPixelSize(_STATUS_FONT_PX)
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
        grid.addWidget(self._byline, 1, _COL_META)
        # Both control clusters span the two metadata rows, which is what puts them on
        # the bar's own vertical centre rather than on the centre of row 1.  Nothing is
        # stacked in either column any more, so they are level with each other -- which
        # they would not be if only one of them spanned.
        grid.addWidget(run, 0, _COL_RUN, 2, 1, centred)
        grid.addWidget(tools, 0, _COL_TOOLS, 2, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # The two side columns take equal stretch, which is the only arrangement that
        # centres the Run button exactly: the centre lands in the middle of the bar
        # only while the columns either side of it are the same width.  The cost is
        # that the metadata is capped at half the bar however much of it there is --
        # so a long filename elides, and in Edit mode a heavily tagged bot wraps its
        # byline.  Both degrade quietly; a Run button that drifts with the length of
        # the bot's name does not.
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
            self._version_pill.setVisible(False)
            self._byline.setVisible(False)
            self._set_label_mode(True)
        else:
            self._name_lbl.setText(config.name or _PLACEHOLDER)
            self._name_edit.setText(config.name or "")
            self._version_edit.setText(config.version or "")
            self._authors_edit.setText(config.authors or "")
            self._authors_lbl.setText(f"by {config.authors}" if config.authors else "")
            self._tags_field.set_tags(config.tags)
            self._apply_version(config.version)
            self._byline.setVisible(True)
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
        self._version_pill.setVisible(bool(version))

    def _set_label_mode(self, label_mode: bool) -> None:
        for lbl, edit in (
            (self._name_lbl, self._name_edit),
            (self._version_pill, self._version_edit),
            (self._authors_lbl, self._authors_edit),
        ):
            # The version pill is hidden outright when there is no version; do not
            # let a mode switch bring an empty one back.
            wanted = label_mode and (lbl is not self._version_pill or bool(self._version_pill.text()))
            lbl.setVisible(wanted)
            edit.setVisible(not label_mode)
        self._tags_field.set_editable(not label_mode)

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

    def apply_theme_styles(self) -> None:
        self.setStyleSheet(f"BotHeader {{ background: {THEME.surface}; }}")
        self._name_lbl.setStyleSheet(f"color: {THEME.text};")
        self._status_lbl.setStyleSheet(f"color: {THEME.text_muted};")
        self._elapsed_lbl.setFont(THEME.font_mono())
        self._size_status_slots()
        self._apply_status()
