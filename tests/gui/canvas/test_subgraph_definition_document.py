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
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary
from bartlebot.content.subgraphs.subgraph_tags import SUBGRAPH_CATEGORIES
from bartlebot.content.subgraphs.subgraphs import definition_to_config
from bartlebot.gui.canvas.bot_canvas import BotMode
from tests.content.subgraphs.test_subgraph_integration import ControllerHarness
from tests.content.subgraphs.test_subgraphs import REGION, SYNC_ID, linked_bot, m, sync_players_definition


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


def test_saving_an_unchanged_definition_keeps_its_version(harness, library, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a))
    canvas = _canvas(harness)
    canvas.open_definition_of(REGION)
    written = []
    monkeypatch.setattr(library, "save", written.append)
    assert harness._save_definition()
    assert not written
    assert library.get(SYNC_ID).version == 1
    assert canvas.config.definition.version == 1
    assert not harness._has_unsaved_changes
    assert not asked, "nothing to save, so a built-in one is not offered a copy"


def test_saving_a_moved_node_writes_it_and_keeps_the_version(harness, library):
    canvas = _canvas(harness)
    canvas.open_definition_of(REGION)
    wisps = next(s for s in canvas.config.states if s.id == "wisps")
    wisps.position = {"x": -40.0, "y": 999.0}
    canvas.config_changed.emit()
    assert harness._save_definition()
    stored = library.get(SYNC_ID)
    assert stored.version == 1
    assert next(s for s in stored.states if s.id == "wisps").position == {"x": -40.0, "y": 999.0}
    assert not harness._has_unsaved_changes


def test_an_edit_undone_before_saving_keeps_the_version(harness, library):
    canvas = _canvas(harness)
    canvas.open_definition_of(REGION)
    wisps = next(s for s in canvas.config.states if s.id == "wisps")
    before = wisps.params.get("timeout")
    wisps.params["timeout"] = 45
    canvas.config_changed.emit()
    wisps.params["timeout"] = before
    canvas.config_changed.emit()
    assert harness._save_definition()
    assert library.get(SYNC_ID).version == 1


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


def _subgraph_cards(panel):
    from bartlebot.gui.canvas.bot_canvas import _BotCard  # noqa: PLC0415

    return [c for c in panel.findChildren(_BotCard) if c.isVisibleTo(panel)]


def _show_subgraphs(panel):
    keys = [panel._cat_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(panel._cat_list.count())]
    panel._cat_list.setCurrentRow(keys.index(panel._SUBGRAPHS_KEY))


def test_a_right_click_on_a_subgraph_card_does_not_open_it(qapp, library):
    from bartlebot.gui.canvas.bot_canvas import _BotLibraryPanel  # noqa: PLC0415

    panel = _BotLibraryPanel()
    panel.set_subgraph_library(library)
    panel.refresh()
    _show_subgraphs(panel)
    opened = []
    panel.open_subgraph_requested.connect(opened.append)
    QTest.mouseClick(_subgraph_cards(panel)[0], Qt.MouseButton.RightButton)
    assert opened == []
    panel.deleteLater()


def test_a_subgraph_cards_menu_deletes_reverts_or_explains_by_origin(qapp, tmp_path):
    from bartlebot.gui.canvas.bot_canvas import _BotLibraryPanel  # noqa: PLC0415

    shipped, user = tmp_path / "shipped", tmp_path / "user"
    lib = SubgraphLibrary(shipped, user)
    sync_players_definition().save(shipped / "subgraphs" / "sync-players.json")
    lib.reload()
    panel = _BotLibraryPanel()
    panel.set_subgraph_library(lib)
    asked = []
    panel.delete_subgraph_requested.connect(asked.append)

    built_in = panel.subgraph_menu(SYNC_ID).actions()
    assert [a.text() for a in built_in] == ["Rename…", "Delete Subgraph…", "", "Export…"], "Export after a rule"
    assert not built_in[1].isEnabled() and "can't be deleted" in built_in[1].toolTip()

    lib.save(sync_players_definition(version=2))
    modified = panel.subgraph_menu(SYNC_ID).actions()[1]
    assert modified.text() == "Revert to Official…" and modified.isEnabled()

    custom = sync_players_definition().model_copy(update={"id": "mine", "name": "Mine"})
    lib.save(custom)
    delete = panel.subgraph_menu("mine").actions()[1]
    assert delete.text() == "Delete Subgraph…" and delete.isEnabled()
    delete.trigger()
    assert asked == ["mine"]
    panel.deleteLater()


def test_a_subgraph_cards_rename_is_offered_on_the_users_own_only(qapp, tmp_path):
    from bartlebot.gui.canvas.bot_canvas import _BotLibraryPanel  # noqa: PLC0415

    shipped, user = tmp_path / "shipped", tmp_path / "user"
    lib = SubgraphLibrary(shipped, user)
    sync_players_definition().save(shipped / "subgraphs" / "sync-players.json")
    lib.reload()
    panel = _BotLibraryPanel()
    panel.set_subgraph_library(lib)
    asked = []
    panel.rename_subgraph_requested.connect(asked.append)

    rename = panel.subgraph_menu(SYNC_ID).actions()[0]
    assert rename.text() == "Rename…"
    assert not rename.isEnabled() and "can't be renamed" in rename.toolTip()
    lib.save(sync_players_definition(version=2))
    assert not panel.subgraph_menu(SYNC_ID).actions()[0].isEnabled(), "a modified built-in is still built in"

    lib.save(sync_players_definition().model_copy(update={"id": "mine", "name": "Mine"}))
    rename = panel.subgraph_menu("mine").actions()[0]
    assert rename.isEnabled()
    rename.trigger()
    assert asked == ["mine"]
    panel.deleteLater()


def _answer_meta_dialog(monkeypatch, name, description=None, categories=None):
    """Stand in for the name and description dialog: accept it with these, or cancel it if ``name`` is None."""
    from bartlebot.gui.canvas import bot_canvas, subgraph_widgets  # noqa: PLC0415

    shown = []

    class Answer:
        def __init__(self, parent=None, **kwargs):
            shown.append(kwargs)
            self.name, self.description = name, description
            self.categories = categories if kwargs.get("categories") is not None else None

        def exec(self):
            return name is not None

    monkeypatch.setattr(subgraph_widgets, "SubgraphMetaDialog", Answer)
    monkeypatch.setattr(bot_canvas, "SubgraphMetaDialog", Answer)
    return shown


def test_saving_a_region_asks_for_its_categories(harness, library, monkeypatch):
    monkeypatch.setattr("bartlebot.gui.canvas.bot_canvas.rekey_bot_strategy_states", lambda *a: 0)
    shown = _answer_meta_dialog(monkeypatch, "Farm loop", categories=["Combat and Healing", "Utility"])
    canvas = _canvas(harness)
    farm = next(r for r in canvas.config.regions if r.source is None)

    assert canvas.save_region_as_subgraph(farm.id)

    assert shown[0]["categories"] == [], "a new definition starts with none"
    assert library.get(canvas.config.definition.id).tags == ["Combat and Healing", "Utility"]
    assert canvas.config.definition.tags == ["Combat and Healing", "Utility"], "the open definition's header shows them"


def test_save_as_new_starts_from_the_originals_categories(harness, library, monkeypatch):
    library.save(library.get(SYNC_ID).model_copy(update={"tags": ["Team"]}))
    canvas = harness._window.bot_canvas
    canvas.open_definition_of(REGION)
    shown = _answer_meta_dialog(monkeypatch, "Sync, then fight", categories=["Team", "Combat and Healing"])

    assert harness._save_definition_as()

    assert shown[0]["categories"] == ["Team"]
    assert library.get(canvas.config.definition.id).tags == ["Team", "Combat and Healing"]
    assert library.get(SYNC_ID).tags == ["Team"], "the original keeps its own"


def test_renaming_from_the_library_asks_for_no_categories(harness, library, monkeypatch):
    shown = _answer_meta_dialog(monkeypatch, "Regroup")
    _canvas(harness)

    assert harness._rename_subgraph(SYNC_ID)

    assert "categories" not in shown[0]


def test_the_meta_dialog_offers_categories_only_when_asked(qapp, parentless_shows):
    from bartlebot.gui.canvas.subgraph_widgets import SubgraphMetaDialog  # noqa: PLC0415

    plain = SubgraphMetaDialog(name="Regroup")
    asked = SubgraphMetaDialog(name="Regroup", categories=["Team"])

    assert plain.categories is None and plain.categories_field is None
    assert asked.categories == ["Team"]
    assert asked.categories_field.vocabulary.groups == {"Category": SUBGRAPH_CATEGORIES}
    assert parentless_shows == []


def test_renaming_a_subgraph_keeps_its_version_and_offers_no_update(harness, library, monkeypatch, tmp_path):
    shown = _answer_meta_dialog(monkeypatch, "Regroup", "Everyone in one place.")
    canvas = _canvas(harness)
    bot = canvas.config
    assert harness._rename_subgraph(SYNC_ID)
    assert shown[0]["name"] == "Sync players", "the dialog starts from the current name"
    stored = library.get(SYNC_ID)
    assert (stored.name, stored.description, stored.version) == ("Regroup", "Everyone in one place.", 1)
    assert [p.name for p in (tmp_path / "library" / "subgraphs").glob("*.json")] == ["regroup.json"]
    composed, report = harness._compose(bot)
    assert report.regions[REGION].kind == "linked"
    assert composed.model_dump() == bot.model_dump(), "the bot links the id, and its label stays its own"


def test_renaming_a_subgraph_can_be_cancelled(harness, library, monkeypatch):
    _answer_meta_dialog(monkeypatch, None)
    assert not harness._rename_subgraph(SYNC_ID)
    assert library.get(SYNC_ID).name == "Sync players"


def test_an_open_definition_is_renamed_in_its_header_not_its_card(harness, library, monkeypatch):
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a))
    shown = _answer_meta_dialog(monkeypatch, "Regroup")
    _canvas(harness).open_definition_of(REGION)
    assert not harness._rename_subgraph(SYNC_ID)
    assert told and not shown
    assert library.get(SYNC_ID).name == "Sync players"


def test_a_built_in_subgraph_is_not_renamed(qapp, tmp_path, monkeypatch):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    shipped, user = tmp_path / "shipped", tmp_path / "user"
    sync_players_definition().save(shipped / "subgraphs" / "sync-players.json")
    lib = SubgraphLibrary(shipped, user)
    shown = _answer_meta_dialog(monkeypatch, "Regroup")
    canvas = BotCanvas()
    h = ControllerHarness(canvas, lib)
    assert not h._rename_subgraph(SYNC_ID)
    lib.save(sync_players_definition(version=2))
    assert not h._rename_subgraph(SYNC_ID), "nor a modified one"
    assert not shown and lib.get(SYNC_ID).name == "Sync players"
    canvas.prepare_for_shutdown()


def test_renaming_in_the_header_saves_at_the_same_version(harness, library):
    canvas = _canvas(harness)
    canvas.open_definition_of(REGION)
    assert canvas.header.name_editable
    canvas.config.name = "Regroup"
    canvas.config_changed.emit()
    assert harness._save_definition()
    stored = library.get(SYNC_ID)
    assert (stored.name, stored.version) == ("Regroup", 1)
    assert harness._definition_back()
    region = next(r for r in canvas.config.regions if r.id == REGION)
    assert region.label == "Sync players", "an instance keeps the label it was placed with"
    assert region.source.version == 1


def test_a_built_in_definitions_name_stays_a_label_in_edit_mode(doc):
    header = doc.header
    doc.set_definition_context(["Farm"], can_rename=False)
    assert not header.name_editable
    assert "can't be renamed" in header.name_tooltip
    doc.set_definition_context(["Farm"], can_rename=True)
    assert header.name_editable


def test_deleting_a_subgraph_says_who_links_it_and_leaves_them_their_snapshot(harness, library, monkeypatch):
    from tests.content.subgraphs.test_subgraphs import _outer_with_snapshot  # noqa: PLC0415

    library.save(_outer_with_snapshot(library).model_copy(update={"name": "Boss outer"}))
    asked = []

    def answer(*a, **k):
        asked.append(a[2])
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "question", answer)
    canvas = _canvas(harness)
    bot = canvas.config
    assert harness._delete_subgraph(SYNC_ID)
    assert "1 bot and the subgraph 'Boss outer'" in asked[0]
    assert library.get(SYNC_ID) is None
    assert canvas.config is bot, "the open bot keeps its snapshot"
    assert canvas.compose_report.regions[REGION].kind == "missing"


def test_deleting_a_subgraph_can_be_cancelled(harness, library, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    assert not harness._delete_subgraph(SYNC_ID)
    assert library.get(SYNC_ID) is not None


def test_an_open_definition_is_not_deleted(harness, library, monkeypatch):
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Ok)
    _canvas(harness).open_definition_of(REGION)
    assert not harness._delete_subgraph(SYNC_ID)
    assert told and library.get(SYNC_ID) is not None


def test_deleting_a_modified_built_in_reverts_it(qapp, tmp_path, monkeypatch):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    shipped, user = tmp_path / "shipped", tmp_path / "user"
    sync_players_definition().save(shipped / "subgraphs" / "sync-players.json")
    lib = SubgraphLibrary(shipped, user)
    lib.save(sync_players_definition(version=2))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Ok)
    canvas = BotCanvas()
    h = ControllerHarness(canvas, lib)
    assert h._delete_subgraph(SYNC_ID)
    assert lib.get(SYNC_ID).version == 1
    assert not h._delete_subgraph(SYNC_ID), "an unedited built-in has nothing to delete"
    assert lib.get(SYNC_ID) is not None
    canvas.prepare_for_shutdown()


def test_the_library_stays_in_the_subgraphs_folder_after_a_delete(qapp, library):
    from bartlebot.gui.canvas.bot_canvas import _BotLibraryPanel  # noqa: PLC0415

    library.save(sync_players_definition().model_copy(update={"id": "mine", "name": "Mine"}))
    panel = _BotLibraryPanel()
    panel.set_subgraph_library(library)
    panel.refresh()
    _show_subgraphs(panel)
    assert len(_subgraph_cards(panel)) == 2
    library.delete("mine")
    panel.refresh(keep_category=True)
    assert panel._cat_list.currentItem().data(Qt.ItemDataRole.UserRole) == panel._SUBGRAPHS_KEY
    assert len(_subgraph_cards(panel)) == 1
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
    assert button.toolTip().startswith("Promote to input group")
    doc.set_node_promoted("hub", True)
    assert button.toolTip().startswith("Demote")
    assert doc.param_panel.node_cards["params"].summary_parts[0].text == "Promoted"
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
    from bartlebot.content.store_roots import Origin  # noqa: PLC0415
    from bartlebot.content.subgraphs.subgraph_store import source_label  # noqa: PLC0415

    assert source_label(Origin.SHIPPED) == "Source: Official"
    assert source_label(Origin.SHIPPED_OVERRIDE) == "Source: Official (modified)"
    assert source_label(Origin.USER) == "Source: Custom"


def test_deleting_a_node_takes_the_inputs_only_it_set(doc):
    # "world" sets gate alone; "hub" also sets tp_b, so it stays.
    assert doc.scene.delete_state("gate", confirm=False)
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

    doc.set_node_promoted("gate", True)
    doc.select_state("gate")
    locked = doc.param_panel.param_form.link_chip("zone")
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
    assert next(s for s in doc.config.states if s.id == "gate").params["zone"] == "Krokotopia"


def test_promoting_a_node_drops_the_inputs_only_it_was_linked_to(doc):
    # "world" is linked to gate alone; "hub" also to tp_b.
    assert doc.set_node_promoted("gate", True)
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
    from bartlebot.content.subgraphs.subgraphs import promoted_param_type  # noqa: PLC0415
    from bartlebot.gui.canvas import recording  # noqa: PLC0415
    from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS  # noqa: PLC0415

    doc.config.states.append(StateModel(id="mark", node_type="mark_location", params={"zone": ""}))
    _add_sync_in_zone(doc)
    assert "mark_location" in recording.RECORDABLE_NODES
    assert doc.set_node_promoted("mark", True)
    panel = next(p for p in _meta(doc).panels if p.member == "mark")
    assert doc.link_param("nav", "zone", shared=(panel.id, "zone"))
    assert promoted_param_type(doc.config, panel, "zone", NODE_PARAM_SCHEMAS) == "nav_zone"
    doc.select_state("mark")
    assert doc.param_panel._record_btn.isVisibleTo(doc.param_panel)


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

    doc.select_state("gate")
    chip = doc.param_panel.param_form.link_chip("zone")
    width = chip.width()
    qapp.sendEvent(chip, QEvent(QEvent.Type.Enter))
    assert chip.text() == "Unlink" and chip.width() == width


def test_a_long_linked_name_is_elided_within_the_cap(doc):
    from bartlebot.gui.canvas.param_panel import _LINK_CHIP_MAX_W  # noqa: PLC0415

    _input(doc, "world").label = "A world name far too long to fit on any chip at all"
    doc.select_state("gate")
    chip = doc.param_panel.param_form.link_chip("zone")
    assert chip.width() <= _LINK_CHIP_MAX_W
    assert chip.text().endswith("…")


# ---------------------------------------------------------------------------
# Linking a nested subgraph node's input to an input of the definition around it
# ---------------------------------------------------------------------------


@pytest.fixture
def outer_doc(qapp, library):
    """A canvas with a definition open that holds a nested Sync players node, ``in1``."""
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415
    from tests.content.subgraphs.test_subgraphs import _outer_with_snapshot  # noqa: PLC0415

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
    from bartlebot.content.subgraphs.subgraphs import compose_bot  # noqa: PLC0415
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

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
    from bartlebot.gui.widgets.buttons.link_button import LinkButton  # noqa: PLC0415

    outer_doc.param_panel.show_signature(outer_doc.config)
    page = outer_doc.param_panel.signature_page
    page.select("i:zone")

    assert "Inner · Hub" in [b.text() for b in page.item("i:zone").detail.findChildren(LinkButton)]
    page._unbind("zone", None, "hub", "in1")
    assert _input(outer_doc, "zone").bindings == []


def test_a_saved_nested_link_reaches_a_bot_through_the_outer_node(outer_doc, library):
    from tests.content.subgraphs.test_subgraphs import _bot_of, _zones  # noqa: PLC0415

    outer_doc.link_nested_input("in1", "hub", new_input=SubgraphInput(name="zone", type="zone"))
    assert outer_doc.validation_errors() == []
    from bartlebot.content.subgraphs.subgraphs import config_to_definition  # noqa: PLC0415

    library.save(config_to_definition(outer_doc.config, version=2))
    assert _zones(_bot_of(library.get("outer"), library, zone="Deep/Z")) == {"tp_b": "Deep/Z", "hub": "Deep/Z"}


def test_building_a_linkable_nested_page_never_shows_a_parentless_widget(outer_doc, parentless_shows):
    outer_doc.link_nested_input("in1", "hub", new_input=SubgraphInput(name="zone", type="zone"))
    parentless_shows.clear()
    _nested_page(outer_doc)
    assert parentless_shows == []


# ---------------------------------------------------------------------------
# ...and one row of a promoted node's input group on the nested node
# ---------------------------------------------------------------------------


@pytest.fixture
def promoted_doc(qapp, library):
    """As ``outer_doc``, with the nested node's ``tp_a`` promoted whole in its own definition."""
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415
    from tests.content.subgraphs.test_subgraphs import _outer_linking_the_row  # noqa: PLC0415

    outer = _outer_linking_the_row(library)
    outer.inputs = []
    library.save(outer)
    c = BotCanvas()
    c.set_subgraph_library(library)
    c.setup(definition_to_config(library.get("outer")))
    c.enter_edit_mode()
    yield c
    c.prepare_for_shutdown()


def test_a_promoted_row_on_a_nested_node_links_by_its_stored_key(promoted_doc):
    asked = []
    promoted_doc.param_panel.link_nested_input_requested.disconnect(promoted_doc._on_nested_link_requested)
    promoted_doc.param_panel.link_nested_input_requested.connect(lambda rid, name: asked.append((rid, name)))
    form = _nested_page(promoted_doc).form("node_tp_a")
    form.link_button("zone").click()
    assert asked == [("in1", "tp_a.zone")]

    assert promoted_doc.link_nested_input("in1", "tp_a.zone", new_input=SubgraphInput(name="first", type="zone"))
    assert [(b.region, b.param) for b in _input(promoted_doc, "first").bindings] == [("in1", "tp_a.zone")]
    form = promoted_doc.param_panel.call_page.form("node_tp_a")
    assert form.link_chip("zone") is not None and form.link_button("zone") is None
    assert not promoted_doc.link_nested_input("in1", "tp_b.zone", existing="first"), "tp_b is not promoted"
    assert promoted_doc.validation_errors() == []


def test_a_linked_promoted_row_is_the_outer_inputs_default_both_ways(promoted_doc):
    promoted_doc.link_nested_input("in1", "tp_a.zone", new_input=SubgraphInput(name="first", type="zone"))
    first = _input(promoted_doc, "first")
    assert first.default is None, "unset: the row keeps the member's own value"
    assert _zone_of(promoted_doc, "tp_a") == "DragonSpyre/DS_Hub"

    first.default = "Outer/Default"
    promoted_doc._on_definition_changed()
    assert _zone_of(promoted_doc, "tp_a") == "Outer/Default"

    _nested_page(promoted_doc).form("node_tp_a").set_str("zone", "Edited/OnTheRow")
    promoted_doc._undo_mgr.flush_debounce()
    assert first.default == "Edited/OnTheRow"

    promoted_doc.param_panel.call_page.form("node_tp_a").link_chip("zone").click()
    assert first.bindings == []
    assert _zone_of(promoted_doc, "tp_a") == "Edited/OnTheRow"


def test_the_signature_page_names_a_promoted_row_by_its_group(promoted_doc):
    from bartlebot.gui.widgets.buttons.link_button import LinkButton  # noqa: PLC0415

    promoted_doc.link_nested_input("in1", "tp_a.zone", new_input=SubgraphInput(name="first", type="zone"))
    promoted_doc.param_panel.show_signature(promoted_doc.config)
    page = promoted_doc.param_panel.signature_page
    page.select("i:first")
    texts = [b.text() for b in page.item("i:first").detail.findChildren(LinkButton)]
    assert any(t.startswith("Inner \u00b7 First hop \u203a ") for t in texts)


def test_the_link_dialog_suggests_the_rows_own_key_as_the_new_inputs_name(promoted_doc, monkeypatch):
    from bartlebot.gui.canvas import bot_canvas  # noqa: PLC0415

    seen = {}

    class _Dialog:
        choice = None

        def __init__(self, inputs, **kw):
            seen.update(kw)

        def exec(self):
            return True

        def new_input(self):
            return SubgraphInput(name=seen["param_key"], type=seen["param_type"])

    monkeypatch.setattr(bot_canvas, "LinkInputDialog", _Dialog)
    promoted_doc._on_nested_link_requested("in1", "tp_a.zone")
    assert seen["param_key"] == "zone", "an input's name cannot hold a dot"
    assert [(b.region, b.param) for b in _input(promoted_doc, "zone").bindings] == [("in1", "tp_a.zone")]


def test_a_linked_row_left_blank_is_not_marked_as_a_required_input_left_empty(promoted_doc):
    """The input it is linked to asks for the value, so the nested node has nothing to fill in."""
    region = next(r for r in promoted_doc.config.regions if r.id == "in1")
    page = _nested_page(promoted_doc)
    region.source.inputs["tp_a.zone"] = ""
    promoted_doc._on_region_property_changed()
    promoted_doc._show_call_page(region)
    assert page.to_fill("node_tp_a") == 1, "blank and unlinked: this node's own gap"

    promoted_doc.link_nested_input("in1", "tp_a.zone", new_input=SubgraphInput(name="first", type="zone"))
    assert _zone_of(promoted_doc, "tp_a") == ""
    assert page.to_fill("node_tp_a") == 0
    assert promoted_doc.validation_errors() == []

    promoted_doc.unlink_nested_input("in1", "tp_a.zone")
    assert page.to_fill("node_tp_a") == 1
    assert any("Required param 'zone' is empty" in e for e in promoted_doc.validation_errors())


# ---------------------------------------------------------------------------
# The signature page: a list of inputs with a detail editor (#155)
# ---------------------------------------------------------------------------


def _page(doc):
    doc.param_panel.show_signature(doc.config)
    return doc.param_panel.signature_page


def _labels(page) -> list[str]:
    return [("  " if line.row.member else "") + line.label_text for line in page.lines]


def _menu_action(menu, text):
    for action in menu.actions():
        if action.text().split("\t")[0] == text:
            return action
        if action.menu() is not None:
            found = _menu_action(action.menu(), text)
            if found is not None:
                return found
    return None


def test_the_inputs_are_one_line_each_and_a_line_opens_its_editor_in_place(doc):
    page = _page(doc)
    assert _labels(page) == ["World", "Hub"]
    assert page.lines[1].meta_text.endswith("links"), "hub sets more than one param"

    page.select("i:hub")
    assert page.item("i:hub").detail is not None
    page.select("i:world")
    assert page.item("i:hub").detail is None, "only one editor is open at a time"
    assert page.item("i:world").detail is not None


def test_typing_a_label_renames_the_line_without_rebuilding_it(doc):
    from PyQt6.QtWidgets import QLineEdit  # noqa: PLC0415

    page = _page(doc)
    page.select("i:hub")
    line = page.item("i:hub")
    label = line.detail.findChildren(QLineEdit)[0]

    label.setText("Hub zone")

    assert page.item("i:hub") is line, "a typed edit leaves the page alone"
    assert line.label_text == "Hub zone"
    assert _input(doc, "hub").label == "Hub zone"


def test_moving_an_input_past_a_group_heading_puts_it_in_the_group(doc):
    page = _page(doc)
    page._add_panel()
    group = next(p for p in _meta(doc).panels if p.kind == "inputs")
    assert _labels(page) == ["World", "Hub", "Group 1"]

    page.item("i:hub").route[0](1)

    assert _input(doc, "hub").panel == group.id
    assert _labels(page) == ["World", "Group 1", "  Hub"]
    doc.undo()
    assert _input(doc, "hub").panel is None


def test_alt_down_in_the_tree_moves_the_line_the_keyboard_is_on(doc):
    page = _page(doc)
    QTest.keyClick(page.tree, Qt.Key.Key_Down)  # onto the first line
    QTest.keyClick(page.tree, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)

    assert [i.name for i in _meta(doc).inputs] == ["hub", "world"]
    assert _labels(page) == ["Hub", "World"]


def test_enter_in_the_tree_opens_the_line_the_keyboard_is_on(doc):
    page = _page(doc)
    QTest.keyClick(page.tree, Qt.Key.Key_Down)
    QTest.keyClick(page.tree, Qt.Key.Key_Down)
    QTest.keyClick(page.tree, Qt.Key.Key_Return)
    assert page.selected == "i:hub"
    QTest.keyClick(page.tree, Qt.Key.Key_Space)
    assert page.selected is None


def test_an_input_cannot_go_under_a_promoted_node(doc):
    assert doc.set_node_promoted("wisps", True)
    page = _page(doc)
    assert [line.row.kind for line in page.lines] == ["input", "input", "node"]

    assert not page.item("i:hub").route[1](1), "only a promoted node is below hub"
    move_down = _menu_action(page.item_menu("i:hub"), "Move down")
    assert not move_down.isEnabled()


def test_a_promoted_node_is_its_heading_alone_and_its_editor_demotes_it(doc):
    assert doc.set_node_promoted("wisps", True)
    page = _page(doc)
    node = next(p for p in _meta(doc).panels if p.kind == "node")
    page.select(f"g:{node.id}")
    assert page.item(f"g:{node.id}").meta_text.endswith("params · promoted node")

    _menu_action(page.item_menu(f"g:{node.id}"), "Demote").trigger()

    assert not any(p.kind == "node" for p in _meta(doc).panels)


def test_move_to_group_in_the_menu_and_delete_input(doc):
    page = _page(doc)
    page._add_panel()
    group = next(p for p in _meta(doc).panels if p.kind == "inputs")

    _menu_action(page.item_menu("i:world"), "Group 1").trigger()
    assert _input(doc, "world").panel == group.id
    assert not _menu_action(page.item_menu("i:world"), "Group 1").isEnabled(), "already there"

    _menu_action(page.item_menu("i:world"), "Delete input").trigger()
    assert not any(i.name == "world" for i in _meta(doc).inputs)


def test_deleting_a_group_keeps_its_inputs_with_the_ungrouped_ones(doc):
    page = _page(doc)
    page._add_panel()
    group = next(p for p in _meta(doc).panels if p.kind == "inputs")
    page.move_input_to_group("world", group.id)
    assert _labels(page) == ["Hub", "Group 1", "  World"]

    _menu_action(page.item_menu(f"g:{group.id}"), "Delete group").trigger()

    assert _labels(page) == ["Hub", "World"]
    assert _input(doc, "world").panel is None


def test_pressing_a_grip_closes_the_open_editor(doc):
    page = _page(doc)
    page.select("i:hub")
    page.item("i:world").grip.pressed.emit()
    assert page.selected is None
    assert page.item("i:hub").detail is None


def test_selecting_an_input_asks_the_canvas_to_light_what_it_sets(doc):
    page = _page(doc)
    lit: list[list] = []
    page.highlight_requested.connect(lit.append)

    page.select("i:hub")
    page.select(None)

    assert set(lit[0]) == {b.state for b in _input(doc, "hub").bindings}
    assert lit[-1] == []


def test_a_link_row_centers_its_node_without_selecting_it(doc):
    from bartlebot.gui.widgets.buttons.link_button import LinkButton  # noqa: PLC0415

    page = _page(doc)
    page.select("i:world")
    centered: list[str] = []
    page.center_requested.connect(centered.append)

    page.item("i:world").detail.findChildren(LinkButton)[0].click()

    assert centered == ["gate"]
    assert doc.param_panel.showing_signature


def test_an_input_linked_while_away_comes_back_selected(doc):
    page = _page(doc)
    assert doc.link_param("wisps", "timeout", new_input=SubgraphInput(name="wait", type="float", default=30.0))

    page = _page(doc)

    assert page.selected == "i:wait"
    assert page.item("i:wait").detail is not None


def test_the_open_editor_survives_a_rebuild(doc):
    page = _page(doc)
    page.select("i:hub")
    first = _input(doc, "hub").bindings[0]

    page._unbind("hub", first.state, first.param)

    assert page.selected == "i:hub"
    assert page.item("i:hub").detail is not None


def test_an_unlinked_input_says_so_and_blocks_saving(doc):
    page = _page(doc)
    _input(doc, "world").bindings = []
    page = _page(doc)

    assert page.item("i:world").meta_text.endswith("not linked")
    assert any("linked to nothing" in e for e in doc.validation_errors())


def test_ports_reorder_from_their_menu(doc):
    from bartlebot.bot.schema import SUBGRAPH_ENTRY  # noqa: PLC0415

    page = _page(doc)
    names = [line.name_edit.text() for line in page.port_lines(SUBGRAPH_ENTRY)]
    assert names == ["Start", "Hub"]

    _menu_action(page.port_menu(SUBGRAPH_ENTRY, 0), "Move down").trigger()

    entries = [s.label for s in doc.config.states if s.node_type == SUBGRAPH_ENTRY]
    assert entries == ["Hub", "Start"]
    assert [line.name_edit.text() for line in page.port_lines(SUBGRAPH_ENTRY)] == ["Hub", "Start"]


def test_the_description_folds_once_written(doc):
    from bartlebot.options import param_cards  # noqa: PLC0415

    assert doc.config.description, "the fixture has one"
    page = _page(doc)
    assert page.folds.cards[param_cards.DESCRIPTION].is_collapsed is True
    assert page.folds.cards[param_cards.PORTS].is_collapsed is False
    assert page.folds.cards[param_cards.INPUTS].is_collapsed is False


def test_building_the_signature_page_never_shows_a_parentless_widget(doc, parentless_shows):
    assert doc.set_node_promoted("wisps", True)
    parentless_shows.clear()
    page = _page(doc)
    page._add_panel()
    for line in page.lines:
        page.select(f"{'i' if line.row.kind == 'input' else 'g'}:{line.row.key}")
    assert parentless_shows == []


def _lit(doc) -> set[str]:
    return {key for key, node in doc.scene.node_items.items() if node.is_link_lit}


def test_selecting_an_input_lights_what_it_sets_and_leaving_the_page_puts_it_out(doc):
    page = _page(doc)
    page.select("i:hub")
    assert _lit(doc) == {b.state for b in _input(doc, "hub").bindings}
    assert doc.scene.selectedItems() == [], "lit, not selected"

    doc.select_state("wisps")

    assert _lit(doc) == set()


def test_coming_back_to_the_page_lights_its_selection_again(doc):
    page = _page(doc)
    page.select("i:world")
    doc.select_state("wisps")
    doc.scene.clearSelection()

    assert doc.param_panel.showing_signature
    assert _lit(doc) == {"gate"}


def test_a_link_row_brings_its_node_into_view_without_selecting_it(doc):
    page = _page(doc)
    page.select("i:world")
    doc.view.centerOn(-5000, -5000)

    page.center_requested.emit("gate")

    node = doc.scene.node_items["gate"]
    assert doc.view.mapToScene(doc.view.viewport().rect()).boundingRect().intersects(node.sceneBoundingRect())
    assert doc.scene.selectedItems() == []
    assert doc.param_panel.showing_signature


def test_saving_a_category_writes_it_and_keeps_the_version(harness, library):
    """A category is metadata, like a name (#172): nothing a bot runs changes."""
    canvas = _canvas(harness)
    canvas.open_definition_of(REGION)
    canvas.config.definition.tags = ["Team"]
    canvas.config_changed.emit()
    assert harness._has_unsaved_changes
    assert harness._save_definition()
    stored = library.get(SYNC_ID)
    assert stored.tags == ["Team"]
    assert stored.version == 1
    assert not harness._has_unsaved_changes
