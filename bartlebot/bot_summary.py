"""The one-sentence description a bot's graph implies, for a bot whose author wrote none.

Most library descriptions say the same thing — "Defeats Lord Nightshade in Nightshade's
Tower, Haunted Cave, Wizard City" — and a bot made from a template rarely gets one at all,
because nobody goes back to write it. Everything that sentence says is already in the graph.

Shown, never stored. The generated sentence stands in wherever the description would show,
only while the description is empty; anything the author writes replaces it outright, which
is also how a wrong guess gets corrected. Writing it into the file would freeze a sentence
that no longer matches the graph the first time the graph changes.

Two layers, kept apart on purpose:

- **Facts** (:func:`bot_facts`): the fights, the entrance the boss is behind, the gauntlet,
  the zones reagents are collected in. A later template guide wants these too, to check
  whether a step has been done, so they carry no phrasing.
- **Phrasing** (:func:`describe`): one sentence from the facts, or None when they say
  nothing worth a sentence. Saying nothing beats saying something vague.

The shapes, first match wins:

=====================  ==============================================================
a gauntlet             "Completes <gauntlet>."
exactly one fight      "Defeats <boss> in <dungeon>, <street>, <world>."
any other fights       "Completes <dungeon>, <world>."
reagents, no fights    "Collects reagents in <zone>, <zone>, and <zone> in <world>."
a dungeon, no fights   "Completes <dungeon>, <world>."
=====================  ==============================================================

A dungeon with several fights is named rather than its bosses, because many have several
bosses and a list of them reads as a roster, not a description. Every ``handle_combat``
counts as a fight, named or not: a dungeon with one named fight and seven blank ones is
still a dungeon run.

**Where the dungeon's name comes from.** An interior zone often shares its street's name
key, so Galvanost Tower and Skull Fort both read as "Triton Avenue". The entrance prompt's
title is the real name; Record keeps it in ``dungeon_name`` (and ``gauntlet_name``). The
zone's name stands in only when it differs from the street's, and a sentence missing a part
drops that part rather than guessing it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bartlebot import entity_names, worlds, zone_names, zone_nav
from bartlebot.wiki_pages import page_title

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

FIGHT = "handle_combat"
DUNGEON_ENTRANCE = "enter_dungeon"
GAUNTLET_ENTRANCE = "enter_gauntlet"
REAGENT = "collect_reagent"

#: Reagent zones named one by one up to this many; past it, only their world is.
MAX_LISTED_ZONES = 3

# A wiki title's namespace ("Creature:") and trailing aside ("(Challenge)").
_NAMESPACE = re.compile(r"^[^:]*:")
_ASIDE = re.compile(r"\s*\([^()]*\)\s*$")


@dataclass(frozen=True)
class Entrance:
    """A dungeon entrance, as a reader names it. Any part may be unknown."""

    #: The dungeon's own name: ``dungeon_name``, else its zone's name if that is not the street's.
    name: str | None
    #: The zone the entrance stands in.
    street: str | None
    world: str | None


@dataclass(frozen=True)
class BotFacts:
    """What a bot's graph says about what it does, in run order."""

    #: Each fight's enemy as a player reads it, None where the node names none.
    fights: tuple[str | None, ...] = ()
    #: The entrance the last fight is behind, or the last entrance when there is no fight.
    dungeon: Entrance | None = None
    #: Whether the bot enters a gauntlet, and the gauntlet's name if known.
    is_gauntlet: bool = False
    gauntlet: str | None = None
    #: The zones reagents are collected in, as readers name them, deduped.
    reagent_zones: tuple[str, ...] = ()
    #: The worlds those zones are in, deduped.
    reagent_worlds: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def describe(config: BotConfig) -> str | None:
    """The sentence *config*'s graph implies, or None when it implies nothing worth saying."""
    return phrase(bot_facts(config))


def describe_data(data: Mapping[str, Any]) -> str | None:
    """:func:`describe` for a bot file's raw JSON, as the library reads it. Never raises."""
    try:
        states = [(s["id"], s["node_type"], s.get("params") or {}) for s in data.get("states") or []]
        edges = [(t["source"], t["dest"], t.get("when")) for t in data.get("transitions") or []]
        return phrase(_facts(data.get("initial"), states, edges))
    except (KeyError, TypeError, AttributeError):
        return None


def bot_facts(config: BotConfig) -> BotFacts:
    """The facts :func:`describe` phrases, from a loaded bot."""
    states = [(s.id, s.node_type, s.params) for s in config.states]
    edges = [(t.source, t.dest, t.when) for t in config.transitions]
    return _facts(config.initial, states, edges)


def phrase(facts: BotFacts) -> str | None:
    """One sentence from *facts*: the first shape in the module docstring's table that fits."""
    if facts.is_gauntlet:
        return f"Completes {facts.gauntlet}." if facts.gauntlet else None
    dungeon = facts.dungeon
    if len(facts.fights) == 1 and facts.fights[0]:
        parts = [dungeon.name, dungeon.street, dungeon.world] if dungeon else []
        return f"Defeats {facts.fights[0]}{_in(parts)}."
    if facts.fights:
        return _completes(dungeon)
    if facts.reagent_zones:
        if len(facts.reagent_zones) > MAX_LISTED_ZONES:
            return f"Collects reagents in {_join(facts.reagent_worlds)}." if facts.reagent_worlds else None
        where = _join(facts.reagent_zones)
        if len(facts.reagent_worlds) == 1:
            where += f" in {facts.reagent_worlds[0]}"
        return f"Collects reagents in {where}."
    return _completes(dungeon)


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


def _facts(
    initial: str | None,
    states: list[tuple[str, str, Mapping[str, Any]]],
    edges: Iterable[tuple[str, str, str | None]],
) -> BotFacts:
    by_id = {sid: (node_type, params) for sid, node_type, params in states}
    ordered = [by_id[sid] for sid in run_order(initial, [sid for sid, _, _ in states], edges)]

    fights: list[str | None] = []
    entrance: Mapping[str, Any] | None = None
    boss_entrance: Mapping[str, Any] | None = None
    gauntlet: Mapping[str, Any] | None = None
    zones: list[str] = []
    for node_type, params in ordered:
        if node_type == FIGHT:
            fights.append(_enemy_name(params))
            boss_entrance = entrance
        elif node_type == DUNGEON_ENTRANCE:
            entrance = params
        elif node_type == GAUNTLET_ENTRANCE and gauntlet is None:
            gauntlet = params
        elif node_type == REAGENT and isinstance(zone := params.get("zone"), str) and zone:
            zones.append(zone)

    dungeon = boss_entrance if fights else entrance
    zones = list(dict.fromkeys(zones))
    return BotFacts(
        fights=tuple(fights),
        dungeon=_entrance(dungeon) if dungeon is not None else None,
        is_gauntlet=gauntlet is not None,
        gauntlet=_gauntlet_name(gauntlet) if gauntlet is not None else None,
        reagent_zones=tuple(n for z in zones if (n := _zone_name(z))),
        reagent_worlds=tuple(dict.fromkeys(w for z in zones if (w := _world_name(z)))),
    )


def run_order(initial: str | None, state_ids: list[str], edges: Iterable[tuple[str, str, str | None]]) -> list[str]:
    """*state_ids* in the order a run meets them: a depth-first walk from *initial*.

    A node's success edges are followed before its others, so the bot's main line comes
    first and its recovery branches after it. States the walk never reaches follow in file
    order — a graph mid-edit is often in pieces, and its pieces still say what it does.
    """
    known = set(state_ids)
    succ: dict[str, list[tuple[bool, str]]] = defaultdict(list)
    for source, dest, when in edges:
        if source in known and dest in known:
            succ[source].append((when not in (None, "SUCCESS"), dest))
    for targets in succ.values():
        targets.sort(key=lambda t: t[0])  # stable: file order within each kind

    start = initial if initial in known else (state_ids[0] if state_ids else None)
    seen: set[str] = set()
    order: list[str] = []
    stack = [start] if start is not None else []
    while stack:
        sid = stack.pop()
        if sid in seen:
            continue
        seen.add(sid)
        order.append(sid)
        stack.extend(dest for _, dest in reversed(succ[sid]) if dest not in seen)
    order.extend(sid for sid in state_ids if sid not in seen)
    return order


def _enemy_name(params: Mapping[str, Any]) -> str | None:
    """A fight's enemy as a player reads it: the game's name for it, else its wiki page's."""
    enemy = params.get("enemy")
    if isinstance(enemy, str) and (name := entity_names.display_name(enemy.strip())):
        return name
    wiki = params.get("wiki_page")
    if isinstance(wiki, str) and (title := page_title(wiki)):
        return _ASIDE.sub("", _NAMESPACE.sub("", title)).strip() or None
    return None


def _entrance(params: Mapping[str, Any]) -> Entrance:
    from_zone = _str(params.get("from_zone"))
    dungeon_zone = _str(params.get("dungeon_zone"))
    street = _zone_name(from_zone)
    name = _str(params.get("dungeon_name")).strip() or None
    if name is None and (zone_name := _zone_name(dungeon_zone)) != street:
        name = zone_name
    return Entrance(name=name, street=street, world=_world_name(from_zone) or _world_name(dungeon_zone))


def _gauntlet_name(params: Mapping[str, Any]) -> str | None:
    return _str(params.get("gauntlet_name")).strip() or _zone_name(_str(params.get("dungeon_zone")))


def _zone_name(zone: str) -> str | None:
    """A zone's name as a reader knows it, or None where the game gives it none.

    ``zone_names.label`` falls back to the path's last segment, which is right for a node
    label and wrong for prose: "Defeats Sisyphus in AQ_Z01_TempleOfSisyphus*" says nothing.
    """
    if not zone:
        return None
    name = zone_names.label(zone)
    return None if name == zone.rsplit("/", 1)[-1] else name


def _world_name(zone: str) -> str | None:
    if not zone:
        return None
    world = zone_nav.load().world_of(zone)
    return worlds.label(world.key) if world is not None else None


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


# ---------------------------------------------------------------------------
# Phrasing
# ---------------------------------------------------------------------------


def _completes(dungeon: Entrance | None) -> str | None:
    """``Completes <dungeon>, <world>.`` — or where it is, when its own name is unknown."""
    if dungeon is None:
        return None
    if dungeon.name:
        return f"Completes {', '.join(_known([dungeon.name, dungeon.world]))}."
    if dungeon.street:
        return f"Completes a dungeon{_in([dungeon.street, dungeon.world])}."
    return None


def _in(parts: list[str | None]) -> str:
    """`` in A, B, C`` from the known *parts*."""
    kept = _known(parts)
    return f" in {', '.join(kept)}" if kept else ""


def _known(parts: list[str | None]) -> list[str]:
    """The *parts* that are known, each once: a dungeon named after its world says it once."""
    return list(dict.fromkeys(p for p in parts if p))


def _join(items: Iterable[str]) -> str:
    """``A``, ``A and B``, ``A, B, and C``."""
    items = list(items)
    if len(items) <= 2:
        return " and ".join(items)
    return f"{', '.join(items[:-1])}, and {items[-1]}"
