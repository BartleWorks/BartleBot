"""Stage 6 of the subgraph-calls plan: an instance on a bot's canvas is one call node.

The scene draws each instance as a single ``CallNodeItem`` at its anchor, its members
with no item of their own, and every edge into or out of it on a named port. These
tests drive a real ``BotCanvas`` over the synthetic "sync players" fixture from
``test_subgraphs``; nothing here is a real character.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPointF

from bartlebot.gui.canvas.call_node_item import CallNodeItem
from bartlebot.subgraph_store import SubgraphLibrary
from bartlebot.subgraphs import compose_bot
from tests.test_subgraphs import REGION, SYNC_ID, linked_bot, m, sync_players_definition


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """Answer any message box a test did not stub, so a missed prompt fails fast instead of blocking the run."""
    from PyQt6.QtWidgets import QMessageBox  # noqa: PLC0415

    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.No)


@pytest.fixture
def library(tmp_path) -> SubgraphLibrary:
    lib = SubgraphLibrary(tmp_path / "library")
    lib.save(sync_players_definition())
    return lib


@pytest.fixture
def canvas(qapp, library):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    c = BotCanvas()
    c.set_subgraph_library(library)
    bot, report = compose_bot(linked_bot(library.get(SYNC_ID), library), library)
    c.setup(bot)
    c.set_compose_report(report)
    c.enter_edit_mode()
    yield c
    c.prepare_for_shutdown()


def _edge(canvas, src, when, dst):
    return next(e for s, w, d, e in canvas.scene.edge_items if (s, w, d) == (src, when, dst))


def _select(canvas, item) -> None:
    canvas.scene.clearSelection()
    item.setSelected(True)


# ---------------------------------------------------------------------------
# The projection
# ---------------------------------------------------------------------------


def test_an_instance_is_one_call_node_and_its_members_have_no_item(canvas):
    items = canvas.scene.node_items
    assert isinstance(items[REGION], CallNodeItem)
    assert not any(sid.startswith(f"{REGION}_") for sid in items)
    assert set(items) == {REGION, "state_1", "state_2"}
    node = items[REGION]
    assert [name for _pid, name in node.entries] == ["Start", "Hub"]
    assert [name for _pid, name in node.exits] == ["Done", "Early"]


def test_edges_land_on_named_ports(canvas):
    done = _edge(canvas, REGION, "exit_done", "state_1")
    assert [(t.source, t.dest) for t in canvas.scene.transitions_of(done)] == [(m("wisps"), "state_1")]
    assert _edge(canvas, REGION, "exit_early", "state_2")
    into_hub = _edge(canvas, "state_1", "FAILURE", REGION)
    assert into_hub.dst_port == "entry_hub"
    assert _edge(canvas, "state_2", "FAILURE", REGION).dst_port == "entry_start"
    # The instance's own internal edges are not drawn at all.
    assert not any(s == d == REGION for s, _w, d, _e in canvas.scene.edge_items)


def test_roles_inside_the_instance_badge_its_entry_port(canvas):
    assert canvas.scene.node_items[REGION].role_ports == ("entry_start", "entry_start")


def test_wiring_into_an_entry_port_adds_the_flat_transition(canvas):
    scene = canvas.scene
    scene._create_transition(scene.node_items["state_2"], scene.node_items[REGION], "SUCCESS", dst_port="entry_hub")
    assert any(t.source == "state_2" and t.dest == m("hub") and t.when == "SUCCESS" for t in canvas.config.transitions)
    assert _edge(canvas, "state_2", "SUCCESS", REGION).dst_port == "entry_hub"
    assert canvas.can_undo()


def test_moving_the_call_node_moves_the_anchor_and_every_member(canvas):
    region = canvas.config.regions[0]
    before_anchor = dict(region.source.anchor)
    wisps_before = next(s for s in canvas.config.states if s.id == m("wisps")).position["x"]
    node = canvas.scene.node_items[REGION]
    node.setPos(node.pos() + QPointF(120, 0))
    canvas.scene.flush_positions()
    assert region.source.anchor["x"] == pytest.approx(before_anchor["x"] + 120)
    wisps = next(s for s in canvas.config.states if s.id == m("wisps"))
    assert wisps.position["x"] == pytest.approx(wisps_before + 120)


def test_deleting_the_call_node_removes_the_whole_instance(canvas):
    assert canvas.scene.remove_instance(REGION, confirm=False)
    assert not [r for r in canvas.config.regions if r.source is not None]
    assert {s.id for s in canvas.config.states} == {"state_1", "state_2"}
    assert all(t.source in {"state_1", "state_2"} for t in canvas.config.transitions)


def test_detaching_turns_members_into_ordinary_nodes(canvas):
    canvas.scene.detach_region(REGION)
    assert REGION not in canvas.scene.node_items
    assert m("wisps") in canvas.scene.node_items
    assert canvas.config.regions[0].source is None


def test_copy_paste_makes_a_second_instance(canvas):
    scene = canvas.scene
    _select(canvas, scene.node_items[REGION])
    scene._copy_selected()
    scene._paste()
    linked = [r for r in canvas.config.regions if r.source is not None]
    assert len(linked) == 2
    assert len(scene.call_nodes()) == 2


# ---------------------------------------------------------------------------
# Validation and run highlight roll up onto the call node
# ---------------------------------------------------------------------------


def test_a_member_error_rolls_up_onto_the_call_node(canvas):
    hub = next(s for s in canvas.config.states if s.id == m("hub"))
    hub.params["zone"] = ""
    canvas.scene.refresh_errors()
    errors = canvas.validation_errors()
    assert any(line.startswith("Sync players › ") and "zone" in line for line in errors)
    # Unreachable-on-its-own and uncovered-outcome are the definition's business.
    assert not any("Sync players ›" in line and "Unreachable" in line for line in canvas.validation_warnings())


def test_a_running_member_lights_its_call_node(canvas):
    canvas.exit_edit_mode()
    node = canvas.scene.node_items[REGION]
    canvas.set_state(m("wisps"))
    assert node.active_member is not None
    canvas.set_state("state_1")
    assert node.active_member is None


def test_search_finds_a_member_as_its_call_node(canvas):
    canvas._show_search_bar()
    canvas._on_search_text_changed("wisps")
    assert canvas._search_matches == [REGION]


# ---------------------------------------------------------------------------
# The call node's page
# ---------------------------------------------------------------------------


def test_selecting_the_call_node_shows_its_page(canvas):
    _select(canvas, canvas.scene.node_items[REGION])
    panel = canvas.param_panel
    assert panel.showing_call
    assert panel.call_page.region.id == REGION
    assert panel.call_page.form(None) is not None


def test_an_input_edit_reaches_the_member_and_is_undoable(canvas):
    _select(canvas, canvas.scene.node_items[REGION])
    page = canvas.param_panel.call_page
    page.form(None).set_str("hub", "Mooshu/Hub")
    canvas._undo_mgr.flush_debounce()
    region = canvas.config.regions[0]
    assert region.source.inputs["hub"] == "Mooshu/Hub"
    assert next(s for s in canvas.config.states if s.id == m("hub")).params["zone"] == "Mooshu/Hub"
    assert canvas.can_undo()


def test_choosing_an_entry_port_moves_the_initial_role(canvas):
    _select(canvas, canvas.scene.node_items[REGION])
    combo = canvas.param_panel.call_page.initial_combo
    combo.setCurrentIndex(combo.findText("Hub"))
    assert canvas.config.initial == m("hub")
    assert canvas.scene.node_items[REGION].role_ports[0] == "entry_hub"
    canvas.undo()
    assert canvas.config.initial == m("relog")


def test_open_definition_asks_for_the_subgraph_id(canvas):
    asked = []
    canvas.open_definition_requested.connect(asked.append)
    canvas.open_definition_of(REGION)
    assert asked == [SYNC_ID]


def test_open_definition_warns_when_the_library_lacks_it(canvas, library, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox  # noqa: PLC0415

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
    asked = []
    canvas.open_definition_requested.connect(asked.append)
    library.delete(SYNC_ID)
    canvas.open_definition_of(REGION)
    assert warned and not asked


def test_a_call_edge_shows_its_first_transition_without_rewiring(canvas):
    _select(canvas, _edge(canvas, REGION, "exit_done", "state_1"))
    panel = canvas.param_panel
    assert panel.shown_transition.source == m("wisps")
    assert not panel.edge_src_combo.isEnabled()
    _select(canvas, _edge(canvas, "state_1", "SUCCESS", "state_2"))
    assert panel.edge_src_combo.isEnabled()


def test_a_changed_input_can_be_reset_to_the_default(canvas):
    _select(canvas, canvas.scene.node_items[REGION])
    page = canvas.param_panel.call_page
    page.form(None).set_str("hub", "Mooshu/Hub")
    _select(canvas, canvas.scene.node_items["state_1"])
    _select(canvas, canvas.scene.node_items[REGION])
    reset = page.form(None).reset_button("hub")
    assert reset is not None
    reset.click()
    region = canvas.config.regions[0]
    assert "hub" not in region.source.inputs
    hub = next(s for s in canvas.config.states if s.id == m("hub"))
    assert hub.params["zone"] == "WizardCity/Hub"
    assert page.form(None).reset_button("hub") is None


def test_a_new_subgraph_node_follows_the_definitions_defaults(canvas):
    rid = canvas.scene.insert_subgraph_instance(SYNC_ID, QPointF(0, 600))
    region = next(r for r in canvas.config.regions if r.id == rid)
    assert region.source.inputs == {}
