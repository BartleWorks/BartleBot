"""Zone-list objects: which destinations one offers, and what its row for a destination reads.

A zone-list object -- Empyrea's Streamportal, Karamelle's Nanavator, Lemuria's Telepad,
Wallaru's Traveler's Truck, Darkmoor's Tamed Deimox, Arcanum's lift -- stands in several
zones and sends players between them. ``zone_nav`` stores each one as a network and
generates a ``LIST`` step for every ordered pair; this module is the half that turns one
of those steps into what ``enter_zone_via_list`` needs to actually find the row: where to
stand, and every text the row might read in the seven locales the game ships.

Both callers go through here so there is one answer. The navigator resolves the step it
planned; the node resolves the destination its author picked. Neither stores the row's
text, which is the point -- a bot authored on an English client names a *zone*, and the
text is worked out again on whatever client runs it. See docs/ZONE_NAVIGATION.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bartlebot import zone_names, zone_nav


@dataclass(frozen=True)
class ListEntry:
    """What it takes to use one list object for one destination."""

    interact_xyz: dict
    reset_xyz: dict
    entry: str
    """The row's text on an en-US client: what the node matches first."""
    aliases: list[str] = field(default_factory=list)
    """Every other text the same row might read: the other six locales, then the map's own."""


def destinations(nav: zone_nav.NavMap, from_zone: str) -> list[str]:
    """Every zone the list object standing in `from_zone` can send players to, name-sorted.

    Empty when `from_zone` has no list object -- it is on no network, or it is on one as a
    destination only, its own object not recorded yet.

    Unverified networks are included: the author picking a destination is saying the object
    is there, and refusing the pick would leave no way to author against a list until
    someone else verified it. The planner is the one that keeps unverified links out of a
    route nobody asked for.
    """
    reachable = {
        step.to_zone for step in nav.steps_from(from_zone, include_unverified=True) if step.kind == zone_nav.LIST
    }
    return sorted(reachable, key=lambda zone: zone_names.label(zone).casefold())


def step_for(nav: zone_nav.NavMap, from_zone: str, to_zone: str) -> zone_nav.Step | None:
    """The list step from `from_zone` to `to_zone`, or None if no one list object joins them."""
    for step in nav.steps_from(from_zone, include_unverified=True):
        if step.kind == zone_nav.LIST and step.to_zone == to_zone:
            return step
    return None


def resolve(step: zone_nav.Step) -> ListEntry | str:
    """What `step`'s row reads and where to stand to open its list, or why that is not known.

    A string is the problem, phrased for a log: the only way to get one is a destination
    whose display name is missing from the shipped snapshot.
    """
    params = step.params
    if params.get("list_aliases_only"):
        # The list's text for this member is not a locale variant of the zone's own display
        # name -- it is a name that belongs to a *different* zone on the same list, so trying
        # the zone's own names would click that other zone's row. The map's aliases are the
        # only safe candidates. See Empyrea's "Mandalla" in docs/ZONE_NAVIGATION.md.
        primary, *aliases = params["list_aliases"]
        return ListEntry(params["interact_xyz"], params["reset_xyz"], primary, list(aliases))

    # The list shows the game's own name for the zone, in the client's language, so every
    # locale is a candidate and whichever one the client is running wins.
    names = zone_names.display_names(step.to_zone)
    if not names:
        return (
            f"no display name is known for {step.to_zone!r}, so its list entry cannot be found; "
            f"regenerate bartlebot/data/zone_names.json with tools/dump_zone_names.py"
        )
    primary, *aliases = names
    # The map's own list_aliases cover text the list shows that is not a locale name at all --
    # Nimbus Hanger's hub list truncates it to "Nimbus".
    return ListEntry(
        params["interact_xyz"],
        params["reset_xyz"],
        primary,
        [*aliases, *params.get("list_aliases", [])],
    )


def resolve_zones(nav: zone_nav.NavMap, from_zone: str, to_zone: str) -> ListEntry | str:
    """`resolve` for a pair of zones rather than a step it already has."""
    step = step_for(nav, from_zone, to_zone)
    if step is None:
        return (
            f"no list object in {from_zone!r} reaches {to_zone!r}; the two zones are on no one "
            f"list network in bartlebot/data/zone_nav.json"
        )
    return resolve(step)
