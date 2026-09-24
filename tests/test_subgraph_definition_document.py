"""Stage 5 of the subgraph-calls plan: a definition is edited as a document of its own.

The canvas half: a definition document's signature page, Promote to input, Expose on
call node, its ports, and the handoff that sets a document aside and puts it back.
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

from bartlebot.bot.schema import SUBGRAPH_EXIT, SubgraphInput
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
    assert doc.definition_crumb.isVisibleTo(doc)


def test_a_port_shows_the_signature_and_a_node_shows_its_page(doc):
    doc.scene.node_items["entry_hub"].setSelected(True)
    assert doc.param_panel.showing_signature
    doc.select_state("wisps")
    assert doc.param_panel.shown_state.id == "wisps"
    assert doc.param_panel.expose_button.isVisibleTo(doc.param_panel)


def test_a_definition_cannot_run_or_leave_edit_mode(doc):
    assert not doc.exit_edit_mode()
    assert doc.mode == BotMode.EDIT


def test_done_goes_back_and_save_and_done_saves_then_goes_back(doc):
    back, saved = [], []
    doc.definition_back_requested.connect(lambda: back.append(True))
    doc.definition_save_requested.connect(saved.append)
    doc._done_btn.click()
    doc._save_done_btn.click()
    doc.definition_crumb.save_button.click()
    assert back == [True]
    assert saved == [True, False]


def test_promote_to_a_new_input_binds_it_and_undoes(doc):
    assert doc.promote_param("wisps", "timeout", new_input=SubgraphInput(name="wait", type="float", default=30.0))
    wait = _input(doc, "wait")
    assert [(b.state, b.param) for b in wait.bindings] == [("wisps", "timeout")]
    doc.select_state("wisps")
    assert doc.param_panel.param_form.bound_chip("timeout") is not None
    doc.undo()
    assert not any(i.name == "wait" for i in _meta(doc).inputs)


def test_promote_to_an_existing_input(doc):
    assert doc.promote_param("tp_a", "zone", existing="hub")
    assert ("tp_a", "zone") in {(b.state, b.param) for b in _input(doc, "hub").bindings}


def test_unbind_stops_the_input_setting_the_param(doc):
    assert doc.unbind_param("tp_b", "zone")
    assert ("tp_b", "zone") not in {(b.state, b.param) for b in _input(doc, "hub").bindings}
    assert not doc.unbind_param("tp_b", "zone"), "nothing left to unbind"


def test_exposing_a_node_makes_a_panel_and_takes_its_params_off_inputs(doc):
    assert doc.set_node_exposed("hub", True)
    panel = next(p for p in _meta(doc).panels if p.kind == "node")
    assert panel.member == "hub"
    assert ("hub", "zone") not in {(b.state, b.param) for b in _input(doc, "hub").bindings}
    doc.select_state("hub")
    assert doc.param_panel.param_form.bound_chip("zone").text() == "exposed"
    assert doc.set_node_exposed("hub", False)
    assert not [p for p in _meta(doc).panels if p.kind == "node"]


def test_another_param_can_share_an_exposed_value(doc):
    doc.set_node_exposed("hub", True)
    panel = next(p for p in _meta(doc).panels if p.kind == "node")
    offered = doc._shareable_params("tp_a", "zone")
    assert (panel.id, "zone") in {(pid, key) for _label, pid, key in offered}
    assert doc.promote_param("tp_a", "zone", shared=(panel.id, "zone"))
    assert [(b.state, b.param) for b in panel.bindings["zone"]] == [("tp_a", "zone")]


def test_adding_and_deleting_a_port(doc):
    doc._on_add_port_requested(SUBGRAPH_EXIT)
    added = [s for s in doc.config.states if s.node_type == SUBGRAPH_EXIT and s.id not in {"exit_done", "exit_early"}]
    assert len(added) == 1
    assert any("nothing leading into it" in w for w in doc.validation_warnings())
    doc._on_delete_port_requested(added[0].id)
    assert added[0].id not in {s.id for s in doc.config.states}


def test_a_broken_signature_is_an_error(doc):
    doc.config.transitions = [t for t in doc.config.transitions if t.source != "entry_hub"]
    doc.scene.setup(doc.config)
    assert any("Entry" in line for line in doc.validation_errors())


def test_take_and_restore_keep_the_document_and_its_undo(doc, qapp):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    doc.promote_param("tp_a", "zone", existing="hub")
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
    assert canvas.definition_crumb.text == "Linked › Sync players"


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
    canvas.promote_param("tp_a", "zone", existing="hub")
    assert harness._has_unsaved_changes
    canvas._done_btn.click()
    assert not canvas.is_definition
    assert library.get(SYNC_ID).version == 1
    assert not harness._has_unsaved_changes


def test_back_can_be_cancelled(harness, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    canvas = _canvas(harness)
    canvas.open_definition_of(REGION)
    canvas.promote_param("tp_a", "zone", existing="hub")
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
