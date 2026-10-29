"""Node: dismiss all dialogue and wait for the conversation to fully conclude."""

import logging

from bartleby.runtime.timing import INTERACT_ANIMATION_DELAY

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="timeout",
        type="float",
        label="Timeout (s)",
        help="The longest to wait before failing. 0 means no limit.",
        default=60.0,
        default_hint="60 seconds",
    ),
    ParamField(
        key="animation_delay",
        type="float",
        label="Animation Delay (s)",
        help=(
            "Extra seconds of quiet to wait for, on top of Safety Delay, so a pause between two "
            "dialogue windows isn't taken for the end."
        ),
        default=INTERACT_ANIMATION_DELAY,
        minimum=0.0,
        default_hint="none",
        required=False,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Clicks through dialogue on every player until none has appeared for Safety Delay plus Animation Delay."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "No player has had dialogue for the whole quiet period."),
        ReturnSpec("FAILURE", "Timeout ran out while a player still had dialogue."),
    ],
    required_bot_options=[
        SAFETY_DELAY_OPTION,
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
