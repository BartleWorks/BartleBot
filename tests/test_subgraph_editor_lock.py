"""Stage 3 of #37: a linked region behaves as one unit in the editor.

Real ``BotCanvas`` tests on the synthetic seven-node "sync players" instance from
``test_subgraphs`` (nothing here is a real character). The one lock query is
``EditScene.is_locked``; every behaviour below is what reads it -- group select and
drag, the deletes it refuses, the edges it refuses, copy/paste minting a new
instance, remove-instance, detach, and the pill state surviving undo.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMenu, QMessageBox

from bartlebot.bot.schema import BotConfig
from bartlebot.gui.canvas.node_item import GRID_H
from bartlebot.gui.canvas.region_manager import RegionManager
from bartlebot.store_roots import Origin
from bartlebot.subgraph_store import SubgraphLibrary
from bartlebot.subgraphs import ComposeReport, RegionStatus, linked_regions, local_id
from tests.test_subgraphs import REGION, SYNC_ID, linked_bot, m, sync_players_definition

LOCALS = ("relog", "form_group", "tp_a", "tp_b", "go_to_world", "hub", "wisps")


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
    c.setup(linked_bot(library.get(SYNC_ID), library))
    c.enter_edit_mode()
    c.resize(800, 600)
    qapp.processEvents()
    yield c
    c.prepare_for_shutdown()


def _press(canvas, pos: QPointF, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    """A left press at scene *pos*, delivered through the view as a real click is."""
    view = canvas.view
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, modifiers, view.mapFromScene(pos))


def _release(canvas, pos: QPointF) -> None:
    view = canvas.view
    QTest.mouseRelease(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, view.mapFromScene(pos)
    )


def _key(scene, key, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    scene.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, modifiers))


def _region(config: BotConfig, rid: str = REGION):
    return next(r for r in config.regions if r.id == rid)


def _yes(monkeypatch) -> None:
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))


# ---------------------------------------------------------------------------
# The lock query
# ---------------------------------------------------------------------------


def test_members_are_locked_and_externals_are_not(canvas):
    scene = canvas.scene
    assert all(scene.is_locked(m(loc)) for loc in LOCALS)
    assert not scene.is_locked("state_1")
    assert scene.linked_region_of(m("hub")).id == REGION
    assert scene.linked_region_of("state_1") is None
    assert scene.node_items[m("hub")].is_locked
    assert not scene.node_items["state_1"].is_locked
    assert "Sync players" in scene.node_items[m("hub")].toolTip()


# ---------------------------------------------------------------------------
# Press and drag
# ---------------------------------------------------------------------------


def test_pressing_a_locked_member_selects_the_whole_instance(canvas):
    scene = canvas.scene
    node = scene.node_items[m("hub")]
    _press(canvas, node.center())
    _release(canvas, node.center())
    selected = {i.state_id for i in scene.selectedItems() if hasattr(i, "state_id")}
    assert selected == {m(loc) for loc in LOCALS}
    assert scene.pressed_member_id == m("hub")


def test_pressing_an_external_node_selects_only_it(canvas):
    scene = canvas.scene
    _press(canvas, scene.node_items["state_1"].center())
    _release(canvas, scene.node_items["state_1"].center())
    selected = {i.state_id for i in scene.selectedItems() if hasattr(i, "state_id")}
    assert selected == {"state_1"}
    assert scene.pressed_member_id is None


def test_group_drag_moves_anchor(canvas):
    scene = canvas.scene
    config = scene.config
    before = dict(_region(config).source.anchor)
    for loc in LOCALS:
        scene.node_items[m(loc)].moveBy(40.0, -30.0)
    _release(canvas, QPointF(0, 0))
    after = _region(config).source.anchor
    assert (after["x"] - before["x"], after["y"] - before["y"]) == (40.0, -30.0)
    assert canvas.can_undo()


def test_moving_one_member_leaves_the_anchor_alone(canvas):
    scene = canvas.scene
    config = scene.config
    before = dict(_region(config).source.anchor)
    scene.node_items[m("hub")].moveBy(40.0, 0.0)
    _release(canvas, QPointF(0, 0))
    assert _region(config).source.anchor == before
    assert {s.id: s for s in config.states}[m("hub")].position["x"] == 2200.0 - 880.0 + 40.0


# ---------------------------------------------------------------------------
# Deletes
# ---------------------------------------------------------------------------


def test_delete_key_skips_locked_members(canvas, monkeypatch):
    _yes(monkeypatch)
    scene = canvas.scene
    scene.clearSelection()
    scene.node_items[m("hub")].setSelected(True)
    scene.node_items["state_1"].setSelected(True)
    _key(scene, Qt.Key.Key_Delete)
    ids = {s.id for s in scene.config.states}
    assert "state_1" not in ids
    assert m("hub") in ids
    assert _region(scene.config).members == [m(loc) for loc in LOCALS]


def test_delete_state_refuses_a_locked_member(canvas):
    scene = canvas.scene
    assert scene.delete_state(m("hub"), confirm=False) is False
    assert m("hub") in {s.id for s in scene.config.states}


def test_delete_key_on_a_whole_instance_removes_it(canvas, monkeypatch):
    _yes(monkeypatch)
    scene = canvas.scene
    scene.clearSelection()
    for loc in LOCALS:
        scene.node_items[m(loc)].setSelected(True)
    _key(scene, Qt.Key.Key_Delete)
    assert not linked_regions(scene.config)
    assert not any(s.id.startswith("sg_") for s in scene.config.states)


def test_remove_instance_deletes_members_and_reassigns_roles(canvas):
    scene = canvas.scene
    assert scene.config.initial == m("relog")
    assert scene.remove_instance(REGION, confirm=False)
    config = scene.config
    assert [s.id for s in config.states] == ["state_1", "state_2"]
    assert [r.id for r in config.regions] == ["aabbccdd"]
    assert config.initial == "state_1" and config.recovery_state == "state_1"
    assert all(t.source in ("state_1", "state_2") and t.dest in ("state_1", "state_2") for t in config.transitions)
    assert REGION not in scene.region_items
    canvas.undo()
    assert m("relog") in scene.node_items
    assert scene.config.initial == m("relog")


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


def test_internal_edge_cannot_be_created_or_deleted(canvas):
    scene = canvas.scene
    n = len(scene.config.transitions)
    scene._create_transition(scene.node_items[m("relog")], scene.node_items[m("wisps")], "SUCCESS")
    assert len(scene.config.transitions) == n
    internal = next(e for s, w, d, e in scene.edge_items if (s, w, d) == (m("hub"), "SUCCESS", m("wisps")))
    assert scene._delete_edge_item(internal, confirm=False) is False
    assert len(scene.config.transitions) == n


def test_external_edge_to_member_is_allowed(canvas):
    scene = canvas.scene
    n = len(scene.config.transitions)
    scene._create_transition(scene.node_items["state_2"], scene.node_items[m("hub")], "SUCCESS")
    assert len(scene.config.transitions) == n + 1
    external = next(e for s, w, d, e in scene.edge_items if (s, w, d) == ("state_2", "SUCCESS", m("hub")))
    assert scene._delete_edge_item(external, confirm=False) is True
    assert len(scene.config.transitions) == n


# ---------------------------------------------------------------------------
# Region membership
# ---------------------------------------------------------------------------


def test_add_and_remove_from_linked_region_are_noops(qapp, library):
    from PyQt6.QtWidgets import QGraphicsScene  # noqa: PLC0415

    config = linked_bot(library.get(SYNC_ID), library)
    edits: list[str] = []
    manager = RegionManager(
        QGraphicsScene(), config, {}, edits.append, lambda: None, region_locked_fn=lambda rid: rid == REGION
    )
    manager.add_to_region(["state_1"], REGION)
    manager.remove_from_region([m("hub")], REGION)
    assert _region(config).members == [m(loc) for loc in LOCALS]
    assert edits == []
    manager.add_to_region([m("hub")], "aabbccdd")  # an ordinary region still takes a locked node
    assert m("hub") in _region(config, "aabbccdd").members
    assert edits == ["Edit Region"]


def test_locked_node_menu_has_no_delete_and_no_region_membership(canvas):
    scene = canvas.scene
    menu = QMenu()
    scene._build_node_menu(menu, scene.node_items[m("hub")], scene.node_items[m("hub")].center())
    texts = [a.text() for a in menu.actions() if a.text()]
    assert "Delete State" not in texts
    assert "Node Help" in texts
    assert not any("Sync players" in t for t in texts)
    assert any("Farm" in t for t in texts), "an ordinary region is still offered"
    menu2 = QMenu()
    scene._build_node_menu(menu2, scene.node_items["state_1"], scene.node_items["state_1"].center())
    assert "Delete State" in [a.text() for a in menu2.actions()]


def test_linked_region_menu_offers_detach_and_remove_instance(canvas):
    scene = canvas.scene
    menu = QMenu()
    scene._build_region_menu(menu, scene.region_items[REGION], QPointF(0, 0))
    texts = [a.text() for a in menu.actions() if a.text()]
    assert "Add State Here" not in texts
    assert "Rename Region" in texts and "Change Color" in texts
    assert "Detach from Library" in texts
    assert any(t.startswith("Remove Subgraph Instance") for t in texts)
    assert "Remove Region" not in texts


# ---------------------------------------------------------------------------
# Detach
# ---------------------------------------------------------------------------


def test_detach_keeps_ids_and_is_undoable(canvas):
    scene = canvas.scene
    ids_before = [s.id for s in scene.config.states]
    scene.detach_region(REGION)
    assert _region(scene.config).source is None
    assert [s.id for s in scene.config.states] == ids_before
    assert not scene.is_locked(m("hub"))
    assert not scene.node_items[m("hub")].is_locked
    assert scene.region_items[REGION].link_state is None
    canvas.undo()
    assert _region(scene.config).source is not None
    assert scene.is_locked(m("hub"))
    assert scene.node_items[m("hub")].is_locked


# ---------------------------------------------------------------------------
# Copy / paste
# ---------------------------------------------------------------------------


def _select_instance(scene) -> None:
    scene.clearSelection()
    for loc in LOCALS:
        scene.node_items[m(loc)].setSelected(True)


def test_copy_paste_linked_region_creates_new_instance_with_derived_ids(canvas):
    scene = canvas.scene
    config = scene.config
    _region(config).source.param_overrides["hub"] = {"zone": "Elsewhere/Hub"}
    _select_instance(scene)
    _key(scene, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    _key(scene, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)

    config = scene.config
    regions = linked_regions(config)
    assert len(regions) == 2
    new = next(r for r in regions if r.id != REGION)
    assert new.source.subgraph_id == SYNC_ID and new.source.version == 1
    assert new.source.param_overrides == {"hub": {"zone": "Elsewhere/Hub"}}
    assert sorted(local_id(new.id, mid) for mid in new.members) == sorted(LOCALS)
    assert not any(s.id.startswith("state_") and s.id not in ("state_1", "state_2") for s in config.states)
    # the pasted copy sits one grid row down, anchor and members alike
    by_id = {s.id: s for s in config.states}
    dy = by_id[m("hub", new.id)].position["y"] - by_id[m("hub")].position["y"]
    assert dy == new.source.anchor["y"] - _region(config).source.anchor["y"] and dy >= GRID_H
    assert by_id[m("hub", new.id)].params["zone"] == "Elsewhere/Hub"
    # internal edges came from the composition; nothing links the two instances
    internal = [t for t in config.transitions if t.source in new.members and t.dest in new.members]
    assert len(internal) == 12
    assert not any((t.source in new.members) != (t.dest in new.members) for t in config.transitions)
    assert all(scene.is_locked(mid) for mid in new.members)
    assert scene.region_items[new.id].link_state == "linked"
    assert {i.state_id for i in scene.selectedItems() if hasattr(i, "state_id")} == set(new.members)


def test_paste_composes_from_the_library_not_the_clipboard(canvas, library):
    """The pasted instance follows the library: a member added since the copy is there."""
    from bartlebot.bot.schema import StateModel  # noqa: PLC0415
    from tests.test_subgraphs import _t  # noqa: PLC0415

    scene = canvas.scene
    _select_instance(scene)
    _key(scene, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    defn = sync_players_definition(version=2)
    defn.states.append(StateModel(id="rest", node_type="handle_potions", position={"x": 3080.0, "y": 0.0}, params={}))
    defn.transitions.append(_t("wisps", "rest"))
    library.save(defn)
    _key(scene, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    new = next(r for r in linked_regions(scene.config) if r.id != REGION)
    assert new.source.version == 2
    assert m("rest", new.id) in new.members
    by_id = {s.id: s for s in scene.config.states}
    assert by_id[m("rest", new.id)].position == {
        "x": new.source.anchor["x"] + 3080.0,
        "y": new.source.anchor["y"] + 0.0,
    }


def test_paste_without_definition_uses_clipboard_snapshot(canvas, library):
    scene = canvas.scene
    _select_instance(scene)
    _key(scene, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    library.delete(SYNC_ID)
    _key(scene, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    new = next(r for r in linked_regions(scene.config) if r.id != REGION)
    assert sorted(local_id(new.id, mid) for mid in new.members) == sorted(LOCALS)
    by_id = {s.id: s for s in scene.config.states}
    assert by_id[m("go_to_world", new.id)].params["world"] == "WizardCity"
    internal = [t for t in scene.config.transitions if t.source in new.members and t.dest in new.members]
    assert len(internal) == 12


def test_partial_selection_pastes_ordinary_states(canvas):
    scene = canvas.scene
    scene.clearSelection()
    scene.node_items[m("hub")].setSelected(True)
    scene.node_items[m("wisps")].setSelected(True)
    _key(scene, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    _key(scene, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert len(linked_regions(scene.config)) == 1
    new_ids = [s.id for s in scene.config.states if s.id.startswith("state_") and s.id not in ("state_1", "state_2")]
    assert len(new_ids) == 2
    assert not any(scene.is_locked(sid) for sid in new_ids)


# ---------------------------------------------------------------------------
# Pill state
# ---------------------------------------------------------------------------


def test_pill_state_follows_the_report_and_survives_undo(canvas):
    scene = canvas.scene
    assert scene.region_items[REGION].link_state == "linked"
    assert scene.region_items["aabbccdd"].link_state is None
    held = ComposeReport({REGION: RegionStatus(Origin.USER, "held", ["the update removes X"], 2)})
    canvas.set_compose_report(held)
    assert scene.region_items[REGION].link_state == "outdated"
    scene.detach_region(REGION)
    assert scene.region_items[REGION].link_state is None
    canvas.undo()
    assert scene.region_items[REGION].link_state == "outdated"
    canvas.set_compose_report(ComposeReport({REGION: RegionStatus(Origin.MISSING, "missing", ["gone"])}))
    assert scene.region_items[REGION].link_state == "missing"


def test_focus_mode_on_linked_region(canvas):
    canvas.enter_focus_mode(REGION)
    scene = canvas.scene
    assert canvas.focus_region_id == REGION
    assert all(scene.node_items[m(loc)].isVisible() for loc in LOCALS)
    assert not scene.node_items["state_1"].isVisible()
    canvas.exit_focus_mode()
    assert scene.node_items["state_1"].isVisible()
