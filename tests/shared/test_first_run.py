"""The first-run question's record in ``preferences.json`` (epic #201, Stage 6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bartlebot.shared.combat_defaults import (
    PREFERENCE_KEYS,
    SHIPPED_EVERYONE_ATTACKS_DUEL_ID,
    SHIPPED_TEAM_DUEL_ID,
    TeamSize,
)
from bartlebot.shared.first_run import (
    ANSWERED_KEY,
    RECOMMENDED,
    TEAM_DUELS,
    BossTactic,
    combat_question_answered,
    current_tactic,
    record_answer,
)
from bartlebot.shared.preferences import PreferenceStore

_TEAM = PREFERENCE_KEYS[TeamSize.TEAM]
_SOLO = PREFERENCE_KEYS[TeamSize.SOLO]
_SOMEONE_ELSES_DUEL = "00000000-0000-0000-0000-0000000000d1"


@pytest.fixture
def prefs(tmp_path: Path) -> PreferenceStore:
    return PreferenceStore(tmp_path)


def _record(prefs: PreferenceStore) -> dict:
    return json.loads(prefs.path.read_text(encoding="utf-8")) if prefs.path.exists() else {}


def test_a_fresh_install_has_not_answered(prefs):
    assert not combat_question_answered(prefs)
    assert current_tactic(prefs) is RECOMMENDED


def test_the_recommended_answer_is_one_hitter_and_stores_no_team_default():
    assert RECOMMENDED is BossTactic.ONE_HITTER
    assert TEAM_DUELS[BossTactic.ONE_HITTER] is None
    assert TEAM_DUELS[BossTactic.EVERYONE_ATTACKS] == SHIPPED_EVERYONE_ATTACKS_DUEL_ID


def test_one_hitter_stores_no_team_default_and_records_the_answer(prefs):
    assert record_answer(prefs, BossTactic.ONE_HITTER)

    assert _record(prefs) == {"version": 1, ANSWERED_KEY: True}
    assert combat_question_answered(prefs)


def test_everyone_attacks_stores_the_shipped_everyone_attacks_duel(prefs):
    assert record_answer(prefs, BossTactic.EVERYONE_ATTACKS)

    assert _record(prefs)[_TEAM] == SHIPPED_EVERYONE_ATTACKS_DUEL_ID
    assert _record(prefs)[ANSWERED_KEY] is True
    assert current_tactic(prefs) is BossTactic.EVERYONE_ATTACKS


def test_one_hitter_after_everyone_attacks_goes_back_to_no_key(prefs):
    record_answer(prefs, BossTactic.EVERYONE_ATTACKS)

    record_answer(prefs, BossTactic.ONE_HITTER)

    assert _TEAM not in _record(prefs)


def test_skip_records_the_question_asked_and_changes_no_default(prefs):
    prefs.set(_TEAM, SHIPPED_EVERYONE_ATTACKS_DUEL_ID)

    assert record_answer(prefs, None)

    assert _record(prefs)[_TEAM] == SHIPPED_EVERYONE_ATTACKS_DUEL_ID
    assert combat_question_answered(prefs)


@pytest.mark.parametrize("tactic", [*BossTactic, None])
def test_no_answer_touches_the_solo_default(prefs, tactic):
    prefs.set(_SOLO, _SOMEONE_ELSES_DUEL)

    record_answer(prefs, tactic)

    assert _record(prefs)[_SOLO] == _SOMEONE_ELSES_DUEL


def test_the_answer_recorded_by_the_other_product_counts(prefs):
    """BartleBuddy writes the same key to the same file; a store of BartleBot's reads it."""
    record_answer(PreferenceStore(prefs.path.parent), None)

    assert combat_question_answered(prefs)


def test_a_team_default_neither_answer_names_is_already_an_answer(prefs):
    prefs.set(_TEAM, _SOMEONE_ELSES_DUEL)
    before = prefs.path.read_bytes()

    assert current_tactic(prefs) is None
    assert combat_question_answered(prefs)
    assert prefs.path.read_bytes() == before


def test_the_stored_shipped_team_duel_reads_as_one_hitter(prefs):
    """Stored by hand or by an older build: the shipped id is the recommended answer, not a third duel."""
    prefs.set(_TEAM, SHIPPED_TEAM_DUEL_ID)

    assert current_tactic(prefs) is BossTactic.ONE_HITTER
    assert not combat_question_answered(prefs)


class _FailingStore(PreferenceStore):
    """A store whose writes to *refused* never reach disk, as a locked file's would not."""

    def __init__(self, directory: Path, refused: str) -> None:
        super().__init__(directory)
        self._refused = refused

    def set(self, key, value) -> bool:
        return False if key == self._refused else super().set(key, value)


def test_a_failed_default_write_leaves_the_question_to_ask_again(tmp_path):
    prefs = _FailingStore(tmp_path, _TEAM)

    assert not record_answer(prefs, BossTactic.EVERYONE_ATTACKS)

    assert not combat_question_answered(prefs)


def test_a_failed_answer_write_says_so(tmp_path):
    prefs = _FailingStore(tmp_path, ANSWERED_KEY)

    assert not record_answer(prefs, None)
    assert not combat_question_answered(prefs)
