"""Pre-run validation of a bot's combat configuration.

Produces the warning list shown before a run starts.  Split out of AppController
so it can be tested without a QApplication: everything here is plain functions
over the strategy store, and every path degrades to a warning rather than an
exception — the bot can always run on built-in defaults.

Checks performed:
  - an active Bot Combat Strategy is set, and its file loads
  - every referenced Duel Combat Strategy resolves, in the profile or the library
  - every linked Player Combat Strategy resolves the same way
  - no library duel links a strategy that belongs to the profile (#118)
  - enough clients are connected for each strategy's ``min_players``
  - every combat state that asked for its own strategy actually has one
  - every DSL that could actually run parses

The DSL check is the reason this runs before the bot starts.  A syntax error
otherwise surfaces inside SprintyCombat mid-fight, several minutes into a run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bartlebot.bot.strategy_models import BotCombatStrategy, DuelConfig, StateAssignment
from bartlebot.combat_profile import StrategyLookup, profile_players_of
from bartlebot.fight_keys import bind, exposed_combat_states
from bartlebot.nodes.combat_runner import validate_dsl
from bartlebot.strategy_store import load_bot_strategy
from bartlebot.strategy_summary import format_players as _format_players

if TYPE_CHECKING:
    from pathlib import Path

    from bartlebot.bot.schema import BotConfig

# Strategy UUIDs are too long to show in a dialog; enough to grep the filename by.
_ID_PREVIEW = 8


def _short(uuid: str) -> str:
    return f"{uuid[:_ID_PREVIEW]}…"


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
    # The profile's own strategies first, then the library's -- see combat_profile.
    lookup = StrategyLookup(bcs, root=root)

    for context_label, dcs_id in _duel_sources(bcs, config):
        duel_config = lookup.duel(dcs_id)
        if duel_config is None:
            warnings.append(f"{context_label} Duel Strategy file is missing (id: {_short(dcs_id)}).")
            continue
        if lookup.profile_duel(dcs_id) is None:
            # Only a hand-edited file gets here: the dialog never offers a profile
            # strategy to a library duel, which every other profile shares.
            for pcs in profile_players_of(duel_config, bcs):
                warnings.append(
                    f"Library duel strategy '{duel_config.name}' uses '{pcs.name}', which belongs to "
                    f"this combat profile only — other profiles using '{duel_config.name}' won't have it."
                )
        config_label = f"strategy '{duel_config.name}'"
        dsl_label = f"'{duel_config.name}'"  # the strategy's own name — a shared preset always reads the same

        if num_clients < duel_config.min_players:
            warnings.append(
                f"{context_label} {config_label} requires ≥{duel_config.min_players} players "
                f"but only {num_clients} client(s) are connected."
            )

        pcs_dsl = _load_linked_presets(duel_config, context_label, config_label, lookup, warnings)
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

    An assignment that exists but links nothing counts as unassigned too — see
    ``resolve_combat``, which treats the two identically.
    """
    fallback = "will use the bot's default strategy" if bcs.default_strategy_id else "will use built-in combat defaults"
    warnings = []
    bound = bind(bcs, config)
    for state in exposed_combat_states(config):
        assignment = bound.get(state.id)
        if assignment is not None and assignment.is_linked:
            continue
        warnings.append(f"Combat state '{state.label or state.id}' has no Duel Strategy assigned — {fallback}.")
    return warnings


def _duel_sources(bcs: BotCombatStrategy, config: BotConfig | None) -> list[tuple[str, str]]:
    """Return (label, dcs_id) for every duel strategy this bot could use.

    Deduplicated by id across the default *and* every state: the same strategy
    assigned to five states should not produce five identical warnings.
    """
    sources: list[tuple[str, str]] = []
    seen: set[str] = set()

    if bcs.default_strategy_id:
        sources.append(("Default", bcs.default_strategy_id))
        seen.add(bcs.default_strategy_id)

    bound = bind(bcs, config)
    for assignment in bcs.state_assignments:
        dcs_id = assignment.duel_combat_strategy_id
        if dcs_id is not None and dcs_id not in seen:
            seen.add(dcs_id)
            sources.append((_state_context_label(assignment, config, bound), dcs_id))

    return sources


def _state_context_label(
    assignment: StateAssignment, config: BotConfig | None, bound: dict[str, StateAssignment]
) -> str:
    """Human-readable label for a state assignment, e.g. "'Defeat Howling Chaney'".

    Names the fights *bound* places on it, which follow an enemy to a new node. Falls
    back to the raw state ids it was stored with when none is placed, and to the id
    when no BotConfig was given to resolve labels from — collect_combat_warnings'
    ``config`` parameter is optional.
    """
    labels_by_id = {s.id: (s.label or s.id) for s in config.states} if config is not None else {}
    state_ids = [sid for sid, owner in bound.items() if owner is assignment] or assignment.state_ids
    names = [labels_by_id.get(sid, sid) for sid in state_ids]
    return ", ".join(f"'{n}'" for n in names)


def _load_linked_presets(
    duel_config: DuelConfig,
    label: str,
    config_label: str,
    lookup: StrategyLookup,
    warnings: list[str],
) -> dict[str, str]:
    """Find every PlayerCombatStrategy this duel config links, warning about missing ones."""
    pcs_dsl: dict[str, str] = {}
    for pcs_id in duel_config.linked_pcs_ids():
        if pcs_id in pcs_dsl:
            continue
        pcs = lookup.player(pcs_id)
        if pcs is not None:
            pcs_dsl[pcs_id] = pcs.dsl
        else:
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
