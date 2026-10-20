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
from PyQt6.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget
from wizwalker import utils as wizwalker_utils

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.clients import character_roster
from bartlebot.clients.credentials import CredentialStore
from bartlebot.gui import screen_fit
from bartlebot.gui.dialogs.first_run_dialog import (
    ACCOUNTS_EMPTY,
    ACCOUNTS_HEADING,
    BACK,
    COMBAT_HEADING,
    FINISH,
    GAME_FOLDER_HEADING,
    GAME_FOLDER_UNSET,
    LAUNCH_PROFILE_HEADING,
    MODE_CARDS,
    MODE_HEADING,
    NEXT,
    PROFILE_EXISTS,
    PROFILE_NO_FIT,
    PROFILE_TOO_FEW,
    PROFILE_WILL_MAKE,
    RECOMMENDED_DETAIL,
    SAVE_FAILED,
    SETTINGS_COMBAT,
    SETUP_PAGE_TYPES,
    TACTIC_CARDS,
    AccountsPage,
    CombatQuestionPage,
    FirstRunDialog,
    FirstRunPage,
    GameArtPage,
    GameFolderPage,
    LaunchProfilePage,
    ModePage,
    OptionsPage,
    QuicksellPage,
    RealmAndDelayPage,
    SetupMode,
    needs_asking,
    setup_pages,
)
from bartlebot.gui.settings import accounts_tab
from bartlebot.gui.settings.accounts_tab import AccountsTab
from bartlebot.gui.settings.bot_tab import BotTab
from bartlebot.gui.settings.combat_defaults_tab import CombatDefaultsTab
from bartlebot.gui.settings.rows import SettingRow, SettingsForm
from bartlebot.gui.widgets.inputs.game_folder_input import FOLDER_EMPTY, FOLDER_MISSING, FOLDER_OK
from bartlebot.nodes.utility.change_realm import OPTION_FAVORITE_REALM
from bartlebot.options import bot_options, option_values
from bartlebot.options.custom_setup import ART_ANSWERED_KEY, EXTRACT_ART_KEY
from bartlebot.options.quicksell_setup import ANSWERED_KEY as QUICKSELL_ANSWERED
from bartlebot.options.quicksell_setup import SellFlags, current_flags, write_sell_flags
from bartlebot.options.realm_delay_setup import ANSWERED_KEY as REALM_DELAY_ANSWERED
from bartlebot.settings_manager import SettingsManager, default_for
from bartlebot.shared import account_setup, game_install, launch_profiles
from bartlebot.shared.account_store import AccountStore
from bartlebot.shared.combat_defaults import PREFERENCE_KEYS, SHIPPED_EVERYONE_ATTACKS_DUEL_ID, TeamSize
from bartlebot.shared.first_run import ANSWERED_KEY, BossTactic, combat_question_answered
from bartlebot.shared.preferences import PreferenceStore
from tests.conftest import FIRST_RUN_REFUSED

_TEAM = PREFERENCE_KEYS[TeamSize.TEAM]
_CUSTOM_DUEL = "00000000-0000-0000-0000-0000000000d1"


#: A 1080p screen less its taskbar: what most users have, and where a profile's resolution matters.
_1080P = ((1920, 1032), 1.0)


@pytest.fixture
def prefs(tmp_path: Path) -> PreferenceStore:
    return PreferenceStore(tmp_path / "vendor")


@pytest.fixture
def team() -> None:
    """Two accounts: the combat question is in the walk only for a team (#264)."""
    _two_accounts()


@pytest.fixture(autouse=True)
def screen(monkeypatch):
    """Pin the screen the Launch profile page fits its resolution to, so it is not the runner's."""
    monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: _1080P)

    def _use(metrics):
        monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: metrics)

    return _use


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


def test_the_mode_is_not_stored(qapp, prefs, team):
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


def test_a_rerun_opens_the_combat_page_on_the_stored_answer(qapp, prefs, team):
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


def test_a_failed_combat_write_keeps_the_question_unanswered(qapp, prefs, team, monkeypatch):
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


def test_showing_the_page_keeps_the_stored_answer_selected(qapp, prefs, team):
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


def test_each_card_leads_with_a_radio_checked_while_it_is_selected(qapp, prefs):
    page = CombatQuestionPage(prefs)
    assert [card.radio.isChecked() for card in page.cards] == [True, False]

    QTest.mouseClick(page.card_for(BossTactic.EVERYONE_ATTACKS), Qt.MouseButton.LeftButton)
    assert [card.radio.isChecked() for card in page.cards] == [False, True]

    page.select(None)
    assert [card.radio.isChecked() for card in page.cards] == [False, False]


def test_a_cards_radio_leaves_clicks_and_focus_to_the_card(qapp):
    """The card selects on a click or on focus; a radio taking either would bypass it."""
    for card in ModePage().cards:
        assert card.radio.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert card.radio.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_the_cards_are_stacked_each_the_pages_full_width(qapp):
    dialog = FirstRunDialog([_Page("First?")])
    dialog.show()
    qapp.processEvents()

    page = dialog.mode_page
    first, second = page.cards
    assert first.x() == second.x() == 0
    assert first.width() == second.width() == page.width()
    assert second.y() > first.geometry().bottom()
    dialog.close()


@pytest.mark.parametrize(
    ("tactic", "stored"),
    [(BossTactic.ONE_HITTER, None), (BossTactic.EVERYONE_ATTACKS, SHIPPED_EVERYONE_ATTACKS_DUEL_ID)],
)
def test_next_stores_the_answer_and_settings_combat_shows_it(qapp, prefs, team, tactic, stored):
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


def test_a_rerun_on_a_duel_neither_answer_names_selects_none_and_keeps_it(qapp, prefs, team):
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


def _one_account() -> None:
    SettingsManager.instance().accounts = [{"username": ONE, "alias": ""}]


def test_with_one_account_the_combat_question_is_not_in_the_walk(qapp, prefs):
    _one_account()
    page = CombatQuestionPage(prefs)

    assert page not in FirstRunDialog([_Page("First?"), page]).walk


def test_a_second_account_added_on_the_way_brings_the_combat_question_in(qapp, prefs):
    _one_account()
    page = CombatQuestionPage(prefs)
    dialog = FirstRunDialog([_Page("First?"), page])

    _two_accounts()
    dialog.mode_page.walk_changed.emit()

    assert page in dialog.walk
    assert dialog.step_text == "Step 1 of 3"


def test_finish_with_one_account_records_the_question_on_the_stored_default(qapp, prefs):
    """So a second account added later doesn't open setup for it alone."""
    _one_account()
    prefs.set(_TEAM, SHIPPED_EVERYONE_ATTACKS_DUEL_ID)
    dialog = FirstRunDialog([_Page("First?"), CombatQuestionPage(prefs)])

    _next(dialog, 2)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert combat_question_answered(prefs)
    assert _record(prefs)[_TEAM] == SHIPPED_EVERYONE_ATTACKS_DUEL_ID
    _two_accounts()
    assert not CombatQuestionPage.asks_first_run(prefs)


def test_closing_setup_with_one_account_leaves_the_question_unanswered(qapp, prefs):
    _one_account()
    dialog = FirstRunDialog([_Page("First?"), CombatQuestionPage(prefs)])
    _next(dialog)

    dialog.reject()

    assert ANSWERED_KEY not in _record(prefs)


def test_one_account_with_only_the_combat_question_open_needs_no_setup(qapp, prefs, tmp_path, monkeypatch):
    _everything_answered(prefs, tmp_path, monkeypatch)
    _one_account()
    prefs.set(ANSWERED_KEY, False)

    assert not needs_asking(prefs)


def test_a_user_who_answered_in_209_is_not_asked_again(qapp, prefs):
    """#209's Skip, or either answer, set the same key; the page is passed over."""
    prefs.set(ANSWERED_KEY, True)

    assert next(page for page in setup_pages(prefs) if isinstance(page, CombatQuestionPage)).answered()


# -- building and opening it ---------------------------------------------------------------


def test_the_pages_are_in_the_plans_order(qapp, prefs):
    assert [type(page) for page in setup_pages(prefs)] == [
        GameFolderPage,
        AccountsPage,
        LaunchProfilePage,
        CombatQuestionPage,
        QuicksellPage,
        RealmAndDelayPage,
        OptionsPage,
        GameArtPage,
    ]


def test_the_page_types_are_the_pages_built(qapp, prefs):
    assert [type(page) for page in setup_pages(prefs)] == list(SETUP_PAGE_TYPES)


def _two_accounts() -> None:
    SettingsManager.instance().accounts = [{"username": u, "alias": ""} for u in (ONE, TWO)]


def _everything_answered(prefs: PreferenceStore, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(game_install, "_places", _Launcher(_install(tmp_path / "Wizard101")))
    _two_accounts()
    settings = SettingsManager.instance()
    settings.launch_profiles = [{"name": "Everyone", "accounts": [ONE, TWO]}]
    prefs.set(ANSWERED_KEY, True)
    settings.set_general(QUICKSELL_ANSWERED, True)
    settings.set_general(REALM_DELAY_ANSWERED, True)
    settings.set_general(ART_ANSWERED_KEY, True)


#: Machine states the first-run check is asked in, each a function of (prefs, tmp_path, monkeypatch).
_STATES = {
    "fresh": lambda prefs, tmp_path, monkeypatch: None,
    "one account": lambda prefs, tmp_path, monkeypatch: setattr(
        SettingsManager.instance(), "accounts", [{"username": ONE, "alias": ""}]
    ),
    "two accounts": lambda prefs, tmp_path, monkeypatch: _two_accounts(),
    "art turned off": lambda prefs, tmp_path, monkeypatch: SettingsManager.instance().set_general(
        EXTRACT_ART_KEY, False
    ),
    "everything answered": _everything_answered,
}


@pytest.mark.parametrize("state", list(_STATES))
def test_each_page_asks_a_first_run_as_its_answer_and_walk_say(qapp, prefs, tmp_path, monkeypatch, state):
    """``asks_first_run`` reads the stores without building the page; it must agree with the page built."""
    _STATES[state](prefs, tmp_path, monkeypatch)

    for page in setup_pages(prefs):
        built = not page.answered() and page.applies(SetupMode.QUICK)
        assert type(page).asks_first_run(prefs) == built, type(page).__name__


def test_a_first_run_is_needed_until_every_page_is_answered(qapp, prefs, tmp_path, monkeypatch):
    assert needs_asking(prefs)

    _everything_answered(prefs, tmp_path, monkeypatch)

    assert not needs_asking(prefs)


def test_the_dialog_needs_a_page(qapp):
    with pytest.raises(ValueError):
        FirstRunDialog([])


def test_building_the_dialog_never_shows_a_parentless_widget(qapp, prefs, team, parentless_shows):
    FirstRunDialog([CombatQuestionPage(prefs)])

    assert parentless_shows == []


def test_the_suite_refuses_to_open_it_modally(qapp, prefs, team):
    """So a test that ever builds an ``AppController`` fails, rather than hanging on it."""
    dialog = FirstRunDialog([CombatQuestionPage(prefs)])

    with pytest.raises(AssertionError, match=FIRST_RUN_REFUSED):
        dialog.exec()


def test_the_heading_follows_the_page(qapp, prefs, team):
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


# -- a page that isn't ready -------------------------------------------------------


class _Unready(_Page):
    def __init__(self, heading: str) -> None:
        super().__init__(heading)
        self.is_ready = False

    def ready(self) -> bool:
        return self.is_ready


def test_next_waits_for_a_page_that_is_not_ready(qapp):
    page = _Unready("Where?")
    dialog = FirstRunDialog([page, _Page("Then?")])
    _next(dialog)

    assert not dialog.commit_button.isEnabled()
    dialog.commit_button.click()
    assert (dialog.current, page.recorded) == (page, 0)

    page.is_ready = True
    page.ready_changed.emit()
    assert dialog.commit_button.isEnabled()
    _next(dialog)
    assert page.recorded == 1


# -- the game folder ---------------------------------------------------------------


def _install(folder: Path) -> Path:
    """A folder the game's client is in."""
    (folder / "Bin").mkdir(parents=True)
    (folder / "Bin" / "WizardGraphicalClient.exe").write_bytes(b"")
    return folder


class _Launcher(game_install.NoPlaces):
    """A machine where the KingsIsle launcher installed the game in *folder*."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def launcher_dir(self) -> Path:
        return self.folder


@pytest.fixture
def wizwalker_install():
    """wizwalker's override, put back afterwards: Next hands it the folder."""
    yield wizwalker_utils
    wizwalker_utils.override_wiz_install_location(None)


def _folder_page(dialog: FirstRunDialog) -> GameFolderPage:
    return next(page for page in dialog.pages if isinstance(page, GameFolderPage))


def test_the_game_folder_page_shows_only_when_nothing_is_found(qapp, prefs, tmp_path, monkeypatch):
    assert not GameFolderPage(prefs).answered()
    assert GameFolderPage in [type(page) for page in FirstRunDialog(setup_pages(prefs)).walk]

    monkeypatch.setattr(game_install, "_places", _Launcher(_install(tmp_path / "Wizard101")))

    assert GameFolderPage(prefs).answered()
    assert GameFolderPage not in [type(page) for page in FirstRunDialog(setup_pages(prefs)).walk]


def test_the_game_folder_page_accepts_only_a_folder_holding_the_client(qapp, prefs, tmp_path, wizwalker_install):
    dialog = FirstRunDialog(setup_pages(prefs))
    _next(dialog)
    page = _folder_page(dialog)
    assert dialog.current is page
    assert (dialog.heading_text, page.status_text) == (GAME_FOLDER_HEADING, FOLDER_EMPTY)
    assert not dialog.commit_button.isEnabled()

    empty = tmp_path / "Empty"
    empty.mkdir()
    page.folder_input.set_path(empty)
    assert page.status_text == FOLDER_MISSING
    assert not dialog.commit_button.isEnabled()

    folder = _install(tmp_path / "Games" / "Wizard101")
    page.folder_input.set_path(folder)
    assert page.status_text == FOLDER_OK
    assert dialog.commit_button.isEnabled()

    _next(dialog)
    assert prefs.get(game_install.INSTALL_DIR_KEY) == str(folder)
    assert wizwalker_install.get_wiz_install() == folder.absolute()
    assert dialog.current is not page


def test_browse_puts_the_folder_chosen_in_the_field(qapp, prefs, tmp_path):
    page = GameFolderPage(prefs)
    folder = _install(tmp_path / "Wizard101")
    page.folder_input.choose_directory = lambda _parent, _start: str(folder)

    page.folder_input.browse_button.click()

    assert page.folder_input.path() == str(folder)
    assert page.ready()


def test_cancelling_browse_leaves_the_field(qapp, prefs, tmp_path):
    page = GameFolderPage(prefs)
    page.folder_input.set_path(tmp_path)
    page.folder_input.choose_directory = lambda _parent, _start: ""

    page.folder_input.browse_button.click()

    assert page.folder_input.path() == str(tmp_path)


def test_a_rerun_shows_the_folder_in_use_and_next_keeps_it_detected(qapp, prefs, tmp_path, monkeypatch):
    folder = _install(tmp_path / "Wizard101")
    monkeypatch.setattr(game_install, "_places", _Launcher(folder))
    dialog = FirstRunDialog(setup_pages(prefs), rerun=True)
    _next(dialog)
    page = _folder_page(dialog)

    assert page.folder_input.path() == str(folder)
    assert page.status_text == "Found in the KingsIsle launcher's folder."
    _next(dialog)
    assert prefs.get(game_install.INSTALL_DIR_KEY) is None


def test_a_folder_changed_to_on_a_rerun_is_stored(qapp, prefs, tmp_path, monkeypatch, wizwalker_install):
    monkeypatch.setattr(game_install, "_places", _Launcher(_install(tmp_path / "Launcher")))
    other = _install(tmp_path / "Other")
    page = GameFolderPage(prefs)

    page.folder_input.set_path(other)

    assert page.record()
    assert prefs.get(game_install.INSTALL_DIR_KEY) == str(other)
    assert GameFolderPage(prefs).folder_input.path() == str(other)


class _Running(game_install.NoPlaces):
    """A machine where the game is found only while it runs, from *folder*; None until it starts."""

    def __init__(self, folder: Path | None = None) -> None:
        self.folder = folder

    def running_clients(self) -> list[Path]:
        return [] if self.folder is None else [self.folder / "Bin" / "WizardGraphicalClient.exe"]


def test_starting_the_game_fills_the_folder_in_and_next_stores_it(
    qapp, prefs, tmp_path, monkeypatch, wizwalker_install
):
    """Found only while the game runs, so Next stores it: closed, nothing would find it again."""
    machine = _Running()
    monkeypatch.setattr(game_install, "_places", machine)
    dialog = FirstRunDialog(setup_pages(prefs))
    _next(dialog)
    page = _folder_page(dialog)
    assert page.polling
    assert not page.look_for_running_game()
    assert not dialog.commit_button.isEnabled()

    machine.folder = _install(tmp_path / "Games" / "W101")

    assert page.look_for_running_game()
    assert page.folder_input.path() == str(machine.folder)
    assert page.status_text == "Found in the game running now."
    assert not page.polling
    assert dialog.commit_button.isEnabled()
    _next(dialog)
    assert prefs.get(game_install.INSTALL_DIR_KEY) == str(machine.folder)
    assert wizwalker_install.get_wiz_install() == machine.folder.absolute()


def test_a_folder_the_user_typed_is_never_replaced_by_a_running_game(qapp, prefs, tmp_path, monkeypatch):
    monkeypatch.setattr(game_install, "_places", _Running(_install(tmp_path / "Running")))
    page = GameFolderPage(prefs)
    page.folder_input.set_path(tmp_path / "Typed")

    assert not page.look_for_running_game()
    assert page.folder_input.path() == str(tmp_path / "Typed")


def test_a_page_with_the_game_found_never_looks_for_it_running(qapp, prefs, tmp_path, monkeypatch):
    monkeypatch.setattr(game_install, "_places", _Launcher(_install(tmp_path / "Wizard101")))

    assert not GameFolderPage(prefs).polling


def test_closing_setup_stops_looking_for_a_running_game(qapp, prefs):
    dialog = FirstRunDialog(setup_pages(prefs))
    page = _folder_page(dialog)
    assert page.polling

    dialog.reject()

    assert not page.polling


def test_a_rerun_with_nothing_found_passes_the_page_and_writes_nothing(qapp, prefs):
    """The rerun is how the other answers are changed; a game on a drive that is gone must not hold them."""
    dialog = FirstRunDialog(setup_pages(prefs, rerun=True), rerun=True)
    _next(dialog)
    page = _folder_page(dialog)
    assert dialog.current is page

    assert page.status_text == GAME_FOLDER_UNSET
    assert dialog.commit_button.isEnabled()
    _next(dialog)
    assert dialog.current is not page
    assert prefs.get(game_install.INSTALL_DIR_KEY) is None


def test_a_rerun_still_refuses_a_folder_without_the_game(qapp, prefs, tmp_path):
    dialog = FirstRunDialog(setup_pages(prefs, rerun=True), rerun=True)
    _next(dialog)
    page = _folder_page(dialog)
    empty = tmp_path / "Empty"
    empty.mkdir()

    page.folder_input.set_path(empty)

    assert page.status_text == FOLDER_MISSING
    assert not dialog.commit_button.isEnabled()


def test_a_first_run_with_nothing_found_still_needs_a_folder(qapp, prefs):
    dialog = FirstRunDialog(setup_pages(prefs))
    _next(dialog)

    assert dialog.current is _folder_page(dialog)
    assert not dialog.commit_button.isEnabled()


# -- accounts (Stage 3) ---------------------------------------------------------------------

# Synthetic: account usernames, never a real login.
ONE = "one@example.com"
TWO = "two@example.com"


class _AccountEditor:
    """Stands in for ``accounts_tab._AccountDialog``, whose ``exec()`` a test cannot run."""

    accepted = True
    username_typed = ONE
    alias_typed = ""
    password_typed = "pw"

    def __init__(self, username="", alias="", color=None, taken=None, parent=None):
        self._username = username or type(self).username_typed

    def exec(self):
        return QDialog.DialogCode.Accepted if type(self).accepted else QDialog.DialogCode.Rejected

    @property
    def username(self):
        return self._username

    @property
    def alias(self):
        return type(self).alias_typed

    @property
    def password(self):
        return type(self).password_typed

    @property
    def color(self):
        return None


@pytest.fixture
def editor(monkeypatch):
    """The account editor answered per test, with Credential Manager and the roster recorded instead."""

    class Editor(_AccountEditor):
        passwords: dict[str, str] = {}

    monkeypatch.setattr(accounts_tab, "_AccountDialog", Editor)
    monkeypatch.setattr(CredentialStore, "store", staticmethod(lambda u, pw: Editor.passwords.__setitem__(u, pw)))
    monkeypatch.setattr(CredentialStore, "delete", staticmethod(lambda u: Editor.passwords.pop(u, None)))
    monkeypatch.setattr(character_roster, "forget", lambda u: None)
    monkeypatch.setattr(
        accounts_tab.QMessageBox, "question", lambda *a, **k: accounts_tab.QMessageBox.StandardButton.Yes
    )
    return Editor


def _accounts_on_disk() -> list[str]:
    """The usernames in the shared ``accounts.json``, read fresh as the other product would."""
    return [account["username"] for account in AccountStore().accounts]


def test_an_account_added_on_the_page_is_stored_and_shows_in_settings(qapp, prefs, editor):
    page = AccountsPage(prefs)
    walk_changes: list[int] = []
    page.walk_changed.connect(lambda: walk_changes.append(1))

    page.accounts_column.add_card.add_requested.emit()

    assert _accounts_on_disk() == [ONE]
    assert editor.passwords == {ONE: "pw"}
    assert len(page.accounts_column.cards) == 1
    assert len(AccountsTab().accounts_column.cards) == 1
    assert walk_changes == [1]


def test_a_cancelled_add_changes_nothing(qapp, prefs, editor):
    editor.accepted = False
    page = AccountsPage(prefs)

    page.accounts_column.add_card.add_requested.emit()

    assert _accounts_on_disk() == []
    assert page.accounts_column.cards == []


def test_edit_and_remove_on_the_page_use_the_settings_actions(qapp, prefs, editor):
    SettingsManager.instance().accounts = [{"username": ONE, "alias": ""}, {"username": TWO, "alias": ""}]
    editor.passwords.update({ONE: "pw1", TWO: "pw2"})
    editor.alias_typed = "Main"
    editor.password_typed = ""  # kept, as the editor says
    page = AccountsPage(prefs)

    page.accounts_column.cards[0].edit_account_requested.emit(ONE)
    page.accounts_column.cards[1].delete_account_requested.emit(TWO)

    assert [(a["username"], a["alias"]) for a in AccountStore().accounts] == [(ONE, "Main")]
    assert editor.passwords == {ONE: "pw1"}
    assert len(page.accounts_column.cards) == 1


def test_with_no_account_the_page_says_what_still_works(qapp, prefs):
    page = AccountsPage(prefs)

    texts = [label.text() for label in page.accounts_column.findChildren(QLabel)]
    assert ACCOUNTS_EMPTY in texts
    assert page.ready()


def test_next_with_no_account_records_the_page_answered(qapp, prefs):
    page = AccountsPage(prefs)
    dialog = FirstRunDialog([page])
    _next(dialog)

    assert dialog.heading_text == ACCOUNTS_HEADING
    _next(dialog)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert _record(prefs)[account_setup.ANSWERED_KEY] is True
    assert SettingsManager.instance().accounts == []
    assert AccountsPage(prefs).answered()


def test_a_failed_record_keeps_the_user_on_the_page(qapp, prefs, monkeypatch):
    monkeypatch.setattr(PreferenceStore, "set", lambda self, key, value: False)
    dialog = FirstRunDialog([AccountsPage(prefs)])
    _next(dialog, 2)

    assert dialog.heading_text == ACCOUNTS_HEADING
    assert dialog.error_shown


def test_an_account_in_the_shared_file_answers_the_page(qapp, prefs):
    """Written by the other product, before BartleBot read the file."""
    AccountStore().set_accounts([{"username": ONE, "alias": ""}])

    assert AccountsPage(prefs).answered()
    assert _record(prefs) == {}


def test_a_first_run_with_an_account_does_not_show_the_page(qapp, prefs):
    SettingsManager.instance().accounts = [{"username": ONE, "alias": ""}]

    dialog = FirstRunDialog([AccountsPage(prefs), _Page("Last?")])

    assert _headings(dialog) == [MODE_HEADING, "Last?"]


def test_a_rerun_shows_the_page_on_the_stored_accounts(qapp, prefs):
    SettingsManager.instance().accounts = [{"username": ONE, "alias": ""}, {"username": TWO, "alias": ""}]

    dialog = FirstRunDialog([AccountsPage(prefs)], rerun=True)

    assert _headings(dialog) == [MODE_HEADING, ACCOUNTS_HEADING]
    assert len(dialog.walk[1].accounts_column.cards) == 2


# -- the Launch profile page (Stage 4) -----------------------------------------------

THREE = "three@example.com"


def _profiles_on_disk() -> list[dict]:
    """The launch profiles in the shared ``accounts.json``, read fresh as the other product would."""
    return AccountStore().launch_profiles


def _three_accounts() -> None:
    SettingsManager.instance().accounts = [{"username": u, "alias": ""} for u in (ONE, TWO, THREE)]


def test_every_account_is_listed_checked_in_the_order_of_the_accounts_page(qapp, prefs):
    SettingsManager.instance().accounts = [
        {"username": TWO, "alias": "Second"},
        {"username": ONE, "alias": "First"},
    ]

    page = LaunchProfilePage(prefs)

    assert [box.text() for box in page.boxes] == ["Second", "First"]
    assert all(box.isChecked() for box in page.boxes)
    assert page.checked == [TWO, ONE]
    assert page.status_text == PROFILE_WILL_MAKE.format(name="Everyone", n=2)


def test_next_makes_one_profile_of_the_checked_accounts_in_order(qapp, prefs):
    _three_accounts()
    page = LaunchProfilePage(prefs)
    page.boxes[1].setChecked(False)

    assert page.record()

    assert _profiles_on_disk() == [{"name": "Everyone", "accounts": [ONE, THREE], "resolution": [560, 420]}]
    assert _record(prefs)[launch_profiles.ANSWERED_KEY] is True


def test_the_profile_shows_on_settings_game(qapp, prefs):
    _three_accounts()

    assert LaunchProfilePage(prefs).record()

    assert len(AccountsTab().profiles_column.cards) == 1


def test_a_name_already_taken_gets_the_next_free_one(qapp, prefs):
    _three_accounts()
    SettingsManager.instance().launch_profiles = [{"name": "Everyone", "accounts": [ONE]}]
    page = LaunchProfilePage(prefs)

    assert page.status_text == PROFILE_WILL_MAKE.format(name="Everyone 2", n=3)
    assert page.record()

    assert [p["name"] for p in _profiles_on_disk()] == ["Everyone", "Everyone 2"]


def test_fewer_than_two_checked_makes_no_profile_and_still_records_the_page(qapp, prefs):
    _three_accounts()
    page = LaunchProfilePage(prefs)
    page.boxes[0].setChecked(False)
    page.boxes[2].setChecked(False)

    assert page.status_text == PROFILE_TOO_FEW
    assert page.record()

    assert _profiles_on_disk() == []
    assert _record(prefs)[launch_profiles.ANSWERED_KEY] is True
    assert LaunchProfilePage(prefs).answered()


def test_the_same_accounts_already_launching_together_make_no_second_profile(qapp, prefs):
    """A rerun, or Next tried again after a failed write, finds the profile already made."""
    _three_accounts()
    SettingsManager.instance().launch_profiles = [{"name": "Team", "accounts": [ONE, TWO, THREE]}]
    page = LaunchProfilePage(prefs)

    assert page.status_text == PROFILE_EXISTS.format(name="Team")
    assert page.record()

    assert [p["name"] for p in _profiles_on_disk()] == ["Team"]


def test_a_failed_profile_write_keeps_the_user_on_the_page(qapp, prefs, monkeypatch):
    _three_accounts()
    monkeypatch.setattr(AccountStore, "save", lambda self: False)
    dialog = FirstRunDialog([LaunchProfilePage(prefs), _Page("Last?")])
    _next(dialog, 2)

    assert dialog.heading_text == LAUNCH_PROFILE_HEADING
    assert dialog.error_shown
    assert launch_profiles.ANSWERED_KEY not in _record(prefs)


def test_the_page_does_not_apply_with_fewer_than_two_accounts(qapp, prefs):
    SettingsManager.instance().accounts = [{"username": ONE, "alias": ""}]

    dialog = FirstRunDialog([LaunchProfilePage(prefs), _Page("Last?")])

    assert _headings(dialog) == [MODE_HEADING, "Last?"]


def test_the_page_is_not_shown_when_a_profile_exists(qapp, prefs):
    """Made by either product: ``accounts.json`` holds it before BartleBot reads the file."""
    other = AccountStore()
    other.set_accounts([{"username": u, "alias": ""} for u in (ONE, TWO, THREE)])
    other.set_launch_profiles([{"name": "Duo", "accounts": [ONE, TWO]}])

    dialog = FirstRunDialog([LaunchProfilePage(prefs), _Page("Last?")])

    assert _headings(dialog) == [MODE_HEADING, "Last?"]


def test_a_rerun_shows_the_page_while_two_accounts_exist(qapp, prefs):
    _three_accounts()
    SettingsManager.instance().launch_profiles = [{"name": "Duo", "accounts": [ONE, TWO]}]

    dialog = FirstRunDialog([LaunchProfilePage(prefs)], rerun=True)

    assert _headings(dialog) == [MODE_HEADING, LAUNCH_PROFILE_HEADING]


def test_accounts_added_on_the_page_before_bring_the_launch_profile_page(qapp, prefs, editor):
    """The walk across both pages: Stage 3's open item for #229."""
    accounts_page = AccountsPage(prefs)
    dialog = FirstRunDialog([accounts_page, LaunchProfilePage(prefs), _Page("Last?")])
    _next(dialog)
    assert _headings(dialog) == [MODE_HEADING, ACCOUNTS_HEADING, "Last?"]

    accounts_page.accounts_column.add_card.add_requested.emit()
    assert _headings(dialog) == [MODE_HEADING, ACCOUNTS_HEADING, "Last?"]
    editor.username_typed = TWO
    accounts_page.accounts_column.add_card.add_requested.emit()
    assert _headings(dialog) == [MODE_HEADING, ACCOUNTS_HEADING, LAUNCH_PROFILE_HEADING, "Last?"]
    assert dialog.step_text == "Step 2 of 4"

    _next(dialog)
    assert dialog.heading_text == LAUNCH_PROFILE_HEADING
    assert dialog.current.checked == [ONE, TWO]
    _next(dialog)

    assert dialog.heading_text == "Last?"
    assert _profiles_on_disk() == [{"name": "Everyone", "accounts": [ONE, TWO], "resolution": [560, 420]}]


def test_next_with_no_account_passes_the_launch_profile_page_over(qapp, prefs):
    dialog = FirstRunDialog([AccountsPage(prefs), LaunchProfilePage(prefs), _Page("Last?")])
    _next(dialog, 2)

    assert dialog.heading_text == "Last?"
    assert _profiles_on_disk() == []


def test_an_account_removed_before_the_page_is_not_on_it(qapp, prefs, editor):
    _three_accounts()
    accounts_page = AccountsPage(prefs)
    dialog = FirstRunDialog([accounts_page, LaunchProfilePage(prefs)], rerun=True)
    _next(dialog)
    _next(dialog)
    assert dialog.current.checked == [ONE, TWO, THREE]
    dialog.back_button.click()

    accounts_page.accounts_column.cards[2].delete_account_requested.emit(THREE)
    _next(dialog)

    assert dialog.current.checked == [ONE, TWO]


def test_an_account_unchecked_stays_unchecked_after_back(qapp, prefs):
    _three_accounts()
    page = LaunchProfilePage(prefs)
    dialog = FirstRunDialog([AccountsPage(prefs), page], rerun=True)
    _next(dialog, 2)
    page.boxes[0].setChecked(False)

    dialog.back_button.click()
    _next(dialog)

    assert page.checked == [TWO, THREE]


def test_a_box_drawn_again_leaves_none_of_the_old_ones_showing(qapp, prefs):
    """Out of the layout and waiting for its deferred delete, an old box sat over the intro."""
    _three_accounts()
    page = LaunchProfilePage(prefs)
    old = page.boxes

    page.enter()

    assert all(box.isHidden() for box in old)
    assert not any(box.isHidden() for box in page.boxes)


# -- the Launch profile page's resolution ---------------------------------------------


def test_the_resolution_starts_on_the_largest_that_fits_the_accounts_checked(qapp, prefs):
    """Two 800x600 clients do not fit side by side on 1080p: the global one would make a dead profile."""
    _three_accounts()
    page = LaunchProfilePage(prefs)
    assert page.resolution == (534, 300)

    page.boxes[2].setChecked(False)

    assert page.resolution == (560, 420)


def test_resolutions_too_big_for_the_accounts_checked_are_grayed(qapp, prefs):
    SettingsManager.instance().accounts = [{"username": u, "alias": ""} for u in (ONE, TWO)]
    combo = LaunchProfilePage(prefs).resolution_combo
    model = combo.model()

    enabled = {combo.itemData(i): model.item(i).isEnabled() for i in range(combo.count())}

    assert enabled[(560, 420)]
    assert not enabled[(800, 600)]


def test_a_smaller_resolution_picked_is_the_one_saved(qapp, prefs):
    SettingsManager.instance().accounts = [{"username": u, "alias": ""} for u in (ONE, TWO)]
    page = LaunchProfilePage(prefs)
    combo = page.resolution_combo
    index = next(i for i in range(combo.count()) if combo.itemData(i) == (534, 300))

    combo.setCurrentIndex(index)
    combo.activated.emit(index)

    assert page.record()
    assert _profiles_on_disk()[0]["resolution"] == [534, 300]


def test_a_pick_that_stops_fitting_gives_way_to_the_largest_that_does(qapp, prefs):
    _three_accounts()
    page = LaunchProfilePage(prefs)
    page.boxes[2].setChecked(False)
    combo = page.resolution_combo
    index = next(i for i in range(combo.count()) if combo.itemData(i) == (560, 420))
    combo.activated.emit(index)

    page.boxes[2].setChecked(True)

    assert page.resolution == (534, 300)


def test_the_resolution_is_judged_at_the_default_canvas_whatever_setup_fitted(qapp, prefs):
    """A canvas fitted to one account would otherwise leave three no resolution at all."""
    SettingsManager.instance().set_general("bottom_section_height", 500)
    _three_accounts()

    assert LaunchProfilePage(prefs).resolution == (534, 300)


def test_with_no_resolution_that_fits_the_profile_follows_the_global_one(qapp, prefs, screen):
    screen(((1366, 728), 1.0))
    _three_accounts()
    page = LaunchProfilePage(prefs)

    assert page.resolution is None
    assert page.status_text.endswith(PROFILE_NO_FIT.format(n=3))
    assert page.record()
    assert "resolution" not in _profiles_on_disk()[0]


def test_the_resolution_is_disabled_when_no_profile_is_made(qapp, prefs):
    _three_accounts()
    page = LaunchProfilePage(prefs)
    assert page.resolution_combo.isEnabled()

    page.boxes[0].setChecked(False)
    page.boxes[1].setChecked(False)

    assert not page.resolution_combo.isEnabled()


# -- quicksell (Stage 5, #230) -------------------------------------------------------


def _row(widget: QWidget, title: str) -> SettingRow:
    """The row titled *title*, found the way a reader would."""
    return next(row for row in widget.findChildren(SettingRow) if row.title == title)


_SWITCHES = ("Enable Quicksell", "Sell Pets", "Sell Socket Wrenches", "Sell Skeleton Keys")


def test_the_quicksell_page_shows_the_bot_tabs_rows_but_the_explanation(qapp):
    page_titles = [row.title for row in QuicksellPage().findChildren(SettingRow)]
    tab_group = next(group for group in BotTab().groups if group.group_id == "quicksell")
    tab_titles = [row.title for row in tab_group.findChildren(SettingRow)]

    assert page_titles == [*_SWITCHES, "Always keep"]
    assert tab_titles == [*_SWITCHES, "Show Quicksell explanation before each run", "Always keep"]


def test_the_quicksell_page_opens_on_the_stored_settings(qapp):
    write_sell_flags(SettingsManager.instance(), SellFlags(True, False, True, False))
    page = QuicksellPage()

    assert [_row(page, title).control.isChecked() for title in _SWITCHES] == [True, False, True, False]


def test_a_fresh_install_opens_on_quicksell_off(qapp):
    """Off by default (#262), and the rest on their defaults for when the user turns it on."""
    page = QuicksellPage()

    assert [_row(page, title).control.isChecked() for title in _SWITCHES] == [False, True, False, False]


def test_the_quicksell_group_on_the_page_does_not_fold(qapp):
    """Folded in Settings or not, every row is on the page."""
    group = QuicksellPage().quicksell.group

    assert group.fold_button is None
    assert not group.is_collapsed


@pytest.mark.parametrize("flags", [SellFlags(True, False, False, False), SellFlags(False, True, True, True)])
def test_what_the_page_writes_shows_on_settings_bot(qapp, flags):
    page = QuicksellPage()
    for title, on in zip(
        _SWITCHES, (flags.enabled, flags.sell_pets, flags.sell_socket_wrenches, flags.sell_skeleton_keys)
    ):
        _row(page, title).control.setChecked(on)

    assert page.record()

    tab = BotTab()
    assert current_flags(SettingsManager.instance()) == flags
    assert [_row(tab, title).control.isChecked() for title in _SWITCHES] == [
        flags.enabled,
        flags.sell_pets,
        flags.sell_socket_wrenches,
        flags.sell_skeleton_keys,
    ]


def test_the_keep_list_round_trips_through_the_page_and_the_bot_tab(qapp):
    page = QuicksellPage()
    _row(page, "Always keep").control.setText('Amber, "No Pain, No Gain Onyx"')
    page.record()

    tab = BotTab()
    assert _row(tab, "Always keep").control.text() == 'Amber, "No Pain, No Gain Onyx"'
    _row(tab, "Always keep").control.setText("Pearl")
    tab.apply()

    assert _row(QuicksellPage(), "Always keep").control.text() == "Pearl"


def test_the_quicksell_page_is_asked_on_a_fresh_install(qapp):
    assert not QuicksellPage().answered()


def test_a_user_whose_quicksell_settings_are_off_their_defaults_is_not_shown_the_page(qapp, prefs):
    SettingsManager.instance().set_general("quicksell_keep_list", ["Amber"])
    page = QuicksellPage()

    dialog = FirstRunDialog([CombatQuestionPage(prefs), page])

    assert page not in dialog.walk


def test_after_the_quicksell_page_it_is_answered_and_the_explanation_is_off(qapp):
    dialog = FirstRunDialog([QuicksellPage()])
    _next(dialog, 2)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert QuicksellPage().answered()
    assert SettingsManager.instance().show_quicksell_explanation is False


def test_a_failed_quicksell_write_keeps_the_user_on_the_page(qapp, monkeypatch):
    page = QuicksellPage()
    dialog = FirstRunDialog([page])
    _next(dialog)

    def _fail():
        raise OSError("disk full")

    monkeypatch.setattr(SettingsManager.instance(), "save", _fail)
    _next(dialog)

    assert dialog.current is page
    assert dialog.error_shown


def test_the_dialog_grows_to_fit_wrapped_text_on_a_page_turn(qapp):
    """Qt sizes a window from its layout's guessed label heights, never its height at the
    window's width; on the Quicksell page that cut Always keep's summary short."""
    page = QuicksellPage()
    dialog = FirstRunDialog([page])
    dialog.show()
    _next(dialog)
    # As short as Qt allows: what the guess gives, before the page needs more.
    dialog.resize(dialog.minimumWidth(), dialog.minimumSizeHint().height())
    qapp.processEvents()

    dialog.back_button.click()
    _next(dialog)
    qapp.processEvents()

    assert dialog.height() >= dialog.layout().totalHeightForWidth(dialog.width())
    for row in page.findChildren(SettingRow):
        assert row.summary.height() >= row.summary.heightForWidth(row.summary.width()), row.title
    dialog.close()


def test_the_quicksell_page_shows_every_description_with_no_way_to_hide_one(qapp):
    page = QuicksellPage()
    form = page.findChild(SettingsForm)

    assert form.switches.isHidden()
    for row in page.findChildren(SettingRow):
        assert row.help_button.isHidden(), row.title
        if row.details is not None:
            assert row.details_shown, row.title


@pytest.mark.parametrize(
    ("title", "keyword"), [("Sell Socket Wrenches", "Socket Wrench"), ("Sell Skeleton Keys", "Skeleton Key")]
)
def test_the_wrench_and_key_rows_say_what_they_do_to_quicksell(qapp, title, keyword):
    """On the page and on Settings › Bot alike: the rows come from one builder."""
    expected = f'When off, quicksell keeps any item with "{keyword}" in its name.'

    assert _row(QuicksellPage(), title).summary.text() == expected
    assert _row(BotTab(), title).summary.text() == expected


def test_the_dialog_grows_when_the_page_shown_grows(qapp):
    """Showing more on a page after it is shown re-lays the dialog out, and it is fitted again."""
    page = _Page("Growing?")
    column = QVBoxLayout(page)
    dialog = FirstRunDialog([page])
    dialog.show()
    _next(dialog)
    qapp.processEvents()
    before = dialog.height()
    center = _center(dialog)

    more = QLabel(" ".join(["Every wizard keeps what the settings keep."] * 30), page)
    more.setWordWrap(True)
    column.addWidget(more)
    # Shown on a later event, as a widget added to a shown parent is, then laid out.
    for _ in range(3):
        qapp.processEvents()

    assert dialog.height() > before
    assert dialog.height() >= dialog.layout().totalHeightForWidth(dialog.width())
    # About its center, so a dialog centered on show stays so.
    assert (_center(dialog) - center).manhattanLength() <= 2
    dialog.close()


# -- realm and Safety Delay (Stage 6, #231) ------------------------------------------


def _realm_page_dialog() -> tuple[FirstRunDialog, RealmAndDelayPage]:
    page = RealmAndDelayPage()
    dialog = FirstRunDialog([page])
    _next(dialog)
    return dialog, page


def test_a_first_run_shows_wraith_and_three_seconds_and_next_stores_both(qapp):
    dialog, page = _realm_page_dialog()

    assert page.realm_combo.get_value() == "Wraith"
    assert page.delay_box.value() == 3.0
    _next(dialog)

    sm = SettingsManager.instance()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert sm.favorite_realm == "Wraith"
    assert sm.default_safety_delay == 3.0
    assert sm.get_general(REALM_DELAY_ANSWERED) is True


def test_the_answer_reaches_settings_json(qapp):
    dialog, page = _realm_page_dialog()
    page.realm_combo.set_value("Balestrom")
    page.delay_box.setValue(6.5)
    _next(dialog)

    general = json.loads(SettingsManager.instance().settings_path.read_text(encoding="utf-8"))["general"]
    assert (general["favorite_realm"], general["default_safety_delay"]) == ("Balestrom", 6.5)


def test_a_rerun_shows_the_stored_realm_and_delay(qapp):
    sm = SettingsManager.instance()
    sm.favorite_realm = "Balestrom"
    sm.default_safety_delay = 7.5

    dialog = FirstRunDialog([RealmAndDelayPage()], rerun=True)
    _next(dialog)

    assert dialog.current.realm_combo.get_value() == "Balestrom"
    assert dialog.current.delay_box.value() == 7.5


def test_a_rerun_after_choosing_no_realm_shows_none(qapp):
    """Once the page is answered, an empty realm is the user's answer, not a gap to fill with Wraith."""
    dialog, page = _realm_page_dialog()
    page.realm_combo.set_value("")
    _next(dialog)

    assert RealmAndDelayPage().realm_combo.get_value() == ""


def test_what_the_page_stores_shows_on_settings_bot(qapp):
    dialog, page = _realm_page_dialog()
    page.realm_combo.set_value("Balestrom")
    page.delay_box.setValue(5.0)
    _next(dialog)

    tab = BotTab()
    assert tab.row("Favorite realm").control.get_value() == "Balestrom"
    assert tab.row("Default Safety Delay").control.value() == 5.0


def test_change_realm_uses_the_realm_the_page_stored(qapp):
    dialog, _page = _realm_page_dialog()
    _next(dialog)
    config = BotConfig(
        id="realm-bot",
        name="Realm Bot",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="change_realm", params={"use_favorite_realm": True})],
        transitions=[],
    )

    resolution = option_values.option_resolution(config, OPTION_FAVORITE_REALM)

    assert resolution.value == "Wraith"
    assert resolution.source == bot_options.SOURCE_APP_SETTING


def test_favorite_realms_own_default_is_still_unset(qapp):
    """Wraith is only the page's selection: a user who never runs setup has no favorite realm."""
    assert default_for("general", "favorite_realm") == ""
    assert SettingsManager.instance().favorite_realm == ""


def test_the_page_shows_the_bot_tabs_rows_with_every_description(qapp):
    page = RealmAndDelayPage()
    tab = BotTab()
    rows = page.findChildren(SettingRow)

    assert [row.title for row in rows] == ["Favorite realm", "Default Safety Delay"]
    for row in rows:
        assert row.summary.text() == tab.row(row.title).summary.text()
        assert row.details.text() == tab.row(row.title).details.text()
        assert row.details_shown, row.title
        assert row.help_button.isHidden(), row.title


def test_the_safety_delay_says_how_long_by_where_the_user_plays(qapp):
    page = RealmAndDelayPage()
    details = page.findChildren(SettingRow)[1].details.text()

    assert "3 to 5 seconds in North America" in details
    assert "5 to 10 elsewhere" in details


def test_the_page_is_asked_on_a_fresh_install(qapp):
    assert not RealmAndDelayPage().answered()


def test_a_realm_already_set_answers_the_page(qapp, prefs):
    SettingsManager.instance().favorite_realm = "Balestrom"
    page = RealmAndDelayPage()

    assert page.answered()
    assert page not in FirstRunDialog([CombatQuestionPage(prefs), page]).walk


def test_a_safety_delay_changed_alone_leaves_the_page_asked(qapp):
    """The realm is still unset, so the page has a question left."""
    SettingsManager.instance().default_safety_delay = 6.0

    assert not RealmAndDelayPage().answered()


def test_a_failed_realm_write_keeps_the_user_on_the_page(qapp, monkeypatch):
    dialog, page = _realm_page_dialog()

    def _fail():
        raise OSError("disk full")

    monkeypatch.setattr(SettingsManager.instance(), "save", _fail)
    _next(dialog)

    assert dialog.current is page
    assert dialog.error_shown
