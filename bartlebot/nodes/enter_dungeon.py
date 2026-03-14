"""Node: enter a dungeon via its sigil."""

import asyncio

from wizwalker import Keycode, XYZ

from bartlebot.log_level import LogLevel
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="from_zone",
        type="str",
        label="From Zone",
        help="Zone name the clients must currently be in when this node executes.",
        default="",
        required=True,
    ),
    ParamField(
        key="dungeon_zone",
        type="str",
        label="Dungeon Zone",
        help="Zone name all clients must be in after successfully entering the dungeon.",
        default="",
        required=True,
    ),
    ParamField(
        key="sigil_xyz",
        type="xyz",
        label="Sigil XYZ",
        help=(
            "Coordinates of the dungeon sigil. "
            "Standing here should trigger the dungeon entrance prompt."
        ),
        default={"x": 0.0, "y": 0.0, "z": 0.0},
        required=True,
    ),
    ParamField(
        key="reset_xyz",
        type="xyz",
        label="Reset XYZ",
        help=(
            "Coordinates used to dismiss the entrance prompt and reset state. "
            "The prompt must NOT appear when standing here."
        ),
        default={"x": 0.0, "y": 0.0, "z": 0.0},
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Enters a dungeon via its sigil. Handles prompt detection, "
        "dungeon warning confirmation, and client zone desynchronization."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All clients are now in dungeon_zone."),
        ReturnSpec(
            "FAILURE",
            "Clients were not in from_zone at the start, the entrance prompt "
            "never appeared after retries, timed out waiting for zone change, "
            "or not all clients reached dungeon_zone.",
        ),
    ],
)

PROMPT_TIMEOUT = 5.0
DIALOG_CHECK_DELAY = 0.5
ZONE_CHANGE_TIMEOUT = 30.0
POLL_INTERVAL = 0.25
MAX_RETRIES = 3


async def run(machine, clients: list, params: dict) -> str:
    """
    Enter a dungeon via its sigil.

    Params:
        from_zone (str):    Zone clients must be in when this node is called.
        dungeon_zone (str): Zone clients must be in when this node completes.
        sigil_xyz (dict):   {"x", "y", "z"} — coordinates of the dungeon sigil.
        reset_xyz (dict):   {"x", "y", "z"} — coordinates used to dismiss the entrance prompt.
    """
    from_zone: str = params["from_zone"]
    dungeon_zone: str = params["dungeon_zone"]
    sigil = XYZ(**params["sigil_xyz"])
    reset = XYZ(**params["reset_xyz"])

    machine.log(f"enter_dungeon: {from_zone!r} → {dungeon_zone!r}")

    # 1. Verify all clients are in from_zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", LogLevel.DEBUG)
    if not all(z == from_zone for z in zones):
        wrong = [z for z in zones if z != from_zone]
        machine.log(
            f"FAILURE: clients not in expected zone "
            f"(expected={from_zone!r}, unexpected={wrong})",
            LogLevel.WARNING,
        )
        return "FAILURE"

    # 2. Retry loop: teleport to sigil, wait for entrance prompt.
    prompt_appeared = False
    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(
            f"Attempt {attempt}/{MAX_RETRIES}: teleporting clients to sigil", LogLevel.DEBUG
        )
        await asyncio.gather(*[c.teleport(sigil) for c in clients])

        machine.log("Waiting for dungeon entrance prompt…", LogLevel.DEBUG)
        if await node_helpers.wait_until(
            lambda: _all_in_npc_range(clients), PROMPT_TIMEOUT, POLL_INTERVAL
        ):
            prompt_appeared = True
            break

        machine.log(
            f"Entrance prompt did not appear (attempt {attempt}); "
            f"resetting to reset_xyz",
            LogLevel.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for c in clients])

    if not prompt_appeared:
        machine.log(
            f"FAILURE: entrance prompt never appeared after {MAX_RETRIES} attempts",
            LogLevel.WARNING,
        )
        return "FAILURE"

    # 3. Press X to interact with the sigil (entrance prompt is showing).
    machine.log("Pressing X to enter dungeon sigil", LogLevel.DEBUG)
    await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in clients])

    # 4. Wait briefly, then confirm dungeon warning dialog if it appeared.
    machine.log("Checking for dungeon warning dialog…", LogLevel.DEBUG)
    await asyncio.gather(*[node_helpers.confirm_dialog_if_visible(c, DIALOG_CHECK_DELAY) for c in clients])

    # 5. Wait for all clients to leave from_zone.
    machine.log(f"Waiting for clients to leave {from_zone!r}…", LogLevel.DEBUG)
    if not await node_helpers.wait_until(
        lambda: node_helpers.all_left_zone(clients, from_zone), ZONE_CHANGE_TIMEOUT, POLL_INTERVAL
    ):
        machine.log(
            f"FAILURE: timed out after {ZONE_CHANGE_TIMEOUT}s waiting for clients "
            f"to leave {from_zone!r}",
            LogLevel.WARNING,
        )
        return "FAILURE"

    # 6. Wait for loading screens to finish.
    machine.log("Waiting for loading screen to clear…", LogLevel.DEBUG)
    await node_helpers.wait_until(
        lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL
    )

    # 7. Verify all clients arrived in dungeon_zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Zones after dungeon entry: {list(zones)}", LogLevel.DEBUG)
    if not all(z == dungeon_zone for z in zones):
        actual = list(set(zones))
        machine.log(
            f"FAILURE: not all clients reached dungeon zone "
            f"(expected={dungeon_zone!r}, got={actual})",
            LogLevel.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {dungeon_zone!r}")
    return "SUCCESS"


async def _all_in_npc_range(clients: list) -> bool:
    """Return True when every client has the NPC/sigil interact prompt."""
    results = await asyncio.gather(*[c.is_in_npc_range() for c in clients])
    return all(results)
