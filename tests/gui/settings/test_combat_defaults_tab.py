"""Settings › Combat: the Solo and Team Default Duel Strategies (epic #201, Stage 5).

Read against the repo's shipped strategies, which the test's private ``%APPDATA%`` leaves
as the only library, and a ``preferences.json`` of the test's own. The dialog Change…
opens is patched: what is under test is what the card asks it for and does with the answer.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from bartleby.shared.combat_defaults import (
    PREFERENCE_KEYS,
    SHIPPED_EVERYONE_ATTACKS_DUEL_ID,
    SHIPPED_SOLO_DUEL_ID,
    SHIPPED_TEAM_DUEL_ID,
    TeamSize,
)
from bartleby.shared.preferences import PreferenceStore
from bartleby.shared.strategy_models import DuelCombatStrategy

from bartlebot.combat.strategy_store import save_duel_strategy
from bartlebot.gui.combat_strategies.dialog import DialogMode
from bartlebot.gui.settings.combat_defaults_tab import (
    CHOSEN_NOTE,
    RECOMMENDED_NOTE,
    CombatDefaultsTab,
)
from bartlebot.gui.settings.settings_dialog import SettingsDialog
from bartlebot.logging_setup import TURN_TRACE_LOGGER
from bartlebot.settings_manager import SettingsManager

_DIALOG = "bartlebot.gui.settings.combat_defaults_tab.CombatStrategyDialog"


@pytest.fixture
def prefs(tmp_path: Path) -> PreferenceStore:
    return PreferenceStore(tmp_path / "vendor")


def _record(prefs: PreferenceStore) -> dict:
    path = prefs.path
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _pick(card, duel_id: str | None):
    """Press Change… with the dialog answering *duel_id*; returns the dialog's mock."""
    with patch(_DIALOG) as dialog:
        dialog.return_value.exec.return_value = duel_id is not None
        dialog.return_value.selected_id.return_value = duel_id
        card.change_button.click()
    return dialog


def test_each_card_shows_the_recommended_duel_when_nothing_is_stored(qapp, prefs):
    tab = CombatDefaultsTab(prefs=prefs)

    for size, name in ((TeamSize.SOLO, "Default Solo Duel"), (TeamSize.TEAM, "Default Team Duel")):
        card = tab.card(size)
        assert card.choice is None
        assert card.strategy_card._name_lbl.text() == name
        assert card.strategy_card.source_text == "Official"
        assert card.note_text == RECOMMENDED_NOTE
        assert not card.recommended_button.isEnabled()


@pytest.mark.parametrize(
    ("size", "duel_id"),
    [(TeamSize.SOLO, SHIPPED_TEAM_DUEL_ID), (TeamSize.TEAM, SHIPPED_EVERYONE_ATTACKS_DUEL_ID)],
)
def test_a_pick_on_either_card_round_trips_through_preferences_json(qapp, prefs, size, duel_id):
    tab = CombatDefaultsTab(prefs=prefs)

    _pick(tab.card(size), duel_id)
    tab.apply()

    assert _record(prefs)[PREFERENCE_KEYS[size]] == duel_id
    reopened = CombatDefaultsTab(prefs=PreferenceStore(prefs.path.parent))
    assert reopened.card(size).choice == duel_id
    assert reopened.card(size).note_text.startswith(CHOSEN_NOTE)


def test_use_recommended_removes_the_key(qapp, prefs):
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], SHIPPED_EVERYONE_ATTACKS_DUEL_ID)
    tab = CombatDefaultsTab(prefs=prefs)
    assert tab.card(TeamSize.TEAM).recommended_button.isEnabled()

    tab.card(TeamSize.TEAM).recommended_button.click()
    tab.apply()

    assert PREFERENCE_KEYS[TeamSize.TEAM] not in _record(prefs)


def test_the_shipped_duel_stored_by_its_id_shows_as_recommended(qapp, prefs):
    """As BartleBuddy or a hand edit might store it (epic #201 review, R5)."""
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], SHIPPED_TEAM_DUEL_ID)

    card = CombatDefaultsTab(prefs=prefs).card(TeamSize.TEAM)

    assert card.choice is None
    assert card.note_text == RECOMMENDED_NOTE
    assert not card.recommended_button.isEnabled()


def test_picking_the_recommended_duel_is_the_same_as_use_recommended(qapp, prefs):
    prefs.set(PREFERENCE_KEYS[TeamSize.SOLO], SHIPPED_TEAM_DUEL_ID)
    tab = CombatDefaultsTab(prefs=prefs)

    _pick(tab.card(TeamSize.SOLO), SHIPPED_SOLO_DUEL_ID)
    tab.apply()

    assert tab.card(TeamSize.SOLO).choice is None
    assert PREFERENCE_KEYS[TeamSize.SOLO] not in _record(prefs)


def test_nothing_is_written_until_ok(qapp, prefs):
    """The settings dialog's apply rule: a choice is pending, and Cancel never applies it."""
    tab = CombatDefaultsTab(prefs=prefs)

    _pick(tab.card(TeamSize.TEAM), SHIPPED_EVERYONE_ATTACKS_DUEL_ID)

    assert not prefs.path.exists()


def test_ok_leaves_a_key_the_other_product_wrote_meanwhile(qapp, prefs):
    tab = CombatDefaultsTab(prefs=prefs)
    _pick(tab.card(TeamSize.TEAM), SHIPPED_EVERYONE_ATTACKS_DUEL_ID)
    PreferenceStore(prefs.path.parent).set(PREFERENCE_KEYS[TeamSize.SOLO], SHIPPED_TEAM_DUEL_ID)

    tab.apply()

    record = _record(prefs)
    assert record[PREFERENCE_KEYS[TeamSize.SOLO]] == SHIPPED_TEAM_DUEL_ID
    assert record[PREFERENCE_KEYS[TeamSize.TEAM]] == SHIPPED_EVERYONE_ATTACKS_DUEL_ID


@pytest.mark.parametrize(("size", "team_size"), [(TeamSize.SOLO, 1), (TeamSize.TEAM, 2)])
def test_change_opens_pick_filtered_to_the_cards_team_size(qapp, prefs, size, team_size):
    tab = CombatDefaultsTab(prefs=prefs)

    dialog = _pick(tab.card(size), None)

    kwargs = dialog.call_args.kwargs
    assert kwargs["mode"] is DialogMode.PICK
    assert kwargs["team_size"] == team_size
    assert kwargs["allow_none"] is False
    assert kwargs["current_id"] == (SHIPPED_SOLO_DUEL_ID if size is TeamSize.SOLO else SHIPPED_TEAM_DUEL_ID)


def test_a_cancelled_pick_changes_nothing(qapp, prefs):
    tab = CombatDefaultsTab(prefs=prefs)

    _pick(tab.card(TeamSize.TEAM), None)

    assert tab.card(TeamSize.TEAM).choice is None


def test_a_stored_default_gone_from_the_library_says_so_and_shows_the_recommended_one(qapp, prefs):
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], "deadbeef-0000-0000-0000-000000000000")

    card = CombatDefaultsTab(prefs=prefs).card(TeamSize.TEAM)

    assert card.strategy_card._name_lbl.text() == "Default Team Duel"
    assert "It is no longer in the library, so the recommended one is used." in card.note_text


def test_a_stored_default_that_does_not_fit_every_team_says_when_it_is_passed_over(qapp, prefs):
    four = DuelCombatStrategy(name="Four Box", min_players=4, max_players=4)
    save_duel_strategy(four)
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], four.id)

    card = CombatDefaultsTab(prefs=prefs).card(TeamSize.TEAM)

    assert card.strategy_card._name_lbl.text() == "Four Box"
    assert "with 2 or 3 clients connected the recommended one is used" in card.note_text


def test_the_settings_dialog_opens_on_combat(qapp):
    dlg = SettingsDialog()

    dlg.open_tab("combat")

    assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == "Combat"


def test_the_tab_offers_description_links_for_the_log_row(qapp, prefs):
    tab = CombatDefaultsTab(prefs=prefs)

    assert not tab.switches.isHidden()


# ---------------------------------------------------------------------------
# Record combat decisions
# ---------------------------------------------------------------------------


@pytest.fixture
def trace_level():
    """Put the turn trace's logger back as it was: its level is process-wide."""
    logger = logging.getLogger(TURN_TRACE_LOGGER)
    level = logger.level
    yield logger
    logger.setLevel(level)


def test_the_record_row_shows_the_stored_setting(qapp, prefs):
    SettingsManager.instance().record_combat_decisions = True

    assert CombatDefaultsTab(prefs=prefs).record_check_box.isChecked()


@pytest.mark.parametrize("record", [True, False])
def test_ok_stores_the_record_setting_and_applies_it_at_once(qapp, prefs, trace_level, record):
    sm = SettingsManager.instance()
    sm.record_combat_decisions = not record
    tab = CombatDefaultsTab(prefs=prefs)

    tab.record_check_box.setChecked(record)
    assert sm.record_combat_decisions is not record  # pending until OK
    tab.apply()

    assert sm.record_combat_decisions is record
    assert trace_level.level == (logging.DEBUG if record else logging.NOTSET)


def test_new_art_redraws_each_cards_strategy(qapp, prefs):
    """Settings is open behind the extraction its General tab starts (#239): the card is made again."""
    from bartlebot.gui.art_signal import announce_art_changed  # noqa: PLC0415

    tab = CombatDefaultsTab(prefs=prefs)
    before = {size: tab.card(size).strategy_card for size in (TeamSize.SOLO, TeamSize.TEAM)}

    announce_art_changed()

    for size, card in before.items():
        assert tab.card(size).strategy_card is not card
        assert tab.card(size).strategy_card._name_lbl.text() == card._name_lbl.text()
