"""Node: enter and complete a combat encounter using WizSprinter."""

import asyncio
import logging
import time

from bartlebot.nodes.combat_runner import (
    _run_combat,
    resolve_combat,
)
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="enemy",
        type="str",
        label="Enemy Name",
        help=("Entity name of the enemy to initiate combat with. Leave empty to use the closest available mob."),
        default="",
        default_hint="targets the closest available mob",
    ),
    ParamField(
        key="use_default_strategy",
        type="bool",
        label="Use default strategy",
        help=(
            "When unchecked, this fight node appears in the Combat Tab's State Assignments section "
            "where you can assign a specific Duel Strategy to it. "
            "When checked, the Bot Strategy's default Duel Strategy is used."
        ),
        default=True,
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
        ReturnSpec("SUCCESS", "All players have exited combat successfully."),
        ReturnSpec(
            "FAILURE",
            "No valid target found, no combat config available, or players failed to enter combat within the timeout.",
        ),
    ],
    required_bot_options=[],
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
    """
    APIs: SprintyClient.find_closest_by_vague_name(), SprintyClient.find_closest_mob(),
          SprintyClient.tp_to(), SprintyClient.in_battle(), SprintyCombat

    Flow:
      1. Resolve combat DSL via the assigned Duel Profile, or the active BotCombatProfile's
         default, or the BartleBot built-in default. Return FAILURE if none available.
      2. Resolve target entity for each client (by enemy name or closest mob), retrying
         TARGET_ACQUIRE_RETRIES times. Return FAILURE if target not found.
      3. If max_duration is set and has elapsed, return FAILURE.
      4. Snapshot chat to enable drop detection after combat.
      5. Teleport clients to their targets:
         - If a combat order is set (duel profile override → profile default → legacy bot option):
           send clients one at a time in order, waiting for each to report in_battle().
         - Otherwise: teleport all clients simultaneously, retrying every _TP_RETRY_INTERVAL
           until all report in_battle(). Return FAILURE if ENTER_COMBAT_TIMEOUT expires.
      6. If max_duration is set and has elapsed, return FAILURE.
      7. Run SprintyCombat for all clients in parallel until combat ends.
      8. Diff chat to detect drops; report each via machine.on_drop().

    FAILURE: no combat DSL available; target not found after retries; max_duration exceeded;
    clients did not enter combat within ENTER_COMBAT_TIMEOUT.
    """
    from bartlebot.utils.drop_detection import extract_new_drops, snapshot_chat  # noqa: PLC0415

    max_duration: float = float(params.get("max_duration", 0.0))
    _node_start = time.monotonic()

    def _check_max_duration() -> bool:
        """Return True if max_duration is set and has been exceeded."""
        return max_duration > 0 and time.monotonic() - _node_start > max_duration

    enemy_name: str = params.get("enemy", "")
    use_default_strategy: bool = params.get("use_default_strategy", True)

    # Resolve per-client DSL list, combat order, and source label.
    per_client_dsl, combat_order, dsl_source = resolve_combat(machine, use_default_strategy, len(clients))

    enemy_display = repr(enemy_name) if enemy_name else "(closest)"
    machine.log(f"handle_combat: enemy={enemy_display}, DSL source={dsl_source!r}")
    if per_client_dsl:
        machine.log(
            f"Combat DSL (P1): {per_client_dsl[0][:80]}{'…' if len(per_client_dsl[0]) > 80 else ''}",
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

    # 5. Run SprintyCombat in parallel using the per-client DSL resolved earlier.
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
