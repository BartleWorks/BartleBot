"""Stage 4 of #37: creating definitions from regions, editing them in place, pushing, inserting.

Real ``BotCanvas`` tests with a ``tmp_path`` library. The bots are the synthetic
ones from ``test_subgraphs`` -- the seven-node "sync players" instance and the
two-node "startup" region; nothing here is a real character. The dialogs the
verbs open are bypassed by the keyword arguments the canvas methods take for
exactly this purpose, except the Done prompt, which is patched.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import QMessageBox

from bartlebot.bot.schema import (
    BotConfig,
    RegionModel,
    StateModel,
    SubgraphDefinition,
    SubgraphInput,
    SubgraphInputBinding,
    SubgraphSource,
)
from bartlebot.bot.strategy_models import BotCombatStrategy, StateAssignment
from bartlebot.gui.canvas.bot_canvas import BotMode
from bartlebot.gui.canvas.node_item import NODE_H
from bartlebot.gui.canvas.subgraph_widgets import ExposeInputDialog
from bartlebot.strategy_store import list_bot_strategies, rekey_bot_strategy_states, save_bot_strategy
from bartlebot.subgraph_store import SubgraphLibrary, count_subgraph_uses
from bartlebot.subgraphs import (
    added_member_ids,
    contains_subgraph,
    definition_values,
    drop_states,
    linked_regions,
    local_id,
    member_id,
    nested_regions,
    reresolve_member_params,
)
from tests.test_subgraph_editor_lock import LOCALS
from tests.test_subgraphs import REGION, SYNC_ID, linked_bot, m, plain_bot, sync_players_definition


@pytest.fixture
def library(tmp_path) -> SubgraphLibrary:
    lib = SubgraphLibrary(tmp_path / "library")
    lib.save(sync_players_definition())
    return lib


def _canvas(qapp, library, config):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    c = BotCanvas()
    c.set_subgraph_library(library)
    c.setup(config)
    c.enter_edit_mode()
    return c


@pytest.fixture
def linked(qapp, library):
    c = _canvas(qapp, library, linked_bot(library.get(SYNC_ID), library))
    yield c
    c.prepare_for_shutdown()


@pytest.fixture
def plain(qapp, library):
    c = _canvas(qapp, library, plain_bot())
    yield c
    c.prepare_for_shutdown()


def _region(canvas, rid: str = REGION):
    return next(r for r in canvas.scene.config.regions if r.id == rid)


def _state(canvas, sid: str):
    return next(s for s in canvas.scene.config.states if s.id == sid)


# ---------------------------------------------------------------------------
# Save region as reusable subgraph
# ---------------------------------------------------------------------------


def test_save_region_as_subgraph_writes_user_root_and_links_region(plain, library):
    assert plain.save_region_as_subgraph("r1", name="Startup", description="Relog and regroup.")
    config = plain.scene.config
    # the region takes a readable instance id: the definition's slug plus a count
    assert [r.id for r in config.regions] == ["startup_1"]
    region = _region(plain, "startup_1")
    assert region.source is not None
    defn = library.get(region.source.subgraph_id)
    assert defn is not None and defn.name == "Startup" and defn.version == 1
    assert defn.description == "Relog and regroup."
    assert sorted(s.id for s in defn.states) == ["state_1", "state_2"]
    s1, s2 = "startup_1_state_1", "startup_1_state_2"
    assert region.members == [s1, s2]
    assert config.initial == s1 and config.recovery_state == s2
    assert [s.id for s in config.states] == [s1, s2, "state_3"]
    assert plain.scene.is_locked(s1) and not plain.scene.is_locked("state_3")
    assert plain.compose_report.regions["startup_1"].kind == "linked"
    assert plain.scene.region_items["startup_1"].link_state == "linked"
    plain.undo()
    assert _region(plain, "r1").source is None
    assert [s.id for s in plain.scene.config.states] == ["state_1", "state_2", "state_3"]


def test_save_region_as_subgraph_refuses_a_linked_region_and_run_mode(linked, plain):
    assert linked.save_region_as_subgraph(REGION, name="Again") is False
    plain._apply_mode(BotMode.RUN)
    assert plain.save_region_as_subgraph("r1", name="Startup") is False


def test_conversion_rekeys_combat_assignments(tmp_path):
    bcs = BotCombatStrategy(name="Farm", bot_id="bot-2")
    bcs.state_assignments.append(StateAssignment(state_ids=["state_1", "state_3"]))
    other = BotCombatStrategy(name="Other", bot_id="bot-9")
    other.state_assignments.append(StateAssignment(state_ids=["state_1"]))
    save_bot_strategy(bcs, tmp_path)
    save_bot_strategy(other, tmp_path)

    changed = rekey_bot_strategy_states("bot-2", {"state_1": member_id("r1", "state_1")}, tmp_path)

    assert changed == 1
    mine = list_bot_strategies("bot-2", tmp_path)[0]
    assert mine.state_assignments[0].state_ids == [member_id("r1", "state_1"), "state_3"]
    assert list_bot_strategies("bot-9", tmp_path)[0].state_assignments[0].state_ids == ["state_1"]


# ---------------------------------------------------------------------------
# Edit in place, save, discard
# ---------------------------------------------------------------------------


def test_edit_subgraph_shows_definition_values(linked):
    region = _region(linked)
    region.source.inputs["world"] = "Mooshu"
    region.source.param_overrides["hub"] = {"zone": "Elsewhere/Hub"}
    reresolve_member_params(linked.scene.config, region, linked.scene.subgraph_library.get(SYNC_ID))
    assert _state(linked, m("hub")).params["zone"] == "Elsewhere/Hub"
    assert _state(linked, m("go_to_world")).params["world"] == "Mooshu"

    assert linked.begin_subgraph_edit(REGION)
    assert linked.editing_region_id == REGION
    assert linked.scene.editing_region_id == REGION
    assert not linked.scene.is_locked(m("hub"))
    assert linked.scene.region_items[REGION].link_state == "editing"
    assert "Sync players" in linked.edit_crumb_text
    assert _state(linked, m("hub")).params["zone"] == "WizardCity/Hub", "the definition's value, not the override"
    assert _state(linked, m("go_to_world")).params["world"] == "WizardCity"
    assert region.source.inputs == {"world": "Mooshu"}, "the instance keeps its inputs for after the edit"
    assert region.source.param_overrides == {"hub": {"zone": "Elsewhere/Hub"}}


def test_edit_subgraph_refuses_when_the_library_has_no_definition(linked, library, monkeypatch):
    library.delete(SYNC_ID)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[1])))
    assert linked.begin_subgraph_edit(REGION) is False
    assert shown == ["Edit Subgraph"]
    assert linked.editing_region_id is None


def test_save_to_library_bumps_version_and_recomposes_sibling_instances(linked, library):
    # a second instance of the same definition, and an override on the first
    _region(linked).source.param_overrides["hub"] = {"zone": "Elsewhere/Hub"}
    scene = linked.scene
    scene.clearSelection()
    for loc in LOCALS:
        scene.node_items[m(loc)].setSelected(True)
    scene._copy_selected()
    scene._paste()
    sibling = next(r for r in linked_regions(scene.config) if r.id != REGION)

    assert linked.begin_subgraph_edit(REGION)
    _state(linked, m("wisps")).params["timeout"] = 99
    assert linked.save_subgraph_to_library()

    assert linked.editing_region_id is None
    defn = library.get(SYNC_ID)
    assert defn.version == 2
    assert {s.id: s for s in defn.states}["wisps"].params["timeout"] == 99
    config = linked.scene.config
    assert _region(linked).source.version == 2
    assert _state(linked, m("wisps")).params["timeout"] == 99
    assert _state(linked, m("wisps", sibling.id)).params["timeout"] == 99, "the sibling follows the push"
    assert _state(linked, m("hub")).params["zone"] == "Elsewhere/Hub", "the override is back"
    assert linked.scene.is_locked(m("wisps"))
    assert linked.scene.region_items[REGION].link_state == "linked"
    assert config.regions[0].source.param_overrides == {"hub": {"zone": "Elsewhere/Hub"}}
    linked.undo()
    assert _state(linked, m("wisps", sibling.id)).params["timeout"] == 30


def test_new_state_added_while_editing_gets_derived_id_after_push(linked, library):
    assert linked.begin_subgraph_edit(REGION)
    scene = linked.scene
    new_id = scene.create_state_at("handle_potions", QPointF(3080.0, 0.0))
    _region(linked).members.append(new_id)
    scene._create_transition(scene.node_items[m("wisps")], scene.node_items[new_id], "SUCCESS")
    scene._create_transition(scene.node_items[new_id], scene.node_items["state_1"], "SUCCESS")  # an external edge
    assert linked.save_subgraph_to_library()

    defn = library.get(SYNC_ID)
    assert new_id in {s.id for s in defn.states}
    derived = member_id(REGION, new_id)
    assert derived in _region(linked).members
    assert derived in {s.id for s in linked.scene.config.states}
    assert any(t.source == derived and t.dest == "state_1" for t in linked.scene.config.transitions)
    assert linked.compose_report.regions[REGION].kind == "linked"


def test_save_to_library_refuses_self_containment(linked, library, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: shown.append(a[2])))
    assert linked.begin_subgraph_edit(REGION)
    rid = linked.scene.insert_subgraph_instance(SYNC_ID, QPointF(0.0, 900.0), into_region=REGION)
    assert rid is not None
    assert linked.save_subgraph_to_library() is False
    assert "contain itself" in shown[0]
    assert library.get(SYNC_ID).version == 1
    assert linked.editing_region_id == REGION, "the edit stays open"


def test_discard_restores_library_version(linked):
    assert linked.begin_subgraph_edit(REGION)
    scene = linked.scene
    _state(linked, m("wisps")).params["timeout"] = 99
    scene.delete_state(m("tp_a"), confirm=False)
    new_id = scene.create_state_at("handle_potions", QPointF(3080.0, 0.0))
    _region(linked).members.append(new_id)
    linked.discard_subgraph_edit()

    assert linked.editing_region_id is None
    config = linked.scene.config
    assert _state(linked, m("wisps")).params["timeout"] == 30
    assert m("tp_a") in {s.id for s in config.states}
    assert new_id not in {s.id for s in config.states}
    assert _region(linked).members == [m(loc) for loc in LOCALS]
    assert linked.scene.is_locked(m("hub"))


def test_done_while_editing_prompts(linked, monkeypatch):
    answers = []

    def question(*_a, **_k):
        return answers.pop(0)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    assert linked.begin_subgraph_edit(REGION)
    answers.append(QMessageBox.StandardButton.Cancel)
    linked._on_done_clicked()
    assert linked.mode == BotMode.EDIT and linked.editing_region_id == REGION
    answers.append(QMessageBox.StandardButton.Discard)
    linked._on_done_clicked()
    assert linked.mode == BotMode.RUN and linked.editing_region_id is None


def test_closing_the_bot_ends_the_edit(linked):
    assert linked.begin_subgraph_edit(REGION)
    linked.close_bot()
    assert linked.editing_region_id is None


# ---------------------------------------------------------------------------
# Expose as input
# ---------------------------------------------------------------------------


def test_expose_as_input_binds_and_survives_push(linked, library):
    assert linked.begin_subgraph_edit(REGION)
    defn = linked.editing_definition
    assert linked.expose_input(m("wisps"), "timeout", new_input=SubgraphInput(name="wisp_timeout", type="int"))
    assert linked.expose_input(m("tp_a"), "target_zone", existing="world")
    assert linked.expose_input(m("tp_a"), "target_zone", existing="world"), "binding twice is a no-op"
    assert linked.expose_input(m("hub"), "zone", new_input=SubgraphInput(name="world", type="str")) is False
    assert [i.name for i in defn.inputs] == ["world", "wisp_timeout"]
    assert defn.inputs[1].bindings == [SubgraphInputBinding(state="wisps", param="timeout")]
    assert len(defn.inputs[0].bindings) == 3

    assert linked.save_subgraph_to_library()
    pushed = library.get(SYNC_ID)
    assert [i.name for i in pushed.inputs] == ["world", "wisp_timeout"]
    assert pushed.inputs[1].bindings[0].state == "wisps"
    # and the new input works on the instance, through the region page
    linked._show_region_page(_region(linked))
    linked.param_panel.region_inputs_form.widget("wisp_timeout").setValue(5)
    assert _region(linked).source.inputs["wisp_timeout"] == 5
    assert _state(linked, m("wisps")).params["timeout"] == 5


def test_expose_is_refused_outside_an_edit(linked):
    assert linked.expose_input(m("wisps"), "timeout", new_input=SubgraphInput(name="t", type="int")) is False


def test_expose_dialog_offers_compatible_inputs_and_builds_a_new_one(qapp):
    inputs = [SubgraphInput(name="world", type="str"), SubgraphInput(name="n", type="int")]
    dlg = ExposeInputDialog(
        inputs, state_local_id="hub", param_key="zone", param_type="str", param_label="Zone", current_value="X/Hub"
    )
    assert dlg.choice == "world", "the one compatible input is preselected"
    dlg.set_new_input("hub_zone", "Hub zone", "Where to regroup")
    assert dlg.choice is None
    new = dlg.new_input()
    assert (new.name, new.label, new.help, new.type, new.default) == (
        "hub_zone",
        "Hub zone",
        "Where to regroup",
        "str",
        "X/Hub",
    )
    dlg.set_new_input("world")
    assert not dlg._ok.isEnabled(), "a taken name cannot be declared again"


def test_region_page_while_editing_edits_the_working_definition(linked):
    assert linked.begin_subgraph_edit(REGION)
    linked._show_region_page(_region(linked))
    panel = linked.param_panel
    assert panel.region_editing
    defn = linked.editing_definition
    panel._remove_binding("world", "hub", "zone")
    assert [b.state for b in defn.inputs[0].bindings] == ["go_to_world"]
    panel._remove_input("world")
    assert defn.inputs == []
    assert panel.region_editing, "the page is rebuilt in place"


# ---------------------------------------------------------------------------
# Insert from the browser
# ---------------------------------------------------------------------------


def test_insert_instance_from_browser_places_at_anchor(plain, library):
    rid = plain.scene.create_state_at(f"subgraph:{SYNC_ID}", QPointF(2000.0, 500.0))
    assert rid is not None
    config = plain.scene.config
    region = _region(plain, rid)
    assert region.source.subgraph_id == SYNC_ID and region.label == "Sync players"
    assert region.source.anchor == {"x": 2000.0, "y": 500.0 + NODE_H / 2}
    assert sorted(local_id(rid, mid) for mid in region.members) == sorted(LOCALS)
    by_id = {s.id: s for s in config.states}
    assert by_id[m("relog", rid)].position == {"x": 2000.0, "y": 500.0 + NODE_H / 2 + 180.0}
    assert all(plain.scene.is_locked(mid) for mid in region.members)
    assert {i.state_id for i in plain.scene.selectedItems() if hasattr(i, "state_id")} == set(region.members)
    assert plain.can_undo()
    plain.undo()
    assert not linked_regions(plain.scene.config)


def test_insert_prefills_inputs_from_defaults(plain, library):
    defn = sync_players_definition(version=2)
    defn.inputs[0].default = "Mooshu"
    library.save(defn)
    rid = plain.scene.create_state_at(f"subgraph:{SYNC_ID}", QPointF(0.0, 0.0))
    region = _region(plain, rid)
    assert region.source.inputs == {"world": "Mooshu"}
    assert _state(plain, m("go_to_world", rid)).params["world"] == "Mooshu"


def test_insert_refuses_an_unknown_subgraph(plain):
    assert plain.scene.create_state_at("subgraph:nope", QPointF(0.0, 0.0)) is None
    assert not plain.can_undo()


def test_nested_instance_round_trip(linked, library):
    """An instance inserted while editing becomes a nested region the push carries."""
    outer = SubgraphDefinition(
        id="outer",
        name="Outer",
        states=[StateModel(id="start", node_type="relog", position={"x": 0.0, "y": 0.0}, params={})],
    )
    library.save(outer)
    scene = linked.scene
    orid = scene.create_state_at("subgraph:outer", QPointF(0.0, 2000.0))
    assert linked.begin_subgraph_edit(orid)
    inner_rid = scene.insert_subgraph_instance(SYNC_ID, QPointF(500.0, 2000.0), into_region=orid)
    assert inner_rid is not None
    assert set(_region(linked, inner_rid).members) <= set(_region(linked, orid).members)
    assert linked.save_subgraph_to_library()

    pushed = library.get("outer")
    assert pushed.version == 2
    assert [r.source.subgraph_id for r in pushed.regions] == [SYNC_ID]
    config = linked.scene.config
    outer_region = _region(linked, orid)
    nested = nested_regions(config, outer_region)
    assert len(nested) == 1
    assert set(nested[0].members) <= set(outer_region.members)
    assert all(linked.scene.linked_region_of(mid).id == orid for mid in nested[0].members)
    assert linked.compose_report.regions[orid].kind == "linked"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_contains_subgraph_follows_the_library(library):
    outer = SubgraphDefinition(
        id="outer",
        name="O",
        states=[],
        regions=[
            RegionModel(id="in1", label="i", members=[], color=1, source=SubgraphSource(subgraph_id=SYNC_ID, version=1))
        ],
    )
    library.save(outer)
    assert contains_subgraph(outer, SYNC_ID, library)
    assert not contains_subgraph(outer, "outer", library)
    loop = sync_players_definition(version=2)
    loop.regions.append(
        RegionModel(id="back", label="b", members=[], color=1, source=SubgraphSource(subgraph_id="outer", version=1))
    )
    assert contains_subgraph(loop, SYNC_ID, library), "through outer, back to itself"


def test_definition_values_and_added_members(library):
    bot = linked_bot(library.get(SYNC_ID), library, inputs={"world": "Mooshu"})
    region = bot.regions[0]
    by_id = {s.id: s for s in bot.states}
    assert by_id[m("go_to_world")].params["world"] == "Mooshu"
    definition_values(bot, region, library.get(SYNC_ID))
    assert by_id[m("go_to_world")].params["world"] == "WizardCity"
    region.members.append("state_1")
    assert added_member_ids(region) == {"state_1": member_id(REGION, "state_1")}


def test_drop_states_reassigns_roles():
    bot = plain_bot()
    drop_states(bot, {"state_1", "state_2"})
    assert [s.id for s in bot.states] == ["state_3"]
    assert bot.initial == "state_3" and bot.recovery_state == "state_3"
    assert bot.transitions == []
    assert bot.regions[0].members == []
    BotConfig.model_validate(bot.model_dump())


def test_count_subgraph_uses_reads_raw_json(tmp_path, library):
    bots = tmp_path / "bots"
    (bots / "a").mkdir(parents=True)
    linked_bot(library.get(SYNC_ID), library).save(bots / "a" / "one.json")
    linked_bot(library.get(SYNC_ID), library).save(bots / "two.json")
    plain_bot().save(bots / "plain.json")
    (bots / "broken.json").write_text("{not json", encoding="utf-8")
    assert count_subgraph_uses(bots) == {SYNC_ID: 2}
    assert count_subgraph_uses(tmp_path / "missing") == {}
