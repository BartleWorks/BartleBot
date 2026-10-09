"""An edge's condition label is drawn over every edge and under every node.

It cannot be a child of its edge for that -- Qt paints a child straight after its
parent, under every edge stacked later -- so it is an item of its own, and these pin
that its edge still carries it: into and out of the scene, and shown and hidden.
"""

import pytest
from PyQt6.QtCore import QLineF, QPointF, Qt
from PyQt6.QtWidgets import QGraphicsLineItem, QGraphicsScene

from bartlebot.gui.canvas.edge_item import EdgeItem
from bartlebot.gui.canvas.node_item import NodeItem


@pytest.fixture
def scene(qapp):
    return QGraphicsScene()


def _labelled_edge(scene: QGraphicsScene) -> EdgeItem:
    src, dst = NodeItem("a", returns=["SUCCESS"]), NodeItem("b")
    dst.setPos(QPointF(700, 300))
    edge = EdgeItem(src, dst, when="SUCCESS", has_flags=True)
    for item in (src, dst, edge):
        scene.addItem(item)
    edge.set_condition("swords == 2")
    return edge


def test_the_label_is_drawn_over_an_edge_stacked_after_its_own(scene):
    edge = _labelled_edge(scene)
    center = edge.label.sceneBoundingRect().center()
    # Added last at an edge's z, so it stacks above every edge before it, as a later edge does.
    later = QGraphicsLineItem(QLineF(center.x(), center.y() - 50, center.x(), center.y() + 50))
    later.setZValue(edge.zValue())
    scene.addItem(later)

    under_point = scene.items(center, Qt.ItemSelectionMode.IntersectsItemShape, Qt.SortOrder.DescendingOrder)
    assert under_point.index(edge.label) < under_point.index(later)


def test_the_label_is_drawn_under_the_nodes(scene):
    edge = _labelled_edge(scene)
    assert edge.zValue() < edge.label.zValue() < edge.src_node.zValue()


def test_the_label_leaves_the_scene_with_its_edge_and_comes_back_with_it(scene):
    edge = _labelled_edge(scene)
    assert edge.label.scene() is scene

    scene.removeItem(edge)
    assert edge.label.scene() is None

    scene.addItem(edge)
    assert edge.label.scene() is scene


def test_a_label_given_to_an_edge_already_in_the_scene_is_added_to_it(scene):
    edge = _labelled_edge(scene)
    edge.set_condition(None)
    plain = EdgeItem(edge.src_node, edge.dst_node, when="SUCCESS")
    scene.addItem(plain)

    plain.set_condition("swords == 3")
    assert plain.label.scene() is scene


def test_the_label_hides_and_shows_with_its_edge(scene):
    edge = _labelled_edge(scene)
    assert edge.label.isVisible()

    edge.setVisible(False)
    assert not edge.label.isVisible()

    edge.setVisible(True)
    assert edge.label.isVisible()


def test_a_label_with_no_text_stays_hidden_when_its_edge_is_shown(scene):
    edge = _labelled_edge(scene)
    edge.set_condition(None)
    edge.setVisible(False)
    edge.setVisible(True)
    assert not edge.label.isVisible()
