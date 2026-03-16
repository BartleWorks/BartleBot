"""Node: apply level-scaling elixir via NPC interaction."""

import asyncio

from wizwalker import Keycode, XYZ

from bartlebot.log_level import LogLevel
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import LEVEL_SCALING_OPTION_BUTTON_PATH

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
        help=(
            "Coordinates of the level-scaling NPC. "
            "The interact prompt should appear when standing here."
        ),
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
        key="clients_to_scale",
        type="int_list",
        label="Clients to Scale",
        help=(
            "1-based client numbers to apply level scaling to (e.g. '2, 3'). "
            "Leave empty to scale all clients. Client numbers exceeding the number "
            "of connected clients are silently skipped."
        ),
        default=[],
        default_hint="Default: scales all clients",
    ),
]

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
            "or NPCServicesWin option button never became visible.",
        ),
    ],
)

PROMPT_TIMEOUT = 5.0
MENU_TIMEOUT = 10.0
POLL_INTERVAL = 0.25
MAX_RETRIES = 3


async def run(machine, clients: list, params: dict) -> str:
    """Apply the level-scaling elixir via NPC interaction."""
    zone: str = params["zone"]
    interact = XYZ(**params["interact_xyz"])
    reset = XYZ(**params["reset_xyz"])

    machine.log(
        f"apply_level_scaling: {zone!r} "
        f"XYZ({interact.x:.1f}, {interact.y:.1f}, {interact.z:.1f})"
    )

    # 1. Verify all clients are in zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", LogLevel.DEBUG)
    if not all(z == zone for z in zones):
        wrong = [z for z in zones if z != zone]
        machine.log(
            f"FAILURE: clients not in expected zone "
            f"(expected={zone!r}, unexpected={wrong})",
            LogLevel.WARNING,
        )
        return "FAILURE"

    # 2. Resolve clients_to_scale.
    raw: list[int] = params.get("clients_to_scale") or []
    scale_indices = (
        [n - 1 for n in raw if 0 < n <= len(clients)]
        if raw
        else list(range(len(clients)))
    )
    if not scale_indices:
        machine.log("apply_level_scaling: no clients to scale — SUCCESS")
        return "SUCCESS"

    scale_clients = [clients[i] for i in scale_indices]
    machine.log(f"Scaling client(s): {[i + 1 for i in scale_indices]}", LogLevel.DEBUG)

    # 3. Clear any pre-existing interact prompt on the scaled clients.
    prompts = await asyncio.gather(*[c.is_in_npc_range() for c in scale_clients])
    if any(prompts):
        stale = [scale_indices[i] for i, p in enumerate(prompts) if p]
        machine.log(
            f"Pre-existing interact prompt on client(s) {[i + 1 for i in stale]}; "
            "teleporting to reset_xyz to clear it",
            LogLevel.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for c in scale_clients])
        await node_helpers.wait_until(
            lambda: node_helpers.none_in_npc_range(scale_clients), PROMPT_TIMEOUT, POLL_INTERVAL
        )

    # 4-5. Retry loop: teleport to interact_xyz, wait for prompt.
    prompt_appeared = False
    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(
            f"Attempt {attempt}/{MAX_RETRIES}: teleporting to interact_xyz", LogLevel.DEBUG
        )
        await asyncio.gather(*[c.teleport(interact) for c in scale_clients])

        machine.log("Waiting for interact prompt…", LogLevel.DEBUG)
        if await node_helpers.wait_until(
            lambda: node_helpers.all_in_npc_range(scale_clients), PROMPT_TIMEOUT, POLL_INTERVAL
        ):
            prompt_appeared = True
            break

        machine.log(
            f"Interact prompt did not appear (attempt {attempt}); resetting",
            LogLevel.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for c in scale_clients])

    if not prompt_appeared:
        machine.log(
            f"FAILURE: interact prompt never appeared after {MAX_RETRIES} attempts",
            LogLevel.WARNING,
        )
        return "FAILURE"

    # 6. Press X on all scaled clients simultaneously.
    machine.log("Pressing X to interact with level-scaling NPC", LogLevel.DEBUG)
    await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in scale_clients])

    # 7. Wait for NPCServicesWin option button to appear.
    machine.log("Waiting for NPCServicesWin menu…", LogLevel.DEBUG)
    if not await node_helpers.wait_until(
        lambda: _all_menu_visible(scale_clients), MENU_TIMEOUT, POLL_INTERVAL
    ):
        machine.log(
            f"FAILURE: NPCServicesWin option button never appeared within {MENU_TIMEOUT}s",
            LogLevel.WARNING,
        )
        return "FAILURE"

    # 8. Click option button on all scaled clients simultaneously.
    machine.log("Clicking level-scaling option button", LogLevel.DEBUG)
    await asyncio.gather(
        *[node_helpers.click_by_path(c, LEVEL_SCALING_OPTION_BUTTON_PATH) for c in scale_clients]
    )

    # 9. Wait for menu to dismiss.
    machine.log("Waiting for NPCServicesWin menu to dismiss…", LogLevel.DEBUG)
    await node_helpers.wait_until(
        lambda: _none_menu_visible(scale_clients), MENU_TIMEOUT, POLL_INTERVAL
    )

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
