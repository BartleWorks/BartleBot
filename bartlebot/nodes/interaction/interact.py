"""Node: interact with an NPC or world object via the X key."""

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
    SEQUENTIAL_INTERACTION_NOTE,
    ZONE_AT_START,
    seconds,
)
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import (
    DIALOG_TIMEOUT,
    INTERACT_ANIMATION_DELAY,
    MAX_RETRIES,
    OBJECT_USE_DELAY,
    POLL_INTERVAL,
)

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
        help=(INTERACT_XYZ),
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
        key="has_dialogue",
        type="bool",
        label="Has Dialogue",
        help=(
            "Turn off for an interaction without dialogue, such as a lever, so the node doesn't wait "
            "for any. Unexpected dialogue is still clicked through."
        ),
        default=True,
        required=False,
    ),
    ParamField(
        key="animation_delay",
        type="float",
        label="Animation Delay (s)",
        help="Extra seconds to wait for an animation or cutscene before dialogue opens, on top of Safety Delay.",
        default=INTERACT_ANIMATION_DELAY,
        minimum=0.0,
        default_hint="none",
        required=False,
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
    description="Interacts with an NPC or object and clicks through any dialogue that follows.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Every player interacted, and all dialogue is closed."),
        ReturnSpec(
            "FAILURE",
            "The interact prompt never appeared, or dialogue didn't close in time.",
        ),
    ],
    required_bot_options=[
        SAFETY_DELAY_OPTION,
    ],
    max_consecutive_retries=10,
    usage_notes=(SEQUENTIAL_INTERACTION_NOTE),
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.is_in_npc_range(),
          client.send_key(Keycode.X), client.is_in_dialog()

    Flow:
      1. Verify all clients are in zone. Return FAILURE if not.
      2. Resolve active_clients from interact_clients param (default: all clients).
         Return SUCCESS immediately if the resolved set is empty.
      2s. If sequential, hand the whole interaction to node_helpers.take_turns: each client
         approaches, presses X and advances its own dialogue alone before the next starts.
         Steps 3–6 are the parallel path and are skipped.
      3. Clear any pre-existing interact prompt by teleporting to reset_xyz.
      4. Retry loop (up to MAX_RETRIES): teleport to interact_xyz, wait for the prompt
         on all active_clients. If it does not appear, teleport to reset_xyz and retry.
         Return FAILURE if the prompt never appeared.
      5. Press X on all active_clients.
      6. Advance dialogue: click the advance button while any client is in_dialog().
         Continue until no client is in dialogue AND the quiet period has elapsed: each
         player gets Safety Delay + Animation Delay for its first window to open (to avoid
         missing one an animation opens late), and Safety Delay alone after its last page.
         Return FAILURE if
         DIALOG_TIMEOUT expires.

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
        machine.log("interact: no clients selected — SUCCESS")
        return "SUCCESS"

    active_clients = [clients[i] for i in interact_indices]
    machine.log(f"Interacting client(s): {[i + 1 for i in interact_indices]}", logging.DEBUG)

    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    has_dialogue: bool = params.get("has_dialogue", True)
    # The animation is a property of the object and the connection slack is a property of
    # the machine, so the wait for the first window has to cover both rather than pick one.
    # The animation plays once per player, before that player's window opens: after its last
    # page the node waits the Safety Delay alone. Each player gets the whole wait, since one
    # player's window opening says nothing about another's animation.
    first_dialogue_wait: float = safety_delay + float(params.get("animation_delay") or INTERACT_ANIMATION_DELAY)

    # 3s. Sequential mode: one client uses the object from approach to last dialogue box
    #     before the next one starts. Its dialogue is advanced per-client rather than for
    #     the group, which is the whole point — a client still reading is a client still
    #     holding the object.
    if params.get("sequential", False):
        machine.log("Sequential interaction: clients take turns at the object", logging.DEBUG)

        async def _turn(client) -> bool:
            await client.send_key(Keycode.X, 0.1)
            if not has_dialogue:
                return True
            return await node_helpers.advance_dialogue_until_clear(
                [client], safety_delay, DIALOG_TIMEOUT, first_wait=first_dialogue_wait
            )

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
            machine.log(
                f"FAILURE: dialogue did not clear within {DIALOG_TIMEOUT}s for client(s) {unfinished}",
                logging.WARNING,
            )
            return "FAILURE"
        machine.log("SUCCESS: interaction complete")
        return "SUCCESS"

    # 3–5. Clear a stale prompt, then teleport to interact_xyz until the prompt shows.
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

    # 6. Press X to begin interaction.
    machine.log("Pressing X to interact", logging.DEBUG)
    await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in active_clients])

    # 7. Advance through dialogue; skip entirely for silent interactions.
    if not has_dialogue:
        machine.log("Skipping dialogue wait (has_dialogue=False)", logging.DEBUG)
        machine.log("SUCCESS: interaction complete")
        return "SUCCESS"

    machine.log("Advancing dialogue…", logging.DEBUG)
    if not await node_helpers.advance_dialogue_until_clear(
        active_clients, safety_delay, DIALOG_TIMEOUT, first_wait=first_dialogue_wait
    ):
        machine.log(
            f"FAILURE: dialogue did not clear within {DIALOG_TIMEOUT}s",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log("SUCCESS: interaction complete")
    return "SUCCESS"
