"""Node: interact with an NPC or world object via the X key."""

import asyncio
import logging

from wizwalker import XYZ, Keycode

from bartlebot.nodes import node_helpers
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
        help="Zone name players must currently be in when this node executes.",
        default="",
        required=True,
    ),
    ParamField(
        key="interact_xyz",
        type="xyz",
        label="Interact XYZ",
        help=("Coordinates of the item to interact with. The interact prompt should appear when standing here."),
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
        key="has_dialogue",
        type="bool",
        label="Has Dialogue",
        help=(
            "Whether this interaction produces dialogue. "
            "Set to False for silent interactions (levers, collectables) to skip waiting for "
            "dialogue that will never appear. The ambient watcher will still catch any unexpected dialogue."
        ),
        default=True,
        required=False,
    ),
    ParamField(
        key="animation_delay",
        type="float",
        label="Animation Delay (s)",
        help=(
            "Extra seconds to wait for this interaction's own animation, on top of the "
            "Safety Delay. Applies only before the first dialogue window opens, so a "
            "cutscene that opens it late is not mistaken for silence; once dialogue has "
            "appeared, the node waits the Safety Delay alone after the last page. Additive "
            "rather than a replacement: the animation takes "
            "the same time on every connection, while Safety Delay is the slack this "
            "player's connection needs on top of it."
        ),
        default=INTERACT_ANIMATION_DELAY,
        minimum=0.0,
        default_hint="No animation to wait out.",
        required=False,
    ),
    ParamField(
        key="sequential",
        type="bool",
        label="Sequential Interaction",
        help=(
            "Have players use the object one at a time instead of together. Some objects only "
            "serve one player at a time, and a second player interacting while the first is "
            "mid-interaction has their keypress swallowed. Each player waits at Reset XYZ, "
            "takes its turn, and advances its own dialogue before the next player starts. "
            "Slower — leave off unless this object actually needs it."
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
            "so this is the only cover for it — raise it for an object with a slow cycle, such as a "
            "teleporter that takes several seconds to reset. Ignored unless Sequential Interaction is "
            "on."
        ),
        default=OBJECT_USE_DELAY,
        minimum=0.0,
        default_hint=f"{OBJECT_USE_DELAY}s between uses.",
        required=False,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Interacts with an NPC or world object (lever, collectable, etc.) and advances through any resulting dialogue. "
        "Set Has Dialogue to False for silent interactions (levers, collectables) to skip the dialogue wait entirely."
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
        SAFETY_DELAY_OPTION,
    ],
    max_consecutive_retries=10,
    usage_notes=(
        "Turn on Sequential Interaction when the object refuses to serve more than one player "
        "at a time — the symptom is that one player interacts normally and the others' X press "
        "does nothing, intermittently rather than every run. It costs roughly one full "
        "interaction per extra player, so leave it off otherwise."
    ),
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
         Continue until no client is in dialogue AND the quiet period has elapsed: Safety
         Delay + Animation Delay until the first window opens (to avoid missing one an
         animation opens late), Safety Delay alone after it. Return FAILURE if
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
    # The animation plays once, before that window opens: after the last page the node
    # waits the Safety Delay alone.
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
