"""Node: apply level-scaling elixir via NPC interaction."""

import asyncio
import logging

from wizwalker import XYZ, Keycode

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import LEVEL_SCALING_ELIXIR_PATH, LEVEL_SCALING_OPTION_BUTTON_PATH

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="str",
        label="Zone",
        help="Zone name all clients must be in when this node executes (the level-scaling dungeon).",
        default="",
        required=True,
    ),
    ParamField(
        key="interact_xyz",
        type="xyz",
        label="Interact XYZ",
        help=("Coordinates of the level-scaling NPC. The interact prompt should appear when standing here."),
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
]

_SAFETY_DELAY_OPTION = BotOptionSpec(
    name="Safety Delay",
    type="float",
    default=3.0,
    description=(
        "Seconds to allow for game-server responses when waiting for prompts or menus. "
        "Increase on high-latency connections."
    ),
)

_LEVEL_SCALING_CLIENTS_OPTION = BotOptionSpec(
    name="Level Scaling Clients",
    type="int_list",
    default=[],
    description=(
        "1-based client numbers to check/apply level scaling (e.g. '2, 3'). "
        "Empty = no clients (no scaling performed). Shared by check_level_scaling and apply_level_scaling."
    ),
)

NODE_SPEC = NodeSpec(
    description=(
        "Interacts with the level-scaling NPC to apply the level-scaling elixir. "
        "Assumes clients are already in the correct dungeon zone."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Level-scaling elixir applied to all specified clients."),
        ReturnSpec(
            "FAILURE",
            "Clients not in zone, interact prompt never appeared after retries, "
            "NPCServicesWin option button never became visible, "
            "or elixir not active after interaction.",
        ),
    ],
    required_bot_options=[_SAFETY_DELAY_OPTION, _LEVEL_SCALING_CLIENTS_OPTION],
)

from bartlebot.nodes.timing import MAX_RETRIES, POLL_INTERVAL


async def run(machine, clients: list, params: dict) -> str:
    """Apply the level-scaling elixir via NPC interaction."""
    zone: str = params["zone"]
    interact = XYZ(**params["interact_xyz"])
    reset = XYZ(**params["reset_xyz"])

    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    machine.log(f"apply_level_scaling: {zone!r} XYZ({interact.x:.1f}, {interact.y:.1f}, {interact.z:.1f})")

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

    # 2. Resolve clients_to_scale.
    raw: list[int] = machine.get_option("Level Scaling Clients", []) or []
    scale_indices = [n - 1 for n in raw if 0 < n <= len(clients)] if raw else []
    if not scale_indices:
        machine.log("apply_level_scaling: no clients to scale — SUCCESS")
        return "SUCCESS"

    scale_clients = [clients[i] for i in scale_indices]
    machine.log(f"Scaling client(s): {[i + 1 for i in scale_indices]}", logging.DEBUG)

    # 3. Clear any pre-existing interact prompt on the scaled clients.
    prompts = await asyncio.gather(*[c.is_in_npc_range() for c in scale_clients])
    if any(prompts):
        stale = [scale_indices[i] for i, p in enumerate(prompts) if p]
        machine.log(
            f"Pre-existing interact prompt on client(s) {[i + 1 for i in stale]}; teleporting to reset_xyz to clear it",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for c in scale_clients])
        await node_helpers.poll_until(
            lambda: node_helpers.none_in_npc_range(scale_clients), safety_delay, POLL_INTERVAL
        )

    # 4-5. Retry loop: teleport to interact_xyz, wait for prompt.
    prompt_appeared = False
    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(f"Attempt {attempt}/{MAX_RETRIES}: teleporting to interact_xyz", logging.DEBUG)
        await asyncio.gather(*[c.teleport(interact) for c in scale_clients])

        machine.log("Waiting for interact prompt…", logging.DEBUG)
        if await node_helpers.poll_until(
            lambda: node_helpers.all_in_npc_range(scale_clients), safety_delay, POLL_INTERVAL
        ):
            prompt_appeared = True
            break

        machine.log(
            f"Interact prompt did not appear (attempt {attempt}); resetting",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for c in scale_clients])

    if not prompt_appeared:
        machine.log(
            f"FAILURE: interact prompt never appeared after {MAX_RETRIES} attempts",
            logging.WARNING,
        )
        return "FAILURE"

    # 6. Press X on all scaled clients simultaneously.
    machine.log("Pressing X to interact with level-scaling NPC", logging.DEBUG)
    await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in scale_clients])

    # 7. Wait for NPCServicesWin option button to appear.
    machine.log("Waiting for NPCServicesWin menu…", logging.DEBUG)
    if not await node_helpers.poll_until(lambda: _all_menu_visible(scale_clients), safety_delay, POLL_INTERVAL):
        machine.log(
            f"FAILURE: NPCServicesWin option button never appeared within {safety_delay}s",
            logging.WARNING,
        )
        return "FAILURE"

    # 8. Click option button on all scaled clients simultaneously.
    machine.log("Clicking level-scaling option button", logging.DEBUG)
    await asyncio.gather(*[node_helpers.click_by_path(c, LEVEL_SCALING_OPTION_BUTTON_PATH) for c in scale_clients])

    # 9. Wait for menu to dismiss.
    machine.log("Waiting for NPCServicesWin menu to dismiss…", logging.DEBUG)
    await node_helpers.poll_until(lambda: _none_menu_visible(scale_clients), safety_delay, POLL_INTERVAL)

    # 10. Wait for elixir to apply before checking.
    machine.log(f"Waiting {safety_delay}s for elixir to apply…", logging.DEBUG)
    await asyncio.sleep(safety_delay)

    # 11. Verify elixir is active on all scaled clients.
    machine.log("Verifying level-scaling elixir is active…", logging.DEBUG)
    elixir_results = await asyncio.gather(
        *[node_helpers.is_visible_by_path(c, LEVEL_SCALING_ELIXIR_PATH) for c in scale_clients]
    )
    missing = [scale_indices[i] + 1 for i, active in enumerate(elixir_results) if not active]
    if missing:
        machine.log(
            f"FAILURE: level-scaling elixir not active on client(s) {missing}",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log("SUCCESS: level scaling applied")
    return "SUCCESS"


async def _all_menu_visible(clients: list) -> bool:
    """Return True when LEVEL_SCALING_OPTION_BUTTON_PATH is visible on all clients."""
    results = await asyncio.gather(
        *[node_helpers.is_visible_by_path(c, LEVEL_SCALING_OPTION_BUTTON_PATH) for c in clients]
    )
    return all(results)


async def _none_menu_visible(clients: list) -> bool:
    """Return True when LEVEL_SCALING_OPTION_BUTTON_PATH is not visible on any client."""
    results = await asyncio.gather(
        *[node_helpers.is_visible_by_path(c, LEVEL_SCALING_OPTION_BUTTON_PATH) for c in clients]
    )
    return not any(results)
