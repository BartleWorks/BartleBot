"""Resolve a card's internal template name to what the player actually sees.

``SpellTemplate.name()`` -- what ``deck_io.py`` stores and keys decks by -- is a
stable identity, not display text: tiered/fusion variants print as e.g. "Novus
Storm - T05 - A", and some templates have been renamed in-game since ("Soul
Burner" now displays as "Ammut") while keeping their original internal name.
``deck_io.py`` must keep keying on the internal name -- see the comment at the
top of that module on why ``spell_base()`` is unreliable -- so resolution only
ever feeds the display layer.

Resolution is a lookup into ``data/spell_names.json``, a snapshot extracted from
Root.wad by ``tools/dump_spell_names.py``; see that file for how the mapping is
built and why a snapshot is safe here. A card missing from the snapshot (never
got a player-facing name, or was added to the game after the snapshot was taken)
falls back to its internal name rather than showing nothing -- the same choice
``deck_content.py`` already made for unviewable treasure cards.

The same snapshot answers the reverse question in :func:`name_problem`: whether a
spell name written in a combat DSL can find a card at all. The fight looks cards up
by internal name, and players write the name they see.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

NAMES_PATH = Path(__file__).resolve().parent / "data" / "spell_names.json"

# Tiered/fusion variants key the deck by e.g. "Kraken - T05 - A" (also seen with a
# trailing " - TC" or " - Pet"); the display map collapses all of them to the base
# name "Kraken", which loses the path and tier the user relies on to tell them
# apart. Pull it back out of the internal name rather than the display name, since
# the latter has already lost it.
_TIER_PATH_RE = re.compile(r"-\s*T0(\d)\s*-\s*([A-C])\b")


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
    """The name the player sees for a card, or its internal name if unresolved.

    A tiered/fusion variant (e.g. "Kraken - T05 - A") has its path and tier appended,
    since those matter to the player and the resolved name alone no longer shows them.
    """
    base = _load_names(path).get(internal_name, internal_name)
    match = _TIER_PATH_RE.search(internal_name)
    if not match:
        return base
    tier, spell_path = match.groups()
    return f"{base} (Path {spell_path}, Tier {tier})"


# ---------------------------------------------------------------------------
# Checking a spell name written in a combat DSL
# ---------------------------------------------------------------------------

#: One word an unquoted DSL name can hold. The grammar reads a digit as a word of its own.
_PLAIN_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]")
#: What an unquoted ``Detonate All 200`` reaches the card lookup as: a word per digit.
_SPLIT_NUMBER = re.compile(r"\b\d(?: \d)+\b")
#: How a player's spell names its treasure card. Mob spells have none.
_TREASURE_SUFFIXES = (" tc", " - tc", "_tc")
#: Names the game has a card for, but not one a player can hold: lowercased name → (the
#: name as the game capitalizes it, the card players get instead, what to write). The snapshot cannot tell a player's card from
#: a monster's, so these are known rather than derived. Mass Feint is the one that matters:
#: among the most-used spells in the game, a template exists under exactly that name, and
#: players only ever get the item card (template 453158534), so a DSL naming it never casts.
_PLAYERS_HOLD: dict[str, tuple[str, str, str]] = {
    "mass feint": ("Mass Feint", "Feint Mass - Amulet", "Feint Mass"),
}


@dataclass(frozen=True, eq=False)
class _NameIndex:
    """The snapshot, arranged for the questions :func:`name_problem` asks."""

    names: dict[str, str]
    #: Every internal name, lowercased — what an unquoted name is searched inside.
    lowered: tuple[str, ...]
    lowered_set: frozenset[str]
    #: Lowercased internal name → the internal name.
    by_lower: dict[str, str]
    #: Lowercased display name → every internal name shown with it.
    shown_as: dict[str, tuple[str, ...]]
    #: Lowercased display name → the display name.
    display: dict[str, str]
    #: Every display and internal name, lowercased, to guess a typo from.
    pool: tuple[str, ...]


@cache
def _name_index(path: Path | None = None) -> _NameIndex:
    names = _load_names(path)
    by_lower: dict[str, str] = {}
    shown_as: dict[str, list[str]] = {}
    display: dict[str, str] = {}
    for internal, shown in names.items():
        by_lower.setdefault(internal.lower(), internal)
        if shown.strip():
            shown_as.setdefault(shown.lower(), []).append(internal)
            display.setdefault(shown.lower(), shown)
    lowered = tuple(internal.lower() for internal in names)
    return _NameIndex(
        names=names,
        lowered=lowered,
        lowered_set=frozenset(lowered),
        by_lower=by_lower,
        shown_as={shown: tuple(internals) for shown, internals in shown_as.items()},
        display=display,
        pool=tuple(sorted(set(lowered) | set(display))),
    )


def name_problem(name: str, *, literal: bool, path: Path | None = None) -> str | None:
    """Why *name*, written as a spell in a combat DSL, will never find a card — or None.

    Mirrors ``SprintyCombat.try_get_spell``, which looks a card up by its internal name:
    unquoted, *name* matches any card whose internal name contains it, ignoring case;
    quoted (*literal*), only a card named exactly that. A name that matches nothing is
    not an error to the game — the move just never casts — so this is the only place
    the mistake gets a voice.

    Beyond a typo, it catches the name the player actually sees. "Incindiate" is the
    card ``Detonate All`` in-game, and an unquoted Incindiate still matches something —
    one mob's spell — so a check for "matches no card at all" passes it. A display name
    is reported when most of the cards shown with it do not contain it.

    The snapshot can be older than the game, so a spell added since is reported too:
    show this as a warning, never as an error. A missing snapshot reports nothing.
    """
    held = _PLAYERS_HOLD.get(name.lower())
    if held is not None:
        spelled, card, written = held
        return f"You can only get {spelled} as the item card {card}, so write {written} instead."
    index = _name_index(path)
    if not index.names:
        return None
    lowered = name.lower()
    if literal and name in index.names:
        return None
    if literal and lowered in index.by_lower:
        return f'"{name}" has to match a card\'s name exactly, capitals too: "{index.by_lower[lowered]}".'

    matched = _containing(index, lowered)
    renamed = _renamed(index, lowered, matched)
    if renamed is not None:
        shown, card, example = renamed
        named = card if card is not None else f"something else, such as {example}"
        return f"{shown} is the name shown in-game — the card is named {named}."
    if matched:
        if not literal:
            return None
        return f'No card is named exactly "{name}". Without the quotes it matches any card with {name} in its name.'
    if not literal and _SPLIT_NUMBER.search(name):
        return (
            f"{name}: a number in an unquoted name is read a digit at a time. Leave it out, or quote the card's name."
        )
    guess = _guess(index, lowered)
    return f"No card has {name} in its name." + (f" Did you mean {guess}?" if guess else "")


@lru_cache(maxsize=4096)
def _containing(index: _NameIndex, lowered: str) -> int:
    """How many internal names contain *lowered* — how many cards an unquoted name matches."""
    return sum(lowered in name for name in index.lowered)


def _renamed(index: _NameIndex, lowered: str, matched: int) -> tuple[str, str | None, str] | None:
    """For a display name most of whose cards do not contain it: the display name, an
    unquoted name that does find them (or None), and the shortest of them."""
    missed = [internal for internal in index.shown_as.get(lowered, ()) if lowered not in internal.lower()]
    if not missed or len(missed) <= matched:
        return None
    return index.display[lowered], _card_name_for(index, missed), min(missed, key=len)


def _card_name_for(index: _NameIndex, missed: list[str]) -> str | None:
    """An unquoted name that finds the cards in *missed*, or None.

    The candidates are each internal name and each of its `` - `` parts, cut to what an
    unquoted name can hold. A candidate has to cover two of the cards, and be specific:
    at least half the cards containing it are among them, or a mob template's one-off
    suffix wins. Of those, one with a treasure card goes first — only a player's spell
    has one — then the one covering the most.
    """
    best, best_key = None, None
    for internal in missed:
        for part in {internal, *internal.split(" - ")}:
            candidate = _plain_prefix(part)
            if len(candidate) < 3:
                continue
            lowered = candidate.lower()
            covered = sum(lowered in name.lower() for name in missed)
            if covered < 2 or covered * 2 < _containing(index, lowered):
                continue
            key = (_has_treasure_card(index, lowered), covered, -len(candidate), candidate)
            if best_key is None or key > best_key:
                best, best_key = candidate, key
    return best


def _plain_prefix(text: str) -> str:
    words = []
    for word in text.split():
        if not _PLAIN_WORD.fullmatch(word):
            break
        words.append(word)
    return " ".join(words)


def _has_treasure_card(index: _NameIndex, lowered: str) -> bool:
    return any(f"{lowered}{suffix}" in index.lowered_set for suffix in _TREASURE_SUFFIXES)


def _guess(index: _NameIndex, lowered: str) -> str | None:
    """The card name *lowered* is probably a typo of, as it should be written."""
    close = difflib.get_close_matches(lowered, index.pool, n=1, cutoff=0.8)
    if not close:
        return None
    guess = close[0]
    renamed = _renamed(index, guess, _containing(index, guess))
    if renamed is not None and renamed[1] is not None:
        return renamed[1]
    return index.by_lower.get(guess) or index.display.get(guess, guess)
