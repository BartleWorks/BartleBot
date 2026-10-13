"""Pre-run check of a bot's combat setup: faults, which stop the run, and notices, which never do.

Split out of AppController so it can be tested without a QApplication: everything here
is plain functions over the strategy store, and nothing raises.

**A fault is a choice the user made that cannot work.** The pre-run dialog shows faults
only, with Go back as its default:

  - the picked Bot Combat Strategy's file is missing, or a duel or player strategy it
    links is
  - a duel it uses needs more players than are connected (``min_players``)
  - a library duel links a strategy that belongs to the bot's own (#118)
  - a DSL that doesn't parse, anywhere this run could reach

**A notice is a fallback in use**: a fight on a less tailored level than it could be
(:mod:`bartlebot.combat.combat_resolution`), or an exposed fight the user's Bot Combat
Strategy covers with its default. A setup with notices and no faults is valid, so notices
go to the log and never to a dialog (``docs/COMBAT_CONFIG.md``, "Before a run").

The DSL check is the reason this runs before the bot starts. A syntax error otherwise
surfaces inside SprintyCombat mid-fight, several minutes into a run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bartlebot.combat.bot_combat_strategy import StrategyLookup, own_players_of
from bartlebot.combat.bot_strategy_models import BotCombatStrategy, StateAssignment
from bartlebot.combat.combat_resolution import (
    CombatResolver,
    FightResolution,
    Level,
    fight_label,
    group_fights,
    missing_pick_text,
)
from bartlebot.combat.fight_keys import bind, exposed_combat_states
from bartlebot.combat.strategy_summary import format_players as _format_players
from bartlebot.nodes.combat_runner import validate_dsl
from bartlebot.shared.strategy_models import DuelConfig

if TYPE_CHECKING:
    from pathlib import Path

    from bartlebot.bot.schema import BotConfig
    from bartlebot.shared.preferences import PreferenceStore

__all__ = ["CombatCheck", "check_combat", "exposed_combat_states"]

# Strategy UUIDs are too long to show in a dialog; enough to grep the filename by.
_ID_PREVIEW = 8


def _short(uuid: str) -> str:
    return f"{uuid[:_ID_PREVIEW]}…"


@dataclass
class CombatCheck:
    """What :func:`check_combat` found. No faults and no notices means nothing to say.

    The notices come in two kinds. ``unassigned`` names the exposed fights the pick's own
    default covers; ``fallbacks`` names the fights that fall past level 1, grouped by duel
    and level. The run-start line (``combat_resolution.describe_run``) says the same as
    ``fallbacks``, so only ``unassigned`` is logged before a run.
    """

    faults: list[str] = field(default_factory=list)
    unassigned: list[str] = field(default_factory=list)
    fallbacks: list[str] = field(default_factory=list)

    @property
    def notices(self) -> list[str]:
        """Both kinds of notice, the unassigned fights first."""
        return [*self.unassigned, *self.fallbacks]


def check_combat(
    active_bcs_id: str | None,
    num_clients: int,
    config: BotConfig | None = None,
    root: str | Path | None = None,
    user_root: str | Path | None = None,
    prefs: PreferenceStore | None = None,
) -> CombatCheck:
    """Check the combat setup of the bot about to run with *num_clients* clients.

    ``active_bcs_id`` is the user's Bot Combat Strategy for the bot, or None. ``config``
    is the bot itself; without it only the default fight is resolved. ``root`` and
    ``user_root`` go to the strategy store and ``prefs`` to the defaults; None uses each
    one's own default.
    """
    resolver = CombatResolver(config, num_clients, active_bcs_id, prefs=prefs, root=root, user_root=user_root)
    check = CombatCheck()
    # DSL string → the labels that use it, so a shared preset is parsed once and
    # reported once no matter how many strategies link it.
    dsl_sources: dict[str, list[str]] = {}

    if resolver.pick:
        bcs = resolver.picked
        if bcs is None:
            check.faults.append(missing_pick_text(resolver.pick))
        else:
            check.faults.extend(_pick_faults(bcs, num_clients, config, root, user_root, dsl_sources))

    # The duels the run falls to are reached too, so their DSLs are checked with the pick's.
    resolutions = resolver.fights()
    for res in resolutions.values():
        if res.level is not Level.PICK and res.duel is not None:
            pcs_dsl = {}
            for pcs_id in res.duel.linked_pcs_ids():
                pcs = res.lookup.player(pcs_id)
                if pcs is not None:
                    pcs_dsl[pcs_id] = pcs.dsl
            _collect_dsls(res.duel, f"'{res.duel.name}'", pcs_dsl, dsl_sources)

    check.faults.extend(_validate_dsls(dsl_sources))
    check.unassigned.extend(_unassigned_notices(config, resolutions))
    check.fallbacks.extend(_fallback_notices(config, resolutions))
    return check


def _pick_faults(
    bcs: BotCombatStrategy,
    num_clients: int,
    config: BotConfig | None,
    root: str | Path | None,
    user_root: str | Path | None,
    dsl_sources: dict[str, list[str]],
) -> list[str]:
    """Faults in the user's own Bot Combat Strategy, recording the DSLs it could run as it goes."""
    faults: list[str] = []
    # The bot's own strategies first, then the library's -- see bot_combat_strategy.
    lookup = StrategyLookup(bcs, root=root, user_root=user_root)
    for context_label, dcs_id in _duel_sources(bcs, config):
        duel_config = lookup.duel(dcs_id)
        if duel_config is None:
            faults.append(f"{context_label} Duel Strategy file is missing (id: {_short(dcs_id)}).")
            continue
        if lookup.own_duel(dcs_id) is None:
            # Only a hand-edited file gets here: the dialog never offers a bot's own
            # strategy to a library duel, which every other bot shares.
            for pcs in own_players_of(duel_config, bcs):
                faults.append(
                    f"Library duel strategy '{duel_config.name}' uses '{pcs.name}', which belongs to "
                    f"this bot only — other bots using '{duel_config.name}' won't have it."
                )
        config_label = f"strategy '{duel_config.name}'"
        dsl_label = f"'{duel_config.name}'"  # the strategy's own name — a shared preset always reads the same

        if num_clients < duel_config.min_players:
            faults.append(
                f"{context_label} {config_label} requires ≥{duel_config.min_players} players "
                f"but only {num_clients} client(s) are connected."
            )

        pcs_dsl = _load_linked_presets(duel_config, context_label, config_label, lookup, faults)
        _collect_dsls(duel_config, dsl_label, pcs_dsl, dsl_sources)
    return faults


def _unassigned_notices(config: BotConfig | None, resolutions: dict[str | None, FightResolution]) -> list[str]:
    """One line naming every exposed fight the pick's own default covers, or none.

    One line, so a Bot Combat Strategy that sets only its default says so once rather than
    once per fight.
    """
    unassigned = [f for f, res in resolutions.items() if res.level is Level.PICK and res.unassigned]
    if not unassigned:
        return []
    res = resolutions[unassigned[0]]
    names = ", ".join(fight_label(config, f) for f in unassigned)
    uses = "it uses" if len(unassigned) == 1 else "they use"
    return [
        f"No duel strategy is assigned to {names}, so {uses} '{res.duel.name}', "
        f"the default of your Bot Combat Strategy '{res.owner.name}'."
    ]


def _fallback_notices(config: BotConfig | None, resolutions: dict[str | None, FightResolution]) -> list[str]:
    """One line per duel and level that fights fall past level 1 to, grouped as ``describe_run`` groups them."""
    notices: list[str] = []
    fallen = {f: res for f, res in resolutions.items() if res.level is not Level.PICK}
    for (name, where), fights in group_fights(fallen).items():
        if len(fights) == len(resolutions):
            who = "Every fight runs"
        else:
            labels = ", ".join(fight_label(config, f) for f in fights)
            verb = "runs" if len(fights) == 1 and fights[0] is not None else "run"
            who = f"{labels[0].upper()}{labels[1:]} {verb}"
        notices.append(f"{who} {name} ({where}).")
    return notices


def _duel_sources(bcs: BotCombatStrategy, config: BotConfig | None) -> list[tuple[str, str]]:
    """Return (label, dcs_id) for every duel strategy this bot could use.

    Deduplicated by id across the default *and* every state: the same strategy
    assigned to five states should not produce five identical warnings.

    With the bot at hand, only assignments placed on one of its fights count. One left
    over from a fight the bot no longer has (a template update removed it) can't run, so
    it is no fault of this run: a dialog whose default is Go back must not stop a run
    over a line the run can never reach.
    """
    sources: list[tuple[str, str]] = []
    seen: set[str] = set()

    if bcs.default_strategy_id:
        sources.append(("Default", bcs.default_strategy_id))
        seen.add(bcs.default_strategy_id)

    bound = bind(bcs, config)
    placed = {id(assignment) for assignment in bound.values()}
    for assignment in bcs.state_assignments:
        if config is not None and id(assignment) not in placed:
            continue
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
    when no BotConfig was given to resolve labels from — check_combat's
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
