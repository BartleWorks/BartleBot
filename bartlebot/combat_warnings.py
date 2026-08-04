"""Pre-run validation of a bot's combat configuration.

Produces the warning list shown before a run starts.  Split out of AppController
so it can be tested without a QApplication: everything here is plain functions
over the strategy store, and every path degrades to a warning rather than an
exception — the bot can always run on built-in defaults.

Checks performed:
  - an active Bot Combat Strategy is set, and its file loads
  - every referenced Duel Combat Strategy file loads
  - every linked Player Combat Strategy file loads
  - enough clients are connected for each strategy's ``min_players``
  - every combat state that asked for its own strategy actually has one
  - every DSL that could actually run parses

The DSL check is the reason this runs before the bot starts.  A syntax error
otherwise surfaces inside SprintyCombat mid-fight, several minutes into a run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bartlebot.bot.strategy_models import BotCombatStrategy, DuelConfig, StateAssignment
from bartlebot.nodes.combat_runner import validate_dsl
from bartlebot.strategy_store import (
    load_bot_strategy,
    load_duel_strategy,
    load_player_strategy,
)

if TYPE_CHECKING:
    from pathlib import Path

    from bartlebot.bot.schema import BotConfig, StateModel

# Strategy UUIDs are too long to show in a dialog; enough to grep the filename by.
_ID_PREVIEW = 8


def exposed_combat_states(config: BotConfig) -> list[StateModel]:
    """Return the ``handle_combat`` states that opted out of the bot default.

    ``use_default_strategy: false`` is how a bot file says "this fight is
    special — give it its own Duel Strategy".  It is the portable half of the
    combat config: it ships with the bot and means the same thing for every
    user, while the strategy that satisfies it stays personal.

    ``use_default_duel_profile`` is the pre-v3 spelling.  The bots in this repo
    were migrated by ``scripts/migrate_bot_ids.py``, but users' own bots were
    not, so the old name is still honored on read.
    """
    return [
        s
        for s in config.states
        if s.node_type == "handle_combat"
        and not s.params.get("use_default_strategy", s.params.get("use_default_duel_profile", True))
    ]


def _short(uuid: str) -> str:
    return f"{uuid[:_ID_PREVIEW]}…"


def _format_players(players: list[int]) -> str:
    """'P1', 'P1–P3', or 'P1, P3' depending on whether the run is contiguous."""
    ordered = sorted(players)
    if not ordered:
        return "no players"
    if len(ordered) == 1:
        return f"P{ordered[0]}"
    if ordered == list(range(ordered[0], ordered[-1] + 1)):
        return f"P{ordered[0]}–P{ordered[-1]}"
    return ", ".join(f"P{p}" for p in ordered)


def collect_combat_warnings(
    active_bcs_id: str | None,
    num_clients: int,
    config: BotConfig | None = None,
    root: str | Path | None = None,
) -> list[str]:
    """Return human-readable warnings about the combat config. Empty means no issues.

    ``active_bcs_id`` is the Bot Combat Strategy selected for the bot about to run.
    ``config`` is the bot itself; when given, its exposed combat states are checked
    for an assignment.  ``root`` is the project root passed through to the strategy
    store; None uses its default.
    """
    if not active_bcs_id:
        return ["No Bot Combat Strategy is set — will use built-in combat defaults."]

    try:
        bcs = load_bot_strategy(active_bcs_id, root)
    except Exception:
        return [f"Active Bot Combat Strategy file is missing (id: {_short(active_bcs_id)})."]

    warnings: list[str] = []
    # DSL string → the labels that use it, so a shared preset is parsed once and
    # reported once no matter how many strategies link it.
    dsl_sources: dict[str, list[str]] = {}

    for context_label, dcs_id, inline_config in _duel_sources(bcs, config):
        if dcs_id is not None:
            try:
                dcs = load_duel_strategy(dcs_id, root)
            except Exception:
                warnings.append(f"{context_label} Duel Strategy file is missing (id: {_short(dcs_id)}).")
                continue
            duel_config = dcs
            config_label = f"strategy '{dcs.name}'"
            dsl_label = f"'{dcs.name}'"  # the strategy's own name — a shared preset always reads the same either way
        else:
            duel_config = inline_config
            config_label = "combat config"
            dsl_label = context_label  # no name to fall back on — identify it by where it's used

        if num_clients < duel_config.min_players:
            warnings.append(
                f"{context_label} {config_label} requires ≥{duel_config.min_players} players "
                f"but only {num_clients} client(s) are connected."
            )

        pcs_dsl = _load_linked_presets(duel_config, context_label, config_label, root, warnings)
        _collect_dsls(duel_config, dsl_label, pcs_dsl, dsl_sources)

    if config is not None:
        warnings.extend(_unassigned_state_warnings(config, bcs))

    warnings.extend(_validate_dsls(dsl_sources))
    return warnings


def _unassigned_state_warnings(config: BotConfig, bcs: BotCombatStrategy) -> list[str]:
    """Report combat states that asked for their own strategy but never got one.

    These are not errors — the state falls back to the bot default, or to the
    built-in default when there isn't one.  But the bot author marked the fight
    as needing special handling, so silently running the generic strategy is
    almost never what the user wants, and nothing else surfaces it.

    An assignment that exists but resolves to nothing (unlinked and empty) counts
    as unassigned too — see ``resolve_combat``, which treats the two identically.
    """
    fallback = "will use the bot's default strategy" if _has_default(bcs) else "will use built-in combat defaults"
    warnings = []
    for state in exposed_combat_states(config):
        assignment = bcs.find_assignment_for_state(state.id)
        if assignment is not None and (assignment.is_linked or not assignment.duel_config.is_empty()):
            continue
        warnings.append(f"Combat state '{state.label or state.id}' has no Duel Strategy assigned — {fallback}.")
    return warnings


def _has_default(bcs: BotCombatStrategy) -> bool:
    return bool(bcs.default_strategy_id) or not bcs.default_duel_config.is_empty()


def _duel_sources(bcs: BotCombatStrategy, config: BotConfig | None) -> list[tuple[str, str | None, DuelConfig | None]]:
    """Return (label, dcs_id, inline_config) for every duel target this bot could use.

    Exactly one of ``dcs_id`` / ``inline_config`` is set per entry — mirrors
    ``StateAssignment.is_linked``.  Linked entries are deduplicated by id across
    the default *and* every state (the same strategy assigned to five states
    should not produce five identical warnings); inline entries have no shared
    identity to dedupe by, so each is reported on its own.
    """
    sources: list[tuple[str, str | None, DuelConfig | None]] = []
    seen: set[str] = set()

    if bcs.default_strategy_id:
        sources.append(("Default", bcs.default_strategy_id, None))
        seen.add(bcs.default_strategy_id)
    elif not bcs.default_duel_config.is_empty():
        sources.append(("Default", None, bcs.default_duel_config))

    for assignment in bcs.state_assignments:
        label = _state_context_label(assignment, config)
        if assignment.is_linked:
            dcs_id = assignment.duel_combat_strategy_id
            if dcs_id not in seen:
                seen.add(dcs_id)
                sources.append((label, dcs_id, None))
        elif not assignment.duel_config.is_empty():
            sources.append((label, None, assignment.duel_config))

    return sources


def _state_context_label(assignment: StateAssignment, config: BotConfig | None) -> str:
    """Human-readable label for a state assignment, e.g. "'Defeat Howling Chaney'".

    Falls back to the raw state id when no BotConfig was given to resolve labels
    from — collect_combat_warnings' ``config`` parameter is optional.
    """
    labels_by_id = {s.id: (s.label or s.id) for s in config.states} if config is not None else {}
    names = [labels_by_id.get(sid, sid) for sid in assignment.state_ids]
    return ", ".join(f"'{n}'" for n in names)


def _load_linked_presets(
    duel_config: DuelConfig,
    label: str,
    config_label: str,
    root: str | Path | None,
    warnings: list[str],
) -> dict[str, str]:
    """Load every PlayerCombatStrategy this duel config links, warning about missing files."""
    pcs_dsl: dict[str, str] = {}
    for pcs_id in duel_config.linked_pcs_ids():
        if pcs_id in pcs_dsl:
            continue
        try:
            pcs_dsl[pcs_id] = load_player_strategy(pcs_id, root).dsl
        except Exception:
            warnings.append(f"Player strategy file missing in {label} {config_label} (id: {_short(pcs_id)}).")
    return pcs_dsl


def _collect_dsls(
    duel_config: DuelConfig,
    label: str,
    pcs_dsl: dict[str, str],
    dsl_sources: dict[str, list[str]],
) -> None:
    """Record every non-empty DSL this config could run, keyed by the DSL itself.

    Empty DSLs are skipped deliberately: ``resolve_dsl_for_player`` returns None
    for them and ``resolve_combat`` substitutes the built-in default, so they
    never execute.  A freshly created preset is empty by design and flagging it
    would fire a warning on every new strategy.

    Assignments are checked regardless of how many clients are connected — a
    broken DSL is broken whether or not this particular run reaches it.
    """
    for assignment in duel_config.player_assignments:
        if assignment.is_linked:
            dsl = pcs_dsl.get(assignment.player_combat_strategy_id)
            if dsl is None:
                continue  # missing file already reported
        else:
            dsl = assignment.dsl
        if not dsl or not dsl.strip():
            continue
        source_label = f"{label} ({_format_players(assignment.players)})"
        dsl_sources.setdefault(dsl, [])
        if source_label not in dsl_sources[dsl]:
            dsl_sources[dsl].append(source_label)


def _validate_dsls(dsl_sources: dict[str, list[str]]) -> list[str]:
    """Parse each distinct DSL once and describe the ones that fail."""
    warnings: list[str] = []
    for dsl, labels in dsl_sources.items():
        error = validate_dsl(dsl)
        if error:
            warnings.append(f"Combat DSL in {', '.join(labels)} does not parse: {error}")
    return warnings
