"""Tests for bartlebot.combat_grouping.

Pure functions over strategy models — no QApplication, same as the
combat_warnings tests next door.  The behavior under test is what the combat
sidebar draws and what "Applies to…" does, expressed without any widgets.
"""

from __future__ import annotations

from bartlebot.bot.schema import StateModel
from bartlebot.bot.strategy_models import StateAssignment
from bartlebot.combat_grouping import (
    apply_membership,
    build_bindings,
    detach_state,
    group_bindings,
    merge_linked,
    split_unconfigured,
    state_label,
)

_DCS_A = "dcs-aaaa"
_DCS_B = "dcs-bbbb"


def _state(state_id: str, label: str | None = None) -> StateModel:
    return StateModel(id=state_id, node_type="handle_combat", label=label)


def _linked(dcs_id: str, *state_ids: str) -> StateAssignment:
    return StateAssignment(state_ids=list(state_ids), duel_combat_strategy_id=dcs_id)


def _empty(*state_ids: str) -> StateAssignment:
    """An untouched placeholder: unlinked, no player assignments."""
    return StateAssignment(state_ids=list(state_ids))


# ---------------------------------------------------------------------------
# merge_linked
# ---------------------------------------------------------------------------


def test_separate_assignments_sharing_a_strategy_are_merged():
    """The shape bot_strategies/f77a6a91-….json is actually in."""
    merged = merge_linked([_linked(_DCS_A, "state_1"), _linked(_DCS_A, "state_2")])

    assert len(merged) == 1
    assert merged[0].state_ids == ["state_1", "state_2"]


def test_merge_keeps_the_first_occurrence_so_file_order_survives():
    first = _linked(_DCS_A, "state_1")
    merged = merge_linked([first, _linked(_DCS_B, "state_2"), _linked(_DCS_A, "state_3")])

    assert merged[0] is first
    assert [a.duel_combat_strategy_id for a in merged] == [_DCS_A, _DCS_B]


def test_different_strategies_are_not_merged():
    merged = merge_linked([_linked(_DCS_A, "state_1"), _linked(_DCS_B, "state_2")])

    assert len(merged) == 2


def test_unlinked_assignments_never_merge():
    """Each is one fight on the default; there is no shared identity to merge on."""
    merged = merge_linked([_empty("state_1"), _empty("state_2")])

    assert len(merged) == 2
    assert [a.state_ids for a in merged] == [["state_1"], ["state_2"]]


def test_merge_does_not_duplicate_a_state_listed_twice():
    merged = merge_linked([_linked(_DCS_A, "state_1"), _linked(_DCS_A, "state_1", "state_2")])

    assert merged[0].state_ids == ["state_1", "state_2"]


def test_merge_of_nothing_is_nothing():
    assert merge_linked([]) == []


# ---------------------------------------------------------------------------
# build_bindings
# ---------------------------------------------------------------------------


def test_every_state_in_a_group_maps_to_one_shared_object():
    """The aliasing that used to be a bug is now the represented intent."""
    bindings = build_bindings([_linked(_DCS_A, "state_1", "state_2", "state_3")])

    assert bindings["state_1"] is bindings["state_2"] is bindings["state_3"]


def test_build_bindings_merges_before_mapping():
    bindings = build_bindings([_linked(_DCS_A, "state_1"), _linked(_DCS_A, "state_2")])

    assert bindings["state_1"] is bindings["state_2"]


def test_unrelated_states_get_distinct_objects():
    bindings = build_bindings([_linked(_DCS_A, "state_1"), _linked(_DCS_B, "state_2")])

    assert bindings["state_1"] is not bindings["state_2"]


# ---------------------------------------------------------------------------
# group_bindings
# ---------------------------------------------------------------------------


def _mirror_lake() -> tuple[dict, list[StateModel]]:
    """The four-fight shared-strategy case that drew four identical panels."""
    exposed = [
        _state("state_10", "Defeat Tse-Tse Snaketail"),
        _state("state_14", "Defeat Spectral Guardians"),
        _state("state_19", "Defeat Goliath Bruisers"),
        _state("state_22", "Defeat Starburst Spiders"),
    ]
    bindings = build_bindings([_linked(_DCS_A, "state_10", "state_14", "state_19", "state_22")])
    return bindings, exposed


def test_four_fights_sharing_a_strategy_collapse_to_one_group():
    bindings, exposed = _mirror_lake()

    groups = group_bindings(bindings, exposed)

    assert len(groups) == 1
    assert groups[0].state_ids == ["state_10", "state_14", "state_19", "state_22"]


def test_configured_groups_come_before_unassigned_fights():
    exposed = [_state("state_1"), _state("state_2"), _state("state_3")]
    bindings = {"state_1": _empty("state_1"), "state_2": _linked(_DCS_A, "state_2"), "state_3": _empty("state_3")}

    groups = group_bindings(bindings, exposed)

    assert [g.state_ids for g in groups] == [["state_2"], ["state_1"], ["state_3"]]
    assert [g.is_configured for g in groups] == [True, False, False]


def test_fsm_order_is_preserved_within_each_half():
    exposed = [_state(f"state_{i}") for i in range(1, 5)]
    shared = _linked(_DCS_A, "state_2", "state_4")
    bindings = {
        "state_1": _empty("state_1"),
        "state_2": shared,
        "state_3": _empty("state_3"),
        "state_4": shared,
    }

    groups = group_bindings(bindings, exposed)

    assert [g.state_ids for g in groups] == [["state_2", "state_4"], ["state_1"], ["state_3"]]


def test_a_group_is_ordered_by_its_earliest_member():
    exposed = [_state("state_1"), _state("state_2"), _state("state_3")]
    late = _linked(_DCS_B, "state_3")
    early = _linked(_DCS_A, "state_2")
    bindings = {"state_1": _empty("state_1"), "state_2": early, "state_3": late}

    groups = group_bindings(bindings, exposed)

    assert [g.assignment.duel_combat_strategy_id for g in groups[:2]] == [_DCS_A, _DCS_B]


def test_an_empty_inline_config_counts_as_unassigned():
    """resolve_combat treats it as no assignment, so the sidebar must agree."""
    groups = group_bindings({"state_1": _empty("state_1")}, [_state("state_1")])

    assert groups[0].is_configured is False


def test_states_with_no_binding_are_skipped_rather_than_crashing():
    groups = group_bindings({}, [_state("state_1")])

    assert groups == []


def test_a_bound_state_that_is_not_exposed_produces_no_group():
    bindings = build_bindings([_linked(_DCS_A, "state_99")])

    assert group_bindings(bindings, [_state("state_1")]) == []


# ---------------------------------------------------------------------------
# state_label
# ---------------------------------------------------------------------------


def test_a_fight_is_named_by_its_label():
    assert state_label(_state("state_1", "Defeat Balaur")) == "Defeat Balaur"


def test_an_unlabeled_state_falls_back_to_its_id():
    assert state_label(_state("state_7")) == "state_7"


def test_a_blank_label_falls_back_to_the_id():
    assert state_label(_state("state_7", "")) == "state_7"


def test_every_fight_in_a_group_is_nameable():
    """No truncation anywhere: the panel lists one label per line, so a group's
    membership can never end early."""
    _, exposed = _mirror_lake()

    assert [state_label(s) for s in exposed] == [
        "Defeat Tse-Tse Snaketail",
        "Defeat Spectral Guardians",
        "Defeat Goliath Bruisers",
        "Defeat Starburst Spiders",
    ]


# ---------------------------------------------------------------------------
# detach_state
# ---------------------------------------------------------------------------


def test_detaching_from_a_linked_group_falls_back_to_the_bot_default():
    """It cannot keep the link — merge_linked would fold it straight back in."""
    shared = _linked(_DCS_A, "state_1", "state_2")
    bindings = build_bindings([shared])

    detached = detach_state(bindings, shared, "state_2")

    assert detached.is_linked is False
    assert shared.state_ids == ["state_1"]
    assert bindings["state_2"] is detached


def test_a_detached_link_does_not_rejoin_the_group_it_left():
    shared = _linked(_DCS_A, "state_1", "state_2")
    bindings = build_bindings([shared])
    detached = detach_state(bindings, shared, "state_2")

    regrouped = build_bindings([shared, detached])

    assert regrouped["state_2"] is not regrouped["state_1"]


def test_detaching_the_last_fight_leaves_the_group_empty():
    """An assignment nothing points at is dropped by _save_bcs / _prune_orphans."""
    shared = _linked(_DCS_A, "state_1")
    bindings = build_bindings([shared])

    detach_state(bindings, shared, "state_1")

    assert shared.state_ids == []


# ---------------------------------------------------------------------------
# apply_membership
# ---------------------------------------------------------------------------


def test_adding_a_fight_to_a_group():
    group = _linked(_DCS_A, "state_1")
    bindings = {"state_1": group, "state_2": _empty("state_2")}

    apply_membership(bindings, group, ["state_1", "state_2"])

    assert group.state_ids == ["state_1", "state_2"]
    assert bindings["state_2"] is group


def test_a_fight_moving_between_groups_leaves_the_old_one():
    source = _linked(_DCS_A, "state_1", "state_2")
    dest = _linked(_DCS_B, "state_3")
    bindings = {"state_1": source, "state_2": source, "state_3": dest}

    apply_membership(bindings, dest, ["state_3", "state_2"])

    assert source.state_ids == ["state_1"]
    assert dest.state_ids == ["state_3", "state_2"]
    assert bindings["state_2"] is dest


def test_a_fight_moving_between_groups_keeps_no_copy():
    """It is about to be given the destination's config; a copy would be an orphan."""
    source = _linked(_DCS_A, "state_1", "state_2")
    dest = _linked(_DCS_B, "state_3")
    bindings = {"state_1": source, "state_2": source, "state_3": dest}

    apply_membership(bindings, dest, ["state_3", "state_2"])

    assert bindings["state_2"] is dest
    assert bindings["state_2"].duel_combat_strategy_id == _DCS_B


def test_unchecking_a_fight_sends_it_back_to_the_default():
    group = _linked(_DCS_A, "state_1", "state_2")
    bindings = build_bindings([group])

    apply_membership(bindings, group, ["state_1"])

    assert group.state_ids == ["state_1"]
    assert bindings["state_2"] is not group
    assert not bindings["state_2"].is_linked


def test_membership_can_add_and_remove_in_one_pass():
    group = _linked(_DCS_A, "state_1", "state_2")
    other = _empty("state_3")
    bindings = {"state_1": group, "state_2": group, "state_3": other}

    apply_membership(bindings, group, ["state_1", "state_3"])

    assert group.state_ids == ["state_1", "state_3"]
    assert bindings["state_2"] is not group
    assert bindings["state_3"] is group


def test_an_unchanged_membership_is_a_no_op():
    group = _linked(_DCS_A, "state_1", "state_2")
    bindings = build_bindings([group])

    apply_membership(bindings, group, ["state_1", "state_2"])

    assert group.state_ids == ["state_1", "state_2"]
    assert bindings["state_1"] is bindings["state_2"] is group


def test_duplicate_choices_are_ignored():
    group = _linked(_DCS_A, "state_1")
    bindings = {"state_1": group, "state_2": _empty("state_2")}

    apply_membership(bindings, group, ["state_2", "state_2"])

    assert group.state_ids == ["state_2"]


def test_membership_survives_a_regroup():
    """What apply_membership produced must still group the same way on reload."""
    group = _linked(_DCS_A, "state_1")
    loose = _empty("state_2")
    bindings = {"state_1": group, "state_2": loose}

    apply_membership(bindings, group, ["state_1", "state_2"])
    regrouped = build_bindings([group, loose])

    assert regrouped["state_1"] is regrouped["state_2"]


# ---------------------------------------------------------------------------
# split_unconfigured
# ---------------------------------------------------------------------------
#
# A group that holds no config shares nothing, so keeping several fights in it
# only costs them the one-click configure that unassigned rows exist to provide.


def test_an_empty_group_is_split_into_one_binding_per_fight():
    split = split_unconfigured([_empty("state_1", "state_2", "state_3")])

    assert [a.state_ids for a in split] == [["state_1"], ["state_2"], ["state_3"]]


def test_splitting_gives_each_fight_a_distinct_object():
    split = split_unconfigured([_empty("state_1", "state_2")])

    assert split[0] is not split[1]


def test_a_linked_group_is_left_alone():
    group = _linked(_DCS_A, "state_1", "state_2")

    split = split_unconfigured([group])

    assert split == [group]


def test_a_single_fight_placeholder_is_untouched():
    group = _empty("state_1")

    assert split_unconfigured([group]) == [group]


def test_clearing_a_group_link_leaves_each_fight_on_its_own():
    """Unlinking to nothing is how a multi-fight group becomes empty."""
    group = _linked(_DCS_A, "state_1", "state_2")
    group.duel_combat_strategy_id = None

    bindings = build_bindings([group])

    assert bindings["state_1"] is not bindings["state_2"]
    assert bindings["state_1"].state_ids == ["state_1"]
