"""Resolve a card's internal template name to what the player actually sees.

``SpellTemplate.name()`` -- what ``deck_io.py`` stores and keys decks by -- is a
stable identity, not display text: tiered/fusion variants print as e.g. "Novus
Storm - T05 - A", and some templates have been renamed in-game since ("Soul
Burner" now displays as "Ammut") while keeping their original internal name.
``deck_io.py`` must keep keying on the internal name -- see the comment at the
top of that module on why ``spell_base()`` is unreliable -- so this module only
ever feeds the display layer.

Resolution is a lookup into ``data/spell_names.json``, a snapshot extracted from
Root.wad by ``tools/dump_spell_names.py``; see that file for how the mapping is
built and why a snapshot is safe here. A card missing from the snapshot (never
got a player-facing name, or was added to the game after the snapshot was taken)
falls back to its internal name rather than showing nothing -- the same choice
``deck_view_dialog.py`` already made for unviewable treasure cards.
"""

from __future__ import annotations

import json
import logging
from functools import cache
from pathlib import Path

logger = logging.getLogger(__name__)

NAMES_PATH = Path(__file__).resolve().parent / "data" / "spell_names.json"


@cache
def _load_names(path: Path | None = None) -> dict[str, str]:
    """Load the checked-in snapshot. Cached: it never changes at runtime."""
    names_path = path or NAMES_PATH
    try:
        raw = json.loads(names_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("could not read spell names from %s", names_path)
        return {}
    return raw.get("names", {})


def display_name(internal_name: str, path: Path | None = None) -> str:
    """The name the player sees for a card, or its internal name if unresolved."""
    return _load_names(path).get(internal_name, internal_name)
