"""Collapsing a plain region into one node (#67): the model, the pure rules behind it, and the canvas.

Synthetic states on one row, a grid column apart -- the shape of a long linear bot
such as a dungeon, where collapsing is meant to close the row up.
"""

from __future__ import annotations

import json

import pytest
from PyQt6.QtCore import QPointF

from bartlebot.bot.schema import (
    SUBGRAPH_ENTRY,
    SUBGRAPH_EXIT,
    BotConfig,
    RegionModel,
    StateModel,
    SubgraphSource,
    TransitionModel,
)
from bartlebot.gui.canvas.call_node_item import CollapsedRegionItem
from bartlebot.subgraphs import (
    boundary_ports,
    collapse_blocker,
    definition_ports,
    extract_definition,
    folded_regions,
)


def _t(src: str, dst: str, when: str | None = "SUCCESS") -> TransitionModel:
    return TransitionModel(trigger="next", source=src, dest=dst, when=when)


def _s(sid: str, x: float, y: float = 0.0, label: str | None = None, node_type: str = "sleep") -> StateModel:
    return StateModel(id=sid, node_type=node_type, label=label, position={"x": x, "y": y}, params={"duration": 1.0})


def linear_bot(n: int = 8) -> BotConfig:
    """``a1 -> a2 -> ... -> an`` on one row, one grid column apart, with a region over a3..a5."""
    ids = [f"a{i}" for i in range(1, n + 1)]
    return BotConfig(
        id="bot",
        name="Linear",
        initial="a1",
        recovery_state="a1",
        states=[_s(sid, 440.0 * i, label=sid.upper()) for i, sid in enumerate(ids)],
        transitions=[_t(a, b) for a, b in zip(ids, ids[1:])],
        regions=[RegionModel(id="r1", label="Middle", members=["a3", "a4", "a5"], color=2)],
    )


# ---------------------------------------------------------------------------
# The flag
# ---------------------------------------------------------------------------


def test_an_unset_flag_is_left_out_of_the_file(tmp_path):
    bot = linear_bot()
    path = tmp_path / "bot.json"
    bot.save(path)
    assert "collapsed" not in json.loads(path.read_text())["regions"][0]


def test_a_collapsed_region_round_trips(tmp_path):
    bot = linear_bot()
    bot.regions[0].collapsed = True
    path = tmp_path / "bot.json"
    bot.save(path)
    assert json.loads(path.read_text())["regions"][0]["collapsed"] is True
    assert BotConfig.load(path).regions[0].collapsed is True


# ---------------------------------------------------------------------------
# Inferred ports
# ---------------------------------------------------------------------------


def test_a_linear_region_has_one_entry_and_one_exit():
    entries, exits = boundary_ports(linear_bot(), ["a3", "a4", "a5"])
    assert [(e.port_id, e.name, e.member) for e in entries] == [("entry_a3", "A3", "a3")]
    assert [(x.port_id, x.name, x.dest, x.feeds) for x in exits] == [("exit_done", "Done", "a6", (("a5", "SUCCESS"),))]


def test_roles_name_their_entries_and_come_first():
    bot = linear_bot()
    bot.initial, bot.recovery_state = "a4", "a3"
    entries, _ = boundary_ports(bot, ["a3", "a4", "a5"])
    assert [(e.name, e.member) for e in entries] == [("Start", "a4"), ("Recover", "a3")]


def test_nothing_reaching_in_gives_one_start_into_the_first_member():
    bot = linear_bot()
    bot.transitions = [t for t in bot.transitions if t.dest != "a3"]
    entries, _ = boundary_ports(bot, ["a3", "a4", "a5"])
    assert [(e.name, e.member) for e in entries] == [("Start", "a3")]


def test_one_exit_per_destination_fed_by_every_pair_reaching_it():
    bot = linear_bot()
    bot.transitions += [_t("a3", "a1", "FAILURE"), _t("a4", "a1", "FAILURE"), _t("a4", "a6", "FAILURE")]
    _, exits = boundary_ports(bot, ["a3", "a4", "a5"])
    assert [(x.name, x.dest, x.feeds) for x in exits] == [
        ("To A6", "a6", (("a5", "SUCCESS"), ("a4", "FAILURE"))),
        ("To A1", "a1", (("a3", "FAILURE"), ("a4", "FAILURE"))),
    ]


def test_extraction_makes_the_ports_a_collapsed_region_shows():
    """A collapsed region must look exactly like the subgraph node saving it would give."""
    bot = linear_bot()
    bot.transitions += [_t("a3", "a1", "FAILURE"), _t("a4", "a6", "FAILURE")]
    entries, exits = boundary_ports(bot, bot.regions[0].members)
    defn, source = extract_definition(bot, "r1", new_id="x", name="X")
    locals_ = {"a3": "state_1", "a4": "state_2", "a5": "state_3"}
    assert [(s.id, s.label) for s in defn.states if s.node_type == SUBGRAPH_ENTRY] == [
        (e.port_id, e.name) for e in entries
    ]
    assert [(s.id, s.label) for s in defn.states if s.node_type == SUBGRAPH_EXIT] == [
        (x.port_id, x.name) for x in exits
    ]
    ports = definition_ports(defn)
    assert [ports[e.port_id].member for e in entries] == [locals_[e.member] for e in entries]
    assert [ports[x.port_id].from_ for x in exits] == [[(locals_[m], w) for m, w in x.feeds] for x in exits]
    assert source.ports == ports


# ---------------------------------------------------------------------------
# What may collapse
# ---------------------------------------------------------------------------


def _with_regions(*regions: RegionModel) -> BotConfig:
    bot = linear_bot()
    bot.regions = list(regions)
    return bot


def test_a_plain_region_may_collapse():
    assert collapse_blocker(linear_bot(), "r1") is None


def test_partly_overlapping_a_collapsed_region_is_refused():
    bot = _with_regions(
        RegionModel(id="r1", label="Middle", members=["a3", "a4", "a5"], color=2, collapsed=True),
        RegionModel(id="r2", label="Late", members=["a5", "a6"], color=3),
    )
    assert collapse_blocker(bot, "r2") == "It overlaps the collapsed region 'Middle'."


def test_nesting_either_way_and_disjoint_regions_may_collapse():
    bot = _with_regions(
        RegionModel(id="outer", label="Outer", members=["a2", "a3", "a4", "a5", "a6"], color=2, collapsed=True),
        RegionModel(id="inner", label="Inner", members=["a3", "a4"], color=3, collapsed=True),
        RegionModel(id="apart", label="Apart", members=["a7", "a8"], color=4),
    )
    assert collapse_blocker(bot, "inner") is None
    assert collapse_blocker(bot, "outer") is None
    assert collapse_blocker(bot, "apart") is None


def test_an_expanded_region_does_not_block_an_overlap():
    bot = _with_regions(
        RegionModel(id="r1", label="Middle", members=["a3", "a4", "a5"], color=2),
        RegionModel(id="r2", label="Late", members=["a5", "a6"], color=3),
    )
    assert collapse_blocker(bot, "r2") is None


def test_a_linked_region_is_refused():
    bot = linear_bot()
    bot.regions[0].source = SubgraphSource(subgraph_id="s", version=1)
    assert collapse_blocker(bot, "r1") == "A subgraph node is already drawn as one node."


def test_holding_part_of_a_subgraph_node_is_refused():
    bot = _with_regions(
        RegionModel(
            id="inst", label="Sync", members=["a2", "a3"], color=1, source=SubgraphSource(subgraph_id="s", version=1)
        ),
        RegionModel(id="r1", label="Middle", members=["a3", "a4"], color=2),
    )
    assert collapse_blocker(bot, "r1") == "It holds only part of the subgraph node 'Sync'."


def test_a_region_holding_a_port_node_is_refused():
    bot = linear_bot()
    bot.states[3] = _s("a4", 1320.0, node_type=SUBGRAPH_ENTRY)
    assert collapse_blocker(bot, "r1") == "It holds an Entry or Exit node, which the signature needs to show."


# ---------------------------------------------------------------------------
# What is drawn folded
# ---------------------------------------------------------------------------


def test_a_plain_region_folds_only_when_collapsed():
    bot = linear_bot()
    assert folded_regions(bot) == []
    bot.regions[0].collapsed = True
    assert [r.id for r in folded_regions(bot)] == ["r1"]
    assert folded_regions(bot, expanded=["r1"]) == []


def test_only_the_outermost_folds_and_an_instance_inside_goes_with_it():
    bot = _with_regions(
        RegionModel(
            id="inst", label="Sync", members=["a3", "a4"], color=1, source=SubgraphSource(subgraph_id="s", version=1)
        ),
        RegionModel(id="inner", label="Inner", members=["a3", "a4", "a5"], color=3, collapsed=True),
        RegionModel(id="outer", label="Outer", members=["a2", "a3", "a4", "a5", "a6"], color=2, collapsed=True),
    )
    assert [r.id for r in folded_regions(bot)] == ["outer"]
    assert [r.id for r in folded_regions(bot, expanded=["outer"])] == ["inner"]
    assert [r.id for r in folded_regions(bot, expanded=["outer", "inner"])] == ["inst"]


def test_a_hand_edited_partial_overlap_folds_the_larger_and_draws_the_other_expanded():
    bot = _with_regions(
        RegionModel(id="small", label="Small", members=["a5", "a6"], color=3, collapsed=True),
        RegionModel(id="big", label="Big", members=["a2", "a3", "a4", "a5"], color=2, collapsed=True),
    )
    assert [r.id for r in folded_regions(bot)] == ["big"]


def test_a_collapse_partly_across_an_instance_is_drawn_expanded():
    bot = _with_regions(
        RegionModel(
            id="inst", label="Sync", members=["a2", "a3"], color=1, source=SubgraphSource(subgraph_id="s", version=1)
        ),
        RegionModel(id="r1", label="Middle", members=["a3", "a4", "a5"], color=2, collapsed=True),
    )
    assert [r.id for r in folded_regions(bot)] == ["inst"]


# ---------------------------------------------------------------------------
# On the canvas
# ---------------------------------------------------------------------------

W = 440.0


@pytest.fixture
def canvas(qapp, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox  # noqa: PLC0415

    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.No)
    c = BotCanvas()
    c.setup(linear_bot())
    c.enter_edit_mode()
    yield c
    c.prepare_for_shutdown()


def _positions(config: BotConfig) -> dict[str, dict]:
    return {s.id: dict(s.position) for s in config.states}


def _edge(canvas, src, when, dst):
    return next(e for s, w, d, e in canvas.scene.edge_items if (s, w, d) == (src, when, dst))


def _drawn_x(canvas, did: str) -> float:
    return canvas.scene.node_items[did].pos().x()


def test_collapsing_draws_one_node_and_moves_nothing_stored(canvas):
    before = _positions(canvas.config)
    assert canvas.set_region_collapsed("r1", True)
    items = canvas.scene.node_items
    assert isinstance(items["r1"], CollapsedRegionItem)
    assert not {"a3", "a4", "a5"} & set(items)
    assert "r1" not in canvas.scene.region_items
    assert _positions(canvas.config) == before
    assert canvas.config.regions[0].collapsed is True


def test_the_space_the_region_used_closes_up(canvas):
    canvas.set_region_collapsed("r1", True)
    assert _drawn_x(canvas, "r1") == 2 * W  # where a3, its top-left member, was
    assert [_drawn_x(canvas, s) for s in ("a6", "a7", "a8")] == [3 * W, 4 * W, 5 * W]
    assert _drawn_x(canvas, "a2") == W


def test_expanding_restores_the_layout_exactly(canvas):
    canvas.set_region_collapsed("r1", True)
    assert canvas.set_region_collapsed("r1", False)
    assert canvas.config.regions[0].collapsed is None
    assert [_drawn_x(canvas, f"a{i}") for i in range(1, 9)] == [i * W for i in range(8)]


def test_undo_of_a_collapse_restores_the_unfolded_view(canvas):
    canvas.set_region_collapsed("r1", True)
    canvas.undo()
    assert canvas.config.regions[0].collapsed is None
    assert "a4" in canvas.scene.node_items
    assert _drawn_x(canvas, "a6") == 5 * W


def test_crossing_edges_land_on_the_inferred_ports(canvas):
    canvas.set_region_collapsed("r1", True)
    node = canvas.scene.node_items["r1"]
    assert node.entries == [("entry_a3", "A3")]
    assert node.exits == [("exit_done", "Done")]
    assert _edge(canvas, "a2", "SUCCESS", "r1").dst_port == "entry_a3"
    out = _edge(canvas, "r1", "exit_done", "a6")
    assert [(t.source, t.dest) for t in canvas.scene.transitions_of(out)] == [("a5", "a6")]


def test_wiring_into_an_entry_and_out_of_an_exit_adds_the_flat_transitions(canvas):
    canvas.set_region_collapsed("r1", True)
    scene = canvas.scene
    scene._create_transition(scene.node_items["a8"], scene.node_items["r1"], "SUCCESS", dst_port="entry_a3")
    assert any((t.source, t.dest, t.when) == ("a8", "a3", "SUCCESS") for t in canvas.config.transitions)
    scene._create_transition(scene.node_items["r1"], scene.node_items["a1"], "exit_done")
    assert any((t.source, t.dest, t.when) == ("a5", "a1", "SUCCESS") for t in canvas.config.transitions)
    # A second destination: the node now shows an exit for each.
    assert [name for _pid, name in scene.node_items["r1"].exits] == ["To A6", "To A1"]


def test_deleting_a_crossing_edge_redraws_the_ports(canvas):
    canvas.set_region_collapsed("r1", True)
    scene = canvas.scene
    scene._create_transition(scene.node_items["r1"], scene.node_items["a1"], "exit_done")
    assert len(scene.node_items["r1"].exits) == 2
    scene._delete_edge_item(_edge(canvas, "r1", "exit_to_a1", "a1"), confirm=False)
    assert scene.node_items["r1"].exits == [("exit_done", "Done")]


def test_moving_the_collapsed_node_moves_its_members(canvas):
    canvas.set_region_collapsed("r1", True)
    node = canvas.scene.node_items["r1"]
    node.setPos(node.pos() + QPointF(0, 2 * 90.0))
    canvas.scene.flush_positions()
    by_id = {s.id: s.position for s in canvas.config.states}
    assert [by_id[s]["y"] for s in ("a3", "a4", "a5")] == [180.0, 180.0, 180.0]
    assert [by_id[s]["x"] for s in ("a3", "a4", "a5")] == [2 * W, 3 * W, 4 * W]
    assert by_id["a6"] == {"x": 5 * W, "y": 0.0}


def test_a_drop_in_the_folded_view_is_stored_on_a_kept_column(canvas):
    canvas.set_region_collapsed("r1", True)
    node = canvas.scene.node_items["a8"]
    node.setPos(QPointF(3 * W, 2 * 90.0 - node.body_height / 2))  # drawn where a6 is, two rows down
    canvas.scene.flush_positions()
    assert next(s for s in canvas.config.states if s.id == "a8").position == {"x": 5 * W, "y": 180.0}


def test_deleting_the_collapsed_node_deletes_what_it_holds(canvas):
    canvas.set_region_collapsed("r1", True)
    assert canvas.scene.remove_instance("r1", confirm=False)
    assert {s.id for s in canvas.config.states} == {"a1", "a2", "a6", "a7", "a8"}
    assert canvas.config.regions == []


def test_removing_the_region_brings_its_nodes_back(canvas):
    canvas.set_region_collapsed("r1", True)
    canvas.scene._remove_region("r1")
    assert {"a3", "a4", "a5"} <= set(canvas.scene.node_items)
    assert _drawn_x(canvas, "a6") == 5 * W


def test_a_members_warning_rolls_up_unfiltered(canvas):
    canvas.config.transitions = [t for t in canvas.config.transitions if t.dest != "a3"]
    canvas.scene.setup(canvas.config)
    canvas.set_region_collapsed("r1", True)
    warnings = canvas.validation_warnings()
    assert any(line.startswith("Middle › A4:") and "Unreachable" in line for line in warnings)


def test_a_running_member_lights_the_collapsed_node(canvas):
    canvas.set_region_collapsed("r1", True)
    canvas.exit_edit_mode()
    node = canvas.scene.node_items["r1"]
    canvas.set_state("a4")
    assert node.active_member == "A4"
    canvas.set_state("a7")
    assert node.active_member is None


def test_search_finds_a_member_as_the_collapsed_node(canvas):
    canvas.set_region_collapsed("r1", True)
    canvas._show_search_bar()
    canvas._on_search_text_changed("a4")
    assert canvas._search_matches == ["r1"]


def test_stepping_in_draws_the_members_where_they_are_and_esc_folds_them_again(canvas):
    canvas.set_region_collapsed("r1", True)
    canvas.enter_focus_mode("r1")
    items = canvas.scene.node_items
    assert {"a3", "a4", "a5"} <= set(items) and "r1" not in items
    assert [_drawn_x(canvas, s) for s in ("a3", "a4", "a5")] == [2 * W, 3 * W, 4 * W]
    assert items["a4"].isVisible() and not items["a7"].isVisible()
    assert canvas.config.regions[0].collapsed is True
    assert canvas._on_escape()
    assert isinstance(canvas.scene.node_items["r1"], CollapsedRegionItem)
    assert _drawn_x(canvas, "a6") == 3 * W


def test_stepping_in_works_in_run_mode_too(canvas):
    canvas.set_region_collapsed("r1", True)
    canvas.exit_edit_mode()
    canvas.enter_focus_mode("r1")
    assert "a4" in canvas.scene.node_items
    canvas.set_state("a4")
    assert canvas.scene.node_items["a4"].is_active


def test_a_collapsed_region_inside_a_focused_one_pushes_a_level(canvas):
    canvas.config.regions.append(RegionModel(id="r0", label="Outer", members=["a2", "a3", "a4", "a5", "a6"], color=3))
    canvas.scene.setup(canvas.config)
    canvas.set_region_collapsed("r1", True)
    canvas.enter_focus_mode("r0")
    canvas.enter_focus_mode("r1")
    assert [lv.region_id for lv in canvas._levels] == ["r0", "r1"]
    canvas.step_out()
    assert isinstance(canvas.scene.node_items["r1"], CollapsedRegionItem)
    assert canvas.focus_region_id == "r0"


def test_the_region_page_collapses_and_expands(canvas):
    canvas.scene.clearSelection()
    for sid in ("a3", "a4", "a5"):
        canvas.scene.node_items[sid].setSelected(True)
    panel = canvas.param_panel
    assert panel.shown_region is canvas.config.regions[0]
    assert panel.region_collapse_button.text() == "Collapse"
    panel.region_collapse_button.click()
    assert canvas.config.regions[0].collapsed is True
    assert panel.region_collapse_button.text() == "Expand"
    panel.region_collapse_button.click()
    assert canvas.config.regions[0].collapsed is None


def test_copy_paste_of_a_collapsed_node_pastes_a_collapsed_region(canvas):
    canvas.set_region_collapsed("r1", True)
    scene = canvas.scene
    scene.clearSelection()
    scene.node_items["r1"].setSelected(True)
    scene._copy_selected()
    scene._paste()
    plain = [r for r in canvas.config.regions if r.source is None]
    assert len(plain) == 2
    pasted = next(r for r in plain if r.id != "r1")
    assert pasted.collapsed is True and pasted.label == "Middle" and len(pasted.members) == 3
    assert not set(pasted.members) & {"a3", "a4", "a5"}
    assert isinstance(scene.node_items[pasted.id], CollapsedRegionItem)
    assert scene.node_items[pasted.id].isSelected()
    # Its internal edges came with it; nothing crosses into the copy.
    inner = [t for t in canvas.config.transitions if t.source in pasted.members and t.dest in pasted.members]
    assert len(inner) == 2


def test_a_paste_never_lands_on_a_collapsed_regions_hidden_members(canvas):
    canvas.set_region_collapsed("r1", True)
    scene = canvas.scene
    scene.clearSelection()
    scene.node_items["a1"].setSelected(True)
    scene._copy_selected()
    scene._paste()
    new = [s for s in canvas.config.states if s.id not in {f"a{i}" for i in range(1, 9)}]
    cells = {(round(s.position["x"] / W), round(s.position["y"] / 90.0)) for s in canvas.config.states if s not in new}
    assert all((round(s.position["x"] / W), round(s.position["y"] / 90.0)) not in cells for s in new)


# ---------------------------------------------------------------------------
# Auto-layout
# ---------------------------------------------------------------------------


def _cols(config: BotConfig) -> dict[str, int]:
    return {s.id: round(s.position["x"] / W) for s in config.states}


def test_auto_layout_places_a_collapsed_region_as_one_node_and_inserts_its_columns():
    from bartlebot.gui.canvas.layout import auto_layout  # noqa: PLC0415

    bot = linear_bot()
    bot.regions[0].collapsed = True
    for s in bot.states:  # scramble what the layout will replace; members keep their shape
        s.position = {"x": s.position["x"] + (0.0 if s.id in {"a3", "a4", "a5"} else 7 * W), "y": 270.0}
    auto_layout(bot)
    assert _cols(bot) == {"a1": 0, "a2": 1, "a3": 2, "a4": 3, "a5": 4, "a6": 5, "a7": 6, "a8": 7}


def test_the_inserted_columns_hold_members_only_so_the_fold_closes_them():
    from bartlebot.gui.canvas.fold import FoldMap  # noqa: PLC0415
    from bartlebot.gui.canvas.layout import auto_layout  # noqa: PLC0415

    bot = linear_bot()
    bot.regions[0].collapsed = True
    # A branch beside the region: a2 also leads to b, which rejoins at a6.
    bot.states.append(_s("b", 0.0, label="B"))
    bot.transitions += [_t("a2", "b", "FAILURE"), _t("b", "a6")]
    auto_layout(bot)
    cols = _cols(bot)
    region_cols = {cols[m] for m in ("a3", "a4", "a5")}
    inserted = region_cols - {cols["a3"]}
    assert not any(cols[s] in inserted for s in cols if s not in {"a3", "a4", "a5"})
    base = FoldMap(W, 90.0)
    span = base.span_of([(s.position["x"], s.position["y"]) for s in bot.states if s.id in {"a3", "a4", "a5"}])
    visible = [(s.position["x"], s.position["y"], 70.0) for s in bot.states if s.id not in {"a3", "a4", "a5"}]
    fold = FoldMap.build(
        [span], visible + [(bot.states[2].position["x"], bot.states[2].position["y"], 70.0)], col_w=W, row_h=90.0
    )
    assert set(fold.hidden_cols) == inserted


def test_a_blocked_collapse_is_refused_and_the_button_says_why(canvas):
    canvas.config.regions.append(RegionModel(id="r2", label="Late", members=["a5", "a6"], color=3))
    canvas.scene.setup(canvas.config)
    canvas.set_region_collapsed("r1", True)
    assert not canvas.set_region_collapsed("r2", True)
    assert canvas.config.regions[1].collapsed is None
    # Half of r2 is folded into r1, so it cannot be selected whole; its page is shown directly.
    canvas._show_region_page(canvas.config.regions[1])
    panel = canvas.param_panel
    assert not panel.region_collapse_button.isEnabled()
    assert panel.region_collapse_button.toolTip() == "It overlaps the collapsed region 'Middle'."
