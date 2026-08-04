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

from bartlebot.bot.strategy_models import BotCombatStrategy, DuelCombatStrategy
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

    for label, dcs_id in _referenced_strategies(bcs):
        try:
            dcs = load_duel_strategy(dcs_id, root)
        except Exception:
            warnings.append(f"{label} Duel Strategy file is missing (id: {_short(dcs_id)}).")
            continue

        if num_clients < dcs.min_players:
            warnings.append(
                f"{label} strategy '{dcs.name}' requires ≥{dcs.min_players} players "
                f"but only {num_clients} client(s) are connected."
            )

        pcs_dsl = _load_linked_presets(dcs, label, root, warnings)
        _collect_dsls(dcs, pcs_dsl, dsl_sources)

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
    """
    fallback = "will use the bot's default strategy" if bcs.default_strategy_id else "will use built-in combat defaults"
    return [
        f"Combat state '{state.label or state.id}' has no Duel Strategy assigned — {fallback}."
        for state in exposed_combat_states(config)
        if bcs.find_dcs_id_for_state(state.id) is None
    ]


def _referenced_strategies(bcs: BotCombatStrategy) -> list[tuple[str, str]]:
    """Return (label, dcs_id) for every Duel Strategy this bot could use.

    Deduplicated by id: one strategy assigned to five states should not produce
    five identical warnings.
    """
    referenced: list[tuple[str, str]] = []
    seen: set[str] = set()

    if bcs.default_strategy_id:
        referenced.append(("Default", bcs.default_strategy_id))
        seen.add(bcs.default_strategy_id)

    for assignment in bcs.state_assignments:
        dcs_id = assignment.duel_combat_strategy_id
        if dcs_id not in seen:
            seen.add(dcs_id)
            referenced.append(("Assigned", dcs_id))

    return referenced


def _load_linked_presets(
    dcs: DuelCombatStrategy,
    label: str,
    root: str | Path | None,
    warnings: list[str],
) -> dict[str, str]:
    """Load every PlayerCombatStrategy this DCS links, warning about missing files."""
    pcs_dsl: dict[str, str] = {}
    for pcs_id in dcs.linked_pcs_ids():
        if pcs_id in pcs_dsl:
            continue
        try:
            pcs_dsl[pcs_id] = load_player_strategy(pcs_id, root).dsl
        except Exception:
            warnings.append(
                f"Player strategy file missing in {label.lower()} strategy '{dcs.name}' (id: {_short(pcs_id)})."
            )
    return pcs_dsl


def _collect_dsls(
    dcs: DuelCombatStrategy,
    pcs_dsl: dict[str, str],
    dsl_sources: dict[str, list[str]],
) -> None:
    """Record every non-empty DSL this strategy could run, keyed by the DSL itself.

    Empty DSLs are skipped deliberately: ``resolve_dsl_for_player`` returns None
    for them and ``resolve_combat`` substitutes the built-in default, so they
    never execute.  A freshly created preset is empty by design and flagging it
    would fire a warning on every new strategy.

    Assignments are checked regardless of how many clients are connected — a
    broken DSL is broken whether or not this particular run reaches it.
    """
    for assignment in dcs.player_assignments:
        if assignment.is_linked:
            dsl = pcs_dsl.get(assignment.player_combat_strategy_id)
            if dsl is None:
                continue  # missing file already reported
        else:
            dsl = assignment.dsl
        if not dsl or not dsl.strip():
            continue
        label = f"'{dcs.name}' ({_format_players(assignment.players)})"
        dsl_sources.setdefault(dsl, [])
        if label not in dsl_sources[dsl]:
            dsl_sources[dsl].append(label)


def _validate_dsls(dsl_sources: dict[str, list[str]]) -> list[str]:
    """Parse each distinct DSL once and describe the ones that fail."""
    warnings: list[str] = []
    for dsl, labels in dsl_sources.items():
        error = validate_dsl(dsl)
        if error:
            warnings.append(f"Combat DSL in {', '.join(labels)} does not parse: {error}")
    return warnings
