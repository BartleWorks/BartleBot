"""Changes the tester makes to zone_nav.json's raw document.

Pure dict edits on the parsed JSON, so the file keeps everything the tester does not know
about. The caller validates the result with ``zone_nav.parse`` before writing it with
``zone_nav.dumps``; nothing here writes a file.
"""

from __future__ import annotations

from bartlebot import zone_nav

SOURCE = "recorded"


def xyz(point: tuple[float, float, float]) -> dict:
    """A position as the file stores it, rounded to what a client can actually be placed at."""
    return {"x": round(point[0], 2), "y": round(point[1], 2), "z": round(point[2], 2)}


def world_declaring(raw: dict, zone: str) -> str | None:
    return next((key for key, spec in raw["worlds"].items() if zone in spec.get("zones", {})), None)


def declare_zone(raw: dict, world_key: str, zone: str) -> None:
    raw["worlds"][world_key].setdefault("zones", {}).setdefault(zone, {})


def upsert_link(raw: dict, link: dict) -> str:
    """Add `link`, or replace the one with the same kind, from and to. Returns "added" or "replaced".

    A link lives with the world declaring its destination, so a side world's way in is kept with
    the side world.
    """
    key = (link["kind"], link["from"], link["to"])
    for spec in raw["worlds"].values():
        links = spec.get("links", [])
        for i, existing in enumerate(links):
            if (existing.get("kind"), existing.get("from"), existing.get("to")) == key:
                links[i] = link
                return "replaced"
    world = world_declaring(raw, link["to"]) or world_declaring(raw, link["from"])
    raw["worlds"][world].setdefault("links", []).append(link)
    return "added"


def add_list_spot(
    raw: dict, origin: str, destination: str, interact: dict, reset: dict, *, verified: bool = False
) -> None:
    """Record where the list object stands in `origin`, and that `destination` is on its list.

    A member needs its own object position to route *from* it, but it is on the list as soon
    as another member's list offers it. So a list use adds the origin's object, and adds the
    destination as a member without one: routes into the destination work at once, and routes
    out of it once a list use from there is recorded. Networks found to share a zone are the
    same list, so they are merged.
    """
    found = [
        (spec, n)
        for spec in raw["worlds"].values()
        for n in spec.get("list_networks", [])
        if origin in n.get("zones", {}) or destination in n.get("zones", {})
    ]
    if found:
        _, network = found[0]
        had = network.get("verified", False)
        for other_spec, other in found[1:]:
            for zone, spot in other["zones"].items():
                if spot or zone not in network["zones"]:
                    network["zones"][zone] = spot
            had = had and other.get("verified", False)
            other_spec["list_networks"].remove(other)
    else:
        world = world_declaring(raw, origin)
        network = {"zones": {}, "verified": verified, "source": SOURCE}
        raw["worlds"][world].setdefault("list_networks", []).append(network)
        had = verified
    network["zones"][origin] = {"interact_xyz": interact, "reset_xyz": reset}
    network["zones"].setdefault(destination, {})
    # One flag covers the whole network, so an untested member makes all of it tentative.
    network["verified"] = had and verified


def set_arrival(raw: dict, zone: str, came_from: str, point: dict) -> None:
    world = world_declaring(raw, zone)
    raw["worlds"][world]["zones"][zone].setdefault("arrivals", {})[came_from] = point


def mark_verified(raw: dict, steps: list[zone_nav.Step]) -> int:
    """Mark the links behind `steps` verified. Returns how many entries changed."""
    changed = 0
    for step in steps:
        for spec in raw["worlds"].values():
            if step.kind == zone_nav.LIST:
                entries = [
                    n
                    for n in spec.get("list_networks", [])
                    if {step.from_zone, step.to_zone} <= set(n.get("zones", {}))
                ]
            else:
                # A ride finished from inside its cutscene zone is the stored link boarded elsewhere.
                from_zone = step.params.get("board_zone", step.from_zone)
                entries = [
                    link
                    for link in spec.get("links", [])
                    if (link.get("kind"), link.get("from"), link.get("to")) == (step.kind, from_zone, step.to_zone)
                ]
            for entry in entries:
                if not entry.get("verified", False):
                    entry["verified"] = True
                    changed += 1
    return changed


def nearest(
    points: list[tuple[str, tuple[float, float, float]]], at: tuple[float, float, float], within: float
) -> tuple[str, tuple[float, float, float], float] | None:
    """The named point closest to `at`, if any is within `within` units."""
    scored = [(name, pos, sum((a - b) ** 2 for a, b in zip(pos, at)) ** 0.5) for name, pos in points]
    best = min(scored, key=lambda item: item[2], default=None)
    return best if best is not None and best[2] <= within else None
