"""Node: visit reagent spawn locations and collect any reagents present."""

import asyncio
import json
import logging

from wizwalker import XYZ, Keycode

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import POLL_INTERVAL, REAGENT_COLLECT_DELAY, jitter

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
        key="spawns",
        type="text",
        label="Spawn Locations",
        help=(
            "One spawn location per line, in Copy XYZ format — e.g. "
            '{"x": -1234.56, "y": 678.9, "z": 0.0}. '
            "Walk to each spawn, click Copy XYZ in the client widget, and paste here. "
            "Locations are visited in order. Blank lines are ignored."
        ),
        default="",
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Visits a list of reagent spawn locations in order and collects any reagents present. "
        "An absent reagent at a spawn is expected — the node waits briefly and moves on."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All spawn locations visited (regardless of how many reagents were collected)."),
        ReturnSpec(
            "FAILURE",
            "Clients were not in the expected zone, or a spawns line could not be parsed.",
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
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.is_in_npc_range(),
          client.send_key(Keycode.X)

    Flow:
      1. Verify all clients are in zone. Return FAILURE if not.
      2. Parse spawns — one JSON dict per line (Copy XYZ format). Return FAILURE if any
         line cannot be parsed. Return SUCCESS immediately if no spawns are configured.
      3. For each spawn XYZ in order:
         a. asyncio.gather teleport all clients to the spawn location.
         b. Check is_in_npc_range() immediately; if no prompt, poll for up to Safety Delay.
         c. If a prompt appeared on any client: press X on prompted clients and repeat
            until all prompts clear or Safety Delay expires.
         d. Move to the next spawn (absence of a reagent is expected — no retry).
      4. Return SUCCESS after all spawns are visited.

    FAILURE: clients not in zone at start; a spawns line cannot be parsed as a JSON XYZ dict.
    """
    from bartlebot.utils.drop_detection import extract_new_drops, snapshot_chat  # noqa: PLC0415

    zone: str = params["zone"]
    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    machine.log(f"collect_reagent: zone={zone!r}")

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

    # 2. Parse spawn list — one JSON dict per line (Copy XYZ format).
    spawns: list[XYZ] = []
    for lineno, line in enumerate((params.get("spawns") or "").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            spawns.append(XYZ(**json.loads(line)))
        except Exception as exc:
            machine.log(
                f"FAILURE: could not parse spawns line {lineno} ({line!r}) — {exc}",
                logging.WARNING,
            )
            return "FAILURE"

    if not spawns:
        machine.log("collect_reagent: no spawns configured — SUCCESS")
        return "SUCCESS"

    machine.log(f"collect_reagent: visiting {len(spawns)} spawn(s)", logging.DEBUG)

    # 3. Visit each spawn.
    collected = 0
    for i, xyz in enumerate(spawns, start=1):
        machine.log(
            f"Spawn {i}/{len(spawns)}: teleporting to ({xyz.x:.1f}, {xyz.y:.1f}, {xyz.z:.1f})",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(xyz) for c in clients])

        # Wait briefly for a reagent prompt — absence is expected (reagent may not be present).
        prompted = await asyncio.gather(*[c.is_in_npc_range() for c in clients])
        if not any(prompted):
            await node_helpers.poll_until(
                lambda: node_helpers.all_in_npc_range(clients),
                safety_delay,
                POLL_INTERVAL,
            )
            prompted = await asyncio.gather(*[c.is_in_npc_range() for c in clients])

        if any(prompted):
            machine.log(f"Spawn {i}: reagent present — collecting", logging.DEBUG)
            # Press X on every client with a prompt.
            # On the same server only one client will actually collect, but pressing X
            # is a no-op for the other — the game handles the collision.
            collectors = [(clients.index(c), c) for c, p in zip(clients, prompted) if p]
            chat_before = await asyncio.gather(*[snapshot_chat(c) for _, c in collectors])

            # Keep pressing X on still-prompted clients until all prompts clear or timeout.
            still_prompted = [c for _, c in collectors]
            deadline = asyncio.get_event_loop().time() + safety_delay
            while still_prompted and asyncio.get_event_loop().time() < deadline:
                await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in still_prompted])
                await asyncio.sleep(jitter(REAGENT_COLLECT_DELAY))
                in_range = await asyncio.gather(*[c.is_in_npc_range() for c in still_prompted])
                still_prompted = [c for c, p in zip(still_prompted, in_range) if p]

            collected += 1
            for (client_idx, c), before in zip(collectors, chat_before):
                after = await snapshot_chat(c)
                for item_name in extract_new_drops(before, after):
                    machine.on_drop(item_name, 1, client_idx)
        else:
            machine.log(f"Spawn {i}: no reagent present — skipping", logging.DEBUG)

    machine.log(f"collect_reagent: collected from {collected}/{len(spawns)} spawn(s) — SUCCESS")
    return "SUCCESS"
