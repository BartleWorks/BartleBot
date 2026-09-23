"""Board a ride -- a boat, an airship, a dragon -- and press X to skip it to where it goes.

Not a node in the library: it is how a planned ``ride`` step is run
(``nodes/zone_navigation.step_call``), which resolves every param below from the navigation
map. Go To Zone is what a bot author reaches for instead. A ride only ever stands in a zone
the map declares, so there is nothing this could be pointed at that Go To Zone cannot
already plan a route to -- the same reason ``enter_zone_via_list`` was retired as a node
(#36). This one was retired in the node audit, with no bot left using it.

Params, as ``step_call`` passes them:

- ``from_zone`` -- where players must be at the start. A player already on the ride, in
  ``ride_zone``, or already in ``to_zone``, is accepted too.
- ``to_zone`` -- where the ride goes.
- ``board`` -- ``BOARD_OBJECT`` to interact with the ride (the Krokotopia boats, the
  Dragonspyre dragon) or ``BOARD_WALK`` to walk into a gate (the Marleybone airships).
- ``board_xyz`` -- where the interact prompt shows, or a point inside the gate's trigger.
- ``reset_xyz`` -- optional; a spot off the prompt. When set, a player whose boarding did
  not take is sent here and tries again, up to ``MAX_RETRIES`` times.
- ``ride_zone`` -- the zone the ride loads, when it loads one (the dragon's
  ``DS_A3_FlightPath``). Empty when the ride happens in ``from_zone`` itself; required when
  walking into a gate.

Once a player is aboard it is never teleported: the ride is carrying it, and a teleport
would pull it off, so retries happen only before boarding. A ride that stays in
``from_zone`` has no loading screen to say it was boarded, so a player counts as aboard
once, within the Safety Delay of pressing X, it has been moved off ``board_xyz``. Players
that do not all reach ``to_zone`` get the zone-arrival recovery (a port to one that did)
before the step fails.
"""

import asyncio
import logging
import math

from wizwalker import XYZ, Keycode

from bartlebot.nodes import node_helpers
from bartlebot.nodes.timing import (
    LOADING_SCREEN_TIMEOUT,
    MAX_RETRIES,
    POLL_INTERVAL,
    RIDE_TIMEOUT,
    TRIGGER_TIMEOUT,
    ZONE_CHANGE_TIMEOUT,
)

BOARD_OBJECT = "object"
BOARD_WALK = "walk"
BOARDED_DISTANCE = 150.0
"""How far off Board XYZ a player must be, after X, to count as carried away by a ride that stays in its zone."""


async def _on_its_way(client, ride_zone: str, to_zone: str) -> bool:
    """True once boarding has committed: loading, or already in the ride's zone or its destination."""
    if await client.is_loading():
        return True
    zone = await client.zone_name()
    return node_helpers.zone_matches(zone, to_zone) or bool(ride_zone) and node_helpers.zone_matches(zone, ride_zone)


async def _no_prompt(client) -> bool:
    return not await client.is_in_npc_range()


async def _press_x_if_prompted(client) -> None:
    if await client.is_in_npc_range():
        await client.send_key(Keycode.X, 0.1)


async def _carried_away(client, board_xyz: XYZ) -> bool:
    """True once the player is somewhere other than Board XYZ -- which, after X and no teleport, is the ride moving it."""
    here = await client.body.position()
    return math.dist((here.x, here.y, here.z), (board_xyz.x, board_xyz.y, board_xyz.z)) > BOARDED_DISTANCE


async def _board(
    machine,
    client,
    number: int,
    board: str,
    board_xyz: XYZ,
    reset: XYZ | None,
    ride_zone: str,
    to_zone: str,
    safety_delay: float,
) -> bool:
    """Put one player on the ride. Returns True once it is aboard.

    This is the only phase that teleports: to Board XYZ, and back to Reset XYZ for a retry.
    """

    async def committed() -> bool:
        return await _on_its_way(client, ride_zone, to_zone)

    async def aboard() -> bool:
        # A ride that stays in from_zone loads nothing on boarding. Nothing here teleports the
        # player after X, so being moved off Board XYZ means the boat has it. Its skip prompt
        # coming up after that is the skip phase's to press, not this one's.
        return await committed() or (not ride_zone and await _carried_away(client, board_xyz))

    if await committed():
        return True
    attempts = MAX_RETRIES if reset else 1
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            if await committed():
                return True
            machine.log(f"Player {number}: boarding did not take; retrying ({attempt}/{attempts})", logging.DEBUG)
        # Clear a prompt left over from somewhere else before trusting the one at Board XYZ.
        if reset and (attempt > 1 or (board == BOARD_OBJECT and await client.is_in_npc_range())):
            await client.teleport(reset)
            if board == BOARD_OBJECT:
                await node_helpers.poll_until(lambda: _no_prompt(client), safety_delay, POLL_INTERVAL)
        await client.teleport(board_xyz)

        if board == BOARD_WALK:
            if await node_helpers.poll_until(committed, safety_delay, POLL_INTERVAL):
                return True
            continue

        if not await node_helpers.poll_until(client.is_in_npc_range, safety_delay, POLL_INTERVAL):
            continue
        await client.send_key(Keycode.X, 0.1)
        seen = await node_helpers.poll_each_until_seen(
            [client],
            check_fn=lambda _c: aboard(),
            action_fn=node_helpers.skip_while_loading(_press_x_if_prompted),
            # A loading screen can be slow to start; a boat moves the player within moments, or not at all.
            timeout=TRIGGER_TIMEOUT if ride_zone else safety_delay,
            poll_interval=POLL_INTERVAL,
        )
        if seen:
            return True
    return await committed()


async def _skip(machine, client, number: int, ride_zone: str, to_zone: str, safety_delay: float) -> bool:
    """Press X on the skip prompt until the player is on its way to to_zone. Never teleports.

    Returns False if the player is still on the ride after RIDE_TIMEOUT.
    """
    # A ride with its own zone starts with a loading screen into it, and that one is not the
    # way out: let it clear before a loading screen can count as leaving.
    await node_helpers.poll_until(lambda: node_helpers.none_loading([client]), LOADING_SCREEN_TIMEOUT, POLL_INTERVAL)

    async def leaving() -> bool:
        return await client.is_loading() or node_helpers.zone_matches(await client.zone_name(), to_zone)

    async def prompted_or_leaving() -> bool:
        return await client.is_in_npc_range() or await leaving()

    if not await node_helpers.poll_until(prompted_or_leaving, safety_delay, POLL_INTERVAL):
        machine.log(
            f"Player {number}: no skip prompt within the Safety Delay; still watching for it until the ride ends",
            logging.DEBUG,
        )
    seen = await node_helpers.poll_each_until_seen(
        [client],
        check_fn=lambda _c: leaving(),
        action_fn=node_helpers.skip_while_loading(_press_x_if_prompted),
        timeout=RIDE_TIMEOUT,
        poll_interval=POLL_INTERVAL,
    )
    return bool(seen)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.is_in_npc_range(),
          client.send_key(Keycode.X), client.is_loading()

    Flow:
      1. Verify every client is in from_zone, ride_zone or to_zone. Return FAILURE if not.
      2. Board, per client and in parallel, for those in from_zone: teleport to board_xyz,
         then either wait for the gate to load the ride zone, or wait for the prompt and press
         X until the client is aboard. Retry from reset_xyz up to MAX_RETRIES when it is set.
         Fail only if no client boarded; the rest are left to the arrival check.
      3. Skip, per client and in parallel: once any boarding load has cleared, press X whenever
         the prompt shows until the client is loading or in to_zone, for up to RIDE_TIMEOUT.
      4. Wait for loading screens to clear, then for every client to arrive in to_zone, with
         teleport-to-player recovery for stragglers.
    """
    from_zone: str = params["from_zone"]
    to_zone: str = params["to_zone"]
    board: str = params.get("board") or BOARD_OBJECT
    board_xyz = XYZ(**params["board_xyz"])
    reset = XYZ(**params["reset_xyz"]) if params.get("reset_xyz") else None
    ride_zone: str = params.get("ride_zone") or ""

    machine.log(
        f"enter_zone_via_ride: {from_zone!r} → {to_zone!r}"
        + (f" through {ride_zone!r}" if ride_zone else "")
        + f", boarded by {board} at XYZ({board_xyz.x:.1f}, {board_xyz.y:.1f}, {board_xyz.z:.1f})"
    )
    if board not in (BOARD_OBJECT, BOARD_WALK):
        machine.log(f"FAILURE: unknown way to board a ride: {board!r}", logging.WARNING)
        return "FAILURE"
    if board == BOARD_WALK and not ride_zone:
        machine.log("FAILURE: a ride boarded through a gate needs its Ride Zone", logging.WARNING)
        return "FAILURE"

    # 1. Verify every client is somewhere this ride can start or finish from.
    zones = list(await asyncio.gather(*[c.zone_name() for c in clients]))
    machine.log(f"Current client zones: {zones}", logging.DEBUG)
    allowed = [zone for zone in (from_zone, ride_zone, to_zone) if zone]
    if wrong := [z for z in zones if not any(node_helpers.zone_matches(z, zone) for zone in allowed)]:
        machine.log(
            f"FAILURE: clients not in expected zone (expected={allowed!r}, unexpected={wrong})", logging.WARNING
        )
        return "FAILURE"

    safety_delay: float = machine.get_option("Safety Delay", 3.0)

    # 2. Board.
    async def _boarded(index: int, client) -> bool:
        if not node_helpers.zone_matches(zones[index], from_zone):
            return True
        return await _board(machine, client, index + 1, board, board_xyz, reset, ride_zone, to_zone, safety_delay)

    aboard = await asyncio.gather(*[_boarded(i, c) for i, c in enumerate(clients)])
    riders = [(i + 1, c) for i, (c, ok) in enumerate(zip(clients, aboard)) if ok]
    if not riders:
        machine.log("FAILURE: no player could board the ride", logging.WARNING)
        return "FAILURE"
    if len(riders) < len(clients):
        stuck = [i + 1 for i, ok in enumerate(aboard) if not ok]
        machine.log(f"Player(s) {stuck} could not board; continuing to the arrival check", logging.INFO)

    # 3. Skip.
    skipped = await asyncio.gather(*[_skip(machine, c, n, ride_zone, to_zone, safety_delay) for n, c in riders])
    if not all(skipped):
        still = [n for (n, _), ok in zip(riders, skipped) if not ok]
        machine.log(
            f"Player(s) {still} still on the ride after {RIDE_TIMEOUT:.0f}s; continuing to the arrival check",
            logging.INFO,
        )

    # 4. Arrive.
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL)
    if (
        actual := await node_helpers.wait_for_zone_with_recovery(
            machine, clients, to_zone, ZONE_CHANGE_TIMEOUT, POLL_INTERVAL
        )
    ) is not None:
        machine.log(
            f"FAILURE: not all clients in expected zone after the ride (expected={to_zone!r}, got={list(set(actual))})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {to_zone!r}")
    return "SUCCESS"
