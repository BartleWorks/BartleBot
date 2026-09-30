"""Node: dismiss all dialogue and wait for the conversation to fully conclude."""

import logging

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import INTERACT_ANIMATION_DELAY
from bartlebot.option_groups import GROUP_GENERAL

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="timeout",
        type="float",
        label="Timeout (seconds, 0 = no limit)",
        help=(
            "Maximum seconds to wait before returning FAILURE. "
            "Set to 0 to wait indefinitely (mirrors Deimos waitforfree behavior)."
        ),
        default=60.0,
        default_hint="60s",
    ),
    ParamField(
        key="animation_delay",
        type="float",
        label="Animation Delay (s)",
        help=(
            "Extra seconds to wait for an animation or cutscene, on top of the Safety "
            "Delay. Added to the quiet period this node requires before it calls the "
            "conversation finished, so a pause between two dialogue windows is not "
            "mistaken for the end of it. Additive rather than a replacement: the "
            "animation takes the same time on every connection, while Safety Delay is "
            "the slack this player's connection needs on top of it."
        ),
        default=INTERACT_ANIMATION_DELAY,
        minimum=0.0,
        default_hint="No animation to wait out.",
        required=False,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Dismisses dialogue on all players. Polls for at least Safety Delay (plus Animation Delay) seconds; "
        "if no player has dialogue for the full delay, returns SUCCESS. Each time dialogue is dismissed, the "
        "timer resets, preventing premature exit between consecutive dialogue windows. Set timeout to 0 for "
        "no time limit."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players have been dialogue-free for at least the quiet period."),
        ReturnSpec("FAILURE", "The timeout expired while players were still in dialogue."),
    ],
    required_bot_options=[
        BotOptionSpec(
            name="Safety Delay",
            group=GROUP_GENERAL,
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
    max_consecutive_retries=None,
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: SprintyClient.is_in_dialog(), node_helpers.click_by_path()

    Flow:
      1. Poll for Safety Delay + Animation Delay seconds. If no dialogue appears, return SUCCESS.
      2. When dialogue is seen on any client, dismiss it and reset the quiet timer.
      3. Repeat until the quiet period has elapsed with no dialogue on any client.
      4. If timeout > 0 and it expires while clients are still in dialogue, return FAILURE.

    FAILURE: timeout expired before all dialogue cleared
    (unreachable when timeout=0).
    """
    timeout_raw: float = params.get("timeout", 60.0)
    timeout_val: float | None = None if timeout_raw == 0.0 else timeout_raw
    safety_delay: float = machine.get_option("Safety Delay")
    # The animation is a property of the scene and the connection slack is a property of
    # the machine, so the quiet period has to cover both rather than pick one.
    quiet: float = safety_delay + float(params.get("animation_delay") or INTERACT_ANIMATION_DELAY)

    if await node_helpers.advance_dialogue_until_clear(clients, quiet, timeout_val):
        machine.log(f"wait_for_dialogue: SUCCESS — dialogue clear for {quiet}s", logging.DEBUG)
        return "SUCCESS"
    machine.log(f"wait_for_dialogue: FAILURE — timed out after {timeout_raw}s", logging.WARNING)
    return "FAILURE"
