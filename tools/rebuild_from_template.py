"""Rebuild a shipped bot from the template it belongs to, keeping who it is.

The library migration (#59, ``docs/LIBRARY_MIGRATION.md``) moves every bot in ``bots/`` onto
one of the released templates. New Bot cannot do it: it mints a fresh id and fills nothing
in. This tool starts from the template's latest released version through
``new_bot.create_bot``, exactly as New Bot does, and then:

- keeps the old bot's id, name, description, tags, authors, drops, clients and version
  string, and its own option declarations (the template's are kept too; the bot's win);
- fills every setup step's fill-in it can find in the old file: the dungeon entry and exit,
  the fight's enemy, the Sync players zone, a mob's wait spot, a gauntlet's entry and a
  reagent's spawns. The node each value came from also lends its label. Dungeon Farmer's
  mark is filled from the entry: the entrance zone, at the sigil's Reset XYZ, and its world
  hub from the zone map: the hub of the world the Sync players zone is in;
- in a template that recalls out of its dungeon (Dungeon Farmer), treats the old bot's whole
  way out as the recall: the walk-out exit, a hub teleport or a teleporter out, an old
  recall, and the plain navigation that only led to them, such as walking back through the
  rooms to the door. Content that led there goes to the recall instead;
- in a Spellement bot, names its Scales when the world default is not the one it used, and
  moves ``handle_level_scaling`` after the dungeon entry when the farm dungeon is the
  Scales' own, as ``docs/LEVEL_SCALING.md`` says. A level scaling bot rebuilt from a
  template with no such node (a dungeon first, :data:`DUNGEONS_FIRST`) gets one of its own,
  straight after the entry;
- composes the bot before writing it, as New Bot does, so the file holds what opening it
  makes of it.

What it never does is carry the bot's own content: the rooms, interacts and extra fights a
dungeon adds after the template's frame. The report lists every old node it did not carry,
so that content can be put back in the app. The old file is in git.

Usage::

    uv run python tools/rebuild_from_template.py rebuild achaelos            # print the report, write nothing
    uv run python tools/rebuild_from_template.py rebuild achaelos --write    # write the rebuilt bot over it
    uv run python tools/rebuild_from_template.py rebuild big_salgio --template "Spellement Farmer"
    uv run python tools/rebuild_from_template.py status                      # every bot: category, template, steps left

A bot is named by its file stem or its path. Without ``--template`` the template is the one
its nodes say it is (see :func:`classify`).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bartlebot.bot.schema import (  # noqa: E402
    FALLTHROUGH_RETURNS,
    BotConfig,
    BotOptionModel,
    StateModel,
    TransitionModel,
)
from bartlebot.content.bot_tags import normalize_tags, suggested_tags  # noqa: E402
from bartlebot.content.new_bot import BotDetails, StartingPoint, create_bot, starting_points  # noqa: E402
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary  # noqa: E402
from bartlebot.content.subgraphs.subgraphs import compose_bot  # noqa: E402
from bartlebot.content.templates import setup_steps  # noqa: E402
from bartlebot.game import scales, zone_nav  # noqa: E402
from bartlebot.gui.canvas.edit_scene import _reachable_states  # noqa: E402
from bartlebot.gui.canvas.layout import auto_layout  # noqa: E402
from bartlebot.nodes.registry import NODE_SPECS  # noqa: E402
from bartlebot.options.bot_options import required_option_specs  # noqa: E402

BOTS_DIR = PROJECT_ROOT / "bots"

#: The template each category of bot is rebuilt from, by name.
TEMPLATE_NAMES = {
    "boss": "Boss Farmer",
    "dungeon": "Dungeon Farmer",
    "spellement": "Spellement Farmer",
    "reagent": "Reagent Collector",
    "mob": "Mob Farmer",
    "gauntlet": "Gauntlet Farmer",
}

#: Bots whose nodes say spellement but that are dungeons first: each farms a whole dungeon that
#: happens to drop spellements, and nobody farms it for them (decided 2026-10-03). They are
#: rebuilt from Dungeon Farmer, keeping their level scaling as a node of their own.
DUNGEONS_FIRST = frozenset({"atlantea", "mt_olympus", "the_great_spyre"})

#: Navigation the Sync players node replaces. A gauntlet's template has its own teleport_to_friend.
NAV_FOLD = frozenset(
    {"teleport_to_world_hub", "enter_zone_via_object", "check_zone", "teleport_to_friend", "relog", "sync_in_zone"}
)
#: The level scaling nodes handle_level_scaling replaces.
SCALING_FOLD = frozenset({"check_level_scaling", "apply_level_scaling"})
LEVEL_SCALING_TYPES = SCALING_FOLD | {"handle_level_scaling"}

#: Params carried from the node a template node is filled from, beyond its fill-ins: what the
#: old bot knew about its dungeon and fight that the template leaves blank.
CARRY_PARAMS = {
    # Whether the dungeon has a Challenge Mode is a fact about it, and content may be guarded
    # on the option it brings. Team-Up stays the template's: its defaults were set on purpose.
    "enter_dungeon": ("dungeon_name", "offers_challenge_mode"),
    "enter_gauntlet": ("gauntlet_name",),
    "handle_combat": ("wiki_page", "use_default_strategy", "max_duration"),
}

#: The template node types a value is looked for in the old bot, by role.
MAPPED_TYPES = ("enter_dungeon", "enter_zone", "handle_combat", "teleport_to_coordinates", "enter_gauntlet")

#: Template fill-ins read from the old bot's dungeon entry, by (node type, param): Dungeon
#: Farmer's mark is made in the entrance zone, at the spot the sigil's prompt does not reach.
ENTRY_FILLS = {
    ("mark_location", "zone"): "from_zone",
    ("mark_location", "xyz"): "reset_xyz",
}

#: Template fill-ins that name the hub of the world the players sync in, read from the zone map:
#: Dungeon Farmer goes there for wisps when a player has no mana to mark the entrance.
HUB_FILLS = frozenset({("teleport_to_world_hub", "zone")})

#: The node types a way out of a dungeon is made of: zone changes, teleports, and the steps
#: and pauses between them. Nothing that farms.
WAY_OUT_TYPES = frozenset(
    {
        "enter_zone",
        "enter_zone_via_object",
        "teleport_to_world_hub",
        "teleport_to_marked_location",
        "teleport_to_coordinates",
        "sleep",
        "check_zone",
        "sync_in_zone",
    }
)

#: What a run still has to do after a node for that node not to be the way out.
FARMING_TYPES = frozenset(
    {"handle_combat", "interact", "interact_menu", "click_path", "collect_reagent", "enter_dungeon"}
)

#: Params of the nodes the template replaces that stay the template's: the worksheet's
#: "set by every template" values, and potions' mark zone, whose place has moved.
TEMPLATE_SET = {
    "collect_wisps": ("only_safe", "timeout", "if_health_below"),
    "handle_potions": ("mark_zone",),
}

#: Author options that are not carried, and why. Every guard on one goes with it.
RETIRED_OPTIONS = {
    "Quicksell": "an author's own switch for selling, which Enable Quicksell, the user's setting, replaces",
}

#: How a report line about the drops fill-in starts (``setup_steps.describe_target``).
DROPS_LABEL = "Possible drops"

#: The Sync players node's promoted zone, as a template's setup step names it.
SYNC_ZONE_INPUT = "state_2.zone"

_FORWARD = (None, "SUCCESS", "TRUE")


class RebuildError(Exception):
    """A bot this tool cannot rebuild, with the reason."""


# ---------------------------------------------------------------------------
# Reading the old bot
# ---------------------------------------------------------------------------


def classify(config: BotConfig) -> str:
    """The template category a bot's nodes put it in, as the migration worksheet measures it.

    Any reagent collection → reagent; any gauntlet → gauntlet; any level scaling → spellement;
    combat with no dungeon → mob; one dungeon fight or none → boss; otherwise dungeon.
    """
    types = [s.node_type for s in config.states]
    fights = types.count("handle_combat")
    if "collect_reagent" in types:
        return "reagent"
    if "enter_gauntlet" in types:
        return "gauntlet"
    if LEVEL_SCALING_TYPES & set(types):
        return "spellement"
    if "enter_dungeon" not in types:
        return "mob" if fights else "other"
    return "boss" if fights <= 1 else "dungeon"


def category_of(path: Path, config: BotConfig) -> str:
    """The category a bot is rebuilt as: what its nodes say, unless it is a dungeon first."""
    return "dungeon" if path.stem in DUNGEONS_FIRST else classify(config)


def walk_order(config: BotConfig) -> list[StateModel]:
    """Every state, in the order a run first reaches it along its forward edges.

    Depth first from the initial state, taking ``SUCCESS``/``TRUE``/unconditional edges
    before any other, so a node's place reads as "how far into the run". States no edge
    reaches follow, in file order.
    """
    by_id = {s.id: s for s in config.states}
    edges: dict[str, list[tuple[int, str]]] = {}
    for tr in config.transitions:
        rank = 0 if tr.when in _FORWARD else 1
        edges.setdefault(tr.source, []).append((rank, tr.dest))
    seen: list[str] = []
    stack = [config.initial] if config.initial in by_id else []
    while stack:
        sid = stack.pop()
        if sid in seen or sid not in by_id:
            continue
        seen.append(sid)
        nexts = [dest for _rank, dest in sorted(edges.get(sid, []), key=lambda e: e[0])]
        stack.extend(reversed(nexts))
    ordered = [by_id[sid] for sid in seen]
    return ordered + [s for s in config.states if s.id not in seen]


def _scales_of(config: BotConfig) -> scales.Scales | None:
    """The Scales an old bot's ``apply_level_scaling`` stands on, or its ``handle_level_scaling`` names."""
    for state in config.states:
        params = state.params or {}
        if state.node_type == "apply_level_scaling":
            return next((e for e in scales.load().values() if e.zone == params.get("zone")), None)
        if state.node_type == "handle_level_scaling":
            return scales.resolve(params.get("scales"), config)
    return None


def inside_zones(entry: scales.Scales) -> set[str]:
    """The zones of a Scales' dungeon: past its sigil, the Scales' own zone included."""
    return {entry.zone, *(step.to_zone for step in entry.steps[entry.dungeon_from :])}


def trip_zones(entry: scales.Scales) -> set[str]:
    """Every zone the trip to a Scales enters, from its entrance zone on."""
    return {entry.zone, *(step.to_zone for step in entry.steps)}


@dataclass
class OldBot:
    """What the rebuild reads out of the old file: each role, and the nodes it folds away."""

    config: BotConfig
    category: str
    scales: scales.Scales | None = None
    entry: StateModel | None = None
    exit: StateModel | None = None
    fight: StateModel | None = None
    spot: StateModel | None = None
    gauntlet: StateModel | None = None
    spawns: list[StateModel] = field(default_factory=list)
    #: State ids the template's Sync players and handle_level_scaling replace.
    folded: set[str] = field(default_factory=set)
    #: Where to sync when the entrance is not a destination: the zone the old bot entered it from.
    sync_override: str = ""
    #: State ids that are the bot's own content even though the template has a node of their type.
    keep: set[str] = field(default_factory=set)
    #: State ids of the old way out of the farm, when the template recalls out instead (:func:`_way_out`).
    way_out: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    @property
    def sync_zone(self) -> str:
        """Where the Sync players node should gather everyone for this bot."""
        if self.sync_override:
            return self.sync_override
        if self.entry is not None:
            return self.entry.params.get("from_zone", "")
        if self.category == "reagent" and self.spawns:
            return self.spawns[0].params.get("zone", "")
        if self.category == "mob":
            synced = next((s for s in self.config.states if s.node_type == "sync_in_zone"), None)
            if synced is not None and synced.params.get("zone"):
                return synced.params["zone"]
            if self.spot is not None:
                return self.spot.params.get("zone", "")
        return ""


def read_old(config: BotConfig, category: str, nav: zone_nav.NavMap | None = None) -> OldBot:
    """Find each role the template has a node for, and what the template folds away."""
    nav = zone_nav.load() if nav is None else nav
    old = OldBot(config=config, category=category, scales=_scales_of(config))
    order = walk_order(config)
    # Sync players hops realms itself; only Reagent Collector has a change_realm of its own.
    fold = NAV_FOLD | SCALING_FOLD | ({"change_realm"} if category != "reagent" else set())
    old.folded = {s.id for s in config.states if s.node_type in fold}
    if category == "gauntlet":
        old.folded = {
            sid for sid in old.folded if next(s for s in config.states if s.id == sid).node_type != "teleport_to_friend"
        }

    dungeons = [s for s in order if s.node_type == "enter_dungeon"]
    if old.scales is not None and len(dungeons) > 1:
        # A dungeon entered only to reach the Scales is handle_level_scaling's trip, and so is
        # every zone change along the way there or out again.
        inside = trip_zones(old.scales)
        detours = [s for s in dungeons if s.params.get("dungeon_zone") in inside_zones(old.scales)]
        if detours and len(detours) < len(dungeons):
            dungeons = [s for s in dungeons if s not in detours]
            old.folded |= {s.id for s in detours}
            old.folded |= {
                s.id
                for s in config.states
                if s.node_type == "enter_zone"
                and (s.params.get("to_zone") in inside or s.params.get("from_zone") in inside)
            }
    if dungeons:
        old.entry = dungeons[0]
        if len(dungeons) > 1:
            others = ", ".join(s.id for s in dungeons[1:])
            old.notes.append(
                f"farm entry is {old.entry.id}, the first reached; the other dungeon entries ({others}) are content"
            )
        old.exit = _exit_of(old, order)

    fights = [s for s in order if s.node_type == "handle_combat"]
    if fights and category in ("boss", "spellement", "mob"):
        old.fight = fights[-1]
        if len(fights) > 1:
            old.notes.append(
                f"the template's fight is {old.fight.id}, the last one reached; the other {len(fights) - 1} are content"
            )
    old.spot = next((s for s in order if s.node_type == "teleport_to_coordinates"), None) if category == "mob" else None
    old.gauntlet = next((s for s in order if s.node_type == "enter_gauntlet"), None)
    old.spawns = [s for s in order if s.node_type == "collect_reagent"]
    _fold_travel(old, order, nav)
    # A teleporter from one of the farm's rooms to another is the dungeon's own, not travel.
    farm = _farm_zones(old)
    old.folded -= {
        s.id
        for s in config.states
        if s.node_type == "enter_zone_via_object"
        and s.params.get("from_zone") in farm
        and s.params.get("to_zone") in farm
    }
    return old


def _fold_travel(old: OldBot, order: list[StateModel], nav: zone_nav.NavMap) -> None:
    """Fold every zone change on the way to the farm: the trip there, which Sync players makes.

    The farm starts at the dungeon entry, else the first reagent spawn, the mob fight or the
    gauntlet entry. A zone change is on the way when it leads there and starts outside the
    farm's own zones -- a bot that walks back to its entrance on every loop still walks there.
    When the zone Sync players should gather in is not a destination, the last zone change into
    it is kept, and the bot syncs where it leaves from.
    """
    anchor = old.entry or (old.spawns[0] if old.spawns else None) or old.fight or old.gauntlet
    if anchor is None:
        return
    farm = _farm_zones(old)
    leads_in = {s.id for s in old.config.states if anchor.id in _reachable(old.config, s.id, stop=anchor.id)}
    travel = [
        s
        for s in order
        if s.node_type in ("enter_zone", "enter_zone_via_object")
        and s.id in leads_in
        and s.params.get("from_zone") not in farm
        and s.id not in old.keep
    ]
    entrance = old.sync_zone
    if entrance and not zone_nav.is_destination(nav, entrance):
        last = next((s for s in reversed(travel) if s.params.get("to_zone") == entrance), None)
        if last is not None and zone_nav.is_destination(nav, last.params.get("from_zone", "")):
            travel.remove(last)
            old.keep.add(last.id)
            old.sync_override = last.params["from_zone"]
            old.notes.append(
                f"{entrance} is not on the zone map, so the bot syncs in {old.sync_override}, where "
                f"{last.id} ({last.label!r}) leaves from; put it back right after Sync players"
            )
    old.folded |= {s.id for s in travel}


def _farm_zones(old: OldBot) -> set[str]:
    """The zones the farm happens in: the dungeon and every room past it, or the spawns' zones."""
    if old.entry is not None:
        zones = {old.entry.params.get("dungeon_zone")}
    elif old.spawns:
        zones = {s.params.get("zone") for s in old.spawns}
    elif old.spot is not None:
        zones = {old.spot.params.get("zone")}
    elif old.gauntlet is not None:
        zones = {old.gauntlet.params.get("dungeon_zone")}
    else:
        return set()
    changes = [s for s in old.config.states if s.node_type in ("enter_zone", "enter_zone_via_object")]
    # Growing stops at the entrance: the way out leads there, and it is not the farm.
    outside = {old.entry.params.get("from_zone")} if old.entry is not None else set()
    grew = True
    while grew:
        more = {s.params.get("to_zone") for s in changes if s.params.get("from_zone") in zones} - zones - outside
        grew = bool(more)
        zones |= more
    zones.discard(None)
    return zones


def _reachable(config: BotConfig, start: str, stop: str | None = None) -> set[str]:
    """Every state a run reaches from ``start`` by any edge but a failure, ``start`` included.

    A walk stops at ``stop`` (which is still counted), so "reaches the farm" is not confused
    with "reaches the farm and then everything after it".
    """
    edges: dict[str, list[str]] = {}
    for tr in config.transitions:
        if tr.when != "FAILURE":
            edges.setdefault(tr.source, []).append(tr.dest)
    seen, stack = set(), [start]
    while stack:
        sid = stack.pop()
        if sid in seen:
            continue
        seen.add(sid)
        if sid != stop or sid == start:
            stack.extend(edges.get(sid, []))
    return seen


def _exit_of(old: OldBot, order: list[StateModel]) -> StateModel | None:
    """The zone change that leaves the farm: from one of its zones to a zone outside it.

    Back to where the dungeon was entered from, when one goes there.
    """
    farm = _farm_zones(old)
    leaving = [
        s
        for s in order
        if s.node_type == "enter_zone"
        and s.id not in old.folded
        and s.params.get("from_zone") in farm
        and s.params.get("to_zone") not in farm
    ]
    entrance = old.entry.params.get("from_zone") if old.entry is not None else None
    back = [s for s in leaving if s.params.get("to_zone") == entrance]
    return (back or leaving or [None])[0]


# ---------------------------------------------------------------------------
# Building the new bot
# ---------------------------------------------------------------------------


@dataclass
class Report:
    """What a rebuild did, for the maintainer to read before writing it."""

    bot: str
    template: str
    version: int
    filled: list[str] = field(default_factory=list)
    unfilled: list[str] = field(default_factory=list)
    carried: list[str] = field(default_factory=list)
    folded: list[str] = field(default_factory=list)
    covered: list[str] = field(default_factory=list)
    grafted: list[str] = field(default_factory=list)
    content: list[str] = field(default_factory=list)
    unread_options: list[str] = field(default_factory=list)
    #: Why the rebuilt bot is not a faithful copy. ``--write`` refuses a bot with any.
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """Every old node carried or replaced, every node fill-in filled, and the result sound.

        Only then is it written. A blank fill-in on a node is a validation error in a bot made
        from a template, so the bot would stop running until someone records it in game; a
        bot left as it was keeps running meanwhile. Missing drops block nothing.
        """
        blocking = [line for line in self.unfilled if not line.startswith(DROPS_LABEL)]
        return not self.problems and not self.content and not self.unread_options and not blocking

    def lines(self) -> list[str]:
        out = [f"{self.bot}: rebuilt from {self.template} v{self.version}"]
        sections = (
            ("filled", self.filled),
            ("NOT FILLED: a setup step stays open, and a blank node fill-in blocks a run", self.unfilled),
            ("also carried", self.carried),
            ("folded into the template", self.folded),
            ("covered by the template's own node", self.covered),
            ("the bot's own content, carried", self.grafted),
            ("NOT CARRIED: the bot's own content, to put back in the app", self.content),
            ("OPTIONS NOTHING READS", self.unread_options),
            ("PROBLEMS: not written", self.problems),
            ("notes", self.notes),
        )
        for title, items in sections:
            if items:
                out.append(f"  {title}:")
                out += [f"    - {item}" for item in items]
        return out


def _field_default(node_type: str, key: str):
    spec = NODE_SPECS.get(node_type)
    return next((f.default for f in spec.params if f.key == key), None) if spec is not None else None


def _label(state: StateModel) -> str:
    return f"{state.id} {state.node_type}" + (f" ({state.label!r})" if state.label else "")


def _empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def template_sources(template: BotConfig, old: OldBot) -> dict[str, StateModel]:
    """``{template state id: old state}`` for every template node a value is read from."""
    roles = {
        "enter_dungeon": old.entry,
        "enter_zone": old.exit,
        "handle_combat": old.fight,
        "teleport_to_coordinates": old.spot,
        "enter_gauntlet": old.gauntlet,
    }
    sources: dict[str, StateModel] = {}
    for state in walk_order(template):
        source = roles.get(state.node_type) if state.node_type in MAPPED_TYPES else None
        if source is not None and source not in sources.values():
            sources[state.id] = source
    spawns = iter(old.spawns)
    for state in walk_order(template):
        if state.node_type == "collect_reagent":
            spawn = next(spawns, None)
            if spawn is not None:
                sources[state.id] = spawn
    return sources


def rebuild(
    old_config: BotConfig,
    start: StartingPoint,
    library: SubgraphLibrary,
    nav: zone_nav.NavMap | None = None,
    category: str | None = None,
) -> tuple[BotConfig, Report]:
    """The old bot rebuilt from ``start``, composed, and what was done. Writes nothing."""
    nav = zone_nav.load() if nav is None else nav
    category = category or classify(old_config)
    # The old graph may be edited on the way (an inner mark spliced out), never the caller's.
    old_config = old_config.model_copy(deep=True)
    old = read_old(old_config, category, nav)
    report = Report(bot=old_config.name, template=start.config.name, version=start.version, notes=list(old.notes))

    config = create_bot(
        start,
        BotDetails(
            name=old_config.name,
            description=old_config.description or "",
            tags=list(old_config.tags or []),
            authors=old_config.authors or "",
        ),
    )
    config.id = old_config.id
    config.version = old_config.version
    config.clients = old_config.clients
    config.drops = [d.model_copy(deep=True) for d in old_config.drops]
    _merge_options(config, old_config, report)

    by_id = {s.id: s for s in config.states}
    sources = template_sources(start.config, old)
    for tid, source in sources.items():
        target = by_id[tid]
        if source.label:
            target.label = source.label
        for key in CARRY_PARAMS.get(target.node_type, ()):
            value = (source.params or {}).get(key)
            if not _empty(value) and value != target.params.get(key, _field_default(target.node_type, key)):
                target.params[key] = value
                report.carried.append(f"{tid}.{key} = {value!r} (from {source.id})")

    _drop_spare_spawns(config, start.config, sources, report)
    present = {st.id for st in config.states}
    steps = start.config.template_meta.setup if start.config.template_meta is not None else []
    for step in steps:
        for target in step.targets:
            if target.state is None or target.state in present:
                _fill(config, target, step.text, sources, old, nav, report)

    template_spawns = sum(1 for st in start.config.states if st.node_type == "collect_reagent")
    if old.spawns and len(old.spawns) < template_spawns:
        report.notes.append(
            f"the template has {template_spawns} spawns and the old bot {len(old.spawns)}: "
            "record the rest in the app, or delete the ones it does not need"
        )
    _check_anchors(config, sources, report)
    _add_level_scaling(config, old, report)
    _place_level_scaling(config, old, report)
    recall = _template_recall(start.config)
    if recall is not None:
        # The template recalls out of the dungeon, so the old way out is the recall now.
        old.way_out = _way_out(old, sources)
    covered = _cover(old, sources, start.config)
    if recall is not None:
        covered |= {sid: recall for sid in old.way_out}
        _drop_inner_marks(old, sources, covered, report)
    hls = next((st.id for st in config.states if st.node_type == "handle_level_scaling"), None)
    if hls is not None:
        # handle_level_scaling replaces the old pair, so whatever hung off them hangs off it.
        covered |= {st.id: hls for st in old_config.states if st.node_type in LEVEL_SCALING_TYPES}
    grafted = _graft(config, old, sources, covered, report)
    config.transitions = [t for t in config.transitions if not _retired_guard(t)]
    fills = {(t.state, t.param) for step in steps for t in step.targets if t.state is not None}
    _carry_tuning(config, old, covered, fills, report)
    if grafted:
        auto_layout(config)

    composed, compose_report = compose_bot(config, library)
    missing = [tag for tag in suggested_tags(composed.states) if tag not in composed.tags]
    if missing:
        composed.tags = normalize_tags([*composed.tags, *missing])
        report.carried.append(f"tags the rebuilt graph suggests: {', '.join(missing)}")
    for rid, status in compose_report.regions.items():
        if status.kind != "linked" or status.reasons:
            report.problems.append(f"region {rid} composes as {status.kind}: {status.reasons}")

    _sort_old_nodes(old, sources, covered, grafted, report)
    report.unread_options = _unread_options(composed)
    unreachable = sorted({s.id for s in composed.states} - _reachable_states(composed))
    if unreachable:
        report.problems.append(f"no run reaches {', '.join(unreachable)}")
    return composed, report


def _drop_spare_spawns(config: BotConfig, template: BotConfig, sources: dict[str, StateModel], report: Report) -> None:
    """Delete the template's reagent spawns the old bot never had, closing the loop round them.

    Reagent Collector has three; a bot that farms one spawn keeps one. A deleted template node
    is something the update review already knows: a change to it is hidden.
    """
    spare = [s.id for s in walk_order(template) if s.node_type == "collect_reagent" and s.id not in sources]
    if not spare or not any(s.node_type == "collect_reagent" for s in sources.values()):
        return
    for sid in spare:
        after = _template_successor(config, sid)
        for tr in config.transitions:
            if tr.dest == sid:
                tr.dest = after
        config.transitions = [t for t in config.transitions if t.source != sid]
        config.states = [s for s in config.states if s.id != sid]
    report.notes.append(f"the old bot farms fewer spawns than the template: {', '.join(spare)} deleted")


def _carry_tuning(
    config: BotConfig, old: OldBot, covered: dict[str, str], fills: set[tuple[str, str]], report: Report
) -> None:
    """Give a template node the old bot's own thresholds on the node it replaces.

    Not the values the worksheet records as the template's to set (``TEMPLATE_SET``), not
    ``mark_zone`` (where the potions run is the template's now, so a zone checked against the
    old place could fail at the new one), and never a fill-in, which a setup step filled.
    """
    by_id = {s.id: s for s in config.states}
    old_by_id = {s.id: s for s in old.config.states}
    for old_id, tid in covered.items():
        target, source = by_id.get(tid), old_by_id[old_id]
        if target is None or target.node_type != source.node_type or old_id in old.way_out:
            # The recall the old way out became names no spot: it checks each player's own mark.
            continue
        for key, value in (source.params or {}).items():
            if key in TEMPLATE_SET.get(target.node_type, ()) or (tid, key) in fills or _empty(value):
                continue
            if value != target.params.get(key, _field_default(target.node_type, key)):
                target.params[key] = value
                report.carried.append(f"{tid}.{key} = {value!r} (from {old_id})")


def _check_anchors(config: BotConfig, sources: dict[str, StateModel], report: Report) -> None:
    """A template node the farm hangs on that nothing in the old bot fills means a different shape."""
    for state in config.states:
        if state.node_type in (*MAPPED_TYPES, "collect_reagent") and state.id not in sources:
            role = "exit" if state.node_type == "enter_zone" else state.node_type
            report.problems.append(f"the old bot has no {role} for the template's {state.id}: a different shape")


def _guarded(tr: TransitionModel) -> bool:
    return bool(tr.flags or tr.flag_groups)


def _template_successor(config: BotConfig, sid: str | None) -> str | None:
    """Where a node's plain ``SUCCESS`` goes."""
    return next(
        (t.dest for t in config.transitions if t.source == sid and t.when == "SUCCESS" and not _guarded(t)), None
    )


def _inside(old: OldBot) -> set[str]:
    """The old nodes inside the farm: past its dungeon or gauntlet entry, short of its way out, by any edge."""
    if old.entry is not None:
        start, stops = old.entry, {old.exit.id} if old.exit is not None else set()
    elif old.gauntlet is not None:
        start = old.gauntlet
        stops = {s.id for s in old.config.states if s.node_type == "teleport_to_friend"}
    else:
        return set()
    # The old level scaling pair is folded too, but a dungeon first scales just past its entry,
    # so the farm goes on beyond it.
    scaling = {s.id for s in old.config.states if s.node_type in SCALING_FOLD}
    stops |= (old.folded - scaling) | old.way_out | {start.id}
    # Failure branches too: a potions a failed wisps falls back to is inside the dungeon all the
    # same. One into navigation stops there, as every edge does.
    edges: dict[str, list[str]] = {}
    for tr in old.config.transitions:
        edges.setdefault(tr.source, []).append(tr.dest)
    seen: set[str] = set()
    stack = list(edges.get(start.id, []))
    while stack:
        sid = stack.pop()
        if sid in seen or sid in stops:
            continue
        seen.add(sid)
        stack.extend(edges.get(sid, []))
    return seen


def _cover(old: OldBot, sources: dict[str, StateModel], template: BotConfig) -> dict[str, str]:
    """``{old id: template id}`` for the old nodes the template's own potions, quicksell, wisps and so on replace.

    Only where the template has them: outside the farm, or the wisps straight after the fight.
    Any other -- a wisps between two rooms, a second potions -- is the bot's own content.
    """
    used = {s.id for s in sources.values()}
    inside = _inside(old)
    kinds = {s.node_type for s in template.states} - set(MAPPED_TYPES)
    kinds -= {"collect_reagent", "relog", "sync_in_zone", *LEVEL_SCALING_TYPES}
    by_kind: dict[str, list[str]] = {}
    for state in walk_order(template):
        if state.node_type in kinds:
            by_kind.setdefault(state.node_type, []).append(state.id)
    template_fight = next((tid for tid, src in sources.items() if src is old.fight), None) if old.fight else None
    after_fight = _template_successor(template, template_fight)
    old_after_fight = _template_successor(old.config, old.fight.id) if old.fight is not None else None
    covered: dict[str, str] = {}
    taken: set[str] = set()
    for state in walk_order(old.config):
        if state.id in used or state.id in old.folded or state.id in old.keep or state.node_type not in kinds:
            continue
        if state.id == old_after_fight and after_fight in by_kind.get(state.node_type, []) and after_fight not in taken:
            covered[state.id] = after_fight
            taken.add(after_fight)
            continue
        if state.id in inside:
            continue
        free = [tid for tid in by_kind.get(state.node_type, []) if tid not in taken and tid != after_fight]
        if free:
            covered[state.id] = free[0]
            taken.add(free[0])
        elif _only_from(old.config, state.id, old.folded):
            # A second wisps or potions on the trip, after the hub teleport: the template runs
            # its own before the entry, so this one goes with the trip.
            old.folded.add(state.id)
    return covered


def _only_from(config: BotConfig, sid: str, sources: set[str]) -> bool:
    """Whether every edge a run takes into ``sid`` when things work comes from ``sources``."""
    ins = [t.source for t in config.transitions if t.dest == sid and t.when not in FALLTHROUGH_RETURNS]
    ins = [src for src in ins if src != sid]
    return bool(ins) and all(src in sources for src in ins)


def _graft(
    config: BotConfig, old: OldBot, sources: dict[str, StateModel], covered: dict[str, str], report: Report
) -> list[str]:
    """Carry the old bot's own content into the rebuilt one, each piece spliced in where it hung.

    The content is every old node nothing replaces. It falls into pieces joined by their own
    edges, and each piece must hang off exactly one node the template has (the dungeon entry,
    the wisps after the fight, a reagent spawn): the piece goes in straight after that node,
    on the edges the old bot left it by, and comes back out where the old bot's edges lead.
    A failure into navigation the template replaces is dropped, since an unrouted failure
    relogs. Anything else -- a piece reached only from navigation, or one that leaves through
    it -- is reported as a problem, and the bot is not written.
    """
    mapping = {src.id: tid for tid, src in sources.items()} | covered
    old_by_id = {s.id: s for s in old.config.states}
    content = [s.id for s in walk_order(old.config) if s.id not in mapping and s.id not in old.folded]
    if not content:
        return []
    content_set = set(content)
    clash = sorted(content_set & {s.id for s in config.states})
    if clash:
        report.problems.append(f"old ids {clash} clash with the template's")
        return []
    sync = next((s.id for s in config.states if s.node_type == "sync_in_zone" and _is_member(config, s.id)), None)

    new_edges: list[TransitionModel] = []
    replaced: set[tuple[str, str | None]] = set()
    problems: list[str] = []
    for piece in _pieces(old.config, content):
        names = ", ".join(sid for sid in content if sid in piece)
        entering = [
            _as_entry(t, old_by_id, mapping) for t in old.config.transitions if t.dest in piece and t.source in mapping
        ]
        entering = [t for t in entering if t is not None]
        # A failure branch into the piece is carried, but where the piece goes in is decided by
        # how the run reaches it when things work.
        anchors = {t.source for t in entering if t.when not in FALLTHROUGH_RETURNS}
        if not anchors and entering:
            new_edges += _unique(entering)
            report.notes.append(f"content {names} hangs off a failure branch")
            _carry_exits(piece, old, old_by_id, mapping, new_edges, problems, rejoin=None)
            continue
        if not anchors and piece & old.keep and sync is not None:
            # The step into an entrance Sync players cannot reach goes straight after Sync players.
            first = next(sid for sid in content if sid in piece)
            entering = [TransitionModel(trigger="advance", source=sync, dest=first, when="SUCCESS")]
            anchors = {sync}
            for tr in old.config.transitions:
                if tr.source in piece and tr.dest in mapping:
                    tr_dest = _template_successor(config, sync)
                    new_edges.append(tr.model_copy(update={"dest": tr_dest}, deep=True))
            replaced.add((sync, "SUCCESS"))
            new_edges.append(entering[0])
            report.notes.append(f"content {names} spliced in straight after Sync players")
            continue
        if len(anchors) != 1:
            why = (
                "only from navigation the template replaces"
                if not anchors
                else f"from several places {sorted(anchors)}"
            )
            problems.append(f"content {names} is reached {why}")
            continue
        anchor = anchors.pop()
        late = _late_on_the_way_in(config, old, piece, anchor, mapping, sync)
        if late:
            problems.append(f"content {names} comes before {late} on the way in, but the template reaches {late} first")
            continue
        for tr in _unique(entering):
            new_edges.append(tr)
            # One edge per outcome: the old bot's, failures included, replaces the template's.
            if not _guarded(tr):
                replaced.add((tr.source, tr.when))
        rejoin = _downstream(config, anchor)
        _carry_exits(
            piece, old, old_by_id, mapping, new_edges, problems, rejoin=rejoin, inner=_inner_loop(config, rejoin, sync)
        )
        if rejoin and not any(t.source in piece and t.dest not in piece for t in old.config.transitions):
            # A chain that just stops -- a bot stripped to the nodes it adds -- goes on to the
            # template's next node from where it ends, as content in a slot does.
            for sid in sorted(piece - {t.source for t in old.config.transitions}):
                when = _happy_path(old_by_id[sid].node_type)
                new_edges.append(TransitionModel(trigger="advance", source=sid, dest=rejoin[0], when=when))
                report.notes.append(f"{sid} ended the old chain: it goes on to {rejoin[0]}")
        report.notes.append(f"content {names} spliced in after {anchor}")
    if problems:
        report.problems += problems
        return []

    # An old edge between two nodes the template has that carries a flag guard is an author's
    # skip. It is kept only where it still skips forward: one into a node the template runs
    # earlier would loop, as "skip potions in Challenge Mode" does now that potions come first.
    for tr in old.config.transitions:
        if not (tr.source in mapping and tr.dest in mapping and _guarded(tr)):
            continue
        if _retired_guard(tr):
            continue
        edge = _as_entry(tr, old_by_id, mapping)
        if edge is None:
            continue
        edge = edge.model_copy(update={"dest": mapping[tr.dest]})
        if edge.dest in _ahead(config, edge.source, sync):
            new_edges.append(edge)
        else:
            problems.append(
                f"guarded edge {tr.source} -> {tr.dest} would run backwards as {edge.source} -> {edge.dest}: "
                "the template's order cannot express it"
            )
    if problems:
        report.problems += problems
        return []

    config.transitions = [t for t in config.transitions if (t.source, t.when) not in replaced or _guarded(t)]
    config.states += [old_by_id[sid].model_copy(deep=True) for sid in content]
    inner = [
        t.model_copy(deep=True) for t in old.config.transitions if t.source in content_set and t.dest in content_set
    ]
    config.transitions += inner + new_edges
    for region in old.config.regions:
        if region.source is None and set(region.members) <= content_set:
            config.regions.append(region.model_copy(deep=True))
    return content


def _happy_path(node_type: str) -> str:
    """The outcome a node of ``node_type`` returns when it works: ``SUCCESS``, or ``TRUE`` for a check."""
    spec = NODE_SPECS.get(node_type)
    names = [r.name for r in spec.returns] if spec is not None else []
    return "TRUE" if "TRUE" in names and "SUCCESS" not in names else "SUCCESS"


def _as_entry(tr: TransitionModel, old_by_id: dict[str, StateModel], mapping: dict[str, str]) -> TransitionModel | None:
    """An old edge into content, leaving from the template node that replaces its source.

    The old level scaling pair maps onto handle_level_scaling, an action: a check's ``TRUE``
    becomes its ``SUCCESS``, and a check's ``FALSE`` -- the way to the old apply -- is dropped.
    """
    source = old_by_id[tr.source]
    when = tr.when
    if source.node_type == "check_level_scaling":
        if when == "FALSE":
            return None
        when = "SUCCESS"
    return tr.model_copy(update={"source": mapping[tr.source], "when": when}, deep=True)


def _late_on_the_way_in(
    config: BotConfig, old: OldBot, piece: set[str], anchor: str, mapping: dict[str, str], sync: str | None
) -> str | None:
    """A template node the piece led to on the old bot's way in that the template's first run reaches too soon.

    A piece the old bot walked through on its way to the farm -- the entry to a dungeon its
    reagents grow in -- has to run before what it led to on the template's first run as well.
    Spliced in after a node the run only reaches later, the first run skips it: players would
    collect in the museum before entering Counterweight West. None when the order holds.
    """
    travel = old.folded - {s.id for s in old.config.states if s.node_type in ("check_zone", *SCALING_FOLD)}
    on_the_way_in = any(
        t.dest in piece and t.source in travel and t.when not in FALLTHROUGH_RETURNS for t in old.config.transitions
    )
    if not on_the_way_in or sync is None:
        return None
    first_run = _downstream(config, sync)
    if anchor not in first_run:
        return None
    led_to = {
        mapping[t.dest]
        for t in old.config.transitions
        if t.source in piece and t.dest in mapping and t.when not in FALLTHROUGH_RETURNS
    }
    reached_first = [tid for tid in first_run[: first_run.index(anchor) + 1] if tid in led_to]
    return reached_first[0] if reached_first else None


def _ahead(config: BotConfig, sid: str, sync: str | None) -> list[str]:
    """The template nodes a run reaches from ``sid`` before its loop starts over."""
    chain = _downstream(config, sid)
    restart = _template_successor(config, sync) if sync is not None else None
    return chain[: chain.index(restart)] if restart in chain else chain


def _retired_guard(tr: TransitionModel) -> bool:
    names = {f.option for f in tr.flags} | {f.option for g in tr.flag_groups for f in g.flags}
    return bool(names & RETIRED_OPTIONS.keys())


def _downstream(config: BotConfig, anchor: str) -> list[str]:
    """The template's chain of plain ``SUCCESS`` edges after ``anchor``, up to where it loops."""
    chain: list[str] = []
    sid = _template_successor(config, anchor)
    while sid is not None and sid not in chain and sid != anchor:
        chain.append(sid)
        sid = _template_successor(config, sid)
    return chain


def _inner_loop(config: BotConfig, rejoin: list[str], sync: str | None) -> frozenset[str]:
    """The template nodes inside the dungeon on the next pass, for content already inside it.

    ``rejoin`` runs on past where the loop starts over, through the next pass's entry. A piece
    whose chain reaches the loop's start before an entry is inside the dungeon, and a way out
    to a node past that entry is its own loop that never leaves -- jade_oni fights again from
    its level scaling -- not a skip down the chain. A way back to the entry or before it still
    goes out first.
    """
    restart = _template_successor(config, sync) if sync is not None else None
    if restart not in rejoin or rejoin.index(restart) == 0:
        return frozenset()  # off Sync players or the way out: outside the dungeon
    types = {s.id: s.node_type for s in config.states}
    entries = ("enter_dungeon", "enter_gauntlet")
    if any(types.get(sid) in entries for sid in rejoin[: rejoin.index(restart)]):
        return frozenset()  # this pass's entry is still ahead
    after = rejoin[rejoin.index(restart) :]
    entry = next((i for i, sid in enumerate(after) if types.get(sid) in entries), None)
    return frozenset() if entry is None else frozenset(after[entry + 1 :])


def _unique(edges: list[TransitionModel]) -> list[TransitionModel]:
    seen, out = set(), []
    for tr in edges:
        key = (tr.source, tr.dest, tr.when, repr(tr.flags), repr(tr.flag_groups))
        if key not in seen:
            seen.add(key)
            out.append(tr)
    return out


def _carry_exits(
    piece: set[str],
    old: OldBot,
    old_by_id: dict[str, StateModel],
    mapping: dict[str, str],
    new_edges: list[TransitionModel],
    problems: list[str],
    rejoin: list[str] | None,
    inner: frozenset[str] = frozenset(),
) -> None:
    """The piece's edges back out: to the template node that replaces where they went.

    ``rejoin`` is the template's own chain after the node the piece hangs off. A way out that
    lands further down that chain comes back in at its start instead, so the piece never skips
    a template node -- the wisps after a fight still run when a chest is opened before the exit.
    A way out to one of ``inner`` is the exception: see ``_inner_loop``.
    """
    for tr in old.config.transitions:
        if tr.source not in piece or tr.dest in piece:
            continue
        if tr.dest in old.way_out and tr.when in FALLTHROUGH_RETURNS and not _also_succeeds_into(old, tr.source):
            continue  # a bail-out to the old exit or hub: unrouted, it relogs, as every failure now does
        if tr.dest in mapping:
            dest = mapping[tr.dest]
            if rejoin and tr.when not in FALLTHROUGH_RETURNS and dest in rejoin[1:] and dest not in inner:
                dest = rejoin[0]
            new_edges.append(tr.model_copy(update={"dest": dest}, deep=True))
        elif tr.dest in old.folded and tr.when in FALLTHROUGH_RETURNS:
            continue  # an unrouted failure relogs, which is what every failure edge now does
        else:
            dest = old_by_id.get(tr.dest)
            kind = dest.node_type if dest is not None else "?"
            problems.append(
                f"content {tr.source} leaves on {tr.when or 'any result'} through {kind} {tr.dest}, "
                "which the template replaces"
            )


def _also_succeeds_into(old: OldBot, sid: str) -> bool:
    """Whether ``sid`` goes on to the way out when it works, too: then its failure going there is "out either way"."""
    return any(
        t.source == sid and t.dest in old.way_out and t.when not in FALLTHROUGH_RETURNS for t in old.config.transitions
    )


def _is_member(config: BotConfig, sid: str) -> bool:
    return any(r.source is not None and sid in r.members for r in config.regions)


def _pieces(config: BotConfig, content: list[str]) -> list[set[str]]:
    """The content split into pieces joined by their own edges, either way round."""
    links: dict[str, set[str]] = {sid: set() for sid in content}
    for tr in config.transitions:
        if tr.source in links and tr.dest in links:
            links[tr.source].add(tr.dest)
            links[tr.dest].add(tr.source)
    pieces: list[set[str]] = []
    seen: set[str] = set()
    for sid in content:
        if sid in seen:
            continue
        piece: set[str] = set()
        stack = [sid]
        while stack:
            cur = stack.pop()
            if cur not in piece:
                piece.add(cur)
                stack.extend(links[cur] - piece)
        seen |= piece
        pieces.append(piece)
    return pieces


def _merge_options(config: BotConfig, old_config: BotConfig, report: Report) -> None:
    """The template's declarations, then the old bot's own; the bot's win on a shared name.

    A retired option is not carried (see :data:`RETIRED_OPTIONS`).
    """
    mine = {o.name for o in old_config.options}
    kept: list[BotOptionModel] = [o for o in config.options if o.name not in mine]
    for option in old_config.options:
        if option.name in RETIRED_OPTIONS:
            report.notes.append(f"option {option.name!r} not carried: {RETIRED_OPTIONS[option.name]}")
        else:
            kept.append(option.model_copy(deep=True))
    config.options = kept


def _fill(config, target, step_text: str, sources, old: OldBot, nav, report: Report) -> None:
    where = setup_steps.describe_target(config, target)
    if target.field is not None:
        if config.drops:
            report.filled.append(f"{where}: {len(config.drops)} drops")
        else:
            report.unfilled.append(f"{where}: the old bot has no drops ({step_text})")
        return
    if target.region is not None:
        region = next((r for r in config.regions if r.id == target.region and r.source is not None), None)
        if region is None or target.input != SYNC_ZONE_INPUT:
            report.unfilled.append(f"{where}: not a fill-in this tool knows ({step_text})")
            return
        zone = old.sync_zone
        if not zone:
            report.unfilled.append(f"{where}: nothing in the old bot says where to sync")
        elif not zone_nav.is_destination(nav, zone):
            report.unfilled.append(f"{where}: {zone} is not a destination on the zone map; sync nearby and add a step")
        else:
            region.source.inputs[target.input] = zone
            report.filled.append(f"{where} = {zone!r}")
        return
    state = next((s for s in config.states if s.id == target.state), None)
    source = sources.get(target.state or "")
    if state is None:
        return
    key = target.param or ""
    if source is None and (state.node_type, key) in HUB_FILLS:
        world = nav.world_of(old.sync_zone) if old.sync_zone else None
        if world is None:
            report.unfilled.append(f"{where}: no world on the zone map holds the sync zone ({step_text})")
        else:
            state.params[key] = world.hub
            report.filled.append(f"{where} = {world.hub!r} (the hub of {world.key})")
        return
    if source is None and (state.node_type, key) in ENTRY_FILLS and old.entry is not None:
        source, key = old.entry, ENTRY_FILLS[(state.node_type, key)]
    value = (source.params or {}).get(key) if source is not None else None
    if source is None:
        report.unfilled.append(f"{where}: the old bot has no node for it ({step_text})")
    elif _empty(value):
        report.unfilled.append(f"{where}: empty on {source.id} in the old bot ({step_text})")
    else:
        state.params[target.param] = value
        report.filled.append(f"{where} = {value!r} (from {source.id})")


def _template_recall(template: BotConfig) -> str | None:
    """The template's own recall out of its dungeon, if it leaves that way."""
    return next((s.id for s in template.states if s.node_type == "teleport_to_marked_location"), None)


def _way_out(old: OldBot, sources: dict[str, StateModel]) -> set[str]:
    """The old bot's way out of its farm: every node a recall to the entrance replaces.

    It starts at whatever takes the players out once nothing is left to farm before the loop
    comes round to the entry again -- the walk-out exit, a hub teleport, a teleporter to a zone
    outside the farm, an old recall, a Sync in zone back to the entrance -- and takes in the plain navigation on either side of it:
    the walk back through the rooms to the door before, and the trip back to the entrance after.
    A hub teleport with more to farm after it, a hop partway through, is not a way out.
    """
    if old.entry is None:
        return set()
    by_id = {s.id: s for s in old.config.states}
    farm = _farm_zones(old)
    used = {s.id for s in sources.values()}
    forward: dict[str, list[str]] = {}
    every: dict[str, list[str]] = {}
    for tr in old.config.transitions:
        every.setdefault(tr.source, []).append(tr.dest)
        if tr.when not in FALLTHROUGH_RETURNS:
            forward.setdefault(tr.source, []).append(tr.dest)

    def walk(start: str) -> set[str]:
        # By any edge: some dungeons run on failures (a plaque puzzle tries the next on one).
        seen, stack = set(), [start]
        while stack:
            sid = stack.pop()
            if sid not in seen:
                seen.add(sid)
                if sid != old.entry.id or sid == start:
                    stack.extend(every.get(sid, []))
        return seen - {start, old.entry.id}

    reached = walk(old.entry.id)

    def leaves(state: StateModel) -> bool:
        params = state.params or {}
        if state.node_type in ("teleport_to_world_hub", "teleport_to_marked_location", "sync_in_zone"):
            return True
        if state.node_type in ("enter_zone", "enter_zone_via_object"):
            return params.get("from_zone") in farm and params.get("to_zone") not in farm
        return False

    def done_farming(sid: str) -> bool:
        return not any(by_id[a].node_type in FARMING_TYPES for a in walk(sid) if a in by_id)

    out = {sid for sid in reached if sid not in used and leaves(by_id[sid]) and done_farming(sid)}
    if not out:
        return set()
    # Forward: the trip back to the entrance, up to whatever the template replaces or the entry.
    stack = list(out)
    while stack:
        for dest in forward.get(stack.pop(), []):
            state = by_id.get(dest)
            if state is not None and dest not in out and dest not in used and state.node_type in WAY_OUT_TYPES:
                out.add(dest)
                stack.append(dest)
    # Backward: navigation whose every way on is the way out.
    grew = True
    while grew:
        grew = False
        for sid in reached - out - used:
            nexts = forward.get(sid, [])
            if by_id[sid].node_type in WAY_OUT_TYPES and nexts and all(n in out for n in nexts):
                out.add(sid)
                grew = True
    return out


def _drop_inner_marks(old: OldBot, sources: dict[str, StateModel], covered: dict[str, str], report: Report) -> None:
    """Splice the bot's own marks out of its content when the template recalls out.

    The template's recall goes back to each player's latest mark, so a mark made inside the
    dungeon would send the exit back into it. A mark with nothing of the bot's own recalling
    to it has no other use, and goes; a recall of the bot's own inside the dungeon is a
    problem, since the two cannot both have the last mark.
    """
    used = {s.id for s in sources.values()} | covered.keys()
    for state in list(old.config.states):
        if state.id in used or state.id in old.folded:
            continue
        if state.node_type == "teleport_to_marked_location":
            report.problems.append(
                f"{_label(state)} recalls to a mark of the bot's own, and the template's exit recalls "
                "to the latest mark: the two cannot share it"
            )
        elif state.node_type == "mark_location" and _splice_out(old.config, state.id):
            report.notes.append(
                f"{_label(state)} dropped: the template's exit recalls to the latest mark, so a mark "
                "inside the dungeon would send it back in"
            )


def _splice_out(config: BotConfig, sid: str) -> bool:
    """Take ``sid`` out of the graph, its way in going on to its one ``SUCCESS``. False if it has no single one."""
    nexts = [t.dest for t in config.transitions if t.source == sid and t.when == "SUCCESS" and not _guarded(t)]
    if len(nexts) != 1 or nexts[0] == sid:
        return False
    for tr in config.transitions:
        if tr.dest == sid:
            tr.dest = nexts[0]
    config.transitions = [t for t in config.transitions if t.source != sid and t.dest != sid]
    config.states = [s for s in config.states if s.id != sid]
    return True


def _add_level_scaling(config: BotConfig, old: OldBot, report: Report) -> None:
    """Give a level scaling bot a handle_level_scaling of its own when its template has none.

    Only for the old pair: a bot that already has a handle_level_scaling keeps it, carried
    where it stands. A new one goes straight after the dungeon entry: a dungeon first scales inside the dungeon it
    farms, where its Scales are, so its players scale in place.
    """
    if any(s.node_type == "handle_level_scaling" for s in config.states):
        return
    if any(s.node_type == "handle_level_scaling" for s in old.config.states):
        return  # the bot's own is already where it belongs: it is carried as content
    if not any(s.node_type in SCALING_FOLD for s in old.config.states):
        return
    farm = next((s for s in config.states if s.node_type == "enter_dungeon"), None)
    after = _template_successor(config, farm.id) if farm is not None else None
    if after is None:
        report.problems.append("the old bot level scales, and the template has no entry to scale after")
        return
    taken = {s.id for s in config.states} | {s.id for s in old.config.states}
    number = next(n for n in range(1, len(taken) + 2) if f"state_{n}" not in taken)
    node = StateModel(
        id=f"state_{number}",
        node_type="handle_level_scaling",
        label="Handle level scaling",
        position=dict(farm.position),
        params={"scales": ""},
    )
    config.states.append(node)
    for tr in config.transitions:
        if tr.source == farm.id and tr.when == "SUCCESS" and not _guarded(tr):
            tr.dest = node.id
    config.transitions.append(TransitionModel(trigger="advance", source=node.id, dest=after, when="SUCCESS"))
    report.carried.append(f"{node.id} handle_level_scaling, the bot's own, after {farm.id}: its template has none")


def _place_level_scaling(config: BotConfig, old: OldBot, report: Report) -> None:
    """Name the Scales where the default is not it, and scale after entry where the Scales are inside."""
    node = next((s for s in config.states if s.node_type == "handle_level_scaling"), None)
    if node is None:
        return
    if old.scales is None:
        report.notes.append("no level scaling in the old bot: the node scales on the world default")
    else:
        default = scales.default_for(config)
        if default is None or default.key != old.scales.key:
            node.params["scales"] = old.scales.key
            report.carried.append(f"{node.id}.scales = {old.scales.key!r} (not the world default)")
    entry = scales.resolve(node.params.get("scales"), config)
    farm = next((s for s in config.states if s.node_type == "enter_dungeon"), None)
    if entry is None or farm is None or farm.params.get("dungeon_zone") not in inside_zones(entry):
        return
    if _template_successor(config, farm.id) == node.id:
        return
    if _move_after(config, node.id, farm.id):
        report.notes.append(
            f"{node.id} handle_level_scaling moved after {farm.id}: the farm dungeon is the Scales' own, so players "
            "scale in place (docs/LEVEL_SCALING.md)"
        )


def _move_after(config: BotConfig, node_id: str, anchor_id: str) -> bool:
    """Take ``node_id`` out of its place on the forward path and put it straight after ``anchor_id``.

    Its predecessors go on to what followed it, and it goes on to what followed the anchor.
    False, changing nothing, when either has no single ``SUCCESS`` edge to splice.
    """
    out_of_node = [t for t in config.transitions if t.source == node_id and t.when == "SUCCESS"]
    out_of_anchor = [t for t in config.transitions if t.source == anchor_id and t.when == "SUCCESS"]
    if len(out_of_node) != 1 or len(out_of_anchor) != 1:
        return False
    after_node, after_anchor = out_of_node[0].dest, out_of_anchor[0].dest
    for tr in config.transitions:
        if tr.dest == node_id and tr is not out_of_node[0]:
            tr.dest = after_node
    out_of_anchor[0].dest = node_id
    out_of_node[0].dest = after_anchor
    return True


def _sort_old_nodes(
    old: OldBot, sources: dict[str, StateModel], covered: dict[str, str], grafted: list[str], report: Report
) -> None:
    used = {src.id for src in sources.values()}
    carried = set(grafted)
    for state in walk_order(old.config):
        if state.id in used:
            continue
        if state.id in carried:
            report.grafted.append(_label(state))
        elif state.id in covered:
            report.covered.append(f"{_label(state)} -> {covered[state.id]}")
        elif state.id in old.folded:
            report.folded.append(_label(state))
        else:
            report.content.append(_label(state))


def _unread_options(config: BotConfig) -> list[str]:
    """The declared options no node requires and no transition guards on."""
    required = set(required_option_specs(config))
    guarded = {fc.option for tr in config.transitions for fc in tr.flags}
    guarded |= {fc.option for tr in config.transitions for group in tr.flag_groups for fc in group.flags}
    return [o.name for o in config.options if o.name not in required and o.name not in guarded]


# ---------------------------------------------------------------------------
# The store and the library
# ---------------------------------------------------------------------------


def find_start(name: str, root: Path = PROJECT_ROOT) -> StartingPoint:
    """The latest released version of the template called ``name``."""
    for start in starting_points(root, root):
        if start.config.name == name:
            return start
    raise RebuildError(f"no released template is called {name!r}")


def find_bot(name: str, bots_dir: Path = BOTS_DIR) -> Path:
    """A bot by path, or by file stem anywhere under ``bots/``."""
    path = Path(name)
    if path.suffix == ".json" and path.is_file():
        return path
    found = sorted(bots_dir.rglob(f"{path.stem}.json"))
    if len(found) != 1:
        raise RebuildError(f"{len(found)} bots are called {path.stem!r}" if found else f"no bot is called {name!r}")
    return found[0]


def all_bots(bots_dir: Path = BOTS_DIR) -> Iterable[Path]:
    return (p for p in sorted(bots_dir.rglob("*.json")) if not p.name.startswith("test_"))


def status_lines(bots_dir: Path = BOTS_DIR, root: Path = PROJECT_ROOT) -> list[str]:
    """One line per bot: its category, the template it records, and its setup steps left."""
    library = SubgraphLibrary(root, root)
    names = {s.template_id: s.config.name for s in starting_points(root, root)}
    lines, done = [], 0
    bots = list(all_bots(bots_dir))
    for path in bots:
        config = BotConfig.load(path)
        if config.template is None:
            lines.append(f"{path.stem:44} {category_of(path, config):11} not rebuilt")
            continue
        done += 1
        steps = setup_steps.steps_for(config, root, root)
        left = setup_steps.steps_left(config, steps, library)
        name = names.get(config.template.id, config.template.id)
        lines.append(
            f"{path.stem:44} {category_of(path, config):11} {name} v{config.template.version}, {left} steps left"
        )
    lines.append(f"{done} of {len(bots)} bots rebuilt")
    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _rebuild_one(name: str, template: str | None, write: bool) -> bool:
    """Rebuild one bot, print its report, and write it if asked and it came out clean. True if written."""
    path = find_bot(name)
    old = BotConfig.load(path)
    if old.template is not None:
        raise RebuildError(f"{path.stem} already records {old.template.id} v{old.template.version}; nothing to rebuild")
    category = category_of(path, old)
    template_name = template or TEMPLATE_NAMES.get(category)
    if template_name is None:
        raise RebuildError(f"{path.stem} is no template's category ({category}); pass --template")
    if template is not None:
        category = next((c for c, n in TEMPLATE_NAMES.items() if n == template), category)
    config, report = rebuild(
        old, find_start(template_name), SubgraphLibrary(PROJECT_ROOT, PROJECT_ROOT), category=category
    )
    print("\n".join(report.lines()))
    if not write:
        print("  (dry run: nothing written; add --write)")
        return False
    if not report.clean:
        print("  NOT WRITTEN: it would lose or break something, as listed above")
        return False
    config.save(path)
    print(f"  wrote {path.as_posix()}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    rebuild_cmd = commands.add_parser("rebuild", help="rebuild bots from their templates")
    rebuild_cmd.add_argument("bots", nargs="*", help="file stems or paths under bots/")
    rebuild_cmd.add_argument("--all", action="store_true", help="every bot that does not record a template yet")
    rebuild_cmd.add_argument("--template", help="the template's name, when its nodes say a different one")
    rebuild_cmd.add_argument(
        "--write", action="store_true", help="write each rebuilt bot over its old file, if it came out clean"
    )
    commands.add_parser("status", help="every bot: its category, template and setup steps left")
    args = parser.parse_args(argv)
    # A step's label carries "·" and "›", which a Windows console's code page cannot print.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.command == "status":
        print("\n".join(status_lines()))
        return 0
    names = list(args.bots)
    if args.all:
        names += [str(p) for p in all_bots() if BotConfig.load(p).template is None]
    if not names:
        parser.error("name a bot, or pass --all")
    failed = written = 0
    for name in names:
        try:
            written += _rebuild_one(name, args.template, args.write)
        except RebuildError as error:
            print(f"{name}: {error}", file=sys.stderr)
            failed += 1
    if args.write:
        print(f"wrote {written} of {len(names)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
