"""When the app opens setup (epic #215, Stage 1).

``AppController``'s startup methods and File › Set up BartleBot…, bound to a stand-in as
the other controller tests do, over the test's private ``%APPDATA%``.
``FirstRunDialog.exec`` is answered here, per test, in place of the guard
``tests/conftest.py`` puts on it for the rest of the suite.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QMenu, QWidget

from bartlebot.app_controller import AppController
from bartlebot.gui.dialogs.first_run_dialog import CombatQuestionPage, FirstRunDialog
from bartlebot.shared.combat_defaults import PREFERENCE_KEYS, SHIPPED_EVERYONE_ATTACKS_DUEL_ID, TeamSize
from bartlebot.shared.first_run import BossTactic, combat_question_answered, record_answer
from bartlebot.shared.preferences import PreferenceStore

_TEAM = PREFERENCE_KEYS[TeamSize.TEAM]


class _Window(QWidget):
    """The main window as far as setup reaches: a parent, and the Combat tab to refresh."""

    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.left_sidebar = SimpleNamespace(combat_tab=SimpleNamespace(refresh_source=lambda: events.append("refresh")))


@pytest.fixture
def startup(qapp, monkeypatch):
    """The controller's setup methods, the dialogs they open, and what happened in order.

    ``state.answer`` is the combat answer to finish on; None closes the dialog on the
    combat page instead.
    """
    state = SimpleNamespace(events=[], opened=[], answer=None)
    ns = SimpleNamespace(_window=_Window(state.events))
    for name in ("_after_window_shown", "_offer_first_run", "_rerun_setup", "_open_setup"):
        setattr(ns, name, getattr(AppController, name).__get__(ns))
    ns._import_from_arguments = lambda: state.events.append("import")

    def answer(dialog: FirstRunDialog) -> int:
        state.events.append("first run")
        state.opened.append(dialog)
        dialog.commit_button.click()  # Quick
        if state.answer is None:
            dialog.reject()
        else:
            dialog.current.select(state.answer)
            dialog.commit_button.click()
        return dialog.result()

    monkeypatch.setattr(FirstRunDialog, "exec", answer)
    state.ns = ns
    return state


def _prefs() -> PreferenceStore:
    """The store the app reads: the vendor root under this test's ``%APPDATA%``."""
    return PreferenceStore()


def _walked(dialog: FirstRunDialog) -> list[type]:
    return [type(page) for page in dialog.walk[1:]]


def test_it_opens_on_a_first_run_with_the_combat_question(startup):
    startup.ns._after_window_shown()

    assert len(startup.opened) == 1
    assert not startup.opened[0].rerun
    assert _walked(startup.opened[0]) == [CombatQuestionPage]


def test_it_opens_once_and_never_after_finish(startup):
    startup.answer = BossTactic.EVERYONE_ATTACKS

    startup.ns._after_window_shown()
    startup.ns._after_window_shown()

    assert len(startup.opened) == 1
    assert _prefs().get(_TEAM) == SHIPPED_EVERYONE_ATTACKS_DUEL_ID


def test_closing_it_unanswered_asks_again_next_launch(startup):
    startup.ns._after_window_shown()
    startup.ns._after_window_shown()

    assert len(startup.opened) == 2
    assert not combat_question_answered(_prefs())
    assert _prefs().get(_TEAM) is None


def test_the_answer_recorded_by_the_other_product_keeps_it_closed(startup):
    """BartleBuddy, or #209, answered first: same file, same key."""
    record_answer(PreferenceStore(_prefs().path.parent), None)

    startup.ns._after_window_shown()

    assert startup.opened == []
    assert startup.events == ["import"]


def test_a_team_default_chosen_elsewhere_keeps_it_closed(startup):
    _prefs().set(_TEAM, "00000000-0000-0000-0000-0000000000d1")

    startup.ns._after_window_shown()

    assert startup.opened == []


def test_it_opens_on_the_answer_the_team_default_already_is(startup):
    _prefs().set(_TEAM, SHIPPED_EVERYONE_ATTACKS_DUEL_ID)
    startup.answer = BossTactic.EVERYONE_ATTACKS

    startup.ns._offer_first_run()

    assert startup.opened[0].pages[1].tactic is BossTactic.EVERYONE_ATTACKS


def test_it_comes_before_a_file_named_at_launch_and_refreshes_the_combat_tab(startup):
    """Never two modals at once: the import's summary waits for setup to close."""
    startup.ns._after_window_shown()

    assert startup.events == ["first run", "refresh", "import"]


def test_set_up_again_shows_every_page_once_all_are_answered(startup):
    record_answer(_prefs(), BossTactic.EVERYONE_ATTACKS)
    startup.answer = BossTactic.ONE_HITTER

    startup.ns._rerun_setup()

    assert startup.opened[0].rerun
    assert _walked(startup.opened[0]) == [CombatQuestionPage]
    assert _prefs().get(_TEAM) is None
    assert startup.events == ["first run", "refresh"]


def test_the_file_menu_offers_set_up_after_settings(qapp):
    from bartlebot.gui.main_window import MainWindow  # noqa: PLC0415

    window = MainWindow()
    try:
        file_menu = next(menu for menu in window.findChildren(QMenu) if menu.title() == "File")
        texts = [action.text() for action in file_menu.actions()]
        assert texts[texts.index("Settings…") + 1] == "Set up BartleBot…"
        assert window.action_setup.text() == "Set up BartleBot…"
    finally:
        window.bot_canvas.prepare_for_shutdown()
