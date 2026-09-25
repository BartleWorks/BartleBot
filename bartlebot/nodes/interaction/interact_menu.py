"""Node: interact with an NPC or world object via the X key and confirm a menu opened."""

import asyncio
import logging

from wizwalker import XYZ, Keycode

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import MAX_RETRIES, OBJECT_USE_DELAY, POLL_INTERVAL, TRIGGER_TIMEOUT

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="zone",
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
        default=None,
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
        default=None,
        required=True,
    ),
    ParamField(
        key="interact_clients",
        type="player_list",
        label="Interact Players",
        help="Which players perform this interaction.",
        default=[],
        required=True,
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
    ParamField(
        key="sequential",
        type="bool",
        label="Sequential Interaction",
        help=(
            "Have players open the menu one at a time instead of together. Each player waits "
            "at Reset XYZ, takes its turn, and has its menu confirmed open before the next "
            "player starts. Note that on an object which only serves one player at a time, "
            "the previous player's menu may well close as the next one opens — so this helps "
            "when the interaction itself is being swallowed, but it cannot leave every player "
            "sitting in an open menu at once."
        ),
        default=False,
        default_hint="All players interact at once.",
        required=False,
    ),
    ParamField(
        key="object_use_delay",
        type="float",
        label="Delay Between Object Uses (s)",
        help=(
            "Seconds to leave between one player finishing at the object and the next player starting "
            "on it, in Sequential Interaction. The object becoming free again has no readable signal, "
            "so this is the only cover for it — raise it for an object whose menu takes several "
            "seconds to close and reopen. Ignored unless Sequential Interaction is on."
        ),
        default=OBJECT_USE_DELAY,
        minimum=0.0,
        default_hint=f"{OBJECT_USE_DELAY}s between uses.",
        required=False,
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
            minimum=0.0,
            suffix=" s",
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
        "target menu is open (e.g. POTION_SHOP_BASE_PATH, LEVEL_SCALING_OPTION_BUTTON_PATH). "
        "Turn on Sequential Interaction when the object will not serve more than one player at "
        "once — but read that parameter's help first: it confirms each menu in turn and does "
        "not promise every player is left in an open menu."
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
    interact_indices = [n - 1 for n in raw if 0 < n <= len(clients)]
    if not interact_indices:
        machine.log("interact_menu: no clients selected — SUCCESS")
        return "SUCCESS"

    active_clients = [clients[i] for i in interact_indices]
    machine.log(f"Interacting client(s): {[i + 1 for i in interact_indices]}", logging.DEBUG)

    safety_delay: float = machine.get_option("Safety Delay", 3.0)

    async def _menu_open(client) -> bool:
        return await node_helpers.poll_until(
            lambda: node_helpers.is_visible_by_path(client, menu_path), menu_timeout, POLL_INTERVAL
        )

    async def _report_missing_menu(failed_clients: list, failed_numbers: list[int]) -> None:
        for client, number in zip(failed_clients, failed_numbers):
            diag = await node_helpers.diagnose_path(client, menu_path)
            machine.log(f"  client {number}: looking for {'→'.join(menu_path)!r} — {diag}", logging.DEBUG)

    # 3s. Sequential mode: one client approaches, presses X and has its menu confirmed open
    #     before the next one starts. See the param help — on a genuinely single-serving
    #     object the earlier menus will not stay open, so this confirms each in turn rather
    #     than leaving them all up.
    if params.get("sequential", False):
        machine.log("Sequential interaction: clients take turns at the object", logging.DEBUG)

        async def _turn(client) -> bool:
            await client.send_key(Keycode.X, 0.1)
            return await _menu_open(client)

        numbers = [i + 1 for i in interact_indices]
        results = await node_helpers.take_turns(
            active_clients,
            interact,
            reset,
            safety_delay,
            _turn,
            numbers=numbers,
            poll_interval=POLL_INTERVAL,
            object_use_delay=params.get("object_use_delay"),
            log=machine.log,
        )
        unprompted, unfinished = node_helpers.summarize_turns(results, numbers)
        if unprompted:
            machine.log(
                f"FAILURE: interact prompt never appeared for client(s) {unprompted} after {MAX_RETRIES} attempts",
                logging.WARNING,
            )
            return "FAILURE"
        if unfinished:
            machine.log(f"FAILURE: menu path not visible within {menu_timeout}s", logging.WARNING)
            await _report_missing_menu(
                [c for c, r in zip(active_clients, results) if r.prompted and not r.completed], unfinished
            )
            return "FAILURE"
        machine.log("interact_menu: SUCCESS")
        return "SUCCESS"

    # 3–4. Clear a stale prompt, then teleport to interact_xyz until the prompt shows.
    if not await node_helpers.reach_prompt(
        machine,
        active_clients,
        interact,
        reset,
        node_helpers.INTERACT_PROMPT,
        safety_delay,
        numbers=[i + 1 for i in interact_indices],
    ):
        return "FAILURE"

    # 5. Press X to begin interaction.
    machine.log("Pressing X to interact", logging.DEBUG)
    await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in active_clients])

    # 6. Wait for the menu to open on all active clients.
    machine.log(f"Waiting for menu path {'→'.join(menu_path)}…", logging.DEBUG)

    menu_visible = await asyncio.gather(*[_menu_open(c) for c in active_clients])

    if not all(menu_visible):
        machine.log(
            f"FAILURE: menu path not visible within {menu_timeout}s",
            logging.WARNING,
        )
        await _report_missing_menu(
            [c for c, visible in zip(active_clients, menu_visible) if not visible],
            [i + 1 for i, visible in zip(interact_indices, menu_visible) if not visible],
        )
        return "FAILURE"

    machine.log("interact_menu: SUCCESS")
    return "SUCCESS"
