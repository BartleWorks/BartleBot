"""Node: interact with an NPC or object that triggers a zone change."""

import asyncio
import logging

from wizwalker import XYZ, Keycode

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
        key="to_zone",
        type="str",
        label="To Zone",
        help="Zone name clients must be in after this node completes.",
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
        "a snowglobe or portal). Teleports clients to the interact point, waits for "
        "the prompt, presses X for all clients, and re-presses X for any client that "
        "hasn't started loading — until all clients are in the destination zone."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All clients are now in to_zone."),
        ReturnSpec(
            "FAILURE",
            "Clients were not in from_zone at the start, the interact prompt never "
            "appeared after retries, clients did not enter a loading screen within "
            "the timeout, or not all clients arrived in to_zone.",
        ),
    ],
)

from bartlebot.nodes.timing import INTERACT_TIMEOUT, MAX_RETRIES, POLL_INTERVAL, TRIGGER_TIMEOUT, ZONE_CHANGE_TIMEOUT


async def run(machine, clients: list, params: dict) -> str:
    """
    Interact with an NPC or object that causes a zone change.

    Params:
        from_zone (str):    Zone clients must be in when this node is called.
        to_zone (str):      Zone clients must be in when this node completes.
        interact_xyz (dict): {"x", "y", "z"} — coordinates of the interactable.
        reset_xyz (dict):   {"x", "y", "z"} — coordinates used to dismiss the prompt.
    """
    from_zone: str = params["from_zone"]
    to_zone: str = params["to_zone"]
    interact = XYZ(**params["interact_xyz"])
    reset = XYZ(**params["reset_xyz"])

    machine.log(
        f"interact_to_zone: {from_zone!r} → {to_zone!r} via XYZ({interact.x:.1f}, {interact.y:.1f}, {interact.z:.1f})"
    )

    # 1. Verify all clients are in from_zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if not all(z == from_zone for z in zones):
        wrong = [z for z in zones if z != from_zone]
        machine.log(
            f"FAILURE: clients not in expected zone (expected={from_zone!r}, unexpected={wrong})",
            logging.WARNING,
        )
        return "FAILURE"

    # 2. Clear any pre-existing interact prompt before starting.
    prompts = await asyncio.gather(*[c.is_in_npc_range() for c in clients])
    if any(prompts):
        stale = [i for i, p in enumerate(prompts) if p]
        machine.log(
            f"Pre-existing interact prompt on client(s) {stale}; teleporting to reset_xyz to clear it",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for c in clients])
        await node_helpers.wait_until(lambda: node_helpers.none_in_npc_range(clients), INTERACT_TIMEOUT, POLL_INTERVAL)

    # 3. Retry loop: teleport to interact_xyz, wait for prompt on all clients.
    prompt_appeared = False
    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(
            f"Attempt {attempt}/{MAX_RETRIES}: teleporting clients to interact_xyz",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(interact) for c in clients])

        machine.log("Waiting for interact prompt on all clients…", logging.DEBUG)
        if await node_helpers.wait_until(
            lambda: node_helpers.all_in_npc_range(clients), INTERACT_TIMEOUT, POLL_INTERVAL
        ):
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

    # 5. Re-press X for any client that hasn't entered a loading screen yet.
    #    Track which clients have *ever* been seen loading rather than requiring
    #    all to be loading simultaneously — a fast client can finish its loading
    #    screen (is_loading() → False) before a slow client even starts, which
    #    would otherwise prevent the all-loading condition from ever being met.
    machine.log(
        f"Waiting for all clients to enter loading screen (timeout={TRIGGER_TIMEOUT}s)…",
        logging.DEBUG,
    )
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
            not_triggered = [i for i in range(len(clients)) if i not in seen_loading]
            machine.log(f"Clients not loading at timeout: {not_triggered}", logging.DEBUG)
            machine.log(
                f"FAILURE: clients did not enter loading screen within {TRIGGER_TIMEOUT}s",
                logging.WARNING,
            )
            return "FAILURE"
        not_loading_clients = [c for i, (c, _l) in enumerate(zip(clients, loading)) if i not in seen_loading]
        await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in not_loading_clients])
        await asyncio.sleep(POLL_INTERVAL)

    # 6. Wait for loading screens to finish.
    machine.log("Loading screen active, waiting for it to clear…", logging.DEBUG)
    await node_helpers.wait_until(lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL)

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
