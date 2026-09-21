"""Unit tests for zone_lists: what a list object's row for a given destination reads."""

from bartlebot import zone_lists, zone_nav

_HUB, _A, _C = "Alpha/Hub", "Alpha/A", "Alpha/C"

_EMPYREA_HUB = "Empyrea/EM_Z00_Aeriel_HUB"
_NIMBUS = "Empyrea/EM_Z06_NimbusHanger"
_MANDALLA = "Empyrea/EM_Z12_LandingZone"
"""Empyrea's two quirks: a row truncated to "Nimbus", and one labelled with another zone's name."""


def _xyz(x: float) -> dict:
    return {"x": x, "y": 0.0, "z": 0.0}


def _spot(x: float = 1.0) -> dict:
    return {"interact_xyz": _xyz(x), "reset_xyz": _xyz(x + 1)}


def _nav(zones: dict) -> zone_nav.NavMap:
    """One world, one list network, whose members are `zones` -- {zone: spot}."""
    return zone_nav.parse(
        {
            "worlds": {
                "Alpha": {
                    "hub": _HUB,
                    "zones": {zone: {} for zone in (_HUB, _A, _C)},
                    "list_networks": [{"zones": zones, "verified": True, "source": "code"}],
                }
            }
        }
    )


def _step(nav: zone_nav.NavMap, from_zone: str, to_zone: str) -> zone_nav.Step:
    """The list step joining two zones -- what the navigator hands zone_lists."""
    [step] = [s for s in nav.steps_from(from_zone) if s.kind == zone_nav.LIST and s.to_zone == to_zone]
    return step


def test_resolve_carries_the_objects_own_coordinates():
    """The coordinates come from where the list object stands, not from the destination."""
    step = _step(zone_nav.load(), _EMPYREA_HUB, _NIMBUS)
    resolved = zone_lists.resolve(step)

    assert not isinstance(resolved, str)
    assert (resolved.interact_xyz, resolved.reset_xyz) == (step.params["interact_xyz"], step.params["reset_xyz"])


def test_resolve_matches_the_destination_in_every_locale():
    """The row reads differently per locale and the map stores none of them, so all are tried."""
    resolved = zone_lists.resolve(_step(zone_nav.load(), _EMPYREA_HUB, "Empyrea/EM_Z02_Zanadu"))

    assert not isinstance(resolved, str)
    assert resolved.entry == "Zanadu"
    assert len(resolved.aliases) > 1
    assert resolved.entry not in resolved.aliases


def test_the_maps_own_aliases_come_after_the_locale_names():
    """Nimbus Hanger's row reads "Nimbus", which is no locale's name for it."""
    resolved = zone_lists.resolve(_step(zone_nav.load(), _EMPYREA_HUB, _NIMBUS))

    assert not isinstance(resolved, str)
    assert resolved.aliases[-1] == "Nimbus"


def test_list_aliases_only_drops_the_zones_own_names():
    """The Landing Zone's row reads "Mandalla"; its own name is the *other* Chaos Jungle's row,
    so trying it would click the wrong one."""
    resolved = zone_lists.resolve(_step(zone_nav.load(), _EMPYREA_HUB, _MANDALLA))

    assert not isinstance(resolved, str)
    assert (resolved.entry, resolved.aliases) == ("Mandalla", [])


def test_resolve_reports_a_destination_with_no_known_display_name():
    nav = _nav({_HUB: _spot(), _A: _spot(2)})

    problem = zone_lists.resolve(_step(nav, _HUB, _A))

    assert isinstance(problem, str)
    assert "no display name is known" in problem and _A in problem


def test_every_member_of_every_shipped_network_resolves():
    """A member whose row cannot be named is one no route can reach through its list."""
    nav = zone_nav.load()
    problems = [
        resolved
        for step in nav.links
        if step.kind == zone_nav.LIST and isinstance(resolved := zone_lists.resolve(step), str)
    ]

    assert problems == []
