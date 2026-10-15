"""Setup's Options page, for Custom, and its Card art page (epic #215, Stage 7, #232).

Driven by the dialog's buttons, never by ``exec()`` (``tests/conftest.py``). The card art
runs are real: ``tests/art_fixture.py`` builds an install of synthetic archives, and the
async tests await the page's own task, the way the app's loop would run it.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QDialog

from bartlebot.game import art_extraction, card_art
from bartlebot.game.art_extraction import ExtractionError
from bartlebot.gui.dialogs.first_run_dialog import (
    CardArtPage,
    CombatQuestionPage,
    FirstRunDialog,
    OptionsPage,
    QuicksellPage,
    RealmAndDelayPage,
    SetupMode,
    needs_asking,
)
from bartlebot.gui.settings.bot_tab import BotTab
from bartlebot.gui.settings.card_art_row import NO_GAME
from bartlebot.gui.settings.rows import SettingRow
from bartlebot.options.custom_setup import (
    ART_ANSWERED_KEY,
    EXTRACT_ART_KEY,
    art_answered,
    options_answered,
)
from bartlebot.settings_manager import SettingsManager
from bartlebot.shared.game_install import set_install_dir
from bartlebot.shared.preferences import PreferenceStore
from tests.art_fixture import game_install


@pytest.fixture(autouse=True)
def _fresh_art_index():
    """The art index is cached per process, and each test has its own ``%APPDATA%``."""
    card_art.forget_cache()
    yield
    card_art.forget_cache()


@pytest.fixture
def prefs(tmp_path: Path) -> PreferenceStore:
    return PreferenceStore(tmp_path / "vendor")


@pytest.fixture
def game(tmp_path, prefs, monkeypatch):
    """A made-up install, stored as the game folder, with its own type list."""
    fixture = game_install(tmp_path / "game")
    set_install_dir(prefs, fixture.install)
    monkeypatch.setattr(art_extraction, "type_dump", lambda root=None: fixture.types)
    return fixture


def _pages(prefs: PreferenceStore) -> tuple[OptionsPage, CardArtPage]:
    options = OptionsPage()
    return options, CardArtPage(options, prefs)


def _dialog(prefs: PreferenceStore, mode: SetupMode = SetupMode.QUICK, *, rerun: bool = False):
    options, art = _pages(prefs)
    dialog = FirstRunDialog([RealmAndDelayPage(), options, art], rerun=rerun)
    dialog.mode_page.select(mode)
    return dialog, options, art


def _next(dialog: FirstRunDialog, times: int = 1) -> None:
    for _ in range(times):
        dialog.commit_button.click()


# -- which pages follow -------------------------------------------------------------


def test_quick_ends_on_the_card_art_page_and_shows_no_options(qapp, prefs):
    dialog, options, art = _dialog(prefs)

    assert options not in dialog.walk
    assert dialog.walk[-1] is art


def test_custom_shows_the_options_then_the_card_art_page(qapp, prefs):
    dialog, options, art = _dialog(prefs, SetupMode.CUSTOM)

    assert dialog.walk[-2:] == [options, art]


def test_custom_with_extract_card_art_off_has_no_art_page(qapp, prefs):
    dialog, options, art = _dialog(prefs, SetupMode.CUSTOM)
    _next(dialog, 2)
    assert dialog.current is options

    options.art_box.setChecked(False)

    assert art not in dialog.walk
    assert dialog.commit_button.text() == "Finish"
    _next(dialog)
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_custom_with_extract_card_art_off_keeps_setup_closed_next_launch(qapp, prefs):
    """The Card art page applies under Quick whatever Custom chose, so only its answer keeps it shut."""
    dialog, options, _art = _dialog(prefs, SetupMode.CUSTOM)
    _next(dialog, 2)
    options.art_box.setChecked(False)
    _next(dialog)
    assert dialog.result() == QDialog.DialogCode.Accepted

    options, art = _pages(prefs)

    assert art.answered()
    assert not needs_asking([RealmAndDelayPage(), options, art])


def test_the_options_page_alone_never_opens_setup(qapp, prefs):
    """Its choices are Quick's: opened for it, setup would end on Quick or Custom, and open again next launch."""
    options, art = _pages(prefs)
    SettingsManager.instance().set_general(ART_ANSWERED_KEY, True)

    assert not options.answered()
    assert not needs_asking([options, art])
    assert needs_asking([options, art, QuicksellPage()])


# -- the Options page ---------------------------------------------------------------


def test_the_options_page_is_three_rows_all_on_every_description_shown(qapp):
    page = OptionsPage()
    rows = page.findChildren(SettingRow)

    assert [row.title for row in rows] == ["Extract card art", "Automatically group players", "Refill potions"]
    assert all(row.control.isChecked() for row in rows)
    for row in rows:
        assert row.details_shown, row.title
        assert row.help_button.isHidden(), row.title


def test_grouping_and_refills_are_the_bot_tabs_rows(qapp):
    page = OptionsPage()
    tab = BotTab()

    for row in page.findChildren(SettingRow)[1:]:
        assert row.summary.text() == tab.row(row.title).summary.text()
        assert row.details.text() == tab.row(row.title).details.text()


def test_each_option_says_what_it_costs(qapp):
    page = OptionsPage()
    details = {row.title: row.details.text() for row in page.findChildren(SettingRow)}

    assert "35 MB" in details["Extract card art"]
    assert "friends lists" in details["Automatically group players"]
    assert "gold" in details["Refill potions"]


def test_custom_writes_the_settings_the_bot_tab_shows(qapp, prefs):
    dialog, options, _art = _dialog(prefs, SetupMode.CUSTOM)
    _next(dialog, 2)
    options.group_box.setChecked(False)
    options.refill_box.setChecked(False)
    options.art_box.setChecked(False)
    _next(dialog)

    sm = SettingsManager.instance()
    assert (sm.auto_group_players, sm.get_general("default_refill_potions")) == (False, False)
    assert sm.get_general(EXTRACT_ART_KEY) is False
    tab = BotTab()
    assert not tab.row("Automatically group players").control.isChecked()
    assert not tab.row("Refill potions").control.isChecked()
    assert options_answered(sm)


def test_a_rerun_opens_the_options_on_the_stored_choices(qapp, prefs):
    sm = SettingsManager.instance()
    sm.auto_group_players = False
    sm.set_general(EXTRACT_ART_KEY, False)

    page = OptionsPage()

    assert (page.art_box.isChecked(), page.group_box.isChecked(), page.refill_box.isChecked()) == (False, False, True)


def test_quick_leaves_grouping_and_refills_as_they_are(qapp, prefs):
    """Quick writes neither: on a first run they stay at their defaults."""
    dialog, _options, art = _dialog(prefs)
    _next(dialog, 2)
    assert dialog.current is art

    sm = SettingsManager.instance()
    assert sm.auto_group_players is True
    assert sm.get_general("default_refill_potions") is True
    assert sm.get_general(EXTRACT_ART_KEY) is None


def test_a_failed_options_write_keeps_the_user_on_the_page(qapp, prefs, monkeypatch):
    dialog, options, _art = _dialog(prefs, SetupMode.CUSTOM)
    _next(dialog, 2)

    def _fail():
        raise OSError("disk full")

    monkeypatch.setattr(SettingsManager.instance(), "save", _fail)
    _next(dialog)

    assert dialog.current is options
    assert dialog.error_shown


# -- the Card art page --------------------------------------------------------------


def test_the_art_page_is_asked_until_finished_or_art_exists(qapp, prefs, game):
    _options, art = _pages(prefs)
    assert not art.answered()

    art_extraction.extract_art(game.game_data)

    assert art.answered()


def test_finish_waits_while_nothing_has_run(qapp, prefs):
    """Without a running loop the page starts nothing, and Finish stays disabled."""
    dialog, _options, art = _dialog(prefs)
    _next(dialog, 2)

    assert dialog.current is art
    assert not art.extraction.running
    assert not dialog.commit_button.isEnabled()


async def test_quick_extracts_the_art_on_the_last_page(qapp, prefs, game):
    dialog, _options, art = _dialog(prefs)
    _next(dialog, 2)
    assert dialog.current is art
    assert not dialog.commit_button.isEnabled()

    await art.extraction.task

    assert art.extraction.ok
    assert dialog.commit_button.isEnabled()
    assert card_art.art_path("Fixture Bolt") is not None
    assert "2 pictures for 3 cards" in art.extraction.outcome_text
    _next(dialog)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert SettingsManager.instance().get_general(ART_ANSWERED_KEY) is True


async def test_custom_with_art_on_extracts_after_the_options(qapp, prefs, game):
    dialog, _options, art = _dialog(prefs, SetupMode.CUSTOM)
    _next(dialog, 3)
    assert dialog.current is art

    await art.extraction.task

    assert art.extraction.ok


async def test_closing_during_extraction_leaves_nothing_half_written_and_the_page_unanswered(
    qapp, prefs, game, monkeypatch
):
    real = art_extraction.extract_art
    reached = threading.Event()

    def held(game_dir, progress, *, cancelled):
        calls = 0

        def gate() -> bool:
            # The third check is inside the spell loop, with the partial folder written to.
            nonlocal calls
            calls += 1
            if calls == 3:
                reached.set()
                deadline = time.monotonic() + 10
                while not cancelled() and time.monotonic() < deadline:
                    time.sleep(0.005)
            return cancelled()

        return real(game_dir, progress, cancelled=gate)

    monkeypatch.setattr(art_extraction, "extract_art", held)
    dialog, _options, art = _dialog(prefs)
    _next(dialog, 2)
    assert dialog.current is art
    deadline = time.monotonic() + 10
    while not reached.is_set():
        assert time.monotonic() < deadline, "the run never reached the spell loop"
        await asyncio.sleep(0.005)
    partial = card_art.art_dir().with_name("art.partial")
    assert (partial / "spells").is_dir()

    dialog.reject()
    await art.extraction.task

    assert not partial.exists()
    assert not card_art.art_dir().exists()
    assert not art_answered(SettingsManager.instance())
    assert not art.extraction.ended


async def test_a_failed_extraction_ends_setup_normally_with_the_reason_shown_and_logged(
    qapp, prefs, game, monkeypatch, caplog
):
    def broken(_game_dir, _progress, *, cancelled):
        raise ExtractionError("The archive could not be read.")

    monkeypatch.setattr(art_extraction, "extract_art", broken)
    dialog, _options, art = _dialog(prefs)
    _next(dialog, 2)
    with caplog.at_level(logging.WARNING):
        await art.extraction.task

    assert not art.extraction.ok
    assert "The archive could not be read." in art.extraction.outcome_text
    assert "The archive could not be read." in caplog.text
    assert dialog.commit_button.isEnabled()
    _next(dialog)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert art_answered(SettingsManager.instance())


async def test_an_unexpected_error_is_shown_and_logged_with_its_trace(qapp, prefs, game, monkeypatch, caplog):
    def broken(_game_dir, _progress, *, cancelled):
        raise OSError("the drive went away")

    monkeypatch.setattr(art_extraction, "extract_art", broken)
    dialog, _options, art = _dialog(prefs)
    _next(dialog, 2)
    with caplog.at_level(logging.WARNING):
        await art.extraction.task

    assert "the drive went away" in art.extraction.outcome_text
    assert any(record.exc_info for record in caplog.records)
    assert dialog.commit_button.isEnabled()


async def test_with_no_game_found_the_page_says_so_and_finish_is_ready(qapp, prefs):
    dialog, _options, art = _dialog(prefs)
    _next(dialog, 2)
    await art.extraction.task

    assert NO_GAME in art.extraction.outcome_text
    assert dialog.commit_button.isEnabled()


def test_a_first_run_with_art_already_extracted_skips_the_page(qapp, prefs, game):
    art_extraction.extract_art(game.game_data)
    options, art = _pages(prefs)

    dialog = FirstRunDialog([CombatQuestionPage(prefs), options, art])

    assert art not in dialog.walk
