import asyncio
import contextlib
import logging

from bartleby.runtime.game import dialog_captions
from wizwalker import XYZ

from bartlebot.nodes import node_helpers
from bartlebot.nodes.help_text import ARRIVAL_NOTE, ZONE_AT_END, ZONE_AT_START
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, NodeSpec, ParamField, ReturnSpec
from bartlebot.options import player_realms

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="from_zone",
        type="zone",
        label="From Zone",
        help=ZONE_AT_START,
        default="",
        required=True,
    ),
    ParamField(
        key="to_zone",
        type="zone",
        label="To Zone",
        help=ZONE_AT_END,
        default="",
        required=True,
    ),
    ParamField(
        key="xyz",
        type="xyz",
        label="Transition XYZ",
        help="A spot inside the zone change's trigger. Teleporting here starts the zone change.",
        default=None,
        required=True,
    ),
    ParamField(
        key="reset_xyz",
        type="xyz",
        label="Reset XYZ",
        help=(
            "A spot clear of the trigger, where players step to re-arm it. A player whose zone change "
            "doesn't fire is moved here and back, up to three times."
        ),
        default=None,
        required=True,
    ),
    ParamField(
        key="stagger",
        type="bool",
        label="Stagger Players",
        help=(
            "When on, the first player goes through alone, and the rest follow once it has loaded, so "
            "all four end up on one realm. Applies only with four players."
        ),
        default=False,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Moves every player to another zone by teleporting them into the spot that triggers the "
        "zone change. Can send four players one at a time to keep them on one realm."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec(
            "SUCCESS",
            "Every player is in To Zone.",
        ),
        ReturnSpec(
            "FAILURE",
            "A player wasn't in From Zone at the start, or not every player reached To Zone.",
        ),
    ],
    required_bot_options=[
        player_realms.SPEC,
        SAFETY_DELAY_OPTION,
    ],
    max_consecutive_retries=10,
    usage_notes=(
        (
            "Keep Reset XYZ clear of the trigger. Record sets it to where players stood when "
            "recording started. Left empty, each player resets to where it stood when the node "
            "started."
        )
        + "\n\n"
        + ARRIVAL_NOTE
    ),
)

from bartleby.runtime.timing import MAX_RETRIES, POLL_INTERVAL, ZONE_CHANGE_TIMEOUT


async def _has_left_zone(client, zone: str) -> bool:
    # Through zone_matches, not !=: a client in a Challenge Mode copy of a literal from_zone,
    # or in a zone a glob from_zone names, has not left it (#143). A None zone has left, as
    # node_helpers.all_left_zone explains: this is only asked once the trigger has fired.
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


def _players(numbers: list[int]) -> str:
    return ", ".join(f"P{n}" for n in numbers) or "nobody"


async def _observe(client) -> tuple[str | None, bool | None, XYZ | None]:
    """A client's (zone, loading, position), each None when its read failed.

    A client on a loading screen can raise on a read, and that is a fact worth logging
    rather than an error: `_watch_group` shows it as the state it is in.
    """

    async def _read(read):
        try:
            return await read()
        except Exception:
            return None

    return tuple(await asyncio.gather(_read(client.zone_name), _read(client.is_loading), _read(client.body.position)))


async def _watch_group(machine, clients: list, numbers: list[int]) -> None:
    """Log each client's zone and loading state every time either changes, with where it stood.

    Diagnostic only. With Stagger on, one of the three followers sometimes never fires the
    trigger, or arrives and is back out again, and the node's own lines say only how many
    were left. This says who, in what order, and where each stood at the time, so a player
    standing in an exit trigger shows up as a position. It reads outside the node's own
    polls and decides nothing.
    """
    last: dict[int, tuple] = {}
    while True:
        observed = await asyncio.gather(*[_observe(c) for c in clients])
        for number, (zone, loading, position) in zip(numbers, observed):
            if last.get(number) == (zone, loading):
                continue
            last[number] = (zone, loading)
            where = f"({position.x:.0f}, {position.y:.0f}, {position.z:.0f})" if position else "unreadable"
            machine.log(
                f"P{number}: zone={zone.rsplit('/', 1)[-1] if zone else 'unreadable'} loading={loading} at {where}",
                logging.DEBUG,
            )
        await asyncio.sleep(POLL_INTERVAL)


async def _transition_group(
    machine, group: list, numbers: list[int], from_zone: str, to_zone: str, target: XYZ, resets: list[XYZ]
) -> str:
    """Teleport `group` into the transition bounding box and wait for them to arrive in to_zone.

    Runs the full teleport → dismiss dungeon dialog → leave-from_zone (with reset retry) →
    wait-for-loading → arrive-in-to_zone sequence for just the clients in `group`. `numbers` is
    each client's player number and `resets` its reset point, both in `group` order. Returns
    "SUCCESS" or "FAILURE".

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
        #
        # The retry answers an instance prompt before it teleports: a prompt that came up late,
        # or that the first answer missed, would otherwise sit there with the client reset
        # around it, and nothing leaves a dungeon until it is answered (#281).
        retry = node_helpers.ModalAwareRetry(
            node_helpers.skip_while_loading(lambda c: c.teleport(target)), recognized_only=True
        )
        seen_left = await node_helpers.poll_each_until_seen(
            group,
            check_fn=lambda c: _has_left_zone(c, from_zone),
            action_fn=retry,
            timeout=safety_delay,
            poll_interval=POLL_INTERVAL,
            # Stop chasing the stragglers the moment a groupmate who already left from_zone
            # is confirmed in to_zone — the arrival check below can start recovery for them
            # immediately rather than waiting out the rest of this attempt's safety_delay.
            stop_fn=lambda left_clients: _any_confirmed_arrival(left_clients, to_zone),
        )
        if retry.refusal:
            machine.log(
                f"FAILURE: the game refused the zone change — {dialog_captions.describe(retry.refusal)}",
                logging.WARNING,
            )
            return "FAILURE"
        if len(seen_left) == len(group):
            # Zone change triggered for everyone — no more retries needed.
            break

        stuck = [group[i] for i in range(len(group)) if i not in seen_left]
        left = [group[i] for i in range(len(group)) if i in seen_left]
        machine.log(
            f"Left {from_zone!r}: {_players([numbers[i] for i in sorted(seen_left)])}; "
            f"still in it: {_players([numbers[i] for i in range(len(group)) if i not in seen_left])}",
            logging.DEBUG,
        )

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
        machine.log(
            "Zones after transition: " + ", ".join(f"P{n} {z}" for n, z in zip(numbers, actual)),
            logging.DEBUG,
        )
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
    if not await node_helpers.in_expected_zone(machine, clients, from_zone, POLL_INTERVAL):
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

    numbers = list(range(1, len(clients) + 1))
    watcher = asyncio.create_task(_watch_group(machine, clients, numbers))
    try:
        if stagger:
            machine.log("enter_zone: staggering — sending lead client through first", logging.DEBUG)
            lead_result = await _transition_group(
                machine, clients[:1], numbers[:1], from_zone, to_zone, target, resets[:1]
            )
            if lead_result == "FAILURE":
                machine.log("FAILURE: lead client failed to transition during staggered entry", logging.WARNING)
                return "FAILURE"
            machine.log("enter_zone: lead client settled in destination zone, sending remaining clients", logging.DEBUG)
            result = await _transition_group(machine, clients[1:], numbers[1:], from_zone, to_zone, target, resets[1:])
        else:
            result = await _transition_group(machine, clients, numbers, from_zone, to_zone, target, resets)
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher

    if result == "SUCCESS":
        machine.log(f"SUCCESS: all clients in {to_zone!r}")
    return result
