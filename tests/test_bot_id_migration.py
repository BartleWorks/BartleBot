"""Tests for scripts/migrate_bot_ids.py — the pure transform core.

The disk-walking main() is not exercised here; every migration rule lives in
``migrate_bot_data`` / ``ensure_id`` / ``rename_legacy_params``, which operate on
plain dicts.
"""

from __future__ import annotations

import copy
from pathlib import Path

from scripts.migrate_bot_ids import (
    _plan_duplicate_restamps,
    ensure_id,
    migrate_bot_data,
    rename_legacy_params,
)

_EXISTING_ID = "11111111-2222-3333-4444-555555555555"


def _bot(**overrides) -> dict:
    """A minimal bot dict in the key order the app writes."""
    data = {
        "name": "Test Bot",
        "description": "A bot",
        "version": "1.0",
        "initial": "start",
        "states": [],
        "transitions": [],
    }
    data.update(overrides)
    return data


def _combat_state(state_id: str, **params) -> dict:
    return {"id": state_id, "node_type": "handle_combat", "params": params}


# ---------------------------------------------------------------------------
# ensure_id
# ---------------------------------------------------------------------------


def test_stamps_id_when_absent():
    data, changes = ensure_id(_bot(), set())

    assert data["id"]
    assert len(changes) == 1
    assert changes[0].startswith("stamped missing id")


def test_stamped_id_is_the_first_key():
    """App-written bots lead with `id`; stamped ones must match so diffs stay clean."""
    data, _ = ensure_id(_bot(), set())

    assert next(iter(data)) == "id"


def test_stamping_preserves_all_other_content_and_order():
    original = _bot()
    data, _ = ensure_id(copy.deepcopy(original), set())

    assert [k for k in data if k != "id"] == list(original.keys())
    assert {k: v for k, v in data.items() if k != "id"} == original


def test_existing_id_is_left_untouched():
    original = _bot(id=_EXISTING_ID)
    data, changes = ensure_id(copy.deepcopy(original), set())

    assert changes == []
    assert data == original


def test_existing_id_is_recorded_as_taken():
    taken: set[str] = set()
    ensure_id(_bot(id=_EXISTING_ID), taken)

    assert taken == {_EXISTING_ID}


def test_force_new_restamps_an_existing_id():
    data, changes = ensure_id(_bot(id=_EXISTING_ID), set(), force_new=True)

    assert data["id"] != _EXISTING_ID
    assert len(changes) == 1
    assert changes[0].startswith("re-stamped duplicate id")


def test_generated_id_never_collides_with_a_taken_one():
    taken = {_EXISTING_ID}
    data, _ = ensure_id(_bot(), taken)

    assert data["id"] not in {_EXISTING_ID}
    assert data["id"] in taken


# ---------------------------------------------------------------------------
# rename_legacy_params
# ---------------------------------------------------------------------------


def test_renames_legacy_param_preserving_value_false():
    """castle_darkmoor's values are False — a rename that dropped them would
    silently flip those states back to the bot's default strategy."""
    data = _bot(states=[_combat_state("fight", use_default_duel_profile=False)])
    changes = rename_legacy_params(data)

    params = data["states"][0]["params"]
    assert params == {"use_default_strategy": False}
    assert len(changes) == 1


def test_renames_legacy_param_preserving_value_true():
    data = _bot(states=[_combat_state("fight", use_default_duel_profile=True)])
    rename_legacy_params(data)

    assert data["states"][0]["params"] == {"use_default_strategy": True}


def test_drops_duel_profile_name():
    data = _bot(states=[_combat_state("fight", use_default_strategy=False, duel_profile_name="old")])
    changes = rename_legacy_params(data)

    assert data["states"][0]["params"] == {"use_default_strategy": False}
    assert len(changes) == 1


def test_other_params_are_preserved():
    data = _bot(states=[_combat_state("fight", use_default_duel_profile=False, enemy="Boss", max_duration=90.0)])
    rename_legacy_params(data)

    assert data["states"][0]["params"] == {"use_default_strategy": False, "enemy": "Boss", "max_duration": 90.0}


def test_non_combat_states_are_ignored():
    data = _bot(states=[{"id": "walk", "node_type": "enter_zone", "params": {"use_default_duel_profile": True}}])
    changes = rename_legacy_params(data)

    assert changes == []
    assert data["states"][0]["params"] == {"use_default_duel_profile": True}


def test_state_without_params_is_ignored():
    data = _bot(states=[{"id": "fight", "node_type": "handle_combat"}])

    assert rename_legacy_params(data) == []


def test_renames_across_multiple_states():
    data = _bot(
        states=[
            _combat_state("fight_a", use_default_duel_profile=False),
            _combat_state("fight_b", use_default_duel_profile=False),
        ]
    )
    changes = rename_legacy_params(data)

    assert len(changes) == 2
    assert all(s["params"] == {"use_default_strategy": False} for s in data["states"])


# ---------------------------------------------------------------------------
# migrate_bot_data — combined behavior and idempotency
# ---------------------------------------------------------------------------


def test_already_migrated_file_reports_no_changes():
    """No changes means the caller must not rewrite the file at all."""
    original = _bot(id=_EXISTING_ID, states=[_combat_state("fight", use_default_strategy=False)])
    data, changes = migrate_bot_data(copy.deepcopy(original), set())

    assert changes == []
    assert data == original


def test_migration_is_idempotent():
    data = _bot(states=[_combat_state("fight", use_default_duel_profile=False)])

    once, first_changes = migrate_bot_data(copy.deepcopy(data), set())
    twice, second_changes = migrate_bot_data(copy.deepcopy(once), set())

    assert first_changes
    assert second_changes == []
    assert twice == once


def test_combined_stamp_and_rename():
    data, changes = migrate_bot_data(_bot(states=[_combat_state("fight", use_default_duel_profile=False)]), set())

    assert data["id"]
    assert data["states"][0]["params"] == {"use_default_strategy": False}
    assert len(changes) == 2


# ---------------------------------------------------------------------------
# _plan_duplicate_restamps
# ---------------------------------------------------------------------------


def test_no_duplicates_plans_no_restamps():
    loaded = {
        Path("bots/a.json"): _bot(id=_EXISTING_ID),
        Path("bots/b.json"): _bot(id="99999999-2222-3333-4444-555555555555"),
    }

    assert _plan_duplicate_restamps(loaded) == set()


def test_duplicate_keeps_first_sorted_path_by_default():
    a, b = Path("bots/a.json"), Path("bots/b.json")
    loaded = {b: _bot(id=_EXISTING_ID), a: _bot(id=_EXISTING_ID)}

    assert _plan_duplicate_restamps(loaded) == {b}


def test_id_owners_override_decides_the_keeper():
    """The Lemuria collision: the BCS is named for hall_of_heroes, which sorts second."""
    hall = Path("bots/18_lemuria/hall_of_heroes.json")
    scion = Path("bots/18_lemuria/scion_of_the_old_one.json")
    shared = "602704e3-45d1-4232-a64a-9c012f870cf3"
    loaded = {hall: _bot(id=shared), scion: _bot(id=shared)}

    assert _plan_duplicate_restamps(loaded) == {scion}


def test_files_without_ids_are_not_treated_as_duplicates():
    loaded = {Path("bots/a.json"): _bot(), Path("bots/b.json"): _bot()}

    assert _plan_duplicate_restamps(loaded) == set()


def test_restamped_duplicate_gets_a_distinct_id():
    shared = "602704e3-45d1-4232-a64a-9c012f870cf3"
    taken = {shared}

    data, changes = migrate_bot_data(_bot(id=shared), taken, force_new_id=True)

    assert data["id"] != shared
    assert next(iter(data)) == "id"
    assert changes[0].startswith("re-stamped duplicate id")
