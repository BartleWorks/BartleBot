"""Node: interact with an NPC or object that triggers a zone change."""

import asyncio
import logging

from wizwalker import XYZ, Keycode

from bartlebot import dialog_captions, player_realms
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import (
    MAX_RETRIES,
    OBJECT_USE_DELAY,
    POLL_INTERVAL,
    TRIGGER_TIMEOUT,
    ZONE_CHANGE_TIMEOUT,
)
from bartlebot.option_groups import GROUP_GENERAL

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="from_zone",
        type="zone",
        label="From Zone",
        help="Zone name players must currently be in when this node executes.",
        default="",
        required=True,
    ),
    ParamField(
        key="to_zone",
        type="zone",
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
        default=None,
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
        default=None,
        required=True,
    ),
    ParamField(
        key="sequential",
        type="bool",
        label="Sequential Interaction",
        help=(
            "Have players use the object one at a time instead of together. Zone teleporters "
            "in particular often serve only one player at a time, and a second player "
            "interacting while the first is mid-interaction has their keypress swallowed. "
            "Each player waits at Reset XYZ, takes its turn, and is released as soon as its "
            "loading screen starts — the others do not wait for it to finish loading, and all "
            "players are still confirmed in the destination zone together at the end."
        ),
        default=False,
        default_hint="All players interact at once.",
        required=False,
    ),
    ParamField(
        key="object_use_delay",
        type="float",
        label="Delay Between Object Uses (s)",
        help=(
            "Seconds to leave between one player finishing at the object and the next player starting "
            "on it, in Sequential Interaction. The object becoming free again has no readable signal, "
            "so this is the only cover for it — raise it for a teleporter that takes several seconds "
            "to reset between uses. Ignored unless Sequential Interaction is on."
        ),
        default=OBJECT_USE_DELAY,
        minimum=0.0,
        default_hint=f"{OBJECT_USE_DELAY}s between uses.",
        required=False,
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
            "Players were not in from_zone at the start, the interact prompt never appeared "
            "for any player after retries, or not all players arrived in to_zone (see usage "
            "notes — a player whose prompt or loading screen never came still gets a "
            "recovery attempt before this is reported).",
        ),
    ],
    required_bot_options=[
        player_realms.SPEC,
        BotOptionSpec(
            name="Safety Delay",
            group=GROUP_GENERAL,
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
    max_consecutive_retries=10,
    usage_notes=(
        "Turn on Sequential Interaction when the object only serves one player at a time — the "
        "symptom is that one player transitions and the others' X press does nothing, "
        "intermittently rather than every run. Zone teleporters are the common case.\n\n"
        "If some but not all players reach to_zone, the node first tries teleporting the "
        "stragglers to whichever player did arrive — the same recovery Teleport To Player "
        "If In Zone offers as a standalone node — before reporting FAILURE. That needs "
        "every player to already be mutual in-game friends."
    ),
)


async def _loading_or_arrived(client, to_zone: str) -> bool:
    """Return True once this client has committed to the transition.

    A fast client can finish its loading screen before a slow one has started, so arriving
    in to_zone counts as much as being on a loading screen — see the note in run() on why
    requiring is_loading() alone deadlocks.
    """
    return await client.is_loading() or node_helpers.zone_matches(await client.zone_name(), to_zone)


async def _await_arrival(machine, clients: list, to_zone: str) -> str:
    """Wait out the loading screens and confirm every client reached to_zone.

    The tail both modes share: however the clients were sent — together or in turn — they
    are only done when all of them are in the destination zone.
    """
    machine.log("Loading screen active, waiting for it to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL)

    if (
        actual := await node_helpers.wait_for_zone_with_recovery(
            machine, clients, to_zone, ZONE_CHANGE_TIMEOUT, POLL_INTERVAL
        )
    ) is not None:
        machine.log(f"Zones after interaction: {actual}", logging.DEBUG)
        machine.log(
            f"FAILURE: not all clients in expected zone after transition "
            f"(expected={to_zone!r}, got={list(set(actual))})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {to_zone!r}")
    return "SUCCESS"


async def _trigger_sequentially(
    machine, clients: list, interact, reset, to_zone: str, safety_delay: float, object_use_delay: float | None
) -> str | None:
    """Send each client through the object alone. Returns "FAILURE", or None to carry on.

    A client is released as soon as its transition is under way — a loading screen has
    started, or it is already in to_zone — rather than when it has finished loading. That
    is the moment the object is free again, and holding the next client past it would add
    a full loading screen per player for nothing. Waiting for the loading screens to clear
    and confirming the destination zone stay collective, back in run().
    """
    machine.log("Sequential interaction: clients take turns at the object", logging.DEBUG)
    refusals: list[str] = []

    async def _turn(client) -> bool:
        await client.send_key(Keycode.X, 0.1)
        # Costs the full Safety Delay per client when no dialog appears, which is the common
        # case — affordable only because _loading_or_arrived also accepts "already in
        # to_zone", so a loading screen missed during the wait is still recognized.
        if refusal := await node_helpers.answer_instance_prompts([client], safety_delay, POLL_INTERVAL):
            refusals.append(refusal)
            return False
        return await node_helpers.poll_until(
            lambda: _loading_or_arrived(client, to_zone), TRIGGER_TIMEOUT, POLL_INTERVAL
        )

    numbers = list(range(1, len(clients) + 1))
    results = await node_helpers.take_turns(
        clients,
        interact,
        reset,
        safety_delay,
        _turn,
        numbers=numbers,
        poll_interval=POLL_INTERVAL,
        object_use_delay=object_use_delay,
        # The client leaves the zone under its own power, so there is nothing to park —
        # and a teleport aimed at a client already on a loading screen goes nowhere good.
        park_after_turn=False,
        log=machine.log,
    )
    unprompted, unfinished = node_helpers.summarize_turns(results, numbers)

    # One player's failed turn is not the group's: whoever went through is already on the
    # other side, and the arrival check is what decides what happens to the rest.
    behind = sorted(set(unprompted) | set(unfinished))
    if behind and len(behind) < len(clients):
        machine.log(
            f"Client(s) {behind} did not go through the object; continuing to the arrival check",
            logging.INFO,
        )
        return None

    if refusals:
        machine.log(
            f"FAILURE: the game refused the zone change — {dialog_captions.describe(refusals[0])}",
            logging.WARNING,
        )
        return "FAILURE"
    if unprompted:
        machine.log(
            f"FAILURE: interact prompt never appeared for client(s) {unprompted} after {MAX_RETRIES} attempts",
            logging.WARNING,
        )
        return "FAILURE"
    if unfinished:
        machine.log(
            f"FAILURE: client(s) {unfinished} did not enter a loading screen within {TRIGGER_TIMEOUT}s",
            logging.WARNING,
        )
        return "FAILURE"
    return None


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.is_in_npc_range(),
          client.send_key(Keycode.X), client.is_loading()

    Flow:
      1. Verify all clients are in from_zone. Return FAILURE if not.
      2s. If sequential, _trigger_sequentially sends each client through the object alone,
         replacing steps 2–5; steps 6–8 (_await_arrival) run either way.
      2. Clear any pre-existing interact prompt by teleporting to reset_xyz.
      3. Retry loop (up to MAX_RETRIES): teleport to interact_xyz, wait for the interact
         prompt on all clients. If it does not appear, teleport to reset_xyz and retry. If
         some but not all clients ever get the prompt, carry on with just those — not failed
         outright — and let the recovery-aware arrival check cover the rest. Only fail here
         if not a single client ever got it.
      4. Press X on the clients that have the prompt.
      5. Dismiss any dungeon exit warning dialog that appears.
      6. Poll until those clients have entered a loading screen, re-pressing X for
         stragglers. Clients already in to_zone are also counted as done. A client that
         never enters a loading screen is not failed here — see step 8.
      7. Wait for all loading screens to finish.
      8. Wait for all clients (including any that never got the prompt) to arrive in to_zone,
         trying a teleport-to-player recovery for any straggler the moment a groupmate
         arrives. Return FAILURE if any are still not in to_zone once that is exhausted.

    FAILURE: clients not in from_zone; interact prompt never appeared for any client after
    MAX_RETRIES; not all clients arrived in to_zone after the recovery-aware arrival check.
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

    # Who shares whose realm, for straggler recovery in a Separate bot.
    await node_helpers.note_realm_peers(machine, clients)

    # 2s. Sequential mode replaces steps 2–5 entirely: each client approaches, interacts and
    #     commits to the transition alone. Steps 6–7 below are shared by both modes.
    if params.get("sequential", False):
        if (
            failure := await _trigger_sequentially(
                machine, clients, interact, reset, to_zone, safety_delay, params.get("object_use_delay")
            )
        ) is not None:
            return failure
        return await _await_arrival(machine, clients, to_zone)

    # 2–3. Clear a stale prompt, then teleport to interact_xyz until the prompt shows on
    #      every client. A miss is not yet a failure — see below.
    prompt_appeared = await node_helpers.reach_prompt(
        machine,
        clients,
        interact,
        reset,
        node_helpers.INTERACT_PROMPT,
        safety_delay,
        POLL_INTERVAL,
        fail_on_miss=False,
    )

    if prompt_appeared:
        ready = clients
    else:
        # Not every client raised the prompt after MAX_RETRIES — rather than failing the
        # whole group, press X for whichever clients did get it and let the recovery-aware
        # arrival check give the rest a teleport-to-player fallback, the same way enter_zone
        # does for a trigger that never fires.
        prompted = await asyncio.gather(*[c.is_in_npc_range() for c in clients])
        ready = [c for c, p in zip(clients, prompted) if p]
        if not ready:
            machine.log(
                f"FAILURE: interact prompt never appeared for any client after {MAX_RETRIES} attempts",
                logging.WARNING,
            )
            return "FAILURE"
        stuck = [i for i, p in enumerate(prompted) if not p]
        machine.log(
            f"Interact prompt never appeared for client(s) {stuck} after {MAX_RETRIES} attempts; "
            f"continuing with the {len(ready)} client(s) that did get it",
            logging.INFO,
        )

    # 4. Press X for whichever clients have the interact prompt.
    machine.log(f"Pressing X to interact ({len(ready)} client(s))", logging.DEBUG)
    await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in ready])

    # 4b. Answer the instance prompt if interacting raised one (e.g. leaving a dungeon).
    machine.log("Checking for an instance prompt…", logging.DEBUG)
    if refusal := await node_helpers.answer_instance_prompts(ready, safety_delay, POLL_INTERVAL):
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

    seen_loading = await node_helpers.poll_each_until_seen(
        ready,
        check_fn=lambda c: _loading_or_arrived(c, to_zone),
        action_fn=lambda c: c.send_key(Keycode.X, 0.1),
        timeout=TRIGGER_TIMEOUT,
        poll_interval=POLL_INTERVAL,
    )
    if len(seen_loading) < len(ready):
        not_triggered = [i for i in range(len(ready)) if i not in seen_loading]
        machine.log(f"Clients not loading at timeout: {not_triggered}", logging.DEBUG)
        machine.log(
            f"{len(not_triggered)} of {len(ready)} prompted client(s) did not enter a loading "
            f"screen within {TRIGGER_TIMEOUT}s; continuing to the recovery-aware arrival check",
            logging.DEBUG,
        )

    # 6. Some went through and some did not: most likely an object that serves one player at a
    #    time, so the rest get turns before anything else is tried for them.
    await _take_turns_if_contended(
        machine,
        clients,
        [ready[i] for i in seen_loading],
        interact,
        reset,
        from_zone,
        to_zone,
        safety_delay,
        params.get("object_use_delay"),
    )
    return await _await_arrival(machine, clients, to_zone)


async def _take_turns_if_contended(
    machine,
    clients: list,
    through: list,
    interact,
    reset,
    from_zone: str,
    to_zone: str,
    safety_delay: float,
    object_use_delay,
) -> None:
    """Send the clients a parallel use left behind through the object one at a time.

    An object that serves one player at a time swallows everyone else's keypress, and nothing
    in the data says which objects those are until someone has watched it happen -- so this
    catches it when it does, and says which object it was, since the Sequential Interaction
    flag is still what makes the next run quick.

    `through` is who the parallel press already saw loading or arrived. Only when someone went
    through: if nobody did, the object is not being shared badly, it is not working, and turns
    would not change that. The rest are read once more first, and a client that started
    loading after the press window closed gets no turn, since it is already on its way (#70).
    """
    if not through or len(through) == len(clients):
        return
    candidates = [c for c in clients if not any(c is t for t in through)]
    committed = await asyncio.gather(*[_loading_or_arrived(c, to_zone) for c in candidates])
    left = [c for c, done in zip(candidates, committed) if not done]
    if not left:
        return
    machine.log(
        f"{len(left)} of {len(clients)} client(s) did not go through {from_zone!r} → {to_zone!r} with the rest; "
        f"giving them turns at the object. If this keeps happening, the object serves one player at a time: "
        f"turn on Sequential Interaction, or mark the link sequential on the navigation map",
        logging.WARNING,
    )
    # Whatever the turns achieve, the arrival check that follows is what reports it.
    await _trigger_sequentially(machine, left, interact, reset, to_zone, safety_delay, object_use_delay)
