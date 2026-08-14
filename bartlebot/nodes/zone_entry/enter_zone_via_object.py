"""Node: interact with an NPC or object that triggers a zone change."""

import asyncio
import logging

from wizwalker import XYZ, Keycode

from bartlebot import dialog_captions
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="from_zone",
        type="str",
        label="From Zone",
        help="Zone name players must currently be in when this node executes.",
        default="",
        required=True,
    ),
    ParamField(
        key="to_zone",
        type="str",
        label="To Zone",
        help="Zone name players must be in after this node completes.",
        default="",
        required=True,
    ),
    ParamField(
        key="interact_xyz",
        type="xyz",
        label="Interact XYZ",
        help=("Coordinates of the object to interact with. The interact prompt should appear when standing here."),
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
        "Interacts with an NPC or world object that triggers a zone change (e.g., "
        "a snowglobe or portal). Teleports players to the interact point, waits for "
        "the prompt, presses X for all players, and re-presses X for any player that "
        "hasn't started loading — until all players are in the destination zone."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players are now in to_zone."),
        ReturnSpec(
            "FAILURE",
            "Players were not in from_zone at the start, the interact prompt never "
            "appeared after retries, players did not enter a loading screen within "
            "the timeout, or not all players arrived in to_zone.",
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
    max_consecutive_retries=10,
)

from bartlebot.nodes.timing import MAX_RETRIES, POLL_INTERVAL, TRIGGER_TIMEOUT, ZONE_CHANGE_TIMEOUT


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.is_in_npc_range(),
          client.send_key(Keycode.X), client.is_loading()

    Flow:
      1. Verify all clients are in from_zone. Return FAILURE if not.
      2. Clear any pre-existing interact prompt by teleporting to reset_xyz.
      3. Retry loop (up to MAX_RETRIES): teleport to interact_xyz, wait for the interact
         prompt on all clients. If it does not appear, teleport to reset_xyz and retry.
      4. Press X on all clients simultaneously.
      5. Dismiss any dungeon exit warning dialog that appears.
      6. Poll until all clients have entered a loading screen, re-pressing X for stragglers.
         Clients already in to_zone are also counted as done. Return FAILURE if timeout expires.
      7. Wait for all loading screens to finish.
      8. Wait for all clients to arrive in to_zone. Return FAILURE if any are not.

    FAILURE: clients not in from_zone; interact prompt never appeared after MAX_RETRIES;
    clients did not enter a loading screen within the timeout; not all clients arrived in to_zone.
    """
    from_zone: str = params["from_zone"]
    to_zone: str = params["to_zone"]
    interact = XYZ(**params["interact_xyz"])
    reset = XYZ(**params["reset_xyz"])

    machine.log(
        f"enter_zone_via_object: {from_zone!r} → {to_zone!r} via XYZ({interact.x:.1f}, {interact.y:.1f}, {interact.z:.1f})"
    )

    # 1. Verify all clients are in from_zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if not all(node_helpers.zone_matches(z, from_zone) for z in zones):
        wrong = [z for z in zones if not node_helpers.zone_matches(z, from_zone)]
        machine.log(
            f"FAILURE: clients not in expected zone (expected={from_zone!r}, unexpected={wrong})",
            logging.WARNING,
        )
        return "FAILURE"

    safety_delay: float = machine.get_option("Safety Delay", 3.0)

    # 2. Clear any pre-existing interact prompt before starting.
    prompts = await asyncio.gather(*[c.is_in_npc_range() for c in clients])
    if any(prompts):
        stale = [i for i, p in enumerate(prompts) if p]
        machine.log(
            f"Pre-existing interact prompt on client(s) {stale}; teleporting to reset_xyz to clear it",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for c in clients])
        await node_helpers.poll_until(lambda: node_helpers.none_in_npc_range(clients), safety_delay, POLL_INTERVAL)

    # 3. Retry loop: teleport to interact_xyz, wait for prompt on all clients.
    prompt_appeared = False
    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(
            f"Attempt {attempt}/{MAX_RETRIES}: teleporting clients to interact_xyz",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(interact) for c in clients])

        machine.log("Waiting for interact prompt on all clients…", logging.DEBUG)
        if await node_helpers.poll_until(lambda: node_helpers.all_in_npc_range(clients), safety_delay, POLL_INTERVAL):
            prompt_appeared = True
            break

        machine.log(
            f"Interact prompt did not appear for all clients (attempt {attempt}); resetting",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for c in clients])

    if not prompt_appeared:
        machine.log(
            f"FAILURE: interact prompt never appeared after {MAX_RETRIES} attempts",
            logging.WARNING,
        )
        return "FAILURE"

    # 4. Press X for all clients simultaneously.
    machine.log("Pressing X to interact", logging.DEBUG)
    await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in clients])

    # 4b. Answer the instance prompt if interacting raised one (e.g. leaving a dungeon).
    machine.log("Checking for an instance prompt…", logging.DEBUG)
    if refusal := await node_helpers.answer_instance_prompts(clients, safety_delay, POLL_INTERVAL):
        machine.log(
            f"FAILURE: the game refused the zone change — {dialog_captions.describe(refusal)}",
            logging.WARNING,
        )
        return "FAILURE"

    # 5. Re-press X for any client that hasn't entered a loading screen yet.
    #    Track which clients have *ever* been seen loading rather than requiring
    #    all to be loading simultaneously — a fast client can finish its loading
    #    screen (is_loading() → False) before a slow client even starts, which
    #    would otherwise prevent the all-loading condition from ever being met.
    #
    #    Also treat a client already in to_zone as "seen loading" — if the dungeon
    #    exit warning check (step 4b) delayed us long enough that the loading screen
    #    completed before we started polling, the client is already in the destination
    #    zone and we should not require it to show a loading screen again.
    machine.log(
        f"Waiting for all clients to enter loading screen (timeout={TRIGGER_TIMEOUT}s)…",
        logging.DEBUG,
    )

    async def _loading_or_arrived(c) -> bool:
        return await c.is_loading() or node_helpers.zone_matches(await c.zone_name(), to_zone)

    seen_loading = await node_helpers.poll_each_until_seen(
        clients,
        check_fn=_loading_or_arrived,
        action_fn=lambda c: c.send_key(Keycode.X, 0.1),
        timeout=TRIGGER_TIMEOUT,
        poll_interval=POLL_INTERVAL,
    )
    if len(seen_loading) < len(clients):
        not_triggered = [i for i in range(len(clients)) if i not in seen_loading]
        machine.log(f"Clients not loading at timeout: {not_triggered}", logging.DEBUG)
        machine.log(
            f"FAILURE: clients did not enter loading screen within {TRIGGER_TIMEOUT}s",
            logging.WARNING,
        )
        return "FAILURE"

    # 6. Wait for loading screens to finish.
    machine.log("Loading screen active, waiting for it to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL)

    # 7. Wait for all clients to arrive in to_zone.
    if (actual := await node_helpers.wait_for_zone(clients, to_zone, ZONE_CHANGE_TIMEOUT, POLL_INTERVAL)) is not None:
        machine.log(f"Zones after interaction: {actual}", logging.DEBUG)
        machine.log(
            f"FAILURE: not all clients in expected zone after transition "
            f"(expected={to_zone!r}, got={list(set(actual))})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {to_zone!r}")
    return "SUCCESS"
