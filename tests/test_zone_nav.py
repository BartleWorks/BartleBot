"""Unit tests for the zone navigation map and route planner."""

import json

import pytest

from bartlebot import zone_nav

_HUB = "Alpha/Hub"
_DOOR = "Alpha/Door"
_S1 = "Alpha/Street1"
_S2 = "Alpha/Street2"
_S3 = "Alpha/Street3"
_ISLAND = "Alpha/Island"
_LX = "Alpha/ListX"
_LY = "Alpha/ListY"
_ALPHA_ZONES = [_HUB, _DOOR, _S1, _S2, _S3, _ISLAND, _LX, _LY]


def _xyz(x: float) -> dict:
    return {"x": x, "y": 0.0, "z": 0.0}


def _walk(src: str, dst: str, **extra) -> dict:
    return {"kind": "walk", "from": src, "to": dst, "xyz": _xyz(1), "reset_xyz": _xyz(2), "verified": True, **extra}


def _object(src: str, dst: str, **extra) -> dict:
    link = {"kind": "object", "from": src, "to": dst, "interact_xyz": _xyz(3), "reset_xyz": _xyz(4)}
    return {**link, "verified": True, **extra}


def _ride(src: str, dst: str, ride_zone: str) -> dict:
    link = {"kind": "ride", "from": src, "to": dst, "interact_xyz": _xyz(5), "reset_xyz": _xyz(6)}
    return {**link, "ride_zone": ride_zone, "verified": True}


def _raw(*alpha_links, networks=(), beta_links=()) -> dict:
    """Two worlds: Alpha with a door, and Beta, a side world entered from Alpha."""
    return {
        "worlds": {
            "Alpha": {
                "hub": _HUB,
                "door": _DOOR,
                "zones": {zone: {} for zone in _ALPHA_ZONES},
                "links": list(alpha_links),
                "list_networks": list(networks),
            },
            "Beta": {
                "hub": "Beta/Hub",
                "entered_from": "Alpha",
                "zones": {"Beta/Hub": {}, "Beta/Far": {}},
                "links": list(beta_links),
            },
        }
    }


def _route(raw: dict, start: str, target: str, **kwargs) -> list[tuple[str, str, str]] | None:
    steps = zone_nav.plan(zone_nav.parse(raw), start, target, **kwargs)
    return None if steps is None else [(s.kind, s.from_zone, s.to_zone) for s in steps]


# ══════════════════════════════════════════════════════════════════════════════
# Planning
# ══════════════════════════════════════════════════════════════════════════════


def test_already_there_is_an_empty_route():
    assert _route(_raw(), _HUB, _HUB) == []


def test_walks_street_by_street():
    raw = _raw(_walk(_HUB, _S1), _walk(_S1, _S2))

    assert _route(raw, _HUB, _S2) == [("walk", _HUB, _S1), ("walk", _S1, _S2)]


def test_the_hub_button_is_one_step_from_anywhere_in_the_world():
    raw = _raw(_walk(_HUB, _S1), _walk(_HUB, _S2))

    assert _route(raw, _S2, _S1) == [("hub", _S2, _HUB), ("walk", _HUB, _S1)]


def test_a_walk_beats_the_hub_button_on_a_near_tie():
    """Both are one load; the button's cooldown and failure modes make the walk the better bet."""
    assert _route(_raw(_walk(_S1, _HUB)), _S1, _HUB) == [("walk", _S1, _HUB)]


def test_a_cooling_hub_button_loses_a_tie_it_would_otherwise_win():
    """Two transitions either way. Fresh, the button has less to go wrong than a teleporter; cooling, it waits."""
    raw = _raw(_walk(_HUB, _S1), _object(_S2, _S3), _walk(_S3, _S1))

    assert _route(raw, _S2, _S1, teleport_cooldown=0.0) == [("hub", _S2, _HUB), ("walk", _HUB, _S1)]
    assert _route(raw, _S2, _S1, teleport_cooldown=30.0) == [("object", _S2, _S3), ("walk", _S3, _S1)]


def test_waiting_out_the_hub_button_beats_an_extra_transition():
    raw = _raw(_walk(_S2, _S1), _walk(_S1, _HUB))

    assert _route(raw, _S2, _HUB, teleport_cooldown=30.0) == [("hub", _S2, _HUB)]


def test_a_list_object_skips_the_streets_between():
    network = {
        "zones": {
            _HUB: {"interact_xyz": _xyz(10), "reset_xyz": _xyz(11)},
            _LX: {"interact_xyz": _xyz(12), "reset_xyz": _xyz(13)},
            _LY: {"interact_xyz": _xyz(14), "reset_xyz": _xyz(15)},
        },
        "verified": True,
    }
    raw = _raw(_walk(_LX, _S1), _walk(_S1, _S2), _walk(_S2, _LY), networks=[network])

    steps = zone_nav.plan(zone_nav.parse(raw), _LX, _LY)

    assert [(s.kind, s.from_zone, s.to_zone) for s in steps] == [("list", _LX, _LY)]
    # The object used is the one standing in the zone the client is in.
    assert steps[0].params["interact_xyz"] == _xyz(12)


def test_a_list_aliases_an_entry_whose_list_text_is_not_its_display_name():
    """Nimbus Hanger's hub list shows just "Nimbus" -- not a locale name, so it lives here."""
    network = {
        "zones": {
            _HUB: {"interact_xyz": _xyz(1), "reset_xyz": _xyz(2)},
            _LX: {"interact_xyz": _xyz(3), "reset_xyz": _xyz(4), "list_aliases": ["Nimbus"]},
        },
        "verified": True,
    }
    nav = zone_nav.parse(_raw(networks=[network]))

    into_lx = next(s for s in nav.links if s.to_zone == _LX)
    out_of_lx = next(s for s in nav.links if s.from_zone == _LX)
    assert into_lx.params["list_aliases"] == ["Nimbus"]
    assert "list_aliases" not in out_of_lx.params


def test_list_aliases_must_be_non_empty_strings():
    network = {"zones": {_HUB: {"interact_xyz": _xyz(1), "reset_xyz": _xyz(2), "list_aliases": ["", 3]}}}

    with pytest.raises(ValueError, match="list_aliases"):
        zone_nav.parse(_raw(networks=[network]))


def test_a_list_member_without_an_object_can_still_carry_list_aliases():
    network = {
        "zones": {
            _HUB: {"interact_xyz": _xyz(1), "reset_xyz": _xyz(2)},
            _LX: {"list_aliases": ["Nimbus"]},
        },
        "verified": True,
    }
    nav = zone_nav.parse(_raw(networks=[network]))

    assert nav.links[0].params["list_aliases"] == ["Nimbus"]


def test_list_aliases_only_drops_the_zones_own_display_name():
    """Empyrea's hub list has two rows for "Chaos Jungle" -- only list_aliases is safe to match."""
    network = {
        "zones": {
            _HUB: {"interact_xyz": _xyz(1), "reset_xyz": _xyz(2)},
            _LX: {"list_aliases": ["Mandalla"], "list_aliases_only": True},
        },
        "verified": True,
    }
    nav = zone_nav.parse(_raw(networks=[network]))

    into_lx = next(s for s in nav.links if s.to_zone == _LX)
    assert into_lx.params["list_aliases"] == ["Mandalla"]
    assert into_lx.params["list_aliases_only"] is True


def test_list_aliases_only_needs_list_aliases_to_use():
    network = {"zones": {_HUB: {"interact_xyz": _xyz(1), "reset_xyz": _xyz(2), "list_aliases_only": True}}}

    with pytest.raises(ValueError, match="list_aliases_only"):
        zone_nav.parse(_raw(networks=[network]))


def test_list_aliases_only_must_be_a_bool():
    network = {
        "zones": {
            _HUB: {
                "interact_xyz": _xyz(1),
                "reset_xyz": _xyz(2),
                "list_aliases": ["Nimbus"],
                "list_aliases_only": "yes",
            }
        }
    }

    with pytest.raises(ValueError, match="list_aliases_only"):
        zone_nav.parse(_raw(networks=[network]))


def test_a_list_network_links_every_member_both_ways():
    network = {
        "zones": {z: {"interact_xyz": _xyz(1), "reset_xyz": _xyz(2)} for z in (_HUB, _LX, _LY)},
        "verified": True,
    }
    nav = zone_nav.parse(_raw(networks=[network]))

    pairs = {(s.from_zone, s.to_zone) for s in nav.links if s.kind == "list"}
    assert pairs == {(a, b) for a in (_HUB, _LX, _LY) for b in (_HUB, _LX, _LY) if a != b}


def test_a_sequential_teleporter_loses_to_a_short_walk_with_a_full_group():
    """Azteca's teleporters take one player at a time: four players are four loading screens."""
    raw = _raw(
        _object(_HUB, _S3, sequential=True, object_use_delay=9.0),
        _walk(_HUB, _S1),
        _walk(_S1, _S2),
        _walk(_S2, _S3),
    )

    assert _route(raw, _HUB, _S3, clients=1) == [("object", _HUB, _S3)]
    assert _route(raw, _HUB, _S3, clients=4) == [("walk", _HUB, _S1), ("walk", _S1, _S2), ("walk", _S2, _S3)]


def test_a_parallel_teleporter_costs_the_same_for_any_group():
    raw = _raw(_object(_HUB, _S3), _walk(_HUB, _S1), _walk(_S1, _S2), _walk(_S2, _S3))

    assert _route(raw, _HUB, _S3, clients=4) == [("object", _HUB, _S3)]


def test_avoiding_teleporters_finds_the_route_on_foot():
    raw = _raw(_object(_HUB, _S3), _walk(_HUB, _S1), _walk(_S1, _S2), _walk(_S2, _S3))

    assert _route(raw, _HUB, _S3) == [("object", _HUB, _S3)]
    assert _route(raw, _HUB, _S3, avoid={"object", "list"}) == [
        ("walk", _HUB, _S1),
        ("walk", _S1, _S2),
        ("walk", _S2, _S3),
    ]


def test_avoiding_the_hub_button_walks_back_to_the_hub():
    raw = _raw(_walk(_S2, _S3), _walk(_S3, _HUB), _walk(_HUB, _S1))

    assert _route(raw, _S2, _S1) == [("hub", _S2, _HUB), ("walk", _HUB, _S1)]
    assert _route(raw, _S2, _S1, avoid={"hub"}) == [
        ("walk", _S2, _S3),
        ("walk", _S3, _HUB),
        ("walk", _HUB, _S1),
    ]


def test_avoiding_a_kind_can_leave_no_route():
    raw = _raw(_object(_HUB, _ISLAND))

    assert _route(raw, _HUB, _ISLAND, avoid={"object"}) is None
    assert _ISLAND not in zone_nav.reachable(zone_nav.parse(raw), _HUB, avoid={"object"})


def test_an_avoided_link_is_routed_around_without_avoiding_its_kind():
    """A teleporter that failed for everyone -- one the account has not unlocked -- is skipped,
    while every other object link stays usable."""
    raw = _raw(_object(_HUB, _S3), _object(_HUB, _S1), _walk(_S1, _S2), _walk(_S2, _S3))
    [failed] = zone_nav.plan(zone_nav.parse(raw), _HUB, _S3)

    assert _route(raw, _HUB, _S3, avoid_links=[failed]) == [
        ("object", _HUB, _S1),
        ("walk", _S1, _S2),
        ("walk", _S2, _S3),
    ]


def test_an_avoided_implied_step_is_left_out_too():
    raw = _raw(_walk(_S2, _S3), _walk(_S3, _HUB))
    hub = zone_nav.Step("hub", _S2, _HUB)

    assert _route(raw, _S2, _HUB, avoid_links=[hub]) == [("walk", _S2, _S3), ("walk", _S3, _HUB)]


def test_a_ride_carries_its_cutscene_zone():
    steps = zone_nav.plan(zone_nav.parse(_raw(_ride(_S1, _ISLAND, "Alpha/Boat"))), _S1, _ISLAND)

    assert [s.kind for s in steps] == ["ride"]
    assert steps[0].params["ride_zone"] == "Alpha/Boat"


def test_a_side_world_is_entered_through_its_parent():
    raw = _raw(_walk(_HUB, _S1), beta_links=[_walk(_S1, "Beta/Hub"), _walk("Beta/Hub", "Beta/Far")])
    nav = zone_nav.parse(raw)

    assert _route(raw, _HUB, "Beta/Far") == [
        ("walk", _HUB, _S1),
        ("walk", _S1, "Beta/Hub"),
        ("walk", "Beta/Hub", "Beta/Far"),
    ]
    assert nav.entry_world("Beta") == "Alpha"
    assert nav.entry_world("Alpha") == "Alpha"


def test_the_hub_button_in_a_side_world_goes_to_its_own_hub():
    raw = _raw(beta_links=[_walk("Beta/Hub", "Beta/Far")])

    assert _route(raw, "Beta/Far", "Beta/Hub") == [("hub", "Beta/Far", "Beta/Hub")]


def test_a_world_may_use_another_worlds_hub():
    """Wintertusk has no hub of its own; its button lands in Grizzleheim's Northguard."""
    raw = _raw()
    raw["worlds"]["Beta"] = {"hub": _HUB, "zones": {"Beta/Far": {}}}

    assert _route(raw, "Beta/Far", _HUB) == [("hub", "Beta/Far", _HUB)]


def test_an_undeclared_zone_can_still_use_the_hub_button():
    """A boss room is on no map, but the button works there like anywhere else in the world."""
    raw = _raw(_walk(_HUB, _S1))

    assert _route(raw, "Alpha/Interiors/BossRoom", _S1) == [
        ("hub", "Alpha/Interiors/BossRoom", _HUB),
        ("walk", _HUB, _S1),
    ]


def test_a_second_hub_use_waits_out_the_cooldown():
    """Beta's button, then Alpha's: the second press pays the full cooldown the first started.

    From Street2, Alpha's hub is one teleporter or one button press. Pressed fresh, the button
    wins the tie; as a second press in the same route it waits 30 s, and the teleporter wins.
    """
    fresh = _raw(_object(_S2, _HUB))
    assert _route(fresh, _S2, _HUB) == [("hub", _S2, _HUB)]

    second = _raw(_object(_S2, _HUB), beta_links=[_walk("Beta/Hub", _S2)])
    assert _route(second, "Beta/Far", _HUB) == [
        ("hub", "Beta/Far", "Beta/Hub"),
        ("walk", "Beta/Hub", _S2),
        ("object", _S2, _HUB),
    ]


def test_unreachable_is_none():
    raw = _raw(_walk(_HUB, _S1))

    assert _route(raw, _HUB, _ISLAND) is None
    assert _route(raw, "Gamma/Nowhere", _S1) is None


def test_unverified_links_are_left_out_unless_asked_for():
    raw = _raw(_walk(_HUB, _S1, verified=False))

    assert _route(raw, _HUB, _S1) is None
    assert _route(raw, _HUB, _S1, include_unverified=True) == [("walk", _HUB, _S1)]


def test_verified_defaults_to_false():
    """A link copied in from game data must be vouched for before anything routes over it."""
    link = _walk(_HUB, _S1)
    del link["verified"]

    assert _route(_raw(link), _HUB, _S1) is None


def test_a_quest_swapped_zone_counts_as_arriving():
    steps = zone_nav.plan(zone_nav.parse(_raw(_walk(_HUB, _S1, also_arrives_in=[_S2]))), _HUB, _S1)

    assert steps[0].arrives_in == (_S1, _S2)


def test_reachable_follows_links_and_the_hub_button():
    nav = zone_nav.parse(_raw(_walk(_HUB, _S1), _walk(_S1, _S2)))

    assert zone_nav.reachable(nav, _S2) == {_S2, _HUB, _S1}


def _spiral(*, far_gate: bool = True, far_gate_verified: bool = True) -> dict:
    """Worlds joined by World Gates, and a home: HOME lands in Home's dorm, two walks from its gate.

    Far's gate is in its hub, Near's door is a walk from its hub and has no gate recorded, and
    Side is entered from Near.
    """
    raw = {
        "home": "Home/Dorm",
        "worlds": {
            "Home": {
                "hub": "Home/Hub",
                "door": "Home/Tree",
                "gate": {"interact_xyz": _xyz(7), "reset_xyz": _xyz(8), "verified": True},
                "zones": {"Home/Dorm": {"destination": False}, "Home/Hub": {}, "Home/Tree": {}},
                "links": [_walk("Home/Dorm", "Home/Hub"), _walk("Home/Hub", "Home/Tree")],
            },
            "Far": {
                "hub": "Far/Hub",
                "door": "Far/Hub",
                "zones": {"Far/Hub": {}, "Far/Deep": {}},
                "links": [_walk("Far/Hub", "Far/Deep")],
            },
            "Near": {
                "hub": "Near/Hub",
                "door": "Near/Door",
                "zones": {"Near/Hub": {}, "Near/Door": {}},
                "links": [_walk("Near/Door", "Near/Hub")],
            },
            "Side": {
                "hub": "Side/Hub",
                "entered_from": "Near",
                "zones": {"Side/Hub": {}},
                "links": [_walk("Near/Hub", "Side/Hub")],
            },
        },
    }
    if far_gate:
        raw["worlds"]["Far"]["gate"] = {"interact_xyz": _xyz(9), "reset_xyz": _xyz(10), "verified": far_gate_verified}
    return raw


def test_a_world_gate_reaches_every_other_world_with_a_door():
    nav = zone_nav.parse(_spiral())

    gates = {(s.from_zone, s.to_zone, s.params["world"]) for s in nav.links if s.kind == "gate"}

    assert gates == {
        ("Home/Tree", "Far/Hub", "Far"),
        ("Home/Tree", "Near/Door", "Near"),
        ("Far/Hub", "Home/Tree", "Home"),
        ("Far/Hub", "Near/Door", "Near"),
    }
    far_to_near = next(s for s in nav.links if (s.from_zone, s.to_zone) == ("Far/Hub", "Near/Door"))
    assert far_to_near.params["interact_xyz"] == _xyz(9)


def test_the_worlds_own_gate_beats_going_home():
    """The hub button and the gate are two transitions; HOME, two walks and a gate are four."""
    assert _route(_spiral(), "Far/Deep", "Near/Hub") == [
        ("hub", "Far/Deep", "Far/Hub"),
        ("gate", "Far/Hub", "Near/Door"),
        ("walk", "Near/Door", "Near/Hub"),
    ]


def test_a_world_without_a_gate_recorded_still_routes_home_to_one():
    assert _route(_spiral(far_gate=False), "Far/Deep", "Near/Hub") == [
        ("home", "Far/Deep", "Home/Dorm"),
        ("walk", "Home/Dorm", "Home/Hub"),
        ("walk", "Home/Hub", "Home/Tree"),
        ("gate", "Home/Tree", "Near/Door"),
        ("walk", "Near/Door", "Near/Hub"),
    ]


def test_home_is_one_step_from_a_zone_on_no_map():
    """A castle is in no world, so neither the hub button nor any link leaves it. HOME does."""
    assert _route(_spiral(), "Housing/Castle", "Home/Hub") == [
        ("home", "Housing/Castle", "Home/Dorm"),
        ("walk", "Home/Dorm", "Home/Hub"),
    ]


def test_a_side_world_is_reached_through_its_parents_gate():
    assert _route(_spiral(), "Far/Hub", "Side/Hub") == [
        ("gate", "Far/Hub", "Near/Door"),
        ("walk", "Near/Door", "Near/Hub"),
        ("walk", "Near/Hub", "Side/Hub"),
    ]


def test_an_unverified_gate_is_left_out_unless_asked_for():
    raw = _spiral(far_gate_verified=False)

    assert _route(raw, "Far/Deep", "Near/Hub")[0][0] == "home"
    assert _route(raw, "Far/Deep", "Near/Hub", include_unverified=True)[0][0] == "hub"


def test_the_hub_button_right_after_home_waits_out_their_shared_cooldown():
    """One timer covers both buttons. Fresh, the button would beat the teleporter on effort; after HOME it waits."""
    raw = _spiral()
    raw["worlds"]["Home"]["links"][0] = _object("Home/Dorm", "Home/Hub")

    assert _route(raw, "Housing/Castle", "Home/Hub") == [
        ("home", "Housing/Castle", "Home/Dorm"),
        ("object", "Home/Dorm", "Home/Hub"),
    ]


def test_a_cooling_timer_delays_home_too():
    step = zone_nav.Step(zone_nav.HOME, "Far/Deep", "Home/Dorm")

    assert zone_nav.step_cost(step, teleport_wait=12.0).waiting == 12.0


def test_avoiding_home_can_leave_no_route():
    assert _route(_spiral(far_gate=False), "Far/Deep", "Near/Hub", avoid={"home"}) is None


def test_a_home_that_is_not_declared_is_refused():
    raw = _spiral()
    raw["home"] = "Home/Nowhere"

    with pytest.raises(ValueError, match="home"):
        zone_nav.parse(raw)


def test_connectors_are_not_destinations():
    raw = _raw()
    raw["worlds"]["Alpha"]["zones"][_S3] = {"destination": False}
    world = zone_nav.parse(raw).worlds["Alpha"]

    assert _S3 not in world.destinations()
    assert _S1 in world.destinations()


# ══════════════════════════════════════════════════════════════════════════════
# Validation
# ══════════════════════════════════════════════════════════════════════════════


def test_a_restricted_zone_is_refused():
    raw = _raw()
    raw["worlds"]["Alpha"]["zones"]["WizardCity/WC_Duel_Arena_New"] = {}

    with pytest.raises(ValueError, match="restricted"):
        zone_nav.parse(raw)


def test_a_restricted_ride_zone_is_refused():
    with pytest.raises(ValueError, match="restricted"):
        zone_nav.parse(_raw(_ride(_S1, _ISLAND, "Battlegrounds/Mirage/Mirage_CTF_01")))


def test_a_link_to_an_undeclared_zone_is_refused():
    with pytest.raises(ValueError, match="not declared"):
        zone_nav.parse(_raw(_walk(_HUB, "Alpha/Nowhere")))


def test_an_unknown_kind_is_refused():
    """list and hub are generated; storing one is a mistake, not a shortcut."""
    link = _walk(_HUB, _S1)
    link["kind"] = "hub"

    with pytest.raises(ValueError, match="unknown kind"):
        zone_nav.parse(_raw(link))


def test_a_walk_without_xyz_is_refused():
    link = _walk(_HUB, _S1)
    del link["xyz"]

    with pytest.raises(ValueError, match="xyz"):
        zone_nav.parse(_raw(link))


def test_an_object_without_a_reset_is_refused():
    link = _object(_HUB, _S1)
    del link["reset_xyz"]

    with pytest.raises(ValueError, match="reset_xyz"):
        zone_nav.parse(_raw(link))


def test_a_ride_inside_its_own_zone_needs_no_cutscene_zone():
    """The Krokotopia boats: nothing loads until the skip."""
    link = _ride(_S1, _ISLAND, "Alpha/Boat")
    del link["ride_zone"]

    [step] = zone_nav.plan(zone_nav.parse(_raw(link)), _S1, _ISLAND)

    assert step.params["board"] == "object"
    assert "ride_zone" not in step.params


def test_a_ride_boarded_through_a_gate_needs_its_cutscene_zone():
    link = {"kind": "ride", "board": "walk", "from": _S1, "to": _ISLAND, "xyz": _xyz(1), "verified": True}

    with pytest.raises(ValueError, match="ride_zone"):
        zone_nav.parse(_raw(link))


def test_a_ride_boarded_through_a_gate_takes_a_walks_coordinates():
    link = {"kind": "ride", "board": "walk", "from": _S1, "to": _ISLAND, "ride_zone": "Alpha/Airship"}
    raw = _raw({**link, "xyz": _xyz(1), "verified": True})
    raw["worlds"]["Alpha"]["zones"][_S1] = {"arrivals": {_ISLAND: _xyz(9)}}

    [step] = zone_nav.plan(zone_nav.parse(raw), _S1, _ISLAND)

    assert step.params["xyz"] == _xyz(1)
    # Like a walk, it resets to where the ride back lands.
    assert step.params["reset_xyz"] == _xyz(9)


def test_an_unknown_way_to_board_is_refused():
    with pytest.raises(ValueError, match="board"):
        zone_nav.parse(_raw({**_ride(_S1, _ISLAND, "Alpha/Boat"), "board": "swim"}))


def test_a_ride_zone_on_the_map_is_refused():
    with pytest.raises(ValueError, match="declared zone"):
        zone_nav.parse(_raw(_ride(_S1, _ISLAND, _S2)))


def test_two_rides_through_one_cutscene_zone_are_refused():
    """A client found in the zone could not tell which ride it is on."""
    with pytest.raises(ValueError, match="more than one ride"):
        zone_nav.parse(_raw(_ride(_S1, _ISLAND, "Alpha/Boat"), _ride(_S2, _ISLAND, "Alpha/Boat")))


def test_a_client_left_on_a_ride_finishes_it():
    nav = zone_nav.parse(_raw(_ride(_S1, _ISLAND, "Alpha/Boat")))

    [step] = zone_nav.plan(nav, "Alpha/Boat", _ISLAND)

    assert (step.kind, step.from_zone, step.params["board_zone"]) == ("ride", "Alpha/Boat", _S1)


def test_a_ride_with_its_own_zone_is_a_second_transition():
    link = _ride(_S1, _ISLAND, "Alpha/Boat")
    in_zone = {k: v for k, v in link.items() if k != "ride_zone"}
    [with_zone] = zone_nav.plan(zone_nav.parse(_raw(link)), _S1, _ISLAND)
    [without] = zone_nav.plan(zone_nav.parse(_raw(in_zone)), _S1, _ISLAND)

    assert (zone_nav.step_cost(with_zone).transitions, zone_nav.step_cost(without).transitions) == (2, 1)


def test_a_gated_walk_carries_its_unlock_and_pays_for_the_wait():
    """Tomb of Storms: three objects, then the gate appears 20 s later."""
    spots = [{"interact_xyz": _xyz(n), "reset_xyz": _xyz(n + 10)} for n in (3, 4, 5)]
    gated = _walk(_HUB, _S1, unlock={"objects": spots, "opens_after": 20})

    [step] = zone_nav.plan(zone_nav.parse(_raw(gated)), _HUB, _S1)
    [plain] = zone_nav.plan(zone_nav.parse(_raw(_walk(_HUB, _S1))), _HUB, _S1)

    assert step.params["unlock"] == {"objects": spots, "opens_after": 20.0}
    gated_cost, plain_cost = zone_nav.step_cost(step), zone_nav.step_cost(plain)
    assert gated_cost.transitions == plain_cost.transitions
    assert gated_cost.waiting - plain_cost.waiting == 20.0


@pytest.mark.parametrize(
    "unlock",
    [
        {"objects": [], "opens_after": 20},
        {"objects": [{"interact_xyz": _xyz(3)}], "opens_after": 20},
        {"objects": [{"interact_xyz": _xyz(3), "reset_xyz": _xyz(4)}]},
    ],
)
def test_an_incomplete_unlock_is_refused(unlock):
    with pytest.raises(ValueError, match="unlock"):
        zone_nav.parse(_raw(_walk(_HUB, _S1, unlock=unlock)))


def test_only_a_walk_can_be_unlocked():
    spots = [{"interact_xyz": _xyz(3), "reset_xyz": _xyz(4)}]

    with pytest.raises(ValueError, match="unlock"):
        zone_nav.parse(_raw(_object(_HUB, _S1, unlock={"objects": spots, "opens_after": 20})))


def test_a_hub_that_is_not_declared_is_refused():
    raw = _raw()
    raw["worlds"]["Alpha"]["hub"] = "Alpha/Gone"

    with pytest.raises(ValueError, match="hub"):
        zone_nav.parse(raw)


def test_a_door_in_another_world_is_refused():
    raw = _raw()
    raw["worlds"]["Alpha"]["door"] = "Beta/Hub"

    with pytest.raises(ValueError, match="door"):
        zone_nav.parse(raw)


def test_a_world_gate_is_read_with_its_world():
    raw = _raw()
    raw["worlds"]["Alpha"]["gate"] = {"interact_xyz": _xyz(1), "reset_xyz": _xyz(2), "verified": True}

    nav = zone_nav.parse(raw)

    assert nav.worlds["Alpha"].gate["interact_xyz"] == _xyz(1)
    assert nav.worlds["Alpha"].gate["verified"] is True
    assert nav.worlds["Beta"].gate is None


def test_a_world_gate_defaults_to_unverified():
    raw = _raw()
    raw["worlds"]["Alpha"]["gate"] = {"interact_xyz": _xyz(1), "reset_xyz": _xyz(2)}

    assert zone_nav.parse(raw).worlds["Alpha"].gate["verified"] is False


def test_a_world_gate_without_a_door_is_refused():
    raw = _raw()
    raw["worlds"]["Beta"]["gate"] = {"interact_xyz": _xyz(1), "reset_xyz": _xyz(2)}

    with pytest.raises(ValueError, match="gate needs the world's door"):
        zone_nav.parse(raw)


@pytest.mark.parametrize("gate", [{"interact_xyz": _xyz(1)}, {"interact_xyz": _xyz(1), "reset_xyz": {"x": 1}}, []])
def test_an_incomplete_world_gate_is_refused(gate):
    raw = _raw()
    raw["worlds"]["Alpha"]["gate"] = gate

    with pytest.raises(ValueError, match="gate"):
        zone_nav.parse(raw)


def test_an_unknown_parent_world_is_refused():
    raw = _raw()
    raw["worlds"]["Beta"]["entered_from"] = "Gamma"

    with pytest.raises(ValueError, match="entered_from"):
        zone_nav.parse(raw)


def test_parent_worlds_that_loop_are_refused():
    raw = _raw()
    raw["worlds"]["Alpha"]["entered_from"] = "Beta"

    with pytest.raises(ValueError, match="loops"):
        zone_nav.parse(raw)


def test_a_zone_declared_twice_is_refused():
    raw = _raw()
    raw["worlds"]["Beta"]["zones"][_S1] = {}

    with pytest.raises(ValueError, match="declared by both"):
        zone_nav.parse(raw)


def test_a_duplicate_link_is_refused():
    with pytest.raises(ValueError, match="more than once"):
        zone_nav.parse(_raw(_walk(_HUB, _S1), _walk(_HUB, _S1)))


def test_a_one_zone_list_network_routes_nothing_yet():
    """The tester records a network one member's object at a time."""
    network = {"zones": {_HUB: {"interact_xyz": _xyz(1), "reset_xyz": _xyz(2)}}, "verified": True}

    assert [s for s in zone_nav.parse(_raw(networks=[network])).links if s.kind == "list"] == []


def test_a_list_member_without_an_object_is_a_destination_only():
    """Its object is not recorded yet, so the list can take you there but not from there."""
    spot = {"interact_xyz": _xyz(1), "reset_xyz": _xyz(2)}
    network = {"zones": {_HUB: spot, _LX: {}}, "verified": True}

    pairs = {(s.from_zone, s.to_zone) for s in zone_nav.parse(_raw(networks=[network])).links}

    assert pairs == {(_HUB, _LX)}


def test_a_half_recorded_list_object_is_refused():
    network = {"zones": {_HUB: {"interact_xyz": _xyz(1)}, _LX: {}}, "verified": True}

    with pytest.raises(ValueError, match="reset_xyz"):
        zone_nav.parse(_raw(networks=[network]))


def test_an_empty_list_network_is_refused():
    with pytest.raises(ValueError, match="at least one"):
        zone_nav.parse(_raw(networks=[{"zones": {}, "verified": True}]))


def test_an_arrival_point_is_a_walks_default_reset():
    """Where you land coming back from Street1 is clear of the Hub's gate to it."""
    link = _walk(_HUB, _S1)
    del link["reset_xyz"]
    raw = _raw(link)
    raw["worlds"]["Alpha"]["zones"][_HUB] = {"arrivals": {_S1: _xyz(77)}}

    [step] = zone_nav.parse(raw).links

    assert step.params["reset_xyz"] == _xyz(77)


def test_a_recorded_reset_wins_over_the_arrival_point():
    raw = _raw(_walk(_HUB, _S1))
    raw["worlds"]["Alpha"]["zones"][_HUB] = {"arrivals": {_S1: _xyz(77)}}

    [step] = zone_nav.parse(raw).links

    assert step.params["reset_xyz"] == _xyz(2)


def test_an_arrival_from_an_undeclared_zone_is_refused():
    raw = _raw()
    raw["worlds"]["Alpha"]["zones"][_HUB] = {"arrivals": {"Alpha/Nowhere": _xyz(1)}}

    with pytest.raises(ValueError, match="arrival from undeclared"):
        zone_nav.parse(raw)


def test_a_malformed_zone_attribute_is_refused():
    raw = _raw()
    raw["worlds"]["Alpha"]["zones"][_S1] = {"label": 5, "arrivals": {_HUB: {"x": 1}}}

    with pytest.raises(ValueError) as caught:
        zone_nav.parse(raw)

    assert "label" in str(caught.value)
    assert "arrivals" in str(caught.value)


def test_a_zone_label_is_available_from_its_world():
    raw = _raw()
    raw["worlds"]["Alpha"]["zones"][_S3] = {"label": "Sewers"}
    world = zone_nav.parse(raw).worlds["Alpha"]

    assert world.label(_S3) == "Sewers"
    assert world.label(_S1) is None


def test_a_volume_name_must_be_text():
    with pytest.raises(ValueError, match="volume"):
        zone_nav.parse(_raw(_walk(_HUB, _S1, volume=3)))


def test_dumps_keeps_small_objects_on_one_line():
    text = zone_nav.dumps(_raw(_walk(_HUB, _S1)))

    assert '"xyz": {"x": 1, "y": 0.0, "z": 0.0}' in text
    assert zone_nav.parse(json.loads(text)).links[0].to_zone == _S1


def test_every_problem_is_reported_at_once():
    raw = _raw(_walk(_HUB, "Alpha/Nowhere"))
    raw["worlds"]["Alpha"]["hub"] = "Alpha/Gone"

    with pytest.raises(ValueError) as caught:
        zone_nav.parse(raw)

    assert "not declared" in str(caught.value)
    assert "hub" in str(caught.value)


def test_an_unreadable_file_loads_as_an_empty_map(monkeypatch, tmp_path):
    """Nothing routes on it, so a node fails with 'no route' instead of the app not starting."""
    monkeypatch.setattr(zone_nav, "ZONE_NAV_PATH", tmp_path / "missing.json")
    zone_nav.load.cache_clear()
    try:
        assert zone_nav.load().worlds == {}
    finally:
        zone_nav.load.cache_clear()


# ══════════════════════════════════════════════════════════════════════════════
# The shipped map
# ══════════════════════════════════════════════════════════════════════════════


def _shipped() -> zone_nav.NavMap:
    """Parsed strictly: load() would log and return an empty map, hiding the problem."""
    return zone_nav.parse(json.loads(zone_nav.ZONE_NAV_PATH.read_text(encoding="utf-8")))


def test_the_shipped_map_is_valid():
    assert "WizardCity" in _shipped().worlds


def test_the_shipped_map_is_in_canonical_form():
    """The tester rewrites the file with dumps(); a hand edit in another layout would churn the diff."""
    text = zone_nav.ZONE_NAV_PATH.read_text(encoding="utf-8")

    assert zone_nav.dumps(json.loads(text)) == text, "reformat with zone_nav.dumps()"


def test_every_spiral_door_world_is_declared():
    from bartlebot.worlds import SPIRAL_DOOR_WORLDS

    nav = _shipped()

    assert [w for w in SPIRAL_DOOR_WORLDS if w not in nav.worlds] == []
    assert all(nav.worlds[w].door for w in SPIRAL_DOOR_WORLDS)


def test_commons_to_colossus_boulevard_goes_through_the_shopping_district():
    """The route Prince Gobblestone's bot records by hand."""
    steps = zone_nav.plan(_shipped(), "WizardCity/WC_Hub", "WizardCity/WC_Streets/WC_Colossus")

    assert [(s.kind, s.from_zone, s.to_zone) for s in steps] == [
        ("walk", "WizardCity/WC_Hub", "WizardCity/WC_Shop_Area"),
        ("walk", "WizardCity/WC_Shop_Area", "WizardCity/WC_Streets/WC_Colossus"),
    ]


def test_the_world_tree_reaches_the_commons_by_button_or_on_foot():
    nav = _shipped()
    tree, commons = "WizardCity/WC_Ravenwood_Teleporter", "WizardCity/WC_Hub"

    assert [s.kind for s in zone_nav.plan(nav, tree, commons, teleport_cooldown=30.0)] == ["hub"]
    assert [s.to_zone for s in zone_nav.plan(nav, tree, commons, avoid={"hub"})] == [
        "WizardCity/WC_Ravenwood",
        commons,
    ]


def test_home_is_the_dorm_that_pressing_home_verifies():
    from bartlebot.nodes import wizard_city

    assert _shipped().home == wizard_city.DORM_ZONE


def test_the_commons_reaches_another_world_through_the_world_tree_gate():
    steps = zone_nav.plan(_shipped(), "WizardCity/WC_Hub", "Krokotopia/KT_WorldTeleporter")

    assert [(s.kind, s.to_zone) for s in steps] == [
        ("walk", "WizardCity/WC_Ravenwood"),
        ("walk", "WizardCity/WC_Ravenwood_Teleporter"),
        ("gate", "Krokotopia/KT_WorldTeleporter"),
    ]


def test_crab_alley_is_reached_through_the_stone_block():
    steps = zone_nav.plan(
        _shipped(), "WizardCity/WC_Hub", "WizardCity/WC_Streets/WC_Triton_Underwater/WC_Triton_Underwater"
    )

    assert steps[-1].kind == "object"
