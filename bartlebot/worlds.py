"""The worlds reachable from the World Tree spiral door, and their display names.

WHY THE KEY IS THE ZONE PREFIX
    ``Root.wad``'s ``WorldNames.lang`` is keyed by exactly the string ``zone_name()``
    returns as its first segment -- ``WizardCity``, ``DragonSpire``, ``MooShu`` -- and
    its value is the localized text the spiral door lists. That correspondence is what
    lets go_to_world verify arrival with the glob ``"<key>/*"`` instead of carrying a
    per-world table of spiral-chamber zone names, which would need re-deriving every
    time KI moves a chamber.

WHY EVERY LOCALE, AND WHY A LIST
    Memory holds the *resolved* display text, so a client running in French lists
    "Sorcelleville", not "Wizard City". Matching one locale fails on the other six, and
    fails by finding nothing -- which enter_zone_via_list reports as "entry not found on
    any page". So the snapshot carries every locale and callers match against all of them.

    Some keys are untranslated in some locales: Darkmoor has no el/it/pl entry, Wallaru
    no el/it/pl, Novus no el. That is not a gap to paper over -- an untranslated key is
    precisely the case where those clients display the English string -- so
    ``display_names()`` returns the deduped set of whatever is present, which always
    includes en-US.

WHY THE DOOR LIST IS HAND-CURATED
    Nothing in the game files distinguishes a spiral-door world from a dungeon that has
    its own WorldNames entry and its own GameData wads. ``Darkmoor`` (a world, on the
    door) and ``G14_DM`` (Castle Darkmoor, reached through Nightside) are
    indistinguishable by any rule I could write, and guessing got it wrong in exactly
    that spot. So SPIRAL_DOOR_WORLDS is written out by hand, every exclusion is named
    below with its reason, and test_worlds.py asserts every entry resolves against the
    snapshot so a typo fails CI rather than silently dropping a world from the editor.

    Being absent from this list only means a world cannot be picked in the properties
    panel. It cannot cause a wrong teleport: the door is searched by display text, never
    by page or index.
"""

from __future__ import annotations

import json
import logging
from functools import cache
from pathlib import Path

logger = logging.getLogger(__name__)

WORLD_NAMES_PATH = Path(__file__).resolve().parent / "data" / "world_names.json"

# The game ships these seven; a key missing from one is normal. Kept in step with
# dialog_captions / tools/dump_dialog_captions.py.
LOCALES: tuple[str, ...] = ("de", "el", "en-US", "es", "fr", "it", "pl")

SPIRAL_DOOR_WORLDS: tuple[str, ...] = (
    "WizardCity",
    "Krokotopia",
    "Marleybone",
    "MooShu",
    "DragonSpire",
    "Grizzleheim",
    "Celestia",
    "Wysteria",
    "Zafaria",
    "Avalon",
    "Azteca",
    "Khrysalis",
    "Polaris",
    "Arcanum",
    "Mirage",
    "Empyrea",
    "Karamelle",
    "Lemuria",
    "Novus",
    "Wallaru",
    "Darkmoor",
)
"""Worlds the World Tree spiral door offers, in progression order.

The order matches the numbered folders under ``bots/`` (08 Wysteria, 12 Khrysalis and
19 Novus have no bots yet, which is why those numbers are missing there).

Deliberately excluded, and why -- these all have WorldNames entries, and several have
their own GameData wads, so none of them can be filtered out by a rule:

    AllWorlds, Crafting  quest-log pseudo-entries, not places
    G14_DM               Castle Darkmoor -- a dungeon, entered from Nightside
    G14_SB               Kembaalung Village -- a dungeon, entered from Zafaria
    Aquila               reached by the chariot in Cyclops Lane, not the door
    Wintertusk           reached through Grizzleheim (Vestrilund), not the door
"""


@cache
def _world_names() -> dict[str, dict[str, str]]:
    """{world key: {locale: display text}} from the checked-in snapshot."""
    try:
        raw = json.loads(WORLD_NAMES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("Could not read world names from %s; the spiral door cannot be searched", WORLD_NAMES_PATH)
        return {}

    worlds: dict[str, dict[str, str]] = raw.get("worlds", {})
    missing = [key for key in SPIRAL_DOOR_WORLDS if key not in worlds]
    if missing:
        logger.warning(
            "world_names.json is missing %d spiral door world(s): %s. Regenerate with tools/dump_world_names.py",
            len(missing),
            ", ".join(missing),
        )
    return worlds


def display_names(world: str) -> list[str]:
    """Every display text the spiral door might show for `world`, deduped, en-US first.

    en-US leads because it is the one locale always present, so it is the sensible thing
    to put in a log line or an error message. Order is otherwise stable but not meaningful
    -- callers match against the whole list.
    """
    by_locale = _world_names().get(world, {})
    ordered = [by_locale[loc] for loc in ("en-US", *LOCALES) if loc in by_locale]
    return list(dict.fromkeys(ordered))


def label(world: str) -> str:
    """The en-US display name, for the editor dropdown and log lines.

    Falls back to the key itself, which is readable enough ("DragonSpire") to be a better
    answer than an empty combo box entry when the snapshot is stale or unreadable.
    """
    return next(iter(display_names(world)), world)


def zone_pattern(world: str) -> str:
    """The glob matching every zone in `world`, for zone_matches().

    Note this is a prefix match on the world segment alone, so Castle Darkmoor
    ("G14_DM/...") does not match Darkmoor -- which is correct: a client inside Castle
    Darkmoor asked to go to Darkmoor genuinely does need to travel.
    """
    return f"{world}/*"
