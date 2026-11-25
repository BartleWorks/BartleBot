"""check_bot_routes.py: which pairs of points a bot walks between, and what planning each finds."""

import dataclasses
import sys
from pathlib import Path

import pytest
from bartleby.runtime.movement import zone_maps
from bartleby.runtime.movement.collision import MESH, WALKABLE, Solid
from bartleby.runtime.movement.route import ZoneFiles, build_planner

from bartlebot.bot.schema import BotConfig, StateModel, TransitionModel

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import check_bot_routes as cbr  # noqa: E402

Z = "Test/T_Walk"


def _xyz(x, y=0.0, z=0.0):
    return {"x": x, "y": y, "z": z}


def _bot(states, edges):
    return BotConfig(
        id="b",
        name="B",
        initial=states[0].id,
        recovery_state=states[0].id,
        states=states,
        transitions=[TransitionModel(trigger="advance", source=a, dest=b, when="SUCCESS") for a, b in edges],
    )


def _s(sid, node_type, **params):
    return StateModel(id=sid, node_type=node_type, label=sid.upper(), params=params)


def test_a_node_walks_from_its_reset_to_its_point():
    bot = _bot([_s("a", "interact", zone=Z, interact_xyz=_xyz(100), reset_xyz=_xyz(50))], [])

    assert cbr.pairs_of(bot) == {cbr.Pair(Z, (50.0, 0.0, 0.0), (100.0, 0.0, 0.0), "A: reset_xyz › interact_xyz")}


def test_the_next_node_walks_from_where_the_last_left_off_through_a_fight():
    bot = _bot(
        [
            _s("a", "teleport_to_coordinates", zone=Z, xyz=_xyz(100)),
            _s("f", "handle_combat"),
            _s("b", "teleport_to_coordinates", zone=Z, xyz=_xyz(900)),
        ],
        [("a", "f"), ("f", "b")],
    )

    assert cbr.Pair(Z, (100.0, 0.0, 0.0), (900.0, 0.0, 0.0), "A › B") in cbr.pairs_of(bot)


def test_a_zone_change_or_a_port_loses_track_of_the_wizard():
    bot = _bot(
        [
            _s("a", "enter_zone", from_zone=Z, to_zone="Test/T_Next", xyz=_xyz(100), reset_xyz=_xyz(50)),
            _s("b", "teleport_to_coordinates", zone=Z, xyz=_xyz(900)),
            _s("p", "teleport_to_friend"),
            _s("c", "teleport_to_coordinates", zone=Z, xyz=_xyz(300)),
        ],
        [("a", "b"), ("b", "p"), ("p", "c")],
    )

    pairs = cbr.pairs_of(bot)

    assert {p.where for p in pairs} == {"A: reset_xyz › xyz"}


def test_an_unset_point_is_no_point():
    bot = _bot([_s("a", "interact", zone=Z, interact_xyz=_xyz(100), reset_xyz=_xyz(0))], [])

    assert cbr.pairs_of(bot) == set()


@pytest.fixture
def zone(monkeypatch):
    """A hall, and an island no floor joins to it, as the zone every pair is in."""

    def _floor(x0, y0, x1, y1):
        a, b, c, d = (x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0)
        return [a, b, c, a, c, d]

    vertices = [*_floor(0, -1000, 40000, 1000), *_floor(60000, -1000, 61000, 1000)]
    faces = [(k, k + 1, k + 2) for k in range(0, len(vertices), 3)]
    solid = Solid(MESH, WALKABLE, (1, 0, 0, 0, 1, 0, 0, 0, 1), (0.0, 0.0, 0.0), 1.0, (), vertices, faces)
    built = build_planner(ZoneFiles(Z, [solid], [], {}))

    def _planner(_zone):
        return dataclasses.replace(
            built, passable=built.passable.copy(), links=list(built.links), closed=[], blocks=[], _took={}
        )

    monkeypatch.setattr(zone_maps.SHIPPED, "planner", _planner)


def test_planning_tells_clear_from_long_from_no_route(zone):
    clear = cbr.Pair(Z, (100.0, 0.0, 0.0), (3000.0, 0.0, 0.0), "clear")
    long = cbr.Pair(Z, (100.0, 0.0, 0.0), (39000.0, 0.0, 0.0), "long")
    apart = cbr.Pair(Z, (100.0, 0.0, 0.0), (60500.0, 0.0, 0.0), "apart")

    found = {f.pair.where: f for f in cbr._check_zone(Z, [clear, long, apart])}

    assert found["clear"].kind == "ok" and found["clear"].seconds == pytest.approx(2900 / 600, rel=0.1)
    assert found["long"].kind == "long" and found["long"].seconds > cbr.LONG_WALK
    assert found["apart"].kind == "no route" and "nowhere on the floor joins the two" in found["apart"].detail


def test_the_report_counts_each_kind_and_lists_the_problems():
    pair = cbr.Pair(Z, (1.0, 2.0, 3.0), (4.0, 5.0, 6.0), "A › B")
    findings = [
        cbr.Finding(pair, bots=["w/x.json"], kind="no route", detail="nowhere on the floor joins the two"),
        cbr.Finding(pair, bots=["w/y.json"], kind="ok"),
    ]

    text = cbr.report(findings, bots=2, seconds=1.0)

    assert "- No route: 1" in text and "- Clear: 1" in text
    assert "| Test/T_Walk | w/x.json | A › B | (1, 2, 3) | (4, 5, 6) | 0 | nowhere on the floor joins the two |" in text
