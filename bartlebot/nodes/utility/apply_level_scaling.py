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
        help="Zone name all players must be in when this node executes (the level-scaling dungeon).",
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
    minimum=0.0,
    suffix=" s",
    description=(
        "Seconds to allow for game-server responses when waiting for prompts or menus. "
        "Increase on high-latency connections."
    ),
)

_LEVEL_SCALING_CLIENTS_OPTION = BotOptionSpec(
    name="Level Scaling Players",
    type="player_list",
    default=[],
    description="Which players to check/apply level scaling. Empty = no players. Shared by check_level_scaling and apply_level_scaling.",
)

NODE_SPEC = NodeSpec(
    description=(
        "Interacts with the level-scaling NPC to apply the level-scaling elixir, one "
        "player at a time, retrying anyone the elixir did not reach. Assumes players "
        "are already in the correct dungeon zone."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Level-scaling elixir applied to all specified players."),
        ReturnSpec(
            "FAILURE",
            "Players not in zone, or one or more players still lack the elixir "
            "after every retry (interact prompt never appeared, NPCServicesWin option "
            "button never became visible, or the elixir did not take effect).",
        ),
    ],
    required_bot_options=[_SAFETY_DELAY_OPTION, _LEVEL_SCALING_CLIENTS_OPTION],
)

from bartlebot.nodes.timing import LEVEL_SCALING_STAGGER_DELAY, MAX_RETRIES, POLL_INTERVAL


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.is_in_npc_range(),
          client.send_key(Keycode.X), node_helpers.is_visible_by_path(), node_helpers.click_by_path()

    Flow:
      1. Verify all clients are in zone. Return FAILURE if not.
      2. Resolve scale_clients from the Level Scaling Players bot option (1-based).
         Return SUCCESS immediately if no clients are configured.
      3. Clear any pre-existing interact prompt by teleporting to reset_xyz.
      4. Retry loop (up to MAX_RETRIES) over the clients still missing the elixir:
         a. Use the station one client at a time (see _use_station), pausing
            LEVEL_SCALING_STAGGER_DELAY between clients. The whole interaction is
            sequential, not just the click: clients using the station at once
            intermittently miss the elixir, and one client clicking while others have
            the menu open closes it for all of them.
         b. Wait Safety Delay seconds for the elixirs to apply server-side.
         c. Read the elixir indicator on every client in this round. Anyone who has it
            is done; anyone who does not is teleported to reset_xyz and retried.
      5. Return SUCCESS once every client has the elixir, FAILURE if any client still
         lacks it after MAX_RETRIES rounds.

    A missing prompt or menu is logged and pushed through rather than failing the node:
    the indicator check decides who still needs the station, so a swallowed keypress and
    a click that did not take are recovered by the same retry.

    FAILURE: clients not in zone; one or more clients still without the elixir after
    MAX_RETRIES rounds.
    """
    zone: str = params["zone"]
    interact = XYZ(**params["interact_xyz"])
    reset = XYZ(**params["reset_xyz"])

    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    machine.log(f"apply_level_scaling: {zone!r} XYZ({interact.x:.1f}, {interact.y:.1f}, {interact.z:.1f})")

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

    # 2. Resolve clients_to_scale.
    raw: list[int] = machine.get_option("Level Scaling Players", []) or []
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

    # 4. Retry rounds over the clients that still need the elixir. One client uses the
    #    station from start to finish before the next one opens it: an open menu closes
    #    on every client that has not clicked yet as soon as one of them does, so the
    #    whole interaction has to be sequential, not just the clicks.
    pending = list(zip(scale_indices, scale_clients))
    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(
            f"Attempt {attempt}/{MAX_RETRIES}: applying elixir to client(s) {[i + 1 for i, _ in pending]}",
            logging.DEBUG,
        )

        for position, (index, client) in enumerate(pending):
            if position:
                await asyncio.sleep(LEVEL_SCALING_STAGGER_DELAY)
            await _use_station(machine, client, index + 1, interact, safety_delay)

        # Give the elixirs time to apply server-side before reading the indicator.
        machine.log(f"Waiting {safety_delay}s for elixir to apply…", logging.DEBUG)
        await asyncio.sleep(safety_delay)

        machine.log("Verifying level-scaling elixir is active…", logging.DEBUG)
        elixir_results = await asyncio.gather(
            *[node_helpers.is_visible_by_path(c, LEVEL_SCALING_ELIXIR_PATH) for _, c in pending]
        )
        pending = [entry for entry, active in zip(pending, elixir_results) if not active]
        if not pending:
            machine.log("SUCCESS: level scaling applied")
            return "SUCCESS"

        # Step out of NPC range so the next round's teleport re-triggers the prompt.
        machine.log(
            f"Elixir not active on client(s) {[i + 1 for i, _ in pending]}; resetting to retry",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for _, c in pending])

    machine.log(
        f"FAILURE: level-scaling elixir not active on client(s) {[i + 1 for i, _ in pending]} "
        f"after {MAX_RETRIES} attempts",
        logging.WARNING,
    )
    return "FAILURE"


async def _use_station(machine, client, client_number: int, interact: XYZ, safety_delay: float) -> None:
    """Walk one client through a single use of the level-scaling station.

    Teleports to the NPC, presses X, clicks the elixir option, and waits for the menu to
    close again before returning, so no other client is sitting in the menu meanwhile.

    Reports problems and returns instead of raising — whether the elixir actually landed
    is decided by the caller's indicator check, which also drives the retry, so there is
    nothing useful to distinguish here between "the menu never opened" and "the click did
    not take".
    """
    await client.teleport(interact)

    machine.log(f"Client {client_number}: waiting for interact prompt…", logging.DEBUG)
    if not await node_helpers.poll_until(lambda: node_helpers.all_in_npc_range([client]), safety_delay, POLL_INTERVAL):
        machine.log(f"Client {client_number}: interact prompt did not appear", logging.DEBUG)
        return

    machine.log(f"Client {client_number}: pressing X to interact with level-scaling NPC", logging.DEBUG)
    await client.send_key(Keycode.X, 0.1)

    machine.log(f"Client {client_number}: waiting for NPCServicesWin menu…", logging.DEBUG)
    if not await node_helpers.poll_until(lambda: _all_menu_visible([client]), safety_delay, POLL_INTERVAL):
        machine.log(
            f"Client {client_number}: NPCServicesWin option button never appeared within {safety_delay}s",
            logging.DEBUG,
        )
        return

    machine.log(f"Client {client_number}: clicking level-scaling option button", logging.DEBUG)
    await node_helpers.click_by_path(client, LEVEL_SCALING_OPTION_BUTTON_PATH)

    machine.log(f"Client {client_number}: waiting for NPCServicesWin menu to dismiss…", logging.DEBUG)
    await node_helpers.poll_until(lambda: _none_menu_visible([client]), safety_delay, POLL_INTERVAL)


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
