"""Rebuild a shipped bot from the template it belongs to, keeping who it is.

The library migration (#59, ``docs/LIBRARY_MIGRATION.md``) moves every bot in ``bots/`` onto
one of the released templates. New Bot cannot do it: it mints a fresh id and fills nothing
in. This tool starts from the template's latest released version through
``new_bot.create_bot``, exactly as New Bot does, and then:

- keeps the old bot's id, name, description, tags, authors, drops, clients and version
  string, and its own option declarations (the template's are kept too; the bot's win);
- fills every setup step's fill-in it can find in the old file: the dungeon entry and exit,
  the fight's enemy, the Sync players zone, a mob's wait spot, a gauntlet's entry and a
  reagent's spawns. The node each value came from also lends its label;
- in a Spellement bot, names its Scales when the world default is not the one it used, and
  moves ``handle_level_scaling`` after the dungeon entry when the farm dungeon is the
  Scales' own, as ``docs/LEVEL_SCALING.md`` says;
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

from bartlebot import scales, setup_steps, zone_nav  # noqa: E402
from bartlebot.bot.schema import BotConfig, BotOptionModel, StateModel  # noqa: E402
from bartlebot.bot_options import required_option_specs  # noqa: E402
from bartlebot.new_bot import BotDetails, StartingPoint, create_bot, starting_points  # noqa: E402
from bartlebot.nodes.registry import NODE_SPECS  # noqa: E402
from bartlebot.subgraph_store import SubgraphLibrary  # noqa: E402
from bartlebot.subgraphs import compose_bot  # noqa: E402

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
    "enter_dungeon": ("dungeon_name",),
    "enter_gauntlet": ("gauntlet_name",),
    "handle_combat": ("wiki_page", "use_default_strategy", "max_duration"),
}

#: The template node types a value is looked for in the old bot, by role.
MAPPED_TYPES = ("enter_dungeon", "enter_zone", "handle_combat", "teleport_to_coordinates", "enter_gauntlet")

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
        old.exit = _exit_of(order, old.entry, old.folded)

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
    grew = True
    while grew:
        more = {s.params.get("to_zone") for s in changes if s.params.get("from_zone") in zones} - zones
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


def _exit_of(order: list[StateModel], entry: StateModel, folded: set[str]) -> StateModel | None:
    """The zone change that leaves the farm dungeon: back to where it was entered from, if one does.

    Only one after the entry in the run: the walk *to* the entrance ends where the exit does.
    """
    after = order[order.index(entry) + 1 :]
    candidates = [s for s in after if s.node_type == "enter_zone" and s.id not in folded]
    back = [s for s in candidates if s.params.get("to_zone") == entry.params.get("from_zone")]
    if back:
        return back[-1]
    return next((s for s in candidates if s.params.get("from_zone") == entry.params.get("dungeon_zone")), None)


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
    content: list[str] = field(default_factory=list)
    unread_options: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"{self.bot}: rebuilt from {self.template} v{self.version}"]
        sections = (
            ("filled", self.filled),
            ("NOT FILLED", self.unfilled),
            ("also carried", self.carried),
            ("folded into the template", self.folded),
            ("covered by the template's own node", self.covered),
            ("NOT CARRIED: the bot's own content, to put back in the app", self.content),
            ("OPTIONS NOTHING READS until their content is back", self.unread_options),
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
    _merge_options(config, old_config)

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

    steps = start.config.template_meta.setup if start.config.template_meta is not None else []
    for step in steps:
        for target in step.targets:
            _fill(config, target, step.text, sources, old, nav, report)

    template_spawns = sum(1 for st in start.config.states if st.node_type == "collect_reagent")
    if old.spawns and len(old.spawns) < template_spawns:
        report.notes.append(
            f"the template has {template_spawns} spawns and the old bot {len(old.spawns)}: "
            "record the rest in the app, or delete the ones it does not need"
        )
    _place_level_scaling(config, old, report)

    composed, compose_report = compose_bot(config, library)
    for rid, status in compose_report.regions.items():
        if status.kind != "linked" or status.reasons:
            report.notes.append(f"region {rid} composes as {status.kind}: {status.reasons}")

    _sort_old_nodes(old, sources, start.config, report)
    report.unread_options = _unread_options(composed)
    return composed, report


def _merge_options(config: BotConfig, old_config: BotConfig) -> None:
    """The template's declarations, then the old bot's own; the bot's win on a shared name."""
    mine = {o.name for o in old_config.options}
    kept: list[BotOptionModel] = [o for o in config.options if o.name not in mine]
    config.options = kept + [o.model_copy(deep=True) for o in old_config.options]


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
    value = (source.params or {}).get(target.param or "") if source is not None else None
    if source is None:
        report.unfilled.append(f"{where}: the old bot has no node for it ({step_text})")
    elif _empty(value):
        report.unfilled.append(f"{where}: empty on {source.id} in the old bot ({step_text})")
    else:
        state.params[target.param] = value
        report.filled.append(f"{where} = {value!r} (from {source.id})")


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


def _sort_old_nodes(old: OldBot, sources: dict[str, StateModel], template: BotConfig, report: Report) -> None:
    used = {s.id for s in sources.values()}
    template_types = {s.node_type for s in template.states}
    for state in walk_order(old.config):
        if state.id in used:
            continue
        if state.id in old.keep:
            report.content.append(_label(state))
        elif state.id in old.folded or (state.node_type in SCALING_FOLD and "handle_level_scaling" in template_types):
            report.folded.append(_label(state))
        elif state.node_type in template_types and state.node_type not in (*MAPPED_TYPES, "collect_reagent"):
            # A potion, quicksell or wisp node the template runs at its own place. A node of a
            # type a value was read from, but not the one it was read from, is content: a
            # second fight, a fourth spawn, a room-to-room zone change.
            report.covered.append(_label(state))
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
            lines.append(f"{path.stem:44} {classify(config):11} not rebuilt")
            continue
        done += 1
        steps = setup_steps.steps_for(config, root, root)
        left = setup_steps.steps_left(config, steps, library)
        name = names.get(config.template.id, config.template.id)
        lines.append(f"{path.stem:44} {classify(config):11} {name} v{config.template.version}, {left} steps left")
    lines.append(f"{done} of {len(bots)} bots rebuilt")
    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _rebuild_one(name: str, template: str | None, write: bool) -> int:
    path = find_bot(name)
    old = BotConfig.load(path)
    if old.template is not None:
        raise RebuildError(f"{path.stem} already records {old.template.id} v{old.template.version}; nothing to rebuild")
    category = classify(old)
    template_name = template or TEMPLATE_NAMES.get(category)
    if template_name is None:
        raise RebuildError(f"{path.stem} is no template's category ({category}); pass --template")
    if template is not None:
        category = next((c for c, n in TEMPLATE_NAMES.items() if n == template), category)
    config, report = rebuild(
        old, find_start(template_name), SubgraphLibrary(PROJECT_ROOT, PROJECT_ROOT), category=category
    )
    print("\n".join(report.lines()))
    if write:
        config.save(path)
        print(f"  wrote {path.as_posix()}")
    else:
        print("  (dry run: nothing written; add --write)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    rebuild_cmd = commands.add_parser("rebuild", help="rebuild bots from their templates")
    rebuild_cmd.add_argument("bots", nargs="+", help="file stems or paths under bots/")
    rebuild_cmd.add_argument("--template", help="the template's name, when its nodes say a different one")
    rebuild_cmd.add_argument("--write", action="store_true", help="write the rebuilt bot over the old file")
    commands.add_parser("status", help="every bot: its category, template and setup steps left")
    args = parser.parse_args(argv)
    # A step's label carries "·" and "›", which a Windows console's code page cannot print.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.command == "status":
        print("\n".join(status_lines()))
        return 0
    failed = 0
    for name in args.bots:
        try:
            _rebuild_one(name, args.template, args.write)
        except RebuildError as error:
            print(f"{name}: {error}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
