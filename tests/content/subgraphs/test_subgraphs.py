"""Tests for bartlebot.content.subgraphs.subgraphs — composing instances into a bot and extracting them back out.

The fixture definition is a synthetic seven-node "sync players" chain shaped like the
boss template's region (relog → gather → three teleports → hub →
wisps), with two entries and two exits. Ids, zones and worlds are invented; nothing
here is a real character.
"""

from __future__ import annotations

import ast
import json

import pytest

from bartlebot.bot.schema import (
    SUBGRAPH_ENTRY,
    SUBGRAPH_EXIT,
    SUBGRAPH_LOCAL_ID,
    BotConfig,
    RegionModel,
    StateModel,
    SubgraphDefinition,
    SubgraphInput,
    SubgraphInputBinding,
    SubgraphPanel,
    SubgraphPort,
    SubgraphSource,
    TransitionModel,
)
from bartlebot.content.store_roots import Origin
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary
from bartlebot.content.subgraphs.subgraphs import (
    MAX_DEPTH,
    ComposeReport,
    SubgraphCycleError,
    compose_bot,
    config_to_definition,
    convert_region_to_instance,
    definition_change,
    definition_ports,
    definition_problems,
    definition_to_config,
    detach,
    exposed_key,
    external_edges,
    extract_definition,
    extraction_locals,
    instance_slug,
    linked_region_of,
    linked_regions,
    local_id,
    materialize_region,
    member_id,
    mint_instance_id,
    rename_states,
    reresolve_member_params,
    resolve_member_params,
    taken_ids,
)
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS
from bartlebot.paths import project_root

_REPO = project_root()

SYNC_ID = "sync-players"
REGION = "9f59026b"


def _t(src: str, dst: str, when: str | None = "SUCCESS") -> TransitionModel:
    return TransitionModel(trigger="next", source=src, dest=dst, when=when)


def _entry(pid: str, label: str, x: float = -440.0, y: float = 0.0) -> StateModel:
    return StateModel(id=pid, node_type=SUBGRAPH_ENTRY, label=label, position={"x": x, "y": y})


def _exit(pid: str, label: str, x: float = 3080.0, y: float = 0.0) -> StateModel:
    return StateModel(id=pid, node_type=SUBGRAPH_EXIT, label=label, position={"x": x, "y": y})


def sync_players_definition(version: int = 1, world: str = "WizardCity") -> SubgraphDefinition:
    """Seven nodes plus ports, local ids, positions relative to (0, 0).

    Entries: ``entry_start`` into relog, ``entry_hub`` into hub. Exits: ``exit_done``
    fed by wisps SUCCESS, ``exit_early`` fed by tp_a SUCCESS.
    """
    return SubgraphDefinition(
        id=SYNC_ID,
        name="Sync players",
        description="Regroup every client at the world hub.",
        version=version,
        inputs=[
            SubgraphInput(
                name="world",
                label="World",
                type="zone",
                bindings=[SubgraphInputBinding(state="gate", param="zone")],
            ),
            SubgraphInput(
                name="hub",
                label="Hub",
                type="zone",
                bindings=[
                    SubgraphInputBinding(state="tp_b", param="zone"),
                    SubgraphInputBinding(state="hub", param="zone"),
                ],
            ),
        ],
        states=[
            StateModel(id="relog", node_type="relog", position={"x": 0.0, "y": 180.0}, params={"relog_clients": []}),
            StateModel(
                id="gather",
                node_type="teleport_to_friend",
                position={"x": 440.0, "y": 180.0},
                params={"target_hooked_player": True, "target_player": 0, "to_zone": ""},
            ),
            StateModel(
                id="tp_a",
                node_type="teleport_to_world_hub",
                position={"x": 880.0, "y": 0.0},
                params={"zone": "DragonSpyre/DS_Hub"},
            ),
            StateModel(
                id="tp_b",
                node_type="teleport_to_world_hub",
                position={"x": 1320.0, "y": 0.0},
                params={"zone": f"{world}/Hub"},
            ),
            StateModel(
                id="gate", node_type="teleport_to_world_hub", position={"x": 1760.0, "y": 90.0}, params={"zone": world}
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
            _entry("entry_start", "Start"),
            _entry("entry_hub", "Hub", x=1760.0, y=-90.0),
            _exit("exit_done", "Done"),
            _exit("exit_early", "Early", x=1320.0, y=-90.0),
        ],
        transitions=[
            _t("entry_start", "relog", None),
            _t("entry_hub", "hub", None),
            _t("relog", "relog", "FAILURE"),
            _t("relog", "gather"),
            _t("gather", "relog", "FAILURE"),
            _t("gather", "tp_a"),
            _t("tp_a", "tp_b", "FAILURE"),
            _t("tp_a", "exit_early"),
            _t("tp_b", "gate", "FAILURE"),
            _t("tp_b", "hub"),
            _t("gate", "hub"),
            _t("gate", "tp_b", "FAILURE"),
            _t("hub", "wisps"),
            _t("hub", "relog", "FAILURE"),
            _t("wisps", "hub", "FAILURE"),
            _t("wisps", "exit_done"),
        ],
    )


def m(local: str, region: str = REGION) -> str:
    return member_id(region, local)


def linked_bot(defn: SubgraphDefinition, library: SubgraphLibrary, **source_kw) -> BotConfig:
    """A bot holding one instance of ``defn`` plus two external nodes wired into its ports.

    Built by composing, so its snapshot is exactly what ``defn`` produces; tests
    then edit the snapshot or the library to make the two disagree.
    """
    source = SubgraphSource(subgraph_id=defn.id, version=defn.version, anchor={"x": -880.0, "y": 90.0}, **source_kw)
    mat = materialize_region(REGION, source, defn, library)
    source.ports = mat.ports
    external = [
        StateModel(id="state_1", node_type="enter_zone", position={"x": 3080.0, "y": 90.0}, params={"to_zone": "X/Y"}),
        StateModel(id="state_2", node_type="handle_potions", position={"x": 3520.0, "y": 90.0}, params={}),
    ]
    edges = [
        _t(m("wisps"), "state_1"),  # Done
        _t(m("tp_a"), "state_2"),  # Early
        _t("state_1", m("hub"), "FAILURE"),  # into Hub
        _t("state_1", "state_2"),
        _t("state_2", m("relog"), "FAILURE"),  # into Start
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


def _save_edited(library: SubgraphLibrary, edit) -> SubgraphDefinition:
    """Save the next version of the library's definition after ``edit(defn)`` changes it."""
    defn = library.get(SYNC_ID).model_copy(deep=True)
    defn.version += 1
    edit(defn)
    defn = SubgraphDefinition.model_validate(defn.model_dump())
    library.save(defn)
    return defn


# ---------------------------------------------------------------------------
# Schema and invariants
# ---------------------------------------------------------------------------


def test_region_without_source_dumps_exactly_as_before():
    region = RegionModel(id="r1", label="L", members=["a", "b"], color=3)
    assert region.model_dump(exclude_none=True) == {"id": "r1", "label": "L", "members": ["a", "b"], "color": 3}


def test_a_bot_never_writes_a_definition_key():
    cfg = BotConfig(
        id="b",
        name="B",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="relog")],
        transitions=[],
    )
    assert "definition" not in cfg.model_dump(exclude_none=True)
    assert not cfg.is_definition


def test_undo_round_trip_preserves_source_and_its_ports(bot):
    """UndoManager round-trips model_dump → model_validate on every edit."""
    again = BotConfig.model_validate(bot.model_dump())
    assert again == bot
    assert again.regions[0].source.anchor == {"x": -880.0, "y": 90.0}
    assert again.regions[0].source.ports["exit_done"].from_ == [("wisps", "SUCCESS")]


def test_the_port_map_is_written_under_from(bot, tmp_path):
    bot.save(tmp_path / "b.json")
    ports = json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))["regions"][0]["source"]["ports"]
    assert ports["exit_done"] == {"from": [["wisps", "SUCCESS"]]}
    assert ports["entry_start"] == {"member": "relog"}
    assert BotConfig.load(tmp_path / "b.json") == bot


def test_definition_rejects_a_transition_that_leaves_it():
    with pytest.raises(ValueError, match="leaves the definition"):
        SubgraphDefinition(
            id="d",
            name="D",
            states=[StateModel(id="a", node_type="relog"), _entry("e", "Start")],
            transitions=[_t("e", "a", None), _t("a", "elsewhere")],
        )


def test_definition_rejects_a_non_local_id():
    with pytest.raises(ValueError, match="not a valid subgraph-local id"):
        SubgraphDefinition(
            id="d", name="D", states=[StateModel(id="a-b", node_type="relog"), _entry("e", "S")], transitions=[]
        )


def test_definition_rejects_a_binding_to_an_unknown_state():
    with pytest.raises(ValueError, match="binds to unknown state"):
        SubgraphDefinition(
            id="d",
            name="D",
            states=[StateModel(id="a", node_type="relog"), _entry("e", "S")],
            transitions=[_t("e", "a", None)],
            inputs=[SubgraphInput(name="w", type="str", bindings=[SubgraphInputBinding(state="zz", param="p")])],
        )


@pytest.mark.parametrize(
    ("states", "transitions", "message"),
    [
        ([StateModel(id="a", node_type="relog")], [], "at least one Entry"),
        ([StateModel(id="a", node_type="relog"), _entry("e", "S")], [], "exactly one edge"),
        (
            [StateModel(id="a", node_type="relog"), StateModel(id="b", node_type="relog"), _entry("e", "S")],
            [_t("e", "a", None), _t("e", "b", None)],
            "exactly one edge",
        ),
        (
            [StateModel(id="a", node_type="relog"), _entry("e", "S"), _entry("f", "T")],
            [_t("e", "a", None), _t("f", "a", None)],
            "both lead to the same node",
        ),
        (
            [StateModel(id="a", node_type="relog"), _entry("e", "S"), _exit("x", "Done")],
            [_t("e", "a", None), _t("a", "x"), _t("x", "a")],
            "cannot have edges leading out",
        ),
        (
            [StateModel(id="a", node_type="relog"), _entry("e", "S"), _exit("x", "Done")],
            [_t("e", "x", None)],
            "straight to another port",
        ),
    ],
)
def test_definition_enforces_the_port_rules(states, transitions, message):
    with pytest.raises(ValueError, match=message):
        SubgraphDefinition(id="d", name="D", states=states, transitions=transitions)


def test_an_exit_is_optional():
    """A subgraph that loops forever, or ends on the bot's own stop, is legitimate."""
    SubgraphDefinition(
        id="d",
        name="D",
        states=[StateModel(id="a", node_type="relog"), _entry("e", "S")],
        transitions=[_t("e", "a", None)],
    )


def test_node_panel_rules():
    base = dict(
        id="d",
        name="D",
        states=[
            StateModel(id="a", node_type="enter_dungeon"),
            StateModel(id="b", node_type="sync_in_zone"),
            _entry("e", "S"),
        ],
        transitions=[_t("e", "a", None), _t("a", "b")],
    )
    SubgraphDefinition(
        **base,
        panels=[
            SubgraphPanel(
                id="p",
                kind="node",
                member="a",
                bindings={"from_zone": [SubgraphInputBinding(state="b", param="zone")]},
            )
        ],
    )
    with pytest.raises(ValueError, match="exposed twice"):
        SubgraphDefinition(
            **base,
            panels=[SubgraphPanel(id="p", kind="node", member="a"), SubgraphPanel(id="q", kind="node", member="a")],
        )
    with pytest.raises(ValueError, match="already owns"):
        SubgraphDefinition(
            **base,
            panels=[SubgraphPanel(id="p", kind="node", member="a")],
            inputs=[
                SubgraphInput(name="z", type="zone", bindings=[SubgraphInputBinding(state="a", param="from_zone")])
            ],
        )
    with pytest.raises(ValueError, match="not an inputs panel"):
        SubgraphDefinition(**base, inputs=[SubgraphInput(name="z", type="zone", panel="nope")])


def test_definition_round_trips_through_its_file(tmp_path):
    defn = sync_players_definition()
    defn.save(tmp_path / "d.json")
    assert SubgraphDefinition.load(tmp_path / "d.json") == defn
    assert "recovery_state" not in json.loads((tmp_path / "d.json").read_text(encoding="utf-8"))


def test_definition_ports_read_entries_and_exit_feeds():
    ports = definition_ports(sync_players_definition())
    assert ports == {
        "entry_start": SubgraphPort(member="relog"),
        "entry_hub": SubgraphPort(member="hub"),
        "exit_done": SubgraphPort(from_=[("wisps", "SUCCESS")]),
        "exit_early": SubgraphPort(from_=[("tp_a", "SUCCESS")]),
    }


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


def test_instance_slug_is_a_safe_lowercase_id():
    assert instance_slug("Sync players") == "sync_players"
    assert instance_slug("  Defeat Boss (v2) -- fast!  ") == "defeat_boss_v2_fast"
    assert instance_slug("Ünïcode") == "n_code"
    assert instance_slug("???") == "subgraph"
    assert SUBGRAPH_LOCAL_ID.match(instance_slug("Farm: Loremaster / 2"))


def test_mint_instance_id_numbers_above_every_use_of_the_slug():
    """The first instance is ``<slug>_1``; the next is above every number the slug has
    carried -- as a region id, or as the head of a state id a detached or deleted
    instance left behind -- so an instance id, like a state id, is never recycled."""
    assert mint_instance_id("Sync players", []) == "sync_players_1"
    assert mint_instance_id("Sync players", ["sync_players_1", "sync_players_1_state_3"]) == "sync_players_2"
    assert mint_instance_id("Sync players", ["sync_players_4_state_1"]) == "sync_players_5"
    assert mint_instance_id("Farm", ["farm_2_1", "farm_2_1_state_1"]) == "farm_3"
    assert mint_instance_id("Farm 2", ["farm_2", "farm_2_state_1"]) == "farm_2_1"
    assert mint_instance_id("Sync players", ["sync_players_10"]) == "sync_players_11"
    assert mint_instance_id("Sync players", ["sync_players_x", "sync_players"]) == "sync_players_1"


def test_mint_instance_id_with_a_prefix_numbers_apart_from_the_unprefixed():
    """A template draft's instances are ``t_``; a bot's own never number against them."""
    taken = ["t_sync_players_1", "t_sync_players_1_state_2", "sync_players_4"]
    assert mint_instance_id("Sync players", taken, prefix="t_") == "t_sync_players_2"
    assert mint_instance_id("Sync players", taken) == "sync_players_5"
    assert mint_instance_id("Sync players", ["t_sync_players_3"]) == "sync_players_1"


def test_member_ids_never_collide_with_unique_id():
    from bartlebot.gui.canvas.edit_scene import _unique_id

    existing = [m("relog"), m("state_7"), "state_3"]
    assert _unique_id(existing) == "state_8"
    assert _unique_id(["state_3", "farm_1_state_2"]) == "state_4"
    assert _unique_id(["hand_written", "state_9x"]) == "state_1"
    assert local_id(REGION, m("state_7")) == "state_7"
    assert local_id(REGION, "state_3") is None
    assert local_id("other", m("relog")) is None


def test_subgraph_modules_import_no_qt():
    """The policy stays testable without a QApplication or settings, like bot_options."""
    forbidden: list[str] = []
    for name in ("content/subgraphs/subgraphs.py", "content/subgraphs/subgraph_store.py", "content/store_roots.py"):
        tree = ast.parse((_REPO / "bartlebot" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for mod in names:
                if mod.startswith(("bartlebot.gui", "PyQt6", "bartlebot.nodes.registry")) or "settings" in mod:
                    forbidden.append(f"{name} imports {mod}")
    assert forbidden == []


# ---------------------------------------------------------------------------
# Params
# ---------------------------------------------------------------------------


def test_a_set_input_beats_the_definition_value():
    defn = sync_players_definition()
    hub = next(s for s in defn.states if s.id == "hub")
    source = SubgraphSource(subgraph_id=SYNC_ID, version=1, inputs={"hub": "Krokotopia/Hub"})
    assert resolve_member_params(defn, hub, source)["zone"] == "Krokotopia/Hub"
    source.inputs = {}
    assert resolve_member_params(defn, hub, source)["zone"] == "WizardCity/Hub"


def test_unset_input_leaves_definition_value():
    defn = sync_players_definition()
    go = next(s for s in defn.states if s.id == "gate")
    source = SubgraphSource(subgraph_id=SYNC_ID, version=1, inputs={"world": None})
    assert resolve_member_params(defn, go, source)["zone"] == "WizardCity"


def test_a_set_input_reaches_every_bound_member(library):
    bot = linked_bot(library.get(SYNC_ID), library, inputs={"hub": "Marleybone/Hub"})
    by_id = {s.id: s for s in bot.states}
    assert by_id[m("hub")].params["zone"] == "Marleybone/Hub"
    assert by_id[m("tp_b")].params["zone"] == "Marleybone/Hub"
    assert by_id[m("tp_a")].params["zone"] == "DragonSpyre/DS_Hub"  # not bound; definition value
    assert by_id[m("gate")].params["zone"] == "WizardCity"  # a different input, unset


def _panel_definition() -> SubgraphDefinition:
    """``enter_dungeon`` exposed whole, with ``sync_in_zone.zone`` sharing its ``from_zone``."""
    return SubgraphDefinition(
        id="dungeon",
        name="Dungeon",
        states=[
            StateModel(id="go", node_type="sync_in_zone", params={"zone": ""}),
            StateModel(id="enter", node_type="enter_dungeon", params={"from_zone": "", "dungeon_zone": "Old/Z"}),
            _entry("entry_start", "Start"),
        ],
        transitions=[_t("entry_start", "go", None), _t("go", "enter")],
        panels=[
            SubgraphPanel(
                id="entrance",
                kind="node",
                member="enter",
                bindings={"from_zone": [SubgraphInputBinding(state="go", param="zone")]},
            )
        ],
    )


def test_a_node_panels_values_compose_and_fan_out_to_the_bound_member(tmp_path):
    """Done-when: an exposed node's values reach its params, and an extra binding shares one."""
    lib = SubgraphLibrary(tmp_path)
    defn = _panel_definition()
    lib.save(defn)
    source = SubgraphSource(
        subgraph_id="dungeon",
        version=1,
        inputs={exposed_key("enter", "from_zone"): "WizardCity/Olde_Town", exposed_key("enter", "dungeon_zone"): None},
    )
    by_id = {s.id: s for s in materialize_region("R", source, defn, lib).states}
    assert by_id["R_enter"].params["from_zone"] == "WizardCity/Olde_Town"
    assert by_id["R_go"].params["zone"] == "WizardCity/Olde_Town", "the extra binding fans the value out"
    assert by_id["R_enter"].params["dungeon_zone"] == "Old/Z", "an unset exposed param keeps the definition's"


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_port_nodes_are_never_composed(bot):
    assert not any(s.node_type in (SUBGRAPH_ENTRY, SUBGRAPH_EXIT) for s in bot.states)
    assert all("entry" not in t.source and "exit" not in t.dest for t in bot.transitions)


def test_composing_an_unchanged_definition_changes_nothing(bot, library):
    composed, report = compose_bot(bot, library)
    assert report.regions[REGION].kind == "linked"
    assert composed.model_dump() == bot.model_dump()


def test_compose_replaces_snapshot_with_current_definition(bot, library):
    library.save(sync_players_definition(version=2, world="Mooshu"))
    composed, report = compose_bot(bot, library)

    assert composed is not bot
    assert bot.regions[0].source.version == 1, "the input is never mutated"
    assert composed.regions[0].source.version == 2
    assert report.regions[REGION].kind == "linked"
    assert report.regions[REGION].library_version == 2
    assert report.regions[REGION].origin is Origin.USER
    assert {s.id: s for s in composed.states}[m("gate")].params["zone"] == "Mooshu"
    assert composed.regions[0].members == bot.regions[0].members
    assert composed.initial == m("relog")


def test_members_follow_the_definition_layout(bot, library):
    """Instance-owned layout is retired: every compose places members at anchor + definition position."""
    next(s for s in bot.states if s.id == m("hub")).position = {"x": 5.0, "y": 5.0}
    composed, _ = compose_bot(bot, library)
    assert {s.id: s for s in composed.states}[m("hub")].position == {"x": -880.0 + 2200.0, "y": 90.0}


def test_compose_keeps_external_edges(bot, library):
    library.save(sync_players_definition(version=2))
    composed, _ = compose_bot(bot, library)
    before = {(t.source, t.dest, t.when) for t in external_edges(bot, bot.regions[0])}
    after = {(t.source, t.dest, t.when) for t in external_edges(composed, composed.regions[0])}
    assert before == after
    assert len(before) == 4
    assert any(t.source == "state_1" and t.dest == "state_2" for t in composed.transitions)


def test_compose_preserves_state_order_around_the_instance(bot, library):
    library.save(sync_players_definition(version=2))
    composed, _ = compose_bot(bot, library)
    assert [s.id for s in composed.states] == [s.id for s in bot.states]


def test_retargeting_an_entry_moves_the_edge_and_the_roles_without_a_hold(bot, library):
    """Done-when: an Entry pointed at another member takes the bot's edge and ``initial`` with it."""

    def edit(d: SubgraphDefinition) -> None:
        for t in d.transitions:
            if t.source == "entry_start":
                t.dest = "gather"

    _save_edited(library, edit)
    composed, report = compose_bot(bot, library)
    assert report.regions[REGION].kind == "linked"
    assert composed.initial == m("gather")
    assert composed.recovery_state == m("gather")
    assert _t("state_2", m("gather"), "FAILURE") in composed.transitions
    assert _t("state_2", m("relog"), "FAILURE") not in composed.transitions
    assert composed.regions[0].source.ports["entry_start"].member == "gather"


def test_an_exit_fed_by_two_outcomes_regenerates_one_flat_edge_per_pair(bot, library):
    """Done-when: one edge from a port on the canvas is N edges in the file."""

    def edit(d: SubgraphDefinition) -> None:
        d.transitions = [t for t in d.transitions if not (t.source == "wisps" and t.when == "FAILURE")]
        d.transitions.append(_t("wisps", "exit_done", "FAILURE"))

    _save_edited(library, edit)
    composed, report = compose_bot(bot, library)
    assert report.regions[REGION].kind == "linked"
    to_state_1 = [(t.source, t.when) for t in composed.transitions if t.dest == "state_1"]
    assert to_state_1 == [(m("wisps"), "SUCCESS"), (m("wisps"), "FAILURE")]
    # and composing again is stable
    again, _ = compose_bot(composed, library)
    assert again.model_dump() == composed.model_dump()


def test_a_regenerated_exit_edge_keeps_its_conditions(bot, library):
    edge = next(t for t in bot.transitions if t.source == m("wisps") and t.dest == "state_1")
    edge.label = "onward"

    def edit(d: SubgraphDefinition) -> None:
        for t in d.transitions:
            if t.dest == "exit_done":
                t.source = "hub"

    _save_edited(library, edit)
    composed, _ = compose_bot(bot, library)
    moved = next(t for t in composed.transitions if t.dest == "state_1" and t.source == m("hub"))
    assert moved.label == "onward"


def test_removing_a_used_entry_holds_the_update(bot, library):
    def edit(d: SubgraphDefinition) -> None:
        d.states = [s for s in d.states if s.id != "entry_hub"]
        d.transitions = [t for t in d.transitions if t.source != "entry_hub"]

    _save_edited(library, edit)
    composed, report = compose_bot(bot, library)
    status = report.regions[REGION]
    assert status.kind == "held"
    assert status.reasons == ["the update removes entry 'entry_hub', which an edge from the rest of the bot uses"]
    assert composed.model_dump() == bot.model_dump()


def test_removing_a_used_exit_holds_the_update(bot, library):
    def edit(d: SubgraphDefinition) -> None:
        d.states = [s for s in d.states if s.id != "exit_early"]
        d.transitions = [t for t in d.transitions if t.dest != "exit_early"]

    _save_edited(library, edit)
    composed, report = compose_bot(bot, library)
    assert report.regions[REGION].kind == "held"
    assert "exit 'exit_early'" in report.regions[REGION].reasons[0]
    assert composed.model_dump() == bot.model_dump()


def test_removing_an_unused_port_and_renaming_one_are_linked(bot, library):
    detached_edge = next(t for t in bot.transitions if t.source == m("tp_a") and t.dest == "state_2")
    bot.transitions.remove(detached_edge)

    def edit(d: SubgraphDefinition) -> None:
        d.states = [s for s in d.states if s.id != "exit_early"]
        d.transitions = [t for t in d.transitions if t.dest != "exit_early"]
        next(s for s in d.states if s.id == "entry_start").label = "Begin"

    _save_edited(library, edit)
    composed, report = compose_bot(bot, library)
    assert report.regions[REGION].kind == "linked"
    assert "exit_early" not in composed.regions[0].source.ports


def test_removing_a_member_that_no_crossing_uses_is_linked(bot, library):
    """Internal changes never hold an update."""

    def edit(d: SubgraphDefinition) -> None:
        d.states = [s for s in d.states if s.id != "gate"]
        d.transitions = [t for t in d.transitions if "gate" not in (t.source, t.dest)]
        d.inputs = [i for i in d.inputs if i.name != "world"]

    _save_edited(library, edit)
    composed, report = compose_bot(bot, library)
    assert report.regions[REGION].kind == "linked"
    assert m("gate") not in {s.id for s in composed.states}


def test_a_crossing_on_no_port_holds_only_if_its_member_goes(bot, library):
    """An edge the port map does not account for keeps its member while the member exists."""
    bot.transitions.append(_t("state_2", m("tp_b"), "SUCCESS"))
    composed, report = compose_bot(bot, library)
    assert report.regions[REGION].kind == "linked"
    assert _t("state_2", m("tp_b")) in composed.transitions

    def edit(d: SubgraphDefinition) -> None:
        d.states = [s for s in d.states if s.id != "tp_b"]
        d.transitions = [t for t in d.transitions if "tp_b" not in (t.source, t.dest)]
        for inp in d.inputs:
            inp.bindings = [b for b in inp.bindings if b.state != "tp_b"]

    _save_edited(library, edit)
    _, report = compose_bot(composed, library)
    assert report.regions[REGION].kind == "held"


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


def test_compose_output_validates_as_a_bot(bot, library):
    library.save(sync_players_definition(version=2))
    composed, _ = compose_bot(bot, library)
    assert BotConfig.model_validate(composed.model_dump()) == composed


# ---------------------------------------------------------------------------
# Nesting
# ---------------------------------------------------------------------------


def _outer_definition(inner_id: str = SYNC_ID) -> SubgraphDefinition:
    """One node of its own, plus a nested instance of ``inner_id`` at (1000, 0) with no snapshot yet."""
    return SubgraphDefinition(
        id="outer",
        name="Outer",
        states=[StateModel(id="start", node_type="relog", position={"x": 0.0, "y": 0.0}, params={}), _entry("e", "S")],
        transitions=[_t("e", "start", None)],
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
    assert nested_hub == "R_in1_hub"
    assert nested_hub in mat.member_ids
    assert [r.id for r in mat.regions] == [member_id("R", "in1")]
    assert nested_hub in mat.regions[0].members
    by_id = {s.id: s for s in mat.states}
    assert by_id[nested_hub].position == {"x": 10.0 + 1000.0 + 2200.0, "y": 20.0 + 0.0 + 0.0}
    assert mat.regions[0].source.anchor == {"x": 1010.0, "y": 20.0}
    assert mat.regions[0].source.ports == definition_ports(library.get(SYNC_ID))


def test_an_outer_input_reaches_a_nested_member(library):
    outer = _outer_definition()
    outer.inputs = [
        SubgraphInput(
            name="zone", type="str", bindings=[SubgraphInputBinding(state=member_id("in1", "hub"), param="zone")]
        )
    ]
    # a definition stores its nested instance's snapshot among its states, as a bot does
    snap = materialize_region("in1", outer.regions[0].source, library.get(SYNC_ID), library)
    outer.states += snap.states
    outer.transitions += snap.transitions
    outer.regions[0].members = snap.member_ids
    outer.regions[0].source.ports = snap.ports
    library.save(SubgraphDefinition.model_validate(outer.model_dump()))
    source = SubgraphSource(subgraph_id="outer", version=1, inputs={"zone": "Deep/Z"})
    mat = materialize_region("R", source, library.get("outer"), library)
    assert {s.id: s for s in mat.states}["R_in1_hub"].params["zone"] == "Deep/Z"
    assert len({s.id for s in mat.states}) == len(mat.states), "the snapshot is recomposed, not duplicated"


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
    source.ports = mat.ports
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
    assert {s.id: s for s in composed.states}["R_in1_gate"].params["zone"] == "Avalon"
    assert [r.id for r in composed.regions] == ["R", "R_in1"]
    assert composed.regions[1].source.version == 2


def test_linked_region_of_answers_the_outermost_instance(library):
    library.save(_outer_definition())
    source = SubgraphSource(subgraph_id="outer", version=1)
    mat = materialize_region("R", source, library.get("outer"), library)
    config = BotConfig(
        id="b",
        name="b",
        initial=mat.member_ids[0],
        recovery_state=mat.member_ids[0],
        states=mat.states,
        transitions=mat.transitions,
        regions=[RegionModel(id="R", label="Outer", members=mat.member_ids, color=1, source=source), *mat.regions],
    )
    nested_hub = "R_in1_hub"
    assert linked_region_of(config, nested_hub).id == "R", "the inner region composes through the outer"
    assert linked_region_of(config, "R_start").id == "R"
    assert linked_region_of(config, "nope") is None
    detach(config, "R")
    assert linked_region_of(config, nested_hub).id == member_id("R", "in1")


# ---------------------------------------------------------------------------
# Extract, convert, detach
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
                id="state_2",
                node_type="teleport_to_friend",
                position={"x": 540.0, "y": 20.0},
                params={"target_player": 0},
            ),
            StateModel(id="state_3", node_type="enter_zone", position={"x": 980.0, "y": 20.0}, params={}),
        ],
        transitions=[_t("state_1", "state_2"), _t("state_2", "state_3"), _t("state_3", "state_1", "FAILURE")],
        regions=[RegionModel(id="r1", label="Startup", members=["state_1", "state_2"], color=4)],
    )


def test_extract_creates_ports_from_the_crossings():
    bot = plain_bot()
    defn, source = extract_definition(bot, "r1", new_id="startup", name="Startup")
    assert source.anchor == {"x": 100.0, "y": 20.0}
    by_id = {s.id: s for s in defn.states}
    assert by_id["state_1"].position == {"x": 0.0, "y": 30.0}
    assert by_id["state_2"].position == {"x": 440.0, "y": 0.0}
    # Start for initial, Recover for recovery; state_3's edge into state_1 reuses Start
    assert [(s.id, s.label) for s in defn.states if s.node_type == SUBGRAPH_ENTRY] == [
        ("entry_start", "Start"),
        ("entry_recover", "Recover"),
    ]
    assert [(s.id, s.label) for s in defn.states if s.node_type == SUBGRAPH_EXIT] == [("exit_done", "Done")]
    assert source.ports == definition_ports(defn)
    assert source.ports["exit_done"].from_ == [("state_2", "SUCCESS")]
    assert [(t.source, t.dest) for t in defn.transitions if t.source.startswith("state")] == [
        ("state_1", "state_2"),
        ("state_2", "exit_done"),
    ]


def test_extract_with_nothing_reaching_in_makes_one_entry_into_the_first_member():
    bot = plain_bot()
    bot.initial = bot.recovery_state = "state_3"
    bot.transitions = [t for t in bot.transitions if t.dest != "state_1"]
    defn, _ = extract_definition(bot, "r1", new_id="x", name="X")
    entries = [s for s in defn.states if s.node_type == SUBGRAPH_ENTRY]
    assert len(entries) == 1 and definition_ports(defn)[entries[0].id].member == "state_1"


def test_extract_names_one_exit_per_destination():
    bot = plain_bot()
    bot.states.append(StateModel(id="state_4", node_type="relog", label="Retry"))
    bot.transitions.append(_t("state_1", "state_4", "FAILURE"))
    defn, _ = extract_definition(bot, "r1", new_id="x", name="X")
    assert sorted(s.label for s in defn.states if s.node_type == SUBGRAPH_EXIT) == ["To Retry", "To state_3"]


def test_extract_then_compose_round_trips(tmp_path):
    bot = plain_bot()
    defn, source = extract_definition(bot, "r1", new_id="startup", name="Startup")
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


def test_extract_renumbers_local_ids_in_file_order():
    bot = plain_bot()
    rename_states(bot, {"state_1": "has-dash", "state_2": "state_27"})
    bot.regions[0].members = ["state_27", "has-dash"]
    defn, source = extract_definition(bot, "r1", new_id="x", name="X")
    assert [s.id for s in defn.states if s.node_type not in (SUBGRAPH_ENTRY, SUBGRAPH_EXIT)] == ["state_1", "state_2"]
    assert extraction_locals(bot, "r1") == {"has-dash": "state_1", "state_27": "state_2"}
    id_map = convert_region_to_instance(bot, "r1", defn, source, instance_id="x_1")
    assert id_map == {"has-dash": "x_1_state_1", "state_27": "x_1_state_2"}
    assert bot.regions[0].id == "x_1"
    assert bot.initial == "x_1_state_1" and bot.recovery_state == "x_1_state_2"


def test_convert_with_a_minted_instance_id_round_trips_through_compose(tmp_path):
    bot = plain_bot()
    defn, source = extract_definition(bot, "r1", new_id="startup", name="Startup")
    rid = mint_instance_id(defn.name, taken_ids(bot))
    assert rid == "startup_1"
    convert_region_to_instance(bot, "r1", defn, source, instance_id=rid)
    lib = SubgraphLibrary(tmp_path)
    lib.save(defn)
    composed, report = compose_bot(bot, lib)
    assert report.regions[rid].kind == "linked"
    assert composed.model_dump() == bot.model_dump()
    assert mint_instance_id("Startup", taken_ids(bot)) == "startup_2"


def test_detach_keeps_ids(bot):
    ids = [s.id for s in bot.states]
    detach(bot, REGION)
    assert bot.regions[0].source is None
    assert [s.id for s in bot.states] == ids
    assert linked_regions(bot) == []
    assert bot.regions[0].model_dump(exclude_none=True).keys() == {"id", "label", "members", "color"}


def test_reresolve_member_params_applies_inputs_in_place(bot, library):
    region = bot.regions[0]
    region.source.inputs["world"] = "Mooshu"
    reresolve_member_params(bot, region, library.get(SYNC_ID))
    by_id = {s.id: s for s in bot.states}
    assert by_id[m("gate")].params["zone"] == "Mooshu"
    assert by_id[m("hub")].params["zone"] == "WizardCity/Hub"
    assert by_id["state_1"].params == {"to_zone": "X/Y"}, "an external state is not the instance's"


# ---------------------------------------------------------------------------
# A definition as a document
# ---------------------------------------------------------------------------


def test_a_definition_round_trips_through_its_document():
    defn = sync_players_definition(version=4)
    config = definition_to_config(defn)
    assert config.is_definition
    assert config.initial == config.recovery_state == "entry_start"
    assert BotConfig.model_validate(config.model_dump()) == config, "undo round-trips the signature too"
    assert config_to_definition(config) == defn
    assert config_to_definition(config, version=5).version == 5


def test_a_broken_document_does_not_convert():
    config = definition_to_config(sync_players_definition())
    config.transitions = [t for t in config.transitions if t.source != "entry_start"]
    with pytest.raises(ValueError, match="exactly one edge"):
        config_to_definition(config)


def test_definition_problems_reports_structure_and_types(library):
    config = definition_to_config(sync_players_definition())
    assert definition_problems(config, NODE_PARAM_SCHEMAS, library) == ([], [])
    config.transitions = [t for t in config.transitions if t.dest != "exit_early"]
    config.definition.inputs.append(
        SubgraphInput(
            name="bad",
            type="str",
            bindings=[
                SubgraphInputBinding(state="wisps", param="timeout"),
                SubgraphInputBinding(state="tp_a", param="zone"),
            ],
        )
    )
    errors, warnings = definition_problems(config, NODE_PARAM_SCHEMAS, library)
    assert any("incompatible types" in e for e in errors)
    assert warnings == [], "an idle Exit is the node's own error, not the signature's"


def test_an_input_linked_to_nothing_blocks_saving_but_still_loads(library):
    """Unlinking an input's last param is a step while editing, so the file still loads;
    saving it is refused, since a subgraph node would offer an input that does nothing."""
    config = definition_to_config(sync_players_definition())
    config.definition.inputs.append(SubgraphInput(name="loose", label="Loose end", type="str"))
    errors, _warnings = definition_problems(config, NODE_PARAM_SCHEMAS, library)
    assert errors == ["input 'Loose end' is linked to nothing; link a param to it or delete it"]
    # The structural rules a definition file is held to do not include it.
    defn = config_to_definition(config)
    assert [i.name for i in defn.inputs if not i.bindings] == ["loose"]


def test_a_zone_and_a_nav_zone_can_share_an_input():
    """The Stage 3 lattice in use: the input is typed the narrower of the two."""
    from bartlebot.content.subgraphs.subgraphs import binding_type

    defn = _panel_definition()
    bindings = [SubgraphInputBinding(state="go", param="zone"), SubgraphInputBinding(state="enter", param="from_zone")]
    assert binding_type(defn, bindings, NODE_PARAM_SCHEMAS) == "nav_zone"


def test_an_unset_input_follows_its_default():
    defn = sync_players_definition()
    world = next(i for i in defn.inputs if i.name == "world")
    world.default = "Mooshu"
    state = next(s for s in defn.states if s.id == "gate")
    source = SubgraphSource(subgraph_id=defn.id, version=1)
    assert resolve_member_params(defn, state, source)["zone"] == "Mooshu"
    source.inputs["world"] = "Krokotopia"
    assert resolve_member_params(defn, state, source)["zone"] == "Krokotopia"


def test_sync_linked_values_keeps_linked_params_equal_to_their_input():
    from bartlebot.content.subgraphs.subgraphs import sync_linked_values  # noqa: PLC0415

    config = definition_to_config(sync_players_definition())
    by_id = {s.id: s for s in config.states}
    by_id["tp_b"].params["zone"] = "Mooshu/Hub"
    sync_linked_values(config, edited_state="tp_b")
    hub = next(i for i in config.definition.inputs if i.name == "hub")
    assert hub.default == "Mooshu/Hub"
    assert by_id["hub"].params["zone"] == "Mooshu/Hub"
    hub.default = "Krokotopia/Hub"
    sync_linked_values(config)
    assert by_id["tp_b"].params["zone"] == by_id["hub"].params["zone"] == "Krokotopia/Hub"


# ---------------------------------------------------------------------------
# An outer input linked to a nested instance's input
# ---------------------------------------------------------------------------


def _outer_with_snapshot(library: SubgraphLibrary, **input_kw) -> SubgraphDefinition:
    """``_outer_definition`` with its nested snapshot stored, and a ``zone`` input linked to the inner ``hub``."""
    outer = _outer_definition()
    snap = materialize_region("in1", outer.regions[0].source, library.get(SYNC_ID), library)
    outer.states += snap.states
    outer.transitions += snap.transitions
    outer.regions[0].members = snap.member_ids
    outer.regions[0].source.ports = snap.ports
    outer.inputs = [
        SubgraphInput(name="zone", type="zone", bindings=[SubgraphInputBinding(region="in1", param="hub")], **input_kw)
    ]
    return SubgraphDefinition.model_validate(outer.model_dump())


def _bot_of(defn: SubgraphDefinition, library: SubgraphLibrary, **inputs) -> BotConfig:
    source = SubgraphSource(subgraph_id=defn.id, version=defn.version, inputs=inputs)
    mat = materialize_region("R", source, defn, library)
    source.ports = mat.ports
    return BotConfig(
        id="b",
        name="B",
        initial=member_id("R", "start"),
        recovery_state=member_id("R", "start"),
        states=mat.states,
        transitions=mat.transitions,
        regions=[RegionModel(id="R", label="Outer", members=mat.member_ids, color=1, source=source), *mat.regions],
    )


def _zones(config: BotConfig) -> dict[str, str]:
    by_id = {s.id: s for s in config.states}
    return {local: by_id[f"R_in1_{local}"].params["zone"] for local in ("tp_b", "hub")}


def test_a_binding_names_a_state_or_a_nested_instance_never_both():
    with pytest.raises(ValueError):
        SubgraphInputBinding(param="zone")
    with pytest.raises(ValueError):
        SubgraphInputBinding(state="hub", region="in1", param="zone")


def test_a_member_binding_is_written_as_before():
    dumped = sync_players_definition().model_dump(exclude_none=True)
    assert dumped["inputs"][0]["bindings"] == [{"state": "gate", "param": "zone"}]


def test_an_outer_input_goes_in_through_the_nested_instances_own_input(library):
    library.save(_outer_with_snapshot(library))
    bot = _bot_of(library.get("outer"), library, zone="Deep/Z")
    # the inner ``hub`` input fans out to both of its members, which the outer never names
    assert _zones(bot) == {"tp_b": "Deep/Z", "hub": "Deep/Z"}
    assert bot.regions[1].source.inputs == {"hub": "Deep/Z"}


def test_an_unset_outer_input_forwards_its_default_and_with_none_leaves_the_inner_alone(library):
    library.save(_outer_with_snapshot(library, default="Outer/Default"))
    assert _zones(_bot_of(library.get("outer"), library)) == {"tp_b": "Outer/Default", "hub": "Outer/Default"}

    library.save(_outer_with_snapshot(library))
    bot = _bot_of(library.get("outer"), library)
    assert _zones(bot) == {"tp_b": "WizardCity/Hub", "hub": "WizardCity/Hub"}
    assert bot.regions[1].source.inputs == {}


def test_a_forwarded_input_survives_a_recompose_and_follows_the_instance(library):
    library.save(_outer_with_snapshot(library))
    bot = _bot_of(library.get("outer"), library, zone="Deep/Z")
    bot.regions[0].source.inputs["zone"] = "Other/Z"
    composed, report = compose_bot(bot, library)
    assert report.regions["R"].kind == "linked"
    assert _zones(composed) == {"tp_b": "Other/Z", "hub": "Other/Z"}


def test_reresolving_forwards_to_the_nested_instance_without_a_recompose(library):
    library.save(_outer_with_snapshot(library))
    defn = library.get("outer")
    bot = _bot_of(defn, library, zone="Deep/Z")
    bot.regions[0].source.inputs["zone"] = "Other/Z"
    reresolve_member_params(bot, bot.regions[0], defn, library)
    assert _zones(bot) == {"tp_b": "Other/Z", "hub": "Other/Z"}
    # reset: nothing is forwarded any more, so the inner falls back rather than keeping the old value
    del bot.regions[0].source.inputs["zone"]
    reresolve_member_params(bot, bot.regions[0], defn, library)
    assert _zones(bot) == {"tp_b": "WizardCity/Hub", "hub": "WizardCity/Hub"}
    assert bot.regions[1].source.inputs == {}


def test_a_link_to_something_that_is_not_a_nested_instance_is_refused(library):
    outer = _outer_with_snapshot(library)
    outer.inputs[0].bindings = [SubgraphInputBinding(region="nowhere", param="hub")]
    with pytest.raises(ValueError, match="not a subgraph node"):
        SubgraphDefinition.model_validate(outer.model_dump())
    outer.inputs[0].bindings = [SubgraphInputBinding(region="in1", param="hub")] * 2
    with pytest.raises(ValueError, match="linked twice"):
        SubgraphDefinition.model_validate(outer.model_dump())


def test_a_nested_link_types_the_outer_input_and_names_where_it_lands(library):
    from bartlebot.content.subgraphs.subgraphs import binding_type, input_targets  # noqa: PLC0415

    outer = _outer_with_snapshot(library)
    inp = outer.inputs[0]
    assert binding_type(outer, inp.bindings, NODE_PARAM_SCHEMAS, library) == "zone"
    assert binding_type(outer, inp.bindings, NODE_PARAM_SCHEMAS) is None, "the inner type needs the library"
    assert input_targets(outer, inp, library) == [("in1_tp_b", "zone"), ("in1_hub", "zone")]
    assert input_targets(outer, inp, library, "R") == [("R_in1_tp_b", "zone"), ("R_in1_hub", "zone")]
    assert input_targets(outer, inp, None) == []


def test_sync_keeps_a_nested_input_equal_to_the_outer_default(library):
    from bartlebot.content.subgraphs.subgraphs import reresolve_linked_instances, sync_linked_values  # noqa: PLC0415

    config = definition_to_config(_outer_with_snapshot(library))
    inner = next(r for r in config.regions if r.id == "in1")
    zone = config.definition.inputs[0]
    by_id = {s.id: s for s in config.states}

    sync_linked_values(config)
    assert zone.default is None and inner.source.inputs == {}, "unset stays unset: the inner follows its own definition"

    zone.default = "Outer/Default"
    sync_linked_values(config)
    reresolve_linked_instances(config, library)
    assert inner.source.inputs == {"hub": "Outer/Default"}
    assert by_id["in1_hub"].params["zone"] == "Outer/Default"

    inner.source.inputs["hub"] = "Edited/OnTheNode"
    sync_linked_values(config, edited_state="in1")
    assert zone.default == "Edited/OnTheNode"

    del inner.source.inputs["hub"]  # Reset on the node's page
    sync_linked_values(config, edited_state="in1")
    assert zone.default is None and inner.source.inputs == {}


def test_a_link_to_a_detached_instance_goes_and_takes_an_input_left_with_nothing(library):
    from bartlebot.content.subgraphs.subgraphs import sync_linked_values  # noqa: PLC0415

    config = definition_to_config(_outer_with_snapshot(library))
    detach(config, "in1")
    sync_linked_values(config)
    assert config.definition.inputs == []


def test_an_inner_definition_that_drops_a_linked_input_is_a_warning(library):
    config = definition_to_config(_outer_with_snapshot(library))
    assert definition_problems(config, NODE_PARAM_SCHEMAS, library) == ([], [])

    def drop_hub(defn):
        defn.inputs = [i for i in defn.inputs if i.name != "hub"]

    _save_edited(library, drop_hub)
    errors, warnings = definition_problems(config, NODE_PARAM_SCHEMAS, library)
    assert errors == []
    assert len(warnings) == 1 and "no longer has that input" in warnings[0]


# ---------------------------------------------------------------------------
# ...and to one row of a promoted node's input group on the nested instance
# ---------------------------------------------------------------------------

PROMOTED = "tp_a.zone"


def _promote_tp_a(library: SubgraphLibrary) -> None:
    """Save the inner definition with ``tp_a`` promoted whole, as the "First hop" input group."""
    inner = sync_players_definition()
    inner.panels = [SubgraphPanel(id="node_tp_a", kind="node", label="First hop", member="tp_a")]
    library.save(SubgraphDefinition.model_validate(inner.model_dump()))


def _outer_linking_the_row(library: SubgraphLibrary, **input_kw) -> SubgraphDefinition:
    _promote_tp_a(library)
    outer = _outer_with_snapshot(library)
    outer.inputs = [
        SubgraphInput(
            name="first", type="zone", bindings=[SubgraphInputBinding(region="in1", param=PROMOTED)], **input_kw
        )
    ]
    return SubgraphDefinition.model_validate(outer.model_dump())


def test_a_key_of_an_instances_inputs_is_a_named_input_or_a_promoted_nodes_param(library):
    from bartlebot.content.subgraphs.subgraphs import resolve_instance_key  # noqa: PLC0415

    _promote_tp_a(library)
    inner = library.get(SYNC_ID)
    named = resolve_instance_key(inner, "hub")
    assert named.input.name == "hub" and named.panel is None
    row = resolve_instance_key(inner, PROMOTED)
    assert row.input is None and row.panel.member == "tp_a" and row.key == "zone"
    assert resolve_instance_key(inner, "nope") is None
    assert resolve_instance_key(inner, "tp_b.zone") is None, "tp_b is not promoted"


def test_an_outer_input_reaches_a_promoted_row_of_the_nested_instance(library):
    library.save(_outer_linking_the_row(library))
    bot = _bot_of(library.get("outer"), library, first="Deep/Z")
    by_id = {s.id: s for s in bot.states}
    assert by_id["R_in1_tp_a"].params["zone"] == "Deep/Z"
    assert bot.regions[1].source.inputs == {PROMOTED: "Deep/Z"}

    unset = _bot_of(library.get("outer"), library)
    assert {s.id: s for s in unset.states}["R_in1_tp_a"].params["zone"] == "DragonSpyre/DS_Hub", "the member's own"

    bot.regions[0].source.inputs["first"] = "Other/Z"
    reresolve_member_params(bot, bot.regions[0], library.get("outer"), library)
    assert by_id["R_in1_tp_a"].params["zone"] == "Other/Z"


def test_a_promoted_row_link_is_typed_and_lands_like_the_row(library):
    from bartlebot.content.subgraphs.subgraphs import binding_type, input_targets  # noqa: PLC0415

    outer = _outer_linking_the_row(library)
    inp = outer.inputs[0]
    own = next(f.type for f in NODE_PARAM_SCHEMAS["teleport_to_world_hub"] if f.key == "zone")
    assert binding_type(outer, inp.bindings, NODE_PARAM_SCHEMAS, library) == own
    assert input_targets(outer, inp, library, "R") == [("R_in1_tp_a", "zone")]


def test_demoting_the_inner_node_leaves_a_warning_on_the_outer_link(library):
    config = definition_to_config(_outer_linking_the_row(library))
    assert definition_problems(config, NODE_PARAM_SCHEMAS, library) == ([], [])
    library.save(sync_players_definition(version=2))
    errors, warnings = definition_problems(config, NODE_PARAM_SCHEMAS, library)
    assert errors == [] and len(warnings) == 1


# ---------------------------------------------------------------------------
# What a save changed (#158)
# ---------------------------------------------------------------------------


def _moved(defn: SubgraphDefinition, state_id: str) -> SubgraphDefinition:
    moved = defn.model_copy(deep=True)
    next(s for s in moved.states if s.id == state_id).position = {"x": -40.0, "y": 999.0}
    return moved


def test_a_definition_unchanged_but_for_its_version_is_no_change():
    assert definition_change(sync_players_definition(), sync_players_definition(version=4)) == "none"


def test_moving_a_node_is_a_layout_change():
    defn = sync_players_definition()
    assert definition_change(defn, _moved(defn, "wisps")) == "layout"


def test_a_plain_region_is_layout():
    defn = sync_players_definition()
    grouped = defn.model_copy(deep=True)
    grouped.regions.append(RegionModel(id="r1", label="Gather", members=["relog", "gather"], color=2))
    assert definition_change(defn, grouped) == "layout"
    recolored = grouped.model_copy(deep=True)
    recolored.regions[0].color = 5
    assert definition_change(grouped, recolored) == "layout"


def test_moving_a_nested_instance_is_a_layout_change():
    defn = _outer_definition()
    moved = defn.model_copy(deep=True)
    moved.regions[0].source.anchor = {"x": 0.0, "y": 400.0}
    assert definition_change(defn, moved) == "layout"


def test_a_name_or_a_description_alone_keeps_the_version():
    defn = sync_players_definition()
    assert definition_change(defn, defn.model_copy(update={"name": "Regroup"})) == "meta"
    assert definition_change(defn, defn.model_copy(update={"description": "Elsewhere."})) == "meta"
    moved = _moved(defn, "wisps").model_copy(update={"name": "Regroup"})
    assert definition_change(defn, moved) == "meta", "a layout change beside a rename keeps the version too"
    param = _moved(defn, "wisps").model_copy(update={"name": "Regroup"})
    next(s for s in param.states if s.id == "wisps").params["timeout"] = 45
    assert definition_change(defn, param) == "content", "a rename does not hide a change to what runs"


def test_a_param_or_an_edge_is_a_content_change():
    defn = sync_players_definition()
    param = _moved(defn, "wisps")
    next(s for s in param.states if s.id == "wisps").params["timeout"] = 45
    assert definition_change(defn, param) == "content", "a layout change alongside does not hide it"
    edge = defn.model_copy(deep=True)
    edge.transitions = edge.transitions[:-1]
    assert definition_change(defn, edge) == "content"
    nested = _outer_definition()
    relinked = nested.model_copy(deep=True)
    relinked.regions[0].source.inputs = {"hub": "Krokotopia/Hub"}
    assert definition_change(nested, relinked) == "content"
