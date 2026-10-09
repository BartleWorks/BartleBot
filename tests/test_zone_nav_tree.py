"""Unit tests for the zone navigation tester's world tree and map colors."""

from bartlebot.game import zone_nav
from tools.zone_nav_tester import tree

_HUB, _S1, _S2, _S3, _ISLAND, _LOST = "W/Hub", "W/S1", "W/S2", "W/S3", "W/Island", "W/Lost"
_DOJO = "W/Interiors/Dojo"

_GRAPH = {
    _HUB: {_S1, _DOJO},
    _S1: {_HUB, _S2},
    _S2: {_S1, _DOJO},
    _DOJO: {_HUB, _S2, _S3},
    _S3: {_DOJO},
}


def _neighbors(zone: str) -> set[str]:
    return _GRAPH.get(zone, set())


def _xyz() -> dict:
    return {"x": 0.0, "y": 0.0, "z": 0.0}


def _walk(src: str, dst: str, verified: bool = True) -> dict:
    return {"kind": "walk", "from": src, "to": dst, "xyz": _xyz(), "verified": verified}


def _nav(*links, extra_worlds=None) -> zone_nav.NavMap:
    object_link = {"kind": "object", "from": _HUB, "to": _ISLAND, "interact_xyz": _xyz(), "reset_xyz": _xyz()}
    raw = {
        "worlds": {
            "W": {
                "hub": _HUB,
                "door": _HUB,
                "zones": {z: {} for z in (_HUB, _S1, _S2, _S3, _ISLAND, _LOST)},
                "links": [object_link, *links],
            },
            **(extra_worlds or {}),
        }
    }
    return zone_nav.parse(raw)


def test_a_zone_hangs_under_the_zone_it_is_reached_from():
    built = tree.build(_nav(), "W", _neighbors)

    assert built.parent[_S1] == _HUB
    assert built.parent[_S2] == _S1


def test_streets_are_preferred_over_a_shortcut_through_an_interior():
    """The dojo reaches Street2 in two hops, the street in two as well -- the street wins."""
    assert tree.build(_nav(), "W", _neighbors).parent[_S2] == _S1


def test_a_zone_only_reachable_through_an_interior_hangs_under_the_zone_before_it():
    assert tree.build(_nav(), "W", _neighbors).parent[_S3] == _HUB


def test_a_map_link_joins_zones_the_game_graph_does_not():
    """A teleporter can reach a zone zonemap.nav has no edge to."""
    assert tree.build(_nav(), "W", _neighbors).parent[_ISLAND] == _HUB


def test_a_zone_nothing_leads_to_is_listed_apart():
    built = tree.build(_nav(), "W", _neighbors)

    assert built.unattached == [_LOST]
    assert built.path_to(_LOST) == []


def test_colors_follow_verified_and_tentative_routes_from_the_hub():
    colors = tree.statuses(_nav(_walk(_HUB, _S1), _walk(_S1, _S2, verified=False)), "W")

    assert colors[_HUB] == tree.VERIFIED
    assert colors[_S1] == tree.VERIFIED
    assert colors[_S2] == tree.TENTATIVE
    assert colors[_S3] == tree.NO_DATA


def test_avoiding_teleporters_colors_what_is_mapped_on_foot():
    """The Island is only reachable by teleporter, so on foot it has no route."""
    nav = _nav(_walk(_HUB, _S1))

    assert tree.statuses(nav, "W")[_ISLAND] == tree.TENTATIVE
    assert tree.statuses(nav, "W", avoid={"object", "list"})[_ISLAND] == tree.NO_DATA
    assert tree.statuses(nav, "W", avoid={"object", "list"})[_S1] == tree.VERIFIED


def test_an_avoided_link_does_not_count_for_a_hop():
    nav = _nav()

    assert tree.link_status(nav, _HUB, _ISLAND) == tree.TENTATIVE
    assert tree.link_status(nav, _HUB, _ISLAND, avoid={"object"}) == tree.NO_DATA


def test_the_hops_down_the_tree_say_what_to_record():
    nav = _nav(_walk(_HUB, _S1, verified=False))

    assert tree.hops_to(nav, tree.build(nav, "W", _neighbors), _S2) == [
        (_HUB, _S1, tree.TENTATIVE),
        (_S1, _S2, tree.NO_DATA),
    ]


def test_a_side_world_is_only_green_once_the_way_in_is_mapped():
    side = {"V": {"hub": "V/Hub", "entered_from": "W", "zones": {"V/Hub": {}}, "links": []}}

    assert tree.statuses(_nav(extra_worlds=side), "V")["V/Hub"] == tree.NO_DATA
    assert tree.statuses(_nav(_walk(_HUB, "V/Hub"), extra_worlds=side), "V")["V/Hub"] == tree.VERIFIED
