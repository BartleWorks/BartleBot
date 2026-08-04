"""Shared combat execution helpers used by handle_combat and the ambient combat watcher.

Extracted so that machine.py does not need to import from a specific node module.
"""

import logging
from functools import lru_cache

from bartlebot.defaults.combat_config import BARTLEBOT_DEFAULT_COMBAT_CONFIG

logger = logging.getLogger(__name__)

_BUILTIN_SOURCE = "BartleBot built-in default"


def slots_in_battle(in_battle_flags: list) -> list[int]:
    """Return the 0-based client indices whose ``in_battle()`` came back True.

    ``in_battle_flags`` is the result of a ``gather(..., return_exceptions=True)``
    over every client, so entries may be True, False, or an Exception.  Callers
    need the *indices* rather than the clients themselves: a client's position in
    the full client list is its player slot, and combat DSL is resolved per slot.
    Filtering to a shorter list of clients and then indexing positionally into the
    resolved DSLs hands the wrong player's strategy to every client after the
    first gap.
    """
    return [i for i, flag in enumerate(in_battle_flags) if flag is True]


def resolve_combat(
    machine,
    use_default_strategy: bool,
    num_clients: int,
) -> tuple[list[str], list[int], str]:
    """Return (per_client_dsl, combat_order, source_label).

    Resolution path:
      1. Get bot_id from machine.bot_config.id.
      2. Look up active BotCombatStrategy UUID in SettingsManager.
      3. Load BotCombatStrategy; if use_default_strategy=True use the BCS default,
         otherwise find the StateAssignment whose state_ids contains machine.state,
         falling back to the BCS default when no assignment covers the state.
      4. Each of those (state assignment or BCS default) is either linked to a
         library DuelCombatStrategy (duel_combat_strategy_id set — load it) or
         inline (its own DuelConfig, used as-is; an empty one is equivalent to
         no config at all).
      5. For each 1-based player slot (1..num_clients) find the PlayerAssignment
         → its inline DSL, or the linked PlayerCombatStrategy preset's DSL if
         player_combat_strategy_id is set.
      6. Players with no assignment get BARTLEBOT_DEFAULT_COMBAT_CONFIG.
      7. combat_order from the resolved config's combat_order (None → []).

    Falls back to BARTLEBOT_DEFAULT_COMBAT_CONFIG at every level if any file is
    missing, no config (linked or inline) is assigned, or an error occurs.
    """
    # Lazy imports to avoid circular imports and keep startup fast.
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415
    from bartlebot.strategy_store import (  # noqa: PLC0415
        load_bot_strategy,
        load_duel_strategy,
        load_player_strategy,
    )

    bot_cfg = machine.bot_config
    if bot_cfg is None:
        return [BARTLEBOT_DEFAULT_COMBAT_CONFIG] * num_clients, [], _BUILTIN_SOURCE

    bot_id: str = bot_cfg.id
    sm = SettingsManager.instance()
    active_bcs_id = sm.get_active_strategy(bot_id)

    if not active_bcs_id:
        logger.debug("resolve_combat: no active BotCombatStrategy for bot %s — using built-in default", bot_id)
        return [BARTLEBOT_DEFAULT_COMBAT_CONFIG] * num_clients, [], _BUILTIN_SOURCE

    # Load the active BotCombatStrategy.
    try:
        bcs = load_bot_strategy(active_bcs_id)
    except Exception:
        logger.warning("resolve_combat: failed to load BotCombatStrategy %s — using built-in default", active_bcs_id)
        return [BARTLEBOT_DEFAULT_COMBAT_CONFIG] * num_clients, [], _BUILTIN_SOURCE

    # Determine which duel target to use: a state assignment (linked or inline),
    # falling back to the BCS default (also linked or inline) when unassigned.
    if use_default_strategy:
        dcs_id, inline_config = bcs.default_strategy_id, bcs.default_duel_config
        assignment_source = "default strategy"
    else:
        current_state: str = getattr(machine, "state", "")
        assignment = bcs.find_assignment_for_state(current_state)
        dcs_id, inline_config = (
            (assignment.duel_combat_strategy_id, assignment.duel_config) if assignment is not None else (None, None)
        )
        # An assignment that resolves to nothing (unlinked and empty) is
        # equivalent to no assignment at all — fall back to the BCS default.
        if dcs_id or (inline_config is not None and not inline_config.is_empty()):
            assignment_source = f"state assignment for '{current_state}'"
        else:
            dcs_id, inline_config = bcs.default_strategy_id, bcs.default_duel_config
            assignment_source = f"default strategy (state '{current_state}' has no assignment)"

    if dcs_id:
        try:
            dcs = load_duel_strategy(dcs_id)
        except Exception:
            logger.warning("resolve_combat: failed to load DuelCombatStrategy %s — using built-in default", dcs_id)
            return [BARTLEBOT_DEFAULT_COMBAT_CONFIG] * num_clients, [], _BUILTIN_SOURCE
        source_label = f"duel strategy '{dcs.name}' (via {assignment_source})"
    elif not inline_config.is_empty():
        dcs = inline_config
        source_label = f"inline duel config (via {assignment_source})"
    else:
        logger.debug("resolve_combat: no duel config assigned (%s) — using built-in default", assignment_source)
        return [BARTLEBOT_DEFAULT_COMBAT_CONFIG] * num_clients, [], _BUILTIN_SOURCE

    # Collect the DSL strings for assignments that link a shared PCS preset.
    # Inline assignments carry their own DSL and need no lookup.
    pcs_map: dict[str, str] = {}
    for pcs_id in dcs.linked_pcs_ids():
        if pcs_id not in pcs_map:
            try:
                pcs = load_player_strategy(pcs_id)
                pcs_map[pcs_id] = pcs.dsl
            except Exception:
                logger.warning(
                    "resolve_combat: failed to load PlayerCombatStrategy %s — affected players use built-in default",
                    pcs_id,
                )
                pcs_map[pcs_id] = BARTLEBOT_DEFAULT_COMBAT_CONFIG

    # Build per-client DSL list (1-based player numbers → 0-based client indices).
    per_client_dsl: list[str] = []
    for client_idx in range(num_clients):
        player_num = client_idx + 1  # 1-based
        dsl = dcs.resolve_dsl_for_player(player_num, pcs_map)
        per_client_dsl.append(dsl if dsl else BARTLEBOT_DEFAULT_COMBAT_CONFIG)

    combat_order: list[int] = dcs.combat_order if dcs.combat_order is not None else []

    return per_client_dsl, combat_order, source_label


# ---------------------------------------------------------------------------
# DSL parsing
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _lark_parser():
    """Build the wizsprinter combat grammar parser once and reuse it.

    ``CombatConfigProvider.parse_config()`` rebuilds a ``Lark`` instance on every
    call (~77 ms), and ``_run_combat`` constructs a provider per client per fight,
    so that cost lands squarely in the hot path.  Lark parsers are reusable across
    ``parse()`` calls and this app is single-threaded under qasync, so one shared
    instance is safe.
    """
    from lark import Lark  # noqa: PLC0415
    from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (  # noqa: PLC0415
        get_sprinty_grammar,
    )

    return Lark(get_sprinty_grammar())


@lru_cache(maxsize=1)
def _str_provider_class():
    """Return a CombatConfigProvider subclass that accepts a DSL string.

    Built lazily and cached so importing this module does not pull in wizwalker,
    and so both ``_run_combat`` and ``validate_dsl`` share one definition.
    """
    from wizwalker.extensions.wizsprinter.combat_backends.backend_base import (  # noqa: PLC0415
        BaseCombatBackend,
    )
    from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (  # noqa: PLC0415
        TreeToConfig,
    )
    from wizwalker.extensions.wizsprinter.combat_backends.config_backend import (  # noqa: PLC0415
        CombatConfigProvider,
    )

    class _StrCombatConfigProvider(CombatConfigProvider):
        """CombatConfigProvider variant that accepts a DSL string directly."""

        def __init__(self, content: str, cast_time: float = 0.2):
            # Bypass the parent __init__ (which opens a file path).
            BaseCombatBackend.__init__(self, cast_time=cast_time)
            self.filename = "<inline>"
            self.config = self.parse_config(content)

        def parse_config(self, file_contents):
            # Same as the parent, but against the shared parser instead of a new one.
            tree = _lark_parser().parse(file_contents)
            return self._expand_config(TreeToConfig().transform(tree))

    return _StrCombatConfigProvider


def validate_dsl(dsl: str) -> str | None:
    """Return a one-line parse error for ``dsl``, or None if it is valid.

    An empty or whitespace-only DSL is reported as valid: ``resolve_dsl_for_player``
    returns None for it and ``resolve_combat`` substitutes the built-in default, so
    it is never executed.  A newly created preset starts empty by design and must
    not be flagged.

    Pure — no client, no Qt, no disk.  Used to surface syntax errors in the pre-run
    warning dialog instead of mid-fight.
    """
    if not dsl or not dsl.strip():
        return None
    try:
        _str_provider_class()(dsl)
    except Exception as exc:
        message = str(exc).strip()
        return message.splitlines()[0] if message else exc.__class__.__name__
    return None


async def _run_combat(client, combat_dsl: str) -> None:
    """Run SprintyCombat for a single client using an inline DSL string."""
    from wizwalker.extensions.wizsprinter import SprintyCombat  # noqa: PLC0415

    provider = _str_provider_class()(combat_dsl)
    await SprintyCombat(client, provider).wait_for_combat()
