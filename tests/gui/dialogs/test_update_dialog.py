"""The update popup: what it shows, Restart now while a bot runs, and a download for each choice.

The async tests await the popup's own task, the way the app's loop would run it. The
download is a stand-in: preparing a real update needs an installed build.
"""

from __future__ import annotations

import threading

import pytest

from bartlebot.gui.dialogs.update_dialog import RESTART_BLOCKED, Choice, UpdateDialog
from bartlebot.updates.check import Offer, PreparedUpdate, UpdateError
from bartlebot.updates.feed import SignedFeed


def offer(notes: str = "") -> Offer:
    return Offer("1.2.0", "1.1.0", notes, SignedFeed(b"{}", "release", ()))


def prepared_for(record: list | None = None):
    def prepare(o: Offer, progress) -> PreparedUpdate:
        progress(5, 10)
        progress(10, 10)
        if record is not None:
            record.append(o.version)
        return PreparedUpdate(o.version, manager=object(), info=object())

    return prepare


def failing(o: Offer, progress) -> PreparedUpdate:
    raise UpdateError("The update did not pass Velopack's check: hash mismatch")


def test_building_the_dialog_never_shows_a_parentless_widget(qapp, parentless_shows):
    UpdateDialog(offer("## Fixed"), prepared_for())

    assert parentless_shows == []


def test_it_names_the_new_version_and_this_one(qapp):
    dialog = UpdateDialog(offer(), prepared_for())

    assert dialog.heading_text == "BartleBot 1.2.0 is available"
    assert dialog.current_text.startswith("You have 1.1.0.")


def test_the_feeds_notes_are_shown(qapp):
    dialog = UpdateDialog(offer("## Fixed\n\n- Foulgaze stops on the stairs"), prepared_for())

    assert "Foulgaze stops on the stairs" in dialog.notes.toPlainText()


def test_with_no_notes_it_points_at_the_release_page(qapp):
    dialog = UpdateDialog(offer(), prepared_for())

    assert "release page" in dialog.notes.toPlainText()
    assert "releases/tag/v1.2.0" in dialog.notes.toHtml()


def test_restart_now_is_disabled_while_a_bot_runs_and_says_why(qapp):
    dialog = UpdateDialog(offer(), prepared_for(), running=True)

    assert not dialog.restart_button.isEnabled()
    assert dialog.restart_button.toolTip() == RESTART_BLOCKED
    assert dialog.install_button.isEnabled(), "installing on close never stops the run"
    assert not dialog.choose(Choice.RESTART)


def test_restart_now_is_enabled_with_no_bot_running(qapp):
    assert UpdateDialog(offer(), prepared_for()).restart_button.isEnabled()


def test_enter_means_install_when_i_close(qapp):
    dialog = UpdateDialog(offer(), prepared_for())
    dialog.show()

    assert dialog.install_button.isDefault()
    assert not dialog.restart_button.isDefault()


@pytest.mark.parametrize("choice", [Choice.ON_CLOSE, Choice.RESTART])
async def test_a_choice_downloads_and_closes_once_velopack_has_it(qapp, choice):
    record = []
    dialog = UpdateDialog(offer(), prepared_for(record))

    assert dialog.choose(choice)
    assert not dialog.install_button.isEnabled()
    await dialog.task

    assert record == ["1.2.0"]
    assert dialog.choice is choice
    assert dialog.prepared.version == "1.2.0"
    assert dialog.result() == UpdateDialog.DialogCode.Accepted


async def test_a_failed_download_says_why_and_can_be_tried_again(qapp):
    dialog = UpdateDialog(offer(), failing)

    dialog.choose(Choice.ON_CLOSE)
    await dialog.task

    assert "hash mismatch" in dialog.status_text
    assert dialog.prepared is None
    assert dialog.choice is Choice.LATER
    assert dialog.install_button.isEnabled()
    assert dialog.restart_button.isEnabled()


async def test_not_now_during_a_download_drops_what_it_returns(qapp):
    release = threading.Event()

    def slow(o: Offer, progress) -> PreparedUpdate:
        release.wait(5)
        return PreparedUpdate(o.version, manager=object(), info=object())

    dialog = UpdateDialog(offer(), slow)
    dialog.choose(Choice.ON_CLOSE)
    dialog.not_now_button.click()
    release.set()

    assert dialog.choice is Choice.LATER
    assert dialog.prepared is None


def test_not_now_is_later(qapp):
    dialog = UpdateDialog(offer(), prepared_for())

    dialog.not_now_button.click()

    assert dialog.choice is Choice.LATER
    assert dialog.result() == UpdateDialog.DialogCode.Rejected


def test_without_a_loop_a_choice_starts_nothing(qapp):
    dialog = UpdateDialog(offer(), prepared_for())

    assert not dialog.choose(Choice.ON_CLOSE)
    assert dialog.install_button.isEnabled()
