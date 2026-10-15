"""Where the user plays: what setup's Realm and Safety Delay page records, and when it counts as answered (epic #215, Stage 6, #231).

Checked against ``settings.json`` on disk, since a first run that closed after the page
has to find the answer there next launch.
"""

from __future__ import annotations

import json

import pytest

from bartlebot.options.realm_delay_setup import (
    ANSWERED_KEY,
    SETUP_REALM,
    realm_delay_answered,
    record_realm_delay,
    setup_realm,
)
from bartlebot.settings_manager import SettingsManager


@pytest.fixture
def sm() -> SettingsManager:
    return SettingsManager.instance()


def _general_on_disk(sm: SettingsManager) -> dict:
    return json.loads(sm.settings_path.read_text(encoding="utf-8"))["general"]


def test_record_writes_the_realm_the_delay_and_the_key_to_disk(sm):
    assert record_realm_delay(sm, "Balestrom", 6.5)

    general = _general_on_disk(sm)
    assert general["favorite_realm"] == "Balestrom"
    assert general["default_safety_delay"] == 6.5
    assert general[ANSWERED_KEY] is True


def test_a_failed_save_says_so_and_logs(sm, monkeypatch, caplog):
    def _fail():
        raise OSError("disk full")

    monkeypatch.setattr(sm, "save", _fail)

    assert not record_realm_delay(sm, "Wraith", 3.0)
    assert "Could not save the realm and Safety Delay answer" in caplog.text


def test_a_fresh_install_is_unanswered_and_opens_on_wraith(sm):
    assert not realm_delay_answered(sm)
    assert setup_realm(sm) == SETUP_REALM == "Wraith"
    # Only the page's selection: the setting itself is still unset.
    assert sm.favorite_realm == ""


def test_a_realm_set_answers_the_page_and_is_the_one_shown(sm):
    sm.favorite_realm = "Balestrom"

    assert realm_delay_answered(sm)
    assert setup_realm(sm) == "Balestrom"


def test_an_empty_realm_after_the_page_is_the_users_answer(sm):
    record_realm_delay(sm, "", 3.0)

    assert realm_delay_answered(sm)
    assert setup_realm(sm) == ""


def test_a_delay_changed_alone_does_not_answer_the_page(sm):
    sm.default_safety_delay = 8.0

    assert not realm_delay_answered(sm)
