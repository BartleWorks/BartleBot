"""The edge page on its own, out of the panel. Rewiring through it is ``test_canvas_property_edits.py``."""

from __future__ import annotations

from bartlebot.bot.schema import StateModel, TransitionModel
from bartlebot.gui.canvas.edge_page import EdgePage

STATES = [StateModel(id="a", node_type="sleep"), StateModel(id="b", node_type="sleep")]


def _edge() -> TransitionModel:
    return TransitionModel(trigger="advance", source="a", dest="b", when="SUCCESS")


def test_building_and_showing_the_page_never_shows_a_parentless_widget(qapp, parentless_shows):
    page = EdgePage()
    page.show_transition(_edge(), ["SUCCESS", "FAILURE"], [], STATES, [])

    assert parentless_shows == []


def test_clear_lets_go_of_the_transition_so_an_edit_no_longer_reaches_it(qapp):
    page = EdgePage()
    edge = _edge()
    page.show_transition(edge, ["SUCCESS", "FAILURE"], [], STATES, [edge])
    page.clear()
    page.when_combo.setCurrentIndex(page.when_combo.findText("FAILURE"))

    assert page.transition is None
    assert edge.when == "SUCCESS"
