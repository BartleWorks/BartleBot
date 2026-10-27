"""Diagnostic: walk a planned route by holding the forward key, around live mobs. (movement anti-cheat)

probe_walk_route.py plans a route from a zone's own files. This walks one: it plans from
wherever the wizard stands to --to, straightens the route to its turns, and follows it by
holding the forward key, reading the position back every tick. The position is only ever
read. It steers by writing the yaw: the server checks where a wizard is, not which way it
faces (the camera turns it at any rate), and the walk counts any yaw that reads back changed
a tick later. --turn-keys steers with the turn keys instead, measuring first which way the
left key turns the yaw and how fast.

THE KEYS are the account's own. Each movement action's key comes from, first found:
  - the client's memory, through clients/game_keys.key_map (the ahmarra-test-realm branch,
    for r811339 onward, where bindings belong to the account);
  - the install's Bin/config.xml, through game/game_keybindings (the live game before it);
  - the shipped defaults, W A D S.
--forward/--left/--right/--back override any of them.

MOBS. A watcher reads every mob's position about five times a second and estimates its
velocity. Each tick the walk projects itself HORIZON seconds along the route at the speed it
is walking, and each mob along its velocity, and asks whether any comes within the mob's own
trigger radius (its template's m_npcProximity) plus a margin that grows with how far ahead
it is looking. If going on is clear it goes on. If not, it plans a detour around every mob
where it is and where it will be shortly (a mob patrols at about 90 units a second, a wizard
walks at 600 or more, so next to a wizard a mob is nearly a standing obstacle) and takes it
if the detour has room. Only with no such detour does it compare standing still with backing
off, doing whichever keeps the most room, for MAX_WAIT at most. The route already keeps
outside every patrol where the zone has room; this is for where it has not: a narrow street a
patrol crosses. --shortest walks the route that ignores patrols, which is the way to test it.

STALLS. Forward held, and the wizard barely moved for STALL_WINDOW: it ran into something
the grid thinks is open. It backs off, marks the spot ahead impassable, replans from where
it stands, and goes on, up to MAX_RECOVERIES times. --test-stall builds the grid without its
cylinders (the trees), which makes stalls to recover from.

LINKS. A route may cross an in-zone link (probe_walk_route.Link): a teleport volume the
server moves a wizard out of, onto an arrival point. A leg ending in one ends at the volume's
center; the walk stands there until it is moved further than LINK_MOVED, then replans from
wherever it landed. Moved by nothing for LINK_WAIT, the link is dropped (a wrong pairing, or
a quest gate) and the walk replans without it. A volume often fires as the wizard crosses its
edge; a jump landing near where the pending link lands is taken as that link.

SPEED. The wizard's speed multiplier (a mount's, as a percentage) is read once, and the plan
assumes WIZARD_SPEED times it; once walking, the walk's own measured speed takes over.

OUTCOME (Unicorn Way, 2026-10-27): it walks. From the Commons gate to T1, 26 legs and 20,614
units, it arrived 46 units from the goal in 35.2s against 35.5s planned: median 9 units off
the route, max 68, never nearer than 253 beyond a trigger radius, no jumps, no stalls, no
battle, never once stopping to turn. A turns the yaw down, at about 152 degrees a second.
The first full attempt stalled on a tree, which is how probe_walk_route.py found it had a
cylinder's length and radius swapped; see blocking_footprints.

Housing_FarmHouse/Interior, the same day: from the main floor down two flights to the basement
(z 3100 to 2718 to 2300), 18 units from the goal in 13.1s against 11.3s, stopping twice to turn
on the landing. Back up writing the yaw: 16 units off in 11.3s against 11.3s, median 5 units
off the route and max 28, no stop to turn, and none of the 12 yaws written read back changed.
So the yaw is the wizard's own to set, and writing it steers better than the turn keys.

Unicorn Way again, on a +40% mount: 840 units a second expected, 838 measured. The keys came
from Bin/config.xml. Twice the server threw the wizard back mid-walk (585 and 644 units, to
where it stood some 0.7s before), the reset that ordinary play sees; reading the position
back every tick absorbed both, re-walking the stretch. With --test-stall a wizard grazing a
trunk slides around it, the yaw re-aimed each tick; aimed at a trunk's middle it stalls, and
then it backed off, closed 23 spots ahead, replanned around in 2 legs and arrived 4 units
off. Half a second of the backward key had carried it 413 units, so a back-off is now by
distance. Mobs: Unicorn Way's ghosts walk their loops at 89 units a second and never pause.
Waiting alone, --shortest down the Ghosts 02 corridor sat out its 30s, the loop never clear;
with detours, the same patrols were crossed to Street 1 House 1 in 29.2s (2 detours, 20.5s
waiting while a detour beside a ghost was too tight) and back to T1 in 11.2s (3 detours,
2.1s waiting), never nearer than 11 units beyond a trigger radius and margin, and no battle.

Triton Avenue, links: To TempestNexus, High_Ledge, Tele Down and Tele Up were each taken on a
walk, the server moving the wizard 835 to 7,597 units and leaving it there; each landed within
5 units of the arrival point its name paired it with. Most fired at the volume's edge. Two
stalls on the street, against something the collision file lacks, were recovered.

What else it watches for:
  - a jump: the wizard moved faster than walking allows. A server reset (rubber-band) looks
    like this, and so would a battle pulling the wizard to a seat.
  - a battle or a zone change: stops at once.

It stops by itself at the goal, or on any of those, and releases every key however it
stops, Ctrl+C included.

HOW TO RUN
    1. Log a wizard in, standing in a zone (anywhere: the route starts where it stands).
    2. Check the plan without moving:

         uv run --with numpy python tools/probe_walk_input.py --to "WC-UW T1 Exit" --dry-run

    3. Walk it, hands off the keyboard:

         uv run --with numpy python tools/probe_walk_input.py --to "WC-UW T1 Exit"
         uv run --with numpy python tools/probe_walk_input.py --to "WC-UW T1 Exit" --shortest      # through patrols
         uv run --with numpy python tools/probe_walk_input.py --to "WC-UW T1 Exit" --test-stall    # into trees

Each walk writes logs/walk_route/walk-<time>.csv (time, position, yaw, waypoint, keys, what it
chose and the room it had; no names or ids), logs/walk_route/mobs-<time>.csv (each mob's
game id, template and position as read), and draws the trail over the route on walk-<time>.png.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import math
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_walk_route import REPO, WIZARD_SPEED, Planner, build_planner, render, resolve_point  # noqa: E402

TICK = 0.05
# Waypoints are passed within this; the last one must be reached within ARRIVE_GOAL.
ARRIVE_TURN = 80.0
ARRIVE_GOAL = 50.0
# Turn keys only: above TURN_IN_PLACE stop and turn; under DEADBAND leave the heading alone.
TURN_IN_PLACE = math.radians(50)
DEADBAND = math.radians(4)
# Movement faster than this between two reads is a jump. A wizard walked Unicorn Way at a
# median 600 units a second, and stretches ran past 800; the reads themselves can stall for
# half a second, so the test is speed over the real gap, never distance per tick.
JUMP_SPEED = 1600.0
STALL_WINDOW = 1.5
STALL_MIN_MOVE = 60.0
CALIBRATE_HOLD = 0.4
# A yaw written that reads back further off than this a tick later was undone.
YAW_UNDONE = math.radians(10)
SETTLE = 0.3
BATTLE_POLL = 0.5

# Stalls: back off this far (or for at most BACK_OFF_LONGEST), then close the floor this far
# ahead within this radius, and replan. By distance, not time: on a +40% mount half a second
# of the backward key covered 413 units, the backward key walking near forward speed.
MAX_RECOVERIES = 3
BACK_OFF = 150.0
BACK_OFF_LONGEST = 1.0
BLOCK_AHEAD = 80.0
BLOCK_RADIUS = 110.0

# Mobs: the list is re-read every MOB_REFRESH (it reads every entity's behaviors), positions every MOB_POLL.
MOB_REFRESH = 2.0
MOB_POLL = 0.2
# A mob's velocity is smoothed this much toward each new reading, and capped; a mob
# yanked across the map (a respawn) is a new mob, not a fast one.
MOB_SMOOTHING = 0.5
MOB_MAX_SPEED = 600.0
# Mobs further than this, or this far above or below, are not watched.
MOB_RANGE = 4000.0
MOB_LEVEL = 300.0
# Unknown mobs (no patrol in this zone's spawn data, such as a boss) get the largest radius seen.
DEFAULT_TRIGGER = 275.0
# How far ahead to look, in what steps, and the room wanted beyond a trigger radius: a fixed
# margin for lag and body radii, and more for every second ahead a guess reaches.
HORIZON = 2.5
LOOK_STEP = 0.1
MARGIN = 120.0
# Unicorn Way's ghosts walk a steady 89 a second, so a straight-line guess drifts slowly;
# 80 here made every detour beside a ghost read as too tight by the time it was checked.
MARGIN_PER_SECOND = 40.0
# Once stopped for a mob, go on only with this much room to spare, so the walk does not flap.
RESUME_SLACK = 60.0
# Backing off is the backward key. On a +40% mount it backed about 413 units in half a second
# (some 0.7 of forward speed, allowing for key latency); the log measures it on every walk.
BACK_SPEED_FACTOR = 0.7
# Stop rather than wait on mobs longer than this in all: a mob parked across the only way.
MAX_WAIT = 30.0
# Links (probe_walk_route.Link): standing in a volume, wait this long to be moved before
# dropping the link; a move further than LINK_MOVED is the server's; a jump landing within
# LINK_LANDED of where a pending link lands is that link, taken early at the volume's edge.
LINK_WAIT = 6.0
LINK_MOVED = 300.0
LINK_LANDED = 600.0
# A mob walks its patrol at about 90 units a second (Unicorn Way's ghosts, never pausing), a
# wizard at 600, or 840 mounted: next to a wizard a mob is nearly a standing obstacle. So when
# the way ahead is short of room, the walk first plans a detour around every mob, closed out
# wherever it will be over HORIZON to the room that room() asks for there, plus DETOUR_SLACK,
# at most once every DETOUR_EVERY. Only with no detour does it wait or back off. A detour
# always weighs the patrols, even on --shortest: it exists because mobs are in the way, so it
# should take the edges they keep off. Discs smaller than room() asks for found 31 detours in
# a row too tight in the Ghosts 02 corridor.
DETOUR_SLACK = 40.0
DETOUR_EVERY = 1.0

MOVE_ACTIONS = ("MoveForward", "MoveLeft", "MoveRight", "MoveBackward")
DEFAULT_MOVE_KEYS = {"MoveForward": "W", "MoveLeft": "A", "MoveRight": "D", "MoveBackward": "S"}
_NAMED_KEYS = {"up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "space": 0x20}


def wrap(angle: float) -> float:
    """An angle in (-pi, pi]."""
    return (angle + math.pi) % math.tau - math.pi


def heading_to(x: float, y: float, tx: float, ty: float) -> float:
    # wizwalker.utils.calculate_perfect_yaw: 0 faces -y, counterclockwise.
    return math.atan2(-(tx - x), -(ty - y)) % math.tau


def facing(yaw: float) -> tuple[float, float]:
    """The unit step a wizard with this yaw takes walking forward."""
    return -math.sin(yaw), -math.cos(yaw)


# --- keys ----------------------------------------------------------------------------------


def key_code(name: str) -> int | None:
    """The virtual-key code of a bare key as config.xml or a flag writes it: ``W``, ``up``. None
    for a key with a modifier, which a key sent as a window message cannot carry."""
    if "+" in name:
        return None
    if len(name) == 1 and name.isalnum():
        return ord(name.upper())
    return _NAMED_KEYS.get(name.lower())


async def movement_keys(client) -> tuple[dict[str, int], str]:
    """Each movement action's key, and where they came from."""
    codes: dict[str, int] = {}
    source = "the defaults"
    try:
        from bartlebot.clients.game_keys import key_map  # ahmarra-test-realm onward
    except ImportError:
        from bartlebot.game.game_keybindings import read_game_keybindings

        for binding in read_game_keybindings():
            action = binding.event.removesuffix("Start")
            if binding.event.endswith("Start") and action in MOVE_ACTIONS and action not in codes:
                if (code := key_code(binding.binding)) is not None:
                    codes[action] = code
        if codes:
            source = "Bin/config.xml"
    else:
        keys = await key_map(client)
        for action in MOVE_ACTIONS:
            if (code := keys.choose(action).code) is not None:
                codes[action] = code
        source = f"the client ({keys.source})"
    for action in MOVE_ACTIONS:
        codes.setdefault(action, key_code(DEFAULT_MOVE_KEYS[action]))
    return codes, source


class Keys:
    """The keys held on one window, by virtual-key code. A held key is re-sent every TICK, as a held key repeats."""

    def __init__(self, hwnd: int) -> None:
        from wizwalker.constants import WM_KEYDOWN, WM_KEYUP, user32
        from wizwalker.window_messages import send_message

        self.hwnd = hwnd
        self._send, self._user32, self._down, self._up = send_message, user32, WM_KEYDOWN, WM_KEYUP
        self.held: set[int] = set()
        self._task: asyncio.Task | None = None

    async def down(self, code: int) -> None:
        await self._send(self._user32.SendMessageW, self.hwnd, self._down, code, 0)

    async def up(self, code: int) -> None:
        await self._send(self._user32.SendMessageW, self.hwnd, self._up, code, 0)

    async def hold(self, code: int) -> bool:
        """Hold the key; True when it was not already held."""
        if code in self.held:
            return False
        self.held.add(code)
        await self.down(code)
        return True

    async def release(self, code: int) -> None:
        if code in self.held:
            self.held.discard(code)
            await self.up(code)

    async def pulse(self, code: int, seconds: float) -> None:
        await self.down(code)
        await asyncio.sleep(seconds)
        await self.up(code)

    async def _repeat(self) -> None:
        while True:
            await asyncio.sleep(TICK)
            for code in list(self.held):
                await self.down(code)

    def start(self) -> None:
        self._task = asyncio.create_task(self._repeat())

    async def release_all(self, codes) -> None:
        if self._task:
            self._task.cancel()
        self.held.clear()
        for code in codes:
            try:
                await self.up(code)
            except Exception:
                pass


async def read(client) -> tuple[float, float, float, float]:
    pos = await client.body.position()
    return pos.x, pos.y, pos.z, await client.body.yaw()


async def calibrate(client, keys: Keys, left: int, right: int) -> tuple[float, float]:
    """(sign, rate): the yaw's change in sign when the left key is held, and radians a second."""
    *_, before = await read(client)
    await keys.hold(left)
    await asyncio.sleep(CALIBRATE_HOLD)
    await keys.release(left)
    await asyncio.sleep(SETTLE)
    *_, after = await read(client)
    turned = wrap(after - before)
    await keys.hold(right)
    await asyncio.sleep(CALIBRATE_HOLD)
    await keys.release(right)
    await asyncio.sleep(SETTLE)
    if abs(turned) < math.radians(5):
        raise RuntimeError(f"the left key turned the wizard {math.degrees(turned):.1f} degrees; is it bound to turn?")
    return math.copysign(1.0, turned), abs(turned) / CALIBRATE_HOLD


# --- mobs ----------------------------------------------------------------------------------


@dataclass
class Mob:
    name: str
    trigger: float
    x: float
    y: float
    z: float
    t: float
    vx: float = 0.0
    vy: float = 0.0
    seen: int = 1


class MobWatch:
    """Every mob's last position and velocity, kept current in the background."""

    def __init__(self, client, triggers: dict[str, float], writer, started: float) -> None:
        self.client = client
        self.triggers = triggers
        self.writer = writer
        self.started = started
        self.mobs: dict[int, Mob] = {}
        self._entities: dict[int, tuple[object, str]] = {}
        self._task: asyncio.Task | None = None
        self.errors = 0

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _refresh(self) -> None:
        entities = {}
        for entity in await self.client.get_mobs():
            try:
                gid = await entity.global_id_full()
                entities[gid] = (entity, await entity.object_name())
            except Exception:
                continue
        self._entities = entities
        for gid in list(self.mobs):
            if gid not in entities:
                del self.mobs[gid]

    async def _run(self) -> None:
        last_refresh = -math.inf
        while True:
            now = time.monotonic() - self.started
            try:
                if now - last_refresh >= MOB_REFRESH:
                    await self._refresh()
                    last_refresh = now
                for gid, (entity, name) in list(self._entities.items()):
                    try:
                        loc = await entity.location()
                    except Exception:
                        self._entities.pop(gid, None)
                        self.mobs.pop(gid, None)
                        continue
                    t = time.monotonic() - self.started
                    self._update(gid, name, loc.x, loc.y, loc.z, t)
                    self.writer.writerow([f"{t:.3f}", gid, name, f"{loc.x:.1f}", f"{loc.y:.1f}", f"{loc.z:.1f}"])
            except asyncio.CancelledError:
                raise
            except Exception:
                self.errors += 1
            await asyncio.sleep(MOB_POLL)

    def _update(self, gid: int, name: str, x: float, y: float, z: float, t: float) -> None:
        mob = self.mobs.get(gid)
        if mob is None:
            self.mobs[gid] = Mob(name, self.triggers.get(name, DEFAULT_TRIGGER), x, y, z, t)
            return
        gap = t - mob.t
        if gap <= 0:
            return
        vx, vy = (x - mob.x) / gap, (y - mob.y) / gap
        speed = math.hypot(vx, vy)
        if speed > MOB_MAX_SPEED:
            vx = vy = 0.0  # moved too far to have walked: a respawn
        mob.vx += (vx - mob.vx) * MOB_SMOOTHING
        mob.vy += (vy - mob.vy) * MOB_SMOOTHING
        mob.x, mob.y, mob.z, mob.t = x, y, z, t
        mob.seen += 1

    def near(self, x: float, y: float, z: float) -> list[Mob]:
        return [
            m for m in self.mobs.values() if math.dist((m.x, m.y), (x, y)) <= MOB_RANGE and abs(m.z - z) <= MOB_LEVEL
        ]


def along(route: list[tuple[float, float, float]], target: int, x: float, y: float, distance: float):
    """Where a wizard at (x, y), heading for route[target], is after walking *distance* along the route."""
    px, py = x, y
    for k in range(target, len(route)):
        tx, ty = route[k][0], route[k][1]
        leg = math.dist((px, py), (tx, ty))
        if leg >= distance:
            f = distance / leg if leg else 0.0
            return px + (tx - px) * f, py + (ty - py) * f
        distance -= leg
        px, py = tx, ty
    return px, py


# The moments, in seconds from now, at which room() compares the wizard with each mob.
LOOK_AHEAD = tuple(k * LOOK_STEP for k in range(int(HORIZON / LOOK_STEP) + 1))


def room(path: list[tuple[float, float]], mobs: list[Mob], now: float) -> float:
    """The least room between the wizard, at path[k] LOOK_AHEAD[k] seconds from now, and any
    mob carried along its velocity, beyond that mob's trigger radius and the margin."""
    least = math.inf
    for tau, (wx, wy) in zip(LOOK_AHEAD, path):
        for m in mobs:
            ahead = tau + (now - m.t)
            mx, my = m.x + m.vx * ahead, m.y + m.vy * ahead
            least = min(least, math.dist((wx, wy), (mx, my)) - m.trigger - MARGIN - MARGIN_PER_SECOND * tau)
    return least


def mob_discs(mobs: list[Mob], now: float) -> list[tuple[float, float, float]]:
    """(x, y, radius) to close around each mob: where it will be now, halfway through HORIZON
    and at its end, each as wide as room() asks for at that moment, plus DETOUR_SLACK."""
    discs = []
    for m in mobs:
        for tau in (0.0, HORIZON / 2, HORIZON):
            ahead = now - m.t + tau
            radius = m.trigger + MARGIN + MARGIN_PER_SECOND * tau + DETOUR_SLACK
            discs.append((m.x + m.vx * ahead, m.y + m.vy * ahead, radius))
    return discs


def choose(go: float, wait: float, back: float, waiting: bool) -> str:
    """go on, wait, or back off, from the room each leaves (see room)."""
    if go >= (RESUME_SLACK if waiting else 0.0):
        return "go"
    return "wait" if wait >= back or wait >= 0 else "back"


# --- the walk ------------------------------------------------------------------------------


def clearance_at(planner: Planner, x: float, y: float) -> float:
    j, i = planner.grid.index(x, y)
    if 0 <= j < planner.grid.ny and 0 <= i < planner.grid.nx:
        return float(planner.clearance[j, i])
    return math.nan


def deviation(points: np.ndarray, route: list[tuple[float, float, float]]) -> np.ndarray:
    best = np.full(len(points), np.inf)
    for (ax, ay, _), (bx, by, _) in zip(route, route[1:]):
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy or 1.0
        t = np.clip(((points[:, 0] - ax) * dx + (points[:, 1] - ay) * dy) / length2, 0, 1)
        best = np.minimum(best, np.hypot(points[:, 0] - (ax + t * dx), points[:, 1] - (ay + t * dy)))
    return best


@dataclass
class Walk:
    """What a walk did, for the report."""

    stopped: str = ""
    seconds: float = 0.0
    events: list[str] | None = None
    trail: list[tuple[float, float]] | None = None
    routes: list[list] | None = None  # the plan, then each replan's cell path
    miss: float = math.nan
    speeds: list[float] | None = None
    back_speeds: list[float] | None = None
    yaw_writes: int = 0
    yaw_undone: int = 0
    turns_in_place: int = 0
    recoveries: int = 0
    detours: int = 0
    links_taken: int = 0
    links_dropped: int = 0
    no_detour: int = 0  # detours sought and not found
    tight_detour: int = 0  # detours found, but with too little room themselves
    waits: int = 0
    waited: float = 0.0
    backed: float = 0.0
    least_room: float = math.inf


async def walk(client, planner: Planner, path, goal, args, keys: Keys, codes: dict[str, int], writer, mob_writer):
    forward, left, right, back = (codes[a] for a in MOVE_ACTIONS)
    # Someone else moving the wizard would read as the walk's own doing, so start only from still.
    x0, y0, _, yaw0 = await read(client)
    await asyncio.sleep(SETTLE)
    x1, y1, _, yaw1 = await read(client)
    if math.dist((x0, y0), (x1, y1)) > 5 or abs(wrap(yaw1 - yaw0)) > math.radians(2):
        raise RuntimeError("the wizard is moving; let it stand still and run again")
    if args.turn_keys:
        sign, rate = await calibrate(client, keys, left, right)
        print(f"turn keys: left turns the yaw {'up' if sign > 0 else 'down'}, at {math.degrees(rate):.0f} deg/s")
    else:
        sign, rate = 1.0, math.inf

    multiplier = await client.client_object.speed_multiplier()
    expected = WIZARD_SPEED * (1 + multiplier / 100)
    print(f"speed multiplier {multiplier}%: expecting {expected:.0f} units/s")

    zone = await client.zone_name()
    started = time.monotonic()
    triggers = {p.template: p.proximity for p in planner.patrols}
    watch = MobWatch(client, triggers, mob_writer, started)
    watch.start()

    w = Walk(events=[], trail=[], routes=[path], speeds=[], back_speeds=[])
    x, y, z, yaw = await read(client)
    legs = planner.legs(path)
    route, link = legs[0].waypoints, legs[0].link
    route[0] = (x, y, route[0][2])
    planned = sum(math.dist(a[:2], b[:2]) for leg in legs for a, b in zip(leg.waypoints, leg.waypoints[1:]))
    budget = planned / expected * 3 + 20 + LINK_WAIT * (len(legs) - 1)
    history: list[tuple[float, float, float]] = []
    forward_since = math.inf
    target = 1
    last_poll = 0.0
    last, last_t = (x, y), 0.0
    written: float | None = None
    mode = "go"
    mode_since = 0.0
    last_detour = -math.inf

    def log(text: str) -> None:
        w.events.append(text)
        print("  " + text)

    def follow(new_path, x: float, y: float) -> None:
        """Walk *new_path* from (x, y): its first leg now, the rest replanned when that leg's link is taken."""
        nonlocal route, link, target
        w.routes.append(new_path)
        first = planner.legs(new_path)[0]
        route, link, target = first.waypoints, first.link, 1
        route[0] = (x, y, route[0][2])

    try:
        while True:
            now = time.monotonic() - started
            x, y, z, yaw = await read(client)
            step, gap = math.dist(last, (x, y)), max(now - last_t, TICK)
            if step > JUMP_SPEED * gap and link is not None and math.dist((x, y), link.target[:2]) <= LINK_LANDED:
                w.links_taken += 1
                log(
                    f"t={now:6.2f}s {link.volume} moved the wizard on the way in, {step:.0f} units to "
                    f"({x:.0f}, {y:.0f}), {math.dist((x, y), link.target[:2]):.0f} from {link.landing}"
                )
                await keys.release(forward)
                forward_since = math.inf
                new_path = planner.route((x, y, z), goal, avoid=not args.shortest)
                if new_path is None:
                    w.stopped = "no route on from where a link landed"
                    break
                follow(new_path, x, y)
                history.clear()
                last, last_t = (x, y), now
                continue
            if step > JUMP_SPEED * gap:
                log(
                    f"t={now:6.2f}s jump of {step:.0f} units in {gap:.2f}s from ({last[0]:.0f}, {last[1]:.0f}) to ({x:.0f}, {y:.0f})"
                )
            elif forward_since <= last_t:
                w.speeds.append(step / gap)
            elif back in keys.held:
                w.back_speeds.append(step / gap)
            last, last_t = (x, y), now
            if written is not None and abs(wrap(yaw - written)) > YAW_UNDONE:
                w.yaw_undone += 1
                if w.yaw_undone <= 5:
                    log(f"t={now:6.2f}s yaw written as {written:.3f} read back as {yaw:.3f} a tick later")
            written = None
            w.trail.append((x, y))
            history.append((now, x, y))

            if now - last_poll >= BATTLE_POLL:
                last_poll = now
                if await client.in_battle():
                    w.stopped = "a battle started"
                    break
                if await client.zone_name() != zone:
                    w.stopped = "the zone changed"
                    break
            if now > budget + w.waited + w.backed:
                w.stopped = "out of time"
                break
            if w.waited + w.backed + (now - mode_since if mode != "go" else 0.0) > MAX_WAIT:
                w.stopped = f"held up by mobs for over {MAX_WAIT:.0f}s"
                break

            # Pass a waypoint when near it, or when past it along its leg.
            while target < len(route):
                tx, ty, _ = route[target]
                last_leg = target == len(route) - 1
                if math.dist((x, y), (tx, ty)) < (ARRIVE_GOAL if last_leg else ARRIVE_TURN):
                    target += 1
                    continue
                px, py, _ = route[target - 1]
                dx, dy = tx - px, ty - py
                if not last_leg and ((x - px) * dx + (y - py) * dy) / ((dx * dx + dy * dy) or 1.0) >= 1.0:
                    target += 1
                    continue
                break
            if target >= len(route) and link is not None:
                await keys.release(forward)
                forward_since = math.inf
                stepped_at, waiting_since = (x, y), time.monotonic()
                moved = False
                while time.monotonic() - waiting_since < LINK_WAIT:
                    await asyncio.sleep(TICK)
                    x, y, z, yaw = await read(client)
                    if math.dist(stepped_at, (x, y)) > LINK_MOVED:
                        moved = True
                        break
                now = time.monotonic() - started
                if moved:
                    w.links_taken += 1
                    log(
                        f"t={now:6.2f}s {link.volume} moved the wizard {math.dist(stepped_at, (x, y)):.0f} units to "
                        f"({x:.0f}, {y:.0f}), {math.dist((x, y), link.target[:2]):.0f} from {link.landing}"
                    )
                else:
                    w.links_dropped += 1
                    log(f"t={now:6.2f}s stood in {link.volume} for {LINK_WAIT:.0f}s and was not moved: dropping it")
                    planner.drop(link)
                new_path = planner.route((x, y, z), goal, avoid=not args.shortest)
                if new_path is None:
                    w.stopped = "no route on from the link"
                    break
                follow(new_path, x, y)
                history.clear()
                last, last_t = (x, y), now
                continue
            if target >= len(route):
                await keys.release(forward)
                await asyncio.sleep(SETTLE)
                x, y, *_ = await read(client)
                w.stopped = "reached the goal"
                w.miss = math.dist((x, y), route[-1][:2])
                break

            tx, ty, _ = route[target]
            want = heading_to(x, y, tx, ty)
            error = wrap(want - yaw)

            # Mobs: go on, wait, or back off, by the room each leaves.
            mobs = watch.near(x, y, z)
            speed = statistics.median(w.speeds[-20:]) if len(w.speeds) >= 10 else expected
            here = [(x, y)] * len(LOOK_AHEAD)
            going = [along(route, target, x, y, speed * tau) for tau in LOOK_AHEAD]
            go_room = room(going, mobs, now) if mobs else math.inf
            enough = RESUME_SLACK if mode != "go" else 0.0
            if go_room < enough and now - last_detour >= DETOUR_EVERY:
                last_detour = now
                await keys.release(forward)
                forward_since = math.inf
                detour = planner.around(mob_discs(mobs, now))
                x, y, z, yaw = await read(client)
                new_path = await asyncio.to_thread(detour.route, (x, y, z), goal, True)
                if new_path:
                    new_leg = (await asyncio.to_thread(detour.legs, new_path))[0]
                    new_route = new_leg.waypoints
                    new_route[0] = (x, y, new_route[0][2])
                    now = time.monotonic() - started
                    new_room = room([along(new_route, 1, x, y, speed * tau) for tau in LOOK_AHEAD], mobs, now)
                    if new_room < enough:
                        w.tight_detour += 1
                    else:
                        w.detours += 1
                        w.routes.append(new_path)
                        log(
                            f"t={now:6.2f}s detour around {len(mobs)} mobs at ({x:.0f}, {y:.0f}): room ahead "
                            f"{go_room:.0f} -> {new_room:.0f}, {len(new_route) - 1} legs"
                        )
                        route, link, target, go_room = new_route, new_leg.link, 1, new_room
                        tx, ty, _ = route[target]
                        want = heading_to(x, y, tx, ty)
                        error = wrap(want - yaw)
                        here = [(x, y)] * len(LOOK_AHEAD)
                else:
                    w.no_detour += 1
            choice = "go"
            if go_room < enough:
                fx, fy = facing(want)
                back_speed = speed * BACK_SPEED_FACTOR
                backing = [(x - fx * back_speed * tau, y - fy * back_speed * tau) for tau in LOOK_AHEAD]
                choice = choose(go_room, room(here, mobs, now), room(backing, mobs, now), mode != "go")
            w.least_room = min(w.least_room, go_room if choice == "go" else room(here, mobs, now))
            if choice != mode:
                if mode == "wait":
                    w.waited += now - mode_since
                elif mode == "back":
                    w.backed += now - mode_since
                if choice == "wait":
                    w.waits += 1
                if choice != "go" or mode != "go":
                    nearest = min(mobs, key=lambda m: math.dist((m.x, m.y), (x, y)), default=None)
                    about = (
                        f"; nearest {nearest.name} {math.dist((nearest.x, nearest.y), (x, y)):.0f} away"
                        if nearest
                        else ""
                    )
                    log(f"t={now:6.2f}s {mode} -> {choice} at ({x:.0f}, {y:.0f}), room ahead {go_room:.0f}{about}")
                mode, mode_since = choice, now

            if mode != "go":
                await keys.release(forward)
                forward_since = math.inf
                if mode == "back":
                    await keys.hold(back)
                else:
                    await keys.release(back)
                if not args.turn_keys and abs(error) > DEADBAND:
                    written = want
                    await client.body.write_yaw(want)
                    w.yaw_writes += 1
                await asyncio.sleep(TICK)
            elif not args.turn_keys:
                await keys.release(back)
                if abs(error) > DEADBAND:
                    written = want
                    await client.body.write_yaw(want)
                    w.yaw_writes += 1
                if await keys.hold(forward):
                    forward_since = now
                await asyncio.sleep(TICK)
            else:
                await keys.release(back)
                turn = left if error * sign > 0 else right
                if abs(error) > TURN_IN_PLACE:
                    if forward in keys.held:
                        w.turns_in_place += 1
                    await keys.release(forward)
                    forward_since = math.inf
                    await keys.pulse(turn, min(abs(error) / rate, TICK * 2))
                else:
                    if await keys.hold(forward):
                        forward_since = now
                    if abs(error) > DEADBAND:
                        await keys.pulse(turn, min(abs(error) / rate, TICK))
                    else:
                        await asyncio.sleep(TICK)

            # Stalled: forward held for the whole window, and the wizard barely moved across it.
            while len(history) > 1 and history[1][0] <= now - STALL_WINDOW:
                history.pop(0)
            window_start, sx, sy = history[0]
            if forward_since <= window_start <= now - STALL_WINDOW:
                moved = math.dist((sx, sy), (x, y))
                if moved < STALL_MIN_MOVE:
                    w.recoveries += 1
                    log(f"t={now:6.2f}s stalled at ({x:.0f}, {y:.0f}): {moved:.0f} units in {STALL_WINDOW}s")
                    if w.recoveries > MAX_RECOVERIES:
                        w.stopped = "stalled"
                        break
                    fx, fy = facing(yaw)
                    await keys.release(forward)
                    forward_since = math.inf
                    await keys.hold(back)
                    backing_from, backing_since = (x, y), time.monotonic()
                    while time.monotonic() - backing_since < BACK_OFF_LONGEST:
                        await asyncio.sleep(TICK)
                        bx, by, *_ = await read(client)
                        if math.dist(backing_from, (bx, by)) >= BACK_OFF:
                            break
                    await keys.release(back)
                    w.back_speeds.append(math.dist(backing_from, (bx, by)) / (time.monotonic() - backing_since))
                    closed = planner.block(x + fx * BLOCK_AHEAD, y + fy * BLOCK_AHEAD, z, BLOCK_RADIUS)
                    x, y, z, yaw = await read(client)
                    path = planner.route((x, y, z), goal, avoid=not args.shortest)
                    if path is None:
                        w.stopped = "stalled, with no way around"
                        break
                    follow(path, x, y)
                    history.clear()
                    history.append((time.monotonic() - started, x, y))
                    last, last_t = (x, y), time.monotonic() - started
                    log(
                        f"         backed off to ({x:.0f}, {y:.0f}), closed {closed} spots ahead, replanned: {len(route) - 1} legs"
                    )
                    continue
            writer.writerow(
                [
                    f"{now:.3f}",
                    f"{x:.1f}",
                    f"{y:.1f}",
                    f"{z:.1f}",
                    f"{yaw:.4f}",
                    target,
                    "+".join(a for a, c in codes.items() if c in keys.held),
                    mode,
                    f"{go_room:.0f}",
                ]
            )
    finally:
        watch.stop()
    w.seconds = time.monotonic() - started
    if mode == "wait":
        w.waited += w.seconds - mode_since
    elif mode == "back":
        w.backed += w.seconds - mode_since
    if watch.errors:
        log(f"the mob watcher hit {watch.errors} read errors")
    return w


async def main(args: argparse.Namespace) -> int:
    from wizwalker.extensions.wizsprinter import WizSprinter

    clients = WizSprinter().get_new_clients()
    if not clients:
        print("No Wizard101 clients found. Start the game and log a wizard in.")
        return 1
    if not 1 <= args.client <= len(clients):
        print(f"No client {args.client}; have {len(clients)}.")
        return 2
    client = clients[args.client - 1]
    keys = Keys(client.window_handle)
    codes: dict[str, int] = {}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = REPO / "logs" / "walk_route"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        await client.activate_hooks()
        codes, source = await movement_keys(client)
        for action, flag in (
            ("MoveForward", args.forward),
            ("MoveLeft", args.left),
            ("MoveRight", args.right),
            ("MoveBackward", args.back),
        ):
            if flag:
                if (code := key_code(flag)) is None:
                    print(f"{flag!r}, given for {action}, is not a key this can press")
                    return 2
                codes[action] = code
        print(
            "keys from "
            + source
            + ": "
            + ", ".join(f"{a} {chr(c) if 0x30 <= c <= 0x5A else hex(c)}" for a, c in codes.items())
        )
        zone = await client.zone_name()
        print(f"client {args.client} is in {zone}; building its grid...")
        planner = await asyncio.to_thread(build_planner, zone, ignore_cylinders=args.test_stall)
        goal_name, goal = resolve_point(args.to, planner.named)
        x, y, z, _ = await read(client)
        path = planner.route((x, y, z), goal, avoid=not args.shortest)
        if path is None:
            print(f"no route from ({x:.0f}, {y:.0f}) to {goal_name}")
            return 1
        legs = planner.legs(path)
        legs[0].waypoints[0] = (x, y, legs[0].waypoints[0][2])
        planned = sum(math.dist(a[:2], b[:2]) for leg in legs for a, b in zip(leg.waypoints, leg.waypoints[1:]))
        if len(legs) == 1 and len(legs[0].waypoints) < 2:
            print(f"already at {goal_name}")
            return 0
        links = [leg.link for leg in legs if leg.link]
        print(f"route to {goal_name}: {planned:.0f} units walked, {len(links)} links")
        k = 0
        for leg in legs:
            for wx, wy, wz in leg.waypoints:
                print(f"  {k:2}  ({wx:9.1f}, {wy:9.1f}, {wz:7.1f})  clearance {clearance_at(planner, wx, wy):6.0f}")
                k += 1
            if leg.link:
                print(f"      step into {leg.link.volume!r}, landing at {leg.link.landing!r}")
        if args.dry_run:
            png = out_dir / f"plan-{stamp}.png"
            render(png, planner, 250.0, [(goal_name, None, path, planner.turns(path))], np.empty((0, 2)))
            print(f"dry run; wrote {png}")
            return 0

        csv_path = out_dir / f"walk-{stamp}.csv"
        mobs_path = out_dir / f"mobs-{stamp}.csv"
        keys.start()
        with (
            csv_path.open("w", newline="", encoding="utf-8") as f,
            mobs_path.open("w", newline="", encoding="utf-8") as g,
        ):
            writer, mob_writer = csv.writer(f), csv.writer(g)
            writer.writerow(["t", "x", "y", "z", "yaw", "waypoint", "keys", "mode", "room_ahead"])
            mob_writer.writerow(["t", "gid", "template", "x", "y", "z"])
            try:
                result = await walk(client, planner, path, goal, args, keys, codes, writer, mob_writer)
            except asyncio.CancelledError:
                result = Walk(stopped="Ctrl+C", events=[], trail=[], routes=[path], speeds=[], back_speeds=[])
            finally:
                await keys.release_all(codes.values())

        print(f"\nstopped: {result.stopped} after {result.seconds:.1f}s")
        if result.stopped == "reached the goal":
            print(f"  {result.miss:.0f} units from the goal")
        if result.speeds:
            print(f"  walked at {statistics.median(result.speeds):.0f} units/s", end="")
            print(f"; backed at {statistics.median(result.back_speeds):.0f}" if result.back_speeds else "")
        print(
            f"  waited for mobs {result.waits} times, {result.waited:.1f}s in all; backed off {result.backed:.1f}s; "
            f"least room beyond a trigger radius and margin {result.least_room:.0f}"
        )
        print(
            f"  detoured around mobs {result.detours} times ({result.no_detour} sought and none found, "
            f"{result.tight_detour} found too tight); recovered from {result.recoveries} stalls; "
            f"replanned {len(result.routes) - 1} times in all"
        )
        if result.links_taken or result.links_dropped:
            print(f"  took {result.links_taken} links; dropped {result.links_dropped} that moved nobody")
        if args.turn_keys:
            print(f"  stopped to turn {result.turns_in_place} times")
        else:
            print(f"  wrote the yaw {result.yaw_writes} times; {result.yaw_undone} read back changed a tick later")
        trail = np.array(result.trail) if result.trail else np.empty((0, 2))
        if len(trail):
            off = np.min([deviation(trail, planner.waypoints(p)) for p in result.routes], axis=0)
            print(
                f"  off the nearest route walked: p50 {np.percentile(off, 50):.0f}  p90 {np.percentile(off, 90):.0f}  max {off.max():.0f}"
            )
        png = out_dir / f"walk-{stamp}.png"
        drawn = [(goal_name, None, p, planner.turns(p)) for p in result.routes]
        render(png, planner, 250.0, drawn, trail, "the walked trail")
        print(f"wrote {csv_path}\n      {mobs_path}\n      {png}")
    finally:
        await keys.release_all(codes.values())
        for c in clients:
            try:
                await c.close()
            except Exception:
                pass
    return 0


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to", required=True, help="arrival point name (substring), or x,y or x,y,z, in the wizard's zone")
    ap.add_argument("--client", type=int, default=1, help="which client, 1-based (default 1)")
    ap.add_argument("--dry-run", action="store_true", help="plan and draw the route; press nothing")
    ap.add_argument("--shortest", action="store_true", help="walk the shortest route, not the one avoiding patrols")
    ap.add_argument("--turn-keys", action="store_true", help="steer with the turn keys instead of writing the yaw")
    ap.add_argument(
        "--test-stall", action="store_true", help="plan without the zone's cylinders, so the walk stalls on trees"
    )
    ap.add_argument("--forward", help="the move forward key, overriding the game's")
    ap.add_argument("--left", help="the turn left key, overriding the game's")
    ap.add_argument("--right", help="the turn right key, overriding the game's")
    ap.add_argument("--back", help="the move backward key, overriding the game's")
    return ap.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(_parse_args())))
