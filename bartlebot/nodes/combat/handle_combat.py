"""Node: enter and complete a combat encounter using WizSprinter."""

import asyncio
import logging
import time

from bartlebot.nodes.combat_runner import _resolve_combat_dsl, _run_combat, _split_combat_config
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="enemy",
        type="str",
        label="Enemy Name",
        help=("Entity name of the enemy to initiate combat with. Leave empty to use the closest available mob."),
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
        help=("Combat config to use for this encounter. Only active when 'Use bot-level default' is unchecked."),
        default="",
        default_hint="Default: uses the bot-level or BartleBot built-in combat config",
        placeholder="Enter combat DSL…",
        disabled_by="use_bot_default",
    ),
    ParamField(
        key="max_duration",
        type="float",
        label="Max Duration (s)",
        help=(
            "If the entire combat node (target acquisition + waiting for battle + SprintyCombat) "
            "exceeds this many seconds, exit early with FAILURE. "
            "0 = no limit."
        ),
        default=0.0,
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
            "No valid target found, no combat config available, or clients failed to enter combat within the timeout.",
        ),
    ],
    required_bot_options=[
        BotOptionSpec(
            name="Default Combat Order",
            type="int_list",
            default=[],
            description=(
                "Default order clients enter combat (e.g. '2, 1' sends client 2 first). "
                "Empty = all clients enter simultaneously. Can be overridden per node "
                "via Option Overrides."
            ),
        ),
    ],
    max_consecutive_retries=5,
)

from bartlebot.nodes.timing import (
    ENTER_COMBAT_TIMEOUT,
    POLL_INTERVAL,
    TARGET_ACQUIRE_DELAY,
    TARGET_ACQUIRE_RETRIES,
    jitter,
)

_TP_RETRY_INTERVAL = 5.0  # re-teleport this often if still not in battle


async def run(machine, clients: list, params: dict) -> str:
    """Enter and complete a combat encounter for all clients."""
    from bartlebot.utils.drop_detection import extract_new_drops, snapshot_chat  # noqa: PLC0415

    max_duration: float = float(params.get("max_duration", 0.0))
    _node_start = time.monotonic()

    def _check_max_duration() -> bool:
        """Return True if max_duration is set and has been exceeded."""
        return max_duration > 0 and time.monotonic() - _node_start > max_duration

    enemy_name: str = params.get("enemy", "")
    use_bot_default: bool = params.get("use_bot_default", True)

    combat_dsl, dsl_source = _resolve_combat_dsl(machine, params, use_bot_default)
    if not combat_dsl:
        machine.log(
            "FAILURE: no combat DSL available (use_bot_default is off and per-node combat_config is empty)",
            logging.WARNING,
        )
        return "FAILURE"

    enemy_display = repr(enemy_name) if enemy_name else "(closest)"
    machine.log(f"handle_combat: enemy={enemy_display}, DSL source={dsl_source!r}")
    machine.log(
        f"Combat DSL: {combat_dsl[:80]}{'…' if len(combat_dsl) > 80 else ''}",
        logging.DEBUG,
    )

    # 1. Resolve target entity for each client, retrying to allow enemies time to load.
    targets = None
    results: list = []
    for attempt in range(TARGET_ACQUIRE_RETRIES):
        results = list(await asyncio.gather(*[_find_target(c, enemy_name) for c in clients]))
        if all(results):
            targets = results
            break
        if attempt < TARGET_ACQUIRE_RETRIES - 1:
            machine.log(
                f"Target not found on attempt {attempt + 1}/{TARGET_ACQUIRE_RETRIES},"
                f" waiting {TARGET_ACQUIRE_DELAY}s for enemy to load\u2026",
                logging.DEBUG,
            )
            await asyncio.sleep(jitter(TARGET_ACQUIRE_DELAY))

    if not targets:
        none_idx = [i for i, t in enumerate(results) if t is None]
        machine.log(
            f"FAILURE: target not found for client(s) {none_idx} after"
            f" {TARGET_ACQUIRE_RETRIES} attempt(s) (enemy={enemy_display})",
            logging.WARNING,
        )
        return "FAILURE"

    if _check_max_duration():
        machine.log(
            f"FAILURE: max_duration {max_duration:.0f}s exceeded during target acquisition",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"Targets resolved for {len(clients)} client(s)", logging.DEBUG)

    # 2. Snapshot chat before combat so we can diff for drops afterward
    chat_before = await asyncio.gather(*[snapshot_chat(c) for c in clients])

    # 3. Teleport clients to their target (sequential or simultaneous).
    combat_order: list[int] = machine.get_option("Default Combat Order", []) or []
    if combat_order:
        machine.log(f"Entering combat sequentially: order={combat_order}", logging.DEBUG)
        for client_num in combat_order:
            idx = client_num - 1  # user-facing 1-based → 0-based list index
            if idx < 0 or idx >= len(clients):
                machine.log(
                    f"combat_order: skipping client {client_num} — not connected (have {len(clients)} client(s))",
                    logging.DEBUG,
                )
                continue
            if not await _tp_and_wait_for_combat(clients[idx], enemy_name, ENTER_COMBAT_TIMEOUT):
                machine.log(
                    f"FAILURE: client {client_num} did not enter combat within {ENTER_COMBAT_TIMEOUT}s",
                    logging.WARNING,
                )
                return "FAILURE"
    else:
        # 4. Teleport all clients to their targets, retrying until in combat
        machine.log("Teleporting all clients to target (retrying until in combat)…", logging.DEBUG)
        results = await asyncio.gather(*[_tp_and_wait_for_combat(c, enemy_name, ENTER_COMBAT_TIMEOUT) for c in clients])
        if not all(results):
            not_in_battle = [i for i, ok in enumerate(results) if not ok]
            machine.log(f"Clients not in battle at timeout: {not_in_battle}", logging.DEBUG)
            machine.log(
                f"FAILURE: timed out after {ENTER_COMBAT_TIMEOUT}s waiting for clients to enter combat",
                logging.WARNING,
            )
            return "FAILURE"

    if _check_max_duration():
        machine.log(
            f"FAILURE: max_duration {max_duration:.0f}s exceeded before SprintyCombat",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log("All clients in combat — running SprintyCombat")

    # 5. Split multi-client DSL by ### pN section headers, then run SprintyCombat in parallel.
    per_client_dsl = _split_combat_config(combat_dsl, len(clients))
    await asyncio.gather(*[_run_combat(c, dsl) for c, dsl in zip(clients, per_client_dsl)])

    machine.log("SprintyCombat finished for all clients", logging.DEBUG)

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
        machine.log("No drops detected", logging.DEBUG)

    machine.log("SUCCESS: combat complete")
    return "SUCCESS"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _find_target(client, enemy_name: str):
    """Return the closest matching entity, or None if not found."""
    if enemy_name:
        return await client.find_closest_by_vague_name(enemy_name)
    return await client.find_closest_mob()


async def _tp_and_wait_for_combat(client, enemy_name: str, timeout: float) -> bool:
    """Teleport to target, retrying every _TP_RETRY_INTERVAL seconds until in combat."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        target = await _find_target(client, enemy_name)
        if target is None:
            return False
        await client.tp_to(target)

        # Poll in_battle() for up to _TP_RETRY_INTERVAL (or remaining time)
        retry_at = min(asyncio.get_event_loop().time() + _TP_RETRY_INTERVAL, deadline)
        while asyncio.get_event_loop().time() < retry_at:
            if await client.in_battle():
                return True
            await asyncio.sleep(POLL_INTERVAL)

        if asyncio.get_event_loop().time() >= deadline:
            break

    return False
