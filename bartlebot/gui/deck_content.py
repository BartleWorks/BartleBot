"""Plain (Qt-free) description of a saved deck's contents, for the sidebar's
inline disclosure and anything else that wants to render the same list.

Two shapes of the same data, and the second is the one to reach for:

* :func:`deck_groups` returns the cards as structured entries, each carrying the
  *template name* alongside the display name. Anything drawing card art needs that
  key -- it is what ``deck_io`` stores and what ``card_art`` looks art up by, and it
  cannot be recovered from the display name, since one display name covers many
  templates.
* :func:`deck_content_lines` is the older flat list of text lines, kept for callers
  that only ever wanted strings. It is built from the same grouping, so the two can
  never disagree about what is in a deck.
"""

from __future__ import annotations

from dataclasses import dataclass

from bartlebot.bot.strategy_models import DeckData
from bartlebot.deck_io import resolve_apply_archmastery
from bartlebot.spell_names import display_name


@dataclass(frozen=True)
class DeckContentLine:
    text: str
    bold: bool = False


@dataclass(frozen=True)
class DeckCardEntry:
    """One card in a deck: how it is stored, how it is shown, and how many.

    ``template`` is the internal name ``deck_io`` keys by ("Storm Lord - T05 - A").
    ``display`` is what the player sees. They are often the same string -- a card
    missing from the names snapshot falls back to its own template name -- so a
    caller showing both should show ``template`` only when it differs.
    """

    template: str
    display: str
    count: int

    @property
    def shows_template(self) -> bool:
        """Whether the template name says anything the display name does not."""
        return self.template != self.display


@dataclass(frozen=True)
class DeckGroup:
    """A run of cards that came from the same part of the deck.

    ``label`` is the short form, for the divider beside the art strip; ``heading`` is
    the sentence form the expanded text list has room for.
    """

    label: str
    heading: str
    entries: tuple[DeckCardEntry, ...]


def archmastery_line(deck: DeckData) -> str | None:
    """The deck's archmastery note, or None when it saved no school.

    Resolved, not just stored: whether the school will actually be set depends on the
    app setting unless this deck pins an answer, and naming only the saved school
    would leave the reader to guess.
    """
    if not deck.archmastery_school:
        return None
    will_set, source = resolve_apply_archmastery(deck)
    verb = "set on apply" if will_set else "not set on apply"
    return f"Archmastery: {deck.archmastery_school} — {verb} ({source})"


def deck_groups(deck: DeckData) -> list[DeckGroup]:
    """The deck's cards, grouped and sorted by template name within each group.

    Treasure cards are omitted, and deliberately: they can be neither read nor
    restored, so listing them would promise a round trip that cannot happen. ``tc``
    exists only so decks saved by earlier versions still parse -- see
    :class:`~bartlebot.bot.strategy_models.DeckData`.

    An empty group is dropped rather than returned empty, so a caller can render
    every group it is given without checking.
    """
    groups: list[DeckGroup] = []
    for label, heading, cards in (
        ("Normal", "Normal Cards", deck.normal),
        ("Item", "Item Cards", deck.item),
    ):
        if not cards:
            continue
        entries = tuple(
            DeckCardEntry(template=name, display=display_name(name), count=count)
            for name, count in sorted(cards.items())
        )
        groups.append(DeckGroup(label=label, heading=heading, entries=entries))
    return groups


def deck_content_lines(deck: DeckData) -> list[DeckContentLine]:
    """Text lines describing a non-empty *deck*'s contents, in display order.

    Callers must not pass a deck for which ``DeckData.is_empty()`` is true --
    there would be nothing to show.
    """
    lines: list[DeckContentLine] = []

    # Above the cards because it describes the whole deck rather than anything in it.
    note = archmastery_line(deck)
    if note:
        lines.append(DeckContentLine(note))

    for group in deck_groups(deck):
        lines.append(DeckContentLine(group.heading, bold=True))
        for entry in group.entries:
            lines.append(DeckContentLine(f"{entry.display} × {entry.count}"))

    return lines
