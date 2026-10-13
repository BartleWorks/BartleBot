"""The first-run dialog and its combat question (epic #201, Stage 6).

Driven by its buttons, never by ``exec()``: the suite refuses that (``tests/conftest.py``),
so no test can leave a modal open. What an answer stores is checked in ``preferences.json``
and through Settings › Combat, which is where the user sees it afterwards.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog

from bartlebot.gui.dialogs.first_run_dialog import (
    COMBAT_HEADING,
    CONTINUE,
    NEXT,
    RECOMMENDED_DETAIL,
    SKIP,
    TACTIC_CARDS,
    CombatQuestionPage,
    FirstRunDialog,
    FirstRunPage,
)
from bartlebot.gui.settings.combat_defaults_tab import CombatDefaultsTab
from bartlebot.shared.combat_defaults import PREFERENCE_KEYS, SHIPPED_EVERYONE_ATTACKS_DUEL_ID, TeamSize
from bartlebot.shared.first_run import ANSWERED_KEY, BossTactic, combat_question_answered
from bartlebot.shared.preferences import PreferenceStore
from tests.conftest import FIRST_RUN_REFUSED

_TEAM = PREFERENCE_KEYS[TeamSize.TEAM]


@pytest.fixture
def prefs(tmp_path: Path) -> PreferenceStore:
    return PreferenceStore(tmp_path / "vendor")


def _record(prefs: PreferenceStore) -> dict:
    return json.loads(prefs.path.read_text(encoding="utf-8")) if prefs.path.exists() else {}


class _Page(FirstRunPage):
    """A page that remembers how it was recorded, for the dialog's walk across pages."""

    def __init__(self, heading: str, *, ok: bool = True) -> None:
        super().__init__()
        self.heading = heading
        self.recorded: list[bool] = []
        self._ok = ok

    def record(self, prefs, *, skipped: bool) -> bool:
        self.recorded.append(skipped)
        return self._ok


# -- the combat question ----------------------------------------------------


def test_the_recommended_answer_comes_first_and_is_selected(qapp):
    page = CombatQuestionPage()

    assert [card.name for card in page.cards] == [
        TACTIC_CARDS[BossTactic.ONE_HITTER][0],
        TACTIC_CARDS[BossTactic.EVERYONE_ATTACKS][0],
    ]
    assert page.tactic is BossTactic.ONE_HITTER
    assert [card.selected for card in page.cards] == [True, False]
    assert [card.detail_text for card in page.cards] == [RECOMMENDED_DETAIL, ""]


def test_the_note_names_the_solo_default_settings_and_the_combat_tab(qapp):
    note = CombatQuestionPage().note_text

    assert "Default Solo Duel" in note
    assert "Settings › Combat" in note
    assert "Combat tab" in note


def test_the_stored_answer_is_the_one_selected(qapp):
    page = CombatQuestionPage(BossTactic.EVERYONE_ATTACKS)

    assert page.tactic is BossTactic.EVERYONE_ATTACKS
    assert [card.selected for card in page.cards] == [False, True]


def test_showing_the_dialog_keeps_the_stored_answer_selected(qapp):
    """A card selects itself on taking focus; the first card must not take it on show."""
    page = CombatQuestionPage(BossTactic.EVERYONE_ATTACKS)
    dialog = FirstRunDialog([page])

    dialog.show()
    qapp.processEvents()

    assert page.tactic is BossTactic.EVERYONE_ATTACKS
    dialog.close()


def test_a_click_on_a_card_selects_it(qapp):
    page = CombatQuestionPage()

    QTest.mouseClick(page.card_for(BossTactic.EVERYONE_ATTACKS), Qt.MouseButton.LeftButton)

    assert page.tactic is BossTactic.EVERYONE_ATTACKS
    assert [card.selected for card in page.cards] == [False, True]


# -- one page: Continue and Skip ---------------------------------------------


def test_one_page_asks_its_question_with_continue_and_skip(qapp):
    dialog = FirstRunDialog([CombatQuestionPage()])

    assert dialog.heading_text == COMBAT_HEADING
    assert dialog.commit_button.text() == CONTINUE
    assert dialog.skip_button.text() == SKIP
    assert not dialog.back_button.isVisibleTo(dialog)


@pytest.mark.parametrize(
    ("tactic", "stored"),
    [(BossTactic.ONE_HITTER, None), (BossTactic.EVERYONE_ATTACKS, SHIPPED_EVERYONE_ATTACKS_DUEL_ID)],
)
def test_continue_stores_the_answer_and_settings_combat_shows_it(qapp, prefs, tactic, stored):
    page = CombatQuestionPage()
    dialog = FirstRunDialog([page])
    page.select(tactic)

    dialog.commit_button.click()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.record(prefs)
    assert _record(prefs).get(_TEAM) == stored
    assert combat_question_answered(prefs)
    card = CombatDefaultsTab(prefs=PreferenceStore(prefs.path.parent)).card(TeamSize.TEAM)
    assert card.choice == stored
    expected = "Default Team Duel (Everyone Attacks)" if stored else "Default Team Duel"
    assert card.strategy_card._name_lbl.text() == expected


def test_skip_records_the_question_asked_and_stores_no_answer(qapp, prefs):
    page = CombatQuestionPage()
    dialog = FirstRunDialog([page])
    page.select(BossTactic.EVERYONE_ATTACKS)

    dialog.skip_button.click()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.record(prefs)
    assert _record(prefs) == {"version": 1, ANSWERED_KEY: True}


def test_escape_is_skip(qapp, prefs):
    page = CombatQuestionPage()
    dialog = FirstRunDialog([page])
    page.select(BossTactic.EVERYONE_ATTACKS)
    dialog.show()

    QTest.keyClick(dialog, Qt.Key.Key_Escape)

    assert dialog.result() == QDialog.DialogCode.Rejected
    dialog.record(prefs)
    assert _TEAM not in _record(prefs)


# -- the page list ------------------------------------------------------------


def test_pages_are_walked_with_next_and_back_and_end_on_continue(qapp):
    first, second = _Page("First?"), _Page("Second?")
    dialog = FirstRunDialog([first, second])

    assert (dialog.page, dialog.heading_text, dialog.commit_button.text()) == (0, "First?", NEXT)
    assert not dialog.back_button.isVisibleTo(dialog)

    dialog.commit_button.click()
    assert (dialog.page, dialog.heading_text, dialog.commit_button.text()) == (1, "Second?", CONTINUE)
    assert dialog.back_button.isVisibleTo(dialog)
    assert dialog.result() != QDialog.DialogCode.Accepted

    dialog.back_button.click()
    assert dialog.page == 0

    dialog.commit_button.click()
    dialog.commit_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_continue_records_every_page_answered(qapp, prefs):
    pages = [_Page("First?"), _Page("Second?")]
    dialog = FirstRunDialog(pages)
    dialog.commit_button.click()
    dialog.commit_button.click()

    dialog.record(prefs)

    assert [page.recorded for page in pages] == [[False], [False]]


def test_skip_on_the_first_page_records_every_page_skipped(qapp, prefs):
    pages = [_Page("First?"), _Page("Second?")]
    dialog = FirstRunDialog(pages)

    dialog.skip_button.click()
    dialog.record(prefs)

    assert [page.recorded for page in pages] == [[True], [True]]


def test_one_page_failing_to_save_still_records_the_rest(qapp, prefs):
    pages = [_Page("First?", ok=False), _Page("Second?")]
    dialog = FirstRunDialog(pages)
    dialog.skip_button.click()

    assert not dialog.record(prefs)
    assert pages[1].recorded == [True]


def test_the_dialog_needs_a_page(qapp):
    with pytest.raises(ValueError):
        FirstRunDialog([])


def test_building_the_dialog_never_shows_a_parentless_widget(qapp, parentless_shows):
    FirstRunDialog([CombatQuestionPage(BossTactic.EVERYONE_ATTACKS)])

    assert parentless_shows == []


def test_the_suite_refuses_to_open_it_modally(qapp):
    """So a test that ever builds an ``AppController`` fails, rather than hanging on it."""
    dialog = FirstRunDialog([CombatQuestionPage()])

    with pytest.raises(AssertionError, match=FIRST_RUN_REFUSED):
        dialog.exec()
