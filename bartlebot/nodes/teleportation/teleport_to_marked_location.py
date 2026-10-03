"""teleport_to_marked_location node — recall to an existing marked location."""

import asyncio
import logging

from wizwalker import XYZ

from bartlebot import dialog_captions, player_realms
from bartlebot.nodes import node_helpers
from bartlebot.nodes.node_helpers import POSITION_MARGIN, TRIGGER_MARGIN
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import RECALL_BUTTON_PATH
from bartlebot.nodes.teleportation import mark_location
from bartlebot.nodes.timing import COMPASS_TELEPORT_TIMEOUT, POLL_INTERVAL, TRIGGER_TIMEOUT

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="zone",
        label="Zone",
        help=(
            "Zone name all players must be in after this node completes. Blank: the zone each "
            "player marked in earlier in this run."
        ),
        default="",
        required=False,
    ),
    ParamField(
        key="xyz",
        type="xyz",
        label="Mark XYZ",
        help=(
            "Expected XYZ coordinates after recalling. Used to verify arrival. Blank: where each "
            "player marked earlier in this run."
        ),
        default=None,
        required=False,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Recalls all players to an existing marked location by clicking the RecallButton. "
        "Handles the loading screen and verifies arrival zone and position."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players recalled and arrived at the expected zone and XYZ."),
        ReturnSpec(
            "FAILURE",
            "One or more players had no mana; the RecallButton was not enabled within the "
            "timeout (no mark set); the game refused the recall (most often because the marked "
            "dungeon's timer ended and it has been reset); players did not move or enter a "
            "loading screen within the trigger timeout; or not all players arrived in the "
            "expected zone and position after recalling.",
        ),
    ],
    required_bot_options=[player_realms.SPEC],
    max_consecutive_retries=5,
    usage_notes=(
        "Leave Zone and Mark XYZ blank to check each player against the mark it set earlier in "
        "this run, through Mark Location or Handle Level Scaling: a bot that marks and recalls "
        "then names the spot once. With them blank, a player that has not marked in this run "
        "fails the node before anyone recalls.\n\n"
        "If some but not all players end up in the expected zone, the node first tries "
        "teleporting the stragglers to whichever player did arrive before reporting "
        "FAILURE. That needs every player to already be mutual in-game friends."
    ),
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
         A blank zone or xyz is each client's own mark from this run (mark_location.marked_at),
         and a client with none fails the node before step 1.

    FAILURE: any client has no mana; RecallButton not enabled within timeout (no mark set);
    recall not triggered within TRIGGER_TIMEOUT; not all clients arrived at expected zone/XYZ;
    zone or xyz blank and a client has not marked in this run.
    """
    zone: str = params.get("zone") or ""
    target = _point(params.get("xyz"))

    # With no zone or XYZ named, each client is checked against its own mark from this run.
    marks = [mark_location.marked_at(machine, c) for c in clients]
    if (not zone or target is None) and None in marks:
        unmarked = [i + 1 for i, m in enumerate(marks) if m is None]
        machine.log(
            f"FAILURE: client(s) {unmarked} have not marked in this run, and the node names no "
            f"{'XYZ' if target is None else 'zone'} to check their arrival against",
            logging.WARNING,
        )
        return "FAILURE"
    expected_zones = [zone] * len(clients) if zone else [m.zone for m in marks]
    expected_points = [target] * len(clients) if target is not None else [m.position for m in marks]

    if zone and target is not None:
        machine.log(
            f"teleport_to_marked_location: expected zone={zone!r} XYZ({target.x:.1f}, {target.y:.1f}, {target.z:.1f})"
        )
    else:
        machine.log("teleport_to_marked_location: expecting each client back on the mark it set in this run")

    if not await recall(machine, clients):
        return "FAILURE"

    # 7. Verify all clients are in the expected zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Zones after recall: {list(zones)}", logging.DEBUG)
    if len(set(expected_zones)) == 1:
        expected = expected_zones[0]
        actual = await node_helpers.wait_for_zone_with_recovery(machine, clients, expected, 0, POLL_INTERVAL)
        if actual is not None:
            machine.log(
                f"FAILURE: not all clients in expected zone (expected={expected!r}, got={list(set(actual))})",
                logging.WARNING,
            )
            return "FAILURE"
    elif wrong := [i + 1 for i, (z, e) in enumerate(zip(zones, expected_zones)) if not node_helpers.zone_matches(z, e)]:
        machine.log(f"FAILURE: client(s) {wrong} did not land in the zone they marked in", logging.WARNING)
        return "FAILURE"

    # 8. Verify all clients are at the expected XYZ.
    positions = await asyncio.gather(*[c.body.position() for c in clients])
    machine.log(
        f"Positions after recall: {[(round(p.x, 1), round(p.y, 1), round(p.z, 1)) for p in positions]}",
        logging.DEBUG,
    )
    off_target = [
        i for i, (p, e) in enumerate(zip(positions, expected_points)) if node_helpers.distance(p, e) > POSITION_MARGIN
    ]
    if off_target:
        machine.log(
            f"FAILURE: client(s) {off_target} not at expected XYZ after recall (margin={POSITION_MARGIN})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients recalled to {zone!r}")
    return "SUCCESS"


async def recall(machine, clients: list) -> bool:
    """Steps 1 to 6 of run(): press Recall on every client and wait out the trip.

    Returns False having logged the reason. Where the clients landed is the caller's to check:
    run() checks one recorded spot, handle_level_scaling the spot each client marked.
    """
    # 1. Verify all clients have mana to cast recall.
    mana_ratios = await asyncio.gather(*[c.calc_mana_ratio() for c in clients])
    machine.log(f"Mana ratios: {[round(r, 2) for r in mana_ratios]}", logging.DEBUG)
    if any(r <= 0.0 for r in mana_ratios):
        machine.log("FAILURE: one or more clients have no mana to recall", logging.WARNING)
        return False

    # 2. Wait for the RecallButton to be enabled on all clients (mark must be set).
    machine.log("Waiting for RecallButton to become enabled…", logging.DEBUG)
    if not await node_helpers.poll_until(lambda: _all_recall_enabled(clients), COMPASS_TELEPORT_TIMEOUT, POLL_INTERVAL):
        machine.log(
            f"FAILURE: RecallButton not enabled within {COMPASS_TELEPORT_TIMEOUT}s — no mark set",
            logging.WARNING,
        )
        return False

    # 3. Snapshot starting positions so a same-zone recall (no loading screen) can be detected
    #    by XYZ movement instead.
    start_positions = await asyncio.gather(*[c.body.position() for c in clients])

    # Who shares whose realm, for straggler recovery in a Separate bot. A recall keeps realms.
    await node_helpers.note_realm_peers(machine, clients)

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
            return False
        loading = await asyncio.gather(*[c.is_loading() for c in clients])
        positions = await asyncio.gather(*[c.body.position() for c in clients])
        for i, (is_now, pos) in enumerate(zip(loading, positions)):
            if is_now or node_helpers.distance(pos, start_positions[i]) > TRIGGER_MARGIN:
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
            return False
        await asyncio.gather(*[_retry_recall(i, c) for i, c in enumerate(clients) if i not in seen_triggered])
        await asyncio.sleep(POLL_INTERVAL)

    # 6. Wait for any loading screens to finish (no-op for same-zone recalls).
    machine.log("Waiting for any loading screens to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL)
    return True


def _point(data) -> XYZ | None:
    """The XYZ a param holds, or None when it is blank."""
    try:
        return XYZ(float(data["x"]), float(data["y"]), float(data["z"]))
    except (KeyError, TypeError, ValueError):
        return None


async def _all_recall_enabled(clients: list) -> bool:
    """Return True when the RecallButton is enabled (not grayed) for every client."""
    results = await asyncio.gather(*[node_helpers.is_enabled_by_path(c, RECALL_BUTTON_PATH) for c in clients])
    return all(results)
