"""Node: interact with an NPC or world object via the X key and confirm a menu opened."""

import asyncio
import logging

from wizwalker import XYZ, Keycode

from bartlebot.nodes import node_helpers
from bartlebot.nodes.help_text import (
    INTERACT_PLAYERS,
    INTERACT_XYZ,
    OBJECT_USE_DELAY_HELP,
    RESET_XYZ,
    SEQUENTIAL_INTERACTION,
    SEQUENTIAL_INTERACTION_HINT,
    ZONE_AT_START,
    seconds,
)
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import MAX_RETRIES, OBJECT_USE_DELAY, POLL_INTERVAL, TRIGGER_TIMEOUT

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="zone",
        label="Zone",
        help=ZONE_AT_START,
        default="",
        required=True,
    ),
    ParamField(
        key="interact_xyz",
        type="xyz",
        label="Interact XYZ",
        help=INTERACT_XYZ,
        default=None,
        required=True,
    ),
    ParamField(
        key="reset_xyz",
        type="xyz",
        label="Reset XYZ",
        help=(RESET_XYZ),
        default=None,
        required=True,
    ),
    ParamField(
        key="interact_clients",
        type="player_list",
        label="Interact Players",
        help=INTERACT_PLAYERS,
        default=[],
        required=True,
    ),
    ParamField(
        key="menu_path",
        type="str_list",
        label="Menu Path",
        help="The UI path of an element that appears when the menu opens. The node waits for it after pressing X.",
        required=True,
    ),
    ParamField(
        key="menu_timeout",
        type="float",
        label="Menu Timeout (s)",
        help="How long to wait for Menu Path after pressing X.",
        default=TRIGGER_TIMEOUT,
        default_hint=seconds(TRIGGER_TIMEOUT),
    ),
    ParamField(
        key="sequential",
        type="bool",
        label="Sequential Interaction",
        help=(SEQUENTIAL_INTERACTION),
        default=False,
        default_hint=SEQUENTIAL_INTERACTION_HINT,
        required=False,
    ),
    ParamField(
        key="object_use_delay",
        type="float",
        label="Delay Between Object Uses (s)",
        help=(OBJECT_USE_DELAY_HELP),
        default=OBJECT_USE_DELAY,
        minimum=0.0,
        default_hint=seconds(OBJECT_USE_DELAY),
        required=False,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Interacts with an NPC or object that opens a menu, such as a shop, and checks that the "
        "menu opened. For an interaction that starts dialogue, use Interact."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Menu Path is visible for every player who interacted."),
        ReturnSpec(
            "FAILURE",
            (
                "A player wasn't in Zone, the interact prompt never appeared, or Menu Path didn't appear "
                "within Menu Timeout."
            ),
        ),
    ],
    required_bot_options=[
        SAFETY_DELAY_OPTION,
    ],
    max_consecutive_retries=10,
    usage_notes=(
        "Set Menu Path to an element that's visible only while the menu is open. The UI Tree Browser shows its path."
        + "\n\n"
        + (
            "Sequential Interaction checks each player's menu in turn. On an object that serves one "
            "player at a time, the previous player's menu may close as the next one opens."
        )
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
    if not await node_helpers.in_expected_zone(machine, clients, zone, POLL_INTERVAL):
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
