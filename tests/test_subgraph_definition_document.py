"""Stage 5 of the subgraph-calls plan: a definition is edited as a document of its own.

The canvas half: a definition document's signature page, Link to subgraph input, Promote to
input group, its ports, and the handoff that sets a document aside and puts it back.
The controller half: the document stack -- open a definition from a bot, save it to
the library as its next version, and go back to a bot recomposed from it. Built on
the synthetic "sync players" fixture from ``test_subgraphs`` and the controller
harness from ``test_subgraph_integration``; nothing here is a real character.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMessageBox

from bartlebot.bot.schema import SUBGRAPH_EXIT, StateModel, SubgraphInput
from bartlebot.gui.canvas.bot_canvas import BotMode
from bartlebot.subgraph_store import SubgraphLibrary
from bartlebot.subgraphs import definition_to_config
from tests.test_subgraph_integration import ControllerHarness
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
def doc(qapp, library):
    """A canvas with the fixture definition open as a document, in Edit mode."""
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    c = BotCanvas()
    c.set_subgraph_library(library)
    c.setup(definition_to_config(library.get(SYNC_ID)))
    c.enter_edit_mode()
    yield c
    c.prepare_for_shutdown()


def _meta(canvas):
    return canvas.config.definition


def _input(canvas, name):
    return next(i for i in _meta(canvas).inputs if i.name == name)


# ---------------------------------------------------------------------------
# The document on the canvas
# ---------------------------------------------------------------------------


def test_nothing_selected_shows_the_signature(doc):
    assert doc.is_definition
    assert doc.param_panel.showing_signature
    assert doc.crumb_bar.isVisibleTo(doc)


def test_a_port_shows_the_signature_and_a_node_shows_its_page(doc):
    doc.scene.node_items["entry_hub"].setSelected(True)
    assert doc.param_panel.showing_signature
    doc.select_state("wisps")
    assert doc.param_panel.shown_state.id == "wisps"
    assert doc.param_panel.promote_button.isVisibleTo(doc.param_panel)


def test_a_definition_cannot_run_or_leave_edit_mode(doc):
    assert not doc.exit_edit_mode()
    assert doc.mode == BotMode.EDIT


def test_done_goes_back_and_save_and_done_saves_then_goes_back(doc):
    back, saved = [], []
    doc.definition_back_requested.connect(lambda: back.append(True))
    doc.definition_save_requested.connect(saved.append)
    doc._done_btn.click()
    doc._save_done_btn.click()
    doc.crumb_bar.save_button.click()
    assert back == [True]
    assert saved == [True, False]


def test_linking_to_a_new_input_and_undoing_it(doc):
    assert doc.link_param("wisps", "timeout", new_input=SubgraphInput(name="wait", type="float", default=30.0))
    wait = _input(doc, "wait")
    assert [(b.state, b.param) for b in wait.bindings] == [("wisps", "timeout")]
    doc.select_state("wisps")
    assert doc.param_panel.param_form.link_chip("timeout") is not None
    assert doc.param_panel.param_form.link_button("timeout") is None
    doc.undo()
    assert not any(i.name == "wait" for i in _meta(doc).inputs)


def test_linking_to_an_existing_input_takes_its_default(doc):
    _input(doc, "hub").default = "Mooshu/Hub"
    assert doc.link_param("tp_a", "zone", existing="hub")
    assert ("tp_a", "zone") in {(b.state, b.param) for b in _input(doc, "hub").bindings}
    assert next(s for s in doc.config.states if s.id == "tp_a").params["zone"] == "Mooshu/Hub"


def test_unlink_stops_the_input_setting_the_param(doc):
    assert doc.unlink_param("tp_b", "zone")
    assert ("tp_b", "zone") not in {(b.state, b.param) for b in _input(doc, "hub").bindings}
    assert not doc.unlink_param("tp_b", "zone"), "nothing left to unlink"


def test_promoting_a_node_makes_an_input_group_and_locks_its_links(doc):
    assert doc.set_node_promoted("hub", True)
    panel = next(p for p in _meta(doc).panels if p.kind == "node")
    assert panel.member == "hub"
    assert ("hub", "zone") not in {(b.state, b.param) for b in _input(doc, "hub").bindings}
    doc.select_state("hub")
    chip = doc.param_panel.param_form.link_chip("zone")
    assert chip.locked and chip.text() == panel.label
    assert not doc.unlink_param("hub", "zone"), "a promoted node's param is its group's"
    assert not doc.link_param("hub", "zone", existing="world")
    assert doc.set_node_promoted("hub", False)
    assert not [p for p in _meta(doc).panels if p.kind == "node"]


def test_another_param_can_link_to_a_promoted_nodes_param(doc):
    doc.set_node_promoted("hub", True)
    panel = next(p for p in _meta(doc).panels if p.kind == "node")
    offered = doc._shareable_params("tp_a", "zone")
    assert (panel.id, "zone") in {(pid, key) for _label, pid, key in offered}
    assert doc.link_param("tp_a", "zone", shared=(panel.id, "zone"))
    assert [(b.state, b.param) for b in panel.bindings["zone"]] == [("tp_a", "zone")]


def test_adding_and_deleting_a_port(doc):
    doc._on_add_port_requested(SUBGRAPH_EXIT)
    added = [s for s in doc.config.states if s.node_type == SUBGRAPH_EXIT and s.id not in {"exit_done", "exit_early"}]
    assert len(added) == 1
    assert any("Nothing leads into this Exit" in e for e in doc.validation_errors())
    doc._on_delete_port_requested(added[0].id)
    assert added[0].id not in {s.id for s in doc.config.states}


def test_a_broken_signature_is_an_error(doc):
    doc.config.transitions = [t for t in doc.config.transitions if t.source != "entry_hub"]
    doc.scene.setup(doc.config)
    assert any("Entry" in line for line in doc.validation_errors())


def test_take_and_restore_keep_the_document_and_its_undo(doc, qapp):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    doc.link_param("tp_a", "zone", existing="hub")
    before = doc.config
    taken = doc.take_document()
    assert not doc.can_undo(), "the next document starts a history of its own"
    doc.setup(definition_to_config(sync_players_definition()))
    doc.restore_document(taken)
    assert doc.config is before
    assert doc.can_undo()
    assert isinstance(doc, BotCanvas)


# ---------------------------------------------------------------------------
# The document stack in the controller
# ---------------------------------------------------------------------------


@pytest.fixture
def harness(qapp, library, tmp_path):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415

    (tmp_path / "bots").mkdir()
    SettingsManager.instance().set("canvas", "bot_library_path", value=str(tmp_path / "bots"))
    canvas = BotCanvas()
    canvas.set_subgraph_library(library)
    h = ControllerHarness(canvas, library)
    canvas.open_definition_requested.connect(h._open_definition)
    canvas.definition_back_requested.connect(h._definition_back)
    canvas.definition_save_requested.connect(h._on_definition_save_requested)
    path = tmp_path / "bots" / "linked.json"
    linked_bot(library.get(SYNC_ID), library).save(path)
    h._open_bot(str(path))
    canvas.enter_edit_mode()
    yield h
    canvas.prepare_for_shutdown()


def _canvas(h):
    return h._window.bot_canvas


def test_opening_a_definition_sets_the_bot_aside(harness):
    canvas = _canvas(harness)
    bot = canvas.config
    canvas.open_definition_of(REGION)
    assert canvas.is_definition
    assert len(harness._doc_stack) == 1
    assert harness._doc_stack[0].doc.config is bot
    assert canvas.crumb_bar.text == "Linked › Sync players"


def test_save_bumps_the_version_and_back_recomposes_the_bot(harness, library):
    canvas = _canvas(harness)
    canvas.open_definition_of(REGION)
    wisps = next(s for s in canvas.config.states if s.id == "wisps")
    wisps.params["timeout"] = 45
    canvas.config_changed.emit()
    assert harness._has_unsaved_changes
    assert harness._save_definition()
    assert library.get(SYNC_ID).version == 2
    assert not harness._has_unsaved_changes

    assert harness._definition_back()
    assert not canvas.is_definition
    region = next(r for r in canvas.config.regions if r.id == REGION)
    assert region.source.version == 2
    assert next(s for s in canvas.config.states if s.id == m("wisps")).params["timeout"] == 45
    assert harness._has_unsaved_changes, "the bot's snapshot moved, so it has something to save"
    assert canvas.mode == BotMode.EDIT


def test_save_is_refused_while_the_definition_has_errors(harness, library, monkeypatch):
    refused = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: refused.append(a))
    canvas = _canvas(harness)
    canvas.open_definition_of(REGION)
    canvas.config.transitions = [t for t in canvas.config.transitions if t.source != "entry_hub"]
    canvas.scene.setup(canvas.config)
    assert not harness._save_definition()
    assert refused
    assert library.get(SYNC_ID).version == 1


def test_back_with_unsaved_changes_can_discard(harness, library, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    canvas = _canvas(harness)
    canvas.open_definition_of(REGION)
    canvas.link_param("tp_a", "zone", existing="hub")
    assert harness._has_unsaved_changes
    canvas._done_btn.click()
    assert not canvas.is_definition
    assert library.get(SYNC_ID).version == 1
    assert not harness._has_unsaved_changes


def test_back_can_be_cancelled(harness, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    canvas = _canvas(harness)
    canvas.open_definition_of(REGION)
    canvas.link_param("tp_a", "zone", existing="hub")
    assert not harness._definition_back()
    assert canvas.is_definition


def test_a_definition_already_open_is_not_opened_again(harness, monkeypatch):
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a))
    canvas = _canvas(harness)
    canvas.open_definition_of(REGION)
    assert not harness._open_definition(SYNC_ID)
    assert told and len(harness._doc_stack) == 1


def test_a_definition_autosaves_under_its_own_id(harness):
    _canvas(harness).open_definition_of(REGION)
    assert harness._autosave_stem() == f"subgraph_{SYNC_ID}"


def test_saving_a_region_as_a_subgraph_opens_the_new_definition(harness, library, monkeypatch):
    monkeypatch.setattr("bartlebot.gui.canvas.bot_canvas.rekey_bot_strategy_states", lambda *a: 0)
    canvas = _canvas(harness)
    farm = next(r for r in canvas.config.regions if r.source is None)
    assert canvas.save_region_as_subgraph(farm.id, name="Farm loop")
    assert canvas.is_definition
    assert canvas.config.name == "Farm loop"
    assert library.get(canvas.config.definition.id) is not None
    bot = harness._doc_stack[0].doc.config
    assert any(r.source is not None and r.source.subgraph_id == canvas.config.definition.id for r in bot.regions)


# ---------------------------------------------------------------------------
# The library's Subgraphs folder
# ---------------------------------------------------------------------------


def test_the_library_lists_subgraphs_and_opens_one(qapp, library):
    from bartlebot.gui.canvas.bot_canvas import _BotCard, _BotLibraryPanel  # noqa: PLC0415

    panel = _BotLibraryPanel()
    panel.set_subgraph_library(library)
    panel.refresh()
    keys = [panel._cat_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(panel._cat_list.count())]
    assert panel._SUBGRAPHS_KEY in keys
    panel._cat_list.setCurrentRow(keys.index(panel._SUBGRAPHS_KEY))
    opened = []
    panel.open_subgraph_requested.connect(opened.append)
    cards = [c for c in panel.findChildren(_BotCard) if c.isVisibleTo(panel)]
    assert len(cards) == 1
    QTest.mouseClick(cards[0], Qt.MouseButton.LeftButton)
    assert opened == [SYNC_ID]
    panel.deleteLater()


def test_undo_keeps_the_node_selected(doc):
    doc.select_state("wisps")
    doc.link_param("wisps", "timeout", new_input=SubgraphInput(name="wait", type="float"))
    doc.undo()
    assert doc.param_panel.shown_state is not None and doc.param_panel.shown_state.id == "wisps"
    doc.redo()
    assert doc.param_panel.shown_state is not None and doc.param_panel.shown_state.id == "wisps"


def test_the_promote_button_turns_into_demote(doc):
    doc.select_state("hub")
    button = doc.param_panel.promote_button
    assert button.text() == "Promote to input group"
    doc.set_node_promoted("hub", True)
    assert button.text() == "Demote"
    button.click()
    assert not [p for p in _meta(doc).panels if p.kind == "node"]


def test_an_input_moves_into_and_out_of_a_group(doc):
    page = doc.param_panel.signature_page
    page._add_panel()
    group = next(p for p in _meta(doc).panels if p.kind == "inputs")
    assert group.label == "Group 1"
    page.move_input_to_group("world", group.id)
    assert _input(doc, "world").panel == group.id
    page.move_input_to_group("world", None)
    assert _input(doc, "world").panel is None
    doc.undo()
    assert _input(doc, "world").panel == group.id


def test_a_new_input_can_be_promoted_straight_into_a_group(qapp):
    from bartlebot.gui.canvas.subgraph_widgets import LinkInputDialog  # noqa: PLC0415

    dlg = LinkInputDialog(
        [], param_key="timeout", param_type="float", param_label="Timeout", current_value=30.0, groups=[("g1", "Waits")]
    )
    dlg.set_new_input("wait")
    dlg.set_group("g1")
    assert dlg.new_input().panel == "g1"
    dlg.deleteLater()


def test_every_surface_names_the_source_the_same_way():
    from bartlebot.store_roots import Origin  # noqa: PLC0415
    from bartlebot.subgraph_store import source_label  # noqa: PLC0415

    assert source_label(Origin.SHIPPED) == "Source: Built-in"
    assert source_label(Origin.SHIPPED_OVERRIDE) == "Source: Built-in (modified)"
    assert source_label(Origin.USER) == "Source: Custom"


def test_deleting_a_node_takes_the_inputs_only_it_set(doc):
    # "world" sets go_to_world alone; "hub" also sets tp_b, so it stays.
    assert doc.scene.delete_state("go_to_world", confirm=False)
    assert not any(i.name == "world" for i in _meta(doc).inputs)
    assert doc.scene.delete_state("hub", confirm=False)
    assert [(b.state, b.param) for b in _input(doc, "hub").bindings] == [("tp_b", "zone")]
    doc.undo()
    doc.undo()
    assert _input(doc, "world").bindings


def test_promoting_over_existing_links_asks_first(doc, monkeypatch):
    answers = [QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Yes]
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a) or answers.pop(0))
    doc.select_state("hub")
    doc.param_panel.promote_button.click()
    assert len(asked) == 1 and "Hub" in asked[0][2]
    assert not [p for p in _meta(doc).panels if p.kind == "node"]
    doc.param_panel.promote_button.click()
    assert [p.member for p in _meta(doc).panels if p.kind == "node"] == ["hub"]


def test_the_link_button_shows_only_on_hover(doc, qapp):
    from PyQt6.QtCore import QEvent  # noqa: PLC0415

    doc.select_state("wisps")
    form = doc.param_panel.param_form
    row = form.hover_row("timeout")
    assert not row.link_shown
    qapp.sendEvent(row, QEvent(QEvent.Type.Enter))
    assert row.link_shown
    qapp.sendEvent(row, QEvent(QEvent.Type.Leave))
    assert not row.link_shown


def test_a_linked_chip_turns_into_unlink_on_hover_but_a_promoted_one_does_not(doc, qapp):
    from PyQt6.QtCore import QEvent  # noqa: PLC0415

    doc.select_state("tp_b")
    chip = doc.param_panel.param_form.link_chip("zone")
    assert chip.text() == "Hub"
    qapp.sendEvent(chip, QEvent(QEvent.Type.Enter))
    assert chip.armed and chip.text() == "Unlink"
    chip.click()
    assert ("tp_b", "zone") not in {(b.state, b.param) for b in _input(doc, "hub").bindings}

    doc.set_node_promoted("go_to_world", True)
    doc.select_state("go_to_world")
    locked = doc.param_panel.param_form.link_chip("world")
    qapp.sendEvent(locked, QEvent(QEvent.Type.Enter))
    assert not locked.armed


def test_editing_a_linked_param_edits_its_input_and_the_other_linked_params(doc):
    doc.select_state("tp_b")
    doc.param_panel.param_form.set_str("zone", "Mooshu/Hub")
    assert _input(doc, "hub").default == "Mooshu/Hub"
    assert next(s for s in doc.config.states if s.id == "hub").params["zone"] == "Mooshu/Hub"


def test_editing_an_inputs_default_edits_every_linked_param(doc):
    _input(doc, "world").default = "Krokotopia"
    doc._on_definition_changed()
    assert next(s for s in doc.config.states if s.id == "go_to_world").params["world"] == "Krokotopia"


def test_promoting_a_node_drops_the_inputs_only_it_was_linked_to(doc):
    # "world" is linked to go_to_world alone; "hub" also to tp_b.
    assert doc.set_node_promoted("go_to_world", True)
    assert not any(i.name == "world" for i in _meta(doc).inputs)
    assert doc.set_node_promoted("hub", True)
    assert [(b.state, b.param) for b in _input(doc, "hub").bindings] == [("tp_b", "zone")]
    doc.undo()
    doc.undo()
    assert _input(doc, "world").bindings


# ---------------------------------------------------------------------------
# A zone linked with a nav_zone takes the stricter type
# ---------------------------------------------------------------------------


def _add_sync_in_zone(doc, zone="WizardCity/WC_Hub"):
    doc.config.states.append(StateModel(id="nav", node_type="sync_in_zone", params={"zone": zone}))
    doc.scene.setup(doc.config)


def test_a_zone_input_linked_with_a_nav_zone_becomes_a_nav_zone(doc):
    _add_sync_in_zone(doc)
    assert _input(doc, "hub").type == "zone"
    assert doc.link_param("nav", "zone", existing="hub")
    assert _input(doc, "hub").type == "nav_zone"
    doc.select_state("tp_b")
    widget = doc.param_panel.param_form.widget("zone")
    assert type(widget).__name__ == "_NavZoneField", "a linked zone row edits through the destination picker"
    assert doc.unlink_param("nav", "zone")
    assert _input(doc, "hub").type == "zone", "unlinking loosens it again"


def test_a_promoted_zone_linked_by_a_nav_zone_keeps_its_record_button(doc):
    from bartlebot.gui.canvas import recording  # noqa: PLC0415
    from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS  # noqa: PLC0415
    from bartlebot.subgraphs import promoted_param_type  # noqa: PLC0415

    doc.config.states.append(StateModel(id="mark", node_type="mark_location", params={"zone": ""}))
    _add_sync_in_zone(doc)
    assert "mark_location" in recording.RECORDABLE_NODES
    assert doc.set_node_promoted("mark", True)
    panel = next(p for p in _meta(doc).panels if p.member == "mark")
    assert doc.link_param("nav", "zone", shared=(panel.id, "zone"))
    assert promoted_param_type(doc.config, panel, "zone", NODE_PARAM_SCHEMAS) == "nav_zone"
    doc.select_state("mark")
    assert doc.param_panel._record_row.isVisibleTo(doc.param_panel)


def test_record_into_a_destination_picker_is_never_refused(qapp):
    from bartlebot.gui.canvas.param_panel import _DynamicParamForm  # noqa: PLC0415
    from bartlebot.nodes.param_schema import ParamField  # noqa: PLC0415

    form = _DynamicParamForm()
    form.load_schema([ParamField(key="zone", type="nav_zone", label="Zone")], {"zone": ""})
    form.set_str("zone", "Nowhere/Somewhere")
    widget = form.widget("zone")
    assert widget.value() == "Nowhere/Somewhere"
    assert widget.notice.isVisibleTo(form)
    form.set_str("zone", "WizardCity/WC_Hub")
    assert not widget.notice.isVisibleTo(form)
    form.deleteLater()


def test_a_nav_zone_that_is_not_a_destination_is_an_error(doc):
    _add_sync_in_zone(doc, "Nowhere/Somewhere")
    assert any("not a destination on the zone map" in e for e in doc.validation_errors())


# ---------------------------------------------------------------------------
# The link chip keeps one size
# ---------------------------------------------------------------------------


def test_the_link_chip_is_one_width_in_both_states(doc, qapp):
    from PyQt6.QtCore import QEvent  # noqa: PLC0415

    doc.select_state("go_to_world")
    chip = doc.param_panel.param_form.link_chip("world")
    width = chip.width()
    qapp.sendEvent(chip, QEvent(QEvent.Type.Enter))
    assert chip.text() == "Unlink" and chip.width() == width


def test_a_long_linked_name_is_elided_within_the_cap(doc):
    from bartlebot.gui.canvas.param_panel import _LINK_CHIP_MAX_W  # noqa: PLC0415

    _input(doc, "world").label = "A world name far too long to fit on any chip at all"
    doc.select_state("go_to_world")
    chip = doc.param_panel.param_form.link_chip("world")
    assert chip.width() <= _LINK_CHIP_MAX_W
    assert chip.text().endswith("…")


# ---------------------------------------------------------------------------
# Linking a nested subgraph node's input to an input of the definition around it
# ---------------------------------------------------------------------------


@pytest.fixture
def outer_doc(qapp, library):
    """A canvas with a definition open that holds a nested Sync players node, ``in1``."""
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415
    from tests.test_subgraphs import _outer_with_snapshot  # noqa: PLC0415

    outer = _outer_with_snapshot(library)
    outer.inputs = []
    library.save(outer)
    c = BotCanvas()
    c.set_subgraph_library(library)
    c.setup(definition_to_config(library.get("outer")))
    c.enter_edit_mode()
    yield c
    c.prepare_for_shutdown()


def _nested_page(canvas):
    canvas.scene.clearSelection()
    canvas.scene.node_items["in1"].setSelected(True)
    assert canvas.param_panel.showing_call
    return canvas.param_panel.call_page


def _zone_of(canvas, local: str) -> str:
    return next(s for s in canvas.config.states if s.id == f"in1_{local}").params["zone"]


def test_a_nested_nodes_inputs_offer_link_in_a_definition_and_not_in_a_bot(outer_doc, qapp, library):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415
    from bartlebot.subgraphs import compose_bot  # noqa: PLC0415

    form = _nested_page(outer_doc).form(None)
    assert form.link_button("hub") is not None and form.link_chip("hub") is None

    bot_canvas = BotCanvas()
    bot_canvas.set_subgraph_library(library)
    bot, report = compose_bot(linked_bot(library.get(SYNC_ID), library), library)
    bot_canvas.setup(bot)
    bot_canvas.set_compose_report(report)
    bot_canvas.enter_edit_mode()
    bot_canvas.scene.node_items[REGION].setSelected(True)
    assert bot_canvas.param_panel.call_page.form(None).link_button("hub") is None
    bot_canvas.prepare_for_shutdown()


def test_linking_a_nested_input_to_a_new_input_and_undoing_it(outer_doc):
    page = _nested_page(outer_doc)
    assert outer_doc.link_nested_input("in1", "hub", new_input=SubgraphInput(name="zone", label="Zone", type="zone"))
    zone = _input(outer_doc, "zone")
    assert [(b.region, b.param) for b in zone.bindings] == [("in1", "hub")]
    chip = page.form(None).link_chip("hub")
    assert chip is not None and chip.text() == "Zone"
    assert page.form(None).link_button("hub") is None
    assert not outer_doc.link_nested_input("in1", "hub", existing="zone"), "set from one place only"
    assert not outer_doc.link_nested_input("in1", "nope", existing="zone"), "not an input of that node"
    outer_doc.undo()
    assert not _meta(outer_doc).inputs


def test_the_link_button_on_a_nested_input_asks_the_canvas(outer_doc):
    asked = []
    outer_doc.param_panel.link_nested_input_requested.connect(lambda rid, name: asked.append((rid, name)))
    outer_doc.param_panel.link_nested_input_requested.disconnect(outer_doc._on_nested_link_requested)
    _nested_page(outer_doc).form(None).link_button("hub").click()
    assert asked == [("in1", "hub")]


def test_a_linked_nested_input_is_the_outer_inputs_default_both_ways(outer_doc):
    outer_doc.link_nested_input("in1", "hub", new_input=SubgraphInput(name="zone", type="zone"))
    zone = _input(outer_doc, "zone")
    region = next(r for r in outer_doc.config.regions if r.id == "in1")
    assert zone.default is None and region.source.inputs == {}

    # the default, edited on the signature page, reaches the nested node and its members
    zone.default = "Outer/Default"
    outer_doc._on_definition_changed()
    assert region.source.inputs == {"hub": "Outer/Default"}
    assert _zone_of(outer_doc, "hub") == _zone_of(outer_doc, "tp_b") == "Outer/Default"

    # and the value, edited on the nested node's own page, is the default
    _nested_page(outer_doc).form(None).set_str("hub", "Edited/OnTheNode")
    outer_doc._undo_mgr.flush_debounce()
    assert zone.default == "Edited/OnTheNode"
    assert _zone_of(outer_doc, "hub") == "Edited/OnTheNode"


def test_linking_a_nested_input_to_an_existing_input_takes_its_default(outer_doc):
    outer_doc.link_param("start", "relog_clients", new_input=SubgraphInput(name="who", type="str"))
    outer_doc.link_nested_input("in1", "hub", new_input=SubgraphInput(name="zone", type="zone", default="A/B"))
    assert _zone_of(outer_doc, "hub") == "A/B"
    assert outer_doc.link_nested_input("in1", "world", existing="who") is True
    assert ("in1", "world") in {(b.region, b.param) for b in _input(outer_doc, "who").bindings}


def test_unlinking_a_nested_input_keeps_the_value_and_the_outer_input(outer_doc):
    outer_doc.link_nested_input("in1", "hub", new_input=SubgraphInput(name="zone", type="zone", default="A/B"))
    assert outer_doc.unlink_nested_input("in1", "hub")
    assert _input(outer_doc, "zone").bindings == []
    assert _zone_of(outer_doc, "hub") == "A/B"
    assert not outer_doc.unlink_nested_input("in1", "hub"), "nothing left to unlink"


def test_the_signature_page_lists_and_unlinks_a_nested_link(outer_doc):
    outer_doc.link_nested_input("in1", "hub", new_input=SubgraphInput(name="zone", type="zone"))
    from PyQt6.QtWidgets import QLabel  # noqa: PLC0415

    outer_doc.param_panel.show_signature(outer_doc.config)
    page = outer_doc.param_panel.signature_page

    assert "linked to Inner · Hub" in [lbl.text() for lbl in page.findChildren(QLabel)]
    page._unbind("zone", None, "hub", "in1")
    assert _input(outer_doc, "zone").bindings == []


def test_a_saved_nested_link_reaches_a_bot_through_the_outer_node(outer_doc, library):
    from tests.test_subgraphs import _bot_of, _zones  # noqa: PLC0415

    outer_doc.link_nested_input("in1", "hub", new_input=SubgraphInput(name="zone", type="zone"))
    assert outer_doc.validation_errors() == []
    from bartlebot.subgraphs import config_to_definition  # noqa: PLC0415

    library.save(config_to_definition(outer_doc.config, version=2))
    assert _zones(_bot_of(library.get("outer"), library, zone="Deep/Z")) == {"tp_b": "Deep/Z", "hub": "Deep/Z"}


def test_building_a_linkable_nested_page_never_shows_a_parentless_widget(outer_doc, parentless_shows):
    outer_doc.link_nested_input("in1", "hub", new_input=SubgraphInput(name="zone", type="zone"))
    parentless_shows.clear()
    _nested_page(outer_doc)
    assert parentless_shows == []
