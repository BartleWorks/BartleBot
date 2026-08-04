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
  - every DSL that could actually run parses

The DSL check is the reason this runs before the bot starts.  A syntax error
otherwise surfaces inside SprintyCombat mid-fight, several minutes into a run.
"""

from __future__ import annotations

from pathlib import Path

from bartlebot.bot.strategy_models import BotCombatStrategy, DuelCombatStrategy
from bartlebot.nodes.combat_runner import validate_dsl
from bartlebot.strategy_store import (
    load_bot_strategy,
    load_duel_strategy,
    load_player_strategy,
)

# Strategy UUIDs are too long to show in a dialog; enough to grep the filename by.
_ID_PREVIEW = 8


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
    root: str | Path | None = None,
) -> list[str]:
    """Return human-readable warnings about the combat config. Empty means no issues.

    ``active_bcs_id`` is the Bot Combat Strategy selected for the bot about to run.
    ``root`` is the project root passed through to the strategy store; None uses
    its default.
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

    warnings.extend(_validate_dsls(dsl_sources))
    return warnings


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
