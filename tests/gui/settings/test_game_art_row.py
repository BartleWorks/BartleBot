"""Settings › General › Game art, and the run it opens (epic #215, Stage 7, #232).

The run is real, against ``tests/art_fixture.py``'s made-up install; the async tests await
the dialog's own task, the way the app's loop would run it.
"""

from __future__ import annotations

import pytest
from bartleby.shared.game_install import set_install_dir
from bartleby.shared.preferences import PreferenceStore
from PyQt6.QtWidgets import QDialog

from bartlebot.game import art_extraction, card_art
from bartlebot.game.art_extraction import ExtractionError
from bartlebot.gui.art_signal import art_signal
from bartlebot.gui.settings.game_art_row import (
    EXTRACT_DETAILS,
    NOT_EXTRACTED,
    ArtExtraction,
    ArtExtractionDialog,
    game_art_row,
)
from bartlebot.gui.settings.general_tab import GeneralTab
from tests.art_fixture import game_install


@pytest.fixture(autouse=True)
def _fresh_art_index():
    card_art.forget_cache()
    yield
    card_art.forget_cache()


@pytest.fixture
def announced(qapp):
    """How many times ``art_changed`` has fired since the test began."""
    count = [0]

    def bump() -> None:
        count[0] += 1

    art_signal().art_changed.connect(bump)
    yield count
    art_signal().art_changed.disconnect(bump)


@pytest.fixture
def prefs(tmp_path) -> PreferenceStore:
    return PreferenceStore(tmp_path / "vendor")


@pytest.fixture
def game(tmp_path, prefs, monkeypatch):
    fixture = game_install(tmp_path / "game")
    set_install_dir(prefs, fixture.install)
    monkeypatch.setattr(art_extraction, "type_dump", lambda root=None: fixture.types)
    return fixture


def test_settings_general_has_the_row_under_game(qapp):
    tab = GeneralTab()

    assert tab.game_art_row.title == "Game art"
    assert tab.game_art_row.details.text() == EXTRACT_DETAILS


def test_with_no_art_the_row_says_so(qapp, prefs):
    row, _button = game_art_row(prefs)

    assert row.summary.text() == NOT_EXTRACTED
    assert not row.is_changed


def test_with_art_the_row_says_when_and_how_much(qapp, prefs, game):
    art_extraction.extract_art(game.game_data)

    row, _button = game_art_row(prefs)

    assert "2 pictures for 3 cards" in row.summary.text()
    assert "Extracted on" in row.summary.text()


async def test_the_dialog_extracts_and_close_is_ready_when_it_ends(qapp, prefs, game):
    dialog = ArtExtractionDialog(prefs)
    assert not dialog.close_button.isEnabled()

    await dialog.extraction.task

    assert dialog.extraction.ok
    assert dialog.close_button.isEnabled()
    assert card_art.is_available()
    dialog.close_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted


async def test_cancel_stops_the_run_and_keeps_the_art_before_it(qapp, prefs, game):
    art_extraction.extract_art(game.game_data)
    before = card_art.index_path().read_bytes()
    dialog = ArtExtractionDialog(prefs)

    dialog.cancel_button.click()
    await dialog.extraction.task

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert card_art.index_path().read_bytes() == before
    assert not card_art.art_dir().with_name("art.partial").exists()


async def test_a_run_starts_with_an_empty_bar(qapp, prefs, game):
    """Not Qt's busy bar, which the stylesheet draws half full before anything is counted."""
    extraction = ArtExtraction(prefs)

    assert extraction.start()

    bar = extraction.bar
    assert (bar.minimum(), bar.maximum(), bar.value()) == (0, 1, 0)
    await extraction.task


async def test_a_retry_after_a_failed_run_shows_its_steps_again(qapp, prefs, game, monkeypatch):
    """Setup's Back and Next tries a failed run again; its progress must not stay hidden."""
    real = art_extraction.extract_art

    def broken(_game_dir, _progress, *, cancelled):
        raise ExtractionError("The archive could not be read.")

    monkeypatch.setattr(art_extraction, "extract_art", broken)
    extraction = ArtExtraction(prefs)
    extraction.start()
    await extraction.task
    assert not extraction.step_shown

    monkeypatch.setattr(art_extraction, "extract_art", real)
    assert extraction.start()

    assert extraction.step_shown
    await extraction.task
    assert extraction.ok


async def test_a_failed_run_says_why_and_close_is_ready(qapp, prefs):
    dialog = ArtExtractionDialog(prefs)

    await dialog.extraction.task

    assert not dialog.extraction.ok
    assert "Wizard101 wasn't found" in dialog.extraction.outcome_text
    assert dialog.close_button.isEnabled()


# ---------------------------------------------------------------------------
# art_changed (#239)
# ---------------------------------------------------------------------------


async def test_a_run_that_writes_art_announces_it_once(qapp, prefs, game, announced):
    dialog = ArtExtractionDialog(prefs)

    await dialog.extraction.task

    assert dialog.extraction.ok
    assert announced[0] == 1


async def test_a_failed_run_announces_nothing(qapp, prefs, game, announced, monkeypatch):
    def broken(_game_dir, _progress, *, cancelled):
        raise ExtractionError("The archive could not be read.")

    monkeypatch.setattr(art_extraction, "extract_art", broken)
    extraction = ArtExtraction(prefs)
    extraction.start()
    await extraction.task

    assert announced[0] == 0


async def test_a_run_with_no_game_announces_nothing(qapp, prefs, announced):
    extraction = ArtExtraction(prefs)
    extraction.start()
    await extraction.task

    assert announced[0] == 0


async def test_a_stopped_run_announces_nothing(qapp, prefs, game, announced):
    dialog = ArtExtractionDialog(prefs)

    dialog.cancel_button.click()
    await dialog.extraction.task

    assert announced[0] == 0
