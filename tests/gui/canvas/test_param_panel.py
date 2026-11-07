"""The properties panel as a stack: one page at a time, and only that page holds anything.

Each page's own behavior is tested beside its module (``test_node_page.py``,
``test_edge_page.py``, ``test_region_page.py``, ``test_call_page_cards.py``); what is
here is the panel's part: which page is up, what ``shown_*`` says, and that a page's
signal comes out of the panel.
"""

from __future__ import annotations

from bartlebot.bot.schema import RegionModel, StateModel, TransitionModel
from bartlebot.gui.canvas.param_panel import _ParamPanel


def _graph() -> tuple[list[StateModel], TransitionModel]:
    states = [StateModel(id="a", node_type="sleep", label="Wait"), StateModel(id="b", node_type="sleep", label="Again")]
    return states, TransitionModel(trigger="advance", source="a", dest="b", when="SUCCESS")


def _region() -> RegionModel:
    return RegionModel(id="r", label="Hub", color=0, members=["a", "b"])


def test_showing_every_page_never_shows_a_parentless_widget(qapp, parentless_shows):
    states, edge = _graph()
    panel = _ParamPanel()
    panel.show_state(states[0], states, [edge], "a", "a")
    panel.show_transition(edge, ["SUCCESS", "FAILURE"], [], states, [edge])
    panel.show_region(_region())
    panel.show_idle()

    assert parentless_shows == []


def test_only_the_page_on_show_holds_anything(qapp):
    states, edge = _graph()
    panel = _ParamPanel()
    region = _region()

    panel.show_state(states[0], states, [edge], "a", "a")
    assert (panel.shown_state, panel.shown_transition, panel.shown_region) == (states[0], None, None)

    panel.show_transition(edge, ["SUCCESS"], [], states, [edge])
    assert (panel.shown_state, panel.shown_transition, panel.shown_region) == (None, edge, None)

    panel.show_region(region)
    assert (panel.shown_state, panel.shown_transition, panel.shown_region) == (None, None, region)

    panel.show_idle()
    assert (panel.shown_state, panel.shown_transition, panel.shown_region) == (None, None, None)


def test_a_pages_edit_comes_out_of_the_panel(qapp):
    states, edge = _graph()
    panel = _ParamPanel()
    starting, changed = [], []
    panel.edit_starting.connect(starting.append)
    panel.properties_changed.connect(lambda: changed.append("node"))
    panel.region_property_changed.connect(lambda: changed.append("region"))

    panel.show_state(states[0], states, [edge], "a", "a")
    panel.node_page.node_label_edit.setText("Rest")
    region = _region()
    panel.show_region(region)
    panel.region_page.region_label_edit.setText("Lobby")

    assert states[0].label == "Rest" and region.label == "Lobby"
    assert starting == ["Edit params", "Edit region"]
    assert changed == ["node", "region"]
