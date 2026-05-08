"""Node: interact with an NPC or world object via the X key."""

import asyncio
import logging

from wizwalker import XYZ, Keycode

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import ADVANCE_DIALOG_PATH

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="str",
        label="Zone",
        help="Zone name players must currently be in when this node executes.",
        default="",
        required=True,
    ),
    ParamField(
        key="interact_xyz",
        type="xyz",
        label="Interact XYZ",
        help=("Coordinates of the item to interact with. The interact prompt should appear when standing here."),
        default={"x": 0.0, "y": 0.0, "z": 0.0},
        required=True,
    ),
    ParamField(
        key="reset_xyz",
        type="xyz",
        label="Reset XYZ",
        help=(
            "Coordinates used to dismiss the interact prompt and reset state. "
            "The prompt must NOT appear when standing here."
        ),
        default={"x": 0.0, "y": 0.0, "z": 0.0},
        required=True,
    ),
    ParamField(
        key="interact_clients",
        type="int_list",
        label="Interact Clients",
        help=(
            "1-based client numbers that should perform this interaction (e.g. '1, 3'). "
            "Leave empty to have all clients interact."
        ),
        default=[],
        required=False,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Interacts with an NPC or world object (lever, collectable, etc.) and advances through any resulting dialogue."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Interaction complete and all dialogue dismissed."),
        ReturnSpec(
            "FAILURE",
            "The interact prompt never appeared after retries, or dialogue did not clear within the timeout.",
        ),
    ],
    required_bot_options=[
        BotOptionSpec(
            name="Safety Delay",
            type="float",
            default=3.0,
            description=(
                "Seconds to allow for game-server responses when waiting for prompts or menus. "
                "Increase on high-latency connections."
            ),
        ),
    ],
    max_consecutive_retries=10,
)

from bartlebot.nodes.timing import DIALOG_TIMEOUT, MAX_RETRIES, POLL_INTERVAL


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.is_in_npc_range(),
          client.send_key(Keycode.X), client.is_in_dialog()

    Flow:
      1. Verify all clients are in zone. Return FAILURE if not.
      2. Resolve active_clients from interact_clients param (default: all clients).
         Return SUCCESS immediately if the resolved set is empty.
      3. Clear any pre-existing interact prompt by teleporting to reset_xyz.
      4. Retry loop (up to MAX_RETRIES): teleport to interact_xyz, wait for the prompt
         on all active_clients. If it does not appear, teleport to reset_xyz and retry.
         Return FAILURE if the prompt never appeared.
      5. Press X on all active_clients.
      6. Advance dialogue: click the advance button while any client is in_dialog().
         Continue until no client is in dialogue AND Safety Delay seconds have elapsed
         (to avoid missing late-opening windows). Return FAILURE if DIALOG_TIMEOUT expires.

    FAILURE: clients not in zone; interact prompt never appeared after MAX_RETRIES;
    dialogue did not clear within DIALOG_TIMEOUT.
    """
    zone: str = params["zone"]
    interact = XYZ(**params["interact_xyz"])
    reset = XYZ(**params["reset_xyz"])

    machine.log(f"interact: {zone!r} XYZ({interact.x:.1f}, {interact.y:.1f}, {interact.z:.1f})")

    # 1. Verify all clients are in zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])

    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if not all(z == zone for z in zones):
        wrong = [z for z in zones if z != zone]
        machine.log(
            f"FAILURE: clients not in expected zone (expected={zone!r}, unexpected={wrong})",
            logging.WARNING,
        )
        return "FAILURE"

    # 2. Resolve which clients should perform the interaction.
    raw: list[int] = params.get("interact_clients") or []
    interact_indices = [n - 1 for n in raw if 0 < n <= len(clients)] if raw else list(range(len(clients)))
    if not interact_indices:
        machine.log("interact: no clients selected — SUCCESS")
        return "SUCCESS"

    active_clients = [clients[i] for i in interact_indices]
    machine.log(f"Interacting client(s): {[i + 1 for i in interact_indices]}", logging.DEBUG)

    safety_delay: float = machine.get_option("Safety Delay", 3.0)

    # 3. Clear any pre-existing interact prompt before starting.
    #    A stale prompt from a previous interaction can linger briefly after teleport,
    #    causing the client to interact with the wrong object if X is pressed too soon.
    prompts = await asyncio.gather(*[c.is_in_npc_range() for c in active_clients])
    if any(prompts):
        stale = [interact_indices[i] for i, p in enumerate(prompts) if p]
        machine.log(
            f"Pre-existing interact prompt on client(s) {[i + 1 for i in stale]}; teleporting to reset_xyz to clear it",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for c in active_clients])
        await node_helpers.poll_until(
            lambda: node_helpers.none_in_npc_range(active_clients), safety_delay, POLL_INTERVAL
        )

    # 4–5. Retry loop: teleport to interact_xyz, wait for the prompt, reset if it doesn't appear.
    prompt_appeared = False
    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(f"Attempt {attempt}/{MAX_RETRIES}: teleporting to interact_xyz", logging.DEBUG)
        await asyncio.gather(*[c.teleport(interact) for c in active_clients])

        machine.log("Waiting for interact prompt…", logging.DEBUG)
        if await node_helpers.poll_until(
            lambda: node_helpers.all_in_npc_range(active_clients), safety_delay, POLL_INTERVAL
        ):
            prompt_appeared = True
            break

        machine.log(
            f"Interact prompt did not appear (attempt {attempt}); resetting",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for c in active_clients])

    if not prompt_appeared:
        machine.log(
            f"FAILURE: interact prompt never appeared after {MAX_RETRIES} attempts",
            logging.WARNING,
        )
        return "FAILURE"

    # 6. Press X to begin interaction.
    machine.log("Pressing X to interact", logging.DEBUG)
    await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in active_clients])

    # 7. Advance through dialogue; start polling immediately after X.
    #    Only exit once no dialog is open AND at least Safety Delay seconds have
    #    passed, so late-opening dialog windows are not missed.
    machine.log("Advancing dialogue…", logging.DEBUG)
    loop_start = asyncio.get_event_loop().time()
    while True:
        dialogs = await asyncio.gather(*[c.is_in_dialog() for c in active_clients])
        elapsed = asyncio.get_event_loop().time() - loop_start
        if not any(dialogs) and elapsed >= safety_delay:
            break
        if elapsed > DIALOG_TIMEOUT:
            stuck = [i for i, d in enumerate(dialogs) if d]
            machine.log(f"Clients still in dialog at timeout: {stuck}", logging.DEBUG)
            machine.log(
                f"FAILURE: dialogue did not clear within {DIALOG_TIMEOUT}s",
                logging.WARNING,
            )
            return "FAILURE"
        if any(dialogs):
            await asyncio.gather(
                *[node_helpers.click_by_path(c, ADVANCE_DIALOG_PATH) for c, d in zip(active_clients, dialogs) if d]
            )
        await asyncio.sleep(POLL_INTERVAL)

    machine.log("SUCCESS: interaction complete")
    return "SUCCESS"
