"""What school a card belongs to, and the order the game lists schools in.

A saved deck stores template names and counts, nothing else, so the school has to
come from the game's own data: ``SpellTemplate.m_sMagicSchoolName``, extracted into
``data/spell_schools.json`` by ``tools/dump_spell_schools.py``. Same reasoning as
``spell_names.py`` for why a snapshot is safe -- the app never reads a WAD at
runtime, and lookups are by exact key, so a stale snapshot costs a card added since
its place in the order and nothing else.

THE ORDER IS THE GAME'S, NOT ALPHABETICAL
    The deck window groups by school, and it does so in an order that is neither
    alphabetical nor the one the spellbook tabs use. It is reproduced verbatim in
    :data:`SCHOOL_ORDER`, because the point of sorting a deck this way is that it
    matches what the player already knows from the game.

NOT EVERY SCHOOL IS A SCHOOL
    Five of the values in the game's data name something other than a wizard's
    school -- ``CastleMagic``, ``Cantrips``, ``Gardening``, ``Fishing``,
    ``WhirlyBurly``. None of them can be in a combat deck, so they are not given
    places in the order; they sort after every real school, together with cards the
    snapshot has never heard of.
"""

from __future__ import annotations

import json
import logging
from functools import cache
from pathlib import Path

logger = logging.getLogger(__name__)

SCHOOLS_PATH = Path(__file__).resolve().parent / "data" / "spell_schools.json"

#: Every school a deck card can belong to, in the order the game's deck window lists
#: them. Not alphabetical, and not the spellbook's order -- see the module docstring.
SCHOOL_ORDER = (
    "Ice",
    "Sun",
    "Life",
    "Fire",
    "Myth",
    "Moon",
    "Star",
    "Death",
    "Storm",
    "Balance",
    "Shadow",
)

_ORDER = {school: index for index, school in enumerate(SCHOOL_ORDER)}

#: Where a card with no school, or a school that is not one of the eleven, sorts.
UNKNOWN_SCHOOL_INDEX = len(SCHOOL_ORDER)


@cache
def _load_schools(path: Path | None = None) -> dict[str, str]:
    """Load the checked-in snapshot. Cached: it never changes at runtime."""
    schools_path = path or SCHOOLS_PATH
    try:
        raw = json.loads(schools_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("could not read spell schools from %s", schools_path)
        return {}
    return raw.get("schools", {})


def school_of(template_name: str, path: Path | None = None) -> str | None:
    """The school of the card stored as *template_name*, or None if unknown.

    None covers a card the snapshot does not name and a card whose school is one of
    the non-combat values; both sort the same way, so no caller needs to tell them
    apart.
    """
    school = _load_schools(path).get(template_name)
    return school if school in _ORDER else None


def school_index(template_name: str, path: Path | None = None) -> int:
    """Where *template_name* sorts among the schools -- last, if it has none."""
    school = school_of(template_name, path)
    return _ORDER[school] if school is not None else UNKNOWN_SCHOOL_INDEX


def deck_sort_key(template_name: str, display_name: str, path: Path | None = None) -> tuple[int, str, str]:
    """School first in the game's order, then alphabetically by what is on screen.

    The display name is what the list shows, so sorting by the template name instead
    would order two cards by a string the reader cannot see -- "Bunyips Rage - T05 -
    A" files under B either way, but "Soul Burner", which the game shows as "Ammut",
    would not. The template name breaks the remaining ties so the order is total.
    """
    return (school_index(template_name, path), display_name.casefold(), template_name)
