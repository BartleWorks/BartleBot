"""Node: collect all present instances of a named reagent entity in the current zone."""

import asyncio
import logging

from wizwalker import XYZ

from bartlebot.nodes import node_helpers
from bartlebot.nodes.help_text import ENTITY_ID_MATCHING, ZONE_AT_START
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import POLL_INTERVAL, REAGENT_DROP_MESSAGE_POLL, REAGENT_DROP_MESSAGE_TIMEOUT

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
        key="xyz",
        type="xyz",
        label="Starting XYZ",
        help="Where to teleport players before searching.",
        default=None,
        default_hint="where players stand",
        required=False,
    ),
    ParamField(
        key="entity_names",
        type="str_list",
        label="Entity IDs",
        help=(
            "The IDs of the reagents to collect, as the Entity Browser shows them, not their in-game names. "
            + ENTITY_ID_MATCHING
        ),
        default=[],
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Collects every listed reagent in the zone, each player on its own. Finding none "
        "succeeds, since spawns are random."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec(
            "SUCCESS",
            "The zone was searched, whether or not anything was found.",
        ),
        ReturnSpec(
            "FAILURE",
            "A player wasn't in Zone, or Entity IDs is empty.",
        ),
    ],
    required_bot_options=[
        SAFETY_DELAY_OPTION,
    ],
    max_consecutive_retries=5,
    safety_warning="This bot teleports to collect reagents. Run it on a quiet realm.",
    implies_tags=["Reagents"],
    see_also=["collect_item"],
)


async def _collect_for_client(machine, client, client_idx: int, entity_names: list[str], safety_delay: float) -> int:
    """Find and collect all matching reagent entities for a single client. Returns collected count."""
    from bartlebot.game.drop_detection import chat_available, poll_new_drops, snapshot_chat  # noqa: PLC0415

    entities = []
    for name in entity_names:
        found = await node_helpers.find_entities_by_name(client, name)
        if found:
            machine.log(f"Client {client_idx + 1}: found {len(found)} '{name}' entity(s)", logging.DEBUG)
            entities.extend(found)

    if not entities:
        machine.log(f"Client {client_idx + 1}: no entities matching {entity_names} present — skipping", logging.DEBUG)
        return 0

    machine.log(
        f"Client {client_idx + 1}: found {len(entities)} entity(s) total across {len(entity_names)} name(s)",
        logging.DEBUG,
    )
    collected = 0

    # Drop announcements arrive from the server well after the reagent prompt has
    # closed, so the chat baseline is carried across the whole pass rather than
    # re-snapshotted per pickup: a message that misses its own poll window is then
    # still detected on a later one instead of being absorbed into a new baseline.
    track_drops = await chat_available(client)
    if not track_drops:
        machine.log(
            f"Client {client_idx + 1}: chat log not visible — drops will not be logged this pass",
            logging.WARNING,
        )
    drop_baseline = await snapshot_chat(client) if track_drops else ""

    async def _report_drops() -> None:
        nonlocal drop_baseline
        drops, drop_baseline = await poll_new_drops(
            client, drop_baseline, REAGENT_DROP_MESSAGE_TIMEOUT, REAGENT_DROP_MESSAGE_POLL
        )
        for item_name in drops:
            machine.on_drop(item_name, 1, client_idx)

    for i, entity in enumerate(entities, start=1):
        try:
            xyz = await entity.location()
        except Exception as exc:
            machine.log(
                f"Client {client_idx + 1}: entity {i} — stale reference, skipping ({exc})",
                logging.DEBUG,
            )
            continue
        machine.log(
            f"Client {client_idx + 1}: entity {i}/{len(entities)} at ({xyz.x:.1f}, {xyz.y:.1f}, {xyz.z:.1f})",
            logging.DEBUG,
        )
        outcome = await node_helpers.pick_up(client, xyz, safety_delay, POLL_INTERVAL)
        if outcome is node_helpers.Pickup.COLLECTED:
            collected += 1
            if track_drops:
                await _report_drops()
        elif outcome is node_helpers.Pickup.NOT_DISMISSED:
            machine.log(
                f"Client {client_idx + 1}: entity {i} — prompt did not dismiss after X, skipping",
                logging.DEBUG,
            )
        else:
            machine.log(
                f"Client {client_idx + 1}: entity {i} — no prompt after teleport, skipping",
                logging.DEBUG,
            )

    # One last look: the final pickup has no following iteration to catch a late
    # message, and a single pickup that yields two items announces them on
    # separate lines, so the per-pickup poll can return with one still in flight.
    if track_drops and collected:
        await _report_drops()

    return collected


async def run(machine, clients: list, params: dict) -> str:
    zone: str = params["zone"]
    entity_names: list[str] = [n.strip() for n in params.get("entity_names", []) if n.strip()]
    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    machine.log(f"collect_reagent: zone={zone!r} entity_names={entity_names!r}")

    # 1. Zone check.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if not all(node_helpers.zone_matches(z, zone) for z in zones):
        wrong = [z for z in zones if not node_helpers.zone_matches(z, zone)]
        machine.log(
            f"FAILURE: clients not in expected zone (expected={zone!r}, unexpected={wrong})",
            logging.WARNING,
        )
        return "FAILURE"

    # 2. Validate entity names.
    if not entity_names:
        machine.log("FAILURE: entity_names is empty", logging.WARNING)
        return "FAILURE"

    # 3. Optional pre-teleport to spawn area.
    xyz_data = params.get("xyz")
    if xyz_data is not None:
        target = XYZ(xyz_data["x"], xyz_data["y"], xyz_data["z"])
        machine.log(
            f"collect_reagent: teleporting to XYZ({target.x:.1f}, {target.y:.1f}, {target.z:.1f})",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(target) for c in clients])

    # 4. Wait for entities to load.
    await asyncio.sleep(safety_delay)

    # 5. Each client independently finds and collects its own entity list.
    counts = await asyncio.gather(
        *[_collect_for_client(machine, c, i, entity_names, safety_delay) for i, c in enumerate(clients)]
    )
    # Reagent spawns are random, so an empty pass is a normal outcome rather than an
    # error. Reporting it as FAILURE made it indistinguishable from a real problem
    # like a wrong-zone client, and sent routes back to their recovery branch instead
    # of on to their next stop. FAILURE is now reserved for the checks above.
    total = sum(counts)
    if total == 0:
        machine.log(f"collect_reagent: no reagents present in {zone!r} — nothing to collect, SUCCESS")
    else:
        machine.log(f"collect_reagent: {total} reagent(s) collected across {len(clients)} client(s) — SUCCESS")
    return "SUCCESS"
