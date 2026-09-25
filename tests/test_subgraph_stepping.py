"""Stage 7 of the subgraph-calls plan: stepping in and out.

One crumb bar says where the canvas is -- Focus Mode on a region, a subgraph node's
body viewed read-only in Run mode, or a definition open as a document -- and Esc,
Alt+Left, its close button and its earlier segments all step back out. In Run mode a
subgraph node's body is Focus Mode over the composed members, drawn at their
definition positions, so the run highlight follows them by id. In a definition the
crumb carries Save, Save as and, for a modified built-in, Revert.

Built on the synthetic "sync players" fixture from ``test_subgraphs`` and the
controller harness from ``test_subgraph_integration``; nothing here is a real character.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMessageBox

from bartlebot.bot.schema import RegionModel
from bartlebot.gui.canvas.bot_canvas import BotMode
from bartlebot.gui.canvas.call_node_item import CallNodeItem
from bartlebot.gui.canvas.subgraph_widgets import CrumbMode
from bartlebot.gui.theme import THEME
from bartlebot.store_roots import Origin
from bartlebot.subgraph_store import SubgraphLibrary
from bartlebot.subgraphs import compose_bot, definition_to_config
from tests.test_subgraph_integration import ControllerHarness
from tests.test_subgraphs import REGION, SYNC_ID, linked_bot, m, sync_players_definition


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """Answer any message box a test did not stub, so a missed prompt fails fast instead of blocking the run."""
    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.No)


@pytest.fixture
def library(tmp_path) -> SubgraphLibrary:
    lib = SubgraphLibrary(tmp_path / "library")
    lib.save(sync_players_definition())
    return lib


def _bot(library):
    bot, report = compose_bot(linked_bot(library.get(SYNC_ID), library), library)
    bot.name = "Farm"
    return bot, report


@pytest.fixture
def canvas(qapp, library):
    """A bot with one subgraph node, in Run mode, sized so the camera has room to move."""
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    c = BotCanvas()
    c.set_subgraph_library(library)
    bot, report = _bot(library)
    c.setup(bot)
    c.set_compose_report(report)
    c.resize(900, 650)
    qapp.processEvents()
    yield c
    c.prepare_for_shutdown()


@pytest.fixture
def doc(qapp, library):
    """The fixture definition open as a document, with a plain region round two of its nodes."""
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    c = BotCanvas()
    c.set_subgraph_library(library)
    config = definition_to_config(library.get(SYNC_ID))
    config.regions.append(RegionModel(id="rg", label="Gather", members=["relog", "gather"], color=3))
    c.setup(config)
    c.enter_edit_mode()
    c.set_definition_context(["Farm"])
    c.resize(900, 650)
    qapp.processEvents()
    yield c
    c.prepare_for_shutdown()


def _centre(c):
    return c.view.mapToScene(c.view.viewport().rect().center())


def _key(c, key, mods=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(c.view, key, mods)


# ---------------------------------------------------------------------------
# A subgraph node's body, in Run mode
# ---------------------------------------------------------------------------


def test_run_mode_view_draws_the_members_and_hides_the_rest(canvas):
    canvas.view_instance(REGION)

    items = canvas.scene.node_items
    assert canvas.viewed_instances == [REGION]
    assert REGION not in items, "the call node gives way to its members"
    assert items[m("relog")].isVisible()
    assert not items["state_1"].isVisible()
    inside = [e for s, _, d, e in canvas.scene.edge_items if s.startswith(f"{REGION}_") and d.startswith(f"{REGION}_")]
    assert inside and all(e.isVisible() for e in inside)
    assert not any(e.isVisible() for s, _, d, e in canvas.scene.edge_items if "state_1" in (s, d))


def test_members_sit_at_their_definition_positions(canvas, library):
    canvas.view_instance(REGION)

    anchor = next(r for r in canvas.config.regions if r.id == REGION).source.anchor
    local = next(s for s in library.get(SYNC_ID).states if s.id == "gather").position
    node = canvas.scene.node_items[m("gather")]
    assert node.pos().x() == pytest.approx(anchor["x"] + local["x"])


def test_the_run_view_is_read_only(canvas):
    canvas.view_instance(REGION)

    assert canvas.mode == BotMode.RUN
    assert (
        not canvas.scene.node_items[m("relog")].flags()
        & canvas.scene.node_items[m("relog")].GraphicsItemFlag.ItemIsMovable
    )


def test_the_crumb_is_a_path_in_the_accent_with_a_double_frame(canvas):
    canvas.view_instance(REGION)

    bar = canvas.crumb_bar
    assert bar.isVisibleTo(canvas)
    assert bar.mode == CrumbMode.VIEW
    assert bar.text == "Farm › Sync players"
    assert "read-only" in bar.hint_text
    assert bar.glyph_visible
    assert not bar.save_button.isVisibleTo(bar), "nothing on screen can be edited"
    assert bar.background() == THEME.qcolor("accent")
    assert canvas.shared_frame.isVisibleTo(canvas) and canvas.shared_frame.is_double
    assert not canvas.focus_frame.isVisibleTo(canvas)


def test_the_run_highlight_lands_on_the_member_and_the_view_follows_it(canvas):
    canvas.set_running()
    canvas.view_instance(REGION)

    canvas.set_state(m("relog"))
    assert canvas.scene.node_items[m("relog")].is_active
    canvas.set_state(m("wisps"))
    assert canvas.scene.node_items[m("wisps")].is_active
    assert not canvas.scene.node_items[m("relog")].is_active


def test_a_member_already_running_is_lit_when_the_view_opens(canvas):
    canvas.set_running()
    canvas.set_state(m("gather"))
    assert canvas.scene.node_items[REGION].is_active

    canvas.view_instance(REGION)

    assert canvas.scene.node_items[m("gather")].is_active


def test_leaving_the_view_lights_the_call_node_again(canvas):
    canvas.set_running()
    canvas.view_instance(REGION)
    canvas.set_state(m("gather"))

    assert canvas.step_out()

    assert canvas.scene.node_items[REGION].is_active


def test_esc_steps_out_and_puts_the_camera_back(canvas):
    before = _centre(canvas)
    canvas.view_instance(REGION)
    assert _centre(canvas) != before

    _key(canvas, Qt.Key.Key_Escape)

    assert canvas.viewed_instances == []
    assert isinstance(canvas.scene.node_items[REGION], CallNodeItem)
    assert not canvas.crumb_bar.isVisibleTo(canvas)
    assert not canvas.shared_frame.isVisibleTo(canvas)
    after = _centre(canvas)
    scale = canvas.view.transform().m11()
    assert abs(after.x() - before.x()) * scale <= 1.0 + 1e-6
    assert abs(after.y() - before.y()) * scale <= 1.0 + 1e-6


def test_alt_left_and_the_close_button_step_out_too(canvas):
    canvas.view_instance(REGION)
    canvas.step_back()
    assert canvas.viewed_instances == []

    canvas.view_instance(REGION)
    canvas.crumb_bar.close_button.click()
    assert canvas.viewed_instances == []


def test_edit_mode_has_no_read_only_view(canvas):
    canvas.enter_edit_mode()

    canvas.view_instance(REGION)

    assert canvas.viewed_instances == []
    assert isinstance(canvas.scene.node_items[REGION], CallNodeItem)


def test_entering_edit_mode_closes_the_view(canvas):
    canvas.view_instance(REGION)

    canvas.enter_edit_mode()

    assert canvas.viewed_instances == []
    assert REGION in canvas.scene.node_items


def test_a_run_mode_double_click_asks_for_the_view(canvas):
    asked = []
    canvas.scene.view_instance_requested.connect(asked.append)
    node = canvas.scene.node_items[REGION]
    view = canvas.view
    pos = view.mapFromScene(node.sceneBoundingRect().center())
    QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=pos)

    assert asked == [REGION]
    assert canvas.viewed_instances == [REGION]


def test_a_recompose_before_the_run_keeps_the_view(canvas, library):
    canvas.view_instance(REGION)

    bot, _report = _bot(library)
    canvas.replace_config(bot)

    assert canvas.viewed_instances == [REGION]
    assert canvas.scene.node_items[m("relog")].isVisible()
    assert not canvas.scene.node_items["state_1"].isVisible()


def test_a_region_focused_first_is_a_segment_of_the_path(canvas):
    canvas.config.regions.append(RegionModel(id="plain", label="Around", members=["state_1", "state_2"], color=2))
    canvas.replace_config(canvas.config)
    canvas.enter_focus_mode("plain")
    assert canvas.crumb_bar.mode == CrumbMode.FOCUS

    canvas.view_instance(REGION)

    assert canvas.crumb_bar.text == "Farm › Around › Sync players"
    QTest.mouseClick(canvas.crumb_bar.segment_labels[1], Qt.MouseButton.LeftButton)
    assert canvas.viewed_instances == []
    assert canvas.focus_region_id == "plain"


# ---------------------------------------------------------------------------
# Focus Mode in a bot keeps its own look
# ---------------------------------------------------------------------------


def test_region_focus_in_a_bot_is_unchanged(canvas):
    canvas.config.regions.append(RegionModel(id="plain", label="Around", members=["state_1", "state_2"], color=2))
    canvas.replace_config(canvas.config)

    canvas.enter_focus_mode("plain")

    bar = canvas.crumb_bar
    assert bar.mode == CrumbMode.FOCUS
    assert bar.text == "Focus Mode — Around"
    assert "Esc" in bar.hint_text
    assert not bar.glyph_visible
    assert bar.background() == THEME.region_color(2)
    assert canvas.focus_frame.isVisibleTo(canvas) and not canvas.shared_frame.isVisibleTo(canvas)


# ---------------------------------------------------------------------------
# A definition document
# ---------------------------------------------------------------------------


def test_a_definition_crumb_names_the_path_and_its_verbs(doc):
    bar = doc.crumb_bar
    assert bar.mode == CrumbMode.DEFINITION
    assert bar.text == "Farm › Sync players"
    assert [b.text() for b in (bar.save_button, bar.save_as_button)] == ["Save", "Save as…"]
    assert bar.save_button.isVisibleTo(bar) and bar.save_as_button.isVisibleTo(bar)
    assert not bar.revert_button.isVisibleTo(bar), "only a modified built-in can be reverted"
    assert doc.shared_frame.isVisibleTo(doc) and doc.shared_frame.is_double


def test_revert_shows_only_for_a_modified_built_in(doc):
    doc.set_definition_context(["Farm"], can_revert=True)
    assert doc.crumb_bar.revert_button.isVisibleTo(doc.crumb_bar)


def test_the_crumb_verbs_ask_the_controller(doc):
    asked = []
    doc.definition_save_requested.connect(lambda then_back: asked.append(("save", then_back)))
    doc.definition_save_as_requested.connect(lambda: asked.append("save as"))
    doc.definition_revert_requested.connect(lambda: asked.append("revert"))
    doc.set_definition_context(["Farm"], can_revert=True)
    bar = doc.crumb_bar
    bar.save_button.click()
    bar.save_as_button.click()
    bar.revert_button.click()
    assert asked == [("save", False), "save as", "revert"]


def test_a_region_focused_in_a_definition_is_a_final_segment_in_its_colour(doc):
    doc.enter_focus_mode("rg")

    bar = doc.crumb_bar
    assert bar.mode == CrumbMode.DEFINITION
    assert bar.text == "Farm › Sync players › Gather"
    assert doc.focus_color == THEME.region_color(3)
    assert doc.shared_frame.isVisibleTo(doc) and doc.focus_frame.isVisibleTo(doc)
    assert doc.focus_frame.width() < doc.shared_frame.width(), "the region's line sits inside the double one"


def test_clicking_the_definition_segment_leaves_the_focus(doc):
    doc.enter_focus_mode("rg")

    QTest.mouseClick(doc.crumb_bar.segment_labels[1], Qt.MouseButton.LeftButton)

    assert doc.focus_region_id is None
    assert doc.is_definition


def test_clicking_the_bot_segment_asks_to_go_back_to_it(doc):
    asked = []
    doc.document_back_requested.connect(asked.append)

    QTest.mouseClick(doc.crumb_bar.segment_labels[0], Qt.MouseButton.LeftButton)

    assert asked == [0]


def test_the_last_segment_is_not_a_link(doc):
    back = []
    doc.document_back_requested.connect(back.append)
    QTest.mouseClick(doc.crumb_bar.segment_labels[-1], Qt.MouseButton.LeftButton)
    assert back == [] and doc.is_definition


def test_esc_dismisses_search_then_focus_then_selection_before_leaving(doc):
    back = []
    doc.definition_back_requested.connect(lambda: back.append(True))
    doc.enter_focus_mode("rg")
    doc.scene.node_items["relog"].setSelected(True)
    doc._show_search_bar()

    _key(doc, Qt.Key.Key_Escape)
    assert doc.focus_region_id == "rg", "the search bar went first"
    _key(doc, Qt.Key.Key_Escape)
    assert doc.focus_region_id is None
    assert doc.scene.selectedItems()
    _key(doc, Qt.Key.Key_Escape)
    assert not doc.scene.selectedItems()
    assert back == []
    _key(doc, Qt.Key.Key_Escape)
    assert back == [True]


def test_alt_left_leaves_the_focus_then_the_definition(doc):
    back = []
    doc.definition_back_requested.connect(lambda: back.append(True))
    doc.enter_focus_mode("rg")
    doc.scene.node_items["relog"].setSelected(True)

    doc.step_back()
    assert doc.focus_region_id is None and back == []
    doc.step_back()
    assert back == [True], "Alt+Left is Back, so it does not stop at the selection"


def test_a_theme_switch_repaints_the_bar_from_the_slot(doc):
    doc.enter_focus_mode("rg")
    doc.crumb_bar.refresh_theme_styles()
    assert THEME.region_color(3).name() in doc.crumb_bar.segment_labels[-1].styleSheet()


# ---------------------------------------------------------------------------
# The controller: back several levels, Save as, Revert
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
    canvas.document_back_requested.connect(h._back_to_document)
    path = tmp_path / "bots" / "linked.json"
    linked_bot(library.get(SYNC_ID), library).save(path)
    h._open_bot(str(path))
    canvas.enter_edit_mode()
    yield h
    canvas.prepare_for_shutdown()


def test_the_controller_hands_the_crumb_the_documents_underneath(harness):
    canvas = harness._window.bot_canvas
    canvas.open_definition_of(REGION)
    assert canvas.crumb_bar.text == "Linked › Sync players"


def test_back_to_a_document_closes_what_is_above_it(harness):
    canvas = harness._window.bot_canvas
    canvas.open_definition_of(REGION)

    QTest.mouseClick(canvas.crumb_bar.segment_labels[0], Qt.MouseButton.LeftButton)

    assert not canvas.is_definition
    assert harness._doc_stack == []


def test_back_to_a_document_stops_when_one_is_cancelled(harness, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    canvas = harness._window.bot_canvas
    canvas.open_definition_of(REGION)
    canvas.link_param("tp_a", "zone", existing="hub")

    assert not harness._back_to_document(0)

    assert canvas.is_definition


def test_save_as_makes_a_new_definition_and_edits_it(harness, library):
    canvas = harness._window.bot_canvas
    canvas.open_definition_of(REGION)
    canvas.link_param("tp_a", "zone", existing="hub")
    assert harness._has_unsaved_changes

    assert harness._save_definition_as("Sync players, faster", "A copy")

    new_id = canvas.config.definition.id
    assert new_id != SYNC_ID
    copy = library.get(new_id)
    assert (copy.name, copy.description, copy.version) == ("Sync players, faster", "A copy", 1)
    assert ("tp_a", "zone") in {(b.state, b.param) for i in copy.inputs for b in i.bindings}
    assert library.get(SYNC_ID).version == 1, "the original is untouched"
    assert not harness._has_unsaved_changes
    assert not canvas.can_undo(), "undo would reach back into the original"
    assert canvas.crumb_bar.text == "Linked › Sync players, faster"

    assert harness._definition_back()
    region = next(r for r in canvas.config.regions if r.id == REGION)
    assert region.source.subgraph_id == SYNC_ID, "the bot keeps linking the original"


def test_save_as_is_refused_while_the_definition_has_errors(harness, library, monkeypatch):
    refused = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: refused.append(a))
    canvas = harness._window.bot_canvas
    canvas.open_definition_of(REGION)
    canvas.config.transitions = [t for t in canvas.config.transitions if t.source != "entry_hub"]
    canvas.scene.setup(canvas.config)

    assert not harness._save_definition_as("Copy", None)
    assert refused
    assert library.ids() == [SYNC_ID]


@pytest.fixture
def two_roots(tmp_path):
    """A built-in definition, and the user's modified copy shadowing it."""
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    sync_players_definition().save(shipped / "subgraphs" / f"{SYNC_ID}.json")
    lib = SubgraphLibrary(shipped, user)
    lib.save(sync_players_definition(version=4, world="Mooshu"))
    assert lib.origin(SYNC_ID) is Origin.SHIPPED_OVERRIDE
    return lib


def test_revert_discards_the_copy_and_shows_the_built_in(qapp, two_roots, monkeypatch):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Ok)
    canvas = BotCanvas()
    canvas.set_subgraph_library(two_roots)
    h = ControllerHarness(canvas, two_roots)
    try:
        assert h._open_definition(SYNC_ID)
        assert canvas.crumb_bar.revert_button.isVisibleTo(canvas.crumb_bar)

        assert h._revert_definition()

        assert two_roots.origin(SYNC_ID) is Origin.SHIPPED
        assert canvas.config.definition.version == 1
        go = next(s for s in canvas.config.states if s.id == "go_to_world")
        assert go.params["world"] == "WizardCity"
        assert not canvas.crumb_bar.revert_button.isVisibleTo(canvas.crumb_bar)
    finally:
        canvas.prepare_for_shutdown()


def test_revert_can_be_cancelled(qapp, two_roots, monkeypatch):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    canvas = BotCanvas()
    canvas.set_subgraph_library(two_roots)
    h = ControllerHarness(canvas, two_roots)
    try:
        h._open_definition(SYNC_ID)
        assert not h._revert_definition()
        assert two_roots.origin(SYNC_ID) is Origin.SHIPPED_OVERRIDE
    finally:
        canvas.prepare_for_shutdown()
