"""Stage 3 of #37: the properties panel on a linked region and on its members.

A locked member's page disables what the definition owns (label, type, notes,
params) but not what the bot owns (the roles), and each param row carries an
Override toggle. The region page -- reached by selecting a whole region -- edits
the instance's label, colour and inputs in place, lists its overrides with Reset,
and offers Detach. Real ``BotCanvas`` tests on the synthetic "sync players"
instance; nothing here is a real character.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QLineEdit

from bartlebot.bot.schema import SubgraphInput, SubgraphInputBinding
from bartlebot.gui.canvas.param_panel import _RegionColorRow, _StrChoiceComboBox, _ZoneWidget
from bartlebot.store_roots import Origin
from bartlebot.subgraph_store import SubgraphLibrary
from bartlebot.subgraphs import ComposeReport, RegionStatus
from tests.test_subgraph_editor_lock import LOCALS
from tests.test_subgraphs import REGION, SYNC_ID, linked_bot, m, sync_players_definition


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
    yield c
    c.prepare_for_shutdown()


def _free_definition():
    """The fixture definition with one free param, and two inputs a fresh instance's page builds widgets for."""
    defn = sync_players_definition()
    defn.free_params = {"tp_b": ["zone"]}
    defn.inputs.append(
        SubgraphInput(
            name="spiral_world",
            label="Spiral world",
            type="str_choice",
            bindings=[SubgraphInputBinding(state="go_to_world", param="world")],
        )
    )
    defn.inputs.append(
        SubgraphInput(name="hub_zone", type="str", bindings=[SubgraphInputBinding(state="hub", param="zone")])
    )
    return defn


@pytest.fixture
def free_canvas(qapp, tmp_path):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    lib = SubgraphLibrary(tmp_path / "free")
    lib.save(_free_definition())
    c = BotCanvas()
    c.set_subgraph_library(lib)
    c.setup(linked_bot(lib.get(SYNC_ID), lib))
    c.enter_edit_mode()
    yield c
    c.prepare_for_shutdown()


def _state(canvas, sid: str):
    return next(s for s in canvas.scene.config.states if s.id == sid)


def _region(canvas, rid: str = REGION):
    return next(r for r in canvas.scene.config.regions if r.id == rid)


def _select_region(canvas, rid: str = REGION) -> None:
    """What clicking the pill does: select every member, with no member pressed."""
    scene = canvas.scene
    scene.clearSelection()
    for sid in _region(canvas, rid).members:
        scene.node_items[sid].setSelected(True)


# ---------------------------------------------------------------------------
# The locked node page
# ---------------------------------------------------------------------------


def test_locked_state_panel_disables_fields_but_not_roles(canvas):
    canvas.select_state(m("hub"))
    panel = canvas.param_panel
    assert panel.shown_state is _state(canvas, m("hub"))
    assert panel.is_locked
    assert not panel.node_type_combo.isEnabled()
    assert not panel.param_form.widget("zone").isEnabled()
    assert panel.param_form.override_button("zone") is not None
    assert not panel.param_form.override_button("zone").isChecked()
    # roles are the bot's: hub is neither, so both boxes are live
    assert panel.initial_check.isEnabled()
    assert panel.recovery_check.isEnabled()


def test_an_external_state_panel_is_not_locked(canvas):
    canvas.select_state("state_1")
    panel = canvas.param_panel
    assert not panel.is_locked
    assert panel.node_type_combo.isEnabled()
    assert panel.param_form.override_button("to_zone") is None


def test_override_unlocks_one_field_and_reset_removes_it(canvas):
    canvas.select_state(m("hub"))
    panel = canvas.param_panel
    source = _region(canvas).source

    panel.param_form.override_button("zone").setChecked(True)
    assert source.param_overrides == {"hub": {"zone": "WizardCity/Hub"}}
    assert panel.shown_state is _state(canvas, m("hub")), "the page is reloaded, not replaced"
    assert panel.param_form.widget("zone").isEnabled()
    assert canvas.can_undo()

    panel.param_form.widget("zone").setText("Mooshu/Hub")
    assert _state(canvas, m("hub")).params["zone"] == "Mooshu/Hub"
    assert source.param_overrides["hub"]["zone"] == "Mooshu/Hub"

    panel.param_form.override_button("zone").setChecked(False)
    assert source.param_overrides == {}
    assert _state(canvas, m("hub")).params["zone"] == "WizardCity/Hub", "re-resolved from the library"
    assert not panel.param_form.widget("zone").isEnabled()


def test_a_role_can_move_onto_a_locked_member(canvas):
    canvas.select_state(m("hub"))
    canvas.param_panel.request_recovery_role()
    assert canvas.scene.config.recovery_state == m("hub")


# ---------------------------------------------------------------------------
# The region page
# ---------------------------------------------------------------------------


def test_selecting_a_whole_region_shows_the_region_page(canvas):
    _select_region(canvas)
    panel = canvas.param_panel
    assert panel.shown_region is _region(canvas)
    assert panel.shown_state is None
    assert panel.region_label_edit.text() == "Sync players"
    assert panel.region_color_row.key == 2


def test_an_ordinary_region_gets_the_page_too(canvas):
    _select_region(canvas, "aabbccdd")
    panel = canvas.param_panel
    assert panel.shown_region is _region(canvas, "aabbccdd")
    assert panel.region_label_edit.text() == "Farm"
    assert not panel._region_link_box.isVisibleTo(panel)


def test_region_page_shows_inputs_and_writes_them_back(canvas):
    _select_region(canvas)
    panel = canvas.param_panel
    world = panel.region_inputs_form.widget("world")
    assert isinstance(world, QLineEdit)
    assert world.text() == "", "unset: the definition's value stands"
    world.setText("Mooshu")
    assert _region(canvas).source.inputs == {"world": "Mooshu"}


def test_input_change_re_resolves_bound_params(canvas):
    _select_region(canvas)
    canvas.param_panel.region_inputs_form.widget("world").setText("Mooshu")
    assert _state(canvas, m("go_to_world")).params["world"] == "Mooshu"
    assert _state(canvas, m("hub")).params["zone"] == "Mooshu"
    assert _state(canvas, m("tp_b")).params["zone"] == "WizardCity/Hub", "not bound; unchanged"


def test_an_override_beats_an_input_on_the_region_page(canvas):
    _region(canvas).source.param_overrides["hub"] = {"zone": "Elsewhere/Hub"}
    _select_region(canvas)
    canvas.param_panel.region_inputs_form.widget("world").setText("Mooshu")
    assert _state(canvas, m("go_to_world")).params["world"] == "Mooshu"
    assert _state(canvas, m("hub")).params["zone"] == "Elsewhere/Hub"


def test_region_page_lists_overrides_and_reset_clears_them(canvas):
    _region(canvas).source.param_overrides["hub"] = {"zone": "Elsewhere/Hub"}
    _select_region(canvas)
    panel = canvas.param_panel
    assert panel._region_overrides_body.isVisibleTo(panel)
    panel.override_toggled.emit(m("hub"), "zone", False)
    assert _region(canvas).source.param_overrides == {}
    assert _state(canvas, m("hub")).params["zone"] == "WizardCity/Hub"
    assert panel.shown_region is _region(canvas)
    assert not panel._region_overrides_body.isVisibleTo(panel)


def test_region_label_edit_renames_the_pill(canvas):
    _select_region(canvas)
    canvas.param_panel.region_label_edit.setText("Regroup")
    assert _region(canvas).label == "Regroup"
    assert canvas.scene.region_items[REGION]._label == "Regroup"


def test_region_colour_change_is_one_undo_step(canvas):
    _select_region(canvas)
    row = canvas.param_panel.region_color_row
    assert isinstance(row, _RegionColorRow)
    row._on_clicked(5)
    assert _region(canvas).color == 5
    assert canvas.can_undo()
    canvas.undo()
    assert _region(canvas).color == 2


def test_region_page_reports_the_held_status(canvas):
    canvas.set_compose_report(ComposeReport({REGION: RegionStatus(Origin.USER, "held", ["the update removes 'x'"], 3)}))
    _select_region(canvas)
    panel = canvas.param_panel
    assert "library version 3" in panel._region_version.text()
    assert "in your library" in panel._region_version.text()
    assert panel._region_status.isVisibleTo(panel)
    assert "removes 'x'" in panel._region_status.text()


def test_detach_from_the_region_page(canvas):
    _select_region(canvas)
    canvas.param_panel.detach_requested.emit(REGION)
    assert _region(canvas).source is None
    assert not any(canvas.scene.is_locked(m(loc)) for loc in LOCALS)
    assert canvas.param_panel.shown_region is _region(canvas), "the page stays up, now as an ordinary region"


# ---------------------------------------------------------------------------
# Free params, the label, and the lock toggle
# ---------------------------------------------------------------------------


def test_a_free_param_is_editable_without_a_toggle_and_records_an_override(free_canvas):
    c = free_canvas
    c.select_state(m("tp_b"))
    form = c.param_panel.param_form
    assert form.widget("zone").isEnabled() and form.override_button("zone") is None
    form.widget("zone").setText("MooShu/Hub")
    assert _state(c, m("tp_b")).params["zone"] == "MooShu/Hub"
    assert _region(c).source.param_overrides == {"tp_b": {"zone": "MooShu/Hub"}}
    form.widget("zone").setText("WizardCity/Hub")
    assert _region(c).source.param_overrides == {}, "back at the subgraph's value: nothing to override"
    # A param that is not free stays locked, and offers its override toggle instead.
    c.select_state(m("gather"))
    assert not form.widget("target_hooked_player").isEnabled()
    assert form.override_button("target_hooked_player") is not None


def test_lock_toggle_reads_locked_then_overridden(canvas):
    canvas.select_state(m("hub"))
    form = canvas.param_panel.param_form
    assert "subgraph owns this value" in form.override_button("zone").toolTip()
    form.override_button("zone").setChecked(True)
    assert "no longer follows the subgraph" in form.override_button("zone").toolTip()
    assert form.override_button("zone").isChecked()


def test_a_locked_member_label_is_editable_and_its_override_resets(free_canvas):
    c = free_canvas
    c.select_state(m("hub"))
    panel = c.param_panel
    assert panel.is_locked and panel.node_label_edit.isEnabled()
    panel.node_label_edit.setText("Meet here")
    assert _state(c, m("hub")).label == "Meet here"
    assert _region(c).source.label_overrides == {"hub": "Meet here"}
    _select_region(c)
    assert panel._region_overrides_body.isVisibleTo(panel)
    panel.label_override_reset.emit(m("hub"))
    assert _region(c).source.label_overrides == {}
    assert _state(c, m("hub")).label is None
    assert not panel._region_overrides_body.isVisibleTo(panel)
    assert c.can_undo()


def test_region_inputs_form_takes_choices_from_the_bound_param(free_canvas):
    c = free_canvas
    _select_region(c)
    form = c.param_panel.region_inputs_form
    world = form.widget("spiral_world")
    assert isinstance(world, _StrChoiceComboBox) and world.count() > 1, "the node's dropdown, not an empty combo"
    world.set_value("MooShu")
    assert _region(c).source.inputs["spiral_world"] == "MooShu"
    assert _state(c, m("go_to_world")).params["world"] == "MooShu"
    assert isinstance(form.widget("hub_zone"), _ZoneWidget)
    assert isinstance(form.widget("world"), QLineEdit), "a str input bound to a str_choice stays a plain field"


def test_region_inputs_form_shares_the_client_and_zone_getters(free_canvas):
    c = free_canvas
    c.set_clients_getter(lambda: ["one"])
    c.set_zone_getter(lambda: ["WizardCity/Hub"])
    _select_region(c)
    form = c.param_panel.region_inputs_form
    assert form._get_clients() == ["one"]
    assert form._get_zone_suggestions() == ["WizardCity/Hub"]


@pytest.fixture
def xyz_canvas(qapp, tmp_path):
    """The fixture definition plus an unbound coordinate input, so the region page builds an xyz field."""
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    defn = sync_players_definition()
    defn.inputs.append(SubgraphInput(name="spot", type="xyz"))
    lib = SubgraphLibrary(tmp_path / "xyz")
    lib.save(defn)
    c = BotCanvas()
    c.set_subgraph_library(lib)
    c.setup(linked_bot(lib.get(SYNC_ID), lib))
    c.enter_edit_mode()
    yield c
    c.prepare_for_shutdown()


def test_a_cleared_coordinate_input_is_unset_again(xyz_canvas):
    """A blank coordinate is absent from the form rather than empty, so clearing one has to unset it."""
    _select_region(xyz_canvas)
    form = xyz_canvas.param_panel.region_inputs_form
    form.set_xyz("spot", 1.0, 2.0, 3.0)
    assert _region(xyz_canvas).source.inputs["spot"] == {"x": 1.0, "y": 2.0, "z": 3.0}

    form.widget("spot").axis_edit("x").setText("")

    assert _region(xyz_canvas).source.inputs["spot"] is None
