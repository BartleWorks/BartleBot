"""Composing subgraph instances into a bot, and extracting them back out. Pure.

A subgraph *definition* (``SubgraphDefinition``) is a user-made node type: its inputs
are its params, its Entry and Exit nodes are its ports, and its body holds states with
local ids and positions relative to ``(0, 0)``. An *instance* is an ordinary
``RegionModel`` whose ``source`` names the definition; its members are ordinary states
the engine sees as any other, and the editor draws the whole instance as one call node.
Everything here is a function over Pydantic models with no Qt, no settings and no file
access beyond the :class:`SubgraphLibrary` it is handed, so the whole policy is
testable on a ``tmp_path``. The plan is linked from docs/SUBGRAPH_CALLS.md.

The rules this module owns:

- **Member ids are derived**: ``<region id>_<local id>``, deterministic, so a
  definition update lands on the same ids that combat assignments and roles key on.
  A linked region's id is minted as ``<slug of the definition name>_<n>`` with ``n``
  above every number that slug has carried in the bot (:func:`mint_instance_id`).
  A nested instance prefixes again.
- **Nesting is membership**: an instance is inside another when every member of it
  is a member of the other. Ids are never parsed to find that out.
- **Port nodes are never composed.** The bot stores members as plain states and
  edges wired straight to members. What records which port each crossing uses is
  ``source.ports``, so a later definition change moves the bot's edges and roles
  with the port (:func:`compose_bot`).
- **An instance sets inputs and nothing else**: a member param is the bound input's
  value when the instance set one, and the definition's otherwise.
- **Layout is the definition's**: members sit at ``anchor + definition position``.
- **Composition never leaves a bot unloadable**: when the definition is missing,
  invalid, cyclic, or removes a port this instance uses, the stored snapshot is kept
  and the region is flagged in the :class:`ComposeReport`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from bartlebot.bot.schema import (
    PORT_NODE_TYPES,
    SUBGRAPH_ENTRY,
    SUBGRAPH_EXIT,
    SUBGRAPH_LOCAL_ID,
    BotConfig,
    DefinitionMeta,
    RegionModel,
    StateModel,
    SubgraphDefinition,
    SubgraphInput,
    SubgraphPanel,
    SubgraphPort,
    SubgraphSource,
    TransitionModel,
    definition_structure_errors,
)
from bartlebot.shared.content_hash import HISTORY_FIELDS
from bartlebot.store_roots import Origin
from bartlebot.subgraph_store import SubgraphLibrary

#: Nesting depth past which composition refuses. A cycle is caught by id before this
#: is reached; the limit is the second guard, against a chain that is merely absurd.
MAX_DEPTH = 8

#: How far a port node sits from the member it crosses at, when one is placed for the
#: author: one grid column, the editor's ``GRID_W``, so it lands where a node would.
PORT_COLUMN = 440.0

RegionKind = Literal["linked", "held", "missing", "cycle", "invalid"]


class SubgraphCycleError(Exception):
    """A definition contains itself, transitively. ``path`` is the id chain that closes."""

    def __init__(self, path: list[str]) -> None:
        self.path = list(path)
        super().__init__(" -> ".join(self.path))


# ---------------------------------------------------------------------------
# Ids
# ---------------------------------------------------------------------------


def member_id(region_id: str, local: str) -> str:
    """The bot-level id of ``local`` inside the instance ``region_id``."""
    return f"{region_id}_{local}"


def local_id(region_id: str, member: str) -> str | None:
    """The inverse of :func:`member_id`; ``None`` if ``member`` is not this region's.

    Only meaningful for a state that *is* a member of ``region_id``: another
    region's id may happen to extend this one ("farm_2" and "farm_2_1"), so ask
    membership first, never this.
    """
    prefix = f"{region_id}_"
    if member.startswith(prefix) and len(member) > len(prefix):
        return member[len(prefix) :]
    return None


def instance_slug(name: str) -> str:
    """``"Sync players"`` -> ``"sync_players"``: lowercase, runs of anything else to ``_``."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "subgraph"


def taken_ids(config: BotConfig) -> set[str]:
    """Every id an instance id must stay clear of: region ids and state ids."""
    return {r.id for r in config.regions} | {s.id for s in config.states}


def mint_instance_id(name: str, existing: Iterable[str], prefix: str = "") -> str:
    """A fresh linked-region id ``<prefix><slug>_<n>`` for a definition called ``name``.

    ``n`` is one above every number the slug already carries in ``existing`` --
    whether as a region id (``sync_players_2``) or as the head of a state id
    (``sync_players_2_state_3``, which a detached instance leaves behind as an
    ordinary state). Never the lowest free number, for the reason
    ``EditScene._unique_id`` gives: combat assignments key on state ids, and a
    recycled instance id would hand a deleted instance's strategies to the next.

    ``prefix`` is ``t_`` in a template draft (``t_sync_players_1``) and empty in a bot,
    and only ids carrying it are counted, so the two never number against each other.
    """
    slug = f"{prefix}{instance_slug(name)}"
    pattern = re.compile(rf"^{re.escape(slug)}_(\d+)(?:_|$)")
    highest = 0
    for i in existing:
        match = pattern.match(i)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{slug}_{highest + 1}"


def is_nested_in(inner: RegionModel, outer: RegionModel) -> bool:
    """Whether the linked region ``inner`` composes through ``outer``: every member is ``outer``'s."""
    return inner is not outer and bool(inner.members) and set(inner.members) <= set(outer.members)


def linked_regions(config: BotConfig) -> list[RegionModel]:
    return [r for r in config.regions if r.source is not None]


def outermost_linked_regions(regions: Sequence[RegionModel]) -> list[RegionModel]:
    """The linked regions not composed through another one, in file order: one call node each."""
    linked = [r for r in regions if r.source is not None]
    return [r for r in linked if not any(is_nested_in(r, o) for o in linked)]


def nested_regions(config: BotConfig, region: RegionModel) -> list[RegionModel]:
    """The linked regions composed through ``region``, in file order."""
    return [r for r in linked_regions(config) if is_nested_in(r, region)]


def linked_region_of(config: BotConfig, state_id: str) -> RegionModel | None:
    """The *outermost* linked region ``state_id`` belongs to -- the call node it is drawn as -- or ``None``."""
    holders = [r for r in linked_regions(config) if state_id in r.members]
    for r in holders:
        if not any(is_nested_in(r, o) for o in holders):
            return r
    return None


def _overlaps_partly(a: set[str], b: set[str]) -> bool:
    return bool(a & b) and not (a <= b or b <= a)


def is_collapsed(region: RegionModel, overrides: Mapping[str, bool] | None = None) -> bool:
    """Whether a plain region is drawn collapsed: its override for this session, else its saved flag.

    ``overrides`` is Run mode's: a collapse or expand there is a view, never saved,
    so it sits over the file's flag rather than changing it.
    """
    if overrides and region.id in overrides:
        return overrides[region.id]
    return bool(region.collapsed)


def collapse_blocker(config: BotConfig, region_id: str, overrides: Mapping[str, bool] | None = None) -> str | None:
    """Why the plain region ``region_id`` cannot be drawn as one node (#67), or ``None`` if it can.

    Collapsed nodes nest the way instances do, by membership, so a region may sit
    wholly inside or wholly around another folded one but never half across it: the
    states they share could then be drawn as either node. ``overrides`` as for
    :func:`is_collapsed`.
    """
    region = next((r for r in config.regions if r.id == region_id), None)
    if region is None:
        return "The region no longer exists."
    if region.source is not None:
        return "A subgraph node is already drawn as one node."
    members = set(region.members)
    if not members:
        return "The region has no nodes."
    by_id = {s.id: s for s in config.states}
    if any(m in by_id and is_port_node(by_id[m]) for m in members):
        return "It holds an Entry or Exit node, which the signature needs to show."
    for other in outermost_linked_regions(config.regions):
        if _overlaps_partly(members, set(other.members)):
            return f"It holds only part of the subgraph node '{other.label}'."
    for other in config.regions:
        if other is region or other.source is not None or not is_collapsed(other, overrides):
            continue
        if _overlaps_partly(members, set(other.members)):
            return f"It overlaps the collapsed region '{other.label}'."
    return None


def folded_regions(
    config: BotConfig, expanded: Iterable[str] = (), overrides: Mapping[str, bool] | None = None
) -> list[RegionModel]:
    """The regions drawn as one node each, outermost only, in file order.

    Every linked region not composed through another, as before, plus every plain
    region marked ``collapsed`` (#67). A region in ``expanded`` -- opened in the
    Run-mode view, or stepped into -- is not folded, so what it holds is considered in
    its place. ``overrides`` as for :func:`is_collapsed`. A hand-edited file can mark a collapse that :func:`collapse_blocker`
    would refuse; such a region is drawn expanded rather than guessed at. Larger
    plain regions are taken first, so of two that partly overlap the larger folds.
    """
    shut = set(expanded)
    linked = outermost_linked_regions([r for r in config.regions if r.id not in shut])
    by_id = {s.id: s for s in config.states}
    plain = [
        r
        for r in config.regions
        if r.source is None
        and is_collapsed(r, overrides)
        and r.id not in shut
        and r.members
        and not any(m in by_id and is_port_node(by_id[m]) for m in r.members)
    ]
    order = {r.id: i for i, r in enumerate(config.regions)}
    accepted: list[RegionModel] = list(linked)
    for r in sorted(plain, key=lambda r: (-len(r.members), order[r.id])):
        members = set(r.members)
        if any(_overlaps_partly(members, set(a.members)) for a in accepted):
            continue
        if any(members <= set(a.members) for a in accepted):
            continue
        accepted.append(r)
    outer = [
        r
        for r in accepted
        if not any(o is not r and o.source is None and set(r.members) <= set(o.members) for o in accepted)
    ]
    return sorted(outer, key=lambda r: order[r.id])


def region_anchor(config: BotConfig, region: RegionModel) -> dict:
    """Where a folded region's one node sits, in the ``StateModel.position`` convention.

    An instance's is its stored ``anchor``. A collapsed plain region stores none: its
    node sits at its members' top-left, so moving the node is moving the members.
    """
    if region.source is not None:
        return region.source.anchor or {"x": 0.0, "y": 0.0}
    members = set(region.members)
    return _anchor_of([s for s in config.states if s.id in members])


def _pos_add(a: dict | None, b: dict | None) -> dict | None:
    if a is None or b is None:
        return None
    return {"x": float(a["x"]) + float(b["x"]), "y": float(a["y"]) + float(b["y"])}


def _pos_sub(a: dict | None, b: dict | None) -> dict | None:
    if a is None or b is None:
        return None
    return {"x": float(a["x"]) - float(b["x"]), "y": float(a["y"]) - float(b["y"])}


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------


def is_port_node(state: StateModel) -> bool:
    return state.node_type in PORT_NODE_TYPES


def entry_nodes(defn: SubgraphDefinition | BotConfig) -> list[StateModel]:
    """The definition's Entry nodes, in file order: the call node's entry ports, top to bottom."""
    return [s for s in defn.states if s.node_type == SUBGRAPH_ENTRY]


def exit_nodes(defn: SubgraphDefinition | BotConfig) -> list[StateModel]:
    """The definition's Exit nodes, in file order: the call node's exit ports, top to bottom."""
    return [s for s in defn.states if s.node_type == SUBGRAPH_EXIT]


def port_name(state: StateModel) -> str:
    """A port's name is its node's label, so renaming one changes nothing structural."""
    return state.label or state.id


def ports_of(states: Sequence[StateModel], transitions: Sequence[TransitionModel]) -> dict[str, SubgraphPort]:
    """``{port id: where it crosses}`` for a definition's graph, in local ids.

    An entry's member is where its one edge goes. An exit's feeds are the
    ``(member, outcome)`` pairs of the edges into it, in file order.
    """
    ports: dict[str, SubgraphPort] = {}
    for s in states:
        if s.node_type == SUBGRAPH_ENTRY:
            out = next((t.dest for t in transitions if t.source == s.id), None)
            ports[s.id] = SubgraphPort(member=out)
        elif s.node_type == SUBGRAPH_EXIT:
            ports[s.id] = SubgraphPort(from_=[(t.source, t.when) for t in transitions if t.dest == s.id])
    return ports


def definition_ports(defn: SubgraphDefinition) -> dict[str, SubgraphPort]:
    return ports_of(defn.states, defn.transitions)


def entry_port_of(source: SubgraphSource, region_id: str, member: str) -> str | None:
    """The entry port whose member is the bot-level ``member`` of this instance, or None."""
    for pid, port in source.ports.items():
        if port.member is not None and member_id(region_id, port.member) == member:
            return pid
    return None


def exit_port_of(source: SubgraphSource, region_id: str, member: str, when: str | None) -> str | None:
    """The exit port the bot-level ``(member, when)`` feeds, or None."""
    for pid, port in source.ports.items():
        for m, w in port.from_ or ():
            if member_id(region_id, m) == member and w == when:
                return pid
    return None


# ---------------------------------------------------------------------------
# Params
# ---------------------------------------------------------------------------


def exposed_key(member: str, key: str) -> str:
    """Where an exposed node's param is valued in ``SubgraphSource.inputs``: ``<member>.<key>``."""
    return f"{member}.{key}"


def node_panels(defn: SubgraphDefinition | DefinitionMeta):
    return [p for p in defn.panels if p.kind == "node"]


@dataclass(frozen=True)
class InstanceKey:
    """One key of an instance's ``source.inputs``, resolved against the instance's definition.

    Either a named ``input``, or ``key`` of the member a node ``panel`` exposes whole
    (stored as ``<member>.<key>``). The one place such a key is told apart: a link into
    a nested instance names one, so its type, where it lands and how it is drawn all
    come through here, and a new kind of key is added here and nowhere else.
    """

    defn: SubgraphDefinition
    name: str
    input: SubgraphInput | None = None
    panel: SubgraphPanel | None = None
    key: str | None = None


def resolve_instance_key(defn: SubgraphDefinition, name: str) -> InstanceKey | None:
    """What ``name`` sets on an instance of ``defn``, or None when the definition has no such key."""
    named = next((i for i in defn.inputs if i.name == name), None)
    if named is not None:
        return InstanceKey(defn, name, input=named)
    member, dot, key = name.partition(".")
    panel = next((p for p in node_panels(defn) if p.member == member), None) if dot else None
    if panel is not None and key and any(s.id == member for s in defn.states):
        return InstanceKey(defn, name, panel=panel, key=key)
    return None


def nested_key(defn: SubgraphDefinition | BotConfig, binding, library: SubgraphLibrary | None) -> InstanceKey | None:
    """The key of a nested instance a binding names, resolved against that instance's definition.

    None for a member binding, with no library, and when the instance, its definition
    or the key is gone.
    """
    if binding.region is None or library is None:
        return None
    region = next((r for r in defn.regions if r.id == binding.region and r.source is not None), None)
    inner = library.get(region.source.subgraph_id) if region is not None else None  # type: ignore[union-attr]
    return resolve_instance_key(inner, binding.param) if inner is not None else None


def instance_key_type(ikey: InstanceKey, schemas: Mapping[str, Sequence[object]]) -> str | None:
    """The param type a value for ``ikey`` must have: the input's, or the promoted param's."""
    if ikey.input is not None:
        return ikey.input.type
    return promoted_param_type(ikey.defn, ikey.panel, ikey.key or "", schemas)


def instance_key_targets(
    ikey: InstanceKey, library: SubgraphLibrary | None, region_id: str | None = None, _depth: int = 0
) -> list[tuple[str, str]]:
    """``(state id, param key)`` of every param a value for ``ikey`` lands on. Ids as :func:`input_targets` gives them."""
    if ikey.input is not None:
        return input_targets(ikey.defn, ikey.input, library, region_id, _depth)

    def here(local: str) -> str:
        return member_id(region_id, local) if region_id is not None else local

    assert ikey.panel is not None and ikey.key is not None
    shared = ikey.panel.bindings.get(ikey.key, [])
    return [(here(ikey.panel.member or ""), ikey.key), *((here(b.state), b.param) for b in shared)]


def forwarded_inputs(defn: SubgraphDefinition, source: SubgraphSource, region_id: str) -> dict[str, object]:
    """What an instance of ``defn`` hands the nested instance ``region_id``: inner key -> value.

    A key is whatever the nested instance's own ``source.inputs`` is keyed by: a named
    input, or a promoted node's ``<member>.<key>``.

    The same order as a member's param: the instance's value, then the input's default.
    An input with neither forwards nothing, so the nested instance keeps what the
    definition stored for it, or follows its own default.
    """
    out: dict[str, object] = {}
    for inp in defn.inputs:
        value = source.inputs.get(inp.name)
        if value is None:
            value = inp.default
        if value is None:
            continue
        for b in inp.bindings:
            if b.region == region_id:
                out[b.param] = value
    return out


def input_targets(
    defn: SubgraphDefinition | BotConfig,
    inp,
    library: SubgraphLibrary | None,
    region_id: str | None = None,
    _depth: int = 0,
) -> list[tuple[str, str]]:
    """``(state id, param key)`` of every param ``inp`` lands on, through nested instances.

    Ids are those of the instance ``region_id`` in a bot, or ``defn``'s own with none.
    A link into a nested instance is followed to the members its input sets, which
    needs ``library``; without one, or past a definition that is gone, it lands nowhere.
    """

    def here(local: str) -> str:
        return member_id(region_id, local) if region_id is not None else local

    out: list[tuple[str, str]] = []
    for b in inp.bindings:
        if b.region is None:
            out.append((here(b.state), b.param))
        elif _depth < MAX_DEPTH and (found := nested_key(defn, b, library)) is not None:
            out.extend(instance_key_targets(found, library, here(b.region), _depth + 1))
    return out


def resolve_member_params(defn: SubgraphDefinition, def_state: StateModel, source: SubgraphSource) -> dict:
    """``instance value (if set) > input default > definition value`` for one member.

    An input -- or a promoted node's param -- whose instance value is ``None`` is unset,
    and follows the definition: the input's default, which the definition document keeps
    equal to every param linked to it (:func:`sync_linked_values`), or for a promoted
    node the node's own value. A stored empty value is a choice and applies. A promoted
    param's value also fans out to the other members linked to it.
    """
    params = dict(def_state.params)
    for inp in defn.inputs:
        value = source.inputs.get(inp.name)
        if value is None:
            value = inp.default
        if value is None:
            continue
        for b in inp.bindings:
            if b.state == def_state.id:
                params[b.param] = value
    for panel in node_panels(defn):
        member = panel.member or ""
        prefix = exposed_key(member, "")
        if member == def_state.id:
            for key, value in source.inputs.items():
                if key.startswith(prefix) and value is not None:
                    params[key[len(prefix) :]] = value
        for key, extra in panel.bindings.items():
            value = source.inputs.get(exposed_key(member, key))
            if value is None:
                continue
            for b in extra:
                if b.state == def_state.id:
                    params[b.param] = value
    return params


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


@dataclass
class Materialized:
    """What one instance composes to, in bot-level ids. Regions are the *nested* linked ones only.

    ``ports`` is the definition's port map, in the definition's local ids -- what the
    instance's ``source.ports`` becomes once these members are in the bot.
    """

    states: list[StateModel]
    transitions: list[TransitionModel]
    regions: list[RegionModel]
    ports: dict[str, SubgraphPort] = field(default_factory=dict)
    #: A nested instance held on its stored snapshot, at any level.
    warnings: list[str] = field(default_factory=list)

    @property
    def member_ids(self) -> list[str]:
        return [s.id for s in self.states]


@dataclass
class _Graph:
    """The parts of a bot -- or of a definition -- that composition rewrites."""

    states: list[StateModel]
    transitions: list[TransitionModel]
    regions: list[RegionModel]
    initial: str | None = None
    recovery_state: str | None = None


def _materialize_local(
    defn: SubgraphDefinition, source: SubgraphSource, library: SubgraphLibrary, stack: tuple[str, ...]
) -> Materialized:
    """Compose ``defn`` into definition-local ids, nested instances recomposed from the library.

    A nested instance is stored in the definition as a snapshot, the way a bot stores
    one, and recomposed here by the same rules a bot's is, with the inputs this
    instance forwards to it (:func:`forwarded_inputs`); held, it keeps the
    definition's snapshot and says so. Port nodes and the edges touching them are
    dropped once the port map has been read off the result. Plain regions are the
    definition's own layout and are not composed.
    """
    if defn.id in stack or len(stack) >= MAX_DEPTH:
        raise SubgraphCycleError([*stack, defn.id])
    stack = (*stack, defn.id)

    graph = _Graph(
        states=[s.model_copy(deep=True) for s in defn.states],
        transitions=[t.model_copy(deep=True) for t in defn.transitions],
        regions=[r.model_copy(deep=True) for r in defn.regions if r.source is not None],
    )
    warnings: list[str] = []
    for inner in outermost_linked_regions(graph.regions):
        # An outer input linked to one of the inner's goes in through the inner signature.
        inner.source.inputs.update(forwarded_inputs(defn, source, inner.id))  # type: ignore[union-attr]
        status = _recompose(graph, inner, library, stack, nested=True)
        if status.kind in ("missing", "invalid"):
            raise LookupError(f"nested subgraph '{inner.source.subgraph_id}' is missing")  # type: ignore[union-attr]
        if status.kind == "held":
            warnings.extend(f"nested '{inner.label}': {r}" for r in status.reasons)

    ports = ports_of(graph.states, graph.transitions)
    port_ids = {s.id for s in graph.states if is_port_node(s)}
    states = [s for s in graph.states if s.id not in port_ids]
    transitions = [t for t in graph.transitions if t.source not in port_ids and t.dest not in port_ids]
    for s in states:
        s.params = resolve_member_params(defn, s, source)
    return Materialized(states, transitions, graph.regions, ports, warnings)


def _prefix(mat: Materialized, region_id: str, anchor: dict | None) -> Materialized:
    """Rewrite every local id in ``mat`` to :func:`member_id` and translate positions by ``anchor``."""
    ids = {s.id: member_id(region_id, s.id) for s in mat.states}
    states = [s.model_copy(update={"id": ids[s.id], "position": _pos_add(s.position, anchor)}) for s in mat.states]
    transitions = [t.model_copy(update={"source": ids[t.source], "dest": ids[t.dest]}) for t in mat.transitions]
    regions = []
    for r in mat.regions:
        assert r.source is not None
        regions.append(
            r.model_copy(
                update={
                    "id": member_id(region_id, r.id),
                    "members": [ids[m] for m in r.members if m in ids],
                    # A nested anchor is relative to its definition's origin; once
                    # prefixed it is in the enclosing frame, and in the bot absolute.
                    "source": r.source.model_copy(update={"anchor": _pos_add(r.source.anchor, anchor)}),
                }
            )
        )
    return Materialized(states, transitions, regions, dict(mat.ports), list(mat.warnings))


def materialize_region(
    region_id: str,
    source: SubgraphSource,
    defn: SubgraphDefinition,
    library: SubgraphLibrary,
    stack: tuple[str, ...] = (),
) -> Materialized:
    """Compose the instance ``region_id`` of ``defn`` into bot-level states, transitions and nested regions.

    Members land at ``anchor + definition position``. Raises
    :class:`SubgraphCycleError` on a cycle or past :data:`MAX_DEPTH`, and
    ``LookupError`` when a nested definition is missing from the library.
    """
    return _prefix(_materialize_local(defn, source, library, stack), region_id, source.anchor)


# ---------------------------------------------------------------------------
# Composing a whole bot
# ---------------------------------------------------------------------------


@dataclass
class RegionStatus:
    origin: Origin
    kind: RegionKind
    reasons: list[str] = field(default_factory=list)
    library_version: int | None = None


@dataclass
class ComposeReport:
    """One entry per *linked* region; empty means the bot has none."""

    regions: dict[str, RegionStatus] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return any(s.kind == "cycle" for s in self.regions.values())


def external_edges(config: BotConfig | _Graph, region: RegionModel) -> list[TransitionModel]:
    """Transitions with exactly one endpoint inside ``region``."""
    members = set(region.members)
    return [t for t in config.transitions if (t.source in members) != (t.dest in members)]


def _recompose(
    graph: _Graph, region: RegionModel, library: SubgraphLibrary, stack: tuple[str, ...], *, nested: bool = False
) -> RegionStatus:
    """Recompose one outermost linked region of ``graph`` in place, and say how it went.

    Every crossing into the region is found on the instance's stored port map and
    moved with its port: an edge or role on an entry follows the entry to its new
    member, and the flat edges an exit stands for are regenerated from its new
    ``(member, outcome)`` feeds. A crossing on no port keeps its member if the update
    still has it. The update is *held* -- the snapshot kept -- only when it removes a
    port this instance uses, leaves a used exit with nothing feeding it, or removes
    the member an off-port crossing names.
    """
    source = region.source
    assert source is not None
    origin = library.origin(source.subgraph_id)
    defn = library.get(source.subgraph_id)
    if defn is None:
        error = library.error_for(source.subgraph_id)
        if error is not None:
            return RegionStatus(origin, "invalid", [error])
        return RegionStatus(origin, "missing", ["subgraph definition not found"])
    try:
        mat = materialize_region(region.id, source, defn, library, stack)
    except SubgraphCycleError as exc:
        if nested:
            raise  # a cycle has no snapshot worth keeping at any level: the outermost reports it
        return RegionStatus(origin, "cycle", [str(exc)], defn.version)
    except LookupError as exc:
        return RegionStatus(origin, "missing", [str(exc)], defn.version)

    rid = region.id
    members = set(region.members)
    new_members = set(mat.member_ids)
    new_ports = mat.ports
    reasons: list[str] = []

    def retarget(member: str, what: str) -> str | None:
        pid = entry_port_of(source, rid, member)
        if pid is not None:
            port = new_ports.get(pid)
            if port is None or port.member is None:
                reasons.append(f"the update removes entry '{pid}', which {what} uses")
                return None
            return member_id(rid, port.member)
        if member in new_members:
            return member
        reasons.append(f"the update removes '{member}', which {what} leads into")
        return None

    new_initial = graph.initial
    new_recovery = graph.recovery_state
    if graph.initial in members:
        new_initial = retarget(graph.initial, "the initial state")
    if graph.recovery_state in members:
        new_recovery = retarget(graph.recovery_state, "the recovery state")

    incoming: dict[int, str | None] = {}
    exit_groups: dict[tuple[str, str], list[TransitionModel]] = {}
    for i, t in enumerate(graph.transitions):
        inside_src, inside_dst = t.source in members, t.dest in members
        if inside_dst and not inside_src:
            incoming[i] = retarget(t.dest, "an edge from the rest of the bot")
        elif inside_src and not inside_dst:
            pid = exit_port_of(source, rid, t.source, t.when)
            if pid is None:
                if t.source not in new_members:
                    reasons.append(f"the update removes '{t.source}', which has an edge to the rest of the bot")
                continue
            exit_groups.setdefault((pid, t.dest), []).append(t)
    for pid, _dest in exit_groups:
        port = new_ports.get(pid)
        if port is None or port.from_ is None:
            reasons.append(f"the update removes exit '{pid}', which has an edge to the rest of the bot")
        elif not port.from_:
            reasons.append(f"the update leaves exit '{pid}' with nothing leading into it")
    if reasons:
        return RegionStatus(origin, "held", sorted(set(reasons)), defn.version)

    # Each exit's edges are regenerated where the first of them stood, so an update
    # that changes nothing leaves the file's order -- and so the file -- as it was.
    regenerated: dict[int, list[TransitionModel]] = {}
    grouped: set[int] = set()
    for (pid, dest), group in exit_groups.items():
        port = new_ports[pid]
        seen: set[tuple[str, str | None]] = set()
        out: list[TransitionModel] = []
        for m, w in port.from_ or ():
            key = (member_id(rid, m), w)
            if key in seen:
                continue
            seen.add(key)
            out.append(group[0].model_copy(update={"source": key[0], "when": w, "dest": dest}, deep=True))
        regenerated[id(group[0])] = out
        grouped.update(id(t) for t in group)
    kept: list[TransitionModel] = []
    for i, t in enumerate(graph.transitions):
        if id(t) in regenerated:
            kept.extend(regenerated[id(t)])
        elif id(t) in grouped:
            continue
        elif i in incoming:
            kept.append(t.model_copy(update={"dest": incoming[i]}))
        else:
            kept.append(t)
    graph.transitions = kept
    graph.initial, graph.recovery_state = new_initial, new_recovery
    _replace_members(graph, region, mat)
    source.ports = {pid: p.model_copy(deep=True) for pid, p in new_ports.items()}
    source.version = defn.version
    return RegionStatus(origin, "linked", list(mat.warnings), defn.version)


def _replace_members(graph: _Graph, region: RegionModel, mat: Materialized) -> None:
    old = set(region.members)
    nested = [r for r in graph.regions if r.source is not None and is_nested_in(r, region)]

    first_index = next((i for i, s in enumerate(graph.states) if s.id in old), len(graph.states))
    kept_states = [s for s in graph.states if s.id not in old]
    insert_at = sum(1 for s in graph.states[:first_index] if s.id not in old)
    graph.states = kept_states[:insert_at] + mat.states + kept_states[insert_at:]

    first_t = next(
        (i for i, t in enumerate(graph.transitions) if t.source in old and t.dest in old), len(graph.transitions)
    )
    kept_t = [t for t in graph.transitions if not (t.source in old and t.dest in old)]
    insert_t = sum(1 for t in graph.transitions[:first_t] if not (t.source in old and t.dest in old))
    graph.transitions = kept_t[:insert_t] + mat.transitions + kept_t[insert_t:]

    kept_r = [r for r in graph.regions if not any(r is n for n in nested)]
    at = next(i for i, r in enumerate(kept_r) if r is region) + 1
    graph.regions = kept_r[:at] + mat.regions + kept_r[at:]
    region.members = list(mat.member_ids)
    # A plain region holding the old members follows the instance to its new ones.
    for r in graph.regions:
        if r is region or r.source is not None or not (old & set(r.members)):
            continue
        r.members = [m for m in r.members if m not in old] + [m for m in mat.member_ids if m not in r.members]


def compose_bot(config: BotConfig, library: SubgraphLibrary) -> tuple[BotConfig, ComposeReport]:
    """Recompose every linked region of ``config`` from ``library``.

    Returns **the same object** and an empty report when the bot has no linked
    region — the common case, and what keeps every existing caller's identity
    checks true. Otherwise works on a deep copy and never mutates the input.

    Per outermost linked region, in file order, the outcome is one :class:`RegionStatus`:

    - ``missing``: no definition in the library; the snapshot is kept.
    - ``invalid``: the definition's file exists but failed to load; snapshot kept.
    - ``cycle``: the definition contains itself; snapshot kept, and the report's
      :attr:`ComposeReport.has_errors` is set — the caller refuses to run.
    - ``held``: the update removes a port this instance uses (see :func:`_recompose`);
      snapshot kept, one reason per offender.
    - ``linked``: members, internal transitions and nested regions replaced from
      the definition, crossings moved with their ports, ``source`` updated. A nested
      instance held inside the definition is listed in ``reasons``.
    """
    if not linked_regions(config):
        return config, ComposeReport()

    result = config.model_copy(deep=True)
    graph = _Graph(result.states, result.transitions, result.regions, result.initial, result.recovery_state)
    report = ComposeReport()
    for region in outermost_linked_regions(graph.regions):
        report.regions[region.id] = _recompose(graph, region, library, ())
    result.states, result.transitions, result.regions = graph.states, graph.transitions, graph.regions
    result.initial = graph.initial or result.initial
    result.recovery_state = graph.recovery_state or result.recovery_state
    # A failure here is a bug in this module, not a library problem: raise.
    return BotConfig.model_validate(result.model_dump()), report


def sync_linked_values(config: BotConfig, *, edited_state: str | None = None) -> None:
    """Keep every linked param equal to what it is linked to, in a definition document. In place.

    A linked param *is* its input's default: edit either and the other follows, and so
    does every other param linked to the same input. A param linked to a promoted node's
    param follows that param the same way. ``edited_state`` names the node whose params
    were just edited -- or the nested instance whose inputs were -- and they win;
    otherwise (a default edited on the signature page, a new link) the defaults win.
    An input with no default takes the first linked value.

    A nested instance's linked input is kept the same way, in its ``source.inputs``:
    unset there while the default is None, so the instance follows its own definition.
    A link to an instance that is no longer one (deleted, or detached) is dropped
    first, and with it an input that linked to nothing else, as on a node's delete.
    """
    meta = config.definition
    if meta is None:
        return
    by_id = {s.id: s for s in config.states}
    instances = {r.id: r for r in config.regions if r.source is not None}

    emptied = set()
    for inp in meta.inputs:
        kept = [b for b in inp.bindings if b.region is None or b.region in instances]
        if inp.bindings and not kept:
            emptied.add(inp.name)
        inp.bindings = kept
    if emptied:
        meta.inputs = [i for i in meta.inputs if i.name not in emptied]

    def value_of(state_id: str, key: str):
        state = by_id.get(state_id)
        return state.params.get(key) if state is not None else None

    def bound_value(b):
        if b.region is not None:
            return instances[b.region].source.inputs.get(b.param)  # type: ignore[union-attr]
        return value_of(b.state, b.param)

    def spread(targets, value) -> None:
        for b in targets:
            if b.region is not None:
                inputs = instances[b.region].source.inputs  # type: ignore[union-attr]
                if value is None:
                    inputs.pop(b.param, None)
                elif inputs.get(b.param) != value:
                    inputs[b.param] = value
                continue
            state = by_id.get(b.state)
            if state is not None and state.params.get(b.param) != value:
                state.params[b.param] = value

    for inp in meta.inputs:
        if not inp.bindings:
            continue
        edited = [b for b in inp.bindings if edited_state is not None and edited_state in (b.state, b.region)]
        if edited:
            inp.default = bound_value(edited[0])
        elif inp.default is None:
            inp.default = bound_value(inp.bindings[0])
        spread(inp.bindings, inp.default)
    for panel in node_panels(meta):
        member = panel.member or ""
        for key, extra in panel.bindings.items():
            edited = [b for b in extra if b.state == edited_state]
            if edited and edited_state != member:
                value = value_of(edited[0].state, edited[0].param)
                state = by_id.get(member)
                if state is not None:
                    state.params[key] = value
            else:
                value = value_of(member, key)
            spread(extra, value)


def reresolve_member_params(
    config: BotConfig,
    region: RegionModel,
    defn: SubgraphDefinition,
    library: SubgraphLibrary | None = None,
    _depth: int = 0,
) -> None:
    """Rewrite every direct member's params from ``defn`` and the instance's inputs. In place.

    What an input edit on the call node's page needs, without a full recompose: the
    members stay, only their params move. A member with no state of that local id in
    ``defn`` (one from a nested instance, or one the definition no longer has) is
    left alone. With ``library``, an input linked into a nested instance is forwarded
    to it and that instance's members re-resolved in turn, as a recompose would.
    """
    source = region.source
    if source is None:
        return
    def_states = {s.id: s for s in defn.states}
    for state in config.states:
        if state.id not in region.members:
            continue
        local = local_id(region.id, state.id)
        def_state = def_states.get(local) if local is not None else None
        if def_state is not None:
            state.params = resolve_member_params(defn, def_state, source)
    if library is None or _depth >= MAX_DEPTH:
        return
    by_id = {r.id: r for r in config.regions if r.source is not None}
    for inner_id in dict.fromkeys(b.region for inp in defn.inputs for b in inp.bindings if b.region is not None):
        stored = next((r for r in defn.regions if r.id == inner_id and r.source is not None), None)
        inner = by_id.get(member_id(region.id, inner_id))
        if stored is None or inner is None:
            continue
        inner_defn = library.get(inner.source.subgraph_id)  # type: ignore[union-attr]
        if inner_defn is None:
            continue
        # From what the definition stored, not what the bot holds: a value this instance
        # stops forwarding has to fall back, not linger.
        inner.source.inputs = {**stored.source.inputs, **forwarded_inputs(defn, source, inner_id)}  # type: ignore[union-attr]
        reresolve_member_params(config, inner, inner_defn, library, _depth + 1)


def reresolve_linked_instances(config: BotConfig, library: SubgraphLibrary | None) -> None:
    """In a definition document, re-resolve each nested instance one of its inputs links into. In place.

    :func:`sync_linked_values` writes an input's default into the nested instance's
    inputs; this moves the instance's members to match, as editing its own page would.
    """
    meta = config.definition
    if meta is None or library is None:
        return
    linked = {b.region for inp in meta.inputs for b in inp.bindings if b.region is not None}
    for region in config.regions:
        if region.id in linked and region.source is not None:
            defn = library.get(region.source.subgraph_id)
            if defn is not None:
                reresolve_member_params(config, region, defn, library)


# ---------------------------------------------------------------------------
# Extracting, converting, detaching
# ---------------------------------------------------------------------------


def _region_by_id(config: BotConfig, region_id: str) -> RegionModel:
    for r in config.regions:
        if r.id == region_id:
            return r
    raise KeyError(f"no region '{region_id}'")


def _anchor_of(states: list[StateModel]) -> dict:
    positioned = [s.position for s in states if s.position is not None]
    if not positioned:
        return {"x": 0.0, "y": 0.0}
    return {"x": min(float(p["x"]) for p in positioned), "y": min(float(p["y"]) for p in positioned)}


def extraction_locals(config: BotConfig, region_id: str) -> dict[str, str]:
    """``{member id: local id}`` an extraction of ``region_id`` assigns: ``state_1..N`` in file order.

    A definition starts life with clean local ids rather than whatever numbers the
    bot had reached, so the same subgraph reads the same in every bot it lands in.
    A member of a nested instance keeps its id *inside* that instance, relative to
    the instance (``<inner region>_<inner local>``), since the inner definition owns it.
    Shared by :func:`extract_definition` and :func:`convert_region_to_instance` so
    the two cannot disagree.
    """
    region = _region_by_id(config, region_id)
    members = set(region.members)
    nested = [
        r for r in nested_regions(config, region) if not any(is_nested_in(r, o) for o in nested_regions(config, region))
    ]
    nested_of = {m: r for r in nested for m in r.members}
    locals_: dict[str, str] = {}
    n = 0
    for s in config.states:
        if s.id not in members:
            continue
        inner = nested_of.get(s.id)
        if inner is not None:
            locals_[s.id] = s.id
            continue
        n += 1
        locals_[s.id] = f"state_{n}"
    return locals_


def _unique_port_id(base: str, taken: set[str]) -> str:
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}_{n}"
        n += 1
    taken.add(candidate)
    return candidate


@dataclass(frozen=True)
class BoundaryEntry:
    """An inferred entry port: where edges or roles from outside a set of states come in."""

    port_id: str
    name: str
    member: str


@dataclass(frozen=True)
class BoundaryExit:
    """An inferred exit port: one outside destination, fed by the ``(member, outcome)`` pairs reaching it."""

    port_id: str
    name: str
    dest: str
    feeds: tuple[tuple[str, str | None], ...]


def boundary_ports(
    config: BotConfig, members: Iterable[str], *, taken: set[str] | None = None
) -> tuple[list[BoundaryEntry], list[BoundaryExit]]:
    """The ports the edges crossing ``members``' boundary imply, in bot ids.

    - an **entry** per distinct member that an outside edge or a role targets --
      named *Start* for the initial state's, *Recover* for the recovery state's,
      the member's label otherwise -- or one *Start* into the first member when
      nothing outside reaches in;
    - an **exit** per distinct outside destination, fed by the ``(member, outcome)``
      pairs that reach it -- *Done* when there is only one, *To <label>* otherwise.

    One rule for both of its callers: :func:`extract_definition` turns them into Entry
    and Exit nodes, and a collapsed plain region (#67) is drawn with them, so a
    collapsed region looks exactly like the subgraph node it would become. Port ids
    are kept clear of ``taken``, which is updated.
    """
    member_set = set(members)
    taken = set() if taken is None else taken
    labels = {s.id: s.label for s in config.states}

    targets: list[tuple[str, str]] = []  # (member, entry name), first reason wins
    if config.initial in member_set:
        targets.append((config.initial, "Start"))
    if config.recovery_state in member_set:
        targets.append((config.recovery_state, "Recover"))
    for t in config.transitions:
        if t.dest in member_set and t.source not in member_set:
            targets.append((t.dest, labels.get(t.dest) or "Entry"))
    if not targets:
        first = next((s.id for s in config.states if s.id in member_set), None)
        if first is not None:
            targets.append((first, "Start"))
    entries: list[BoundaryEntry] = []
    seen_members: set[str] = set()
    for member, entry_name in targets:
        if member in seen_members:
            continue
        seen_members.add(member)
        pid = _unique_port_id(f"entry_{instance_slug(entry_name)}", taken)
        entries.append(BoundaryEntry(pid, entry_name, member))

    outgoing = [t for t in config.transitions if t.source in member_set and t.dest not in member_set]
    dests = list(dict.fromkeys(t.dest for t in outgoing))
    exits: list[BoundaryExit] = []
    for dest in dests:
        exit_name = "Done" if len(dests) == 1 else f"To {labels.get(dest) or dest}"
        pid = _unique_port_id(f"exit_{instance_slug(exit_name)}", taken)
        feeds = tuple(dict.fromkeys((t.source, t.when) for t in outgoing if t.dest == dest))
        exits.append(BoundaryExit(pid, exit_name, dest, feeds))
    return entries, exits


def extract_definition(
    config: BotConfig,
    region_id: str,
    *,
    new_id: str,
    name: str,
    description: str | None = None,
) -> tuple[SubgraphDefinition, SubgraphSource]:
    """Turn an ordinary region into a definition, and return the source that would link it.

    Local ids are renumbered ``state_1..N`` in file order (:func:`extraction_locals`);
    the conversion that follows (:func:`convert_region_to_instance`) applies the same
    map to the bot. The anchor is the members' top-left; definition positions are
    relative to it. Only internal transitions are carried, plus the ports:

    - an **Entry** per distinct member that an outside edge or a role targets --
      named *Start* for the initial state's, *Recover* for the recovery state's --
      or one into the first member when nothing outside reaches in;
    - an **Exit** per distinct outside destination, fed by the ``(member, outcome)``
      pairs that reach it -- *Done* when there is only one.

    A linked region wholly inside the members becomes a nested instance, its members
    kept as the definition's snapshot of it. No inputs — those are the author's to add.
    Raises ``ValueError`` if a member id cannot be a local id.
    """
    region = _region_by_id(config, region_id)
    members = set(region.members)
    states = [s for s in config.states if s.id in members]
    locals_ = extraction_locals(config, region_id)
    for s in states:
        if not SUBGRAPH_LOCAL_ID.match(locals_[s.id]):
            raise ValueError(f"state id {s.id!r} cannot be a subgraph-local id")
    anchor = _anchor_of(states)
    by_id = {s.id: s for s in states}

    nested: list[RegionModel] = []
    for r in nested_regions(config, region):
        assert r.source is not None
        src = r.source.model_copy(deep=True)
        src.anchor = _pos_sub(src.anchor, anchor) or {"x": 0.0, "y": 0.0}
        nested.append(
            RegionModel(id=r.id, label=r.label, members=[locals_[m] for m in r.members], color=r.color, source=src)
        )

    local_states = [
        s.model_copy(update={"id": locals_[s.id], "position": _pos_sub(s.position, anchor)}, deep=True) for s in states
    ]
    transitions = [
        t.model_copy(update={"source": locals_[t.source], "dest": locals_[t.dest]}, deep=True)
        for t in config.transitions
        if t.source in members and t.dest in members
    ]

    taken: set[str] = set(locals_.values())
    port_states: list[StateModel] = []
    port_edges: list[TransitionModel] = []

    def place(member: str, dx: float) -> dict | None:
        pos = by_id[member].position
        return _pos_sub({"x": float(pos["x"]) + dx, "y": float(pos["y"])}, anchor) if pos is not None else None

    entries, exits = boundary_ports(config, region.members, taken=taken)
    for entry in entries:
        port_states.append(
            StateModel(
                id=entry.port_id, node_type=SUBGRAPH_ENTRY, label=entry.name, position=place(entry.member, -PORT_COLUMN)
            )
        )
        port_edges.append(TransitionModel(trigger="advance", source=entry.port_id, dest=locals_[entry.member]))
    for ex in exits:
        rightmost = max((m for m, _ in ex.feeds), key=lambda m: float((by_id[m].position or {"x": 0})["x"]))
        port_states.append(
            StateModel(id=ex.port_id, node_type=SUBGRAPH_EXIT, label=ex.name, position=place(rightmost, PORT_COLUMN))
        )
        for m, when in ex.feeds:
            port_edges.append(TransitionModel(trigger="advance", source=locals_[m], dest=ex.port_id, when=when))

    all_states = local_states + port_states
    all_transitions = transitions + port_edges
    defn = SubgraphDefinition(
        id=new_id,
        name=name,
        description=description,
        version=1,
        states=all_states,
        transitions=all_transitions,
        regions=nested,
    )
    source = SubgraphSource(subgraph_id=new_id, version=1, ports=definition_ports(defn), anchor=anchor)
    return defn, source


def convert_region_to_instance(
    config: BotConfig,
    region_id: str,
    defn: SubgraphDefinition,
    source: SubgraphSource,
    *,
    instance_id: str | None = None,
) -> dict[str, str]:
    """Rewrite the region's member ids to the derived form and attach ``source``. In place.

    The region takes ``instance_id`` (mint one with :func:`mint_instance_id`; the
    default keeps its current id). Every place a member id appears is rewritten:
    states, transitions, ``initial``, ``recovery_state`` and every region's
    ``members``. A linked region nested inside gets its id prefixed too. Returns
    ``{old id: new id}`` so the caller can re-key what lives outside the config
    (combat assignments).
    """
    region = _region_by_id(config, region_id)
    new_rid = instance_id or region_id
    nested = nested_regions(config, region)
    id_map = {m: member_id(new_rid, loc) for m, loc in extraction_locals(config, region_id).items()}
    rename_states(config, id_map)
    for r in nested:
        r.id = member_id(new_rid, r.id)
    region.id = new_rid
    region.source = source.model_copy(deep=True)
    region.collapsed = None  # an instance is drawn as its call node regardless
    return id_map


def rename_states(config: BotConfig, id_map: dict[str, str]) -> None:
    """Rewrite state ids everywhere they appear: states, transitions, roles, region members. In place."""
    for s in config.states:
        if s.id in id_map:
            s.id = id_map[s.id]
    for t in config.transitions:
        t.source = id_map.get(t.source, t.source)
        t.dest = id_map.get(t.dest, t.dest)
    config.initial = id_map.get(config.initial, config.initial)
    config.recovery_state = id_map.get(config.recovery_state, config.recovery_state)
    for r in config.regions:
        r.members = [id_map.get(m, m) for m in r.members]


def contains_subgraph(defn: SubgraphDefinition, subgraph_id: str, library: SubgraphLibrary) -> bool:
    """Whether ``defn`` nests ``subgraph_id``, transitively through the library.

    The check a save makes before writing: a definition that contains its own id
    would compose forever, and the library must never hold one.
    """
    seen: set[str] = set()
    stack = [defn]
    while stack:
        d = stack.pop()
        for r in d.regions:
            if r.source is None:
                continue
            inner_id = r.source.subgraph_id
            if inner_id == subgraph_id:
                return True
            if inner_id in seen:
                continue
            seen.add(inner_id)
            inner = library.get(inner_id)
            if inner is not None:
                stack.append(inner)
    return False


def reassign_orphaned_roles(config: BotConfig) -> None:
    """Move ``initial`` / ``recovery_state`` onto the first state when their holder is gone.

    With no states left there is nothing to hold either, and both are cleared: a blank
    bot is the one config that loads with empty roles.
    """
    if not config.states:
        config.initial = config.recovery_state = ""
        return
    ids = {s.id for s in config.states}
    fallback = config.states[0].id
    if config.initial not in ids:
        config.initial = fallback
    if config.recovery_state not in ids:
        config.recovery_state = fallback


def drop_states(config: BotConfig, state_ids: set[str]) -> None:
    """Remove states, their edges and their region memberships. In place; roles are reassigned."""
    config.states = [s for s in config.states if s.id not in state_ids]
    config.transitions = [t for t in config.transitions if t.source not in state_ids and t.dest not in state_ids]
    for r in config.regions:
        r.members = [m for m in r.members if m not in state_ids]
    reassign_orphaned_roles(config)


def detach(config: BotConfig, region_id: str) -> None:
    """Drop the link. Members and their ids stay exactly as they are, as ordinary states. In place.

    Nested instances inside it stay linked: they are whole instances of their own.
    """
    _region_by_id(config, region_id).source = None


# ---------------------------------------------------------------------------
# A definition as a document
# ---------------------------------------------------------------------------


def definition_to_config(defn: SubgraphDefinition) -> BotConfig:
    """The in-memory ``BotConfig`` a definition is edited as.

    The canvas, scene and undo read almost nothing but states, transitions, regions
    and the two roles, so a definition opens as a config whose roles both sit on the
    first Entry -- the validator is satisfied, and "reachable" means reachable from an
    entry. ``definition`` carries the rest of it (identity and signature) through
    undo and autosave; :func:`config_to_definition` is the way back.
    """
    first = entry_nodes(defn)
    role = first[0].id if first else defn.states[0].id
    return BotConfig(
        id=defn.id,
        name=defn.name,
        description=defn.description,
        initial=role,
        recovery_state=role,
        states=[s.model_copy(deep=True) for s in defn.states],
        transitions=[t.model_copy(deep=True) for t in defn.transitions],
        regions=[r.model_copy(deep=True) for r in defn.regions],
        definition=DefinitionMeta(
            id=defn.id,
            version=defn.version,
            inputs=[i.model_copy(deep=True) for i in defn.inputs],
            panels=[p.model_copy(deep=True) for p in defn.panels],
            tags=list(defn.tags),
        ),
    )


def config_to_definition(config: BotConfig, *, version: int | None = None) -> SubgraphDefinition:
    """The definition a document holds. Raises ``ValueError`` when its structure is not a valid one.

    ``version`` defaults to the document's own; a save passes the next one.
    """
    meta = config.definition
    if meta is None:
        raise ValueError("this document is a bot, not a subgraph definition")
    return SubgraphDefinition(
        id=meta.id,
        name=config.name,
        description=config.description,
        version=version if version is not None else meta.version,
        tags=list(meta.tags),
        inputs=[i.model_copy(deep=True) for i in meta.inputs],
        panels=[p.model_copy(deep=True) for p in meta.panels],
        states=[s.model_copy(deep=True) for s in config.states],
        transitions=[t.model_copy(deep=True) for t in config.transitions],
        regions=[r.model_copy(deep=True) for r in config.regions],
    )


DefinitionChange = Literal["none", "layout", "meta", "content"]


#: What :func:`definition_change` ignores: the version it decides, and the history a save records (#169).
_NOT_COMPARED = {"version", *HISTORY_FIELDS}


def definition_change(old: SubgraphDefinition, new: SubgraphDefinition) -> DefinitionChange:
    """How ``new`` differs from ``old``, version aside: not at all, in layout only, in name, or in content.

    Layout is what only the definition document draws: where each node and nested
    instance sits, and the plain regions, which are never composed. A change there
    reaches no bot's run, so a save that makes only one keeps the version (#158).
    Neither does a rename (#160): ``"meta"`` is a changed name, description or
    category (#172), with or without a layout change beside it, and keeps the version
    too. A bot links the id, and an instance's label is the bot's own.
    """
    if old.model_dump(mode="json", exclude=_NOT_COMPARED) == new.model_dump(mode="json", exclude=_NOT_COMPARED):
        return "none"
    old_runs, new_runs = _without_layout(old), _without_layout(new)
    if old_runs == new_runs:
        return "layout"
    return "meta" if _without_meta(old_runs) == _without_meta(new_runs) else "content"


def _without_meta(data: dict) -> dict:
    return {k: v for k, v in data.items() if k not in ("name", "description", "tags")}


def _without_layout(defn: SubgraphDefinition) -> dict:
    data = defn.model_dump(mode="json", exclude=_NOT_COMPARED)
    for state in data["states"]:
        state.pop("position", None)
    linked = []
    for region in data["regions"]:
        if region.get("source") is None:
            continue
        region.pop("collapsed", None)
        region["source"].pop("anchor", None)
        linked.append(region)
    data["regions"] = linked
    return data


def definition_problems(
    config: BotConfig,
    schemas: Mapping[str, Sequence[object]],
    library: SubgraphLibrary | None = None,
) -> tuple[list[str], list[str]]:
    """``(errors, warnings)`` for a definition document; an error blocks saving it.

    Errors: the structural rules (:func:`~bartlebot.bot.schema.definition_structure_errors`),
    a binding whose type is incompatible with the others it shares a value with, an
    input linked to nothing, and a definition that would contain itself. The unlinked
    input is checked here rather than with the structural rules so that a file holding
    one still loads: unlinking an input's last param is an ordinary step while editing,
    and the signature page is where the input is relinked or deleted. It still blocks
    saving, because a subgraph node would offer the input and setting it would do
    nothing. An Exit nothing leads into is the node's
    own error, found with the rest of node validation, as an Entry's missing edge is.
    ``schemas`` maps node type to its ``ParamField``s (the registry's
    ``NODE_PARAM_SCHEMAS``; passed in so this module stays registry-free). A required
    param left empty is the node's own error, checked with the rest of node validation.
    """
    from bartlebot.nodes.param_schema import narrowest_type  # noqa: PLC0415 - registry-free at import

    meta = config.definition
    if meta is None:
        return [], []
    errors = definition_structure_errors(config.states, config.transitions, config.regions, meta.inputs, meta.panels)
    by_id = {s.id: s for s in config.states}

    def field_type(state_id: str, key: str) -> str | None:
        state = by_id.get(state_id)
        if state is None:
            return None
        return next(
            (getattr(f, "type", None) for f in schemas.get(state.node_type, []) if getattr(f, "key", None) == key), None
        )

    warnings: list[str] = []
    instances = {r.id: r for r in config.regions if r.source is not None}
    for inp in meta.inputs:
        if not inp.bindings:
            errors.append(f"input '{inp.label or inp.name}' is linked to nothing; link a param to it or delete it")
        types = [t for b in inp.bindings if b.region is None and (t := field_type(b.state, b.param)) is not None]
        for b in inp.bindings:
            if b.region is None or library is None:
                continue
            found = nested_key(config, b, library)
            if found is not None:
                if (t := instance_key_type(found, schemas)) is not None:
                    types.append(t)
            elif b.region in instances and library.get(instances[b.region].source.subgraph_id) is not None:  # type: ignore[union-attr]
                warnings.append(
                    f"input '{inp.label or inp.name}' is linked to '{b.param}' of "
                    f"'{instances[b.region].label}', which no longer has that input"
                )
        if types and narrowest_type(types) is None:
            errors.append(f"input '{inp.label or inp.name}' binds params of incompatible types ({', '.join(types)})")
    for panel in node_panels(meta):
        for key, extra in panel.bindings.items():
            own = field_type(panel.member or "", key)
            types = [t for t in [own, *(field_type(b.state, b.param) for b in extra)] if t is not None]
            if types and narrowest_type(types) is None:
                errors.append(f"the exposed '{key}' of '{panel.member}' is shared with a param of an incompatible type")

    if library is not None:
        try:
            defn = config_to_definition(config)
        except ValueError:
            defn = None
        if defn is not None and contains_subgraph(defn, defn.id, library):
            errors.append(f"'{config.name}' would contain itself, which can never finish composing")

    return errors, warnings


def binding_type(
    defn: SubgraphDefinition | BotConfig,
    bindings,
    schemas: Mapping[str, Sequence[object]],
    library: SubgraphLibrary | None = None,
) -> str | None:
    """The narrowest of the bound params' types -- the type an input over ``bindings`` must have.

    A link into a nested instance counts as that instance's input's type, which takes
    ``library`` to look up; without one it is left out.
    """
    from bartlebot.nodes.param_schema import narrowest_type  # noqa: PLC0415

    by_id = {s.id: s for s in defn.states}
    types = []
    for b in bindings:
        if b.region is not None:
            found = nested_key(defn, b, library)
            if found is not None and (t := instance_key_type(found, schemas)) is not None:
                types.append(t)
            continue
        state = by_id.get(b.state)
        if state is None:
            continue
        for f in schemas.get(state.node_type, []):
            if getattr(f, "key", None) == b.param:
                types.append(getattr(f, "type", ""))
    return narrowest_type(types)


def promoted_param_type(
    defn: SubgraphDefinition | BotConfig, panel, key: str, schemas: Mapping[str, Sequence[object]]
) -> str | None:
    """The type a promoted node's param has on the subgraph node: its own, narrowed by the params linked to it.

    A ``zone`` param linked to by a ``nav_zone`` one takes the stricter ``nav_zone``, so
    the one value both run with is one both can use.
    """
    from bartlebot.nodes.param_schema import narrowest_type  # noqa: PLC0415

    by_id = {s.id: s for s in defn.states}
    member = by_id.get(panel.member or "")
    own = next(
        (getattr(f, "type", None) for f in schemas.get(member.node_type, []) if getattr(f, "key", None) == key)
        if member is not None
        else [],
        None,
    )
    linked = binding_type(defn, panel.bindings.get(key, []), schemas)
    types = [t for t in (own, linked) if t is not None]
    return narrowest_type(types) if types else None


def normalize_input_types(
    config: BotConfig, schemas: Mapping[str, Sequence[object]], library: SubgraphLibrary | None = None
) -> None:
    """Give each linked input the strictest type among its params. In place.

    An input keeps the type of the param it was made from until another links to it;
    a ``nav_zone`` param joining a ``zone`` input makes it ``nav_zone``, and unlinking
    that param loosens it again. An input with no links keeps the type it has.
    """
    meta = config.definition
    if meta is None:
        return
    for inp in meta.inputs:
        if inp.bindings and (t := binding_type(config, inp.bindings, schemas, library)) is not None:
            inp.type = t
