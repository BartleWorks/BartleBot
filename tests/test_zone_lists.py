"""Unit tests for zone_lists: which destinations a list object offers, and what its rows read."""

from bartlebot import zone_lists, zone_nav

_HUB, _A, _B, _C = "Alpha/Hub", "Alpha/A", "Alpha/B", "Alpha/C"

_EMPYREA_HUB = "Empyrea/EM_Z00_Aeriel_HUB"
_NIMBUS = "Empyrea/EM_Z06_NimbusHanger"
_MANDALLA = "Empyrea/EM_Z12_LandingZone"
"""Empyrea's two quirks: a row truncated to "Nimbus", and one labelled with another zone's name."""


def _xyz(x: float) -> dict:
    return {"x": x, "y": 0.0, "z": 0.0}


def _nav(zones: dict, *, verified: bool = True) -> zone_nav.NavMap:
    """One world, one list network, whose members are `zones` -- {zone: spot}."""
    return zone_nav.parse(
        {
            "worlds": {
                "Alpha": {
                    "hub": _HUB,
                    "zones": {zone: {} for zone in (_HUB, _A, _B, _C)},
                    "list_networks": [{"zones": zones, "verified": verified, "source": "code"}],
                }
            }
        }
    )


def _spot(x: float = 1.0, **extra) -> dict:
    return {"interact_xyz": _xyz(x), "reset_xyz": _xyz(x + 1), **extra}


# ── destinations ───────────────────────────────────────────────────────────────


def test_destinations_are_every_other_member_of_the_list():
    nav = _nav({_HUB: _spot(), _A: _spot(2), _B: _spot(3)})
    assert zone_lists.destinations(nav, _HUB) == sorted([_A, _B])


def test_a_member_with_no_object_of_its_own_offers_nothing():
    """It is on the list as a destination, so the list routes into it and not out of it."""
    nav = _nav({_HUB: _spot(), _A: {}})
    assert zone_lists.destinations(nav, _A) == []
    assert zone_lists.destinations(nav, _HUB) == [_A]


def test_a_zone_on_no_list_offers_nothing():
    nav = _nav({_HUB: _spot(), _A: _spot(2)})
    assert zone_lists.destinations(nav, _C) == []


def test_an_unverified_network_still_offers_its_destinations():
    """The author picking it is saying the object is there; keeping unverified links out of a
    route is the planner's job, not the picker's."""
    nav = _nav({_HUB: _spot(), _A: _spot(2)}, verified=False)
    assert zone_lists.destinations(nav, _HUB) == [_A]


# ── step_for ───────────────────────────────────────────────────────────────────


def test_step_for_finds_the_list_step_joining_two_zones():
    nav = _nav({_HUB: _spot(), _A: _spot(2)})
    step = zone_lists.step_for(nav, _HUB, _A)
    assert step is not None
    assert (step.kind, step.from_zone, step.to_zone) == (zone_nav.LIST, _HUB, _A)


def test_step_for_is_none_when_no_list_joins_them():
    nav = _nav({_HUB: _spot(), _A: _spot(2)})
    assert zone_lists.step_for(nav, _HUB, _C) is None


# ── resolve ────────────────────────────────────────────────────────────────────


def test_resolve_carries_the_objects_own_coordinates():
    """The coordinates come from where the list object stands, not from the destination."""
    nav = zone_nav.load()
    resolved = zone_lists.resolve_zones(nav, _EMPYREA_HUB, _NIMBUS)
    assert not isinstance(resolved, str)
    spot = zone_lists.step_for(nav, _EMPYREA_HUB, _NIMBUS).params
    assert (resolved.interact_xyz, resolved.reset_xyz) == (spot["interact_xyz"], spot["reset_xyz"])


def test_resolve_matches_the_destination_in_every_locale():
    """A bot authored on an English client has to run on the other six."""
    resolved = zone_lists.resolve_zones(zone_nav.load(), _EMPYREA_HUB, "Empyrea/EM_Z02_Zanadu")
    assert not isinstance(resolved, str)
    assert resolved.entry == "Zanadu"
    assert len(resolved.aliases) > 1
    assert resolved.entry not in resolved.aliases


def test_the_maps_own_aliases_come_after_the_locale_names():
    """Nimbus Hanger's row reads "Nimbus", which is no locale's name for it."""
    resolved = zone_lists.resolve_zones(zone_nav.load(), _EMPYREA_HUB, _NIMBUS)
    assert not isinstance(resolved, str)
    assert resolved.aliases[-1] == "Nimbus"


def test_list_aliases_only_drops_the_zones_own_names():
    """The Landing Zone's row reads "Mandalla"; its own name is the *other* Chaos Jungle's row,
    so trying it would click the wrong one."""
    resolved = zone_lists.resolve_zones(zone_nav.load(), _EMPYREA_HUB, _MANDALLA)
    assert not isinstance(resolved, str)
    assert (resolved.entry, resolved.aliases) == ("Mandalla", [])


def test_resolve_reports_a_destination_with_no_known_display_name():
    nav = _nav({_HUB: _spot(), _A: _spot(2)})
    problem = zone_lists.resolve_zones(nav, _HUB, _A)
    assert isinstance(problem, str)
    assert "no display name is known" in problem and _A in problem


def test_resolve_zones_reports_a_pair_no_list_joins():
    nav = _nav({_HUB: _spot(), _A: _spot(2)})
    problem = zone_lists.resolve_zones(nav, _HUB, _C)
    assert isinstance(problem, str)
    assert "no list object" in problem


# ── the shipped map ────────────────────────────────────────────────────────────


def test_every_member_of_every_shipped_network_resolves():
    """A member whose row cannot be named is one no bot and no route can reach through its list."""
    nav = zone_nav.load()
    problems = [
        zone_lists.resolve(step)
        for step in nav.links
        if step.kind == zone_nav.LIST and isinstance(zone_lists.resolve(step), str)
    ]
    assert problems == []
