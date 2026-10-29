"""File › Templates…: every template, Official and Custom, and the verbs that make templates of your own (#164).

A gallery built from New Bot's :class:`~bartlebot.gui.dialogs.new_bot_dialog.StartCard`: one card
per template, with its Source pill, what it is for, and a last line giving its latest
version, how many of the user's bots were made from it, and the template it was copied
from. A Source filter sits beside the heading, since this is the gallery that holds the
user's own templates beside the Official ones (New Bot has pills only).

The verbs are a footer row, as the strategy dialog's are, and a card's right-click menu
offers the same ones: **New** (a blank template), **Duplicate** (a fork), **Rename**,
**Delete**, and the commit, **Edit**, which opens the selected template's draft. A
double-click is Edit. Which verbs a card allows is :mod:`bartlebot.content.templates.template_gallery`'s
to say; a verb a card does not allow is disabled with the reason as its tooltip.

The dialog decides nothing and writes nothing. Edit, New and Duplicate end it with a
:class:`TemplatesRequest` for the controller, which opens the draft as a document.
Rename and Delete are the controller's too, through the callables it hands in, since
either can touch the open document; the gallery reloads after each.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass

from bartleby.qt.theming.theme import THEME
from bartleby.qt.widgets.buttons.action_button import ActionButton, ActionStyle
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bartlebot.combat.strategy_tags import SOURCE
from bartlebot.content.store_roots import SOURCE_LABELS
from bartlebot.content.templates.template_gallery import GalleryEntry
from bartlebot.gui.dialogs.new_bot_dialog import StartCard
from bartlebot.gui.menu_utils import popup_menu
from bartlebot.gui.widgets.layout.dialog_footer import dialog_footer
from bartlebot.gui.widgets.layout.discard import discard_widget
from bartlebot.gui.widgets.pickers.tag_picker import TagPickerButton, source_vocabulary, tag_icon

#: Two columns, as New Bot's gallery has.
_GALLERY_COLUMNS = 2

EDIT_TEXT = "Edit"
NEW_TEXT = "New"
DUPLICATE_TEXT = "Duplicate"
RENAME_TEXT = "Rename"
DELETE_TEXT = "Delete"

_NOTHING_MATCHES = "No templates match the Source filter."
_NONE_YET = "There are no templates yet. New starts a blank one."


class TemplatesVerb(enum.Enum):
    """What the dialog closed asking for. Rename and Delete happen while it is open."""

    EDIT = "edit"
    NEW = "new"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class TemplatesRequest:
    verb: TemplatesVerb
    #: The template it is about; ``None`` for New.
    entry: GalleryEntry | None


class TemplatesDialog(QDialog):
    """The Templates dialog. Accepted means one of :class:`TemplatesVerb`: read :attr:`request`.

    ``load`` lists the gallery, and is called again after a rename or a delete.
    ``rename`` and ``delete`` do those (asking whatever they need to) and return
    whether anything changed.
    """

    def __init__(
        self,
        load: Callable[[], list[GalleryEntry]],
        parent: QWidget | None = None,
        *,
        rename: Callable[[GalleryEntry], bool],
        delete: Callable[[GalleryEntry], bool],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Templates")
        self.setMinimumSize(600, 460)
        self.resize(760, 560)
        self._load = load
        self._rename = rename
        self._delete = delete
        self._entries: list[GalleryEntry] = []
        #: The cards on show, parallel to :attr:`_shown`.
        self._cards: list[StartCard] = []
        self._shown: list[GalleryEntry] = []
        self._selected_id: str | None = None
        self.request: TemplatesRequest | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        heading_row = QHBoxLayout()
        heading_row.setSpacing(8)
        layout.addLayout(heading_row)
        heading = QLabel("Templates")
        heading.setFont(THEME.ui_font(THEME.font_heading_px, bold=True))
        heading_row.addWidget(heading)
        heading_row.addStretch()
        self._source = TagPickerButton(
            title=SOURCE, placeholder=SOURCE, group=SOURCE, vocabulary=source_vocabulary(("template", "templates"))
        )
        self._source.setToolTip("Filter templates by where they come from: Official, or your own")
        self._source.selection_changed.connect(lambda _keys: self._rebuild())
        heading_row.addWidget(self._source)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(scroll, 1)
        host = QWidget()
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, 0, 0)
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(6)
        for column in range(_GALLERY_COLUMNS):
            self._grid.setColumnStretch(column, 1)
        outer.addLayout(self._grid)
        self._empty = ThemedTextLabel()
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        outer.addWidget(self._empty)
        outer.addStretch()
        scroll.setWidget(host)

        self._new_btn = ActionButton("plus", NEW_TEXT)
        self._new_btn.setToolTip("Start a blank template of your own")
        self._new_btn.clicked.connect(lambda: self._finish(TemplatesVerb.NEW, None))
        self._dup_btn = ActionButton("copy", DUPLICATE_TEXT)
        self._dup_btn.clicked.connect(lambda: self._finish(TemplatesVerb.DUPLICATE, self.selected))
        self._rename_btn = ActionButton("pencil", RENAME_TEXT)
        self._rename_btn.clicked.connect(lambda: self._run_rename(self.selected))
        self._del_btn = ActionButton("trash", DELETE_TEXT, style=ActionStyle.CAUTION)
        self._del_btn.clicked.connect(lambda: self._run_delete(self.selected))
        footer = dialog_footer(
            self,
            commit_text=EDIT_TEXT,
            commit_icon="arrow_right",
            on_commit=lambda: self._finish(TemplatesVerb.EDIT, self.selected),
            cancel_text="Close",
            extras=[self._new_btn, self._dup_btn, self._rename_btn, self._del_btn],
        )
        self._edit_btn = footer.commit
        layout.addLayout(footer.layout)

        self.reload()

    # ------------------------------------------------------------------
    # For the controller and the tests
    # ------------------------------------------------------------------

    @property
    def entries(self) -> list[GalleryEntry]:
        """Every template, whatever the filter shows."""
        return list(self._entries)

    @property
    def cards(self) -> list[StartCard]:
        """The cards on show, in order."""
        return list(self._cards)

    @property
    def selected(self) -> GalleryEntry | None:
        return next((e for e in self._shown if e.id == self._selected_id), None)

    @property
    def source_filter(self) -> TagPickerButton:
        return self._source

    @property
    def empty_text(self) -> str:
        return self._empty.text() if not self._empty.isHidden() else ""

    @property
    def edit_button(self) -> ActionButton:
        return self._edit_btn

    @property
    def new_button(self) -> ActionButton:
        return self._new_btn

    @property
    def duplicate_button(self) -> ActionButton:
        return self._dup_btn

    @property
    def rename_button(self) -> ActionButton:
        return self._rename_btn

    @property
    def delete_button(self) -> ActionButton:
        return self._del_btn

    def select(self, template_id: str) -> None:
        """Select the card for ``template_id``, as a click would."""
        self._select(template_id)

    def card_menu_actions(self, template_id: str) -> list[tuple[str, bool]]:
        """``(text, enabled)`` for each entry of a card's right-click menu, as it would be built."""
        entry = next((e for e in self._shown if e.id == template_id), None)
        return [(text, enabled) for text, enabled, _tip, _run in self._verbs(entry)] if entry else []

    def reload(self) -> None:
        """List the gallery again, keeping the selection when its template is still there."""
        self._entries = self._load()
        counts: dict[str, int] = {}
        for entry in self._entries:
            word = SOURCE_LABELS[entry.origin]
            counts[word] = counts.get(word, 0) + 1
        self._source.set_counts(counts)
        self._rebuild()

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------

    def _rebuild(self) -> None:
        for card in self._cards:
            discard_widget(card, self._grid)
        chosen = set(self._source.selection())
        self._shown = [e for e in self._entries if not chosen or SOURCE_LABELS[e.origin] in chosen]
        self._cards = []
        for index, entry in enumerate(self._shown):
            card = StartCard(
                entry.name,
                entry.summary,
                tooltip=entry.blurb if entry.blurb != entry.summary else "",
                icon=tag_icon(entry.icon_tag) if entry.icon_tag else None,
                source=entry.origin,
                detail=entry.detail,
            )
            # Into the installed grid before anything shows it: see gui/CLAUDE.md.
            self._grid.addWidget(card, index // _GALLERY_COLUMNS, index % _GALLERY_COLUMNS)
            card.chosen.connect(lambda tid=entry.id: self._select(tid))
            card.activated.connect(lambda: self._finish(TemplatesVerb.EDIT, self.selected))
            card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            card.customContextMenuRequested.connect(lambda pos, c=card, tid=entry.id: self._show_menu(c, tid, pos))
            self._cards.append(card)
        if self._shown:
            self._empty.setVisible(False)
        else:
            self._empty.setText(_NOTHING_MATCHES if self._entries else _NONE_YET)
            self._empty.setVisible(True)
        ids = [e.id for e in self._shown]
        self._select(self._selected_id if self._selected_id in ids else (ids[0] if ids else None))

    def _select(self, template_id: str | None) -> None:
        self._selected_id = template_id
        for entry, card in zip(self._shown, self._cards):
            card.set_selected(entry.id == template_id)
        self._refresh_buttons()

    def _verbs(self, entry: GalleryEntry | None) -> list[tuple[str, bool, str, Callable[[], None]]]:
        """``(text, enabled, why not, run)`` for each verb on ``entry``, in menu order."""
        if entry is None:
            return []
        return [
            (EDIT_TEXT, entry.may_edit, entry.edit_refused, lambda: self._finish(TemplatesVerb.EDIT, entry)),
            (
                f"{DUPLICATE_TEXT}…",
                entry.may_duplicate,
                "Nothing is released yet to copy.",
                lambda: self._finish(TemplatesVerb.DUPLICATE, entry),
            ),
            (
                f"{RENAME_TEXT}…",
                entry.may_rename,
                "Official templates keep the name they ship with.",
                lambda: self._run_rename(entry),
            ),
            (f"{DELETE_TEXT}…", entry.may_delete, entry.delete_refused, lambda: self._run_delete(entry)),
        ]

    def _refresh_buttons(self) -> None:
        entry = self.selected
        verbs = {text.rstrip("…"): (enabled, why) for text, enabled, why, _run in self._verbs(entry)}
        for button, text, tip in (
            (self._edit_btn, EDIT_TEXT, "Open this template's draft to change it"),
            (self._dup_btn, DUPLICATE_TEXT, "Make a template of your own from this one"),
            (self._rename_btn, RENAME_TEXT, "Rename this template"),
            (self._del_btn, DELETE_TEXT, "Delete this template; bots made from it keep running"),
        ):
            enabled, why = verbs.get(text, (False, ""))
            button.setEnabled(enabled)
            button.setToolTip(tip if enabled else why)

    def _show_menu(self, card: StartCard, template_id: str, pos) -> None:
        self._select(template_id)
        menu = popup_menu(card)
        for text, enabled, why, run in self._verbs(self.selected):
            action = menu.addAction(text)
            action.setEnabled(enabled)
            if not enabled and why:
                action.setToolTip(why)
            action.triggered.connect(run)
        menu.setToolTipsVisible(True)
        menu.exec(card.mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Behavior
    # ------------------------------------------------------------------

    def _finish(self, verb: TemplatesVerb, entry: GalleryEntry | None) -> None:
        if verb is not TemplatesVerb.NEW:
            allowed = {TemplatesVerb.EDIT: "may_edit", TemplatesVerb.DUPLICATE: "may_duplicate"}[verb]
            if entry is None or not getattr(entry, allowed):
                return
        self.request = TemplatesRequest(verb, entry)
        self.accept()

    def _run_rename(self, entry: GalleryEntry | None) -> None:
        if entry is not None and entry.may_rename and self._rename(entry):
            self.reload()

    def _run_delete(self, entry: GalleryEntry | None) -> None:
        if entry is not None and entry.may_delete and self._delete(entry):
            self.reload()
