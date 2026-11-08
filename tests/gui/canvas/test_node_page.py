"""The node page on its own, out of the panel. Its cards are ``tests/options/test_node_page_cards.py``."""

from __future__ import annotations

from bartlebot.bot.schema import StateModel
from bartlebot.gui.canvas.node_page import NodePage

STATE = StateModel(id="a", node_type="sleep", label="Wait")


def test_building_and_showing_the_page_never_shows_a_parentless_widget(qapp, parentless_shows):
    page = NodePage()
    page.show_state(STATE, [STATE], [], "a", "a")

    assert parentless_shows == []


def test_clear_lets_go_of_the_state_so_an_edit_no_longer_reaches_it(qapp):
    page = NodePage()
    page.show_state(STATE.model_copy(), [STATE], [], "a", "a")
    shown = page.state
    page.clear()
    page.node_label_edit.setText("Changed")

    assert page.state is None
    assert shown.label == "Wait"
