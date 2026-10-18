"""Node geometry: where each shape puts its ports, and the snap that lines them up.

Every shape puts its first port, on either side, PORT_Y0 below its top, so nodes whose
tops are level -- as snapping to the grid by the top-left corner makes them -- have
their first ports level, whatever their heights.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPointF

from bartlebot.gui.canvas.call_node_item import CallNodeItem, CollapsedRegionItem, PortNodeItem
from bartlebot.gui.canvas.node_item import GRID_H, GRID_W, NODE_H, PORT_PITCH, PORT_Y0, NodeItem


def _ports(n: int, prefix: str) -> list[tuple[str, str]]:
    return [(f"{prefix}{i}", f"{prefix} {i}") for i in range(n)]


def _call(entries: int, exits: int) -> CallNodeItem:
    return CallNodeItem(
        "r", "Call", subline="lib", entries=_ports(entries, "in"), exits=_ports(exits, "out"), interactive=True
    )


def _collapsed(entries: int, exits: int) -> CollapsedRegionItem:
    return CollapsedRegionItem(
        "r", "Region", subline="", entries=_ports(entries, "in"), exits=_ports(exits, "out"), color=0, interactive=True
    )


def _first_in(node: NodeItem) -> float:
    return node.port_in_at(node.input_ports()[0]).y() - node.pos().y()


def _first_out(node: NodeItem) -> float:
    first = node.returns[0] if node.returns else None
    return node.port_out_for(first).y() - node.pos().y()


@pytest.mark.parametrize("returns", [["SUCCESS"], ["SUCCESS", "FAILURE"]])
def test_a_plain_nodes_first_ports_are_level_at_port_y0(qapp, returns):
    node = NodeItem("s", interactive=True, returns=returns)
    assert _first_in(node) == _first_out(node) == PORT_Y0


@pytest.mark.parametrize("make", [_call, _collapsed])
@pytest.mark.parametrize("entries, exits", [(1, 1), (1, 3), (3, 1), (4, 4)])
def test_a_folded_nodes_first_ports_are_level_at_port_y0_however_tall(qapp, make, entries, exits):
    node = make(entries, exits)
    assert _first_in(node) == _first_out(node) == PORT_Y0


def test_an_entry_and_exit_pill_port_at_port_y0(qapp):
    assert _first_out(PortNodeItem("e", "In", is_entry=True)) == PORT_Y0
    assert _first_in(PortNodeItem("x", "Out", is_entry=False)) == PORT_Y0


def test_later_ports_follow_at_one_pitch_on_every_shape(qapp):
    plain = NodeItem("s", interactive=True, returns=["SUCCESS", "FAILURE"])
    call = _call(2, 2)
    assert plain.port_out_for("FAILURE").y() == PORT_Y0 + PORT_PITCH
    assert call.port_in_at("in1").y() == call.port_out_for("out1").y() == PORT_Y0 + PORT_PITCH


def test_a_plain_node_squeezes_more_ports_than_its_height_holds_at_one_pitch(qapp):
    node = NodeItem("s", interactive=True, returns=["A", "B", "C", "D"])
    assert node.port_out_for("A").y() == PORT_Y0
    assert node.port_out_for("D").y() < NODE_H


def test_a_folded_node_with_one_port_row_is_a_plain_nodes_height(qapp):
    assert _collapsed(1, 1).body_height == NODE_H
    assert _collapsed(2, 1).body_height == NODE_H + PORT_PITCH - 3


@pytest.mark.parametrize("make", [_call, _collapsed])
def test_a_folded_nodes_rows_fit_inside_it(qapp, make):
    node = make(3, 5)
    assert node.port_out_for("out4").y() + PORT_PITCH / 2 <= node.body_height


def test_snapping_puts_the_top_left_corner_on_the_grid(qapp):
    node = _collapsed(3, 3)
    node.set_snap_enabled(True)
    node.setPos(QPointF(GRID_W * 2 + 100, GRID_H * 4 + 40))
    assert node.pos() == QPointF(GRID_W * 2, GRID_H * 4)
    node.setPos(QPointF(GRID_W * 2 - 100, GRID_H * 4 - 40))
    assert node.pos() == QPointF(GRID_W * 2, GRID_H * 4)


def test_snapped_nodes_of_different_heights_in_one_row_have_level_first_ports(qapp):
    nodes = [NodeItem("s", interactive=True, returns=["SUCCESS", "FAILURE"]), _call(2, 1), _collapsed(4, 2)]
    for i, node in enumerate(nodes):
        node.set_snap_enabled(True)
        node.setPos(QPointF(GRID_W * i + 7, GRID_H * 3 - 30))
    assert len({n.body_height for n in nodes}) == 3  # or this proves nothing
    assert len({n.port_in().y() for n in nodes} | {n.port_out_for(n.returns[0]).y() for n in nodes}) == 1
