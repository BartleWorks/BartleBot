"""Unit tests for the zone navigation tester's edits to zone_nav.json."""

import copy

from bartlebot import zone_nav
from tools.zone_nav_tester import edits

_BASE = {
    "worlds": {
        "W": {"hub": "W/Hub", "door": "W/Hub", "zones": {"W/Hub": {}, "W/A": {}, "W/B": {}}, "links": []},
        "V": {"hub": "V/Hub", "entered_from": "W", "zones": {"V/Hub": {}}, "links": []},
    }
}


def _raw() -> dict:
    return copy.deepcopy(_BASE)


def _walk(src: str, dst: str, x: float = 1.0) -> dict:
    return {"kind": "walk", "from": src, "to": dst, "xyz": {"x": x, "y": 0.0, "z": 0.0}, "verified": False}


def test_xyz_rounds_to_hundredths():
    assert edits.xyz((1.23456, -2.0, 3.005)) == {"x": 1.23, "y": -2.0, "z": 3.0}


def test_a_new_link_lives_with_its_destination_world():
    raw = _raw()

    assert edits.upsert_link(raw, _walk("W/A", "V/Hub")) == "added"
    assert raw["worlds"]["V"]["links"][0]["to"] == "V/Hub"


def test_recording_a_link_again_replaces_it():
    raw = _raw()
    edits.upsert_link(raw, _walk("W/Hub", "W/A", x=1.0))

    assert edits.upsert_link(raw, _walk("W/Hub", "W/A", x=2.0)) == "replaced"
    assert [link["xyz"]["x"] for link in raw["worlds"]["W"]["links"]] == [2.0]


def _pairs(raw: dict) -> set[tuple[str, str]]:
    return {(s.from_zone, s.to_zone) for s in zone_nav.parse(raw).links}


def test_a_list_use_routes_into_its_destination_at_once():
    raw = _raw()
    spot = {"x": 0.0, "y": 0.0, "z": 0.0}
    edits.add_list_spot(raw, "W/Hub", "W/B", spot, spot)

    assert _pairs(raw) == {("W/Hub", "W/B")}


def test_a_list_use_from_the_destination_routes_back():
    raw = _raw()
    spot = {"x": 0.0, "y": 0.0, "z": 0.0}
    edits.add_list_spot(raw, "W/Hub", "W/B", spot, spot)
    edits.add_list_spot(raw, "W/B", "W/A", spot, spot)

    [network] = raw["worlds"]["W"]["list_networks"]
    assert set(network["zones"]) == {"W/Hub", "W/A", "W/B"}
    assert _pairs(raw) == {("W/Hub", "W/B"), ("W/Hub", "W/A"), ("W/B", "W/Hub"), ("W/B", "W/A")}


def test_networks_that_share_a_zone_are_merged():
    """Two one-zone networks, as the first recorder left them, are one list once a use joins them."""
    raw = _raw()
    spot = {"x": 0.0, "y": 0.0, "z": 0.0}
    own = {"interact_xyz": spot, "reset_xyz": spot}
    raw["worlds"]["W"]["list_networks"] = [
        {"zones": {"W/A": own}, "verified": True},
        {"zones": {"W/B": own}, "verified": True},
    ]

    edits.add_list_spot(raw, "W/Hub", "W/A", spot, spot, verified=True)
    edits.add_list_spot(raw, "W/Hub", "W/B", spot, spot, verified=True)

    [network] = raw["worlds"]["W"]["list_networks"]
    assert all(network["zones"][zone] for zone in ("W/Hub", "W/A", "W/B"))
    assert network["verified"] is True
    assert len(_pairs(raw)) == 6


def test_a_network_is_verified_only_while_every_member_was_used_in_game():
    raw = _raw()
    spot = {"x": 0.0, "y": 0.0, "z": 0.0}
    edits.add_list_spot(raw, "W/Hub", "W/B", spot, spot, verified=True)
    edits.add_list_spot(raw, "W/B", "W/Hub", spot, spot, verified=True)
    [network] = raw["worlds"]["W"]["list_networks"]
    assert network["verified"] is True

    edits.add_list_spot(raw, "W/A", "W/Hub", spot, spot, verified=False)

    assert network["verified"] is False


def test_an_arrival_point_becomes_the_walk_backs_reset():
    raw = _raw()
    edits.upsert_link(raw, _walk("W/A", "W/Hub"))

    edits.set_arrival(raw, "W/A", "W/Hub", {"x": 9.0, "y": 9.0, "z": 9.0})

    [step] = zone_nav.parse(raw).links
    assert step.params["reset_xyz"] == {"x": 9.0, "y": 9.0, "z": 9.0}


def test_mark_verified_covers_links_and_list_networks():
    raw = _raw()
    edits.upsert_link(raw, _walk("W/Hub", "W/A"))
    spot = {"x": 0.0, "y": 0.0, "z": 0.0}
    edits.add_list_spot(raw, "W/Hub", "W/B", spot, spot)
    edits.add_list_spot(raw, "W/B", "W/Hub", spot, spot)
    steps = [s for s in zone_nav.parse(raw).links if (s.from_zone, s.to_zone) in {("W/Hub", "W/A"), ("W/B", "W/Hub")}]

    assert edits.mark_verified(raw, steps) == 2
    assert all(s.verified for s in zone_nav.parse(raw).links)


def test_declaring_a_zone_adds_it_to_the_world():
    raw = _raw()

    edits.declare_zone(raw, "W", "W/Interiors/Dojo")

    assert edits.world_declaring(raw, "W/Interiors/Dojo") == "W"


def test_nearest_only_matches_within_range():
    points = [("far", (1000.0, 0.0, 0.0)), ("near", (30.0, 40.0, 0.0))]

    assert edits.nearest(points, (0.0, 0.0, 0.0), 100.0) == ("near", (30.0, 40.0, 0.0), 50.0)
    assert edits.nearest(points, (0.0, 0.0, 0.0), 10.0) is None
    assert edits.nearest([], (0.0, 0.0, 0.0), 10.0) is None
