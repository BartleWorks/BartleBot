"""Decode a wizard's packed name keys into text.

Wizard101 never stores a character's name as text in the client. It stores three indices
into the game's own name tables, packed into the uint32 ``m_nameKeys`` on
``WizPlayerNameBehavior``, and resolves them against a dictionary at draw time. This module
turns that uint32 back into a name.

THE ENCODING
    m_nameKeys is read most-significant-byte first:

        byte0  flags     3 for male, 2 for female in every sample so far
        byte1  first     index into FirstName_HumanMale / FirstName_HumanFemale, 0-based
        byte2  middle    index into MiddleName_Human,  1-based (0 means absent)
        byte3  last      index into LastName_Human,    1-based (0 means absent)

    Male and female have separate first-name tables; the middle and last tables are shared.
    Gender comes from ``m_eGender`` on the same behavior -- 1 male, 0 female, both observed.

    The mixed index bases are the trap. Treating middle and last as 0-based shifts them by
    one and produces a plausible but wrong name rather than an error, so a bug here looks
    like working code.

THE TABLES
    Read from ``data/name_tables.json``, a snapshot of the game's own tables extracted by
    ``tools/dump_name_tables.py``. The app does not read Root.wad at runtime: that would
    mean a 170k-entry archive read on every lookup and a hard dependency on the game being
    installed at a guessable path.

    A snapshot is safe because KI appends to these tables and never inserts -- the server
    stores each character's name as indices, so an insertion would rename every existing
    wizard. A stale snapshot therefore cannot corrupt a known name; it can only fail to
    know a newly added one, which surfaces as an index past the end of the table.
    ``decode_name_keys`` returns None in that case rather than guessing; ``wizard_name``
    turns that into a visible "Unknown". Regenerate with tools/dump_name_tables.py.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import cache
from pathlib import Path

logger = logging.getLogger(__name__)

TABLES_PATH = Path(__file__).resolve().parent / "data" / "name_tables.json"

FIRST_MALE = "FirstName_HumanMale"
FIRST_FEMALE = "FirstName_HumanFemale"
MIDDLE = "MiddleName_Human"
LAST = "LastName_Human"
TABLE_NAMES = (FIRST_MALE, FIRST_FEMALE, MIDDLE, LAST)

DEFAULT_LOCALE = "en-US"

# m_eGender on WizPlayerNameBehavior, both values confirmed against live characters.
# Note female is 0, not 2 -- so any `gender or DEFAULT` idiom silently turns a female
# wizard male. Test for None explicitly.
GENDER_MALE = 1
GENDER_FEMALE = 0

# byte0 of m_nameKeys tracks gender exactly across every recorded sample -- 3 for male,
# 2 for female, over three male and two female characters. It behaves like a first-name
# table selector, which makes m_nameKeys self-describing. m_eGender is still preferred when
# the caller has it, since it is the directly named field; byte0 is the fallback.
FLAGS_GENDER = {3: GENDER_MALE, 2: GENDER_FEMALE}

# Shown instead of a name when the tables cannot resolve one. Deliberately visible: it
# means the checked-in snapshot is older than the client and needs regenerating with
# tools/dump_name_tables.py, and a silent fallback would hide that indefinitely.
UNKNOWN_NAME = "Unknown"


@dataclass(frozen=True)
class NameTables:
    """The ordered name tables for one locale, resolved to display text."""

    first_male: tuple[str, ...]
    first_female: tuple[str, ...]
    middle: tuple[str, ...]
    last: tuple[str, ...]

    def first(self, gender: int) -> tuple[str, ...]:
        return self.first_male if gender == GENDER_MALE else self.first_female


@cache
def load_name_tables(locale: str = DEFAULT_LOCALE, path: Path | None = None) -> NameTables | None:
    """Load the checked-in tables for a locale, or None if unavailable.

    Cached: the snapshot never changes at runtime, and re-reading it per name lookup would
    be pure waste.
    """
    tables_path = path or TABLES_PATH
    try:
        raw = json.loads(tables_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("could not read name tables from %s", tables_path)
        return None

    locales = raw.get("locales", {})
    entry = locales.get(locale) or locales.get(DEFAULT_LOCALE)
    if entry is None:
        logger.warning("no name tables for locale %r in %s", locale, tables_path)
        return None

    return NameTables(
        first_male=tuple(entry.get(FIRST_MALE, ())),
        first_female=tuple(entry.get(FIRST_FEMALE, ())),
        middle=tuple(entry.get(MIDDLE, ())),
        last=tuple(entry.get(LAST, ())),
    )


def split_name_keys(value: int) -> tuple[int, int, int, int]:
    """Split m_nameKeys into (flags, first, middle, last), most significant byte first."""
    return ((value >> 24) & 0xFF, (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


def _lookup(table: tuple[str, ...], index: int, one_based: bool) -> str | None:
    """Resolve one index. Returns "" for an absent part, None if out of range."""
    if one_based:
        if index == 0:
            return ""
        index -= 1
    if not 0 <= index < len(table):
        return None
    return table[index]


def decode_name_keys(
    value: int,
    gender: int | None = None,
    locale: str = DEFAULT_LOCALE,
    tables: NameTables | None = None,
) -> str | None:
    """Decode m_nameKeys into "First MiddleLast", or None if it cannot be resolved.

    ``gender`` is m_eGender from the same behavior. Pass it whenever it is available; it is
    directly observed and authoritative. When it is None the gender is inferred from byte0,
    which has matched in every sample but is the weaker signal (see FLAGS_GENDER).

    None means the snapshot is older than the client -- an index landed past the end of a
    table, which is what a newly added game name looks like. Callers should fall back to
    the keypress route rather than showing a guess.
    """
    if tables is None:
        tables = load_name_tables(locale)
    if tables is None:
        return None

    flags, first_idx, middle_idx, last_idx = split_name_keys(value)
    if gender is None:
        gender = FLAGS_GENDER.get(flags, GENDER_MALE)
    first = _lookup(tables.first(gender), first_idx, one_based=False)
    middle = _lookup(tables.middle, middle_idx, one_based=True)
    last = _lookup(tables.last, last_idx, one_based=True)

    if first is None or middle is None or last is None:
        logger.warning(
            "m_nameKeys %d has an index past the end of the name tables "
            "(first=%d middle=%d last=%d); regenerate with tools/dump_name_tables.py",
            value,
            first_idx,
            middle_idx,
            last_idx,
        )
        return None

    # The game renders the two surname parts joined: Skull + Hunter is "SkullHunter".
    return f"{first} {middle}{last}".strip()


def wizard_name(
    value: int,
    gender: int | None = None,
    locale: str = DEFAULT_LOCALE,
    tables: NameTables | None = None,
) -> str:
    """Decode m_nameKeys for display, falling back to UNKNOWN_NAME.

    Use this anywhere a name is shown to the user. ``decode_name_keys`` stays the honest
    primitive -- it returns None so callers that need to *act* on an unresolved name (retry,
    fall back to the keypress route) can tell the difference. This one is for display, where
    a visible "Unknown" is the point.
    """
    return decode_name_keys(value, gender, locale, tables) or UNKNOWN_NAME
