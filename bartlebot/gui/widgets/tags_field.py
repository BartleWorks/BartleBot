"""``TagsField``: a bot's tags as pills, led when editable by one picker button per group.

Shared by Bar B (``canvas/bot_header.py``), where it switches between Run and Edit mode,
and the New Bot dialog, which only ever edits. One widget so the two cannot drift: the
same chips, the same pickers, the same order.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QWidget

from bartlebot.bot_tags import BOT_TAGS, REWARDS, TAG_GROUPS, tag_group
from bartlebot.gui.sidebar.sidebar_section import value_label
from bartlebot.gui.widgets.flow_layout import flow_container
from bartlebot.gui.widgets.tag_picker import TagPickerButton
from bartlebot.gui.widgets.tag_pill import SuggestedTagPill, TagChip, TagPill

_PLACEHOLDER = "—"


class TagsField(QWidget):
    """The bot's tags as pills, led in Edit mode by one picker button per group.

    Bar B's tag row, and the New Bot dialog's, which keeps it editable throughout.

    Unlike the header's other fields this is not a label swapped for an editor. The pills are
    the value in both modes — what changes is whether they carry a ✕ and whether the
    picker buttons are shown — because a tag list reads the same either way and building
    a second, differently-shaped view of it just to be read-only is how the two drift.

    The buttons are captioned "Rewards" / "Activities" with no count, unlike the
    library's, where the button *is* the filter and its text has to report the
    selection.  Here the chips beside them already do that.  Nor "Add Rewards…": the
    popup toggles, so the same button takes a tag off again, and a caption saying
    "Add" mislabels half of what it does.

    In Edit mode the chips are followed by an outlined ``+ Tag`` for each tag the graph
    suggests and the bot does not carry (#19). A reminder rather than a question: a click
    makes it an ordinary tag, and ignoring it costs nothing and is remembered nowhere.
    Taking an accepted one off again brings its suggestion back, which is the same
    reminder, not a nag.
    """

    tags_changed = pyqtSignal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tags: list[str] = []
        self._suggested: list[str] = []
        self._editable = False
        self._rewards_offered = True

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self._empty_lbl = value_label()
        self._empty_lbl.setText(_PLACEHOLDER)
        root.addWidget(self._empty_lbl)

        # Centered, because the field reserves the editable form's height in both modes
        # -- that is what keeps the bar's height off the mode -- so in Run mode the
        # pills are shorter than the rect they are handed, and the grid row is taller
        # still while the button clusters span it.  A flow hangs its rows from the top
        # of what it was given, which put the pills above the row's own center.
        self._pill_host, self._pill_layout = flow_container(center_rows=True)
        root.addWidget(self._pill_host)

        # The picker buttons ride *inside* the pill flow, as one item, rather than in a
        # flow of their own beside it.  Two flows side by side each wrap on their own
        # share of the width, so a row short of a hundred pixels came back two rows
        # of chips *and* two rows of buttons; one flow wraps once, and the buttons
        # move down whole rather than splitting.
        #
        # They lead the flow rather than trail it, so that their position is fixed.
        # Trailing, every chip added or removed shifted both buttons sideways by that
        # chip's width -- and removing one is a click on the chip's own ✕, which lands
        # the pointer where the buttons are about to arrive.  Leading, the chips grow
        # away from a stationary target instead.
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

    def set_suggestions(self, tags: list[str]) -> None:
        """Offer *tags*, the ones the graph suggests, wherever the bot does not carry them.

        Called on every graph edit, so an unchanged list is a no-op rather than a rebuild.
        """
        if tags == self._suggested:
            return
        self._suggested = list(tags)
        self._rebuild()

    def shown_suggestions(self) -> list[str]:
        """The suggestions on show: Edit mode only, and only for tags not already set."""
        if not self._editable:
            return []
        return [
            t for t in self._suggested if t not in self._tags and (self._rewards_offered or tag_group(t) != REWARDS)
        ]

    def set_rewards_offered(self, offered: bool) -> None:
        """Whether the Rewards group is offered at all. Not on a template draft: what a run
        yields depends on the dungeon a bot made from it is for, so there is nothing true to pick."""
        if offered == self._rewards_offered:
            return
        self._rewards_offered = offered
        self._pickers[REWARDS].setVisible(offered)
        self._rebuild()

    @property
    def rewards_offered(self) -> bool:
        return self._rewards_offered

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
        *center* from one it should stretch.

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

    def _accept(self, tag: str) -> None:
        self.set_tags([*self._tags, tag])
        self.tags_changed.emit(list(self._tags))

    def _rebuild(self) -> None:
        # The picker buttons live in this layout too and are long-lived, so they come out
        # before the sweep and go back in after it, always first.
        self._pill_layout.removeWidget(self._buttons)
        while (item := self._pill_layout.takeAt(0)) is not None:
            if w := item.widget():
                w.hide()
                w.deleteLater()

        self._pill_layout.addWidget(self._buttons)
        for tag in self._tags:
            if self._editable:
                chip = TagChip(tag)
                chip.removed.connect(self._remove)
                self._pill_layout.addWidget(chip)
            else:
                self._pill_layout.addWidget(TagPill(tag))
        for tag in self.shown_suggestions():
            ghost = SuggestedTagPill(tag, tooltip=f"This bot's graph suggests {tag}. Click to add it.")
            ghost.clicked.connect(lambda _checked=False, t=tag: self._accept(t))
            self._pill_layout.addWidget(ghost)

        # The placeholder stands in for the pills, not for the whole row: with the
        # picker buttons showing, an untagged bot already reads as untagged.
        self._empty_lbl.setVisible(not self._tags and not self._editable)
        self._pill_host.setVisible(bool(self._tags) or self._editable)
        self._buttons.setVisible(self._editable)
        self._pill_host.updateGeometry()
        # Both halves of the floor ``sizeHint`` documents.  ``minimumHeight`` is the one
        # a wrapping widget's parent actually honors: the pill flow reports
        # height-for-width, and ``QWidgetItem`` answers that from this widget's *layout*
        # rather than from the widget, so the hint alone was read straight past.  Set
        # here rather than once in ``__init__`` because the buttons are sized by the
        # theme's font, and this runs on every rebuild.
        self.setMinimumHeight(self._floor())
        self.updateGeometry()
