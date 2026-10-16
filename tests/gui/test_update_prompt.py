"""When the update popup opens (gui/update_prompt.py): once per version, never over anything, never in a checkout."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from PyQt6.QtWidgets import QMessageBox, QWidget

from bartlebot.gui import update_prompt
from bartlebot.gui.dialogs.update_dialog import Choice, UpdateDialog
from bartlebot.gui.update_prompt import BUTTON_TEXT, UpdatePrompt
from bartlebot.settings_manager import SettingsManager
from bartlebot.updates.check import CheckResult, Offer, PreparedUpdate, Status
from bartlebot.updates.feed import SignedFeed


def offered(version: str) -> CheckResult:
    return CheckResult(Status.OFFERED, Offer(version, "1.1.0", "", SignedFeed(b"{}", "release", ())))


class Harness:
    """A prompt over stand-ins: what opened, what was applied, and the title bar's text."""

    def __init__(self, *, installed=True, check=None) -> None:
        self.window = QWidget()
        self.modal = False
        self.bot_running = False
        self.opened: list[UpdateDialog] = []
        self.applied: list[tuple[str, bool]] = []
        self.restarts = 0
        self.checks = 0
        self.button: list[str] = []
        self.told: list[str] = []
        self.check_fn = check or (lambda: offered("1.2.0"))
        self.prompt = UpdatePrompt(
            self.window,
            busy=lambda: self.modal,
            running=lambda: self.bot_running,
            restart=self._restart,
            check=self._count_check,
            prepare=lambda o, progress: PreparedUpdate(o.version, object(), object()),
            apply=lambda prepared, restart: self.applied.append((prepared.version, restart)),
            installed=lambda: installed,
        )
        self.prompt.button_changed.connect(self.button.append)

    def _count_check(self) -> CheckResult:
        self.checks += 1
        return self.check_fn()

    def _restart(self) -> None:
        self.restarts += 1


@pytest.fixture
def harness(qapp, monkeypatch):
    """A prompt whose popup records itself and ends on *Not now*."""
    monkeypatch.setattr(update_prompt, "RETRY_MS", 1)
    made = Harness()

    def exec_(dialog: UpdateDialog) -> int:
        made.opened.append(dialog)
        return 0

    monkeypatch.setattr(UpdateDialog, "exec", exec_)
    monkeypatch.setattr(QMessageBox, "information", lambda _parent, _title, text: made.told.append(text))
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, text: made.told.append(text))
    return made


def _settle(qtbot, harness, count: int) -> None:
    qtbot.waitUntil(lambda: len(harness.opened) >= count, timeout=2000)


# -- when it checks -----------------------------------------------------------------


async def test_a_checkout_never_checks(qapp):
    """The real ``installed``: a checkout is not a build Velopack installed."""
    made = UpdatePrompt(QWidget(), busy=lambda: False, running=lambda: False, restart=lambda: None)

    assert not made.can_check
    assert not made.check_at_startup()
    assert not made.check_now()
    assert made.task is None


async def test_with_the_setting_off_it_never_checks_at_startup(harness):
    SettingsManager.instance().check_for_updates_at_startup = False

    assert not harness.prompt.check_at_startup()
    assert harness.prompt.task is None
    assert harness.checks == 0


async def test_the_check_never_delays_the_window(qapp, harness):
    """It runs in a worker thread: starting it returns at once, however long the network takes."""
    release = threading.Event()
    harness.check_fn = lambda: (release.wait(5), CheckResult(Status.UP_TO_DATE))[1]

    began = time.monotonic()
    assert harness.prompt.check_at_startup()
    assert time.monotonic() - began < 0.5
    release.set()
    await harness.prompt.task
    assert harness.checks == 1


async def test_help_checks_whatever_the_setting(harness):
    SettingsManager.instance().check_for_updates_at_startup = False

    assert harness.prompt.check_now()
    await harness.prompt.task
    assert harness.checks == 1


# -- once per version ----------------------------------------------------------------


def test_a_newer_version_opens_the_popup_once_and_records_it(harness, qtbot):
    harness.prompt.take_result(offered("1.2.0"))
    _settle(qtbot, harness, 1)

    assert SettingsManager.instance().last_offered_update == "1.2.0"
    harness.prompt.take_result(offered("1.2.0"))
    qtbot.wait(20)
    assert len(harness.opened) == 1


def test_after_not_now_the_title_bar_names_it_and_opens_it_again(harness, qtbot):
    harness.prompt.take_result(offered("1.2.0"))
    _settle(qtbot, harness, 1)
    harness.prompt.finish(Choice.LATER, None)

    assert harness.button[-1] == BUTTON_TEXT.format(version="1.2.0") == "Update 1.2.0"
    harness.prompt.open_offer()
    assert len(harness.opened) == 2


def test_the_next_launch_shows_the_button_and_not_the_popup_for_the_same_version(harness, qtbot):
    SettingsManager.instance().last_offered_update = "1.2.0"

    harness.prompt.take_result(offered("1.2.0"))
    qtbot.wait(20)

    assert harness.opened == []
    assert harness.button == ["Update 1.2.0"]


def test_a_newer_version_than_the_one_put_off_opens_the_popup_again(harness, qtbot):
    SettingsManager.instance().last_offered_update = "1.2.0"

    harness.prompt.take_result(offered("1.3.0"))
    _settle(qtbot, harness, 1)

    assert harness.opened[0].offer.version == "1.3.0"
    assert SettingsManager.instance().last_offered_update == "1.3.0"


def test_reopening_an_older_offer_never_moves_last_offered_back(harness, qtbot):
    SettingsManager.instance().last_offered_update = "1.3.0"
    harness.prompt.take_result(offered("1.2.0"), manual=True)
    _settle(qtbot, harness, 1)

    assert SettingsManager.instance().last_offered_update == "1.3.0"


# -- never over anything ------------------------------------------------------------


@pytest.mark.parametrize("held_by", ["modal", "bot_running"])
def test_the_popup_waits_for_a_dialog_or_a_running_bot(harness, qtbot, held_by):
    """First-run setup and a launch-time import's summary are both modal; a run is its own."""
    setattr(harness, held_by, True)
    harness.prompt.take_result(offered("1.2.0"))
    qtbot.wait(30)
    assert harness.opened == []

    setattr(harness, held_by, False)
    _settle(qtbot, harness, 1)
    assert len(harness.opened) == 1


def test_opened_while_a_bot_runs_restart_now_is_disabled(harness):
    harness.bot_running = True
    harness.prompt.take_result(offered("1.2.0"), manual=False)
    harness.prompt.open_offer()

    assert not harness.opened[-1].restart_button.isEnabled()


# -- what each choice leaves --------------------------------------------------------


def test_install_when_i_close_hands_it_over_and_hides_the_button(harness):
    harness.prompt.take_result(offered("1.2.0"))

    harness.prompt.finish(Choice.ON_CLOSE, PreparedUpdate("1.2.0", object(), object()))

    assert harness.applied == [("1.2.0", False)]
    assert harness.button[-1] == ""
    assert harness.restarts == 0
    assert harness.prompt.pending_version == "1.2.0"


def test_an_update_chosen_during_a_run_waits_for_the_app_to_close(harness):
    """Velopack's updater waits for the process to exit; the run carries on until then."""
    harness.bot_running = True
    harness.prompt.take_result(offered("1.2.0"))

    harness.prompt.finish(Choice.ON_CLOSE, PreparedUpdate("1.2.0", object(), object()))

    assert harness.applied == [("1.2.0", False)]
    assert harness.restarts == 0


def test_restart_now_closes_the_app_the_usual_way(harness):
    harness.prompt.take_result(offered("1.2.0"))

    harness.prompt.finish(Choice.RESTART, PreparedUpdate("1.2.0", object(), object()))

    assert harness.applied == [("1.2.0", True)]
    assert harness.restarts == 1


def test_once_an_update_waits_for_close_the_button_and_help_say_so(harness, qtbot):
    harness.prompt.take_result(offered("1.2.0"))
    harness.prompt.finish(Choice.ON_CLOSE, PreparedUpdate("1.2.0", object(), object()))
    opened = len(harness.opened)

    harness.prompt.open_offer()
    assert len(harness.opened) == opened
    assert not harness.prompt.check_now()
    qtbot.waitUntil(lambda: bool(harness.told), timeout=1000)
    assert "installs when you close" in harness.told[-1]


def test_velopack_refusing_it_keeps_the_button(harness):
    def refuse(prepared, restart):
        raise RuntimeError("Update.exe is missing")

    harness.prompt._apply = refuse  # noqa: SLF001 - one stand-in swapped for another
    harness.prompt.take_result(offered("1.2.0"))
    harness.prompt.finish(Choice.ON_CLOSE, PreparedUpdate("1.2.0", object(), object()))

    assert harness.button[-1] == "Update 1.2.0"
    assert harness.prompt.pending_version is None
    assert "Update.exe is missing" in harness.told[-1]


# -- a manual check's answer --------------------------------------------------------


@pytest.mark.parametrize(
    ("result", "says"),
    [
        (CheckResult(Status.UP_TO_DATE), "is up to date"),
        (CheckResult(Status.UNREACHABLE, reason="releases.win.json could not be read."), "Couldn't check"),
        (CheckResult(Status.REFUSED, reason="no matching key."), "signature doesn't match"),
    ],
)
def test_a_manual_check_always_answers(harness, qtbot, result, says):
    harness.prompt.take_result(result, manual=True)

    qtbot.waitUntil(lambda: bool(harness.told), timeout=1000)
    assert says in harness.told[-1]


def test_a_startup_check_with_nothing_new_says_nothing(harness, qtbot):
    for status in (Status.UP_TO_DATE, Status.UNREACHABLE, Status.REFUSED):
        harness.prompt.take_result(CheckResult(status))
    qtbot.wait(30)

    assert harness.told == []
    assert harness.opened == []


async def test_a_check_that_raises_is_logged_and_shows_nothing(harness, caplog):
    def broken():
        raise OSError("disk on fire")

    harness.check_fn = broken
    harness.prompt.check_at_startup()
    await harness.prompt.task
    await asyncio.sleep(0)

    assert harness.opened == []
    assert "update check failed" in caplog.text
