"""Tests for bartlebot.combat.fight_keys: placing a profile's stored fights on a bot's states.

Pure functions over the strategy models and a BotConfig, so no QApplication. The
Combat tab's half -- seeding from ``bind`` and healing the keys on save -- is in
test_combat_tab.py.
"""

from __future__ import annotations

import json

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.combat.fight_keys import bind, enemy_keys, heal, place, settle
from bartlebot.combat.profile_models import BotCombatStrategy, FightRef, StateAssignment

_DCS_A = "dcs-aaaa"
_DCS_B = "dcs-bbbb"


def _fight(state_id: str, enemy: str | None = None, *, exposed: bool = True) -> StateModel:
    params: dict = {"use_default_strategy": not exposed}
    if enemy is not None:
        params["enemy"] = enemy
    return StateModel(id=state_id, node_type="handle_combat", params=params)


def _bot(*fights: StateModel) -> BotConfig:
    states = [StateModel(id="s0", node_type="sleep"), *fights]
    return BotConfig(id="bot-1", name="Bot", initial="s0", recovery_state="s0", states=states, transitions=[])


def _assignment(dcs_id: str, *fights: tuple[str, str | None]) -> StateAssignment:
    return StateAssignment(
        fights=[FightRef(state=state, enemy=enemy) for state, enemy in fights], duel_combat_strategy_id=dcs_id
    )


def _bcs(*assignments: StateAssignment) -> BotCombatStrategy:
    return BotCombatStrategy(name="BCS", bot_id="bot-1", state_assignments=list(assignments))


# ---------------------------------------------------------------------------
# Enemy keys
# ---------------------------------------------------------------------------


def test_an_enemy_one_exposed_fight_names_is_a_key():
    config = _bot(_fight("state_1", "Lamia"), _fight("state_2", "Cronus"))

    assert enemy_keys(config) == {"Lamia": "state_1", "Cronus": "state_2"}


def test_an_enemy_named_by_two_fights_is_not_a_key():
    config = _bot(_fight("state_1", "Lamia"), _fight("state_2", "Lamia"), _fight("state_3", "Cronus"))

    assert enemy_keys(config) == {"Cronus": "state_3"}


def test_a_fight_that_takes_the_closest_mob_has_no_key():
    config = _bot(_fight("state_1", ""), _fight("state_2", "   "), _fight("state_3"))

    assert enemy_keys(config) == {}


def test_a_key_is_the_name_stripped_and_otherwise_as_typed():
    config = _bot(_fight("state_1", "  Lamia* "), _fight("state_2", "lamia*"))

    assert enemy_keys(config) == {"Lamia*": "state_1", "lamia*": "state_2"}


def test_a_fight_on_the_bot_default_gives_its_enemy_no_key():
    """Only an exposed fight takes a strategy of its own, so only one can be followed."""
    config = _bot(_fight("state_1", "Lamia", exposed=False))

    assert enemy_keys(config) == {}


# ---------------------------------------------------------------------------
# bind: step 1, by enemy
# ---------------------------------------------------------------------------


def test_a_fight_whose_enemy_is_a_key_goes_to_the_node_naming_it():
    """The node was deleted and made again against the same enemy: a new id, the same strategy."""
    assignment = _assignment(_DCS_A, ("state_14", "Lamia"))
    config = _bot(_fight("state_20", "Lamia"))

    assert bind(_bcs(assignment), config) == {"state_20": assignment}


def test_an_enemy_match_wins_over_another_fights_state_id():
    """state_2 is where one fight was stored and where the other's enemy now is."""
    by_state = _assignment(_DCS_A, ("state_2", None))
    by_enemy = _assignment(_DCS_B, ("state_1", "Lamia"))
    config = _bot(_fight("state_2", "Lamia"))

    bound = bind(_bcs(by_state, by_enemy), config)

    assert bound == {"state_2": by_enemy}
    assert [fight.state for _, fight in place([by_state, by_enemy], config).orphans] == ["state_2"]


def test_a_stored_enemy_matches_a_node_that_pads_the_name():
    assignment = _assignment(_DCS_A, ("state_14", "Lamia"))

    assert bind(_bcs(assignment), _bot(_fight("state_20", " Lamia "))) == {"state_20": assignment}


# ---------------------------------------------------------------------------
# bind: step 2, by state
# ---------------------------------------------------------------------------


def test_a_renamed_enemy_is_still_bound_by_its_state():
    assignment = _assignment(_DCS_A, ("state_14", "Lamia"))
    config = _bot(_fight("state_14", "Cronus"))

    assert bind(_bcs(assignment), config) == {"state_14": assignment}


def test_a_fight_with_no_enemy_is_bound_by_its_state():
    assignment = _assignment(_DCS_A, ("state_14", None))

    assert bind(_bcs(assignment), _bot(_fight("state_14"))) == {"state_14": assignment}


def test_an_enemy_named_twice_falls_back_to_state_ids():
    """Two fights against one enemy: the name says which of neither, so the ids decide."""
    first = _assignment(_DCS_A, ("state_1", "Lamia"))
    second = _assignment(_DCS_B, ("state_2", "Lamia"))
    config = _bot(_fight("state_1", "Lamia"), _fight("state_2", "Lamia"))

    assert bind(_bcs(first, second), config) == {"state_1": first, "state_2": second}


def test_two_stored_fights_with_one_enemy_key_share_it_first_come():
    """Only a hand-edited file holds this; the second falls back to its own state."""
    first = _assignment(_DCS_A, ("state_1", "Lamia"))
    second = _assignment(_DCS_B, ("state_2", "Lamia"))
    config = _bot(_fight("state_9", "Lamia"), _fight("state_2", "Cronus"))

    assert bind(_bcs(first, second), config) == {"state_9": first, "state_2": second}


# ---------------------------------------------------------------------------
# bind: step 3, orphans
# ---------------------------------------------------------------------------


def test_changing_the_node_and_the_enemy_at_once_orphans_the_fight():
    assignment = _assignment(_DCS_A, ("state_14", "Lamia"))
    config = _bot(_fight("state_20", "Cronus"))

    assert bind(_bcs(assignment), config) == {}
    assert place([assignment], config).orphans == [(assignment, assignment.fights[0])]


def test_a_fight_whose_state_went_back_to_the_bot_default_is_orphaned():
    assignment = _assignment(_DCS_A, ("state_14", None))
    config = _bot(_fight("state_14", exposed=False))

    assert bind(_bcs(assignment), config) == {}


def test_a_state_that_is_not_a_fight_any_more_is_orphaned():
    assignment = _assignment(_DCS_A, ("s0", None))

    assert bind(_bcs(assignment), _bot()) == {}


def test_one_assignment_can_hold_placed_and_orphaned_fights():
    assignment = _assignment(_DCS_A, ("state_1", None), ("state_404", None))
    config = _bot(_fight("state_1"))

    placement = place([assignment], config)

    assert placement.by_state() == {"state_1": assignment}
    assert [fight.state for _, fight in placement.orphans] == ["state_404"]


def test_binding_changes_nothing_in_the_profile():
    assignment = _assignment(_DCS_A, ("state_14", "Lamia"), ("state_404", None))
    bcs = _bcs(assignment)
    before = bcs.model_dump()

    bind(bcs, _bot(_fight("state_20", "Lamia")))

    assert bcs.model_dump() == before


# ---------------------------------------------------------------------------
# bind with no bot
# ---------------------------------------------------------------------------


def test_with_no_config_fights_are_bound_by_their_raw_state_ids():
    first = _assignment(_DCS_A, ("state_1", "Lamia"), ("state_2", None))
    second = _assignment(_DCS_B, ("state_3", None))

    assert bind(_bcs(first, second), None) == {"state_1": first, "state_2": first, "state_3": second}


# ---------------------------------------------------------------------------
# The old shape
# ---------------------------------------------------------------------------


def test_an_old_state_ids_file_loads_as_fights_with_no_enemy():
    assignment = StateAssignment.model_validate(
        {"state_ids": ["state_1", "state_2"], "duel_combat_strategy_id": _DCS_A}
    )

    assert assignment.fights == [FightRef(state="state_1"), FightRef(state="state_2")]
    assert assignment.state_ids == ["state_1", "state_2"]


def test_an_old_state_ids_file_resolves_the_same_fights_as_before():
    old = BotCombatStrategy.model_validate(
        {
            "name": "BCS",
            "bot_id": "bot-1",
            "state_assignments": [
                {"state_ids": ["state_1", "state_2"], "duel_combat_strategy_id": _DCS_A},
                {"state_ids": ["state_3"], "duel_combat_strategy_id": _DCS_B},
            ],
        }
    )
    config = _bot(_fight("state_1", "Lamia"), _fight("state_2"), _fight("state_3", "Cronus"))

    bound = bind(old, config)

    assert {sid: a.duel_combat_strategy_id for sid, a in bound.items()} == {
        "state_1": _DCS_A,
        "state_2": _DCS_A,
        "state_3": _DCS_B,
    }


def test_a_file_carrying_both_shapes_keeps_its_fights():
    assignment = StateAssignment.model_validate(
        {"state_ids": ["state_1"], "fights": [{"state": "state_2", "enemy": "Lamia"}]}
    )

    assert assignment.fights == [FightRef(state="state_2", enemy="Lamia")]


def test_a_profile_is_written_as_state_and_enemy_pairs(tmp_path):
    bcs = _bcs(_assignment(_DCS_A, ("state_14", "Lamia"), ("state_15", None)))
    path = tmp_path / "bcs.json"

    bcs.save(path)

    written = json.loads(path.read_text(encoding="utf-8"))["state_assignments"]
    assert written == [
        {"fights": [{"state": "state_14", "enemy": "Lamia"}, {"state": "state_15"}], "duel_combat_strategy_id": _DCS_A}
    ]
    assert BotCombatStrategy.load(path) == bcs


def test_loading_an_old_file_and_saving_it_writes_the_new_shape(tmp_path):
    old = BotCombatStrategy.model_validate(
        {"name": "BCS", "bot_id": "bot-1", "state_assignments": [{"state_ids": ["state_1"]}]}
    )
    path = tmp_path / "bcs.json"

    old.save(path)

    assert '"state_ids"' not in path.read_text(encoding="utf-8")
    assert BotCombatStrategy.load(path).state_assignments[0].state_ids == ["state_1"]


# ---------------------------------------------------------------------------
# settle: what a loaded profile becomes in memory
# ---------------------------------------------------------------------------


def test_settling_moves_each_fight_to_the_state_it_is_placed_on():
    assignment = _assignment(_DCS_A, ("state_14", "Lamia"), ("state_2", None), ("state_404", None))
    config = _bot(_fight("state_20", "Lamia"), _fight("state_2"))

    orphans = settle([assignment], config)

    assert assignment.fights == [FightRef(state="state_20", enemy="Lamia"), FightRef(state="state_2")]
    assert orphans == [FightRef(state="state_404")]


# ---------------------------------------------------------------------------
# heal: what a save writes
# ---------------------------------------------------------------------------


def test_healing_writes_each_fight_with_the_enemy_that_is_a_key_now():
    assignment = _assignment(_DCS_A, ("state_1", None), ("state_2", "Stale"), ("state_3", None))
    config = _bot(_fight("state_1", "Lamia"), _fight("state_2", "Cronus"), _fight("state_3"))

    assert heal([assignment], config) == []

    assert assignment.fights == [
        FightRef(state="state_1", enemy="Lamia"),
        FightRef(state="state_2", enemy="Cronus"),
        FightRef(state="state_3"),
    ]


def test_healing_writes_no_enemy_for_one_two_fights_name():
    assignment = _assignment(_DCS_A, ("state_1", "Lamia"), ("state_2", None))
    config = _bot(_fight("state_1", "Lamia"), _fight("state_2", "Lamia"))

    heal([assignment], config)

    assert assignment.fights == [FightRef(state="state_1"), FightRef(state="state_2")]


def test_healing_goes_by_state_not_by_a_stale_enemy():
    """In memory the state is the truth: the tab edits membership by state id."""
    assignment = _assignment(_DCS_A, ("state_1", "Cronus"))
    config = _bot(_fight("state_1", "Lamia"), _fight("state_2", "Cronus"))

    heal([assignment], config)

    assert assignment.fights == [FightRef(state="state_1", enemy="Lamia")]


def test_healing_drops_a_fight_that_is_gone_and_keeps_the_rest_of_its_assignment():
    assignment = _assignment(_DCS_A, ("state_1", None), ("state_404", "Gone"))
    config = _bot(_fight("state_1"))

    dropped = heal([assignment], config)

    assert dropped == [FightRef(state="state_404", enemy="Gone")]
    assert assignment.state_ids == ["state_1"]


def test_a_healed_profile_binds_to_the_same_fights():
    assignment = _assignment(_DCS_A, ("state_1", None), ("state_2", None))
    config = _bot(_fight("state_1", "Lamia"), _fight("state_2"))
    bcs = _bcs(assignment)

    heal(bcs.state_assignments, config)

    assert bind(bcs, config) == {"state_1": assignment, "state_2": assignment}
