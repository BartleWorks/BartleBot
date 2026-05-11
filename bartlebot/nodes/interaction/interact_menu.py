"""Node: interact with an NPC or world object via the X key and confirm a menu opened."""

import asyncio
import logging

from wizwalker import XYZ, Keycode

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import MAX_RETRIES, POLL_INTERVAL, TRIGGER_TIMEOUT

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
        help="Coordinates of the item to interact with. The interact prompt should appear when standing here.",
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
    ParamField(
        key="menu_path",
        type="str_list",
        label="Menu Path",
        help=(
            "Window-tree path to the UI element that becomes visible when the menu opens. "
            "Polled after pressing X to confirm the interaction succeeded. "
            "Use paths from bartlebot/nodes/paths.py when possible."
        ),
        required=True,
    ),
    ParamField(
        key="menu_timeout",
        type="float",
        label="Menu Timeout (s)",
        help="Seconds to wait for the menu path to become visible after pressing X.",
        default=TRIGGER_TIMEOUT,
        default_hint=f"{TRIGGER_TIMEOUT}s",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Interacts with an NPC or world object (lever, vendor, service NPC, etc.) and confirms "
        "that a specific UI menu opened. Unlike the interact node, this node does not advance "
        "dialogue — use it when the interaction opens a menu window rather than a dialogue box."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Interaction complete and menu path became visible on all active players."),
        ReturnSpec(
            "FAILURE",
            "Players not in zone, interact prompt never appeared after retries, "
            "or menu path did not become visible within menu_timeout.",
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
    usage_notes=(
        "Use this node when pressing X opens a shop, NPC services menu, or any other UI window. "
        "For interactions that trigger dialogue, use the interact node instead. "
        "Set menu_path to a path from bartlebot/nodes/paths.py that is only visible when the "
        "target menu is open (e.g. POTION_SHOP_BASE_PATH, LEVEL_SCALING_OPTION_BUTTON_PATH)."
    ),
)


async def run(machine, clients: list, params: dict) -> str:
    zone: str = params["zone"]
    interact = XYZ(**params["interact_xyz"])
    reset = XYZ(**params["reset_xyz"])
    menu_path: list[str] = params.get("menu_path", [])
    menu_timeout: float = params.get("menu_timeout", TRIGGER_TIMEOUT)

    machine.log(f"interact_menu: {zone!r} XYZ({interact.x:.1f}, {interact.y:.1f}, {interact.z:.1f})")

    # 1. Verify all clients are in zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])

    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if not all(node_helpers.zone_matches(z, zone) for z in zones):
        wrong = [z for z in zones if not node_helpers.zone_matches(z, zone)]
        machine.log(
            f"FAILURE: clients not in expected zone (expected={zone!r}, unexpected={wrong})",
            logging.WARNING,
        )
        return "FAILURE"

    # 2. Resolve which clients should perform the interaction.
    raw: list[int] = params.get("interact_clients") or []
    interact_indices = [n - 1 for n in raw if 0 < n <= len(clients)] if raw else list(range(len(clients)))
    if not interact_indices:
        machine.log("interact_menu: no clients selected — SUCCESS")
        return "SUCCESS"

    active_clients = [clients[i] for i in interact_indices]
    machine.log(f"Interacting client(s): {[i + 1 for i in interact_indices]}", logging.DEBUG)

    safety_delay: float = machine.get_option("Safety Delay", 3.0)

    # 3. Clear any pre-existing interact prompt before starting.
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

    # 4. Retry loop: teleport to interact_xyz, wait for the prompt, reset if it doesn't appear.
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

        machine.log(f"Interact prompt did not appear (attempt {attempt}); resetting", logging.DEBUG)
        await asyncio.gather(*[c.teleport(reset) for c in active_clients])

    if not prompt_appeared:
        machine.log(
            f"FAILURE: interact prompt never appeared after {MAX_RETRIES} attempts",
            logging.WARNING,
        )
        return "FAILURE"

    # 5. Press X to begin interaction.
    machine.log("Pressing X to interact", logging.DEBUG)
    await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in active_clients])

    # 6. Wait for the menu to open on all active clients.
    machine.log(f"Waiting for menu path {'→'.join(menu_path)}…", logging.DEBUG)

    menu_visible = await asyncio.gather(
        *[
            node_helpers.poll_until(
                lambda c=c: node_helpers.is_visible_by_path(c, menu_path), menu_timeout, POLL_INTERVAL
            )
            for c in active_clients
        ]
    )

    if not all(menu_visible):
        machine.log(
            f"FAILURE: menu path not visible within {menu_timeout}s",
            logging.WARNING,
        )
        for i, (client, visible) in enumerate(zip(active_clients, menu_visible)):
            if not visible:
                diag = await node_helpers.diagnose_path(client, menu_path)
                machine.log(
                    f"  client {interact_indices[i] + 1}: looking for {'→'.join(menu_path)!r} — {diag}",
                    logging.DEBUG,
                )
        return "FAILURE"

    machine.log("interact_menu: SUCCESS")
    return "SUCCESS"
