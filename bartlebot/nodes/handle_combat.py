"""Node: enter and complete a combat encounter using WizSprinter."""

import asyncio

from bartlebot.defaults.combat_config import BARTLEBOT_DEFAULT_COMBAT_CONFIG
from bartlebot.log_level import LogLevel
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="enemy",
        type="str",
        label="Enemy Name",
        help=(
            "Entity name of the enemy to initiate combat with. "
            "Leave empty to use the closest available mob."
        ),
        default="",
        default_hint="Default: targets the closest available mob",
    ),
    ParamField(
        key="use_bot_default",
        type="bool",
        label="Use bot-level default combat config",
        help=(
            "When checked, the bot-level default combat config is used "
            "(or the BartleBot built-in default if none is set). "
            "Uncheck to supply a per-node combat config below."
        ),
        default=True,
    ),
    ParamField(
        key="combat_config",
        type="text",
        label="Combat Config",
        help=(
            "WizSprinter combat DSL to use for this encounter. "
            "Only active when 'Use bot-level default' is unchecked."
        ),
        default="",
        default_hint="Default: uses the bot-level or BartleBot built-in combat config",
        disabled_by="use_bot_default",
    ),
    ParamField(
        key="combat_order",
        type="int_list",
        label="Combat Order",
        help=(
            "Order in which clients enter combat. Use 1-based client numbers "
            "matching p1/p2/p3/p4 in your combat config (e.g. '2, 1' sends "
            "client 2 first). Each client waits to enter battle before the "
            "next is sent. Leave empty to send all clients simultaneously."
        ),
        default=[],
        default_hint="Default: all clients enter combat simultaneously",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Initiates and completes a combat encounter. Teleports all clients to the "
        "target enemy, waits for them to enter battle, and then runs SprintyCombat "
        "until the duel ends. Detects item drops via chat during the encounter."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All clients have exited combat successfully."),
        ReturnSpec(
            "FAILURE",
            "No valid target found, no combat config available, or clients "
            "failed to enter combat within the timeout.",
        ),
    ],
)

_ENTER_COMBAT_TIMEOUT = 30.0
_POLL_INTERVAL = 0.25


async def run(machine, clients: list, params: dict) -> str:
    """Enter and complete a combat encounter for all clients."""
    from bartlebot.utils.drop_detection import extract_new_drops, snapshot_chat  # noqa: PLC0415

    enemy_name: str = params.get("enemy", "")
    use_bot_default: bool = params.get("use_bot_default", True)

    combat_dsl, dsl_source = _resolve_combat_dsl(machine, params, use_bot_default)
    if not combat_dsl:
        machine.log(
            "FAILURE: no combat DSL available "
            "(use_bot_default is off and per-node combat_config is empty)",
            LogLevel.WARNING,
        )
        return "FAILURE"

    enemy_display = repr(enemy_name) if enemy_name else "(closest)"
    machine.log(f"handle_combat: enemy={enemy_display}, DSL source={dsl_source!r}")
    machine.log(
        f"Combat DSL: {combat_dsl[:80]}{'…' if len(combat_dsl) > 80 else ''}",
        LogLevel.DEBUG,
    )

    # 1. Resolve target entity for each client
    targets = await asyncio.gather(*[_find_target(c, enemy_name) for c in clients])
    if not all(targets):
        none_idx = [i for i, t in enumerate(targets) if t is None]
        machine.log(
            f"FAILURE: target not found for client(s) {none_idx} "
            f"(enemy={enemy_display})",
            LogLevel.WARNING,
        )
        return "FAILURE"

    machine.log(f"Targets resolved for {len(clients)} client(s)", LogLevel.DEBUG)

    # 2. Snapshot chat before combat so we can diff for drops afterward
    chat_before = await asyncio.gather(*[snapshot_chat(c) for c in clients])

    # 3. Teleport clients to their target (sequential or simultaneous)
    combat_order: list[int] = params.get("combat_order") or []
    if combat_order:
        machine.log(f"Entering combat sequentially: order={combat_order}", LogLevel.DEBUG)
        for client_num in combat_order:
            idx = client_num - 1  # user-facing 1-based → 0-based list index
            if idx < 0 or idx >= len(clients):
                machine.log(
                    f"FAILURE: combat_order contains invalid client number {client_num} "
                    f"(have {len(clients)} client(s))",
                    LogLevel.WARNING,
                )
                return "FAILURE"
            await clients[idx].tp_to(targets[idx])
            if not await _wait_for_all_in_combat([clients[idx]], _ENTER_COMBAT_TIMEOUT):
                machine.log(
                    f"FAILURE: client {client_num} did not enter combat within "
                    f"{_ENTER_COMBAT_TIMEOUT}s",
                    LogLevel.WARNING,
                )
                return "FAILURE"
    else:
        machine.log("Teleporting all clients to target simultaneously…", LogLevel.DEBUG)
        await asyncio.gather(*[c.tp_to(t) for c, t in zip(clients, targets)])

        # 4. Wait for all clients to enter combat
        machine.log(
            f"Waiting for all clients to enter combat (timeout={_ENTER_COMBAT_TIMEOUT}s)…",
            LogLevel.DEBUG,
        )
        if not await _wait_for_all_in_combat(clients, _ENTER_COMBAT_TIMEOUT):
            battle_states = await asyncio.gather(*[c.in_battle() for c in clients])
            not_in_battle = [i for i, b in enumerate(battle_states) if not b]
            machine.log(f"Clients not in battle at timeout: {not_in_battle}", LogLevel.DEBUG)
            machine.log(
                f"FAILURE: timed out after {_ENTER_COMBAT_TIMEOUT}s waiting for "
                "clients to enter combat",
                LogLevel.WARNING,
            )
            return "FAILURE"

    machine.log("All clients in combat — running SprintyCombat")

    # 5. Run SprintyCombat for all clients in parallel
    await asyncio.gather(*[_run_combat(c, combat_dsl) for c in clients])

    machine.log("SprintyCombat finished for all clients", LogLevel.DEBUG)

    # 6. Detect drops by comparing chat before and after combat
    drops_found: list[str] = []
    for i, (client, before) in enumerate(zip(clients, chat_before)):
        after = await snapshot_chat(client)
        for item_name in extract_new_drops(before, after):
            machine.on_drop(item_name, 1, i)
            drops_found.append(item_name)

    if drops_found:
        machine.log(f"Detected {len(drops_found)} drop(s): {', '.join(drops_found)}")
    else:
        machine.log("No drops detected", LogLevel.DEBUG)

    machine.log("SUCCESS: combat complete")
    return "SUCCESS"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_combat_dsl(machine, params: dict, use_bot_default: bool) -> tuple[str, str]:
    """Return (dsl_string, source_label). dsl_string is empty if none can be resolved."""
    if use_bot_default:
        bot_cfg = machine._bot_config
        if bot_cfg is not None and not bot_cfg.use_bartlebot_default_combat_config:
            return bot_cfg.default_combat_config or "", "bot-level custom config"
        return BARTLEBOT_DEFAULT_COMBAT_CONFIG, "BartleBot built-in default"
    return params.get("combat_config", "").strip(), "per-node config"


async def _find_target(client, enemy_name: str):
    """Return the closest matching entity, or None if not found."""
    if enemy_name:
        return await client.find_closest_by_vague_name(enemy_name)
    return await client.find_closest_mob()


async def _run_combat(client, combat_dsl: str) -> None:
    """Run SprintyCombat for a single client using an inline DSL string."""
    from wizwalker.extensions.wizsprinter import SprintyCombat  # noqa: PLC0415
    from wizwalker.extensions.wizsprinter.combat_backends.config_backend import (  # noqa: PLC0415
        CombatConfigProvider,
    )
    from wizwalker.extensions.wizsprinter.combat_backends.backend_base import (  # noqa: PLC0415
        BaseCombatBackend,
    )

    class _StrCombatConfigProvider(CombatConfigProvider):
        """CombatConfigProvider variant that accepts a DSL string directly."""

        def __init__(self, content: str, cast_time: float = 0.2):
            # Bypass the parent __init__ (which opens a file path).
            BaseCombatBackend.__init__(self, cast_time=cast_time)
            self.filename = "<inline>"
            self.config = self.parse_config(content)

    provider = _StrCombatConfigProvider(combat_dsl)
    await SprintyCombat(client, provider).handle_combat()


async def _wait_for_all_in_combat(clients: list, timeout: float) -> bool:
    """Poll until all clients report in_battle(), or timeout expires."""
    async def _poll() -> None:
        while not all(await asyncio.gather(*[c.in_battle() for c in clients])):
            await asyncio.sleep(_POLL_INTERVAL)

    try:
        await asyncio.wait_for(_poll(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False
