"""Node: collect all present instances of a named reagent entity in the current zone."""

import asyncio
import logging

from wizwalker import Keycode

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import POLL_INTERVAL

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="str",
        label="Zone",
        help="Zone name all clients must currently be in when this node executes.",
        default="",
        required=True,
    ),
    ParamField(
        key="entity_name",
        type="str",
        label="Entity Name",
        help=(
            "Internal object name of the reagent to collect — use the Nearest Entity "
            "display in the client widget to find it. Partial matches work "
            "(e.g. 'Fungi' matches 'FungusWort')."
        ),
        default="",
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Finds all present instances of a named reagent entity in the zone and collects them. "
        "Each client searches and collects independently, so clients on different servers "
        "work from their own entity lists."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "At least one entity was found and visited across all clients."),
        ReturnSpec(
            "FAILURE",
            "Clients were not in the expected zone, entity_name is blank, or no matching entities were found.",
        ),
    ],
    required_bot_options=[
        BotOptionSpec(
            name="Safety Delay",
            type="float",
            default=3.0,
            description=(
                "Seconds to allow for game-server responses when waiting for prompts or menus. "
                "Increase on high-latency connections."
            ),
        ),
    ],
    max_consecutive_retries=5,
)


async def _collect_for_client(machine, client, client_idx: int, entity_name: str, safety_delay: float) -> int:
    """Find and collect all matching reagent entities for a single client. Returns collected count."""
    from bartlebot.utils.drop_detection import extract_new_drops, snapshot_chat  # noqa: PLC0415

    entities = await client.get_base_entities_with_vague_name(entity_name)
    if not entities:
        machine.log(f"Client {client_idx + 1}: no '{entity_name}' present — skipping", logging.DEBUG)
        return 0

    machine.log(f"Client {client_idx + 1}: found {len(entities)} '{entity_name}' entity(s)", logging.DEBUG)
    collected = 0

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

        prompted = await client.is_in_npc_range() or await node_helpers.poll_until(
            lambda: node_helpers.all_in_npc_range([client]),
            safety_delay,
            POLL_INTERVAL,
        )

        if prompted:
            chat_before = await snapshot_chat(client)
            await client.send_key(Keycode.X, 0.1)
            await asyncio.sleep(safety_delay)
            collected += 1
            for item_name in extract_new_drops(chat_before, await snapshot_chat(client)):
                machine.on_drop(item_name, 1, client_idx)
        else:
            machine.log(
                f"Client {client_idx + 1}: entity {i} — no prompt after teleport, skipping",
                logging.DEBUG,
            )

    return collected


async def run(machine, clients: list, params: dict) -> str:
    zone: str = params["zone"]
    entity_name: str = params.get("entity_name", "").strip()
    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    machine.log(f"collect_reagent: zone={zone!r} entity_name={entity_name!r}")

    # 1. Zone check.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if not all(z == zone for z in zones):
        wrong = [z for z in zones if z != zone]
        machine.log(
            f"FAILURE: clients not in expected zone (expected={zone!r}, unexpected={wrong})",
            logging.WARNING,
        )
        return "FAILURE"

    # 2. Validate entity name.
    if not entity_name:
        machine.log("FAILURE: entity_name is blank", logging.WARNING)
        return "FAILURE"

    # 3. Each client independently finds and collects its own entity list.
    counts = await asyncio.gather(
        *[_collect_for_client(machine, c, i, entity_name, safety_delay) for i, c in enumerate(clients)]
    )
    total = sum(counts)
    if total == 0:
        machine.log(f"FAILURE: no '{entity_name}' entities found on any client", logging.WARNING)
        return "FAILURE"
    machine.log(f"collect_reagent: collected {total} reagent(s) across {len(clients)} client(s) — SUCCESS")
    return "SUCCESS"
