"""recall_to_marked_location node — recall to an existing marked location."""

import asyncio
import math

from wizwalker import Keycode, XYZ

from bartlebot.log_level import LogLevel
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import RECALL_BUTTON_PATH  # used only to check button state

RECALL_BUTTON_TIMEOUT = 35.0  # wait for RecallButton to become enabled (mark must be set)
TRIGGER_TIMEOUT = 10.0        # seconds to wait for loading screen to start after clicking recall
POSITION_MARGIN = 10.0        # acceptable distance from target XYZ after recall
POLL_INTERVAL = 0.25

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="str",
        label="Zone",
        help="Zone name all clients must be in after this node completes.",
        default="",
        required=True,
    ),
    ParamField(
        key="xyz",
        type="xyz",
        label="Mark XYZ",
        help="Expected XYZ coordinates after recalling. Used to verify arrival.",
        default={"x": 0.0, "y": 0.0, "z": 0.0},
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Recalls all clients to an existing marked location by clicking the RecallButton. "
        "Handles the loading screen and verifies arrival zone and position."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All clients recalled and arrived at the expected zone and XYZ."),
        ReturnSpec(
            "FAILURE",
            "One or more clients had no mana; the RecallButton was not enabled within the "
            "timeout (no mark set); clients did not enter a loading screen; or not all clients "
            "arrived in the expected zone and position after recalling.",
        ),
    ],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    Recall all clients to their marked location.

    Params:
        zone (str): Expected zone after recall.
        xyz (dict): {"x": float, "y": float, "z": float} — expected position after recall.
    """
    zone: str = params["zone"]
    xyz_data: dict = params["xyz"]
    target = XYZ(xyz_data["x"], xyz_data["y"], xyz_data["z"])

    machine.log(
        f"recall_to_marked_location: expected zone={zone!r} "
        f"XYZ({target.x:.1f}, {target.y:.1f}, {target.z:.1f})"
    )

    # 1. Verify all clients have mana to cast recall.
    mana_ratios = await asyncio.gather(*[c.calc_mana_ratio() for c in clients])
    machine.log(f"Mana ratios: {[round(r, 2) for r in mana_ratios]}", LogLevel.DEBUG)
    if any(r <= 0.0 for r in mana_ratios):
        machine.log("FAILURE: one or more clients have no mana to recall", LogLevel.WARNING)
        return "FAILURE"

    # 2. Wait for the RecallButton to be enabled on all clients (mark must be set).
    machine.log("Waiting for RecallButton to become enabled…", LogLevel.DEBUG)
    if not await node_helpers.wait_until(
        lambda: _all_recall_enabled(clients), RECALL_BUTTON_TIMEOUT, POLL_INTERVAL
    ):
        machine.log(
            f"FAILURE: RecallButton not enabled within {RECALL_BUTTON_TIMEOUT}s — no mark set",
            LogLevel.WARNING,
        )
        return "FAILURE"

    # 3. Press PAGE_UP on all clients to recall to the marked location.
    machine.log("Pressing PAGE_UP to recall on all clients…", LogLevel.DEBUG)
    await asyncio.gather(*[c.send_key(Keycode.PAGE_UP, 0.1) for c in clients])

    # 4. Wait for all clients to enter a loading screen ("ever seen" pattern).
    #    Re-press PAGE_UP for any client that hasn't started loading yet.
    #    A fast client can finish its loading screen before a slow one starts,
    #    so we track which clients have *ever* been seen loading.
    seen_loading: set[int] = set()
    loop_start = asyncio.get_event_loop().time()
    while True:
        loading = await asyncio.gather(*[c.is_loading() for c in clients])
        for i, is_now in enumerate(loading):
            if is_now:
                seen_loading.add(i)
        if len(seen_loading) == len(clients):
            break
        if asyncio.get_event_loop().time() - loop_start > TRIGGER_TIMEOUT:
            not_loading_idx = [i for i in range(len(clients)) if i not in seen_loading]
            machine.log(f"Clients not loading at timeout: {not_loading_idx}", LogLevel.DEBUG)
            machine.log(
                f"FAILURE: clients did not enter loading screen within {TRIGGER_TIMEOUT}s",
                LogLevel.WARNING,
            )
            return "FAILURE"
        not_loading_clients = [
            c for i, (c, l) in enumerate(zip(clients, loading)) if i not in seen_loading
        ]
        await asyncio.gather(
            *[c.send_key(Keycode.PAGE_UP, 0.1) for c in not_loading_clients]
        )
        await asyncio.sleep(POLL_INTERVAL)

    # 5. Wait for loading screens to finish.
    machine.log("Loading screen active, waiting for it to clear…", LogLevel.DEBUG)
    await node_helpers.wait_until(
        lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL
    )

    # 6. Verify all clients are in the expected zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Zones after recall: {list(zones)}", LogLevel.DEBUG)
    if not all(z == zone for z in zones):
        actual = list(set(zones))
        machine.log(
            f"FAILURE: not all clients in expected zone (expected={zone!r}, got={actual})",
            LogLevel.WARNING,
        )
        return "FAILURE"

    # 7. Verify all clients are at the expected XYZ.
    positions = await asyncio.gather(*[c.body.position() for c in clients])
    machine.log(
        f"Positions after recall: {[(round(p.x, 1), round(p.y, 1), round(p.z, 1)) for p in positions]}",
        LogLevel.DEBUG,
    )
    off_target = [i for i, p in enumerate(positions) if _distance(p, target) > POSITION_MARGIN]
    if off_target:
        machine.log(
            f"FAILURE: client(s) {off_target} not at expected XYZ after recall "
            f"(margin={POSITION_MARGIN})",
            LogLevel.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients recalled to {zone!r}")
    return "SUCCESS"


async def _all_recall_enabled(clients: list) -> bool:
    """Return True when the RecallButton is enabled (not grayed) for every client."""
    results = await asyncio.gather(
        *[node_helpers.is_enabled_by_path(c, RECALL_BUTTON_PATH) for c in clients]
    )
    return all(results)


def _distance(a: XYZ, b: XYZ) -> float:
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)
