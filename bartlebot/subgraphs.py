"""Composing subgraph instances into a bot, and extracting them back out. Pure.

A subgraph *definition* (``SubgraphDefinition``) holds states with local ids and
positions relative to ``(0, 0)``. An *instance* is an ordinary ``RegionModel`` whose
``source`` names the definition; its members are ordinary states the engine sees
as any other. Everything here is a function over Pydantic models with no Qt, no
settings and no file access beyond the :class:`SubgraphLibrary` it is handed, so
the whole policy is testable on a ``tmp_path``. docs/SUBGRAPHS.md has the design.

The rules this module owns:

- **Member ids are derived**: ``sg_<region id>_<local id>``, deterministic, so a
  definition update lands on the same ids that combat assignments and roles key on.
  A nested instance prefixes again: the inner definition composes to local ids like
  ``sg_<inner region>_<local>`` and the outer instance prefixes every one of them.
- **Params resolve in three levels**: an instance's ``param_overrides`` beat an
  input the instance has *set* (non-``None``), which beats the definition's value.
- **Layout is instance-owned**: a member the instance already has keeps its stored
  position; a member new to it is placed at ``anchor + definition position``.
- **Composition never leaves a bot unloadable**: when the definition is missing,
  invalid, cyclic, or would remove a member holding ``initial``/``recovery_state``
  or the endpoint of an external edge, the stored snapshot is kept and the region
  is flagged in the :class:`ComposeReport`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from bartlebot.bot.schema import (
    SUBGRAPH_LOCAL_ID,
    BotConfig,
    RegionModel,
    StateModel,
    SubgraphDefinition,
    SubgraphSource,
    TransitionModel,
)
from bartlebot.store_roots import Origin
from bartlebot.subgraph_store import SubgraphLibrary

ID_PREFIX = "sg_"
#: Nesting depth past which composition refuses. A cycle is caught by id before this
#: is reached; the limit is the second guard, against a chain that is merely absurd.
MAX_DEPTH = 8

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
    return f"{ID_PREFIX}{region_id}_{local}"


def local_id(region_id: str, member: str) -> str | None:
    """The inverse of :func:`member_id`; ``None`` if ``member`` is not this region's."""
    prefix = f"{ID_PREFIX}{region_id}_"
    if member.startswith(prefix) and len(member) > len(prefix):
        return member[len(prefix) :]
    return None


def _pos_add(a: dict | None, b: dict | None) -> dict | None:
    if a is None or b is None:
        return None
    return {"x": float(a["x"]) + float(b["x"]), "y": float(a["y"]) + float(b["y"])}


def _pos_sub(a: dict | None, b: dict | None) -> dict | None:
    if a is None or b is None:
        return None
    return {"x": float(a["x"]) - float(b["x"]), "y": float(a["y"]) - float(b["y"])}


# ---------------------------------------------------------------------------
# Params
# ---------------------------------------------------------------------------


def resolve_member_params(defn: SubgraphDefinition, def_state: StateModel, source: SubgraphSource) -> dict:
    """``param_override > bound input (if set) > definition value`` for one member.

    An input whose instance value is ``None`` is unset and leaves the definition's
    value in place; a stored empty value is a choice and applies.
    """
    params = dict(def_state.params)
    for inp in defn.inputs:
        value = source.inputs.get(inp.name)
        if value is None:
            continue
        for b in inp.bindings:
            if b.state == def_state.id:
                params[b.param] = value
    params.update(source.param_overrides.get(def_state.id, {}))
    return params


def _stale_overrides(states: list[StateModel], source: SubgraphSource) -> list[str]:
    """Overrides naming a state or param the definition no longer has. Kept, never pruned.

    ``states`` is the definition-local view *before* overrides are applied, so a
    param that exists only because the override put it there still reads as stale.
    """
    by_id = {s.id: s for s in states}
    reasons: list[str] = []
    for state_id, params in source.param_overrides.items():
        state = by_id.get(state_id)
        if state is None:
            reasons.append(f"override on '{state_id}' names a state the subgraph no longer has")
            continue
        for key in params:
            if key not in state.params:
                reasons.append(f"override '{key}' on '{state_id}' names a param the subgraph no longer sets")
    return reasons


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


@dataclass
class Materialized:
    """What one instance composes to, in bot-level ids. Regions are the *nested* ones only."""

    states: list[StateModel]
    transitions: list[TransitionModel]
    regions: list[RegionModel]
    #: Stale overrides found at any level. The instance keeps them; the report lists them.
    warnings: list[str] = field(default_factory=list)

    @property
    def member_ids(self) -> list[str]:
        return [s.id for s in self.states]


def _materialize_local(
    defn: SubgraphDefinition, source: SubgraphSource, library: SubgraphLibrary, stack: tuple[str, ...]
) -> Materialized:
    """Compose ``defn`` into definition-local ids, nested instances already expanded.

    Positions come out relative to ``defn``'s origin. Params are resolved for this
    level only: the caller (an outer instance) applies its own inputs and overrides
    on top, keyed by whatever local id the state has at that level.
    """
    if defn.id in stack:
        raise SubgraphCycleError([*stack, defn.id])
    if len(stack) >= MAX_DEPTH:
        raise SubgraphCycleError([*stack, defn.id])
    stack = (*stack, defn.id)

    states = [StateModel(**s.model_dump()) for s in defn.states]
    transitions = [TransitionModel(**t.model_dump()) for t in defn.transitions]
    regions: list[RegionModel] = []
    warnings: list[str] = []

    for inner in defn.regions:
        inner_src = inner.source
        assert inner_src is not None  # the schema refuses a definition region without one
        inner_defn = library.get(inner_src.subgraph_id)
        if inner_defn is None:
            raise LookupError(f"nested subgraph '{inner_src.subgraph_id}' is missing")
        inner_mat = _materialize_local(inner_defn, inner_src, library, stack)
        prefixed = _prefix(inner_mat, inner.id, inner_src.anchor)
        states.extend(prefixed.states)
        transitions.extend(prefixed.transitions)
        warnings.extend(prefixed.warnings)
        regions.append(
            RegionModel(
                id=inner.id,
                label=inner.label,
                members=prefixed.member_ids,
                color=inner.color,
                source=inner_src.model_copy(update={"version": inner_defn.version}),
            )
        )
        regions.extend(prefixed.regions)

    # This level's inputs and overrides, over every local id it can see -- a
    # nested member's local id at this level is its inner-prefixed one.
    warnings.extend(_stale_overrides(states, source))
    for s in states:
        s.params = resolve_member_params(defn, s, source)
    return Materialized(states, transitions, regions, warnings)


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
                    "members": [ids[m] for m in r.members],
                    # A nested anchor is relative to its definition's origin; once
                    # prefixed it is in the enclosing frame, and in the bot it is
                    # absolute -- which is what extract/push_back subtract from.
                    "source": r.source.model_copy(update={"anchor": _pos_add(r.source.anchor, anchor)}),
                }
            )
        )
    return Materialized(states, transitions, regions, list(mat.warnings))


def materialize_region(
    region_id: str,
    source: SubgraphSource,
    defn: SubgraphDefinition,
    library: SubgraphLibrary,
    *,
    existing_positions: dict[str, dict | None] | None = None,
) -> Materialized:
    """Compose the instance ``region_id`` of ``defn`` into bot-level states, transitions and nested regions.

    ``existing_positions`` maps a bot-level member id to the position the instance
    already stores for it; a member found there keeps that position, and one that
    is not is placed at ``anchor + definition position``.

    Raises :class:`SubgraphCycleError` on a cycle or past :data:`MAX_DEPTH`, and
    ``LookupError`` when a nested definition is missing from the library.
    """
    mat = _prefix(_materialize_local(defn, source, library, ()), region_id, source.anchor)
    if existing_positions:
        for s in mat.states:
            if s.id in existing_positions:
                s.position = existing_positions[s.id]
    return mat


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


def linked_regions(config: BotConfig) -> list[RegionModel]:
    return [r for r in config.regions if r.source is not None]


def external_edges(config: BotConfig, region: RegionModel) -> list[TransitionModel]:
    """Transitions with exactly one endpoint inside ``region``."""
    members = set(region.members)
    return [t for t in config.transitions if (t.source in members) != (t.dest in members)]


def _nested_prefix(region_id: str) -> str:
    return f"{ID_PREFIX}{region_id}_"


def compose_bot(config: BotConfig, library: SubgraphLibrary) -> tuple[BotConfig, ComposeReport]:
    """Recompose every linked region of ``config`` from ``library``.

    Returns **the same object** and an empty report when the bot has no linked
    region — the common case, and what keeps every existing caller's identity
    checks true. Otherwise works on a deep copy and never mutates the input.

    Per linked region, in file order, the outcome is one :class:`RegionStatus`:

    - ``missing``: no definition in the library; the snapshot is kept.
    - ``invalid``: the definition's file exists but failed to load; snapshot kept.
    - ``cycle``: the definition contains itself; snapshot kept, and the report's
      :attr:`ComposeReport.has_errors` is set — the caller refuses to run.
    - ``held``: the current definition would remove the member holding ``initial``
      or ``recovery_state``, or the endpoint of an external edge; snapshot kept,
      one reason per offender.
    - ``linked``: members, internal transitions and nested regions replaced from
      the definition; ``source.version`` updated. A stale ``param_override`` is
      kept and listed in ``reasons``.
    """
    if not linked_regions(config):
        return config, ComposeReport()

    result = config.model_copy(deep=True)
    report = ComposeReport()

    # Nested instances are recomposed by their outer region, so only the outermost
    # linked regions drive the loop: a region whose id is prefixed by another
    # linked region's is inside it.
    outer_ids = [r.id for r in linked_regions(result)]
    for region in list(linked_regions(result)):
        if any(region.id != o and region.id.startswith(_nested_prefix(o)) for o in outer_ids):
            continue
        source = region.source
        assert source is not None
        origin = library.origin(source.subgraph_id)
        defn = library.get(source.subgraph_id)
        if defn is None:
            error = library.error_for(source.subgraph_id)
            if error is not None:
                report.regions[region.id] = RegionStatus(origin, "invalid", [error])
            else:
                report.regions[region.id] = RegionStatus(origin, "missing", ["subgraph definition not found"])
            continue

        existing = {s.id: s.position for s in result.states if s.id in set(region.members)}
        try:
            mat = materialize_region(region.id, source, defn, library, existing_positions=existing)
        except SubgraphCycleError as exc:
            report.regions[region.id] = RegionStatus(origin, "cycle", [str(exc)], defn.version)
            continue
        except LookupError as exc:
            report.regions[region.id] = RegionStatus(origin, "missing", [str(exc)], defn.version)
            continue

        removed = set(region.members) - set(mat.member_ids)
        reasons: list[str] = []
        if result.initial in removed:
            reasons.append(f"the update removes '{result.initial}', which is the initial state")
        if result.recovery_state in removed:
            reasons.append(f"the update removes '{result.recovery_state}', which is the recovery state")
        for t in external_edges(result, region):
            inside = t.source if t.source in set(region.members) else t.dest
            if inside in removed:
                reasons.append(f"the update removes '{inside}', which has an edge to the rest of the bot")
        if reasons:
            report.regions[region.id] = RegionStatus(origin, "held", reasons, defn.version)
            continue

        _replace_members(result, region, mat)
        source.version = defn.version
        report.regions[region.id] = RegionStatus(origin, "linked", list(mat.warnings), defn.version)

    # A failure here is a bug in this module, not a library problem: raise.
    return BotConfig.model_validate(result.model_dump()), report


def _replace_members(config: BotConfig, region: RegionModel, mat: Materialized) -> None:
    old = set(region.members)
    nested_prefix = _nested_prefix(region.id)

    first_index = next((i for i, s in enumerate(config.states) if s.id in old), len(config.states))
    kept_states = [s for s in config.states if s.id not in old]
    insert_at = sum(1 for s in config.states[:first_index] if s.id not in old)
    config.states = kept_states[:insert_at] + mat.states + kept_states[insert_at:]

    first_t = next(
        (i for i, t in enumerate(config.transitions) if t.source in old and t.dest in old), len(config.transitions)
    )
    kept_t = [t for t in config.transitions if not (t.source in old and t.dest in old)]
    insert_t = sum(1 for t in config.transitions[:first_t] if not (t.source in old and t.dest in old))
    config.transitions = kept_t[:insert_t] + mat.transitions + kept_t[insert_t:]

    kept_r = [r for r in config.regions if not r.id.startswith(nested_prefix)]
    at = next(i for i, r in enumerate(kept_r) if r is region) + 1
    config.regions = kept_r[:at] + mat.regions + kept_r[at:]
    region.members = list(mat.member_ids)


# ---------------------------------------------------------------------------
# Extracting, converting, pushing back, detaching
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


def extract_definition(
    config: BotConfig, region_id: str, *, new_id: str, name: str, description: str | None = None
) -> tuple[SubgraphDefinition, SubgraphSource]:
    """Turn an ordinary region into a definition, and return the source that would link it.

    Local ids are the current member ids, so the conversion that follows
    (:func:`convert_region_to_instance`) keeps them recognisable. The anchor is
    the members' top-left; definition positions are relative to it. Only
    internal transitions are carried. A linked region wholly inside the members
    becomes a nested instance. No inputs — those are declared while editing.

    Raises ``ValueError`` if a member id cannot be a local id.
    """
    region = _region_by_id(config, region_id)
    members = set(region.members)
    by_id = {s.id: s for s in config.states}
    states = [by_id[m] for m in region.members if m in by_id]
    for s in states:
        if not SUBGRAPH_LOCAL_ID.match(s.id):
            raise ValueError(f"state id {s.id!r} cannot be a subgraph-local id")
    anchor = _anchor_of(states)

    nested: list[RegionModel] = []
    nested_member_ids: set[str] = set()
    for r in config.regions:
        if r.id == region_id or r.source is None or not set(r.members) <= members:
            continue
        src = r.source.model_copy(deep=True)
        src.anchor = _pos_sub(src.anchor, anchor) or {"x": 0.0, "y": 0.0}
        nested.append(RegionModel(id=r.id, label=r.label, members=list(r.members), color=r.color, source=src))
        nested_member_ids.update(r.members)

    defn = SubgraphDefinition(
        id=new_id,
        name=name,
        description=description,
        version=1,
        states=[s.model_copy(update={"position": _pos_sub(s.position, anchor)}, deep=True) for s in states],
        transitions=[t.model_copy(deep=True) for t in config.transitions if t.source in members and t.dest in members],
        regions=nested,
    )
    return defn, SubgraphSource(subgraph_id=new_id, version=1, anchor=anchor)


def convert_region_to_instance(
    config: BotConfig, region_id: str, defn: SubgraphDefinition, source: SubgraphSource
) -> dict[str, str]:
    """Rewrite the region's member ids to the derived form and attach ``source``. In place.

    Every place a member id appears is rewritten: states, transitions, ``initial``,
    ``recovery_state`` and every region's ``members``. A linked region nested inside
    gets its id prefixed too. Returns ``{old id: new id}`` so the caller can re-key
    what lives outside the config (combat assignments).
    """
    region = _region_by_id(config, region_id)
    id_map = {m: member_id(region_id, m) for m in region.members}
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
        if r.id != region_id and r.source is not None and any(m in id_map.values() for m in r.members):
            r.id = member_id(region_id, r.id)
    region.source = source.model_copy(deep=True)
    return id_map


def push_back(config: BotConfig, region_id: str, previous: SubgraphDefinition) -> tuple[SubgraphDefinition, list[str]]:
    """The instance ``region_id`` as a new definition version, after in-place editing.

    A member whose id derives from this region maps back to its local id; a
    state added while editing (a plain ``state_N``) keeps its id as the local id,
    and gets the derived form on the next compose. Inputs are carried from
    ``previous``; a binding whose state no longer exists is dropped and returned
    as a warning. ``version`` is ``previous.version + 1``.
    """
    region = _region_by_id(config, region_id)
    members = set(region.members)
    anchor = region.source.anchor if region.source is not None else {"x": 0.0, "y": 0.0}
    by_id = {s.id: s for s in config.states}
    local = {m: (local_id(region_id, m) or m) for m in region.members}
    for m, loc in local.items():
        if not SUBGRAPH_LOCAL_ID.match(loc):
            raise ValueError(f"state id {m!r} cannot be a subgraph-local id")

    nested: list[RegionModel] = []
    for r in config.regions:
        if r.id == region_id or r.source is None or not set(r.members) <= members:
            continue
        src = r.source.model_copy(deep=True)
        src.anchor = _pos_sub(src.anchor, anchor) or {"x": 0.0, "y": 0.0}
        nested.append(
            RegionModel(
                id=local_id(region_id, r.id) or r.id,
                label=r.label,
                members=[local[m] for m in r.members],
                color=r.color,
                source=src,
            )
        )

    states = [
        by_id[m].model_copy(update={"id": local[m], "position": _pos_sub(by_id[m].position, anchor)}, deep=True)
        for m in region.members
        if m in by_id
    ]
    transitions = [
        t.model_copy(update={"source": local[t.source], "dest": local[t.dest]}, deep=True)
        for t in config.transitions
        if t.source in members and t.dest in members
    ]

    local_ids = {s.id for s in states}
    warnings: list[str] = []
    inputs = []
    for inp in previous.inputs:
        kept = [b for b in inp.bindings if b.state in local_ids]
        for b in inp.bindings:
            if b.state not in local_ids:
                warnings.append(f"input '{inp.name}' lost its binding to '{b.state}', which no longer exists")
        inputs.append(inp.model_copy(update={"bindings": kept}, deep=True))

    defn = SubgraphDefinition(
        id=previous.id,
        name=previous.name,
        description=previous.description,
        version=previous.version + 1,
        inputs=inputs,
        states=states,
        transitions=transitions,
        regions=nested,
    )
    return defn, warnings


def detach(config: BotConfig, region_id: str) -> None:
    """Drop the link. Members and their ids stay exactly as they are. In place."""
    _region_by_id(config, region_id).source = None
