"""teleport_to_marked_location node — recall to an existing marked location."""

import asyncio
import logging
import math

from wizwalker import XYZ

from bartlebot import dialog_captions
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import RECALL_BUTTON_PATH
from bartlebot.nodes.timing import COMPASS_TELEPORT_TIMEOUT, POLL_INTERVAL, TRIGGER_TIMEOUT

TRIGGER_MARGIN = 5.0  # minimum XYZ movement that counts as a same-zone recall trigger
POSITION_MARGIN = 10.0  # acceptable distance from target XYZ after recall

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="str",
        label="Zone",
        help="Zone name all players must be in after this node completes.",
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
        ReturnSpec("SUCCESS", "All players recalled and arrived at the expected zone and XYZ."),
        ReturnSpec(
            "FAILURE",
            "One or more clients had no mana; the RecallButton was not enabled within the "
            "timeout (no mark set); the game refused the recall (most often because the marked "
            "dungeon's timer ended and it has been reset); clients did not move or enter a "
            "loading screen within the trigger timeout; or not all clients arrived in the "
            "expected zone and position after recalling.",
        ),
    ],
    max_consecutive_retries=5,
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.calc_mana_ratio(), client.is_loading(), client.body.position(),
          client.zone_name(), node_helpers.click_by_path()

    Flow:
      1. Verify all clients have mana (ratio > 0.0). Return FAILURE if any do not.
      2. Wait for RecallButton to be enabled on all clients. Return FAILURE if
         COMPASS_TELEPORT_TIMEOUT expires (indicates no mark has been set).
      3. Snapshot starting positions to detect same-zone recalls (no loading screen).
      4. Click RecallButton on all clients simultaneously.
      5. "Ever seen" loop: poll until all clients have either entered a loading screen or
         moved more than TRIGGER_MARGIN units. For each straggler, answer any modal — a
         dungeon exit warning is confirmed, a refusal (e.g. the marked dungeon has expired)
         fails the node by name — otherwise re-click RecallButton.
         Return FAILURE if TRIGGER_TIMEOUT expires.
      6. Wait for all loading screens to finish.
      7. Verify all clients are in zone and within POSITION_MARGIN of xyz. Return FAILURE if not.

    FAILURE: any client has no mana; RecallButton not enabled within timeout (no mark set);
    recall not triggered within TRIGGER_TIMEOUT; not all clients arrived at expected zone/XYZ.
    """
    zone: str = params["zone"]
    xyz_data: dict = params["xyz"]
    target = XYZ(xyz_data["x"], xyz_data["y"], xyz_data["z"])

    machine.log(
        f"teleport_to_marked_location: expected zone={zone!r} XYZ({target.x:.1f}, {target.y:.1f}, {target.z:.1f})"
    )

    # 1. Verify all clients have mana to cast recall.
    mana_ratios = await asyncio.gather(*[c.calc_mana_ratio() for c in clients])
    machine.log(f"Mana ratios: {[round(r, 2) for r in mana_ratios]}", logging.DEBUG)
    if any(r <= 0.0 for r in mana_ratios):
        machine.log("FAILURE: one or more clients have no mana to recall", logging.WARNING)
        return "FAILURE"

    # 2. Wait for the RecallButton to be enabled on all clients (mark must be set).
    machine.log("Waiting for RecallButton to become enabled…", logging.DEBUG)
    if not await node_helpers.poll_until(lambda: _all_recall_enabled(clients), COMPASS_TELEPORT_TIMEOUT, POLL_INTERVAL):
        machine.log(
            f"FAILURE: RecallButton not enabled within {COMPASS_TELEPORT_TIMEOUT}s — no mark set",
            logging.WARNING,
        )
        return "FAILURE"

    # 3. Snapshot starting positions so a same-zone recall (no loading screen) can be detected
    #    by XYZ movement instead.
    start_positions = await asyncio.gather(*[c.body.position() for c in clients])

    # 4. Click the RecallButton on all clients to recall to the marked location.
    machine.log("Clicking RecallButton on all clients…", logging.DEBUG)
    await asyncio.gather(*[node_helpers.click_by_path(c, RECALL_BUTTON_PATH) for c in clients])

    # 5. Wait for all clients to trigger ("ever seen" pattern).
    #    A recall triggers either a loading screen (cross-zone) or an immediate XYZ jump
    #    (same-zone mark).  We accept either signal so both cases are handled.
    #    Re-press PAGE_UP for any client that hasn't triggered yet.
    #    Recalling out of a dungeon raises the dungeon exit warning first; until it is
    #    confirmed the recall never fires, and re-clicking behind the modal does nothing.
    #    A recall *into* an expired dungeon is refused outright instead — the mark outlives
    #    the instance, so this is a normal thing to hit after a long run, and it deserves
    #    to be named rather than reported as "clients did not recall".
    refusals: dict[int, str] = {}

    async def _retry_recall(index: int, client) -> None:
        outcome = await node_helpers.resolve_modal(client)
        if outcome.refusal:
            refusals[index] = outcome.refusal
            return
        if outcome.answered:
            return
        await node_helpers.click_by_path(client, RECALL_BUTTON_PATH)

    seen_triggered: set[int] = set()
    loop_start = asyncio.get_event_loop().time()
    while True:
        if refusals:
            for i, key in refusals.items():
                machine.log(
                    f"FAILURE: client {i + 1} could not recall — {dialog_captions.describe(key)}",
                    logging.WARNING,
                )
            return "FAILURE"
        loading = await asyncio.gather(*[c.is_loading() for c in clients])
        positions = await asyncio.gather(*[c.body.position() for c in clients])
        for i, (is_now, pos) in enumerate(zip(loading, positions)):
            if is_now or _distance(pos, start_positions[i]) > TRIGGER_MARGIN:
                seen_triggered.add(i)
        if len(seen_triggered) == len(clients):
            break
        if asyncio.get_event_loop().time() - loop_start > TRIGGER_TIMEOUT:
            not_triggered_idx = [i for i in range(len(clients)) if i not in seen_triggered]
            machine.log(f"Clients not triggered at timeout: {not_triggered_idx}", logging.DEBUG)
            machine.log(
                f"FAILURE: clients did not recall within {TRIGGER_TIMEOUT}s",
                logging.WARNING,
            )
            return "FAILURE"
        await asyncio.gather(*[_retry_recall(i, c) for i, c in enumerate(clients) if i not in seen_triggered])
        await asyncio.sleep(POLL_INTERVAL)

    # 6. Wait for any loading screens to finish (no-op for same-zone recalls).
    machine.log("Waiting for any loading screens to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL)

    # 7. Verify all clients are in the expected zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Zones after recall: {list(zones)}", logging.DEBUG)
    if not all(node_helpers.zone_matches(z, zone) for z in zones):
        actual = list(set(zones))
        machine.log(
            f"FAILURE: not all clients in expected zone (expected={zone!r}, got={actual})",
            logging.WARNING,
        )
        return "FAILURE"

    # 8. Verify all clients are at the expected XYZ.
    positions = await asyncio.gather(*[c.body.position() for c in clients])
    machine.log(
        f"Positions after recall: {[(round(p.x, 1), round(p.y, 1), round(p.z, 1)) for p in positions]}",
        logging.DEBUG,
    )
    off_target = [i for i, p in enumerate(positions) if _distance(p, target) > POSITION_MARGIN]
    if off_target:
        machine.log(
            f"FAILURE: client(s) {off_target} not at expected XYZ after recall (margin={POSITION_MARGIN})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients recalled to {zone!r}")
    return "SUCCESS"


async def _all_recall_enabled(clients: list) -> bool:
    """Return True when the RecallButton is enabled (not grayed) for every client."""
    results = await asyncio.gather(*[node_helpers.is_enabled_by_path(c, RECALL_BUTTON_PATH) for c in clients])
    return all(results)


def _distance(a: XYZ, b: XYZ) -> float:
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)
