"""The wiki import dialog's fetch: run off the GUI thread by ``asyncio.to_thread``, never a ``QThread``.

The parsing is in ``test_wiki_import_parsing.py``; these cover what the dialog does with
a fetch's result, its failure, and a close while one is under way.
"""

from __future__ import annotations

import ast
import asyncio
import threading

import pytest

from bartlebot.game.wiki_pages import WIKI_HOST
from bartlebot.gui.dialogs import wiki_import_dialog
from bartlebot.gui.dialogs.wiki_import_dialog import WikiImportDialog
from bartlebot.paths import project_root

URL = f"https://{WIKI_HOST}/wiki/Creature:Lost_Soul"


def _start(dlg: WikiImportDialog) -> asyncio.Task:
    dlg._url_edit.setText(URL)
    dlg._on_fetch()
    assert dlg._task is not None
    return dlg._task


async def test_a_fetch_fills_the_tree_and_counts_what_it_found(qapp, monkeypatch):
    fetched_on = []

    def fetch(url):
        fetched_on.append(threading.current_thread())
        return {"Hats": ["Cap of Doom", "Hood"], "Rings": ["Band"]}

    monkeypatch.setattr(wiki_import_dialog, "_fetch_drops", fetch)
    dlg = WikiImportDialog()

    await _start(dlg)

    assert fetched_on and fetched_on[0] is not threading.main_thread()
    assert dlg._tree.topLevelItemCount() == 2
    assert dlg._status_lbl.text() == "Found 3 item(s) across 2 categories."
    assert dlg._ok_btn.isEnabled()
    assert dlg._btn_fetch.isEnabled()


async def test_a_failed_fetch_says_why_and_lets_the_user_try_again(qapp, monkeypatch):
    def fetch(url):
        raise OSError("connection reset")

    monkeypatch.setattr(wiki_import_dialog, "_fetch_drops", fetch)
    dlg = WikiImportDialog()

    await _start(dlg)

    assert dlg._status_lbl.text() == "Error: connection reset"
    assert dlg._status_error
    assert dlg._btn_fetch.isEnabled()
    assert dlg._tree.topLevelItemCount() == 0


async def test_closing_during_a_fetch_shows_nothing_it_returns(qapp, monkeypatch):
    release = threading.Event()

    def fetch(url):
        release.wait(5)
        return {"Hats": ["Hood"]}

    monkeypatch.setattr(wiki_import_dialog, "_fetch_drops", fetch)
    dlg = WikiImportDialog()
    task = _start(dlg)
    await asyncio.sleep(0)

    dlg.close()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert dlg._tree.topLevelItemCount() == 0
    assert dlg._status_lbl.text() == "Fetching…"


def _names_qthread(source: str) -> bool:
    """Whether the code imports or names ``QThread``; a docstring saying why it doesn't is fine."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.alias) and node.name.split(".")[-1] == "QThread":
            return True
        if isinstance(node, ast.Name) and node.id == "QThread":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "QThread":
            return True
    return False


def test_no_qthread_remains_in_bartlebot():
    # Nuitka documents Qt threading as unsupported under PyQt6 (docs/RELEASING.md).
    users = [
        path.relative_to(project_root()).as_posix()
        for path in (project_root() / "bartlebot").rglob("*.py")
        if _names_qthread(path.read_text(encoding="utf-8"))
    ]
    assert users == []


def test_the_check_would_catch_a_qthread():
    assert _names_qthread("from PyQt6.QtCore import QThread")
    assert _names_qthread("import PyQt6.QtCore as c\nclass W(c.QThread): pass")
    assert not _names_qthread('"""Not a QThread."""')
