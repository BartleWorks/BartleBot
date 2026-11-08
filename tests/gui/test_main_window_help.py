"""The Help menu, and F1 on the main window (epic #25, Stage 5)."""

from __future__ import annotations

import os

import pytest
from bartleby.qt.game_embed.game_view import GameView
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMenu

from bartlebot.gui import context_help
from bartlebot.gui.canvas.bot_canvas import BotCanvas, BotMode
from bartlebot.packaging.help_topics import REPORT_PROBLEM_URL, TROUBLESHOOTING, help_url, page_url
from bartlebot.paths import project_root


@pytest.fixture
def opened(monkeypatch) -> list[str]:
    urls: list[str] = []
    monkeypatch.setattr(
        context_help, "open_url", lambda url: urls.append(url.toString() if isinstance(url, QUrl) else url)
    )
    return urls


@pytest.fixture
def window(qapp):
    from bartlebot.gui.main_window import MainWindow  # noqa: PLC0415

    window = MainWindow()
    yield window
    window.bot_canvas.prepare_for_shutdown()
    window.deleteLater()


def _help_menu(window) -> QMenu:
    return next(m for m in window.action_check_updates.associatedObjects() if isinstance(m, QMenu))


def test_the_help_menu_reaches_the_guide_troubleshooting_the_logs_and_the_issues_above_updates(window):
    entries = [a.text() for a in _help_menu(window).actions()]

    assert entries == [
        "User Guide\tF1",
        "Troubleshooting",
        "Open Logs Folder",
        "Report a Problem",
        "",
        "Check for Updates…",
    ]


def test_f1_on_the_window_opens_running_a_bot_in_run_mode_and_the_canvas_in_edit_mode(window, monkeypatch):
    assert window.bot_canvas.mode == BotMode.RUN
    assert window.context_help_url() == help_url("run_mode")

    monkeypatch.setattr(BotCanvas, "mode", property(lambda _self: BotMode.EDIT))
    assert window.context_help_url() == help_url("edit_mode")


def test_f1_from_the_canvas_and_user_guide_open_the_same_page(window, opened):
    QTest.keyClick(window.bot_canvas, Qt.Key.Key_F1)
    window.action_user_guide.trigger()

    assert opened == [help_url("run_mode"), help_url("run_mode")]


def test_f1_while_a_game_window_has_the_keyboard_opens_nothing(window, opened, monkeypatch):
    monkeypatch.setattr(GameView, "focused_hwnd", property(lambda _self: 0x1234))

    QTest.keyClick(window.bot_canvas, Qt.Key.Key_F1)

    assert opened == []


def test_troubleshooting_and_report_a_problem_open_their_pages(window, opened):
    window.action_troubleshooting.trigger()
    window.action_report_problem.trigger()

    assert opened == [page_url(TROUBLESHOOTING), REPORT_PROBLEM_URL]


def test_open_logs_folder_opens_the_folder_troubleshooting_names(window, opened, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("BARTLEBOT_DEV", raising=False)
    named = (project_root() / "site" / "pages" / TROUBLESHOOTING).read_text(encoding="utf-8")
    assert "%APPDATA%\\Bartleby\\BartleBot\\logs" in named

    window.action_open_logs.trigger()

    folder = tmp_path / "Bartleby" / "BartleBot" / "logs"
    assert opened == [QUrl.fromLocalFile(str(folder)).toString()]
    assert folder.is_dir()
    assert os.path.samefile(QUrl(opened[0]).toLocalFile(), folder)
