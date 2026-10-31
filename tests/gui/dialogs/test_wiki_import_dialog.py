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


PAGE_HTML = """
<div id="mw-content-text">
  <p>Hats</p>
  <div class="drop-category"><a href="/wiki/Hat:Cap_of_Doom">Cap of Doom</a></div>
</div>
"""


class FakeBrowser:
    """Stands in for ``BrowserFetch``: answers *html* once *release* is set."""

    instances: list[FakeBrowser] = []

    def __init__(self, url, html=PAGE_HTML, release=None):
        self.url = url
        self.html = html
        self.release = release
        self.cancelled = False
        FakeBrowser.instances.append(self)

    def run(self):
        if self.release is not None:
            self.release.wait(5)
        return None if self.cancelled else self.html

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def challenged(monkeypatch):
    def fetch(url):
        raise wiki_import_dialog.WikiChallenged("HTTP 403")

    monkeypatch.setattr(wiki_import_dialog, "_fetch_drops", fetch)
    FakeBrowser.instances = []


async def test_a_challenged_fetch_loads_the_page_in_a_window(qapp, monkeypatch, challenged):
    monkeypatch.setattr(wiki_import_dialog, "BrowserFetch", FakeBrowser)
    dlg = WikiImportDialog()

    await _start(dlg)

    assert [b.url for b in FakeBrowser.instances] == [URL]
    assert dlg._status_lbl.text() == "Found 1 item(s) across 1 category."
    assert dlg._tree.topLevelItemCount() == 1


async def test_a_window_closed_first_says_so_and_lets_the_user_try_again(qapp, monkeypatch, challenged):
    monkeypatch.setattr(wiki_import_dialog, "BrowserFetch", lambda url: FakeBrowser(url, html=None))
    dlg = WikiImportDialog()

    await _start(dlg)

    assert dlg._status_lbl.text().startswith("The wiki window was closed before the page loaded.")
    assert dlg._status_error
    assert dlg._btn_fetch.isEnabled()


async def test_a_window_that_failed_shows_its_message(qapp, monkeypatch, challenged):
    class FailingBrowser(FakeBrowser):
        def run(self):
            raise wiki_import_dialog.WikiBrowserError("The wiki window couldn't open.", "no WebView2 runtime")

    monkeypatch.setattr(wiki_import_dialog, "BrowserFetch", FailingBrowser)
    dlg = WikiImportDialog()

    await _start(dlg)

    assert dlg._status_lbl.text() == "The wiki window couldn't open."
    assert dlg._status_error
    assert dlg._btn_fetch.isEnabled()


# Cancel and Esc reject the dialog, which sends no closeEvent.
@pytest.mark.parametrize("end", ["close", "reject"])
async def test_ending_the_dialog_closes_the_window(qapp, monkeypatch, challenged, end):
    release = threading.Event()
    monkeypatch.setattr(wiki_import_dialog, "BrowserFetch", lambda url: FakeBrowser(url, release=release))
    dlg = WikiImportDialog()
    task = _start(dlg)
    while not FakeBrowser.instances:
        await asyncio.sleep(0.01)

    getattr(dlg, end)()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert FakeBrowser.instances[0].cancelled
    assert dlg._tree.topLevelItemCount() == 0


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
