"""Tests for tools/rebuild_from_template.py — rebuilding a bot from its template, keeping who it is.

Built against the shipped templates and the shipped Sync players definition, read-only, since
what the tool fills is whatever their setup steps ask for. The old bots are synthetic, with
real zone paths so the Sync players zone is a destination; every name and id is invented.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from bartlebot import setup_steps  # noqa: E402
from bartlebot.bot.schema import BotConfig  # noqa: E402
from bartlebot.store_roots import project_root  # noqa: E402
from bartlebot.subgraph_store import SubgraphLibrary  # noqa: E402
from tools import rebuild_from_template as rft  # noqa: E402

ROOT = project_root()

_A = {"x": 100.0, "y": 200.0, "z": 0.0}
_B = {"x": 300.0, "y": 400.0, "z": 0.0}

HUB = "Celestia/CL_Hub"
ENTRANCE = "Celestia/CL_Z10_Kingdom_Of_The_Crabs"
DUNGEON = "Celestia/Interiors/CL_Z10i6_Kingdom_Of_The_Crabs"


def _s(sid: str, node_type: str, label: str = "", **params) -> dict:
    return {"id": sid, "node_type": node_type, "label": label, "position": {"x": 0.0, "y": 0.0}, "params": params}


def _t(source: str, dest: str, when: str | None = "SUCCESS") -> dict:
    t = {"trigger": "advance", "source": source, "dest": dest}
    if when:
        t["when"] = when
    return t


def _bot(states: list[dict], transitions: list[dict], more_states=(), **extra) -> BotConfig:
    data = {
        "id": "00000000-1111-2222-3333-444444444444",
        "name": "Test Boss",
        "description": "Farms a test boss.",
        "version": "3.0",
        "tags": ["Boss"],
        "authors": "Someone",
        "drops": [{"name": "Test Hat"}],
        "initial": states[0]["id"],
        "recovery_state": states[0]["id"],
        "states": [*states, *more_states],
        "transitions": transitions,
    }
    return BotConfig.model_validate({**data, **extra})


def _boss(enemy: str = "Test-Boss-01", more_states=(), more_transitions=(), **extra) -> BotConfig:
    return _bot(
        [
            _s("state_1", "teleport_to_world_hub", "Go to the hub", zone=HUB),
            _s("state_2", "enter_zone", "Walk to the entrance", from_zone=HUB, to_zone=ENTRANCE, xyz=_A, reset_xyz=_B),
            _s(
                "state_3",
                "enter_dungeon",
                "Enter the Test Lair",
                from_zone=ENTRANCE,
                dungeon_zone=DUNGEON,
                sigil_xyz=_A,
                reset_xyz=_B,
                dungeon_name="Test Lair",
            ),
            _s("state_4", "handle_combat", "Defeat the test boss", enemy=enemy, use_default_strategy=False),
            _s("state_5", "interact", "Open the chest", zone=DUNGEON, interact_xyz=_A, reset_xyz=_B),
            _s(
                "state_6",
                "enter_zone",
                "Leave the Test Lair",
                from_zone=DUNGEON,
                to_zone=ENTRANCE,
                xyz=_A,
                reset_xyz=_B,
            ),
        ],
        [
            _t("state_1", "state_2"),
            _t("state_2", "state_3"),
            _t("state_3", "state_4"),
            _t("state_4", "state_5"),
            _t("state_5", "state_6"),
            _t("state_6", "state_3"),
            *more_transitions,
        ],
        more_states=more_states,
        **extra,
    )


@pytest.fixture(scope="module")
def library() -> SubgraphLibrary:
    return SubgraphLibrary(ROOT, ROOT)


def _rebuild(old: BotConfig, template: str, library) -> tuple[BotConfig, rft.Report]:
    return rft.rebuild(old, rft.find_start(template, ROOT), library)


def _steps_left(config: BotConfig, library) -> int:
    return setup_steps.steps_left(config, setup_steps.steps_for(config, ROOT, ROOT), library)


def _by_type(config: BotConfig, node_type: str):
    return [s for s in config.states if s.node_type == node_type]


def _next(config: BotConfig, sid: str) -> str:
    return next(t.dest for t in config.transitions if t.source == sid and t.when == "SUCCESS")


# ---------------------------------------------------------------------------
# Which template
# ---------------------------------------------------------------------------


def test_a_bot_is_classified_by_its_nodes():
    boss = _boss()
    assert rft.classify(boss) == "boss"

    second_fight = boss.states[3].model_copy(update={"id": "state_9"})
    assert rft.classify(boss.model_copy(update={"states": [*boss.states, second_fight]})) == "dungeon"

    scaling = boss.states[0].model_copy(update={"id": "state_9", "node_type": "check_level_scaling", "params": {}})
    assert rft.classify(boss.model_copy(update={"states": [*boss.states, scaling]})) == "spellement"


# ---------------------------------------------------------------------------
# A boss bot, end to end
# ---------------------------------------------------------------------------


def test_a_rebuilt_bot_keeps_who_it_is_and_records_its_template(library):
    old = _boss()
    config, _report = _rebuild(old, "Boss Farmer", library)
    start = rft.find_start("Boss Farmer", ROOT)

    assert config.id == old.id
    assert (config.name, config.description, config.version) == (old.name, old.description, old.version)
    assert config.authors == old.authors
    assert config.tags == ["Boss", "Team-Up"], "its own, and the Team-Up its rebuilt entry offers"
    assert [d.name for d in config.drops] == ["Test Hat"]
    assert config.template is not None
    assert (config.template.id, config.template.version) == (start.template_id, start.version)
    assert config.template_meta is None


def test_every_fill_in_is_filled_from_the_old_bot(library):
    config, report = _rebuild(_boss(), "Boss Farmer", library)

    entry = _by_type(config, "enter_dungeon")[0]
    assert (entry.params["from_zone"], entry.params["dungeon_zone"]) == (ENTRANCE, DUNGEON)
    assert entry.params["sigil_xyz"] == _A and entry.label == "Enter the Test Lair"
    assert entry.params["dungeon_name"] == "Test Lair"
    fight = _by_type(config, "handle_combat")[0]
    assert fight.params["enemy"] == "Test-Boss-01" and fight.params["use_default_strategy"] is False
    exit_ = next(s for s in _by_type(config, "enter_zone") if s.params.get("from_zone") == DUNGEON)
    assert exit_.params["to_zone"] == ENTRANCE
    sync = next(r for r in config.regions if r.source is not None)
    assert sync.source.inputs[rft.SYNC_ZONE_INPUT] == ENTRANCE

    assert not report.unfilled
    assert _steps_left(config, library) == 0


def test_the_report_sorts_every_old_node(library):
    _config, report = _rebuild(_boss(), "Boss Farmer", library)

    assert any("state_1 teleport_to_world_hub" in line for line in report.folded)
    assert any("state_2 enter_zone" in line for line in report.folded), "the walk to the entrance is Sync players'"
    assert [line.split()[0] for line in report.grafted] == ["state_5"], "the chest is the bot's own content"
    assert not report.content and report.clean


def test_content_is_spliced_in_after_the_node_it_hung_off_and_skips_nothing(library):
    config, _report = _rebuild(_boss(), "Boss Farmer", library)
    fight = _by_type(config, "handle_combat")[0]
    chest = next(s for s in config.states if s.id == "state_5")
    wisps_after = next(s for s in _by_type(config, "collect_wisps") if s.id == _next(config, chest.id))

    assert _next(config, fight.id) == chest.id
    assert _next(config, wisps_after.id) == next(s.id for s in _by_type(config, "enter_zone")), "then the exit"
    assert chest.label == "Open the chest" and chest.params["interact_xyz"] == _A


def test_a_flag_guarded_skip_comes_with_its_content(library):
    skip = {**_t("state_4", "state_6"), "flags": [{"option": "Skip the chest", "value": True}]}
    old = _boss(more_transitions=[skip], options=[{"name": "Skip the chest", "type": "bool", "default": False}])
    config, report = _rebuild(old, "Boss Farmer", library)
    fight = _by_type(config, "handle_combat")[0]

    assert any(t.source == fight.id and t.flags for t in config.transitions), "the skip is kept"
    assert not report.unread_options and report.clean


def test_content_reached_only_from_replaced_navigation_is_not_written(tmp_path, library):
    old = _boss(
        more_states=[_s("state_9", "interact", "Pull a lever", zone=HUB, interact_xyz=_A, reset_xyz=_B)],
        more_transitions=[_t("state_1", "state_9"), _t("state_9", "state_2")],
    )
    _config, report = _rebuild(old, "Boss Farmer", library)

    assert any("only from navigation" in line for line in report.problems)
    assert not report.clean

    path = tmp_path / "lever.json"
    old.save(path)
    before = path.read_bytes()
    assert rft._rebuild_one(str(path), None, write=True) is False
    assert path.read_bytes() == before


def test_an_unnamed_enemy_is_reported_and_left_as_a_step(library):
    config, report = _rebuild(_boss(enemy=""), "Boss Farmer", library)

    assert any("Enemy" in line for line in report.unfilled)
    assert _steps_left(config, library) == 1
    assert not report.clean, "a blank fill-in would stop the bot running, so it is not written"


def test_the_old_bots_options_are_kept_and_one_nothing_reads_is_reported(library):
    old = _boss(options=[{"name": "Skip the chest", "type": "bool", "default": False}])
    config, report = _rebuild(old, "Boss Farmer", library)

    assert "Skip the chest" in {o.name for o in config.options}
    assert report.unread_options == ["Skip the chest"], "no edge guards on it"
    assert not report.clean


def test_an_entrance_off_the_zone_map_syncs_where_the_old_bot_entered_it_from(library):
    manor, wastes = "Mirage/Interiors/MR_Z04_CalixcoManor", "Mirage/MR_Z04_RubalWastes"
    old = _bot(
        [
            _s("state_1", "teleport_to_world_hub", zone="Mirage/MR_Z00_Hub"),
            _s("state_2", "enter_zone", "Enter the manor", from_zone=wastes, to_zone=manor, xyz=_A, reset_xyz=_B),
            _s(
                "state_3",
                "enter_dungeon",
                from_zone=manor,
                dungeon_zone="Mirage/Interiors/X",
                sigil_xyz=_A,
                reset_xyz=_B,
            ),
            _s("state_4", "handle_combat", enemy="Test-Boss-01"),
        ],
        [_t("state_1", "state_2"), _t("state_2", "state_3"), _t("state_3", "state_4")],
    )
    config, report = _rebuild(old, "Boss Farmer", library)

    sync = next(r for r in config.regions if r.source is not None)
    assert sync.source.inputs[rft.SYNC_ZONE_INPUT] == wastes
    assert [line.split()[0] for line in report.grafted] == ["state_2"]
    sync_member = next(s for s in config.states if s.node_type == "sync_in_zone")
    assert _next(config, sync_member.id) == "state_2", "the step into the manor goes straight after Sync players"
    assert _next(config, "state_2") == _by_type(config, "handle_potions")[0].id


# ---------------------------------------------------------------------------
# Level scaling
# ---------------------------------------------------------------------------


def _spellement(scales_zone: str, entrance: str, dungeon: str) -> BotConfig:
    return _bot(
        [
            _s("state_1", "teleport_to_world_hub", zone=entrance),
            _s("state_2", "check_level_scaling"),
            _s("state_3", "enter_dungeon", from_zone=entrance, dungeon_zone=dungeon, sigil_xyz=_A, reset_xyz=_B),
            _s("state_4", "apply_level_scaling", zone=scales_zone, interact_xyz=_A, reset_xyz=_B),
            _s("state_5", "handle_combat", enemy="Test-Boss-01"),
        ],
        [
            _t("state_1", "state_2"),
            _t("state_2", "state_3", "TRUE"),
            _t("state_2", "state_3", "FALSE"),
            _t("state_3", "state_4"),
            _t("state_4", "state_5"),
        ],
        name="Test Spellement",
    )


def test_a_bot_farming_the_scales_own_dungeon_scales_after_entry(library):
    storms = "Krokotopia/KT_Tomb/KT_TempleOfStorms"
    config, report = _rebuild(
        _spellement(storms, "Krokotopia/KT_Tomb/KT_WellOfSpirits", storms), "Spellement Farmer", library
    )
    hls = _by_type(config, "handle_level_scaling")[0]
    entry = _by_type(config, "enter_dungeon")[0]
    fight = _by_type(config, "handle_combat")[0]

    assert _next(config, entry.id) == hls.id
    assert _next(config, hls.id) == fight.id
    assert hls.id not in {t.dest for t in config.transitions if t.source != entry.id}
    assert not hls.params.get("scales"), "Temple of Storms is Krokotopia's default"
    assert any("moved after" in note for note in report.notes)


def test_a_scales_that_is_not_the_world_default_is_named(library):
    atlantea = "Aquila/AQ_Z02_Atlantea"
    config, _report = _rebuild(_spellement(atlantea, "Aquila/AQ_Z00_Hub", atlantea), "Spellement Farmer", library)

    assert _by_type(config, "handle_level_scaling")[0].params["scales"] == "atlantea"


def test_a_bot_farming_elsewhere_keeps_the_templates_order(library):
    storms = "Krokotopia/KT_Tomb/KT_TempleOfStorms"
    config, report = _rebuild(
        _spellement(storms, "Celestia/CL_Hub", "Celestia/Interiors/Elsewhere"), "Spellement Farmer", library
    )
    entry = _by_type(config, "enter_dungeon")[0]

    assert _by_type(config, "handle_combat")[0].id == _next(config, entry.id)
    assert not any("moved after" in note for note in report.notes)


# ---------------------------------------------------------------------------
# Reagents
# ---------------------------------------------------------------------------


def test_reagent_spawns_fill_in_the_order_a_run_reaches_them(library):
    zone = "Celestia/CL_Hub"
    spawns = [
        _s(f"state_{i}", "collect_reagent", zone=zone, xyz={"x": float(i), "y": 0.0, "z": 0.0}, entity_names=["Pearl"])
        for i in (1, 2, 3, 4)
    ]
    old = _bot(
        [spawns[2], spawns[0], spawns[1], spawns[3]],
        [_t("state_3", "state_1"), _t("state_1", "state_2"), _t("state_2", "state_4"), _t("state_4", "state_3")],
        name="Test Reagent",
    )
    config, report = _rebuild(old, "Reagent Collector", library)

    filled = {s.id: s.params["xyz"]["x"] for s in config.states if s.node_type == "collect_reagent"}
    assert [filled[tid] for tid in ("t_4", "t_5", "t_6")] == [3.0, 1.0, 2.0], "the template's three, in run order"
    assert [line.split()[0] for line in report.grafted] == ["state_4"], "the fourth spawn is carried"
    assert _next(config, "t_6") == "state_4"
    assert _next(config, "state_4") == _by_type(config, "change_realm")[0].id, "and the loop changes realm as before"


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def test_a_dry_run_writes_nothing_and_write_saves_a_bot_that_round_trips(tmp_path, capsys):
    path = tmp_path / "test_boss.json"
    _boss().save(path)
    before = path.read_bytes()

    rft._rebuild_one(str(path), None, write=False)
    assert path.read_bytes() == before

    rft._rebuild_one(str(path), None, write=True)
    rebuilt = BotConfig.load(path)
    assert rebuilt.template is not None
    resaved = tmp_path / "again.json"
    rebuilt.save(resaved)
    assert resaved.read_bytes() == path.read_bytes()
    assert "rebuilt from Boss Farmer" in capsys.readouterr().out


def test_a_bot_that_already_records_a_template_is_refused(tmp_path, library):
    config, _report = _rebuild(_boss(), "Boss Farmer", library)
    path = tmp_path / "done.json"
    config.save(path)

    with pytest.raises(rft.RebuildError, match="already records"):
        rft._rebuild_one(str(path), None, write=False)


def test_a_reagent_bot_with_fewer_spawns_loses_the_templates_spares(library):
    zone = "Celestia/CL_Hub"
    old = _bot(
        [_s("state_1", "collect_reagent", zone=zone, xyz=_A, entity_names=["Pearl"])],
        [_t("state_1", "state_1")],
        name="Test Reagent",
    )
    config, report = _rebuild(old, "Reagent Collector", library)
    spawns = _by_type(config, "collect_reagent")

    assert [s.id for s in spawns] == ["t_4"]
    assert _next(config, "t_4") == _by_type(config, "change_realm")[0].id, "the loop closes round the spares"
    assert report.clean and _steps_left(config, library) == 0


def test_a_wisps_on_the_trip_goes_with_the_trip(library):
    old = _boss(
        more_states=[_s("state_9", "collect_wisps", "Collect mana at the hub"), _s("state_10", "collect_wisps")],
        more_transitions=[_t("state_1", "state_9"), _t("state_9", "state_2"), _t("state_6", "state_10")],
    )
    _config, report = _rebuild(old, "Boss Farmer", library)

    assert any(line.startswith("state_9 ") for line in report.folded), "the template collects before the entry"
    assert report.clean


def test_a_dungeon_entered_on_the_way_to_the_spawns_is_not_spliced_after_them(library):
    """The first run would collect in the hall before entering the dungeon the reagents grow in."""
    hall, dungeon = "Celestia/CL_Hub", "Celestia/Interiors/Spawns"
    old = _bot(
        [
            _s("state_1", "teleport_to_world_hub", zone=hall),
            _s("state_2", "enter_dungeon", from_zone=hall, dungeon_zone=dungeon, sigil_xyz=_A, reset_xyz=_B),
            _s("state_3", "collect_reagent", zone=dungeon, xyz=_A, entity_names=["Pearl"]),
            _s("state_4", "enter_zone", from_zone=dungeon, to_zone=hall, xyz=_A, reset_xyz=_B),
        ],
        [_t("state_1", "state_2"), _t("state_2", "state_3"), _t("state_3", "state_4"), _t("state_4", "state_2")],
        name="Test Reagent",
    )
    _config, report = _rebuild(old, "Reagent Collector", library)

    assert any("on the way in" in line for line in report.problems)
    assert not report.clean
