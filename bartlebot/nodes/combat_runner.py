"""Shared combat execution helpers used by handle_combat and the ambient combat watcher.

Extracted so that machine.py does not need to import from a specific node module.
"""

import logging
import re

from bartlebot.defaults.combat_config import BARTLEBOT_DEFAULT_COMBAT_CONFIG

logger = logging.getLogger(__name__)

_BUILTIN_SOURCE = "BartleBot built-in default"


def resolve_combat(
    machine,
    use_default_strategy: bool,
    num_clients: int,
) -> tuple[list[str], list[int], str]:
    """Return (per_client_dsl, combat_order, source_label).

    Resolution path:
      1. Get bot_id from machine.bot_config.id.
      2. Look up active BotCombatStrategy UUID in SettingsManager.
      3. Load BotCombatStrategy; if use_default_strategy=True use default_strategy_id,
         otherwise find the StateAssignment whose state_ids contains machine.state.
      4. Load DuelCombatStrategy; for each 1-based player slot (1..num_clients) find
         the PlayerAssignment → load PlayerCombatStrategy → DSL string.
      5. Players with no assignment get BARTLEBOT_DEFAULT_COMBAT_CONFIG.
      6. combat_order from DuelCombatStrategy.combat_order (None → []).

    Falls back to BARTLEBOT_DEFAULT_COMBAT_CONFIG at every level if any file is
    missing, the active strategy is not set, or an error occurs.
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

    # Determine which DuelCombatStrategy UUID to use.
    if use_default_strategy:
        dcs_id = bcs.default_strategy_id
        assignment_source = "default strategy"
    else:
        current_state: str = getattr(machine, "state", "")
        dcs_id = bcs.find_dcs_id_for_state(current_state)
        assignment_source = f"state assignment for '{current_state}'"
        if not dcs_id:
            # State not assigned — fall back to the BCS default.
            dcs_id = bcs.default_strategy_id
            assignment_source = f"default strategy (state '{current_state}' has no assignment)"

    if not dcs_id:
        logger.debug("resolve_combat: no DCS assigned (%s) — using built-in default", assignment_source)
        return [BARTLEBOT_DEFAULT_COMBAT_CONFIG] * num_clients, [], _BUILTIN_SOURCE

    # Load the DuelCombatStrategy.
    try:
        dcs = load_duel_strategy(dcs_id)
    except Exception:
        logger.warning("resolve_combat: failed to load DuelCombatStrategy %s — using built-in default", dcs_id)
        return [BARTLEBOT_DEFAULT_COMBAT_CONFIG] * num_clients, [], _BUILTIN_SOURCE

    source_label = f"duel strategy '{dcs.name}' (via {assignment_source})"

    # Collect the DSL strings for each player assignment's referenced PCS.
    pcs_map: dict[str, str] = {}
    for assignment in dcs.player_assignments:
        pcs_id = assignment.player_combat_strategy_id
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


async def _run_combat(client, combat_dsl: str) -> None:
    """Run SprintyCombat for a single client using an inline DSL string."""
    from wizwalker.extensions.wizsprinter import SprintyCombat  # noqa: PLC0415
    from wizwalker.extensions.wizsprinter.combat_backends.backend_base import (  # noqa: PLC0415
        BaseCombatBackend,
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

    provider = _StrCombatConfigProvider(combat_dsl)
    await SprintyCombat(client, provider).wait_for_combat()


def _split_combat_config(dsl: str, num_clients: int) -> list[str]:
    """Split a Deimos-style multi-client DSL into per-client config strings.

    Section headers of the form '### p1', '### p2', etc. (1-based, matching
    Deimos convention) delimit per-client configs. Content before the first
    header and clients without a matching section fall back to client 1's
    section, or the full string if no headers exist.
    """
    pattern = re.compile(r"###\sp\s*(\d+)")

    lines = dsl.splitlines()
    sections: dict[int, list[str]] = {}  # 0-indexed client → lines
    current_idx = -1
    current_lines: list[str] = []

    for line in lines:
        match = re.search(pattern, line)
        if match:
            if current_idx != -1:
                sections[current_idx] = current_lines[:]
            current_idx = int(match.group(1)) - 1  # 1-based → 0-based
            current_lines = []
        else:
            current_lines.append(line)

    if current_idx == -1:
        # No section headers — all clients share the full config.
        return [dsl] * num_clients

    sections[current_idx] = current_lines
    return ["\n".join(sections.get(i, sections.get(0, lines))) for i in range(num_clients)]
