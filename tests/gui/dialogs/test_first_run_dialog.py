"""The setup dialog: Quick or Custom, its pages, and the combat question (epic #215, Stage 1).

Driven by its buttons, never by ``exec()``: the suite refuses that (``tests/conftest.py``),
so no test can leave a modal open. What an answer stores is checked in ``preferences.json``
and through Settings › Combat, which is where the user sees it afterwards.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog, QWidget

from bartlebot.gui.dialogs.first_run_dialog import (
    BACK,
    COMBAT_HEADING,
    FINISH,
    MODE_CARDS,
    MODE_HEADING,
    NEXT,
    RECOMMENDED_DETAIL,
    SAVE_FAILED,
    SETTINGS_COMBAT,
    TACTIC_CARDS,
    CombatQuestionPage,
    FirstRunDialog,
    FirstRunPage,
    ModePage,
    SetupMode,
    setup_pages,
)
from bartlebot.gui.settings.combat_defaults_tab import CombatDefaultsTab
from bartlebot.shared.combat_defaults import PREFERENCE_KEYS, SHIPPED_EVERYONE_ATTACKS_DUEL_ID, TeamSize
from bartlebot.shared.first_run import ANSWERED_KEY, BossTactic, combat_question_answered
from bartlebot.shared.preferences import PreferenceStore
from tests.conftest import FIRST_RUN_REFUSED

_TEAM = PREFERENCE_KEYS[TeamSize.TEAM]
_CUSTOM_DUEL = "00000000-0000-0000-0000-0000000000d1"


@pytest.fixture
def prefs(tmp_path: Path) -> PreferenceStore:
    return PreferenceStore(tmp_path / "vendor")


def _record(prefs: PreferenceStore) -> dict:
    return json.loads(prefs.path.read_text(encoding="utf-8")) if prefs.path.exists() else {}


class _Page(FirstRunPage):
    """A page with a stored answer of its own, for the dialog's walk across pages."""

    def __init__(self, heading: str, *, answered: bool = False, custom_only: bool = False, ok: bool = True) -> None:
        super().__init__()
        self.heading = heading
        self.recorded = 0
        self._answered = answered
        self._custom_only = custom_only
        self.ok = ok

    def answered(self) -> bool:
        return self._answered

    def applies(self, mode: SetupMode) -> bool:
        return mode is SetupMode.CUSTOM or not self._custom_only

    def record(self) -> bool:
        if not self.ok:
            return False
        self.recorded += 1
        self._answered = True
        return True


def _headings(dialog: FirstRunDialog) -> list[str]:
    return [page.heading for page in dialog.walk]


def _next(dialog: FirstRunDialog, times: int = 1) -> None:
    for _ in range(times):
        dialog.commit_button.click()


# -- Quick or Custom ------------------------------------------------------------


def test_quick_comes_first_and_is_selected(qapp):
    page = ModePage()

    assert [card.name for card in page.cards] == [MODE_CARDS[SetupMode.QUICK][0], MODE_CARDS[SetupMode.CUSTOM][0]]
    assert page.mode is SetupMode.QUICK
    assert [card.selected for card in page.cards] == [True, False]
    assert [card.detail_text for card in page.cards] == [RECOMMENDED_DETAIL, ""]


def test_the_dialog_opens_on_quick_or_custom_with_a_step_count(qapp):
    dialog = FirstRunDialog([_Page("First?"), _Page("Second?")])

    assert dialog.current is dialog.mode_page
    assert dialog.heading_text == MODE_HEADING
    assert dialog.step_text == "Step 1 of 3"
    assert dialog.commit_button.text() == NEXT
    assert not dialog.back_button.isVisibleTo(dialog)


def test_the_mode_is_not_stored(qapp, prefs):
    dialog = FirstRunDialog([CombatQuestionPage(prefs)])
    dialog.mode_page.select(SetupMode.CUSTOM)

    _next(dialog)

    assert _record(prefs) == {}


# -- no Skip: Next records, Back doesn't, closing keeps what was answered ---------------


def test_there_is_no_skip_only_back_and_next(qapp):
    dialog = FirstRunDialog([_Page("First?")])

    assert dialog.back_button.text() == BACK
    texts = {button.text() for button in dialog.findChildren(type(dialog.commit_button))}
    assert texts == {BACK, NEXT}


def test_next_records_the_page_shown_and_back_records_nothing(qapp):
    first, second = _Page("First?"), _Page("Second?")
    dialog = FirstRunDialog([first, second])

    _next(dialog)
    assert (dialog.current, dialog.step_text, first.recorded) == (first, "Step 2 of 3", 0)
    _next(dialog)
    assert (dialog.current, first.recorded, second.recorded) == (second, 1, 0)
    assert dialog.commit_button.text() == FINISH

    dialog.back_button.click()
    assert (dialog.current, first.recorded, second.recorded) == (first, 1, 0)
    assert dialog.back_button.isVisibleTo(dialog)
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_finish_records_the_last_page_and_accepts(qapp):
    first, second = _Page("First?"), _Page("Second?")
    dialog = FirstRunDialog([first, second])

    _next(dialog, 3)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert (first.recorded, second.recorded) == (1, 1)


def test_a_page_answered_on_the_way_stays_in_the_walk(qapp):
    """Back reaches it, and the count doesn't drop, though it is now answered."""
    first, second = _Page("First?"), _Page("Second?")
    dialog = FirstRunDialog([first, second])
    _next(dialog, 2)

    assert first.answered()
    assert _headings(dialog) == [MODE_HEADING, "First?", "Second?"]
    dialog.back_button.click()
    assert dialog.current is first


def test_escape_rejects_and_records_nothing_more(qapp):
    first, second = _Page("First?"), _Page("Second?")
    dialog = FirstRunDialog([first, second])
    dialog.show()
    _next(dialog, 2)

    QTest.keyClick(dialog, Qt.Key.Key_Escape)

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert (first.recorded, second.recorded) == (1, 0)


def test_closing_on_page_n_keeps_the_pages_before_it_and_resumes_after_them(qapp):
    pages = [_Page("First?"), _Page("Second?"), _Page("Third?")]
    dialog = FirstRunDialog(pages)
    _next(dialog, 3)
    assert dialog.current is pages[2]
    dialog.reject()

    resumed = FirstRunDialog(pages)
    assert _headings(resumed) == [MODE_HEADING, "Third?"]
    _next(resumed)
    assert resumed.current is pages[2]


# -- first run and rerun ----------------------------------------------------------------


def test_a_first_run_shows_no_answered_page(qapp):
    dialog = FirstRunDialog([_Page("Answered?", answered=True), _Page("Open?")])

    assert _headings(dialog) == [MODE_HEADING, "Open?"]


def test_a_rerun_shows_every_page_that_applies(qapp):
    dialog = FirstRunDialog([_Page("Answered?", answered=True), _Page("Open?")], rerun=True)

    assert _headings(dialog) == [MODE_HEADING, "Answered?", "Open?"]


def test_a_rerun_opens_the_combat_page_on_the_stored_answer(qapp, prefs):
    prefs.set(_TEAM, SHIPPED_EVERYONE_ATTACKS_DUEL_ID)
    prefs.set(ANSWERED_KEY, True)
    page = CombatQuestionPage(prefs)

    dialog = FirstRunDialog([page], rerun=True)

    assert page in dialog.walk
    assert page.tactic is BossTactic.EVERYONE_ATTACKS


# -- Custom only ------------------------------------------------------------------------


def test_a_page_for_custom_only_follows_custom_and_not_quick(qapp):
    dialog = FirstRunDialog([_Page("Always?"), _Page("Custom?", custom_only=True)])

    assert _headings(dialog) == [MODE_HEADING, "Always?"]
    assert dialog.step_text == "Step 1 of 2"

    dialog.mode_page.select(SetupMode.CUSTOM)

    assert _headings(dialog) == [MODE_HEADING, "Always?", "Custom?"]
    assert dialog.step_text == "Step 1 of 3"


def test_switching_mode_with_back_changes_the_step_count_and_the_last_page(qapp):
    dialog = FirstRunDialog([_Page("Always?"), _Page("Custom?", custom_only=True)])
    _next(dialog)
    assert (dialog.step_text, dialog.commit_button.text()) == ("Step 2 of 2", FINISH)

    dialog.back_button.click()
    QTest.mouseClick(dialog.mode_page.cards[1], Qt.MouseButton.LeftButton)
    _next(dialog)

    assert (dialog.step_text, dialog.commit_button.text()) == ("Step 2 of 3", NEXT)


# -- a failed write -----------------------------------------------------------------------


def test_a_failed_write_keeps_the_user_on_the_page_and_says_so(qapp, caplog):
    page = _Page("First?", ok=False)
    dialog = FirstRunDialog([page])
    _next(dialog)

    _next(dialog)

    assert dialog.current is page
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.error_shown
    assert "Could not save the setup answer" in caplog.text
    assert SAVE_FAILED.startswith("Couldn't save")

    page.ok = True
    _next(dialog)
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_the_failure_goes_once_the_page_is_left(qapp):
    page = _Page("First?", ok=False)
    dialog = FirstRunDialog([page])
    _next(dialog, 2)

    dialog.back_button.click()

    assert not dialog.error_shown


def test_a_failed_combat_write_keeps_the_question_unanswered(qapp, prefs, monkeypatch):
    monkeypatch.setattr(PreferenceStore, "set", lambda self, key, value: False)
    page = CombatQuestionPage(prefs)
    dialog = FirstRunDialog([page])
    _next(dialog, 2)

    assert dialog.current is page
    assert not combat_question_answered(prefs)


# -- the combat question ------------------------------------------------------------------


def test_the_recommended_answer_comes_first_and_is_selected(qapp, prefs):
    page = CombatQuestionPage(prefs)

    assert [card.name for card in page.cards] == [
        TACTIC_CARDS[BossTactic.ONE_HITTER][0],
        TACTIC_CARDS[BossTactic.EVERYONE_ATTACKS][0],
    ]
    assert page.tactic is BossTactic.ONE_HITTER
    assert [card.selected for card in page.cards] == [True, False]
    assert [card.detail_text for card in page.cards] == [RECOMMENDED_DETAIL, ""]


def test_the_note_names_the_solo_default_settings_and_the_combat_tab(qapp, prefs):
    note = CombatQuestionPage(prefs).note_text

    assert "Default Solo Duel" in note
    assert SETTINGS_COMBAT in note
    assert SETTINGS_COMBAT.replace(" ", " ") == "Settings › Combat"
    assert "Combat tab" in note


def test_the_stored_answer_is_the_one_selected(qapp, prefs):
    prefs.set(_TEAM, SHIPPED_EVERYONE_ATTACKS_DUEL_ID)
    page = CombatQuestionPage(prefs)

    assert page.tactic is BossTactic.EVERYONE_ATTACKS
    assert [card.selected for card in page.cards] == [False, True]


def test_showing_the_page_keeps_the_stored_answer_selected(qapp, prefs):
    """A card selects itself on taking focus; the first card must not take it on show."""
    prefs.set(_TEAM, SHIPPED_EVERYONE_ATTACKS_DUEL_ID)
    page = CombatQuestionPage(prefs)
    dialog = FirstRunDialog([page], rerun=True)
    dialog.show()
    _next(dialog)
    qapp.processEvents()

    assert page.tactic is BossTactic.EVERYONE_ATTACKS
    dialog.close()


def test_showing_the_dialog_keeps_quick_selected(qapp):
    dialog = FirstRunDialog([_Page("First?")])
    dialog.mode_page.select(SetupMode.CUSTOM)

    dialog.show()
    qapp.processEvents()

    assert dialog.mode_page.mode is SetupMode.CUSTOM
    dialog.close()


def test_a_click_on_a_card_selects_it(qapp, prefs):
    page = CombatQuestionPage(prefs)

    QTest.mouseClick(page.card_for(BossTactic.EVERYONE_ATTACKS), Qt.MouseButton.LeftButton)

    assert page.tactic is BossTactic.EVERYONE_ATTACKS
    assert [card.selected for card in page.cards] == [False, True]


@pytest.mark.parametrize(
    ("tactic", "stored"),
    [(BossTactic.ONE_HITTER, None), (BossTactic.EVERYONE_ATTACKS, SHIPPED_EVERYONE_ATTACKS_DUEL_ID)],
)
def test_next_stores_the_answer_and_settings_combat_shows_it(qapp, prefs, tactic, stored):
    page = CombatQuestionPage(prefs)
    dialog = FirstRunDialog([page])
    _next(dialog)
    page.select(tactic)

    _next(dialog)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert _record(prefs).get(_TEAM) == stored
    assert combat_question_answered(prefs)
    card = CombatDefaultsTab(prefs=PreferenceStore(prefs.path.parent)).card(TeamSize.TEAM)
    assert card.choice == stored
    expected = "Default Team Duel (Everyone Attacks)" if stored else "Default Team Duel"
    assert card.strategy_card._name_lbl.text() == expected


def test_a_rerun_on_a_duel_neither_answer_names_selects_none_and_keeps_it(qapp, prefs):
    """Settings › Combat chose it; Next on the page with no card taken leaves it alone."""
    prefs.set(_TEAM, _CUSTOM_DUEL)
    page = CombatQuestionPage(prefs)
    dialog = FirstRunDialog([page], rerun=True)
    dialog.show()
    _next(dialog)
    qapp.processEvents()

    assert page.tactic is None
    assert [card.selected for card in page.cards] == [False, False]
    _next(dialog)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert _record(prefs)[_TEAM] == _CUSTOM_DUEL
    dialog.close()


def test_a_user_who_answered_in_209_is_not_asked_again(qapp, prefs):
    """#209's Skip, or either answer, set the same key; the page is passed over."""
    prefs.set(ANSWERED_KEY, True)

    assert setup_pages(prefs)[0].answered()


# -- building and opening it ---------------------------------------------------------------


def test_the_combat_question_is_a_setup_page(qapp, prefs):
    assert [type(page) for page in setup_pages(prefs)] == [CombatQuestionPage]


def test_the_dialog_needs_a_page(qapp):
    with pytest.raises(ValueError):
        FirstRunDialog([])


def test_building_the_dialog_never_shows_a_parentless_widget(qapp, prefs, parentless_shows):
    FirstRunDialog([CombatQuestionPage(prefs)])

    assert parentless_shows == []


def test_the_suite_refuses_to_open_it_modally(qapp, prefs):
    """So a test that ever builds an ``AppController`` fails, rather than hanging on it."""
    dialog = FirstRunDialog([CombatQuestionPage(prefs)])

    with pytest.raises(AssertionError, match=FIRST_RUN_REFUSED):
        dialog.exec()


def test_the_heading_follows_the_page(qapp, prefs):
    dialog = FirstRunDialog([CombatQuestionPage(prefs)])
    _next(dialog)

    assert dialog.heading_text == COMBAT_HEADING


# -- where it opens -------------------------------------------------------------------------


class _JustMaximized(QWidget):
    """The main window as setup finds it at startup: maximized, at its stale normal size."""

    def isMaximized(self) -> bool:
        return True


def _center(dialog: FirstRunDialog) -> QPoint:
    return dialog.frameGeometry().center()


def test_it_opens_centered_on_the_screen_over_a_window_still_being_maximized(qapp):
    window = _JustMaximized()
    window.setGeometry(40, 40, 300, 200)
    window.show()
    dialog = FirstRunDialog([_Page("First?")], parent=window)

    dialog.show()

    usable = window.screen().availableGeometry()
    assert (_center(dialog) - usable.center()).manhattanLength() <= 2
    dialog.close()
    window.close()


def test_it_opens_centered_over_a_window_that_is_not_maximized(qapp):
    window = QWidget()
    window.setGeometry(100, 80, 900, 700)
    window.show()
    dialog = FirstRunDialog([_Page("First?")], parent=window)

    dialog.show()

    assert (_center(dialog) - window.frameGeometry().center()).manhattanLength() <= 2
    dialog.close()
    window.close()


def test_a_later_show_leaves_it_where_the_user_put_it(qapp):
    window = QWidget()
    window.setGeometry(100, 80, 900, 700)
    window.show()
    dialog = FirstRunDialog([_Page("First?")], parent=window)
    dialog.show()
    dialog.move(dialog.pos() + QPoint(30, 20))
    moved = dialog.pos()

    dialog.hide()
    dialog.show()

    assert dialog.pos() == moved
    dialog.close()
    window.close()
