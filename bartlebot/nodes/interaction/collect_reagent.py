"""Node: collect all present instances of a named reagent entity in the current zone."""

import asyncio
import logging

from wizwalker import XYZ, Keycode

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import POLL_INTERVAL, REAGENT_DROP_MESSAGE_POLL, REAGENT_DROP_MESSAGE_TIMEOUT

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="zone",
        label="Zone",
        help="Zone name all players must currently be in when this node executes.",
        default="",
        required=True,
    ),
    ParamField(
        key="xyz",
        type="xyz",
        label="Starting XYZ",
        help=(
            "Optional coordinates to teleport all players to before searching for reagents. "
            "Leave blank to collect from the current position."
        ),
        default=None,
        default_hint="No teleport — collect from current position.",
        required=False,
    ),
    ParamField(
        key="entity_names",
        type="str_list",
        label="Entity Names",
        help=(
            "Internal object names of the reagents to collect — use the Nearest Entity "
            "display in the Players tab to find them. Partial matches work "
            "(e.g. 'Fungi' matches 'FungusWort'). Wildcards (* ? []) match the whole "
            "name instead, so use '*Fungi*' to match anywhere and 'Fungi*' to match only "
            "names starting with it. All listed reagents are collected in one pass."
        ),
        default=[],
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Finds all present instances of a named reagent entity in the zone and collects them. "
        "Optionally teleports all players to a starting position first. "
        "Each player searches and collects independently, so players on different servers "
        "work from their own entity lists. "
        "An empty spawn is a normal outcome, not a failure — the node succeeds with nothing collected."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec(
            "SUCCESS",
            "The zone was searched. Includes finding nothing — reagent spawns are random, "
            "so an empty pass is expected and the route should carry on to its next stop.",
        ),
        ReturnSpec(
            "FAILURE",
            "The search could not be performed: a player is in the wrong zone, or entity_names is empty.",
        ),
    ],
    required_bot_options=[
        BotOptionSpec(
            name="Safety Delay",
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
    max_consecutive_retries=5,
    safety_warning=(
        "This bot teleports to collect reagents. Ensure that you are not in a busy realm when you run this bot."
    ),
)


async def _collect_for_client(machine, client, client_idx: int, entity_names: list[str], safety_delay: float) -> int:
    """Find and collect all matching reagent entities for a single client. Returns collected count."""
    from bartlebot.utils.drop_detection import chat_available, poll_new_drops, snapshot_chat  # noqa: PLC0415

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
        await client.teleport(xyz)

        prompted = await node_helpers.poll_until(
            lambda: node_helpers.all_in_npc_range([client]),
            safety_delay,
            POLL_INTERVAL,
        )

        if prompted:
            await client.send_key(Keycode.X, 0.1)
            dismissed = await node_helpers.poll_until(
                lambda: node_helpers.none_in_npc_range([client]),
                safety_delay,
                POLL_INTERVAL,
            )
            if dismissed:
                collected += 1
                if track_drops:
                    await _report_drops()
            else:
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
