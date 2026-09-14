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


def test_a_cooling_hub_button_makes_walking_worth_it():
    raw = _raw(_walk(_S2, _S1), _walk(_S1, _HUB))

    assert _route(raw, _S2, _HUB, hub_cooldown=0.0) == [("hub", _S2, _HUB)]
    assert _route(raw, _S2, _HUB, hub_cooldown=30.0) == [("walk", _S2, _S1), ("walk", _S1, _HUB)]


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
        "zones": {_HUB: {"interact_xyz": _xyz(1), "reset_xyz": _xyz(2), "list_aliases": ["Nimbus"], "list_aliases_only": "yes"}}
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
    """Azteca's teleporters need 8-10 s per player; four players pay that three times."""
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

    Alpha's hub is two walks from Street2 or one second button press. Pressed fresh, the
    button wins; as a second press in the same route, it waits 30 s and the walks win.
    """
    fresh = _raw(_walk(_S2, _S1), _walk(_S1, _HUB))
    assert _route(fresh, _S2, _HUB) == [("hub", _S2, _HUB)]

    second = _raw(_walk(_S2, _S1), _walk(_S1, _HUB), beta_links=[_walk("Beta/Hub", _S2)])
    assert _route(second, "Beta/Far", _HUB) == [
        ("hub", "Beta/Far", "Beta/Hub"),
        ("walk", "Beta/Hub", _S2),
        ("walk", _S2, _S1),
        ("walk", _S1, _HUB),
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


def test_a_ride_with_its_own_zone_costs_a_second_load():
    link = _ride(_S1, _ISLAND, "Alpha/Boat")
    in_zone = {k: v for k, v in link.items() if k != "ride_zone"}
    [with_zone] = zone_nav.plan(zone_nav.parse(_raw(link)), _S1, _ISLAND)
    [without] = zone_nav.plan(zone_nav.parse(_raw(in_zone)), _S1, _ISLAND)

    assert zone_nav.step_cost(with_zone) - zone_nav.step_cost(without) == zone_nav.LOAD_SECONDS


def test_a_gated_walk_carries_its_unlock_and_pays_for_the_wait():
    """Tomb of Storms: three objects, then the gate appears 20 s later."""
    spots = [{"interact_xyz": _xyz(n), "reset_xyz": _xyz(n + 10)} for n in (3, 4, 5)]
    gated = _walk(_HUB, _S1, unlock={"objects": spots, "opens_after": 20})

    [step] = zone_nav.plan(zone_nav.parse(_raw(gated)), _HUB, _S1)
    [plain] = zone_nav.plan(zone_nav.parse(_raw(_walk(_HUB, _S1))), _HUB, _S1)

    assert step.params["unlock"] == {"objects": spots, "opens_after": 20.0}
    assert zone_nav.step_cost(step) - zone_nav.step_cost(plain) == 20.0 + 3 * zone_nav.UNLOCK_OBJECT_SECONDS


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

    assert [s.kind for s in zone_nav.plan(nav, tree, commons)] == ["hub"]
    assert [s.to_zone for s in zone_nav.plan(nav, tree, commons, hub_cooldown=30.0)] == [
        "WizardCity/WC_Ravenwood",
        commons,
    ]


def test_crab_alley_is_reached_through_the_stone_block():
    steps = zone_nav.plan(
        _shipped(), "WizardCity/WC_Hub", "WizardCity/WC_Streets/WC_Triton_Underwater/WC_Triton_Underwater"
    )

    assert steps[-1].kind == "object"
