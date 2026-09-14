"""Take clients to any zone on the navigation map: the right world first, then one planned step at a time.

Not a node yet. The navigation node will be a thin wrapper around ``navigate()``, and
``tools/zone_nav_tester`` runs it directly, so the tester exercises the code the node will.

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
from types import ModuleType

from bartlebot import zone_names, zone_nav
from bartlebot.nodes import node_helpers, wizard_city
from bartlebot.nodes.paths import HUB_BUTTON_PATH
from bartlebot.nodes.teleportation import go_to_world, teleport_to_world_hub
from bartlebot.nodes.zone_entry import enter_zone, enter_zone_via_list, enter_zone_via_object
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


def step_call(step: zone_nav.Step) -> tuple[ModuleType, dict] | str:
    """The zone-entry node and params that run `step`, or why it cannot run yet."""
    p = step.params
    zones = {"from_zone": step.from_zone, "to_zone": step.to_zone}
    if step.kind == zone_nav.WALK:
        return enter_zone, {**zones, "xyz": p["xyz"], **({"reset_xyz": p["reset_xyz"]} if "reset_xyz" in p else {})}
    if step.kind == zone_nav.OBJECT:
        params = {**zones, "interact_xyz": p["interact_xyz"], "reset_xyz": p["reset_xyz"]}
        params["sequential"] = p.get("sequential", False)
        if "object_use_delay" in p:
            params["object_use_delay"] = p["object_use_delay"]
        return enter_zone_via_object, params
    if step.kind == zone_nav.LIST:
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
    if step.kind == zone_nav.HUB:
        return teleport_to_world_hub, {"zone": step.to_zone}
    return f"{step.kind} steps cannot run yet: the ride helper is not built"


def in_reach(nav: zone_nav.NavMap, zone: str | None, world: zone_nav.World) -> bool:
    """True when a client in `zone` can route to `world` without the spiral door.

    That is any zone whose world is entered through the same door world -- Nightside reaches
    Castle Darkmoor. The dorm is the exception: go_to_world does not count it either.
    """
    if not zone or zone == wizard_city.DORM_ZONE or (here := nav.world_of(zone)) is None:
        return False
    return nav.entry_world(here.key) == nav.entry_world(world.key)


async def _hub_cooldown(clients: list) -> float:
    """What is left of the hub button's cooldown. The button says only whether it is enabled."""
    enabled = await asyncio.gather(*[node_helpers.is_enabled_by_path(c, HUB_BUTTON_PATH) for c in clients])
    return 0.0 if all(enabled) else zone_nav.HUB_BUTTON_COOLDOWN


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

    The spiral door is used at most once: when a client starts outside the target's reach, or
    when no route exists from where the clients are. A split group regroups at its world's hub.

    `avoid` is passed to the planner: the kinds of link the route must not use. It does not
    cover getting to the world or regrouping, which have no alternative to the spiral door and
    the hub button.
    """
    nav = zone_nav.load() if nav is None else nav
    world = nav.world_of(target)
    taken: list[zone_nav.Step] = []
    if world is None or target not in world.zones:
        return _failed(machine, f"{target!r} is not on the navigation map", taken)
    entry = nav.entry_world(world.key)
    travelled = False

    for _ in range(MAX_STEPS):
        zones = list(await asyncio.gather(*[c.zone_name() for c in clients]))
        if all(zone == target for zone in zones):
            machine.log(f"SUCCESS: all clients in {target!r}")
            return Outcome(True, taken=taken)
        if hit := next((h for z in zones if (h := check_zone(z))), None):
            return _failed(machine, f"a client is in a restricted zone: {hit.describe()}", taken)

        if not travelled and not all(in_reach(nav, zone, world) for zone in zones):
            travelled = True
            machine.log(f"Travelling to {entry} through the spiral door")
            if await go_to_world.run(machine, clients, {"world": entry}) != "SUCCESS":
                return _failed(machine, f"could not reach {entry}", taken)
            continue

        if len(set(zones)) > 1:
            here = nav.world_of(zones[0])
            if here is None or not all(nav.world_of(zone) is here for zone in zones):
                return _failed(machine, f"clients are split across worlds: {sorted(set(zones))}", taken)
            machine.log(f"Clients are split across {sorted(set(zones))}; regrouping at {here.hub!r}")
            if await teleport_to_world_hub.run(machine, clients, {"zone": here.hub}) != "SUCCESS":
                return _failed(machine, f"could not regroup at {here.hub!r}", taken)
            continue

        route = zone_nav.plan(
            nav,
            zones[0],
            target,
            hub_cooldown=await _hub_cooldown(clients),
            clients=len(clients),
            include_unverified=include_unverified,
            avoid=avoid,
        )
        if route is None:
            if not travelled:
                travelled = True
                machine.log(f"No route from {zones[0]!r}; starting again from {entry}'s spiral door")
                if await go_to_world.run(machine, clients, {"world": entry}) != "SUCCESS":
                    return _failed(machine, f"could not reach {entry}", taken)
                continue
            return _failed(machine, f"no route from {zones[0]!r} to {target!r}", taken)

        step = route[0]
        call = step_call(step)
        if isinstance(call, str):
            return _failed(machine, call, taken)
        node, params = call
        machine.log(f"Step {len(taken) + 1}: {step.kind} {step.from_zone!r} -> {step.to_zone!r}")
        if await node.run(machine, clients, params) != "SUCCESS":
            return _failed(machine, f"{step.kind} step {step.from_zone!r} -> {step.to_zone!r} failed", taken)
        taken.append(step)

    return _failed(machine, f"gave up after {MAX_STEPS} steps without reaching {target!r}", taken)
