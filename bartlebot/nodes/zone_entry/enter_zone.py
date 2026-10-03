import asyncio
import logging

from wizwalker import XYZ

from bartlebot import dialog_captions, player_realms
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
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
        key="xyz",
        type="xyz",
        label="Transition XYZ",
        help=(
            "A point inside the destination zone's transition bounding box. "
            "Teleporting players here triggers the automatic zone change."
        ),
        default=None,
        required=True,
    ),
    ParamField(
        key="reset_xyz",
        type="xyz",
        label="Reset XYZ",
        help=(
            "Where players stand to re-arm the trigger: a spot clear of the transition "
            "bounding box. When the zone change does not fire (e.g. a trigger that needs a "
            "scripted event to finish first), the stuck players are teleported here and "
            "back, up to 3 attempts, each waiting the user's Safety Delay for the trigger "
            "to fire (see usage notes). Record captures it as the starting position."
        ),
        default=None,
        required=True,
    ),
    ParamField(
        key="stagger",
        type="bool",
        label="Stagger Players",
        help=(
            "Send the first player through the transition alone and wait for it to fully "
            "load into the destination zone before sending the rest. Prevents players from "
            "being split across different realms of the destination zone. Only takes effect "
            "when exactly 4 players are connected — with 3 or fewer this has not been observed "
            "to be an issue."
        ),
        default=False,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Transitions all players from one zone to another by teleporting them "
        "into the destination zone's transition bounding box. Optionally staggers "
        "the transition (lead player first, then the rest) when 4 players are "
        "connected, to avoid players landing in different realms."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec(
            "SUCCESS",
            "All players are now in to_zone.",
        ),
        ReturnSpec(
            "FAILURE",
            "Players were not in from_zone at the start, or not all players reached "
            "to_zone after the transition and its retries.",
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
        "reset_xyz is where players stand to re-arm the trigger, so it has to be clear of "
        "the transition bounding box — anywhere in from_zone the trigger does not reach. "
        "Record sets it to where the players stood when recording started. A caller that "
        "passes none (a route the zone navigation map builds, say) resets each player to "
        "where it stood when the node started instead.\n\n"
        "A player whose trigger never fires is not failed on its own: once reset_xyz's "
        "retries are exhausted, the node still waits for a loading screen and "
        "checks arrival, so a groupmate who did transition gives the straggler a working "
        "fallback — see the next paragraph.\n\n"
        "If some but not all players reach to_zone, the node first tries teleporting the "
        "stragglers to whichever player did arrive — the same recovery Teleport To Player "
        "If In Zone offers as a standalone node — before reporting FAILURE. That needs "
        "every player to already be mutual in-game friends."
    ),
)

from bartlebot.nodes.timing import MAX_RETRIES, POLL_INTERVAL, ZONE_CHANGE_TIMEOUT


async def _has_left_zone(client, zone: str) -> bool:
    # Through zone_matches, not !=: a client in a Challenge Mode copy of a literal from_zone,
    # or in a zone a glob from_zone names, has not left it (#143).
    return not node_helpers.zone_matches(await client.zone_name(), zone)


async def _any_confirmed_arrival(clients: list, to_zone: str) -> bool:
    """Return True if any client has both finished loading and landed in to_zone."""
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    loading = await asyncio.gather(*[c.is_loading() for c in clients])
    return any(node_helpers.zone_matches(z, to_zone) and not is_loading for z, is_loading in zip(zones, loading))


def _reset_point(data) -> XYZ | None:
    """The configured reset_xyz, or None when there is none to use: missing, malformed, or all-zero.

    All-zero is what an unset coordinate was saved as before the field could be blank (#100);
    taken literally it teleports every player to the zone's origin.
    """
    try:
        reset = XYZ(float(data["x"]), float(data["y"]), float(data["z"]))
    except (KeyError, TypeError, ValueError):
        return None
    return reset if (reset.x, reset.y, reset.z) != (0.0, 0.0, 0.0) else None


async def _transition_group(machine, group: list, from_zone: str, to_zone: str, target: XYZ, resets: list[XYZ]) -> str:
    """Teleport `group` into the transition bounding box and wait for them to arrive in to_zone.

    Runs the full teleport → dismiss dungeon dialog → leave-from_zone (with reset retry) →
    wait-for-loading → arrive-in-to_zone sequence for just the clients in `group`. `resets` is
    each client's reset point, in `group` order. Returns "SUCCESS" or "FAILURE".

    A client that never triggers the transition at all is not failed here: the arrival check
    at the end runs regardless, and wait_for_zone_with_recovery can still port a client stuck
    in from_zone straight to a groupmate who made it — a working transition elsewhere in the
    group is a better fix than retrying a trigger that has already failed a few times.
    """
    safety_delay = machine.get_option("Safety Delay", 3.0)
    attempts = MAX_RETRIES
    to_move = group
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            machine.log(f"Attempt {attempt}/{attempts}…", logging.DEBUG)

        # Teleport whichever clients still need to trigger the transition into the bounding
        # box — the whole group on the first attempt, only the stragglers on a retry.
        machine.log(f"Teleporting {len(to_move)} client(s) to transition bounding box", logging.DEBUG)
        await asyncio.gather(*[c.teleport(target) for c in to_move])

        # Answer the instance prompt if the transition raised one — leaving a dungeon asks
        # before it lets you go, and until that is answered the zone change never fires.
        machine.log("Checking for an instance prompt…", logging.DEBUG)
        refusal = await node_helpers.answer_instance_prompts(to_move, safety_delay, POLL_INTERVAL)
        if refusal:
            machine.log(
                f"FAILURE: the game refused the zone change — {dialog_captions.describe(refusal)}",
                logging.WARNING,
            )
            return "FAILURE"

        # Wait for the group to leave from_zone (zone change triggered by bounding box).
        # Re-teleport only stuck clients to the trigger on each poll; clients that have
        # already transitioned are not disturbed. Bounded by Safety Delay rather than the
        # full zone-change budget: this is only checking whether the trigger fired, not
        # waiting out a loading screen — that budget belongs to the arrival check below.
        #
        # A client on a loading screen has triggered but still reads from_zone, so the
        # retry is skipped for it: that teleport would be applied on arrival and drop the
        # client back at the bounding box in the zone it just reached (#70).
        machine.log(f"Waiting for clients to leave {from_zone!r}…", logging.DEBUG)
        seen_left = await node_helpers.poll_each_until_seen(
            group,
            check_fn=lambda c: _has_left_zone(c, from_zone),
            action_fn=node_helpers.skip_while_loading(lambda c: c.teleport(target)),
            timeout=safety_delay,
            poll_interval=POLL_INTERVAL,
            # Stop chasing the stragglers the moment a groupmate who already left from_zone
            # is confirmed in to_zone — the arrival check below can start recovery for them
            # immediately rather than waiting out the rest of this attempt's safety_delay.
            stop_fn=lambda left_clients: _any_confirmed_arrival(left_clients, to_zone),
        )
        if len(seen_left) == len(group):
            # Zone change triggered for everyone — no more retries needed.
            break

        stuck = [group[i] for i in range(len(group)) if i not in seen_left]
        left = [group[i] for i in range(len(group)) if i in seen_left]

        # A groupmate who already reached to_zone is a better recovery path than another
        # reset_xyz retry: stop burning attempts on a trigger that keeps failing and let
        # the arrival check below port the stragglers to the client that made it.
        if left and await _any_confirmed_arrival(left, to_zone):
            machine.log(
                f"At least one client already reached {to_zone!r}; skipping remaining trigger "
                f"retries for {len(stuck)} straggler(s) and moving to the recovery-aware arrival check",
                logging.INFO,
            )
            break

        stuck_zones = await asyncio.gather(*[c.zone_name() for c in stuck])
        machine.log(f"Client zones at timeout: {list(stuck_zones)}", logging.DEBUG)
        if attempt < attempts:
            machine.log(
                f"Trigger did not fire for {len(stuck)} client(s) after {safety_delay:.1f}s; "
                "resetting them and retrying…",
                logging.WARNING,
            )
            await asyncio.gather(*[group[i].teleport(resets[i]) for i in range(len(group)) if i not in seen_left])
            to_move = stuck
            continue

        # Retries exhausted — carry on to the arrival check rather than failing here; see
        # the docstring.
        machine.log(
            f"{len(stuck)} client(s) never left {from_zone!r}; continuing to the arrival check",
            logging.DEBUG,
        )
        break

    # Wait for loading screens to finish.
    machine.log("Waiting for loading screen to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading(group), None, POLL_INTERVAL)

    # Wait for the group to arrive in to_zone.
    if (
        actual := await node_helpers.wait_for_zone_with_recovery(
            machine, group, to_zone, ZONE_CHANGE_TIMEOUT, POLL_INTERVAL
        )
    ) is not None:
        machine.log(f"Zones after transition: {actual}", logging.DEBUG)
        machine.log(
            f"FAILURE: clients not in expected zone after transition (expected={to_zone!r}, got={list(set(actual))})",
            logging.WARNING,
        )
        return "FAILURE"

    return "SUCCESS"


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.is_loading()

    Flow:
      1. Verify all clients are in from_zone. Return FAILURE if not.
      2. If stagger is enabled and exactly 4 clients are connected: transition the lead
         client (clients[0]) alone first, then the remaining 3 — each phase running its
         own teleport/dismiss-dialog/leave-zone/load/arrive-zone sequence (see
         _transition_group). Otherwise all clients transition together in one phase.
      3. Each phase: asyncio.gather teleport clients into the transition bounding box (xyz),
         dismiss any dungeon exit warning dialog, poll until all have left from_zone
         (re-teleporting stragglers, retrying via reset_xyz up to MAX_RETRIES on timeout —
         or via each client's starting position when no reset_xyz is given),
         wait for loading screens to finish, then wait for arrival in to_zone.

    FAILURE: clients not in from_zone at start; a phase's zone change timed out after all
    retry attempts; not all clients in a phase arrived in to_zone after the transition.
    """
    from_zone: str = params["from_zone"]
    to_zone: str = params["to_zone"]
    xyz_data: dict = params["xyz"]
    target = XYZ(xyz_data["x"], xyz_data["y"], xyz_data["z"])

    stagger: bool = bool(params.get("stagger", False)) and len(clients) == 4

    machine.log(f"enter_zone: {from_zone!r} → {to_zone!r} via XYZ({target.x:.1f}, {target.y:.1f}, {target.z:.1f})")

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

    # A bot has to set reset_xyz, but the code paths and the zone navigation map call this
    # without one. Where a player stands now is clear of the trigger -- it has not fired.
    if (reset := _reset_point(params.get("reset_xyz"))) is not None:
        resets = [reset] * len(clients)
    else:
        resets = list(await asyncio.gather(*[c.body.position() for c in clients]))
        machine.log("No reset_xyz; a retry resets each client to where it started", logging.DEBUG)

    # Who shares whose realm, for straggler recovery in a Separate bot.
    await node_helpers.note_realm_peers(machine, clients)

    if stagger:
        machine.log("enter_zone: staggering — sending lead client through first", logging.DEBUG)
        lead_result = await _transition_group(machine, clients[:1], from_zone, to_zone, target, resets[:1])
        if lead_result == "FAILURE":
            machine.log("FAILURE: lead client failed to transition during staggered entry", logging.WARNING)
            return "FAILURE"
        machine.log("enter_zone: lead client settled in destination zone, sending remaining clients", logging.DEBUG)
        result = await _transition_group(machine, clients[1:], from_zone, to_zone, target, resets[1:])
    else:
        result = await _transition_group(machine, clients, from_zone, to_zone, target, resets)

    if result == "SUCCESS":
        machine.log(f"SUCCESS: all clients in {to_zone!r}")
    return result
