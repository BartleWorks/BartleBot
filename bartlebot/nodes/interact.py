"""Node: interact with an NPC or world object via the X key."""

import asyncio

from wizwalker import Keycode, XYZ

from bartlebot.log_level import LogLevel
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="interact_xyz",
        type="xyz",
        label="Interact XYZ",
        help=(
            "Coordinates of the item to interact with. "
            "The interact prompt should appear when standing here."
        ),
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

NODE_SPEC = NodeSpec(
    description=(
        "Interacts with an NPC or world object (lever, collectable, etc.) "
        "and advances through any resulting dialogue."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Interaction complete and all dialogue dismissed."),
        ReturnSpec(
            "FAILURE",
            "The interact prompt never appeared after retries, "
            "or dialogue did not clear within the timeout.",
        ),
    ],
)

PROMPT_TIMEOUT = 5.0
DIALOG_TIMEOUT = 60.0
POLL_INTERVAL = 0.25
MAX_RETRIES = 3


async def run(machine, clients: list, params: dict) -> str:
    """
    Interact with an NPC or world object and advance through dialogue.

    Params:
        interact_xyz (dict): {"x", "y", "z"} — coordinates of the interactable.
        reset_xyz (dict):    {"x", "y", "z"} — coordinates used to dismiss the prompt.
    """
    interact = XYZ(**params["interact_xyz"])
    reset = XYZ(**params["reset_xyz"])

    machine.log(
        f"interact: XYZ({interact.x:.1f}, {interact.y:.1f}, {interact.z:.1f})"
    )

    # 1–2. Retry loop: teleport to interact_xyz, wait for the prompt, reset if it doesn't appear.
    prompt_appeared = False
    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(
            f"Attempt {attempt}/{MAX_RETRIES}: teleporting to interact_xyz", LogLevel.DEBUG
        )
        await asyncio.gather(*[c.teleport(interact) for c in clients])

        machine.log("Waiting for interact prompt…", LogLevel.DEBUG)
        if await node_helpers.wait_until(
            lambda: _all_in_npc_range(clients), PROMPT_TIMEOUT, POLL_INTERVAL
        ):
            prompt_appeared = True
            break

        machine.log(
            f"Interact prompt did not appear (attempt {attempt}); resetting",
            LogLevel.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for c in clients])

    if not prompt_appeared:
        machine.log(
            f"FAILURE: interact prompt never appeared after {MAX_RETRIES} attempts",
            LogLevel.WARNING,
        )
        return "FAILURE"

    # 3. Press X to begin interaction.
    machine.log("Pressing X to interact", LogLevel.DEBUG)
    await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in clients])

    # 4. Advance through dialogue until all clients have dismissed it.
    machine.log("Advancing dialogue…", LogLevel.DEBUG)
    loop_start = asyncio.get_event_loop().time()
    while True:
        dialogs = await asyncio.gather(*[c.is_in_dialog() for c in clients])
        if not any(dialogs):
            break
        if asyncio.get_event_loop().time() - loop_start > DIALOG_TIMEOUT:
            machine.log(
                f"FAILURE: dialogue did not clear within {DIALOG_TIMEOUT}s",
                LogLevel.WARNING,
            )
            return "FAILURE"
        await asyncio.gather(*[
            c.send_key(Keycode.SPACEBAR, 0.1) for c, d in zip(clients, dialogs) if d
        ])
        await asyncio.sleep(POLL_INTERVAL)

    machine.log("SUCCESS: interaction complete")
    return "SUCCESS"


async def _all_in_npc_range(clients: list) -> bool:
    """Return True when every client has the interact prompt showing."""
    results = await asyncio.gather(*[c.is_in_npc_range() for c in clients])
    return all(results)
