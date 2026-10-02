"""The Scales of Knowledge: every place a player can take the level scaling elixir.

WHY A TABLE
    The game has a small, fixed set of Scales, roughly 25, and the only thing a bot's author
    chooses is which one. Everything else a trip there needs (the entrance, the sigil, the
    interact point) is the same for every bot that uses it, so it is recorded once here
    rather than in each bot. ``handle_level_scaling`` takes an entry's key as its one param.

WHAT AN ENTRY HOLDS
    ``entrance_zone`` is a destination on the zone navigation map: where zone navigation
    hands over. ``steps`` lead from it to ``zone``, the zone the Scales stand in. Each step
    is a dungeon sigil or a gateway walked through. Most entries are one sigil; the Scales
    of a secret boss nested in another dungeon add a gateway after it, and a dungeon whose
    sigil is indoors has gateways before it.

    The data is names, zone paths and coordinates, so it is text and safe to track (see
    "No game assets" in the root CLAUDE.md). ``docs/LEVEL_SCALING.md`` has the rest.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from wizwalker import XYZ

from bartlebot import worlds

logger = logging.getLogger(__name__)

SCALES_PATH = Path(__file__).resolve().parent / "data" / "scales_of_knowledge.json"

SIGIL = "sigil"
"""A step through a dungeon sigil: stand on it, answer its entrance prompt."""
GATEWAY = "gateway"
"""A step through a zone transition: stand in its bounding box."""
STEP_KINDS = (SIGIL, GATEWAY)


@dataclass(frozen=True)
class Step:
    """One zone change on the way from an entry's entrance zone to its Scales."""

    kind: str
    from_zone: str
    to_zone: str
    xyz: XYZ
    reset_xyz: XYZ


@dataclass(frozen=True)
class Scales:
    """One Scales of Knowledge."""

    key: str
    name: str
    world: str
    level: int | None
    entrance_zone: str
    steps: tuple[Step, ...]
    zone: str
    interact_xyz: XYZ
    reset_xyz: XYZ
    world_default: bool = False
    """The Scales a bot farming this world uses when its node names none: the world's final dungeon."""

    @property
    def dungeon_from(self) -> int:
        """The index of the first sigil step. Players past it are inside the Scales' dungeon."""
        return next(i for i, step in enumerate(self.steps) if step.kind == SIGIL)


def _xyz(raw: dict) -> XYZ:
    return XYZ(float(raw["x"]), float(raw["y"]), float(raw["z"]))


def _parse(raw: dict) -> Scales:
    steps = tuple(
        Step(s["kind"], s["from_zone"], s["to_zone"], _xyz(s["xyz"]), _xyz(s["reset_xyz"])) for s in raw["steps"]
    )
    if not any(step.kind == SIGIL for step in steps):
        raise ValueError("no sigil step")
    if unknown := [step.kind for step in steps if step.kind not in STEP_KINDS]:
        raise ValueError(f"unknown step kind {unknown[0]!r}")
    level = raw.get("level")
    return Scales(
        key=raw["key"],
        name=raw["name"],
        world=raw["world"],
        level=None if level is None else int(level),
        entrance_zone=raw["entrance_zone"],
        steps=steps,
        zone=raw["zone"],
        interact_xyz=_xyz(raw["interact_xyz"]),
        reset_xyz=_xyz(raw["reset_xyz"]),
        world_default=bool(raw.get("world_default", False)),
    )


@cache
def load() -> dict[str, Scales]:
    """Every Scales by key, in the file's order. An entry that cannot be read is logged and left out."""
    try:
        entries = json.loads(SCALES_PATH.read_text(encoding="utf-8"))["scales"]
    except (OSError, ValueError, KeyError, TypeError):
        logger.exception("Could not read the Scales of Knowledge from %s; no Scales can be picked", SCALES_PATH)
        return {}
    table: dict[str, Scales] = {}
    for raw in entries:
        try:
            scales = _parse(raw)
        except (KeyError, TypeError, ValueError) as error:
            logger.error("Skipping a Scales of Knowledge entry that cannot be read (%s): %r", error, raw)
            continue
        table[scales.key] = scales
    return table


def get(key: str) -> Scales | None:
    """The Scales stored under `key`, or None when the table has none."""
    return load().get(key) if isinstance(key, str) else None


def is_known(value: object) -> bool:
    """Whether a ``scales`` param value names a Scales. An empty one, or an unloadable table, passes."""
    if not value or not isinstance(value, str):
        return True
    table = load()
    return not table or value in table


def world_default(world: str | None) -> Scales | None:
    """The default Scales of `world`, or None when the table marks none for it."""
    return next((entry for entry in load().values() if entry.world_default and entry.world == world), None)


def bot_world(config) -> str | None:
    """The world a bot farms: the one most of its zone params are in. None when none leads.

    Read from the params rather than stored, so it follows the bot as its zones are filled
    in. A param counts when its key says it holds a zone (``zone`` or ``*_zone``), which
    ``test_every_param_named_for_a_zone_is_typed_as_one`` keeps true of every zone param.
    """
    counts: dict[str, int] = {}
    for state in getattr(config, "states", None) or ():
        for key, value in (state.params or {}).items():
            if (key == "zone" or key.endswith("_zone")) and isinstance(value, str) and "/" in value:
                world = value.split("/", 1)[0]
                counts[world] = counts.get(world, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return None
    return ranked[0][0]


def default_for(config) -> Scales | None:
    """The Scales a ``scales`` param left empty means in this bot: its world's default, if it has one."""
    return world_default(bot_world(config))


def resolve(key: object, config) -> Scales | None:
    """The Scales a ``scales`` param value names in this bot. An empty one is the bot's world's default."""
    return get(key) if key else default_for(config)


def describe(scales: Scales) -> str:
    """ "Aquila · Tartarus (level 90)": the world leads, as the picker's sections read."""
    level = "" if scales.level is None else f" (level {scales.level})"
    return f"{worlds.label(scales.world)} · {scales.name}{level}"
