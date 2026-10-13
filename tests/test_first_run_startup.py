"""When the app asks the first-run questions (epic #201, Stage 6).

``AppController``'s two startup methods, bound to a stand-in as the other controller tests
do, over the test's private ``%APPDATA%``. ``FirstRunDialog.exec`` is answered here, per
test, in place of the guard ``tests/conftest.py`` puts on it for the rest of the suite.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QDialog, QWidget

from bartlebot.app_controller import AppController
from bartlebot.gui.dialogs.first_run_dialog import CombatQuestionPage, FirstRunDialog
from bartlebot.shared.combat_defaults import PREFERENCE_KEYS, SHIPPED_EVERYONE_ATTACKS_DUEL_ID, TeamSize
from bartlebot.shared.first_run import BossTactic, combat_question_answered, record_answer
from bartlebot.shared.preferences import PreferenceStore

_TEAM = PREFERENCE_KEYS[TeamSize.TEAM]


class _Window(QWidget):
    """The main window as far as first run reaches: a parent, and the Combat tab to refresh."""

    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.left_sidebar = SimpleNamespace(combat_tab=SimpleNamespace(refresh_source=lambda: events.append("refresh")))


@pytest.fixture
def startup(qapp, monkeypatch):
    """The controller's startup methods, the dialogs they open, and what happened in order."""
    state = SimpleNamespace(events=[], opened=[], answer=None)
    ns = SimpleNamespace(_window=_Window(state.events))
    for name in ("_after_window_shown", "_offer_first_run"):
        setattr(ns, name, getattr(AppController, name).__get__(ns))
    ns._import_from_arguments = lambda: state.events.append("import")

    def answer(dialog: FirstRunDialog) -> int:
        """Continue on *state.answer*, or Skip for None."""
        state.events.append("first run")
        state.opened.append(dialog)
        if state.answer is None:
            dialog.skip_button.click()
        else:
            dialog.pages[0].select(state.answer)
            dialog.commit_button.click()
        return dialog.result()

    monkeypatch.setattr(FirstRunDialog, "exec", answer)
    state.ns = ns
    return state


def _prefs() -> PreferenceStore:
    """The store the app reads: the vendor root under this test's ``%APPDATA%``."""
    return PreferenceStore()


def test_it_opens_on_a_first_run_with_the_combat_question(startup):
    startup.ns._after_window_shown()

    assert len(startup.opened) == 1
    (page,) = startup.opened[0].pages
    assert isinstance(page, CombatQuestionPage)


def test_it_opens_once_and_never_after_continue(startup):
    startup.answer = BossTactic.EVERYONE_ATTACKS

    startup.ns._after_window_shown()
    startup.ns._after_window_shown()

    assert len(startup.opened) == 1
    assert _prefs().get(_TEAM) == SHIPPED_EVERYONE_ATTACKS_DUEL_ID


def test_it_never_opens_after_skip(startup):
    startup.ns._after_window_shown()
    startup.ns._after_window_shown()

    assert len(startup.opened) == 1
    assert startup.opened[0].result() == QDialog.DialogCode.Rejected
    assert combat_question_answered(_prefs())
    assert _prefs().get(_TEAM) is None


def test_the_answer_recorded_by_the_other_product_keeps_it_closed(startup):
    """BartleBuddy answered first: same file, same key."""
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

    assert startup.opened[0].pages[0].tactic is BossTactic.EVERYONE_ATTACKS


def test_it_comes_before_a_file_named_at_launch_and_refreshes_the_combat_tab(startup):
    """Never two modals at once: the import's summary waits for first run to close."""
    startup.ns._after_window_shown()

    assert startup.events == ["first run", "refresh", "import"]


def test_a_failed_write_is_logged_and_asks_again_next_time(startup, monkeypatch, caplog):
    monkeypatch.setattr(PreferenceStore, "set", lambda self, key, value: False)

    startup.ns._offer_first_run()
    startup.ns._offer_first_run()

    assert len(startup.opened) == 2
    assert "first-run answers" in caplog.text
