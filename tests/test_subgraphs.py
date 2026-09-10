"""Tests for bartlebot.subgraphs — composing instances into a bot and extracting them back out.

The fixture definition is a synthetic seven-node "sync players" chain shaped like the
boss template's region (relog → form_group → two teleports → go_to_world → hub →
wisps). Ids, zones and worlds are invented; nothing here is a real character.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from bartlebot.bot.schema import (
    BotConfig,
    RegionModel,
    StateModel,
    SubgraphDefinition,
    SubgraphInput,
    SubgraphInputBinding,
    SubgraphSource,
    TransitionModel,
)
from bartlebot.store_roots import Origin
from bartlebot.subgraph_store import SubgraphLibrary
from bartlebot.subgraphs import (
    MAX_DEPTH,
    ComposeReport,
    SubgraphCycleError,
    compose_bot,
    convert_region_to_instance,
    detach,
    external_edges,
    extract_definition,
    linked_regions,
    local_id,
    materialize_region,
    member_id,
    push_back,
    resolve_member_params,
)

_REPO = Path(__file__).parent.parent
_BOTS_DIR = _REPO / "bots"

SYNC_ID = "sync-players"
REGION = "9f59026b"


def _t(src: str, dst: str, when: str | None = "SUCCESS") -> TransitionModel:
    return TransitionModel(trigger="next", source=src, dest=dst, when=when)


def sync_players_definition(version: int = 1, world: str = "WizardCity") -> SubgraphDefinition:
    """Seven nodes, local ids, positions relative to (0, 0)."""
    return SubgraphDefinition(
        id=SYNC_ID,
        name="Sync players",
        description="Regroup every client at the world hub.",
        version=version,
        inputs=[
            SubgraphInput(
                name="world",
                label="World",
                type="str",
                bindings=[
                    SubgraphInputBinding(state="go_to_world", param="world"),
                    SubgraphInputBinding(state="hub", param="zone"),
                ],
            )
        ],
        states=[
            StateModel(id="relog", node_type="relog", position={"x": 0.0, "y": 180.0}, params={"relog_clients": []}),
            StateModel(
                id="form_group",
                node_type="form_group",
                position={"x": 440.0, "y": 180.0},
                params={"leader_index": 0, "require_all": True},
            ),
            StateModel(
                id="tp_a",
                node_type="teleport_to_player_if_in_zone",
                position={"x": 880.0, "y": 0.0},
                params={"target_zone": "DragonSpyre/DS_Hub", "skip_extra_clients_in_zone": True},
            ),
            StateModel(
                id="tp_b",
                node_type="teleport_to_player_if_in_zone",
                position={"x": 1320.0, "y": 0.0},
                params={"target_zone": f"{world}/Hub", "skip_extra_clients_in_zone": True},
            ),
            StateModel(
                id="go_to_world", node_type="go_to_world", position={"x": 1760.0, "y": 90.0}, params={"world": world}
            ),
            StateModel(
                id="hub",
                node_type="teleport_to_world_hub",
                position={"x": 2200.0, "y": 0.0},
                params={"zone": f"{world}/Hub"},
            ),
            StateModel(
                id="wisps",
                node_type="collect_wisps",
                position={"x": 2640.0, "y": 0.0},
                params={"timeout": 30, "only_safe": True},
            ),
        ],
        transitions=[
            _t("relog", "relog", "FAILURE"),
            _t("relog", "form_group"),
            _t("form_group", "relog", "FAILURE"),
            _t("form_group", "tp_a"),
            _t("tp_a", "tp_b", "FAILURE"),
            _t("tp_b", "go_to_world", "FAILURE"),
            _t("tp_b", "hub"),
            _t("go_to_world", "hub"),
            _t("go_to_world", "tp_b", "FAILURE"),
            _t("hub", "wisps"),
            _t("hub", "relog", "FAILURE"),
            _t("wisps", "hub", "FAILURE"),
        ],
    )


def m(local: str, region: str = REGION) -> str:
    return member_id(region, local)


def linked_bot(defn: SubgraphDefinition, library: SubgraphLibrary, **source_kw) -> BotConfig:
    """A bot holding one instance of ``defn`` plus two external nodes wired into it.

    Built by composing, so its snapshot is exactly what ``defn`` produces; tests
    then edit the snapshot or the library to make the two disagree.
    """
    source = SubgraphSource(subgraph_id=defn.id, version=defn.version, anchor={"x": -880.0, "y": 90.0}, **source_kw)
    mat = materialize_region(REGION, source, defn, library)
    external = [
        StateModel(id="state_1", node_type="enter_zone", position={"x": 3080.0, "y": 90.0}, params={"to_zone": "X/Y"}),
        StateModel(id="state_2", node_type="handle_potions", position={"x": 3520.0, "y": 90.0}, params={}),
    ]
    edges = [
        _t(m("wisps"), "state_1"),  # leaves the region
        _t(m("tp_a"), "state_2"),  # a second exit, from a different member
        _t("state_1", m("hub"), "FAILURE"),  # re-enters the region
        _t("state_1", "state_2"),
        _t("state_2", m("relog"), "FAILURE"),
    ]
    return BotConfig(
        id="bot-1",
        name="Linked",
        initial=m("relog"),
        recovery_state=m("relog"),
        states=mat.states + external,
        transitions=mat.transitions + edges,
        regions=[
            RegionModel(id=REGION, label="Sync players", members=mat.member_ids, color=2, source=source),
            RegionModel(id="aabbccdd", label="Farm", members=["state_1", "state_2"], color=1),
        ],
    )


@pytest.fixture
def library(tmp_path) -> SubgraphLibrary:
    lib = SubgraphLibrary(tmp_path)
    lib.save(sync_players_definition())
    return lib


@pytest.fixture
def bot(library) -> BotConfig:
    return linked_bot(library.get(SYNC_ID), library)


# ---------------------------------------------------------------------------
# Schema and invariants
# ---------------------------------------------------------------------------


def test_region_without_source_dumps_exactly_as_before():
    region = RegionModel(id="r1", label="L", members=["a", "b"], color=3)
    assert region.model_dump(exclude_none=True) == {"id": "r1", "label": "L", "members": ["a", "b"], "color": 3}


def test_undo_round_trip_preserves_source(bot):
    """UndoManager round-trips model_dump → model_validate on every edit."""
    again = BotConfig.model_validate(bot.model_dump())
    assert again == bot
    assert again.regions[0].source is not None
    assert again.regions[0].source.anchor == {"x": -880.0, "y": 90.0}


def test_definition_rejects_a_transition_that_leaves_it():
    with pytest.raises(ValueError, match="leaves the definition"):
        SubgraphDefinition(
            id="d", name="D", states=[StateModel(id="a", node_type="relog")], transitions=[_t("a", "elsewhere")]
        )


def test_definition_rejects_a_non_local_id():
    with pytest.raises(ValueError, match="not a valid subgraph-local id"):
        SubgraphDefinition(id="d", name="D", states=[StateModel(id="a-b", node_type="relog")])


def test_definition_rejects_a_binding_to_an_unknown_state():
    with pytest.raises(ValueError, match="binds to unknown state"):
        SubgraphDefinition(
            id="d",
            name="D",
            states=[StateModel(id="a", node_type="relog")],
            inputs=[SubgraphInput(name="w", type="str", bindings=[SubgraphInputBinding(state="zz", param="p")])],
        )


def test_definition_round_trips_through_its_file(tmp_path):
    defn = sync_players_definition()
    defn.save(tmp_path / "d.json")
    assert SubgraphDefinition.load(tmp_path / "d.json") == defn
    assert "recovery_state" not in json.loads((tmp_path / "d.json").read_text(encoding="utf-8"))


def test_compose_is_identity_for_unlinked_config(tmp_path):
    cfg = BotConfig(
        id="b",
        name="B",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="relog")],
        transitions=[],
        regions=[RegionModel(id="r", label="R", members=["a"], color=1)],
    )
    composed, report = compose_bot(cfg, SubgraphLibrary(tmp_path))
    assert composed is cfg
    assert report == ComposeReport()


def _all_bot_files() -> list[Path]:
    files = sorted(_BOTS_DIR.rglob("*.json"))
    assert files
    return files


def test_every_shipped_bot_composes_to_itself(tmp_path):
    """Nothing in the library links a subgraph yet, so composition must be a no-op
    on every file — same object back, empty report, identical machine config."""
    lib = SubgraphLibrary(tmp_path)
    for path in _all_bot_files():
        cfg = BotConfig.load(path)
        before = cfg.to_machine_config()
        composed, report = compose_bot(cfg, lib)
        assert composed is cfg, path.name
        assert report.regions == {}, path.name
        assert composed.to_machine_config() == before, path.name


def test_no_shipped_bot_uses_the_sg_prefix_unlinked():
    """The derived-id prefix is reserved: a hand-written ``sg_`` id would collide with
    the first instance that composes next to it."""
    offenders = [
        f"{p.name}:{s.id}" for p in _all_bot_files() for s in BotConfig.load(p).states if s.id.startswith("sg_")
    ]
    assert offenders == []


def test_member_ids_never_collide_with_unique_id():
    """The editor mints ``state_N``; every member id carries the ``sg_`` prefix."""
    from bartlebot.gui.canvas.edit_scene import _unique_id

    existing = [m("relog"), m("state_7"), "state_3"]
    assert _unique_id(existing) == "state_4"
    assert not any(i.startswith("state_") for i in existing[:2])
    assert local_id(REGION, m("state_7")) == "state_7"
    assert local_id(REGION, "state_3") is None
    assert local_id("other", m("relog")) is None


def test_subgraph_modules_import_no_qt():
    """The policy stays testable without a QApplication or settings, like bot_options."""
    forbidden: list[str] = []
    for name in ("subgraphs.py", "subgraph_store.py", "store_roots.py"):
        tree = ast.parse((_REPO / "bartlebot" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for mod in names:
                if mod.startswith(("bartlebot.gui", "PyQt6")) or "settings" in mod:
                    forbidden.append(f"{name} imports {mod}")
    assert forbidden == []


# ---------------------------------------------------------------------------
# Params
# ---------------------------------------------------------------------------


def test_resolve_param_override_beats_input_beats_definition():
    defn = sync_players_definition()
    hub = next(s for s in defn.states if s.id == "hub")
    source = SubgraphSource(
        subgraph_id=SYNC_ID, version=1, inputs={"world": "Krokotopia"}, param_overrides={"hub": {"zone": "Override/Z"}}
    )
    assert resolve_member_params(defn, hub, source)["zone"] == "Override/Z"
    source.param_overrides = {}
    assert resolve_member_params(defn, hub, source)["zone"] == "Krokotopia"
    source.inputs = {}
    assert resolve_member_params(defn, hub, source)["zone"] == "WizardCity/Hub"


def test_unset_input_leaves_definition_value():
    defn = sync_players_definition()
    go = next(s for s in defn.states if s.id == "go_to_world")
    assert (
        resolve_member_params(defn, go, SubgraphSource(subgraph_id=SYNC_ID, version=1, inputs={"world": None}))["world"]
        == "WizardCity"
    )


def test_a_set_input_reaches_every_bound_member(library):
    bot = linked_bot(library.get(SYNC_ID), library, inputs={"world": "Marleybone"})
    by_id = {s.id: s for s in bot.states}
    assert by_id[m("go_to_world")].params["world"] == "Marleybone"
    assert by_id[m("hub")].params["zone"] == "Marleybone"
    assert by_id[m("tp_b")].params["target_zone"] == "WizardCity/Hub"  # not bound; definition value


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_compose_replaces_snapshot_with_current_definition(bot, library):
    library.save(sync_players_definition(version=2, world="Mooshu"))
    composed, report = compose_bot(bot, library)

    assert composed is not bot
    assert bot.regions[0].source.version == 1, "the input is never mutated"
    assert composed.regions[0].source.version == 2
    assert report.regions[REGION].kind == "linked"
    assert report.regions[REGION].library_version == 2
    assert report.regions[REGION].origin is Origin.USER
    by_id = {s.id: s for s in composed.states}
    assert by_id[m("go_to_world")].params["world"] == "Mooshu"
    assert composed.regions[0].members == bot.regions[0].members
    assert composed.initial == m("relog")


def test_compose_keeps_existing_positions_and_places_new_members_from_anchor(bot, library):
    defn = sync_players_definition(version=2)
    defn.states.append(StateModel(id="extra", node_type="quicksell", position={"x": 100.0, "y": 10.0}, params={}))
    defn.transitions.append(_t("wisps", "extra"))
    library.save(defn)
    moved = next(s for s in bot.states if s.id == m("hub"))
    moved.position = {"x": 5.0, "y": 5.0}

    composed, _ = compose_bot(bot, library)
    by_id = {s.id: s for s in composed.states}
    assert by_id[m("hub")].position == {"x": 5.0, "y": 5.0}
    assert by_id[m("extra")].position == {"x": -780.0, "y": 100.0}  # anchor (-880, 90) + (100, 10)
    assert m("extra") in composed.regions[0].members


def test_compose_keeps_external_edges(bot, library):
    library.save(sync_players_definition(version=2))
    composed, _ = compose_bot(bot, library)
    before = {(t.source, t.dest, t.when) for t in external_edges(bot, bot.regions[0])}
    after = {(t.source, t.dest, t.when) for t in external_edges(composed, composed.regions[0])}
    assert before == after
    assert len(before) == 4
    # and the unrelated edge between the two external nodes is untouched
    assert any(t.source == "state_1" and t.dest == "state_2" for t in composed.transitions)


def test_compose_preserves_state_order_around_the_instance(bot, library):
    library.save(sync_players_definition(version=2))
    composed, _ = compose_bot(bot, library)
    assert [s.id for s in composed.states] == [s.id for s in bot.states]


def test_compose_held_when_initial_would_vanish(bot, library):
    defn = sync_players_definition(version=2)
    defn.states = [s for s in defn.states if s.id != "relog"]
    defn.transitions = [t for t in defn.transitions if "relog" not in (t.source, t.dest)]
    library.save(defn)

    composed, report = compose_bot(bot, library)
    status = report.regions[REGION]
    assert status.kind == "held"
    assert any("initial state" in r for r in status.reasons)
    assert any("recovery state" in r for r in status.reasons)
    assert composed.regions[0].source.version == 1
    assert composed.model_dump() == bot.model_dump()


def test_compose_held_when_external_edge_endpoint_would_vanish(bot, library):
    defn = sync_players_definition(version=2)
    defn.states = [s for s in defn.states if s.id != "tp_a"]
    defn.transitions = [t for t in defn.transitions if "tp_a" not in (t.source, t.dest)]
    defn.transitions.append(_t("form_group", "tp_b"))
    library.save(defn)

    composed, report = compose_bot(bot, library)
    status = report.regions[REGION]
    assert status.kind == "held"
    assert status.reasons == [f"the update removes '{m('tp_a')}', which has an edge to the rest of the bot"]
    assert composed.model_dump() == bot.model_dump()


def test_compose_missing_definition_keeps_snapshot_and_flags(bot, library):
    library.delete(SYNC_ID)
    composed, report = compose_bot(bot, library)
    assert report.regions[REGION].kind == "missing"
    assert report.regions[REGION].origin is Origin.MISSING
    assert composed.model_dump() == bot.model_dump()
    assert not report.has_errors


def test_compose_invalid_definition_keeps_snapshot(bot, library, tmp_path):
    (tmp_path / "subgraphs" / f"{SYNC_ID}.json").write_text('{"id": "sync-players", "name": 3}', encoding="utf-8")
    library.reload()
    composed, report = compose_bot(bot, library)
    assert report.regions[REGION].kind == "invalid"
    assert report.regions[REGION].reasons
    assert composed.model_dump() == bot.model_dump()


def test_stale_override_is_kept_and_reported(library):
    bot = linked_bot(library.get(SYNC_ID), library, param_overrides={"gone": {"x": 1}, "hub": {"nope": 2}})
    composed, report = compose_bot(bot, library)
    assert composed.regions[0].source.param_overrides == {"gone": {"x": 1}, "hub": {"nope": 2}}
    assert report.regions[REGION].kind == "linked"
    assert len(report.regions[REGION].reasons) == 2
    assert {s.id: s for s in composed.states}[m("hub")].params["nope"] == 2


def test_compose_output_validates_as_a_bot(bot, library):
    library.save(sync_players_definition(version=2))
    composed, _ = compose_bot(bot, library)
    assert BotConfig.model_validate(composed.model_dump()) == composed


# ---------------------------------------------------------------------------
# Nesting
# ---------------------------------------------------------------------------


def _outer_definition(inner_id: str = SYNC_ID) -> SubgraphDefinition:
    """One node of its own, plus a nested instance of ``inner_id`` at (1000, 0)."""
    return SubgraphDefinition(
        id="outer",
        name="Outer",
        states=[StateModel(id="start", node_type="relog", position={"x": 0.0, "y": 0.0}, params={})],
        transitions=[],
        regions=[
            RegionModel(
                id="in1",
                label="Inner",
                members=[],
                color=3,
                source=SubgraphSource(subgraph_id=inner_id, version=1, anchor={"x": 1000.0, "y": 0.0}),
            )
        ],
    )


def test_nested_instance_ids_are_prefixed_twice(library):
    library.save(_outer_definition())
    source = SubgraphSource(subgraph_id="outer", version=1, anchor={"x": 10.0, "y": 20.0})
    mat = materialize_region("R", source, library.get("outer"), library)

    assert member_id("R", "start") in mat.member_ids
    nested_hub = member_id("R", member_id("in1", "hub"))
    assert nested_hub == "sg_R_sg_in1_hub"
    assert nested_hub in mat.member_ids
    assert [r.id for r in mat.regions] == [member_id("R", "in1")]
    assert nested_hub in mat.regions[0].members
    # positions: outer anchor + inner anchor + definition position
    by_id = {s.id: s for s in mat.states}
    assert by_id[nested_hub].position == {"x": 10.0 + 1000.0 + 2200.0, "y": 20.0 + 0.0 + 0.0}
    # the nested region's anchor is absolute in the bot
    assert mat.regions[0].source.anchor == {"x": 1010.0, "y": 20.0}


def test_outer_overrides_reach_nested_members(library):
    library.save(_outer_definition())
    source = SubgraphSource(
        subgraph_id="outer", version=1, param_overrides={member_id("in1", "hub"): {"zone": "Deep/Z"}}
    )
    mat = materialize_region("R", source, library.get("outer"), library)
    assert {s.id: s for s in mat.states}["sg_R_sg_in1_hub"].params["zone"] == "Deep/Z"
    assert mat.warnings == []


def test_cycle_is_reported_not_raised(library):
    library.save(_outer_definition(inner_id="loop"))
    loop = _outer_definition(inner_id="outer")
    loop.id = "loop"
    library.save(loop)

    source = SubgraphSource(subgraph_id="outer", version=1)
    with pytest.raises(SubgraphCycleError) as exc:
        materialize_region("R", source, library.get("outer"), library)
    assert exc.value.path == ["outer", "loop", "outer"]

    bot = BotConfig(
        id="b",
        name="B",
        initial="x",
        recovery_state="x",
        states=[StateModel(id="x", node_type="relog")],
        transitions=[],
        regions=[RegionModel(id="R", label="Outer", members=[], color=1, source=source)],
    )
    composed, report = compose_bot(bot, library)
    assert report.regions["R"].kind == "cycle"
    assert report.has_errors
    assert composed.model_dump() == bot.model_dump()


def test_depth_limit(library):
    for i in range(MAX_DEPTH + 2):
        d = _outer_definition(inner_id=f"chain{i + 1}")
        d.id = f"chain{i}"
        library.save(d)
    tail = _outer_definition()
    tail.id = f"chain{MAX_DEPTH + 2}"
    tail.regions = []
    library.save(tail)

    with pytest.raises(SubgraphCycleError):
        materialize_region("R", SubgraphSource(subgraph_id="chain0", version=1), library.get("chain0"), library)
    # a chain inside the limit composes
    materialize_region(
        "R",
        SubgraphSource(subgraph_id=f"chain{MAX_DEPTH - 1}", version=1),
        library.get(f"chain{MAX_DEPTH - 1}"),
        library,
    )


def test_nested_missing_definition_flags_the_outer_region(library):
    library.save(_outer_definition(inner_id="nowhere"))
    bot = BotConfig(
        id="b",
        name="B",
        initial="x",
        recovery_state="x",
        states=[StateModel(id="x", node_type="relog")],
        transitions=[],
        regions=[
            RegionModel(
                id="R", label="Outer", members=[], color=1, source=SubgraphSource(subgraph_id="outer", version=1)
            )
        ],
    )
    _, report = compose_bot(bot, library)
    assert report.regions["R"].kind == "missing"
    assert "nowhere" in report.regions["R"].reasons[0]


def test_compose_recomposes_a_nested_instance_through_its_outer_region(library):
    library.save(_outer_definition())
    source = SubgraphSource(subgraph_id="outer", version=1)
    mat = materialize_region("R", source, library.get("outer"), library)
    bot = BotConfig(
        id="b",
        name="B",
        initial=member_id("R", "start"),
        recovery_state=member_id("R", "start"),
        states=mat.states,
        transitions=mat.transitions,
        regions=[RegionModel(id="R", label="Outer", members=mat.member_ids, color=1, source=source), *mat.regions],
    )
    library.save(sync_players_definition(version=2, world="Avalon"))
    composed, report = compose_bot(bot, library)
    assert set(report.regions) == {"R"}, "the nested region is not composed on its own"
    assert {s.id: s for s in composed.states}["sg_R_sg_in1_go_to_world"].params["world"] == "Avalon"
    assert [r.id for r in composed.regions] == ["R", "sg_R_in1"]
    assert composed.regions[1].source.version == 2


# ---------------------------------------------------------------------------
# Extract, convert, push back, detach
# ---------------------------------------------------------------------------


def plain_bot() -> BotConfig:
    """Two-node region plus one external node, in editor-minted ids."""
    return BotConfig(
        id="bot-2",
        name="Plain",
        initial="state_1",
        recovery_state="state_2",
        states=[
            StateModel(id="state_1", node_type="relog", position={"x": 100.0, "y": 50.0}, params={"relog_clients": []}),
            StateModel(
                id="state_2", node_type="form_group", position={"x": 540.0, "y": 20.0}, params={"leader_index": 0}
            ),
            StateModel(id="state_3", node_type="enter_zone", position={"x": 980.0, "y": 20.0}, params={}),
        ],
        transitions=[_t("state_1", "state_2"), _t("state_2", "state_3"), _t("state_3", "state_1", "FAILURE")],
        regions=[RegionModel(id="r1", label="Startup", members=["state_1", "state_2"], color=4)],
    )


def test_extract_then_compose_round_trips(tmp_path):
    bot = plain_bot()
    defn, source = extract_definition(bot, "r1", new_id="startup", name="Startup")
    assert source.anchor == {"x": 100.0, "y": 20.0}
    assert {s.id: s.position for s in defn.states} == {
        "state_1": {"x": 0.0, "y": 30.0},
        "state_2": {"x": 440.0, "y": 0.0},
    }
    assert [(t.source, t.dest) for t in defn.transitions] == [("state_1", "state_2")]

    id_map = convert_region_to_instance(bot, "r1", defn, source)
    assert id_map == {"state_1": member_id("r1", "state_1"), "state_2": member_id("r1", "state_2")}
    lib = SubgraphLibrary(tmp_path)
    lib.save(defn)

    composed, report = compose_bot(bot, lib)
    assert report.regions["r1"].kind == "linked"
    assert composed.model_dump() == bot.model_dump()


def test_convert_region_rewrites_roles_and_regions():
    bot = plain_bot()
    bot.regions.append(RegionModel(id="r2", label="Also", members=["state_2", "state_3"], color=1))
    defn, source = extract_definition(bot, "r1", new_id="startup", name="Startup")
    convert_region_to_instance(bot, "r1", defn, source)

    s1, s2 = member_id("r1", "state_1"), member_id("r1", "state_2")
    assert bot.initial == s1
    assert bot.recovery_state == s2
    assert [s.id for s in bot.states] == [s1, s2, "state_3"]
    assert [(t.source, t.dest) for t in bot.transitions] == [(s1, s2), (s2, "state_3"), ("state_3", s1)]
    assert bot.regions[0].members == [s1, s2]
    assert bot.regions[0].source == source
    assert bot.regions[1].members == [s2, "state_3"]
    assert BotConfig.model_validate(bot.model_dump()) == bot


def test_extract_refuses_an_id_that_cannot_be_local():
    bot = plain_bot()
    bot.states[0].id = "has-dash"
    bot.initial = "has-dash"
    bot.transitions[0].source = "has-dash"
    bot.transitions[2].dest = "has-dash"
    bot.regions[0].members[0] = "has-dash"
    with pytest.raises(ValueError, match="cannot be a subgraph-local id"):
        extract_definition(bot, "r1", new_id="x", name="X")


def test_push_back_bumps_version_and_keeps_inputs(bot, library):
    previous = library.get(SYNC_ID)
    by_id = {s.id: s for s in bot.states}
    by_id[m("hub")].params["zone"] = "Edited/Hub"
    by_id[m("hub")].position = {"x": 0.0, "y": 0.0}
    # a state added while editing keeps its editor-minted id as the local id
    bot.states.append(StateModel(id="state_9", node_type="quicksell", position={"x": -880.0, "y": 490.0}, params={}))
    bot.transitions.append(_t(m("wisps"), "state_9"))
    bot.regions[0].members.append("state_9")

    pushed, warnings = push_back(bot, REGION, previous)
    assert pushed.version == 2
    assert pushed.id == SYNC_ID and pushed.name == previous.name
    assert warnings == []
    assert [i.name for i in pushed.inputs] == ["world"]
    assert pushed.inputs[0].bindings == previous.inputs[0].bindings
    got = {s.id: s for s in pushed.states}
    assert got["hub"].params["zone"] == "Edited/Hub"
    assert got["hub"].position == {"x": 880.0, "y": -90.0}  # (0,0) - anchor (-880, 90)
    assert got["state_9"].position == {"x": 0.0, "y": 400.0}
    assert ("wisps", "state_9") in {(t.source, t.dest) for t in pushed.transitions}
    assert not any(t.source.startswith("state_1") for t in pushed.transitions), "external edges are not pushed"


def test_push_back_drops_a_binding_to_a_deleted_state_with_a_warning(bot, library):
    previous = library.get(SYNC_ID)
    bot.states = [s for s in bot.states if s.id != m("go_to_world")]
    bot.transitions = [t for t in bot.transitions if m("go_to_world") not in (t.source, t.dest)]
    bot.regions[0].members.remove(m("go_to_world"))

    pushed, warnings = push_back(bot, REGION, previous)
    assert [b.state for b in pushed.inputs[0].bindings] == ["hub"]
    assert warnings == ["input 'world' lost its binding to 'go_to_world', which no longer exists"]


def test_detach_keeps_ids(bot):
    ids = [s.id for s in bot.states]
    detach(bot, REGION)
    assert bot.regions[0].source is None
    assert [s.id for s in bot.states] == ids
    assert linked_regions(bot) == []
    assert bot.regions[0].model_dump(exclude_none=True).keys() == {"id", "label", "members", "color"}
