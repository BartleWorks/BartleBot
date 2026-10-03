"""The three-way template diff: what a newer version changes in a bot, and applying it.

One small fixture template at v1 and v2, and bots made from v1 that edited some of the
same units. The Setup tab's half -- the review cards, Apply through undo, Finish -- is in
test_setup_panel.py.
"""

from __future__ import annotations

from datetime import date

import pytest

from bartlebot import template_diff
from bartlebot.bot.schema import (
    BotConfig,
    BotOptionModel,
    FlagCondition,
    SetupStep,
    SetupTarget,
    StateModel,
    TemplateMeta,
    TemplateRef,
    TransitionModel,
)
from bartlebot.bot.strategy_models import BotCombatStrategy, FightRef, StateAssignment
from bartlebot.new_bot import BotDetails, StartingPoint, create_bot
from bartlebot.subgraphs import drop_states, member_id, nested_regions
from bartlebot.template_diff import MISSING, Port, Release, Status, TemplateUpdate, apply, diff, finish
from bartlebot.template_store import list_templates, release, save_draft

TEMPLATE_ID = "tpl-fixture"


def _sleep(state_id: str, label: str, duration: float, x: float = 0.0) -> StateModel:
    return StateModel(
        id=state_id, node_type="sleep", label=label, params={"duration": duration}, position={"x": x, "y": 0.0}
    )


def _fight(state_id: str, x: float = 0.0, enemy: str = "") -> StateModel:
    return StateModel(
        id=state_id,
        node_type="handle_combat",
        label="Fight",
        params={"enemy": enemy, "use_default_strategy": False},
        position={"x": x, "y": 0.0},
    )


def _edge(source: str, dest: str, when: str = "SUCCESS", **extra) -> TransitionModel:
    return TransitionModel(trigger="advance", source=source, dest=dest, when=when, **extra)


def _template(states: list[StateModel], edges: list[TransitionModel], **extra) -> BotConfig:
    setup = extra.pop("setup", [SetupStep(text="Name the enemy", targets=[SetupTarget(state="t_2", param="enemy")])])
    return BotConfig(
        id=TEMPLATE_ID,
        name="Fixture Farmer",
        initial="t_1",
        recovery_state="t_1",
        states=states,
        transitions=edges,
        template_meta=TemplateMeta(blurb="A fixture.", setup=setup),
        **extra,
    )


def _v1() -> BotConfig:
    """Start -> Fight -> Wait -> Old -> Start."""
    return _template(
        [_sleep("t_1", "Start", 1, 0), _fight("t_2", 200), _sleep("t_3", "Wait", 2, 400), _sleep("t_4", "Old", 4, 600)],
        [_edge("t_1", "t_2"), _edge("t_2", "t_3"), _edge("t_3", "t_4"), _edge("t_4", "t_1")],
    )


def _v2() -> BotConfig:
    """Begin -> Fight -> Collect -> Wait -> Begin: a node added, one removed, two params and a label changed."""
    return _template(
        [
            _sleep("t_1", "Begin", 5, 0),
            _fight("t_2", 200),
            _sleep("t_5", "Collect", 7, 300),
            _sleep("t_3", "Wait", 3, 400),
        ],
        [_edge("t_1", "t_2"), _edge("t_2", "t_5"), _edge("t_5", "t_3"), _edge("t_3", "t_1")],
    )


def _bot(version: BotConfig, base: int = 1) -> BotConfig:
    """A bot made from ``version``, with its fill-in filled."""
    bot = version.model_copy(deep=True)
    bot.id, bot.name = "bot-1", "My Bot"
    bot.template_meta = None
    bot.template = TemplateRef(id=TEMPLATE_ID, version=base)
    next(s for s in bot.states if s.id == "t_2").params["enemy"] = "Lamia"
    return bot


def _update(base: BotConfig | None = None, *releases: BotConfig) -> TemplateUpdate:
    later = releases or (_v2(),)
    return TemplateUpdate(
        TEMPLATE_ID,
        "Fixture Farmer",
        1,
        base or _v1(),
        [Release(n, f"Release {n}", config) for n, config in enumerate(later, start=2)],
    )


def _state(config: BotConfig, state_id: str) -> StateModel:
    return next(s for s in config.states if s.id == state_id)


def _by_title(changes, text: str):
    return next(c for c in changes if text in c.title)


def _edges(config: BotConfig) -> set[tuple[str, str | None, str]]:
    return {(t.source, t.when, t.dest) for t in config.transitions}


# ---------------------------------------------------------------------------
# The outcome table
# ---------------------------------------------------------------------------


def test_a_unit_the_template_did_not_change_is_not_shown_whatever_the_bot_did():
    mine = _bot(_v1())
    _state(mine, "t_2").label = "Defeat Lamia"

    changes = diff(_update(), mine)

    assert not any("Fight" in c.title or "Defeat Lamia" in c.title for c in changes)


def test_a_change_the_bot_never_touched_is_offered():
    change = _by_title(diff(_update(), _bot(_v1())), "Start · Duration")

    assert change.status is Status.OFFERED
    assert change.detail == "1 → 5"


def test_a_change_the_bot_already_has_is_hidden():
    mine = _bot(_v1())
    _state(mine, "t_1").params["duration"] = 5

    assert not any("Duration" in c.title and "Start" in c.title for c in diff(_update(), mine))


def test_a_unit_both_changed_is_a_conflict():
    mine = _bot(_v1())
    _state(mine, "t_3").params["duration"] = 9

    change = _by_title(diff(_update(), mine), "Wait · Duration")

    assert change.status is Status.CONFLICT
    assert "Yours: 9" in change.detail


def test_a_change_on_a_node_the_user_deleted_is_hidden():
    mine = _bot(_v1())
    mine.states = [s for s in mine.states if s.id != "t_3"]
    mine.transitions = [t for t in mine.transitions if "t_3" not in (t.source, t.dest)]

    changes = diff(_update(), mine)

    assert not any("Wait" in c.title for c in changes)


def test_a_change_needing_a_node_the_bot_does_not_have_cannot_be_applied():
    theirs = _v1()
    theirs.transitions.append(_edge("t_1", "t_3", "FAILURE"))
    mine = _bot(_v1())
    mine.states = [s for s in mine.states if s.id != "t_3"]
    mine.transitions = [t for t in mine.transitions if "t_3" not in (t.source, t.dest)]

    (change,) = diff(_update(None, theirs), mine)

    assert change.status is Status.BLOCKED
    assert change.reason == "This bot has no 'Wait'."
    with pytest.raises(ValueError):
        apply(mine, change)


def test_a_filled_fill_in_is_never_a_unit():
    """The enemy is the user's to fill: blank in both versions, and no business of the diff."""
    theirs = _v2()
    assert not any("Enemy" in c.title for c in diff(_update(), _bot(_v1())))

    # Nor is a param a newer version turns into a fill-in, whatever value it held before.
    theirs.template_meta.setup.append(
        SetupStep(text="Set the wait", targets=[SetupTarget(state="t_3", param="duration")])
    )
    assert not any("Wait · Duration" in c.title for c in diff(_update(None, theirs), _bot(_v1())))


def test_a_node_the_bot_added_is_never_a_unit():
    mine = _bot(_v1())
    mine.states.append(_sleep("state_1", "Mine", 3))
    mine.transitions.append(_edge("state_1", "t_1"))
    plain = diff(_update(), _bot(_v1()))

    assert [c.id for c in diff(_update(), mine)] == [c.id for c in plain]


def test_a_bot_at_its_own_base_has_no_changes():
    assert diff(_update(None, _v1()), _bot(_v1())) == []


def test_positions_and_the_bots_own_metadata_are_never_units():
    theirs = _v1()
    theirs.name, theirs.description, theirs.tags = "Renamed", "New words", ["Boss"]
    for state in theirs.states:
        state.position = {"x": 999.0, "y": 999.0}

    assert diff(_update(None, theirs), _bot(_v1())) == []


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def test_an_added_node_comes_with_its_params_its_edges_and_the_edge_that_now_points_at_it():
    change = _by_title(diff(_update(), _bot(_v1())), "Add 'Collect'")

    keys = {u.key for u in change.units}
    assert ("node", "t_5") in keys
    assert ("param", "t_5", "duration") in keys
    assert ("slot", "t_5", "SUCCESS", ()) in keys  # its own edge
    assert ("slot", "t_2", "SUCCESS", ()) in keys  # the edge that now points at it
    assert change.status is Status.OFFERED
    assert change.added == ["t_5"]


def test_a_removed_node_goes_with_the_edge_that_used_to_point_at_it():
    change = _by_title(diff(_update(), _bot(_v1())), "Remove 'Old'")

    keys = {u.key for u in change.units}
    assert {("node", "t_4"), ("slot", "t_4", "SUCCESS", ()), ("slot", "t_3", "SUCCESS", ())} <= keys
    assert change.removed == ["t_4"]


def test_every_other_unit_is_a_change_of_its_own():
    changes = diff(_update(), _bot(_v1()))

    assert sorted(c.title for c in changes) == [
        "Add 'Collect'",
        "Remove 'Old'",
        "Rename 'Start'",
        "Start · Duration (seconds)",
        "Wait · Duration (seconds)",
    ]


def test_a_removal_of_a_node_the_user_edited_is_a_conflict():
    mine = _bot(_v1())
    _state(mine, "t_4").params["duration"] = 60

    assert _by_title(diff(_update(), mine), "Remove 'Old'").status is Status.CONFLICT


def test_a_removal_that_drops_edges_the_bot_wired_is_a_conflict_naming_what_to_rewire():
    """A node the user put after a template node, and back into it: the gauntlet's fight, in small.

    The slot out of 'Old' on failure is empty in both versions, and an edge out of a node
    the bot added is never a unit, so no unit holds either edge. Removing 'Old' drops them
    all the same, and calling that clean left the user's node wired to nothing.
    """
    mine = _bot(_v1())
    mine.states.append(_sleep("state_1", "Mine", 3))
    mine.transitions += [_edge("t_4", "state_1", "FAILURE"), _edge("state_1", "t_4", "FAILURE")]

    change = _by_title(diff(_update(), mine), "Remove 'Old'")

    assert change.status is Status.CONFLICT
    assert change.detail.endswith("Drops 2 edges you added: 'Mine' will need rewiring")
    assert "state_1" in change.nodes
    assert not any("state_1" in (source, dest) for source, _, dest in _edges(apply(mine, change)))


def test_a_template_edge_the_bot_rewired_into_a_removed_node_is_its_own_too():
    mine = _bot(_v1())
    mine.transitions.append(_edge("t_1", "t_4", "FAILURE"))

    change = _by_title(diff(_update(), mine), "Remove 'Old'")

    assert change.status is Status.CONFLICT
    assert change.detail.endswith("Drops 1 edge you added: 'Start' will need rewiring")


def test_a_removal_that_touches_only_the_templates_own_edges_is_still_offered():
    mine = _bot(_v1())
    mine.states.append(_sleep("state_1", "Mine", 3))
    mine.transitions.append(_edge("t_3", "state_1", "FAILURE"))

    change = _by_title(diff(_update(), mine), "Remove 'Old'")

    assert change.status is Status.OFFERED
    assert "you added" not in change.detail


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def test_applying_one_change_touches_nothing_else_and_leaves_the_bot_alone():
    mine = _bot(_v1())
    before = mine.model_dump()
    change = _by_title(diff(_update(), mine), "Start · Duration")

    after = apply(mine, change)

    assert mine.model_dump() == before
    assert _state(after, "t_1").params["duration"] == 5
    assert _state(after, "t_1").label == "Start"
    assert _edges(after) == _edges(mine)


def test_an_applied_change_is_not_offered_again():
    mine = _bot(_v1())
    after = apply(mine, _by_title(diff(_update(), mine), "Start · Duration"))

    assert not any("Start · Duration" in c.title for c in diff(_update(), after))


def test_applying_the_added_node_wires_it_in():
    mine = _bot(_v1())

    after = apply(mine, _by_title(diff(_update(), mine), "Add 'Collect'"))

    assert _state(after, "t_5").params == {"duration": 7}
    assert ("t_2", "SUCCESS", "t_5") in _edges(after)
    assert ("t_5", "SUCCESS", "t_3") in _edges(after)
    assert ("t_2", "SUCCESS", "t_3") not in _edges(after)


def test_applying_the_removal_drops_the_node_and_rewires_around_it():
    mine = _bot(_v1())

    after = apply(mine, _by_title(diff(_update(), mine), "Remove 'Old'"))

    assert all(s.id != "t_4" for s in after.states)
    assert ("t_3", "SUCCESS", "t_1") in _edges(after)
    assert not any("t_4" in (source, dest) for source, _, dest in _edges(after))


def test_applying_every_change_in_turn_reaches_the_template_and_keeps_the_fill_in():
    mine = _bot(_v1())
    update = _update()

    while changes := diff(update, mine):
        mine = apply(mine, changes[0])

    theirs = _bot(_v2())
    assert _edges(mine) == _edges(theirs)
    assert {s.id: (s.label, s.node_type) for s in mine.states} == {s.id: (s.label, s.node_type) for s in theirs.states}
    assert _state(mine, "t_1").params == {"duration": 5}
    assert _state(mine, "t_2").params["enemy"] == "Lamia"


def test_taking_the_templates_side_of_a_conflict():
    mine = _bot(_v1())
    _state(mine, "t_3").params["duration"] = 9

    after = apply(mine, _by_title(diff(_update(), mine), "Wait · Duration"))

    assert _state(after, "t_3").params["duration"] == 3


def test_an_edge_the_bot_added_survives_a_rewire_of_another_slot():
    mine = _bot(_v1())
    mine.states.append(_sleep("state_1", "Mine", 3))
    mine.transitions.append(_edge("t_3", "state_1", "FAILURE"))

    after = apply(mine, _by_title(diff(_update(), mine), "Remove 'Old'"))

    assert ("t_3", "FAILURE", "state_1") in _edges(after)


def test_a_guarded_edge_and_a_plain_one_are_different_slots():
    theirs = _v1()
    theirs.options = [BotOptionModel(name="fast", type="bool", default=False)]
    theirs.transitions.insert(0, _edge("t_1", "t_3", flags=[FlagCondition(option="fast")]))
    mine = _bot(_v1())

    changes = diff(_update(None, theirs), mine)
    after = mine
    for change in changes:
        after = apply(after, change)

    assert sorted(c.title for c in changes) == ["Option 'fast'", "Start · on SUCCESS (with option conditions)"]
    assert ("t_1", "SUCCESS", "t_2") in _edges(after)
    guarded = next(t for t in after.transitions if t.dest == "t_3" and t.source == "t_1")
    assert guarded.flags == [FlagCondition(option="fast")]
    assert [o.name for o in after.options] == ["fast"]


def test_a_changed_role_is_a_unit():
    theirs = _v1()
    theirs.recovery_state = "t_3"
    mine = _bot(_v1())

    (change,) = diff(_update(None, theirs), mine)

    assert change.title == "Recovery node"
    assert apply(mine, change).recovery_state == "t_3"


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


def test_an_added_node_is_placed_beside_the_node_it_is_wired_from():
    """As far from Fight as in the template, from where Fight is in the bot."""
    mine = _bot(_v1())
    _state(mine, "t_2").position = {"x": 1000.0, "y": 500.0}

    after = apply(mine, _by_title(diff(_update(), mine), "Add 'Collect'"))

    assert _state(after, "t_5").position == {"x": 1100.0, "y": 500.0}


def test_the_editor_decides_where_a_node_goes_when_its_spot_is_taken():
    """Taken by something the node does not lead on to, so there are no columns to move over for it."""
    mine = _bot(_v1())
    _state(mine, "t_3").position = {"x": -400.0, "y": 0.0}
    mine.states.append(_sleep("state_9", "Mine", 1, 300))
    asked: list[dict] = []

    def place(wanted: dict) -> dict:
        asked.append(wanted)
        return {"x": -50.0, "y": -60.0}

    after = apply(mine, _by_title(diff(_update(), mine), "Add 'Collect'"), place=place)

    assert asked == [{"x": 300.0, "y": 0.0}]
    assert _state(after, "t_5").position == {"x": -50.0, "y": -60.0}


# ---------------------------------------------------------------------------
# A subgraph node: inputs, and edges by port
# ---------------------------------------------------------------------------


def _boss() -> tuple[BotConfig, BotConfig]:
    """The shipped Boss Farmer's first release, and a bot made from it."""
    template = next(t for t in list_templates() if t.name == "Boss Farmer")
    version = BotConfig.load(template.version_path(1))
    bot = create_bot(StartingPoint(template.id, 1, version), BotDetails(name="Boss Bot"))
    return version, bot


def _sync_label(config: BotConfig) -> str:
    return next(r for r in config.regions if r.id == "t_sync_players_1").label


def test_an_edge_out_of_a_subgraph_node_is_compared_by_port_and_applied_to_its_members():
    v1, mine = _boss()
    theirs = v1.model_copy(deep=True)
    leaving = next(t for t in theirs.transitions if t.source.startswith("t_sync_players_1_") and t.dest == "t_1")
    leaving.dest = "t_2"

    (change,) = diff(TemplateUpdate(v1.id, "Boss Farmer", 1, v1, [Release(2, "Rewire", theirs)]), mine)

    assert change.units[0].key == ("slot", Port("t_sync_players_1", "exit_1"), None, ())
    assert change.title == f"{_sync_label(v1)} · on port exit_1"
    assert (leaving.source, "SUCCESS", "t_2") in _edges(apply(mine, change))


def test_a_role_on_a_subgraph_node_is_compared_by_port():
    v1, mine = _boss()
    theirs = v1.model_copy(deep=True)
    theirs.initial = "t_sync_players_1_state_1"

    (change,) = diff(TemplateUpdate(v1.id, "Boss Farmer", 1, v1, [Release(2, "Start at relog", theirs)]), mine)

    assert change.units[0].theirs == Port("t_sync_players_1", "entry_recover")
    assert apply(mine, change).initial == "t_sync_players_1_state_1"


def test_a_subgraph_nodes_members_and_snapshot_are_never_units():
    v1, mine = _boss()
    theirs = v1.model_copy(deep=True)
    region = next(r for r in theirs.regions if r.source is not None)
    region.source.version += 1
    next(s for s in theirs.states if s.id == "t_sync_players_1_state_1").params["anything"] = 1

    assert diff(TemplateUpdate(v1.id, "Boss Farmer", 1, v1, [Release(2, "Snapshot", theirs)]), mine) == []


def test_a_changed_input_is_a_unit_and_a_fill_in_input_is_not():
    v1, mine = _boss()
    theirs = v1.model_copy(deep=True)
    theirs.template_meta.setup = [
        SetupStep(text="Zone", targets=[SetupTarget(region="t_sync_players_1", input="state_2.zone")])
    ]
    inputs = next(r for r in theirs.regions if r.source is not None).source.inputs
    inputs["state_2.zone"] = "WizardCity/WC_Ravenwood"
    inputs["state_3.realm"] = "Wu"

    (change,) = diff(TemplateUpdate(v1.id, "Boss Farmer", 1, v1, [Release(2, "Inputs", theirs)]), mine)

    assert change.units[0].key == ("input", "t_sync_players_1", "state_3.realm")
    after = apply(mine, change)
    assert next(r for r in after.regions if r.source is not None).source.inputs["state_3.realm"] == "Wu"


def test_every_shipped_template_offers_nothing_to_a_bot_made_from_its_latest_release():
    for template in list_templates():
        if template.latest_version is None:
            continue
        latest = BotConfig.load(template.version_path(template.latest_version))
        bot = create_bot(StartingPoint(template.id, template.latest_version, latest), BotDetails(name="Bot"))
        update = TemplateUpdate(template.id, template.name, template.latest_version, latest, [Release(99, "", latest)])

        assert diff(update, bot) == [], template.name


def test_every_shipped_draft_applies_cleanly_to_a_bot_made_from_its_first_release():
    """The real change waiting for the first bots: v1 to what the draft holds now."""
    for template in list_templates():
        v1 = BotConfig.load(template.version_path(1))
        draft = BotConfig.load(template.draft_path)
        bot = create_bot(StartingPoint(template.id, 1, v1), BotDetails(name="Bot"))
        update = TemplateUpdate(template.id, template.name, 1, v1, [Release(2, "Draft", draft)])

        changes = diff(update, bot)
        assert all(c.status is Status.OFFERED for c in changes), template.name
        while remaining := diff(update, bot):
            bot = apply(bot, remaining[0])
        assert diff(update, bot) == [], template.name


# ---------------------------------------------------------------------------
# The store: which bots are behind, and finishing
# ---------------------------------------------------------------------------


def _store(tmp_path, *versions: BotConfig) -> None:
    for number, config in enumerate(versions, start=1):
        save_draft(config, tmp_path)
        release(TEMPLATE_ID, f"Release {number}", root=tmp_path, today=date(2026, 10, 1))


def test_a_bot_at_the_latest_version_has_no_update(tmp_path):
    _store(tmp_path, _v1())

    assert template_diff.load_update(_bot(_v1()), tmp_path) is None


def test_a_bot_made_from_no_template_has_no_update(tmp_path):
    _store(tmp_path, _v1(), _v2())
    bot = _bot(_v1())
    bot.template = None

    assert template_diff.load_update(bot, tmp_path) is None
    assert template_diff.review(None, bot) is None


def test_a_bot_whose_template_is_not_in_the_store_has_no_update(tmp_path):
    assert template_diff.load_update(_bot(_v1()), tmp_path) is None


def test_an_update_holds_the_base_and_every_release_since(tmp_path):
    _store(tmp_path, _v1(), _v2(), _v2())

    update = template_diff.load_update(_bot(_v1()), tmp_path)

    assert (update.base_version, update.latest_version) == (1, 3)
    assert [(r.version, r.note) for r in update.releases] == [(2, "Release 2"), (3, "Release 3")]


def test_changes_are_listed_under_the_release_that_made_them(tmp_path):
    v3 = _v2()
    _state(v3, "t_5").params["duration"] = 8
    v3.states.append(_sleep("t_6", "Extra", 1, 500))
    v3.transitions.append(_edge("t_1", "t_6", "FAILURE"))
    _store(tmp_path, _v1(), _v2(), v3)
    mine = _bot(_v1())

    found = template_diff.review(template_diff.load_update(mine, tmp_path), mine)

    assert (found.name, found.base_version, found.latest_version) == ("Fixture Farmer", 1, 3)
    titles = {version: sorted(c.title for c in changes) for version, _note, changes in found.sections}
    assert titles[2] == ["Remove 'Old'", "Rename 'Start'", "Start · Duration (seconds)", "Wait · Duration (seconds)"]
    # Collect arrived in v2, but v3 touched it again: it is listed where it was last changed.
    assert titles[3] == ["Add 'Collect'", "Add 'Extra'"]
    assert [note for _version, note, _changes in found.sections] == ["Release 2", "Release 3"]


def test_finishing_advances_the_base_so_a_skipped_change_is_not_offered_again(tmp_path):
    _store(tmp_path, _v1(), _v2())
    mine = _bot(_v1())
    update = template_diff.load_update(mine, tmp_path)
    assert diff(update, mine)  # skipped, every one of them

    done = finish(mine, update)

    assert done.template == TemplateRef(id=TEMPLATE_ID, version=2)
    assert mine.template.version == 1
    assert template_diff.load_update(done, tmp_path) is None
    assert done.model_dump(exclude={"template"}) == mine.model_dump(exclude={"template"})


def test_a_library_row_knows_a_bot_is_behind_from_its_file(tmp_path):
    _store(tmp_path, _v1())
    data = _bot(_v1()).file_dump()
    assert template_diff.behind_in_file(data, tmp_path) is False

    _store(tmp_path, _v2())  # v2 is released: the cached answer has to move with the index

    assert template_diff.behind_in_file(data, tmp_path) is True
    assert template_diff.behind_in_file({"name": "No template"}, tmp_path) is False
    assert template_diff.behind_in_file({"template": {"id": "gone", "version": 1}}, tmp_path) is False


# ---------------------------------------------------------------------------
# A fight with a strategy
# ---------------------------------------------------------------------------


def _profile(state_id: str = "t_2", enemy: str | None = "Lamia") -> BotCombatStrategy:
    return BotCombatStrategy(
        name="BCS",
        bot_id="bot-1",
        state_assignments=[
            StateAssignment(fights=[FightRef(state=state_id, enemy=enemy)], duel_combat_strategy_id="dcs-1")
        ],
    )


def test_removing_a_fight_with_a_strategy_is_named():
    theirs = _v1()
    theirs.states = [s for s in theirs.states if s.id != "t_2"]
    theirs.transitions = [_edge("t_1", "t_3"), _edge("t_3", "t_4"), _edge("t_4", "t_1")]
    theirs.template_meta.setup = []
    mine = _bot(_v1())
    after = apply(mine, _by_title(diff(_update(None, theirs), mine), "Remove 'Fight'"))

    assert template_diff.strategies_lost(_profile(), mine, after) == ["Fight"]


def test_a_fight_replaced_against_the_same_enemy_keeps_its_strategy():
    mine = _bot(_v1())
    after = mine.model_copy(deep=True)
    _state(after, "t_2").id = "t_9"
    for t in after.transitions:
        t.source, t.dest = t.source.replace("t_2", "t_9"), t.dest.replace("t_2", "t_9")

    assert template_diff.strategies_lost(_profile(), mine, after) == []
    assert template_diff.strategies_lost(_profile(enemy=None), mine, after) == ["Fight"]


def test_missing_reads_as_missing():
    assert repr(MISSING) == "MISSING"


# ---------------------------------------------------------------------------
# Finding a change again, and where an applied option lands
# ---------------------------------------------------------------------------


def _guarded(dest_on: str, dest_off: str) -> BotConfig:
    """Start leaves on SUCCESS two ways, by the option A: on to one node, off to another."""
    return _template(
        [_sleep("t_1", "Start", 1), _sleep("t_2", "X", 1, 200), _sleep("t_3", "Y", 1, 400), _sleep("t_4", "Z", 1, 600)],
        [
            _edge("t_1", dest_on, flags=[FlagCondition(option="A", negate=False)]),
            _edge("t_1", dest_off, flags=[FlagCondition(option="A", negate=True)]),
        ],
        options=[BotOptionModel(name="A", type="bool", default=True)],
        setup=[],
    )


def test_two_guarded_edges_from_one_outcome_are_found_by_ids_of_their_own():
    """Both rewired in one release. They shared an id, so Apply on the second card applied the first."""
    v1, v2 = _guarded("t_2", "t_3"), _guarded("t_3", "t_4")
    mine = _bot(v1)
    update = _update(v1, v2)

    changes = diff(update, mine)
    off = next(c for c in changes if "'Z'" in c.detail)

    assert len(changes) == 2 and len({c.id for c in changes}) == 2
    assert template_diff.review(update, mine).change(off.id).detail == off.detail
    assert _edges(apply(mine, off)) == {("t_1", "SUCCESS", "t_2"), ("t_1", "SUCCESS", "t_4")}


def test_an_applied_option_keeps_its_place_in_the_list():
    def version(default: int) -> BotConfig:
        options = [BotOptionModel(name="A", type="int", default=default), BotOptionModel(name="B", type="bool")]
        return _template([_sleep("t_1", "Start", 1), _fight("t_2", 200)], [], options=options, setup=[])

    mine = _bot(version(1))

    (change,) = diff(_update(version(1), version(2)), mine)
    after = apply(mine, change)

    assert [(o.name, o.default) for o in after.options] == [("A", 2), ("B", None)]


# ---------------------------------------------------------------------------
# A whole subgraph node, added or removed
# ---------------------------------------------------------------------------


def _without_sync(config: BotConfig) -> BotConfig:
    """``config`` with its Sync players subgraph node taken out: its members, their edges, its regions."""
    new = config.model_copy(deep=True)
    region = next(r for r in new.regions if r.source is not None)
    gone = {r.id for r in [region, *nested_regions(new, region)]}
    members = set(region.members)
    new.regions = [r for r in new.regions if r.id not in gone]
    drop_states(new, members)
    return new


def _members(config: BotConfig) -> set[str]:
    return {s.id for s in config.states if s.id.startswith("t_sync_players_1_")}


def test_applying_a_removed_subgraph_node_takes_its_members_and_their_edges():
    v1, mine = _boss()
    theirs = _without_sync(v1)
    update = TemplateUpdate(v1.id, "Boss Farmer", 1, v1, [Release(2, "No sync", theirs)])

    removal = next(c for c in diff(update, mine) if "t_sync_players_1" in c.removed)
    after = apply(mine, removal)

    assert removal.status is Status.OFFERED and removal.title == f"Remove '{_sync_label(v1)}'"
    assert not _members(after) and all(r.source is None for r in after.regions)
    ids = {s.id for s in after.states}
    assert all(t.source in ids and t.dest in ids for t in after.transitions)
    assert after.initial in ids and after.recovery_state in ids
    assert not any("t_sync_players_1" in c.removed for c in diff(update, after)), "not offered again"


def test_removing_a_subgraph_node_the_bot_wired_into_is_a_conflict():
    """An edge into a port lands on a member; it is the bot's own all the same, and its edges inside are not."""
    v1, mine = _boss()
    region = next(r for r in mine.regions if r.source is not None)
    entry = next(p for p in region.source.ports.values() if p.member is not None)
    mine.states.append(_sleep("state_1", "Mine", 3))
    mine.transitions.append(_edge("state_1", member_id(region.id, entry.member)))
    update = TemplateUpdate(v1.id, "Boss Farmer", 1, v1, [Release(2, "No sync", _without_sync(v1))])

    removal = next(c for c in diff(update, mine) if "t_sync_players_1" in c.removed)

    assert removal.status is Status.CONFLICT
    assert removal.detail.endswith("Drops 1 edge you added: 'Mine' will need rewiring")


def test_applying_an_added_subgraph_node_brings_its_members_and_wires_its_ports():
    v2, _ = _boss()
    v1 = _without_sync(v2)
    mine = create_bot(StartingPoint(v1.id, 1, v1), BotDetails(name="Boss Bot"))
    update = TemplateUpdate(v1.id, "Boss Farmer", 1, v1, [Release(2, "Sync first", v2)])

    addition = next(c for c in diff(update, mine) if "t_sync_players_1" in c.added)
    after = apply(mine, addition)

    assert addition.status is Status.OFFERED and addition.title == f"Add '{_sync_label(v2)}'"
    assert _members(after) == _members(v2)
    region = next(r for r in after.regions if r.id == "t_sync_players_1")
    assert region.source == next(r for r in v2.regions if r.id == "t_sync_players_1").source
    touching = lambda c: {e for e in _edges(c) if e[0] in _members(v2) or e[2] in _members(v2)}  # noqa: E731
    assert touching(after) == touching(v2), "its own edges, and every edge into and out of its ports"
    assert (after.initial, after.recovery_state) == (v2.initial, v2.recovery_state)
    assert not any("t_sync_players_1" in c.added for c in diff(update, after)), "not offered again"


# ---------------------------------------------------------------------------
# A node inserted between two others makes room for itself
# ---------------------------------------------------------------------------


def test_the_grid_this_module_assumes_is_the_canvas_grid():
    from bartlebot.gui.canvas.node_item import GRID_W, NODE_H, NODE_W  # noqa: PLC0415

    assert (template_diff.COLUMN_WIDTH, template_diff.NODE_WIDTH, template_diff.NODE_HEIGHT) == (GRID_W, NODE_W, NODE_H)


def test_a_node_inserted_in_front_of_another_moves_the_columns_over_and_takes_its_place():
    """Collect goes between Fight and Wait. It used to land on the nearest free cell, off the line."""
    mine = _bot(_v1())
    mine.states.append(_sleep("state_9", "Mine, under Wait", 1, 400))
    _state(mine, "state_9").position["y"] = 180.0
    placed: list[dict] = []

    after = apply(mine, _by_title(diff(_update(), mine), "Add 'Collect'"), place=placed.append)

    column = template_diff.COLUMN_WIDTH
    assert placed == [], "there was room made, so nothing to ask the editor"
    assert _state(after, "t_5").position == {"x": 300.0, "y": 0.0}
    assert _state(after, "t_3").position == {"x": 400.0 + column, "y": 0.0}
    assert _state(after, "t_4").position == {"x": 600.0 + column, "y": 0.0}, "every column to the right"
    assert _state(after, "state_9").position == {"x": 400.0 + column, "y": 180.0}, "all of each, not only downstream"
    assert _state(after, "t_1").position["x"] == 0.0 and _state(after, "t_2").position["x"] == 200.0
    assert _state(mine, "t_3").position == {"x": 400.0, "y": 0.0}, "the bot itself is untouched"


def test_a_subgraph_node_moves_over_as_one():
    _, mine = _boss()
    region = next(r for r in mine.regions if r.id == "t_sync_players_1")
    anchor = dict(region.source.anchor)
    before = {m: dict(_state(mine, m).position) for m in region.members}
    theirs = mine.model_copy(deep=True)
    theirs.states.append(_sleep("t_99", "First", 1))
    theirs.transitions.append(_edge("t_99", region.members[0]))

    assert template_diff._make_room(mine, theirs, "t_99", anchor)

    column = template_diff.COLUMN_WIDTH
    assert region.source.anchor == {**anchor, "x": anchor["x"] + column}
    assert {m: _state(mine, m).position for m in region.members} == {
        m: {**spot, "x": spot["x"] + column} for m, spot in before.items()
    }
