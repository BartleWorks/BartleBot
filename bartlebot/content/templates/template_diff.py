"""What a newer template version changes in a bot made from an older one, and applying it.

A bot made from a template records the version it was based on (``template.version``,
the *base*). When the template releases a newer one, this module compares three configs:

- **base**: the released version the bot was made from, or last finished reviewing against;
- **theirs**: the latest released version;
- **mine**: the bot as it stands.

Each is turned into a set of **units**, the smallest things compared: one param, one
label, one edge slot, one input, one option, one variable, one role. A unit the template did not change
is never shown. One it did change is hidden when the bot already matches, **offered** when
the user never touched it, and a **conflict** when the user changed it too. A removal is a
conflict as well when the bot wired edges of its own to what it removes, since applying it
drops them: the change names the nodes left to rewire.

Units are grouped into **changes**, which is what the user applies or skips. A node added
together with its params, its label, its own edges and every edge that now points at it
is one change, and so is a removal; every other unit is a change of its own. Applying is a
pure function (:func:`apply`). The editor wraps it in one undo step.

Never units, because they are the bot's own or follow something else: positions; name,
description, tags and authors; plain regions; ``drops``; the members inside a subgraph
node, and its ``version`` and snapshot, which follow their definition; every fill-in the
base or the latest version names; and anything on a node the bot itself added.

Skips are remembered by advancing the base (:func:`finish`), not by an ignore list: a
finished review is not offered again.

Edges are compared by **slot**: (source, ``when``, flags) is the key, and where the edge
lands and what it sets when taken (:class:`SlotValue`) the value, so a rewire, or a
changed assignment, is one slot's value changing. An end on a subgraph node is
normalized to its port, so a definition that moved a port's member is not a template
change.

A variable the template declares or drops is one change with every edge and node of that
release that reads or writes it, since either alone would leave a row naming a variable
the bot does not declare, which refuses the run.

No Qt here. The Setup tab (``gui/sidebar/setup_panel.py``) draws what :func:`review` returns.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from bartlebot.bot import variables
from bartlebot.bot.schema import (
    AssignmentModel,
    BotConfig,
    BotOptionModel,
    FlagCondition,
    RegionModel,
    StateModel,
    TransitionModel,
    VariableModel,
)
from bartlebot.combat import fight_keys
from bartlebot.content.subgraphs.subgraphs import (
    drop_states,
    entry_port_of,
    exit_port_of,
    member_id,
    nested_regions,
    outermost_linked_regions,
    reresolve_member_params,
)
from bartlebot.content.templates import template_store
from bartlebot.content.templates.template_store import id_prefixes, is_template_owned
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS
from bartlebot.options.bot_options import storable_options

logger = logging.getLogger(__name__)


class _Missing:
    """The value of a unit a config does not have."""

    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()


@dataclass(frozen=True)
class Port:
    """An end of an edge on a subgraph node: the port it uses, not the member behind it."""

    region: str
    port: str


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def _canon_row(f: FlagCondition) -> tuple:
    """One condition row as a hashable tuple: ``(option, negate)``, or ``(variable, compare, value)``."""
    if f.is_variable:
        return (f.variable, f.compare, f.value)
    return (f.option, f.negate)


def _canon_flags(t: TransitionModel) -> tuple:
    """A transition's flag guard in one canonical form, so two spellings of one guard share a slot.

    Option and variable rows alike: a changed comparison is a different guard, so it moves
    the edge to another slot and shows as a change to it.
    """
    if t.flag_groups:
        groups = frozenset(
            (frozenset(_canon_row(f) for f in g.flags), g.op if len(g.flags) > 1 else "AND") for g in t.flag_groups
        )
        return ("groups", groups, t.flag_groups_op if len(groups) > 1 else "OR")
    if t.flags:
        return ("flags", frozenset(_canon_row(f) for f in t.flags), t.flag_op if len(t.flags) > 1 else "AND")
    return ()


def _canon_assign(t: TransitionModel) -> tuple:
    """An edge's assignments as a hashable tuple of ``(variable, op, value)``, in order: a later row sees an earlier one."""
    return tuple((a.variable, a.op, a.value) for a in t.assign)


@dataclass(frozen=True)
class SlotValue:
    """What an edge slot holds: where the edge lands, and what it sets when taken (#189)."""

    dest: str | Port
    assign: tuple = ()


@dataclass
class _Snapshot:
    """One config as units, with what applying a unit needs to copy from it."""

    config: BotConfig
    units: dict[tuple, Any] = field(default_factory=dict)
    states: dict[str, StateModel] = field(default_factory=dict)
    #: Outermost subgraph instances the template owns, by id.
    instances: dict[str, RegionModel] = field(default_factory=dict)
    #: The first transition in each slot.
    edges: dict[tuple, TransitionModel] = field(default_factory=dict)
    #: State id to the outermost subgraph node drawing it.
    member_of: dict[str, RegionModel] = field(default_factory=dict)


def _end(snap: _Snapshot, state_id: str) -> str | Port:
    """Where an edge or a role lands: a node, or the entry port of a subgraph node."""
    region = snap.member_of.get(state_id)
    if region is None or region.source is None:
        return state_id
    port = entry_port_of(region.source, region.id, state_id)
    return Port(region.id, port) if port is not None else state_id


def _snapshot(config: BotConfig, prefixes: tuple[str, ...]) -> _Snapshot:
    """``config`` as units. ``prefixes`` say which of its nodes the template owns (:attr:`TemplateUpdate.prefixes`)."""
    snap = _Snapshot(config)
    for region in outermost_linked_regions([r for r in config.regions if r.source is not None]):
        for m in region.members:
            snap.member_of[m] = region
        if is_template_owned(region.id, prefixes):
            snap.instances[region.id] = region
    units = snap.units

    for s in config.states:
        if not is_template_owned(s.id, prefixes) or s.id in snap.member_of:
            continue
        snap.states[s.id] = s
        units[("node", s.id)] = s.node_type
        units[("label", s.id)] = s.label
        units[("stops", s.id)] = dict(s.stops) if s.stops else None
        for key, value in s.params.items():
            units[("param", s.id, key)] = value

    for region in snap.instances.values():
        units[("instance", region.id)] = region.source.subgraph_id
        for name, value in region.source.inputs.items():
            if value is not None:
                units[("input", region.id, name)] = value

    for t in config.transitions:
        region = snap.member_of.get(t.source)
        if region is not None:
            if region.id not in snap.instances or snap.member_of.get(t.dest) is region:
                continue
            port = exit_port_of(region.source, region.id, t.source, t.when)
            if port is None:
                continue
            key = ("slot", Port(region.id, port), None, _canon_flags(t))
        elif t.source in snap.states:
            key = ("slot", t.source, t.when, _canon_flags(t))
        else:
            continue
        if key not in units:
            units[key] = SlotValue(_end(snap, t.dest), _canon_assign(t))
            snap.edges[key] = t

    for option in storable_options(config):
        units[("option", option.name)] = option.model_dump(exclude_none=True)
    for variable in config.variables:
        units.setdefault(("variable", variable.name), variable.model_dump(exclude_none=True))
    units[("role", "initial")] = _end(snap, config.initial)
    units[("role", "recovery_state")] = _end(snap, config.recovery_state)
    units[("role", "clients")] = config.clients
    return snap


def _fill_in_keys(config: BotConfig) -> set[tuple]:
    """The unit keys of every fill-in ``config``'s own setup steps name. They are the user's to fill."""
    keys: set[tuple] = set()
    for step in config.template_meta.setup if config.template_meta is not None else ():
        for target in step.targets:
            if target.state is not None and target.param is not None:
                keys.add(("param", target.state, target.param))
            elif target.region is not None and target.input is not None:
                keys.add(("input", target.region, target.input))
    return keys


def _owner(key: tuple) -> str | None:
    """The node or subgraph node a unit belongs to; None for an option or a role."""
    kind = key[0]
    if kind in ("node", "label", "stops", "param", "instance", "input"):
        return key[1]
    if kind == "slot":
        return key[1].region if isinstance(key[1], Port) else key[1]
    return None


def _entity(end: Any) -> str | None:
    """The node or subgraph node an edge end, a slot or a role names."""
    if isinstance(end, SlotValue):
        return _entity(end.dest)
    if isinstance(end, Port):
        return end.region
    return end if isinstance(end, str) else None


# ---------------------------------------------------------------------------
# The update, and the diff
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Release:
    """One released version newer than the bot's base."""

    version: int
    note: str
    config: BotConfig


@dataclass
class TemplateUpdate:
    """What a bot is behind: its base version, and every version released since, oldest first."""

    template_id: str
    name: str
    base_version: int
    base: BotConfig
    releases: list[Release]

    @property
    def latest_version(self) -> int:
        return self.releases[-1].version

    @property
    def theirs(self) -> BotConfig:
        return self.releases[-1].config

    @property
    def prefixes(self) -> tuple[str, ...]:
        """What the template's own ids start with, in any version: ``t_``, or a fork's lineage (#164).

        The bot carries no ``template_meta``, so its versions are what say which of its
        nodes the template owns.
        """
        found = dict.fromkeys(id_prefixes(self.base))
        for release in self.releases:
            found.update(dict.fromkeys(id_prefixes(release.config)))
        return tuple(found)


def load_update(
    config: BotConfig | None, root: str | Path | None = None, user_root: str | Path | None = None
) -> TemplateUpdate | None:
    """The update waiting for ``config``, or None: a bot made from no template, or one that is not behind.

    None as well when the store does not have the template or the base version, as for a
    bot shared without its template: there is nothing to compare against.
    """
    if config is None or config.template is None or config.is_template:
        return None
    ref = config.template
    template = template_store.find_template(ref.id, root, user_root)
    if template is None or template.latest_version is None or template.latest_version <= ref.version:
        return None
    try:
        base = BotConfig.load(template.version_path(ref.version))
        releases = [
            Release(entry.version, entry.note, BotConfig.load(template.folder / entry.file))
            for entry in sorted(template.index.versions, key=lambda e: e.version)
            if entry.version > ref.version
        ]
    except Exception:
        logger.warning(
            "No template update for %s: a version of %s does not load", config.name, template.name, exc_info=True
        )
        return None
    return TemplateUpdate(ref.id, template.name, ref.version, base, releases)


@lru_cache(maxsize=8)
def _latest_versions(shipped_dir: str, user_dir: str, stamp: tuple) -> dict[str, int]:
    del stamp  # only keys the cache: a release rewrites an index, which moves its mtime
    return {
        t.id: t.latest_version
        for t in template_store.list_templates(Path(shipped_dir).parent, Path(user_dir).parent)
        if t.latest_version is not None
    }


def latest_versions(root: str | Path | None = None, user_root: str | Path | None = None) -> Mapping[str, int]:
    """Template id to its latest released version, cached until an index file changes in either root."""
    shipped_dir, user_dir = template_store.templates_dirs(root, user_root)
    try:
        stamp = tuple(
            sorted(
                (str(p), p.stat().st_mtime_ns)
                for folder in {shipped_dir, user_dir}
                for p in folder.glob(f"*/{template_store.INDEX_FILE}")
            )
        )
    except OSError:
        return {}
    return _latest_versions(str(shipped_dir), str(user_dir), stamp)


def behind_in_file(
    data: Mapping[str, Any], root: str | Path | None = None, user_root: str | Path | None = None
) -> bool:
    """Whether a bot file's raw ``data`` names a template version older than the latest. What a library row asks."""
    ref = data.get("template")
    if not isinstance(ref, dict) or not isinstance(ref.get("version"), int):
        return False
    latest = latest_versions(root, user_root).get(str(ref.get("id")))
    return latest is not None and latest > ref["version"]


class Status(Enum):
    #: The template changed it and the bot never touched it.
    OFFERED = "offered"
    #: Both changed it: the user picks the template's value or keeps their own.
    CONFLICT = "conflict"
    #: It needs a node or a port the bot does not have.
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Unit:
    key: tuple
    base: Any
    theirs: Any
    mine: Any

    @property
    def conflicts(self) -> bool:
        return self.mine != self.base


@dataclass
class Change:
    """A group of units applied or skipped together."""

    id: str
    status: Status
    title: str
    detail: str
    #: The release that made it: the last one to touch any of its units.
    version: int
    units: list[Unit]
    #: Why it cannot be applied; empty unless ``status`` is ``BLOCKED``.
    reason: str = ""
    #: What to select on the canvas for it: state ids, or a region id for a subgraph node.
    nodes: tuple[str, ...] = ()
    theirs: _Snapshot | None = field(default=None, repr=False, compare=False)

    @property
    def added(self) -> list[str]:
        """Ids of the nodes and subgraph nodes this change adds."""
        return [u.key[1] for u in self.units if u.key[0] in ("node", "instance") and u.base is MISSING]

    @property
    def removed(self) -> list[str]:
        return [u.key[1] for u in self.units if u.key[0] in ("node", "instance") and u.theirs is MISSING]


@dataclass
class Review:
    """What the Setup tab draws for a bot behind its template: the changes, under each release's note."""

    name: str
    base_version: int
    latest_version: int
    #: (version, note, its changes), oldest first. A release with nothing for this bot is still listed.
    sections: list[tuple[int, str, list[Change]]]

    @property
    def changes(self) -> list[Change]:
        return [c for _, _, changes in self.sections for c in changes]

    def change(self, change_id: str) -> Change | None:
        return next((c for c in self.changes if c.id == change_id), None)


def _name(snap: _Snapshot, entity: str) -> str:
    """What a node or subgraph node is called, for a change's title."""
    state = next((s for s in snap.config.states if s.id == entity), None)
    if state is not None:
        return state.label or state.node_type
    region = next((r for r in snap.config.regions if r.id == entity), None)
    return region.label if region is not None else entity


def _param_label(node_type: str, key: str) -> str:
    field_ = next((f for f in NODE_PARAM_SCHEMAS.get(node_type, []) if f.key == key), None)
    return (field_.label or key) if field_ is not None else key


def _fmt(snap: _Snapshot, value: Any) -> str:
    """A unit's value in a few words."""
    if value is MISSING:
        return "nothing"
    if isinstance(value, SlotValue):
        dest = _fmt(snap, value.dest)
        return f"{dest}, setting {_sets_text(value.assign)}" if value.assign else dest
    if isinstance(value, Port):
        return f"'{_name(snap, value.region)}'"
    if isinstance(value, str):
        if any(s.id == value for s in snap.config.states):
            return f"'{_name(snap, value)}'"
        return f"'{value}'" if value else "blank"
    if isinstance(value, dict) and set(value) == {"x", "y", "z"}:
        return f"({value['x']:g}, {value['y']:g}, {value['z']:g})"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "none"
    if value is None:
        return "blank"
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def _describe(unit: Unit, theirs: _Snapshot, base: _Snapshot, mine: _Snapshot) -> tuple[str, str]:
    """A single unit's change as a title and a detail line."""
    key = unit.key
    kind = key[0]
    home = theirs if unit.theirs is not MISSING else base

    def fmt(value: Any) -> str:
        return _fmt(theirs if value is unit.theirs else (mine if value is unit.mine else base), value)

    arrow = f"{fmt(unit.base)} → {fmt(unit.theirs)}"
    if unit.conflicts:
        arrow += f". Yours: {fmt(unit.mine)}"
    owner = _owner(key) or ""
    # By the name the node has in the bot, which is the one on the user's canvas.
    in_bot = any(s.id == owner for s in mine.config.states) or any(r.id == owner for r in mine.config.regions)
    named = mine if in_bot else home
    if kind == "param":
        node = home.states.get(key[1]) or mine.states.get(key[1])
        label = _param_label(node.node_type, key[2]) if node is not None else key[2]
        return f"{_name(named, key[1])} · {label}", arrow
    if kind == "label":
        return f"Rename '{unit.base or key[1]}'", arrow
    if kind == "stops":
        return f"{_name(named, key[1])} · stops", "Which outcomes end the run changed"
    if kind == "node":
        return f"{_name(named, key[1])} · node type", arrow
    if kind == "input":
        return f"{_name(named, key[1])} · {key[2]}", arrow
    if kind == "instance":
        return f"{_name(named, key[1])} · subgraph", "The template uses a different subgraph here"
    if kind == "option":
        return f"Option '{key[1]}'", "Removed" if unit.theirs is MISSING else "Its declaration changed"
    if kind == "variable":
        if unit.base is MISSING:
            return f"Variable '{key[1]}'", "Added"
        return f"Variable '{key[1]}'", "Removed" if unit.theirs is MISSING else "Its declaration changed"
    if kind == "role":
        what = {"initial": "Initial node", "recovery_state": "Recovery node", "clients": "Players"}[key[1]]
        return what, arrow
    source = _name(named, owner)
    outcome = f"port {key[1].port}" if isinstance(key[1], Port) else (key[2] or "any outcome")
    guard = (" (with option conditions)" if _options_only(key[3]) else " (with conditions)") if key[3] else ""
    if isinstance(unit.base, SlotValue) and isinstance(unit.theirs, SlotValue) and unit.base.dest == unit.theirs.dest:
        # Only what the edge sets changed, so that is what the detail says.

        def sets(value: SlotValue) -> str:
            return f"sets {_sets_text(value.assign)}" if value.assign else "sets nothing"

        arrow = f"When taken, {sets(unit.base)} → {sets(unit.theirs)}"
        if unit.conflicts:
            arrow += f". Yours: {sets(unit.mine) if isinstance(unit.mine, SlotValue) else fmt(unit.mine)}"
    return f"{source} · on {outcome}{guard}", arrow


def _sets_text(assign: tuple) -> str:
    """A slot's canonical assignments (:func:`_canon_assign`) as the edge's label says them: ``swords + 1``."""
    rows = [AssignmentModel(variable=v, op=op, value=value) for v, op, value in assign]
    return variables.describe_assignments(rows) or "nothing"


def _variables_named(unit: Unit) -> set[str]:
    """The variables a unit reads or writes, in either of the template's versions: what groups it with their declarations."""
    kind = unit.key[0]
    found: set[str] = set()
    if kind == "variable":
        found.add(unit.key[1])
    elif kind == "slot":
        guard = unit.key[3]
        if guard:
            rows = guard[1] if guard[0] == "flags" else [r for group, _op in guard[1] for r in group]
            found.update(r[0] for r in rows if len(r) == 3)
        for value in (unit.base, unit.theirs):
            if isinstance(value, SlotValue):
                found.update(row[0] for row in value.assign)
    elif kind == "param" and unit.key[2] == variables.ASSIGNMENTS_PARAM:
        for value in (unit.base, unit.theirs):
            if isinstance(value, list):
                found.update(r["variable"] for r in value if isinstance(r, dict) and isinstance(r.get("variable"), str))
    return found


def _options_only(guard: tuple) -> bool:
    """Whether a canonical guard (:func:`_canon_flags`) reads options alone, and no variable."""
    rows = guard[1] if guard[0] == "flags" else [r for found, _op in guard[1] for r in found]
    return all(len(r) == 2 for r in rows)


def _guard_text(guard: tuple) -> str:
    """A canonical flag guard (:func:`_canon_flags`) spelled out, the same way every time.

    In full, not as a marker that there is one: two edges from one node on one outcome
    differ only by their guards, and a change is found again by its id.
    """
    if not guard:
        return ""

    def row(found: tuple) -> str:
        if len(found) == 3:
            variable, compare, value = found
            return f"{variable}{compare}{value}"
        option, negate = found
        return f"{'!' if negate else ''}{option}"

    def flags(found) -> str:
        return ",".join(sorted(row(f) for f in found))

    if guard[0] == "flags":
        return f"{guard[2]}({flags(guard[1])})"
    groups = sorted(f"{op}({flags(found)})" for found, op in guard[1])
    return f"{guard[2]}[{';'.join(groups)}]"


def _change_id(units: Sequence[Unit]) -> str:
    def part(value: Any) -> str:
        if isinstance(value, Port):
            return f"{value.region}@{value.port}"
        if isinstance(value, tuple):
            return _guard_text(value)
        return "" if value is None else str(value)

    # Named for the node it adds or removes when it has one, else for its one unit.
    whole = [u for u in units if u.key[0] in ("node", "instance")]
    first = min(whole or units, key=lambda u: repr(u.key))
    return ":".join(part(p) for p in first.key)


def diff(update: TemplateUpdate, mine: BotConfig, library=None) -> list[Change]:
    """Every change ``update`` has for ``mine``, oldest release first. Hidden ones are left out.

    ``library`` is passed to the trial :func:`apply` each change is given, so a change
    that would not leave a valid bot is reported as blocked instead of raising later.
    """
    prefixes = update.prefixes
    base = _snapshot(update.base, prefixes)
    theirs = _snapshot(update.theirs, prefixes)
    own = _snapshot(mine, prefixes)
    skip = _fill_in_keys(update.base) | _fill_in_keys(update.theirs)

    changed: list[Unit] = []
    for key in list(dict.fromkeys([*base.units, *theirs.units])):
        if key in skip:
            continue
        b, t = base.units.get(key, MISSING), theirs.units.get(key, MISSING)
        if b != t:
            changed.append(Unit(key, b, t, own.units.get(key, MISSING)))

    added = {u.key[1] for u in changed if u.key[0] in ("node", "instance") and u.base is MISSING}
    removed = {u.key[1] for u in changed if u.key[0] in ("node", "instance") and u.theirs is MISSING}
    # A variable declared or dropped goes with what uses it: applied alone, either side
    # leaves a row naming a variable the bot does not declare.
    declared = {u.key[1] for u in changed if u.key[0] == "variable" and MISSING in (u.base, u.theirs)}

    # Group around the nodes a release added or removed: a node, what it carries, and
    # every edge and role that now points at it or used to.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    anchors: list[list[str]] = []
    for unit in changed:
        near = []
        owner = _owner(unit.key)
        if owner in added or owner in removed:
            near.append(owner)
        if unit.key[0] in ("slot", "role"):
            if _entity(unit.theirs) in added:
                near.append(_entity(unit.theirs))
            if _entity(unit.base) in removed:
                near.append(_entity(unit.base))
        near += [_variable_anchor(name) for name in sorted(_variables_named(unit) & declared)]
        for other in near[1:]:
            parent[find(other)] = find(near[0])
        anchors.append(near)

    groups: dict[str, list[Unit]] = {}
    order: list[str] = []
    for unit, near in zip(changed, anchors):
        name = f"group:{find(near[0])}" if near else f"unit:{len(order)}"
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(unit)

    versions = _unit_versions(update, base, prefixes)
    changes: list[Change] = []
    for name in order:
        units = []
        for unit in groups[name]:
            owner = _owner(unit.key)
            deleted = (
                owner is not None
                and ("node", owner) not in own.units
                and ("instance", owner) not in own.units
                and (("node", owner) in base.units or ("instance", owner) in base.units)
            )
            # The bot already matches, or the user removed what it belongs to on purpose.
            if unit.mine == unit.theirs or deleted:
                continue
            units.append(unit)
        if not units:
            continue
        change = _build_change(units, base, theirs, own)
        change.version = max(versions.get(u.key, update.latest_version) for u in units)
        if change.status is not Status.BLOCKED:
            try:
                apply(mine, change, library=library)
            except Exception as exc:
                logger.debug("Template change %s cannot be applied: %s", change.id, exc)
                change.status = Status.BLOCKED
                change.reason = "It can't be applied to this bot as it stands."
        changes.append(change)
    changes.sort(key=lambda c: c.version)
    # An id is what Apply and Keep mine find a change by, so no two may share one. Each
    # is spelled from a unit key, and keys are distinct; this is for a name that happens
    # to contain the separator.
    seen: dict[str, int] = {}
    for change in changes:
        seen[change.id] = seen.get(change.id, 0) + 1
        if seen[change.id] > 1:
            change.id = f"{change.id}#{seen[change.id]}"
    return changes


def _variable_anchor(name: str) -> str:
    """What a declared or dropped variable's change is grouped under. Never a node id."""
    return f"\0variable:{name}"


def _unit_versions(update: TemplateUpdate, base: _Snapshot, prefixes: tuple[str, ...]) -> dict[tuple, int]:
    """Each unit's last release to change it, walking the releases in order."""
    last: dict[tuple, int] = {}
    previous = base.units
    for release in update.releases:
        units = _snapshot(release.config, prefixes).units
        for key in {*previous, *units}:
            if previous.get(key, MISSING) != units.get(key, MISSING):
                last[key] = release.version
        previous = units
    return last


def _home(snap: _Snapshot, state_id: str) -> str:
    """The node an edge end is drawn on: the state itself, or the subgraph node drawing it."""
    region = snap.member_of.get(state_id)
    return region.id if region is not None else state_id


def _edge_key(snap: _Snapshot, t: TransitionModel) -> tuple | None:
    """A transition with both ends normalized as a slot's are; None for one inside a subgraph node."""
    region = snap.member_of.get(t.source)
    source: tuple = (t.source, t.when)
    if region is not None:
        if snap.member_of.get(t.dest) is region:
            return None
        port = exit_port_of(region.source, region.id, t.source, t.when)
        if port is not None:
            source = (Port(region.id, port), None)
    return (*source, _canon_flags(t), _end(snap, t.dest))


def _lost_edges(removed: Sequence[str], base: _Snapshot, own: _Snapshot) -> list[TransitionModel]:
    """The bot's own edges that go with the nodes a change removes: ones touching them that base never had.

    No unit holds them. An edge out of a node the bot added is never a unit, and one the
    bot wired into an empty slot of a removed node is a slot the template left empty in
    both versions. Removing the node drops them all the same, so the change says so.
    """
    gone = set(removed)
    if not gone:
        return []
    template_edges = {key for t in base.config.transitions if (key := _edge_key(base, t)) is not None}
    lost = []
    for t in own.config.transitions:
        key = _edge_key(own, t)
        if key is None or key in template_edges:
            continue
        if _home(own, t.source) in gone or _home(own, t.dest) in gone:
            lost.append(t)
    return lost


def _lost_text(lost: Sequence[TransitionModel], left: Sequence[str], own: _Snapshot) -> str:
    """ "Drops 2 edges you added: 'Defeat Lamia' will need rewiring"."""
    text = f"Drops {len(lost)} edge{'s' if len(lost) != 1 else ''} you added"
    if not left:
        return text
    names = [f"'{_name(own, entity)}'" for entity in left]
    named = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
    return f"{text}: {named} will need rewiring"


def _blocker(units: Sequence[Unit], own: _Snapshot, theirs: _Snapshot) -> str:
    """Why these units cannot land in the bot, or "" when they can."""
    adds = {u.key[1] for u in units if u.key[0] in ("node", "instance") and u.base is MISSING}

    def end_missing(end: Any) -> str:
        if isinstance(end, Port):
            if end.region in adds:
                return ""
            region = own.instances.get(end.region)
            if region is None:
                return f"This bot has no '{_name(theirs, end.region)}'."
            port = region.source.ports.get(end.port)
            if port is None or port.member is None:
                return f"This bot's '{region.label}' has no port {end.port}. Update the subgraph first."
            return ""
        if isinstance(end, SlotValue):
            return end_missing(end.dest)
        if isinstance(end, str) and end not in adds and not any(s.id == end for s in own.config.states):
            return f"This bot has no '{_name(theirs, end)}'."
        return ""

    for unit in units:
        key = unit.key
        if key[0] == "instance" and MISSING not in (unit.base, unit.theirs):
            return "The template uses a different subgraph here. Replace it by hand."
        if key[0] == "slot" and isinstance(key[1], Port) and key[1].region not in adds:
            region = own.instances.get(key[1].region)
            port = region.source.ports.get(key[1].port) if region is not None else None
            if port is None or not port.from_:
                return f"This bot's '{_name(theirs, key[1].region)}' has no port {key[1].port}."
        lands = key[0] == "slot" or (key[0] == "role" and key[1] != "clients")
        if lands and unit.theirs is not MISSING:
            reason = end_missing(unit.theirs)
            if reason:
                return reason
    return ""


def _build_change(units: list[Unit], base: _Snapshot, theirs: _Snapshot, own: _Snapshot) -> Change:
    adds = [u.key[1] for u in units if u.key[0] in ("node", "instance") and u.base is MISSING]
    drops = [u.key[1] for u in units if u.key[0] in ("node", "instance") and u.theirs is MISSING]
    if adds or drops:
        parts = []
        if adds:
            parts.append("Add " + ", ".join(f"'{_name(theirs, a)}'" for a in adds))
        if drops:
            parts.append(("remove " if adds else "Remove ") + ", ".join(f"'{_name(base, d)}'" for d in drops))
        declared = [u.key[1] for u in units if u.key[0] == "variable" and u.base is MISSING]
        dropped = [u.key[1] for u in units if u.key[0] == "variable" and u.theirs is MISSING]
        if declared:
            parts.append("declare " + ", ".join(f"'{v}'" for v in declared))
        if dropped:
            parts.append("drop variable " + ", ".join(f"'{v}'" for v in dropped))
        title = "; ".join(parts)
        rewired = sum(
            1 for u in units if u.key[0] == "slot" and _owner(u.key) not in adds and _owner(u.key) not in drops
        )
        detail = f"With {rewired} edge{'s' if rewired != 1 else ''} rewired around it" if rewired else ""
    else:
        # A variable declared together with the edges that use it is named for the variable.
        lead = next((u for u in units if u.key[0] == "variable"), units[0])
        title, detail = _describe(lead, theirs, base, own)
        if len(units) > 1:
            detail = f"{detail} (and {len(units) - 1} more)"
    # The nodes left on the other end of an edge the removal drops: the user's to rewire.
    lost = _lost_edges(drops, base, own)
    left = list(dict.fromkeys(e for t in lost for e in (_home(own, t.source), _home(own, t.dest)) if e not in drops))
    if lost:
        detail = ". ".join(part for part in (detail, _lost_text(lost, left, own)) if part)
    reason = _blocker(units, own, theirs)
    # A unit the bot never touched keeps base's value. One being added has no value there
    # either way, so only an existing unit the user changed is a conflict, or a removal
    # that would take edges the user wired with it.
    conflict = any(u.conflicts for u in units) or bool(lost)
    status = Status.BLOCKED if reason else (Status.CONFLICT if conflict else Status.OFFERED)
    nodes: list[str] = []
    for unit in units:
        for entity in (_owner(unit.key), _entity(unit.theirs) if unit.key[0] in ("slot", "role") else None):
            if entity is not None and entity not in nodes:
                nodes.append(entity)
    nodes += [e for e in left if e not in nodes]
    return Change(
        id=_change_id(units),
        status=status,
        title=title,
        detail=detail,
        version=0,
        units=units,
        reason=reason,
        nodes=tuple(nodes),
        theirs=theirs,
    )


def review(update: TemplateUpdate | None, mine: BotConfig | None, library=None) -> Review | None:
    """The review the Setup tab shows, or None when there is no update."""
    if update is None or mine is None or mine.template is None:
        return None
    changes = diff(update, mine, library)
    sections = [(r.version, r.note, [c for c in changes if c.version == r.version]) for r in update.releases]
    return Review(update.name, update.base_version, update.latest_version, sections)


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------

#: Called with where an added node wants to go; returns where it goes. The editor's
#: moves it to a free grid cell when that spot is taken.
Placer = Callable[[dict], dict]


def _resolve(config: BotConfig, end: Any) -> str:
    """The state id an edge end names in ``config``. Raises ``LookupError`` if it has none."""
    if isinstance(end, Port):
        region = next((r for r in config.regions if r.id == end.region and r.source is not None), None)
        port = region.source.ports.get(end.port) if region is not None else None
        if port is None or port.member is None:
            raise LookupError(f"no entry port {end.port} on {end.region}")
        return member_id(region.id, port.member)
    if not any(s.id == end for s in config.states):
        raise LookupError(f"no node {end}")
    return end


def _beside(config: BotConfig, theirs: BotConfig, node_id: str) -> dict | None:
    """Where an added node goes: beside a node it is wired to, as far from it as in the template.

    The node it is wired *from* first, then one it leads to. None when no neighbor of
    it is in ``config`` yet, or the template gives no positions to measure with.
    """
    there = {s.id: s.position for s in theirs.states if s.position}
    here = {s.id: s.position for s in config.states if s.position}
    if node_id not in there:
        return None
    wired = [t.source for t in theirs.transitions if t.dest == node_id]
    wired += [t.dest for t in theirs.transitions if t.source == node_id]
    for other in wired:
        if other != node_id and other in there and other in here:
            return {
                "x": here[other]["x"] + there[node_id]["x"] - there[other]["x"],
                "y": here[other]["y"] + there[node_id]["y"] - there[other]["y"],
            }
    return None


#: The canvas's grid, in stored coordinates: a node's footprint and the width of a column.
#: Spelled out here, since this module has no Qt in it and the canvas's own constants
#: live beside its items (``gui/canvas/node_item.py``); a test holds the two together.
NODE_WIDTH = 300.0
NODE_HEIGHT = 70.0
COLUMN_WIDTH = 440.0


def _make_room(config: BotConfig, theirs: BotConfig, node_id: str, wanted: dict) -> bool:
    """Move the columns from *wanted* rightward over by one, when the added node goes in front of a node there.

    A node a template inserts between two others wants the spot the second one is in.
    Moving it to the nearest free cell instead put it off the line the edge ran along.
    So when the spot is taken and the added node leads on to something at or right of
    it, every column from there on moves over, all of each, and the node takes the spot.
    True when it did; False leaves placing to the caller.

    A subgraph node moves as one: its anchor, its members and the anchors nested in it.
    """
    member_of: dict[str, RegionModel] = {}
    for region in outermost_linked_regions([r for r in config.regions if r.source is not None]):
        for m in region.members:
            member_of[m] = region
    loose = [s for s in config.states if s.position and s.id not in member_of]
    anchored = {id(r): r for r in member_of.values() if r.source.anchor}.values()

    def over(spot: dict) -> bool:
        return abs(spot["x"] - wanted["x"]) < NODE_WIDTH and abs(spot["y"] - wanted["y"]) < NODE_HEIGHT

    if not any(over(s.position) for s in loose) and not any(over(r.source.anchor) for r in anchored):
        return False
    edge = wanted["x"] - 1
    here = {s.id: s.position for s in config.states if s.position}
    leads_to = {t.dest for t in theirs.transitions if t.source == node_id}
    if not any(d in here and here[d]["x"] >= edge for d in leads_to):
        return False

    def moved(spot: dict) -> dict:
        return {**spot, "x": spot["x"] + COLUMN_WIDTH}

    for state in loose:
        if state.position["x"] >= edge:
            state.position = moved(state.position)
    by_id = {s.id: s for s in config.states}
    for region in anchored:
        if region.source.anchor["x"] < edge:
            continue
        for r in [region, *nested_regions(config, region)]:
            if r.source is not None and r.source.anchor:
                r.source.anchor = moved(r.source.anchor)
        for m in region.members:
            if m in by_id and by_id[m].position:
                by_id[m].position = moved(by_id[m].position)
    return True


def _slot_sources(config: BotConfig, key: tuple) -> list[tuple[str, str | None]]:
    """The (source, when) pairs a slot is stored as in ``config``: one, or one per pair feeding an exit port."""
    source = key[1]
    if not isinstance(source, Port):
        return [(source, key[2])]
    region = next((r for r in config.regions if r.id == source.region and r.source is not None), None)
    port = region.source.ports.get(source.port) if region is not None else None
    if port is None or not port.from_:
        raise LookupError(f"no exit port {source.port} on {source.region}")
    return [(member_id(region.id, member), when) for member, when in port.from_]


def _apply_slot(config: BotConfig, unit: Unit, theirs: _Snapshot) -> None:
    flags = unit.key[3]
    value = unit.theirs
    dest = _resolve(config, value.dest) if value is not MISSING else None
    for source, when in _slot_sources(config, unit.key):
        held = [t for t in config.transitions if t.source == source and t.when == when and _canon_flags(t) == flags]
        if dest is None:
            config.transitions = [t for t in config.transitions if not any(t is h for h in held)]
        elif held:
            held[0].dest = dest
            held[0].assign = [AssignmentModel(variable=v, op=op, value=x) for v, op, x in value.assign]
            config.transitions = [t for t in config.transitions if not any(t is h for h in held[1:])]
        else:
            copy = theirs.edges[unit.key].model_copy(deep=True)
            copy.source, copy.when, copy.dest = source, when, dest
            config.transitions.append(copy)


def _add_nodes(config: BotConfig, change: Change, place: Placer | None) -> None:
    theirs = change.theirs
    assert theirs is not None
    pending = [i for i in change.added if i in theirs.states]

    def add(node_id: str, wanted: dict | None) -> None:
        state = theirs.states[node_id].model_copy(deep=True)
        if wanted is not None:
            if _make_room(config, theirs.config, node_id, wanted):
                state.position = dict(wanted)
            else:
                state.position = place(wanted) if place is not None else wanted
        config.states.append(state)
        pending.remove(node_id)

    # Each beside a node already here. One wired only to another being added waits a
    # round for it; one with no neighbor here at all keeps the template's position.
    progressed = True
    while pending and progressed:
        progressed = False
        for node_id in list(pending):
            wanted = _beside(config, theirs.config, node_id)
            if wanted is not None:
                add(node_id, wanted)
                progressed = True
    for node_id in list(pending):
        add(node_id, theirs.states[node_id].position)

    for region_id in change.added:
        region = theirs.instances.get(region_id)
        if region is None:
            continue
        members = set(region.members)
        anchor = dict(region.source.anchor)
        moved = place(anchor) if place is not None else anchor
        dx, dy = moved["x"] - anchor["x"], moved["y"] - anchor["y"]
        for state in theirs.config.states:
            if state.id in members:
                copy = state.model_copy(deep=True)
                if copy.position:
                    copy.position = {"x": copy.position["x"] + dx, "y": copy.position["y"] + dy}
                config.states.append(copy)
        config.transitions += [
            t.model_copy(deep=True) for t in theirs.config.transitions if t.source in members and t.dest in members
        ]
        for r in [region, *nested_regions(theirs.config, region)]:
            copy = r.model_copy(deep=True)
            if r is region:
                copy.source.anchor = moved
            config.regions.append(copy)


def apply(config: BotConfig, change: Change, *, place: Placer | None = None, library=None) -> BotConfig:
    """``config`` with ``change`` applied: all of its units, or an exception and nothing.

    Pure: ``config`` is not touched. ``place`` decides where an added node goes when its
    spot is taken. ``library`` (a ``SubgraphLibrary``) lets a changed input reach the
    members it drives; without one the next compose does it. For a conflict this takes
    the template's value; keeping the bot's own is not applying.
    """
    theirs = change.theirs
    if theirs is None:
        raise ValueError("a change has to come from diff()")
    if change.status is Status.BLOCKED:
        raise ValueError(change.reason)
    new = config.model_copy(deep=True)
    _add_nodes(new, change, place)
    states = {s.id: s for s in new.states}
    regions = {r.id: r for r in new.regions}
    touched_inputs: set[str] = set()
    removed: set[str] = set()

    for unit in change.units:
        key, value = unit.key, unit.theirs
        kind = key[0]
        if kind not in ("node", "instance") and _owner(key) in change.removed:
            continue  # goes with the node
        if kind == "node":
            if value is MISSING:
                removed.add(key[1])
            elif unit.base is not MISSING:
                states[key[1]].node_type = value
        elif kind == "instance":
            if value is MISSING:
                region = regions.get(key[1])
                if region is not None:
                    removed.update(region.members)
                    gone = {r.id for r in [region, *nested_regions(new, region)]}
                    new.regions = [r for r in new.regions if r.id not in gone]
        elif key[1] in change.added and kind in ("label", "stops", "param", "input"):
            continue  # came with the node
        elif kind == "label":
            states[key[1]].label = value
        elif kind == "stops":
            states[key[1]].stops = dict(value) if value else None
        elif kind == "param":
            if value is MISSING:
                states[key[1]].params.pop(key[2], None)
            else:
                states[key[1]].params[key[2]] = value
        elif kind == "input":
            inputs = regions[key[1]].source.inputs
            if value is MISSING:
                inputs.pop(key[2], None)
            else:
                inputs[key[2]] = value
            touched_inputs.add(key[1])
        elif kind == "option":
            # In the place it already has: the Options tab lists them in file order.
            at = next((i for i, o in enumerate(new.options) if o.name == key[1]), None)
            if value is MISSING:
                new.options = [o for o in new.options if o.name != key[1]]
            elif at is None:
                new.options.append(BotOptionModel.model_validate(value))
            else:
                new.options[at] = BotOptionModel.model_validate(value)
        elif kind == "variable":
            # The same, for the Variables section.
            at = next((i for i, v in enumerate(new.variables) if v.name == key[1]), None)
            if value is MISSING:
                new.variables = [v for v in new.variables if v.name != key[1]]
            elif at is None:
                new.variables.append(VariableModel.model_validate(value))
            else:
                new.variables[at] = VariableModel.model_validate(value)

    for unit in change.units:
        if unit.key[0] == "slot" and _owner(unit.key) not in change.removed:
            _apply_slot(new, unit, theirs)
        elif unit.key[0] == "role":
            if unit.key[1] == "clients":
                new.clients = unit.theirs
            else:
                setattr(new, unit.key[1], _resolve(new, unit.theirs))

    if removed:
        drop_states(new, removed)
    if library is not None:
        for region_id in touched_inputs:
            region = regions[region_id]
            defn = library.get(region.source.subgraph_id)
            if defn is not None:
                reresolve_member_params(new, region, defn)
    return BotConfig.model_validate(new.model_dump())


def finish(config: BotConfig, update: TemplateUpdate) -> BotConfig:
    """``config`` with its base advanced to the latest version: the review is over.

    This is what remembers a skipped change. Against the new base the template has not
    changed it, so it is not offered again.
    """
    if config.template is None:
        raise ValueError(f"{config.name!r} was not made from a template")
    new = config.model_copy(deep=True)
    new.template.version = update.latest_version
    return new


def strategies_lost(bcs, before: BotConfig, after: BotConfig) -> list[str]:
    """The fights that have a duel strategy in ``before`` and none in ``after``, by label.

    What applying a change that removes an exposed fight is asked about first. Goes
    through :func:`fight_keys.place`, so a fight the same change recreates against the
    same enemy keeps its strategy and is not listed.
    """
    kept = {id(fight) for _, a, fight in fight_keys.place(bcs.state_assignments, after).placed if a.is_linked}
    labels = {s.id: (s.label or s.id) for s in before.states}
    return [
        labels.get(state_id, state_id)
        for state_id, assignment, fight in fight_keys.place(bcs.state_assignments, before).placed
        if assignment.is_linked and id(fight) not in kept
    ]
