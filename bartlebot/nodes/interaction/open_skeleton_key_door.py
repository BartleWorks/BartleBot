"""Node: unlock a skeleton key door, which one player with a key is enough to open."""

import asyncio
import logging

from wizwalker import XYZ

from bartlebot.clients import game_keys
from bartlebot.game.key_bindings import GameAction
from bartlebot.nodes import node_helpers
from bartlebot.nodes.help_text import INTERACT_XYZ, RESET_XYZ, ZONE_AT_START
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import DIALOG_TIMEOUT, MAX_RETRIES, POLL_INTERVAL, TRIGGER_TIMEOUT

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
        help=RESET_XYZ,
        default=None,
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description="Unlocks a skeleton key door with a key from any one player who has one.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "The interact prompt went away after at least one player pressed X."),
        ReturnSpec(
            "FAILURE",
            "A player wasn't in Zone, no player got the interact prompt (none has a key, or the door is "
            "already open), the prompt stayed after X, or dialogue didn't close in time.",
        ),
    ],
    required_bot_options=[
        SAFETY_DELAY_OPTION,
    ],
    max_consecutive_retries=5,
    usage_notes=(
        "Use it in place of Interact for a skeleton key door, in a skeleton key boss dungeon or a side "
        "room of an ordinary one. A key opens the door for everyone in the instance, so one player's "
        "key is enough.\n\n"
        "Every player goes to the door, and every player who gets the interact prompt presses X. A "
        "player without a key gets no prompt and is skipped, so the node succeeds as long as one player "
        "has a key. Only one key is used: the door opens for everyone at once.\n\n"
        "Follow it with Enter Zone for the room behind the door. A player who was skipped walks "
        "through like the rest.\n\n"
        "Route FAILURE to a recovery state or to the end of the bot. If the node runs a second time "
        "after the door is already open, no player gets a prompt and it fails."
    ),
    see_also=["interact", "enter_zone"],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.is_in_npc_range(),
          game_keys.press(client, GameAction.INTERACT)

    Flow:
      1. Verify all clients are in zone. Return FAILURE if not.
      2. Clear any pre-existing interact prompt by teleporting everyone to reset_xyz.
      3. Teleport everyone to interact_xyz until the prompt shows on at least one client
         (up to MAX_RETRIES). A player with no key never gets it, so waiting for every
         client would cost each attempt its full Safety Delay.
      4. Press X once on each client that has the prompt.
      5. Wait up to TRIGGER_TIMEOUT for a prompt to go away: the door opens for everyone, so
         the first prompt to go is the proof. Clients are not re-pressed, since a second X on
         a door that just opened would cost another key.
      6. Click through any dialogue the door opened.

    FAILURE: clients not in zone; no client got the prompt after MAX_RETRIES; every prompt stayed
    after X; dialogue did not clear within DIALOG_TIMEOUT.
    """
    zone: str = params["zone"]
    interact = XYZ(**params["interact_xyz"])
    reset = XYZ(**params["reset_xyz"])

    machine.log(f"open_skeleton_key_door: {zone!r} XYZ({interact.x:.1f}, {interact.y:.1f}, {interact.z:.1f})")

    # 1. Verify all clients are in zone.
    if not await node_helpers.in_expected_zone(machine, clients, zone, POLL_INTERVAL):
        return "FAILURE"

    safety_delay: float = machine.get_option("Safety Delay", 3.0)

    # 2–3. Clear a stale prompt, then teleport to interact_xyz until it shows on any client.
    #      A miss is the node's own failure to word, so the helper is told not to log one.
    if not await node_helpers.clear_stale_prompt(
        machine, clients, reset, node_helpers.INTERACT_PROMPT, safety_delay, POLL_INTERVAL
    ):
        return "FAILURE"
    if not await node_helpers.reach_prompt(
        machine,
        clients,
        interact,
        reset,
        node_helpers.INTERACT_PROMPT,
        safety_delay,
        POLL_INTERVAL,
        fail_on_miss=False,
        clear_first=False,
        any_client=True,
    ):
        machine.log(
            f"FAILURE: no player got the interact prompt after {MAX_RETRIES} attempts — "
            "none has a skeleton key, or the door is already open",
            logging.WARNING,
        )
        return "FAILURE"

    # 4. Press X for whoever has the prompt. The rest hold no key (or are late): not a failure.
    prompted = await asyncio.gather(*[c.is_in_npc_range() for c in clients])
    ready = [c for c, p in zip(clients, prompted) if p]
    if not ready:
        # The prompt showed and went again before it was read. Nothing has pressed X, so the
        # door is not known to be open; fail and let the route decide.
        machine.log("FAILURE: the interact prompt went away before X was pressed", logging.WARNING)
        return "FAILURE"
    skipped = [i + 1 for i, p in enumerate(prompted) if not p]
    if skipped:
        machine.log(f"Player(s) {skipped} got no interact prompt; continuing without them", logging.INFO)
    machine.log(f"Pressing X to unlock the door ({len(ready)} player(s))", logging.DEBUG)
    await asyncio.gather(*[game_keys.press(c, GameAction.INTERACT, 0.1) for c in ready])

    # 5. The door opening takes the prompt from everyone; one client losing it is enough.
    async def _a_prompt_went() -> bool:
        return not await node_helpers.all_in_npc_range(ready)

    if not await node_helpers.poll_until(_a_prompt_went, TRIGGER_TIMEOUT, POLL_INTERVAL):
        machine.log(
            f"FAILURE: the interact prompt stayed on all {len(ready)} player(s) {TRIGGER_TIMEOUT:.0f}s after X",
            logging.WARNING,
        )
        return "FAILURE"

    # 6. Click through anything the door said.
    machine.log("Advancing dialogue…", logging.DEBUG)
    if not await node_helpers.advance_dialogue_until_clear(clients, safety_delay, DIALOG_TIMEOUT):
        machine.log(f"FAILURE: dialogue did not clear within {DIALOG_TIMEOUT}s", logging.WARNING)
        return "FAILURE"

    machine.log("SUCCESS: door unlocked")
    return "SUCCESS"
