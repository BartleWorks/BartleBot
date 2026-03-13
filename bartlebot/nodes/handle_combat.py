"""Node: enter and complete a combat encounter using WizSprinter."""

import asyncio

from bartlebot.defaults.combat_config import BARTLEBOT_DEFAULT_COMBAT_CONFIG
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
        disabled_by="use_bot_default",
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

    combat_dsl = _resolve_combat_dsl(machine, params, use_bot_default)
    if not combat_dsl:
        return "FAILURE"

    # 1. Resolve target entity for each client
    targets = await asyncio.gather(*[_find_target(c, enemy_name) for c in clients])
    if not all(targets):
        return "FAILURE"

    # 2. Snapshot chat before combat so we can diff for drops afterward
    chat_before = await asyncio.gather(*[snapshot_chat(c) for c in clients])

    # 3. Teleport all clients to their target
    await asyncio.gather(*[c.tp_to(t) for c, t in zip(clients, targets)])

    # 4. Wait for all clients to enter combat
    if not await _wait_for_all_in_combat(clients, _ENTER_COMBAT_TIMEOUT):
        return "FAILURE"

    # 5. Run SprintyCombat for all clients in parallel
    await asyncio.gather(*[_run_combat(c, combat_dsl) for c in clients])

    # 6. Detect drops by comparing chat before and after combat
    for i, (client, before) in enumerate(zip(clients, chat_before)):
        after = await snapshot_chat(client)
        for item_name in extract_new_drops(before, after):
            machine.on_drop(item_name, 1, i)

    return "SUCCESS"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_combat_dsl(machine, params: dict, use_bot_default: bool) -> str:
    """Return the DSL string to use, or empty string if none can be resolved."""
    if use_bot_default:
        bot_cfg = machine._bot_config
        if bot_cfg is not None and not bot_cfg.use_bartlebot_default_combat_config:
            return bot_cfg.default_combat_config or ""
        return BARTLEBOT_DEFAULT_COMBAT_CONFIG
    return params.get("combat_config", "").strip()


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
