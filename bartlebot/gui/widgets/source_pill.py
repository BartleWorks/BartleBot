"""The pill naming where an item comes from: Official, Official (modified) or Custom (#149).

One widget for every surface that lists bots, templates, subgraphs or strategies, so the
same origin reads the same everywhere. Its words are :data:`bartlebot.store_roots.SOURCE_LABELS`,
and the Source filter beside each list offers the same words (``SOURCE_VOCABULARY`` in
:mod:`bartlebot.gui.widgets.tag_picker`), so a chip always says what the pill it matches says.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel

from bartlebot.gui.widgets.tag_pill import AccentPill, NeutralPill
from bartlebot.store_roots import SOURCE_LABELS, Origin


def source_pill(source: Origin) -> QLabel:
    """The pill naming where an item comes from, weighted like ``ActionButton``.

    Always words rather than a glyph: no icon says "ships with the app, tested in game"
    unaided (#124). Official is filled with the accent (``PRIMARY``), a modified Official
    one outlined in it (``SECONDARY``) and anything else gray (``TERTIARY``). All three are
    the same height, so a card does not change size when an edit turns one into another.
    """
    text = SOURCE_LABELS[source]
    if source is Origin.SHIPPED:
        return AccentPill(text, filled=True)
    if source is Origin.SHIPPED_OVERRIDE:
        return AccentPill(text)
    return NeutralPill(text)
