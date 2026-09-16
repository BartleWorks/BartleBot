"""Turn a Wizard101 Central item name into the name the game itself displays.

The two are not the same, and the difference is load-bearing. A wiki page cannot
hold two entries under one title, so it disambiguates by appending the attribute
that tells the variants apart::

    wiki:    Balance Crushing Pin (60, Death)
    in-game: Balance Crushing Pin (60)

The game has no such problem: the quicksell window renders school as an *icon* in
its own column, so six pins — one per opposing school — all read as the identical
string. Anything that matches item names against text read out of that window
therefore has to match the in-game form, not the wiki form.

The consequence is worth stating plainly, because it constrains the UI: when six
drops collapse to one in-game name, keeping one of them keeps all six. No amount
of care in the keep list can separate them, because the text the node reads is
the same for all six.

Normalisation is deliberately conservative. A suffix is stripped only when it
appears in the table below; anything unrecognised is left exactly as it is, which
degrades to the behaviour that predates this module rather than to a wrong guess.
``tools/drop_name_patterns.py`` reports any suffix in the library that no rule
here accounts for, so a new one shows up as a failing check rather than as an
item that quietly sells.
"""

from __future__ import annotations

import re

#: Schools, as the wiki spells them in a disambiguating suffix. Star is the only one
#: that is also a jewel shape, which is why the school rule needs a category.
SCHOOLS: frozenset[str] = frozenset(
    {"Fire", "Ice", "Storm", "Myth", "Life", "Death", "Balance", "Sun", "Moon", "Star", "Shadow"}
)

#: The five jewel shapes, split by whether the name is also a school's.
#:
#: These four identify a jewel on their own — ``Fire Trap Ruby (Triangle)`` is a
#: jewel whatever category the bot happens to record.
UNAMBIGUOUS_JEWEL_SHAPES: frozenset[str] = frozenset({"Tear", "Circle", "Square", "Triangle"})

#: Star is the one shape that collides with a school name: ``(Star)`` is a shape on
#: a jewel and the Star school on a treasure card, and the token alone cannot say
#: which, so that case falls to the category-gated school rule.
AMBIGUOUS_JEWEL_SHAPES: frozenset[str] = frozenset({"Star"})

#: The category whose names the school and shape suffixes disambiguate. Both rules
#: are gated on it, so an uncategorized drop keeps its suffix until a wiki
#: re-import gives it one.
_JEWEL_CATEGORY = "Jewels"

_TRAILING_PAREN = re.compile(r"\s*\(([^()]*)\)\s*$")
_LEVEL_AND_SCHOOL = re.compile(rf"^(\d+)\s*,\s*({'|'.join(sorted(SCHOOLS))})$")
_LEVEL_ONLY = re.compile(r"^\d+$")
_LEVEL_REQUIREMENT = re.compile(r"^Level\b", re.IGNORECASE)
_TIER = re.compile(r"^Tier\b", re.IGNORECASE)
#: A treasure card's pip cost, as in ``Shatter (2 Pip)``. Left alone: treasure cards
#: are in NEVER_QUICKSOLD_CATEGORIES, so whether the game shows it cannot matter.
_PIP_COST = re.compile(r"^\d+\s*Pips?$", re.IGNORECASE)

#: Suffixes seen in the shipped library that are left alone. Being here means
#: someone looked at the item and decided not to strip it — either because the
#: game shows the suffix too, or because the item belongs to a category quicksell
#: never touches (treasure cards, mostly) and so the answer cannot matter.
KNOWN_KEPT_SUFFIXES: frozenset[str] = frozenset({"Seed", "Inactive", "Attack", "No Card", "Wizard City", "-3"})

# Classification labels, shared with tools/drop_name_patterns.py.
KIND_LEVEL_AND_SCHOOL = "level+school"
KIND_LEVEL = "level"
KIND_LEVEL_REQUIREMENT = "level requirement"
KIND_SCHOOL = "school"
KIND_JEWEL_SHAPE = "jewel shape"
KIND_TIER = "tier / pip cost"
KIND_KNOWN_KEPT = "known, kept"
KIND_NONE = "no suffix"
KIND_UNCLASSIFIED = "UNCLASSIFIED"

#: Kinds whose suffix the game does not show. Membership here does not mean the
#: suffix is always stripped — the school and shape rules still need a category.
WIKI_ONLY_KINDS: frozenset[str] = frozenset(
    {KIND_LEVEL_AND_SCHOOL, KIND_LEVEL_REQUIREMENT, KIND_SCHOOL, KIND_JEWEL_SHAPE}
)


def suffix_of(name: str) -> str | None:
    """The text inside a trailing ``(...)``, or None when the name has none."""
    match = _TRAILING_PAREN.search(name)
    return match.group(1).strip() if match else None


def classify_suffix(suffix: str | None) -> str:
    """Label a trailing suffix as one of the KIND_* constants.

    The label describes the suffix alone. Whether it is actually stripped can
    depend on the drop's category as well — see :func:`in_game_name`.
    """
    if suffix is None:
        return KIND_NONE
    if _LEVEL_AND_SCHOOL.match(suffix):
        return KIND_LEVEL_AND_SCHOOL
    if _LEVEL_ONLY.match(suffix):
        return KIND_LEVEL
    if _LEVEL_REQUIREMENT.match(suffix):
        return KIND_LEVEL_REQUIREMENT
    if _TIER.match(suffix) or _PIP_COST.match(suffix):
        return KIND_TIER
    if suffix in UNAMBIGUOUS_JEWEL_SHAPES:
        return KIND_JEWEL_SHAPE
    if suffix in AMBIGUOUS_JEWEL_SHAPES or suffix in SCHOOLS:
        return KIND_SCHOOL
    if suffix in KNOWN_KEPT_SUFFIXES:
        return KIND_KNOWN_KEPT
    return KIND_UNCLASSIFIED


def in_game_name(name: str, category: str | None = None) -> str:
    """The name the quicksell window shows for the item the wiki calls *name*.

    Returns *name* unchanged when no rule applies, so this is safe to call on any
    string, including one that is already an in-game name.

    Rules, in the order they are tried:

    ``Balance Crushing Pin (60, Death)`` → ``Balance Crushing Pin (60)``
        The level is shown; the school it opposes is not. Applies whatever the
        category, because only pins take this form.

    ``Ring of Gorgon Imprisonment (Level 90+)`` → ``Ring of Gorgon Imprisonment``
        A wiki annotation for the level the gear requires; never shown in-game.

    ``Fire Trap Ruby (Triangle)`` → ``Fire Trap Ruby``
        The jewel's shape. Stripped without a category when the shape name cannot
        be a school, since nothing else takes that suffix.

    ``Bright Critical Amethyst +44 (Ice)`` → ``Bright Critical Amethyst +44``
        A jewel's school, but only for a drop recorded as a jewel: the same suffix
        on a treasure card (``Mutate Snowman (Fire)``) is left alone, both because
        the answer there is unverified and because treasure cards never reach the
        quicksell window anyway.
    """
    suffix = suffix_of(name)
    if suffix is None:
        return name

    kind = classify_suffix(suffix)
    if kind == KIND_LEVEL_AND_SCHOOL:
        level = _LEVEL_AND_SCHOOL.match(suffix).group(1)
        return _TRAILING_PAREN.sub(f" ({level})", name)
    if kind == KIND_LEVEL_REQUIREMENT or kind == KIND_JEWEL_SHAPE:
        return _TRAILING_PAREN.sub("", name)
    if kind == KIND_SCHOOL and category == _JEWEL_CATEGORY:
        return _TRAILING_PAREN.sub("", name)
    return name


def match_name_for(name: str, category: str | None = None) -> str | None:
    """The value to store as ``DropModel.match_name``, or None when it adds nothing.

    None means "the same as the display name", which keeps the common case out of
    the bot file entirely — ``model_dump(exclude_none=True)`` drops it.
    """
    resolved = in_game_name(name, category)
    return None if resolved == name else resolved


#: The drop category whose items come in the three pin kinds below.
PIN_CATEGORY = "Pins"

#: Pin type word -> the kind the game files it under. The bank and quicksell windows
#: filter pins by these three kinds, one button each (Power, Shield, Sword), and a pin's
#: kind is the one word in its name that says what it does -- ``Balance Blocking Pin
#: (150, Death)`` is a Shield pin. Written out because the grouping is the game's and
#: cannot be derived from the words themselves.
PIN_KINDS: dict[str, str] = {
    "Accurate": "power",
    "Conversion": "power",
    "Blocking": "shield",
    "Resist": "shield",
    "Mending": "shield",
    "Punishing": "sword",
    "Disabling": "sword",
    "Crushing": "sword",
}

#: The type word must sit directly before "Pin", so a school or a word elsewhere in the
#: name cannot be mistaken for it.
_PIN_TYPE = re.compile(rf"\b({'|'.join(PIN_KINDS)})\s+Pin\b")


def pin_kind(name: str) -> str | None:
    """The kind of pin *name* is ("power", "shield" or "sword"), or None if it names none.

    Works on the wiki name and the in-game name alike, since the type word is in both.
    None for a pin type not in :data:`PIN_KINDS`, which callers treat as "a pin of no
    known kind" rather than as an error.
    """
    match = _PIN_TYPE.search(name)
    return PIN_KINDS[match.group(1)] if match else None
