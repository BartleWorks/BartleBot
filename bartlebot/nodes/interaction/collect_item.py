"""Node: pick up the nearest of a named quest item that has spawned in the current zone."""

import logging

from wizwalker import XYZ

from bartlebot.nodes import node_helpers
from bartlebot.nodes.help_text import ENTITY_ID_MATCHING, ZONE_AT_START
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import POLL_INTERVAL

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
        help=(
            "Where the Collecting Player teleports before searching. An item only shows to a player "
            "nearby, so choose a spot within range of where it spawns."
        ),
        default=None,
        default_hint="where the player stands",
        required=False,
    ),
    ParamField(
        key="entity_names",
        type="str_list",
        label="Entity IDs",
        help="The IDs of the item, as the Entity Browser shows them, not its in-game name. " + ENTITY_ID_MATCHING,
        default=[],
        required=True,
    ),
    ParamField(
        key="collect_client",
        type="player_slot",
        label="Collecting Player",
        help=(
            "Which player picks the item up. In an instance one pickup counts for the whole group, so "
            "one player is enough."
        ),
        default=1,
        required=False,
    ),
]

NODE_SPEC = NodeSpec(
    description="Teleports one player to the nearest listed quest item and picks it up.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "The Collecting Player picked up an item."),
        ReturnSpec(
            "FAILURE",
            "No item was in range of Starting XYZ, picking it up failed, a player wasn't in Zone, "
            "the Collecting Player isn't connected, or Entity IDs is empty.",
        ),
    ],
    required_bot_options=[
        SAFETY_DELAY_OPTION,
    ],
    max_consecutive_retries=5,
    usage_notes=(
        "Use it for a quest item that spawns in one of several places, such as a piece of a puzzle. "
        "For reagents, use Collect Reagent, which collects every one it finds and records the drops.\n\n"
        "Route FAILURE to the next place to search. An item shows only to a player nearby, so FAILURE "
        "means none was in range of Starting XYZ, and spawns far apart need a node for each area. The "
        "node waits up to Safety Delay for an item to show.\n\n"
        "Only the Collecting Player moves. The others stay where they are."
    ),
    see_also=["collect_reagent", "interact"],
    safety_warning="This bot teleports to collect items. Run it on a quiet realm.",
)


async def _matches(client, entity_names: list[str]) -> list:
    entities = []
    for name in entity_names:
        entities.extend(await node_helpers.find_entities_by_name(client, name))
    return entities


async def run(machine, clients: list, params: dict) -> str:
    zone: str = params["zone"]
    entity_names: list[str] = [n.strip() for n in params.get("entity_names", []) if n.strip()]
    slot = int(params.get("collect_client") or 0)
    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    machine.log(f"collect_item: zone={zone!r} entity_names={entity_names!r}")

    # 1. Zone check.
    if not await node_helpers.in_expected_zone(machine, clients, zone, POLL_INTERVAL):
        return "FAILURE"

    # 2. Validate params.
    if not entity_names:
        machine.log("FAILURE: entity_names is empty", logging.WARNING)
        return "FAILURE"
    if not 1 <= slot <= len(clients):
        machine.log(
            f"FAILURE: Collecting Player P{slot} is not connected ({len(clients)} client(s) running)",
            logging.WARNING,
        )
        return "FAILURE"
    client = clients[slot - 1]

    # 3. Optional teleport to a spot the item can be seen from.
    xyz_data = params.get("xyz")
    if xyz_data is not None:
        start = XYZ(xyz_data["x"], xyz_data["y"], xyz_data["z"])
        machine.log(
            f"collect_item: P{slot} teleporting to XYZ({start.x:.1f}, {start.y:.1f}, {start.z:.1f})",
            logging.DEBUG,
        )
        await client.teleport(start)

    # 4. Wait for a match to show. That none is there is only known once the wait is over.
    found: list = []

    async def visible() -> bool:
        found[:] = await _matches(client, entity_names)
        return bool(found)

    if not await node_helpers.poll_until(visible, safety_delay, POLL_INTERVAL):
        machine.log(f"FAILURE: no entity matching {entity_names} in range of P{slot}", logging.WARNING)
        return "FAILURE"

    # 5. Nearest first: the one the player was placed to find.
    located = []
    for entity in found:
        try:
            located.append(await entity.location())
        except Exception as exc:
            machine.log(f"collect_item: stale entity reference, skipping ({exc})", logging.DEBUG)
    try:
        here = await client.body.position()
    except Exception:
        here = None
    if here is not None:
        located.sort(key=lambda xyz: node_helpers.distance(here, xyz))

    # 6. Pick one up, trying the next if one does not take.
    for i, xyz in enumerate(located, start=1):
        machine.log(
            f"collect_item: entity {i}/{len(located)} at ({xyz.x:.1f}, {xyz.y:.1f}, {xyz.z:.1f})",
            logging.DEBUG,
        )
        outcome = await node_helpers.pick_up(client, xyz, safety_delay, POLL_INTERVAL)
        if outcome is node_helpers.Pickup.COLLECTED:
            machine.log(f"collect_item: P{slot} picked up an item — SUCCESS")
            return "SUCCESS"
        machine.log(f"collect_item: entity {i} — {outcome.value}, trying the next", logging.DEBUG)

    machine.log(f"FAILURE: P{slot} found {len(found)} item(s) but picked none up", logging.WARNING)
    return "FAILURE"
