"""Plain (Qt-free) description of a saved deck's contents, for the sidebar's
inline disclosure and anything else that wants to render the same list.
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


def deck_content_lines(deck: DeckData) -> list[DeckContentLine]:
    """Text lines describing a non-empty *deck*'s contents, in display order.

    Callers must not pass a deck for which ``DeckData.is_empty()`` is true —
    there would be nothing to show.

    Treasure cards are omitted: they can be neither read nor restored, so
    listing them would promise a round trip that cannot happen.
    """
    lines: list[DeckContentLine] = []

    # Shown above the cards because it describes the whole deck rather than
    # anything in it.  Resolved, not just stored: whether the school will
    # actually be set depends on the app setting unless this deck pins an
    # answer, and naming only the saved school would leave the reader to guess.
    if deck.archmastery_school:
        will_set, source = resolve_apply_archmastery(deck)
        verb = "set on apply" if will_set else "not set on apply"
        lines.append(DeckContentLine(f"Archmastery: {deck.archmastery_school} — {verb} ({source})"))

    for section_name, cards in (("Normal Cards", deck.normal), ("Item Cards", deck.item)):
        if not cards:
            continue
        lines.append(DeckContentLine(section_name, bold=True))
        for name, count in sorted(cards.items()):
            lines.append(DeckContentLine(f"{display_name(name)} × {count}"))

    return lines
