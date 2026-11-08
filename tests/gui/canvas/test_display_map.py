"""What each state is drawn as, without a scene: the map ``EditScene`` rebuilds on every setup.

The scene's own tests (``test_region_collapse.py``, ``test_subgraph_call_node.py``,
``test_subgraph_stepping.py``) drive this through the canvas; these hold the map's rules
on their own. The bots are the synthetic linear row and "sync players" instance those
tests use.
"""

from __future__ import annotations

from bartlebot.gui.canvas.display_map import DisplayMap
from tests.content.subgraphs.test_subgraphs import REGION, SYNC_ID, linked_bot, m
from tests.content.subgraphs.test_subgraphs import library as library  # noqa: F401 - the fixture
from tests.gui.canvas.test_region_collapse import _t, linear_bot


def _map(config, library=None) -> DisplayMap:
    display = DisplayMap()
    display.set_library(library)
    display.rebuild(config)
    return display


def test_an_instances_members_are_drawn_as_its_call_node_and_its_edges_leave_by_port(library):
    bot = linked_bot(library.get(SYNC_ID), library)
    display = _map(bot, library)

    assert display.display_id(m("wisps")) == REGION
    assert display.folded_region_of("state_1") is None
    # wisps SUCCESS feeds the Done exit, so the edge out is drawn from that port.
    out = next(t for t in bot.transitions if t.source == m("wisps") and t.dest == "state_1")
    assert display.display_key(out) == (REGION, "exit_done", "state_1", None)
    # One into the hub member lands on the Hub entry.
    into = next(t for t in bot.transitions if t.source == "state_1" and t.dest == m("hub"))
    assert display.display_key(into) == ("state_1", "FAILURE", REGION, "entry_hub")
    # An edge between two members is inside the node, and drawn as nothing.
    inside = next(t for t in bot.transitions if t.source == m("relog"))
    assert display.display_key(inside) is None


def test_a_plain_region_folds_only_while_collapsed():
    bot = linear_bot()
    assert _map(bot).folded_ids == []

    bot.regions[0].collapsed = True
    display = _map(bot)

    assert display.folded_ids == ["r1"]
    assert {display.display_id(s) for s in ("a3", "a4", "a5")} == {"r1"}
    assert display.display_key(_t("a2", "a3")) == ("a2", "SUCCESS", "r1", display.ports_of("r1").entries[0][0])


def test_a_run_mode_collapse_is_a_view_and_one_equal_to_the_file_is_not_kept():
    bot = linear_bot()
    display = _map(bot)

    assert display.set_collapse_overrides(["r1"], True)
    assert display.is_region_collapsed("r1") and not bot.regions[0].collapsed
    assert not display.set_collapse_overrides(["r1"], True), "already collapsed: nothing changed"
    assert display.set_collapse_overrides(["r1"], False)
    assert not display.forget_collapse_overrides(), "back to the saved flag, so nothing is held"


def test_a_rebuild_drops_what_names_a_region_the_bot_no_longer_folds():
    bot = linear_bot()
    display = _map(bot)
    display.set_collapse_overrides(["r1"], True)
    display.rebuild(bot)
    display.set_expanded(["r1"])

    bot.regions = []
    display.rebuild(bot)

    assert display.expanded == []
    assert not display.forget_collapse_overrides()


def test_folded_changed_sees_a_member_added_to_a_collapsed_region():
    bot = linear_bot()
    bot.regions[0].collapsed = True
    display = _map(bot)
    assert not display.folded_changed()

    bot.regions[0].members.append("a6")

    assert display.folded_changed()


def test_ports_changed_sees_an_edge_added_across_a_collapsed_region():
    bot = linear_bot()
    bot.regions[0].collapsed = True
    display = _map(bot)
    assert not display.ports_changed()

    bot.transitions.append(_t("a4", "a8", "FAILURE"))

    assert display.ports_changed()


def test_collapse_all_goes_largest_first_and_expand_all_names_the_collapsed():
    bot = linear_bot()
    display = _map(bot)

    assert display.bulk_collapse_targets(True) == ["r1"]
    assert display.bulk_collapse_targets(False) == []
    bot.regions[0].collapsed = True
    assert display.bulk_collapse_targets(False) == ["r1"]
