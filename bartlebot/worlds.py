"""The worlds reachable from the World Tree spiral door, and their display names.

WHY THE KEY IS THE ZONE PREFIX
    ``Root.wad``'s ``WorldNames.lang`` is keyed by exactly the string ``zone_name()``
    returns as its first segment -- ``WizardCity``, ``DragonSpire``, ``MooShu`` -- and
    its value is the localized text the spiral door lists. That correspondence is what
    lets a World Gate step verify arrival with the glob ``"<key>/*"`` instead of carrying a
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
import re
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

This is story order, which is what the door lists. The folders under ``bots/`` follow
the badge page instead (see ``bartlebot/bot_folders.py``), which puts Wysteria after
Khrysalis and Arcanum after Empyrea, so the two orders are not interchangeable.

Deliberately excluded, and why -- these all have WorldNames entries, and several have
their own GameData wads, so none of them can be filtered out by a rule:

    AllWorlds, Crafting  quest-log pseudo-entries, not places
    G14_DM               Castle Darkmoor -- a dungeon, entered from Nightside
    G14_SB               Kembaalung Village -- a dungeon, entered from Zafaria
    Aquila               reached by the chariot in Cyclops Lane, not the door
    Wintertusk           reached through Grizzleheim (Vestrilund), not the door
"""


DUNGEON_WORLDS: tuple[str, ...] = (
    "G14_DM",
    "G14_SB",
    "G14_HS",
)
"""Nav-map worlds that are dungeons rather than worlds, and so have no world emblem.

``zone_nav`` groups zones into 25 "worlds", but four of those are not on the spiral door
and three of those four are dungeon groups -- Castle Darkmoor, Kembaalung Village, and
Zigazag. The game ships no ``Button_World_`` texture for any of them, so they take
the generic dungeon badge instead; see ``WORLD_ICON_SOURCES`` in
``tools/extract_card_art.py``.

Aquila is the fourth non-door world and is deliberately **not** here: it is a world with
its own emblem, reached by the Cyclops Lane chariot rather than the door.

This is a display fact and nothing routes on it. A world missing from this tuple loses an
icon; it cannot send a client anywhere.
"""


LABEL_FALLBACKS: dict[str, str] = {
    "G14_HS": "Zigazag",
}
"""Names for nav-map worlds the game's ``WorldNames.lang`` does not name at all.

Used by :func:`label` only, and only when the snapshot has no entry. Deliberately kept out
of :func:`display_names`: that is the list matched against the World Gate's own text, and
none of these is on the door, so a name here would be a claim about text the game never
shows. ``test_worlds.py`` fails if the game ever adds a real name for one of these, so a
stale fallback cannot shadow it quietly.
"""


WORLD_GATE_TITLE_KEY = "GUI:00001017"
"""The World Gate list window's title, "Spiral Map" in English. A zone list opens the same
window, so this title is what tells a World Gate from a zone list without using either."""


@cache
def _snapshot() -> dict:
    try:
        return json.loads(WORLD_NAMES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("Could not read world names from %s; the spiral door cannot be searched", WORLD_NAMES_PATH)
        return {}


@cache
def _world_names() -> dict[str, dict[str, str]]:
    """{world key: {locale: display text}} from the checked-in snapshot."""
    worlds: dict[str, dict[str, str]] = _snapshot().get("worlds", {})
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

    Falls back to :data:`LABEL_FALLBACKS` for a world the game never names, then to the key
    itself, which is readable enough ("DragonSpire") to be a better answer than an empty
    combo box entry when the snapshot is stale or unreadable.
    """
    return next(iter(display_names(world)), LABEL_FALLBACKS.get(world, world))


def world_gate_titles() -> list[str]:
    """Every locale's World Gate window title, deduped, en-US first."""
    by_locale = _snapshot().get("world_gate_title", {})
    if not by_locale:
        logger.warning("world_names.json has no World Gate title. Regenerate with tools/dump_world_names.py")
    return list(dict.fromkeys(by_locale[loc] for loc in ("en-US", *LOCALES) if loc in by_locale))


_MARKUP = re.compile(r"<[^>]*>")


def _plain(text: str) -> str:
    return " ".join(_MARKUP.sub(" ", text).split()).casefold()


def is_world_gate_title(text: str | None) -> bool:
    """True when `text`, read from the list window's title, is the World Gate's in any locale.

    Exact after stripping markup and case, not a substring match: a zone list's own title
    could easily contain the word "map".
    """
    return bool(text) and _plain(text) in {_plain(title) for title in world_gate_titles()}


def zone_pattern(world: str) -> str:
    """The glob matching every zone in `world`, for zone_matches().

    Note this is a prefix match on the world segment alone, so Castle Darkmoor
    ("G14_DM/...") does not match Darkmoor -- which is correct: a client inside Castle
    Darkmoor asked to go to Darkmoor genuinely does need to travel.
    """
    return f"{world}/*"
