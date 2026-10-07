"""The pill naming where an item comes from: Official, Official (modified) or Custom (#149).

One widget for every surface that lists bots, templates, subgraphs or strategies, so the
same origin reads the same everywhere. Its words are :data:`bartlebot.store_roots.SOURCE_LABELS`,
and the Source filter beside each list offers the same words (``SOURCE_VOCABULARY`` in
:mod:`bartlebot.gui.widgets.tag_picker`), so a chip always says what the pill it matches says.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel

from bartlebot.gui.widgets.tag_pill import AccentPill, NeutralPill
from bartlebot.store_roots import Origin, pill_text

#: The tooltip on the pill of an override whose Official item has changed since (#169).
OFFICIAL_UPDATED_TIP = (
    "The Official version has changed since you made your changes. "
    "Revert to Official to use the new one; your changes are discarded."
)


def source_pill(source: Origin, *, official_updated: bool = False) -> QLabel:
    """The pill naming where an item comes from, weighted like ``ActionButton``.

    Always words rather than a glyph: no icon says "ships with the app, tested in game"
    unaided (#124). Official is filled with the accent (``PRIMARY``), a modified Official
    one outlined in it (``SECONDARY``) and anything else gray (``TERTIARY``). All three are
    the same height, so a card does not change size when an edit turns one into another.

    ``official_updated`` marks an override whose Official item has changed since it was
    made: the pill adds *· Official updated* and says in its tooltip what Revert does.
    """
    text = pill_text(source, official_updated=official_updated)
    if source is Origin.SHIPPED:
        return AccentPill(text, filled=True)
    if source is Origin.SHIPPED_OVERRIDE:
        pill = AccentPill(text)
        if official_updated:
            pill.setToolTip(OFFICIAL_UPDATED_TIP)
        return pill
    return NeutralPill(text)
