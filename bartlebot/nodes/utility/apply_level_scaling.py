"""Node: apply level-scaling elixir via NPC interaction."""

import asyncio
import logging

from wizwalker import XYZ

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

# The elixir itself is handle_level_scaling's, which outlives this node: the library is
# moving to it (#59), and this node goes once no bot uses it.
from bartlebot.nodes.utility.handle_level_scaling import (
    LEVEL_SCALING_PLAYERS_OPTION,
    SAFETY_DELAY_OPTION,
    apply_elixir,
)

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="zone",
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
]

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
    required_bot_options=[SAFETY_DELAY_OPTION, LEVEL_SCALING_PLAYERS_OPTION],
    # Each tick already retries every player up to MAX_RETRIES rounds, so a FAILURE that
    # survives a few more ticks is not going to clear by itself.
    max_consecutive_retries=3,
)


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

    numbered = [(i + 1, c) for i, c in zip(scale_indices, scale_clients)]
    return "SUCCESS" if await apply_elixir(machine, numbered, interact, reset, safety_delay) else "FAILURE"
