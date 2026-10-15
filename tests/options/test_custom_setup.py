"""What Custom setup's Options page and the Card art page record (epic #215, Stage 7, #232).

Checked against ``settings.json`` on disk, since a first run that closed after the page
has to find the answer there next launch.
"""

from __future__ import annotations

import json

import pytest

from bartlebot.game import card_art
from bartlebot.options.custom_setup import (
    ART_ANSWERED_KEY,
    EXTRACT_ART_KEY,
    art_answered,
    extract_art_chosen,
    options_answered,
    record_art_answered,
    record_options,
    refill_potions,
)
from bartlebot.settings_manager import SettingsManager


@pytest.fixture
def sm() -> SettingsManager:
    card_art.forget_cache()
    yield SettingsManager.instance()
    card_art.forget_cache()


def _general_on_disk(sm: SettingsManager) -> dict:
    return json.loads(sm.settings_path.read_text(encoding="utf-8"))["general"]


def test_a_fresh_install_has_every_option_on_and_nothing_answered(sm):
    assert extract_art_chosen(sm)
    assert sm.auto_group_players
    assert refill_potions(sm)
    assert not options_answered(sm)
    assert not art_answered(sm)


def test_record_options_writes_all_three_to_disk(sm):
    assert record_options(sm, extract_art=False, auto_group=False, refill=False)

    general = _general_on_disk(sm)
    assert general[EXTRACT_ART_KEY] is False
    assert general["auto_group_players"] is False
    assert general["default_refill_potions"] is False
    assert options_answered(sm)


def test_choosing_art_answers_the_options_page_too(sm):
    record_options(sm, extract_art=True, auto_group=True, refill=True)

    assert options_answered(sm)
    assert extract_art_chosen(sm)


def test_record_art_answered_writes_its_key(sm):
    assert record_art_answered(sm)

    assert _general_on_disk(sm)[ART_ANSWERED_KEY] is True
    assert art_answered(sm)


def test_turning_art_off_answers_the_art_page(sm):
    record_options(sm, extract_art=False, auto_group=True, refill=True)

    assert art_answered(sm)


def test_turning_art_on_leaves_the_art_page_to_ask(sm):
    record_options(sm, extract_art=True, auto_group=True, refill=True)

    assert not art_answered(sm)


def test_art_extracted_before_setup_answers_the_art_page(sm):
    card_art.spells_dir().mkdir(parents=True)
    card_art.index_path().write_text(json.dumps({"images": {"Fixture Bolt": "x.png"}}), encoding="utf-8")
    card_art.forget_cache()

    assert art_answered(sm)


def test_a_failed_save_says_so(sm, monkeypatch, caplog):
    def _fail():
        raise OSError("disk full")

    monkeypatch.setattr(sm, "save", _fail)

    assert not record_options(sm, extract_art=True, auto_group=True, refill=True)
    assert not record_art_answered(sm)
    assert "Could not save the options answer" in caplog.text
