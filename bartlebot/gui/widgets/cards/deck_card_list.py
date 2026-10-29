"""A deck's cards as a list of rows, each with its own card art beside the name.

The shared form of what the sidebar's deck card unfolds into and what the Deck Presets
page shows at rest. Both are answering the same question -- *what is in this deck* --
and they were answering it in two different vocabularies: the sidebar built art, count,
display name and template name into a row each, while the library page wrote
``QLabel(f"  {name} × {count}")`` off the raw dict, unsorted by school and with no art
at all.

ORDER AND GROUPING COME FROM ``deck_groups``
    So both surfaces list a deck the way the game's own deck window does -- school
    first, then by name -- and neither one sorts it itself. Treasure cards are absent
    for the reason :func:`~bartlebot.combat.deck_content.deck_groups` gives.

THE ART IS OPTIONAL, AND ITS ABSENCE IS THE ORDINARY CASE
    A row whose card has no extracted art is the row without the tile, never a row with
    a placeholder in it: most installs never run the extractor, so a placeholder per
    card would turn the whole list into a column of gray squares. The name and the count
    carry the row either way. See "Game art is optional" in ``gui/CLAUDE.md``.

``empty_text`` is what a surface says about a deck holding nothing. The sidebar passes
none -- its card refuses to unfold at all with no deck, so the list is never reached
empty -- and the library page passes the sentence that tells the user how to fill it.
"""

from __future__ import annotations

from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from bartleby.shared.strategy_models import DeckData
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from bartlebot.combat.deck_content import DeckCardEntry, deck_groups
from bartlebot.gui.art_signal import follow_art
from bartlebot.gui.widgets.cards.card_art_tile import ROW_TILE, CardArtTile, card_icon, tooltip_for
from bartlebot.gui.widgets.layout.discard import discard_widget

#: A group heading is the row's own text in bold rather than a heading widget: this is a
#: list inside a card or a form region, and ``RuleHeading`` would divide the two groups
#: as though they were sections of the surface.
_HEADING_CSS = "font-weight: bold;"

#: Gap between rows. Tight, because a row is one line of text tall and the art tile
#: beside it already separates one from the next.
_ROW_SPACING = 2


class DeckCardList(QWidget):
    """Every card in a deck, one row each, grouped and headed."""

    def __init__(
        self,
        *,
        empty_text: str = "",
        margins: tuple[int, int, int, int] = (0, 0, 0, 0),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._empty_text = empty_text
        self._deck: DeckData | None = None
        self._rows: list[QWidget] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(*margins)
        self._layout.setSpacing(_ROW_SPACING)
        # A row without art is a row minus its tile, so new art means new rows. A list
        # never filled stays empty rather than gaining its empty text.
        follow_art(self, lambda: self.set_deck(self._deck) if self._rows else None)

    # -- reading --------------------------------------------------------

    @property
    def rows(self) -> list[QWidget]:
        """The row widgets currently shown, headings included, in order."""
        return list(self._rows)

    def row_texts(self) -> list[str]:
        """One string per row: a heading's text, or a card row's labels joined.

        Public because a card row is a composite -- tile, count, name, and the template
        name where it differs -- so neither surface's tests can read it off a ``text()``.
        """
        texts: list[str] = []
        for row in self._rows:
            inner = row.layout()
            if inner is None:
                texts.append(row.text())
                continue
            parts: list[str] = []
            for index in range(inner.count()):
                child = inner.itemAt(index).widget()
                getter = getattr(child, "text", None)
                if callable(getter):
                    parts.append(getter())
            texts.append(" ".join(parts))
        return texts

    # -- filling --------------------------------------------------------

    def set_deck(self, deck: DeckData | None) -> None:
        """Redraw from *deck*. ``None`` and an empty deck are the same thing here."""
        self._deck = deck
        self._clear()
        for group in deck_groups(deck) if deck is not None else []:
            self._add(ThemedTextLabel(group.heading, color="text", css=_HEADING_CSS, parent=self))
            for entry in group.entries:
                self._add(self._make_row(entry))
        if not self._rows and self._empty_text:
            empty = ThemedTextLabel(self._empty_text, parent=self)
            empty.setWordWrap(True)
            self._add(empty)

    def _make_row(self, entry: DeckCardEntry) -> QWidget:
        """One card: art, count, display name, template name.

        The template name is muted and shown only where it differs from the display
        name. For most cards the two are the same string -- an unresolved card falls
        back to its own template name -- and the pair would read as the name twice.
        """
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        icon = card_icon(entry.template)
        if icon is not None:
            layout.addWidget(CardArtTile(entry, icon, ROW_TILE, row), 0)
        layout.addWidget(ThemedTextLabel(f"×{entry.count}", color="text", parent=row), 0)
        # The name takes the slack and wraps; a fixed-width name label and a trailing
        # stretch instead left "Bunyip's Rage (Path A, Tier 5)" clipped mid-word at a
        # 390px sidebar, which is the width the sidebar's copy of this is usually at.
        name = ThemedTextLabel(entry.display, color="text", parent=row)
        name.setWordWrap(True)
        layout.addWidget(name, 1)
        if entry.shows_template:
            template = ThemedTextLabel(entry.template, parent=row)
            template.setWordWrap(True)
            layout.addWidget(template, 0)
        row.setToolTip(tooltip_for(entry))
        return row

    def _add(self, widget: QWidget) -> None:
        """Put a built widget into the list, then show it.

        In that order, and never the reverse: a widget shown while it still has no
        parent is a top-level window until the next line reparents it -- one real frame
        of it, per row -- see gui/CLAUDE.md.
        """
        self._layout.addWidget(widget)
        widget.show()
        self._rows.append(widget)

    def _clear(self) -> None:
        while (item := self._layout.takeAt(0)) is not None:
            widget = item.widget()
            if widget is not None:
                discard_widget(widget)
        self._rows.clear()
