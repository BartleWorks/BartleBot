"""The game's realms, and their display names in every locale.

WHY A KEY, AND WHY EVERY LOCALE
    ``Root.wad``'s ``RealmNames.lang`` is keyed by the realm's English name and valued
    with the text the realm list shows in each locale. Memory holds the *resolved*
    text, so a German client lists "Hagelkorn" where an English one lists "Balestrom".
    The Favorite Realm setting therefore stores the *key*, which is the same on every
    machine, and the node matches a list row against every locale's text for that key.

    Yes/No is the same problem one page over: the Remember Realm option shows its value
    as localized text, and telling which way to toggle it means recognizing both words
    in all seven locales. They ride along in the same snapshot.

WHY A SNAPSHOT
    Nothing reads a WAD at run time (see tools/CLAUDE.md). ``tools/dump_realm_names.py``
    extracts the table into ``data/realm_names.json``; regenerate it when the game adds
    or retires a realm.
"""

from __future__ import annotations

import json
import logging
import re
from functools import cache
from pathlib import Path

logger = logging.getLogger(__name__)

REALM_NAMES_PATH = Path(__file__).resolve().parent / "data" / "realm_names.json"

# The game ships these seven. Kept in step with worlds.py and dialog_captions.py.
LOCALES: tuple[str, ...] = ("de", "el", "en-US", "es", "fr", "it", "pl")


@cache
def _snapshot() -> dict:
    try:
        return json.loads(REALM_NAMES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("Could not read realm names from %s; no realm can be picked or matched", REALM_NAMES_PATH)
        return {}


def _realm_names() -> dict[str, dict[str, str]]:
    """{realm key: {locale: display text}} from the checked-in snapshot."""
    return _snapshot().get("realms", {})


def realm_keys() -> list[str]:
    """Every realm the game offers, by key, in the snapshot's (alphabetical) order."""
    return list(_realm_names())


def is_known(key: str) -> bool:
    """Whether *key* names a realm the snapshot lists."""
    return key in _realm_names()


def display_names(key: str) -> list[str]:
    """Every text the realm list might show for *key*, deduped, en-US first.

    en-US leads because it is the one locale always present, so it is the sensible thing
    to put in a log line. Order is otherwise stable but not meaningful -- callers match
    against the whole list.
    """
    by_locale = _realm_names().get(key, {})
    ordered = [by_locale[loc] for loc in ("en-US", *LOCALES) if loc in by_locale]
    return list(dict.fromkeys(ordered))


def label(key: str) -> str:
    """The en-US display name, for the picker and log lines. Falls back to the key."""
    return next(iter(display_names(key)), key)


_MARKUP = re.compile(r"<[^>]*>")


def _fold(text: str) -> str:
    """Comparable form: the game's markup gone (el "Yes" is "<scale;0.90>Ναι!"), case and edges ignored."""
    return _MARKUP.sub("", text).strip().casefold()


def matches(key: str, shown: str) -> bool:
    """Whether a realm list row showing *shown* is the realm *key*.

    Case-insensitive and whitespace-tolerant, against every locale's text, because the
    row text has had the game's HTML stripped but is otherwise whatever locale the
    client runs in.
    """
    wanted = _fold(shown)
    return bool(wanted) and any(_fold(name) == wanted for name in display_names(key))


def key_for(shown: str) -> str | None:
    """The realm key a list row showing *shown* names, or None if no realm shows that."""
    wanted = _fold(shown)
    if not wanted:
        return None
    for key, by_locale in _realm_names().items():
        if any(_fold(text) == wanted for text in by_locale.values()):
            return key
    return None


def yes_no(shown: str) -> bool | None:
    """Read a localized Yes/No option value: True, False, or None if it is neither.

    None is the honest answer for an empty read or a word the snapshot does not know --
    a caller toggling on it would otherwise flip the option the wrong way.
    """
    wanted = _fold(shown)
    if not wanted:
        return None
    table: dict[str, dict[str, str]] = _snapshot().get("yes_no", {})
    if any(_fold(text) == wanted for text in table.get("Yes", {}).values()):
        return True
    if any(_fold(text) == wanted for text in table.get("No", {}).values()):
        return False
    return None
