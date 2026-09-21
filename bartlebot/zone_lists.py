"""Zone-list objects: what a row for a given destination reads.

A zone-list object -- Empyrea's Streamportal, Karamelle's Nanavator, Lemuria's Telepad,
Wallaru's Traveler's Truck, Darkmoor's Tamed Deimox, Arcanum's lift -- stands in several
zones and sends players between them. ``zone_nav`` stores each one as a network and
generates a ``LIST`` step for every ordered pair; this module turns one of those
steps into what ``enter_zone_via_list`` needs to find the row: where to stand, and every text
the row might read across the seven locales the game ships.

The row's text is never stored anywhere -- not in the map, not in a bot file. It is worked
out from the destination zone each time, on the client that is about to run the step, which
is what lets one map serve all seven locales. See docs/ZONE_NAVIGATION.md.
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
