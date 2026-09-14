"""Take clients to any zone on the navigation map, one planned step at a time.

Not a node yet. The navigation node will be a thin wrapper around ``navigate()``, and
``tools/zone_nav_tester`` runs it directly, so the tester exercises the code the node will.

Getting to another world is part of the plan, not a separate trip first: a World Gate is a step
like any other, and so is pressing HOME, so a route through the gate of the world the clients
are already in is weighed against going home to the World Tree, and the one with fewer
transitions wins.

Each step runs through the zone-entry node its kind names, so everything those nodes already
handle -- prompts, sequential teleporters, stragglers, the dungeon exit warning -- comes along
unchanged.

The route is planned again after every step rather than run whole: a step can land somewhere
unplanned, and the hub button's cooldown depends on what has happened since. A plan costs
nothing next to a loading screen.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Protocol

from bartlebot import worlds, zone_names, zone_nav
from bartlebot.nodes import node_helpers, wizard_city
from bartlebot.nodes.interaction import interact
from bartlebot.nodes.teleportation import teleport_to_world_hub
from bartlebot.nodes.utility import sleep
from bartlebot.nodes.zone_entry import enter_zone, enter_zone_via_list, enter_zone_via_object, enter_zone_via_ride
from bartlebot.restricted_zones import check_zone

MAX_STEPS = 30
"""No route needs this many. Reaching it means steps keep landing somewhere the plan did not expect."""


@dataclass
class Outcome:
    success: bool
    reason: str = ""
    taken: list[zone_nav.Step] = field(default_factory=list)

    @property
    def unverified(self) -> list[zone_nav.Step]:
        """Tentative links this run used -- each one worked, if the run succeeded."""
        return [step for step in self.taken if not step.verified]


class Runs(Protocol):
    """A node module, or anything else with a node's run()."""

    async def run(self, machine, clients: list, params: dict) -> str: ...


class GoHome:
    """HOME as a step, with a node's run(). wizard_city's press-and-verify unequips castles first, so HOME reaches the dorm."""

    @staticmethod
    async def run(machine, clients: list, params: dict) -> str:
        return "SUCCESS" if await wizard_city.teleport_home_and_verify_dorm(machine, clients) else "FAILURE"


def step_call(step: zone_nav.Step) -> list[tuple[Runs, dict]] | str:
    """The nodes and params that run `step`, in order, or why it cannot run.

    Every step is one zone-entry node, except a walk through a gate that has to be opened first.
    """
    p = step.params
    zones = {"from_zone": step.from_zone, "to_zone": step.to_zone}
    if step.kind == zone_nav.WALK:
        walk = (enter_zone, {**zones, "xyz": p["xyz"], **({"reset_xyz": p["reset_xyz"]} if "reset_xyz" in p else {})})
        if not (unlock := p.get("unlock")):
            return [walk]
        # The gate is shared, so one player opens it for the group; then it takes a while to appear.
        opening = [
            (
                interact,
                {
                    "zone": step.from_zone,
                    "interact_xyz": spot["interact_xyz"],
                    "reset_xyz": spot["reset_xyz"],
                    "interact_clients": [1],
                    "has_dialogue": False,
                },
            )
            for spot in unlock["objects"]
        ]
        return [*opening, (sleep, {"duration": unlock["opens_after"]}), walk]
    call = _zone_entry_call(step, zones)
    return call if isinstance(call, str) else [call]


def _zone_entry_call(step: zone_nav.Step, zones: dict) -> tuple[Runs, dict] | str:
    p = step.params
    if step.kind == zone_nav.OBJECT:
        params = {**zones, "interact_xyz": p["interact_xyz"], "reset_xyz": p["reset_xyz"]}
        params["sequential"] = p.get("sequential", False)
        if "object_use_delay" in p:
            params["object_use_delay"] = p["object_use_delay"]
        return enter_zone_via_object, params
    if step.kind == zone_nav.LIST:
        if p.get("list_aliases_only"):
            # The list's real text for this member doesn't just omit the zone's own display
            # name (Nimbus Hanger's "Nimbus") -- that text belongs to a different real zone
            # that also happens to be on this list, so trying it would click that zone's row
            # instead. list_aliases is the only safe candidate set here.
            primary, *aliases = p["list_aliases"]
        else:
            # The list shows the game's own name in the client's language, so match every locale.
            names = zone_names.display_names(step.to_zone)
            if not names:
                return (
                    f"no display name is known for {step.to_zone!r}, so its list entry cannot be found; "
                    f"regenerate bartlebot/data/zone_names.json with tools/dump_zone_names.py"
                )
            primary, *aliases = names
            # The map's own list_aliases cover text the list shows that isn't a locale name at
            # all -- Nimbus Hanger's hub list truncates it to "Nimbus".
            aliases = [*aliases, *p.get("list_aliases", [])]
        return enter_zone_via_list, {
            "zone": step.from_zone,
            "to_zone": step.to_zone,
            "interact_xyz": p["interact_xyz"],
            "reset_xyz": p["reset_xyz"],
            "zone_list_entry": primary,
            "zone_list_aliases": aliases,
        }
    if step.kind == zone_nav.RIDE:
        # A ride finished from inside its cutscene zone still names the zone it is boarded in.
        params = {"from_zone": p.get("board_zone", step.from_zone), "to_zone": step.to_zone, "board": p["board"]}
        params["board_xyz"] = p["interact_xyz"] if p["board"] == zone_nav.OBJECT else p["xyz"]
        params.update({name: p[name] for name in ("reset_xyz", "ride_zone") if name in p})
        return enter_zone_via_ride, params
    if step.kind == zone_nav.HUB:
        return teleport_to_world_hub, {"zone": step.to_zone}
    if step.kind == zone_nav.GATE:
        # The gate lists worlds, in the client's language, as go_to_world searches it.
        names = worlds.display_names(p["world"])
        if not names:
            return (
                f"no display name is known for world {p['world']!r}, so its World Gate row cannot be found; "
                f"regenerate bartlebot/data/world_names.json with tools/dump_world_names.py"
            )
        primary, *aliases = names
        return enter_zone_via_list, {
            "zone": step.from_zone,
            # Anywhere in the world counts as arriving, as for go_to_world; the next plan starts from there.
            "to_zone": worlds.zone_pattern(p["world"]),
            "interact_xyz": p["interact_xyz"],
            "reset_xyz": p["reset_xyz"],
            "zone_list_entry": primary,
            "zone_list_aliases": aliases,
        }
    if step.kind == zone_nav.HOME:
        return GoHome, {}
    return f"{step.kind} steps cannot run: no zone-entry node runs them"


async def _teleport_cooldown(clients: list) -> float:
    """The longest wait left on any client's hub/HOME cooldown. A timer that cannot be read counts as a full one."""
    left = await asyncio.gather(*[node_helpers.teleport_cooldown(c) for c in clients])
    return max((zone_nav.TELEPORT_COOLDOWN if seconds is None else seconds for seconds in left), default=0.0)


def _failed(machine, reason: str, taken: list[zone_nav.Step]) -> Outcome:
    machine.log(f"FAILURE: {reason}", logging.WARNING)
    return Outcome(False, reason, taken)


async def navigate(
    machine,
    clients: list,
    target: str,
    *,
    include_unverified: bool = False,
    avoid: Collection[str] = (),
    nav: zone_nav.NavMap | None = None,
) -> Outcome:
    """Move every client to `target`. See the module docstring for the approach.

    A group split within one world regroups at its hub; a group split across worlds, or with a
    client somewhere no world declares, regroups at home.

    `avoid` is passed to the planner: the kinds of link the route must not use. It does not
    cover regrouping, which has no alternative to the hub button and HOME.
    """
    nav = zone_nav.load() if nav is None else nav
    world = nav.world_of(target)
    taken: list[zone_nav.Step] = []
    if world is None or target not in world.zones:
        return _failed(machine, f"{target!r} is not on the navigation map", taken)

    for _ in range(MAX_STEPS):
        zones = list(await asyncio.gather(*[c.zone_name() for c in clients]))
        if all(zone == target for zone in zones):
            machine.log(f"SUCCESS: all clients in {target!r}")
            return Outcome(True, taken=taken)
        if hit := next((h for z in zones if (h := check_zone(z))), None):
            return _failed(machine, f"a client is in a restricted zone: {hit.describe()}", taken)

        if len(set(zones)) > 1:
            here = nav.world_of(zones[0])
            if here is not None and all(nav.world_of(zone) is here for zone in zones):
                machine.log(f"Clients are split across {sorted(set(zones))}; regrouping at {here.hub!r}")
                if await teleport_to_world_hub.run(machine, clients, {"zone": here.hub}) != "SUCCESS":
                    return _failed(machine, f"could not regroup at {here.hub!r}", taken)
                continue
            if nav.home is None:
                return _failed(machine, f"clients are split across worlds: {sorted(set(zones))}", taken)
            machine.log(f"Clients are split across worlds {sorted(set(zones))}; regrouping at home")
            if await GoHome.run(machine, clients, {}) != "SUCCESS":
                return _failed(machine, "could not regroup at home", taken)
            continue

        route = zone_nav.plan(
            nav,
            zones[0],
            target,
            teleport_cooldown=await _teleport_cooldown(clients),
            clients=len(clients),
            include_unverified=include_unverified,
            avoid=avoid,
        )
        if route is None:
            return _failed(machine, f"no route from {zones[0]!r} to {target!r}", taken)

        step = route[0]
        calls = step_call(step)
        if isinstance(calls, str):
            return _failed(machine, calls, taken)
        machine.log(f"Step {len(taken) + 1}: {step.kind} {step.from_zone!r} -> {step.to_zone!r}")
        for node, params in calls:
            if await node.run(machine, clients, params) != "SUCCESS":
                return _failed(machine, f"{step.kind} step {step.from_zone!r} -> {step.to_zone!r} failed", taken)
        taken.append(step)

    return _failed(machine, f"gave up after {MAX_STEPS} steps without reaching {target!r}", taken)
