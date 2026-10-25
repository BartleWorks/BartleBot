"""The WebView2 window a drop import falls back to when Cloudflare challenges its fetch (#278).

The parent's side runs a stand-in child (``python -c``), so these never open a window.
The child's side runs against a stand-in ``webview`` module.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from bartlebot.game import wiki_browser
from bartlebot.game.wiki_browser import (
    EXIT_CAPTURED,
    EXIT_CLOSED,
    EXIT_FAILED,
    FLAG,
    OPEN_FAILED,
    READ_FAILED,
    TIMED_OUT,
    BrowserFetch,
    WikiBrowserError,
    capture,
    command,
    is_challenge,
    main,
    profile_dir,
    run_window,
)
from bartlebot.game.wiki_pages import WIKI_HOST
from bartlebot.paths import project_root

URL = f"https://{WIKI_HOST}/wiki/Creature:Lost_Soul"
CHALLENGE_HTML = "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body></body></html>"


# ---------------------------------------------------------------------------
# Telling a challenge from the page
# ---------------------------------------------------------------------------


def test_cloudflares_challenge_header_marks_a_challenge_whatever_its_case():
    assert is_challenge(403, {"CF-Mitigated": "Challenge"}, "")


def test_the_challenge_title_marks_one_on_a_403_without_the_header():
    assert is_challenge(403, {}, CHALLENGE_HTML)


def test_a_page_that_loaded_is_never_a_challenge_whatever_it_says():
    assert not is_challenge(200, {}, CHALLENGE_HTML)


def test_a_403_of_some_other_kind_is_not_a_challenge():
    assert not is_challenge(403, {"server": "cloudflare"}, "<title>Attention Required!</title>")


# ---------------------------------------------------------------------------
# Where the window runs from, and what it keeps
# ---------------------------------------------------------------------------


def test_a_checkout_starts_the_window_through_bartlebot_py(tmp_path):
    out = tmp_path / "page.json"
    assert command(URL, out) == [sys.executable, str(project_root() / "bartlebot.py"), FLAG, URL, str(out)]


def test_a_packaged_build_starts_its_own_exe(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    out = tmp_path / "page.json"
    assert command(URL, out) == [sys.executable, FLAG, URL, str(out)]


def test_bartlebot_py_dispatches_on_the_flag():
    assert f'"{FLAG}"' in (project_root() / "bartlebot.py").read_text(encoding="utf-8")


def test_the_profile_is_kept_in_the_products_data_folder():
    assert profile_dir().parts[-3:] == ("Bartleby", "BartleBot", "webview")


# ---------------------------------------------------------------------------
# The parent: start the child and read its answer
# ---------------------------------------------------------------------------


def _child(body: str):
    """A ``command_for`` running *body* as Python, with ``out`` bound to the answer's path."""

    def command_for(url: str, out_path: Path) -> list[str]:
        return [sys.executable, "-c", f"from pathlib import Path\nout = Path({str(out_path)!r})\n{body}"]

    return command_for


def test_the_pages_html_comes_back_from_the_window():
    fetch = BrowserFetch(URL, _child("import json; out.write_text(json.dumps({'html': '<p>drops</p>'}))"))
    assert fetch.run() == "<p>drops</p>"


def test_a_window_closed_before_the_page_loaded_returns_none():
    assert BrowserFetch(URL, _child("raise SystemExit(1)")).run() is None


def test_a_window_that_failed_says_what_happened_and_logs_why():
    answer = {"error": OPEN_FAILED, "detail": "no WebView2 runtime"}
    fetch = BrowserFetch(URL, _child(f"import json; out.write_text(json.dumps({answer!r}))"))
    with pytest.raises(WikiBrowserError) as raised:
        fetch.run()
    assert str(raised.value) == OPEN_FAILED
    assert raised.value.detail == "no WebView2 runtime"


def test_a_child_that_cannot_start_is_an_error():
    fetch = BrowserFetch(URL, lambda url, out: [str(Path("no") / "such" / "program.exe")])
    with pytest.raises(WikiBrowserError) as raised:
        fetch.run()
    assert str(raised.value) == OPEN_FAILED
    assert "could not start" in raised.value.detail


def test_a_window_left_open_too_long_is_closed_and_an_error(monkeypatch):
    monkeypatch.setattr(wiki_browser, "WAIT_SECONDS", 0.2)
    with pytest.raises(WikiBrowserError) as raised:
        BrowserFetch(URL, _child("import time; time.sleep(30)")).run()
    assert str(raised.value) == TIMED_OUT


def test_cancel_closes_an_open_window_and_returns_none():
    fetch = BrowserFetch(URL, _child("import time; time.sleep(30)"))
    result = []
    runner = threading.Thread(target=lambda: result.append(fetch.run()))
    started = time.monotonic()
    runner.start()
    time.sleep(0.3)

    fetch.cancel()
    runner.join(10)

    assert result == [None]
    assert time.monotonic() - started < 10


def test_cancel_before_run_starts_nothing():
    started = []
    fetch = BrowserFetch(URL, lambda url, out: started.append(url) or [sys.executable, "-c", "pass"])
    fetch.cancel()
    assert fetch.run() is None
    assert started == []


# ---------------------------------------------------------------------------
# The child: its arguments
# ---------------------------------------------------------------------------


@pytest.fixture
def no_window(monkeypatch):
    opened = []
    monkeypatch.setattr(wiki_browser, "run_window", lambda url, out: opened.append(url) or EXIT_CAPTURED)
    return opened


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/wiki/Creature:X",
        f"http://{WIKI_HOST}/wiki/Creature:X",
        f"https://{WIKI_HOST}/w/index.php",
        f"https://{WIKI_HOST}.evil.example/wiki/Creature:X",
    ],
)
def test_the_child_opens_nothing_but_a_wiki_page(no_window, tmp_path, url):
    assert main([FLAG, url, str(tmp_path / "page.json")]) == EXIT_FAILED
    assert no_window == []


def test_the_child_needs_both_its_arguments(no_window):
    assert main([FLAG, URL]) == EXIT_FAILED
    assert no_window == []


def test_the_child_opens_a_wiki_page(no_window, tmp_path):
    assert main(["--other", FLAG, URL, str(tmp_path / "page.json")]) == EXIT_CAPTURED
    assert no_window == [URL]


# ---------------------------------------------------------------------------
# The child: the window
# ---------------------------------------------------------------------------


class FakeWindow:
    """A pywebview window showing *pages* one after another, each ``(url, content_ready)``."""

    def __init__(self, pages):
        self._pages = list(pages)
        self.page = 0
        self.destroyed = False
        self.events = types.SimpleNamespace(loaded=_Event())

    def get_current_url(self):
        return self._pages[self.page][0]

    def evaluate_js(self, script):
        url, ready = self._pages[self.page]
        if "mw-content-text" in script:
            return ready
        return f"<html>{url}</html>"

    def destroy(self):
        self.destroyed = True


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        for handler in self.handlers:
            handler()


def test_capture_waits_while_the_challenge_is_showing(tmp_path):
    out = tmp_path / "page.json"
    assert not capture(FakeWindow([(URL, False)]), out)
    assert not out.exists()


def test_capture_ignores_a_page_off_the_wiki(tmp_path):
    out = tmp_path / "page.json"
    assert not capture(FakeWindow([("https://evil.example/wiki/X", True)]), out)
    assert not out.exists()


def test_capture_writes_the_page_once_the_wikis_content_is_on_it(tmp_path):
    out = tmp_path / "page.json"
    assert capture(FakeWindow([(URL, True)]), out)
    assert json.loads(out.read_text(encoding="utf-8")) == {"html": f"<html>{URL}</html>"}


def _fake_webview(monkeypatch, window: FakeWindow, start):
    module = types.SimpleNamespace(create_window=lambda *args, **kwargs: window, start=start)
    monkeypatch.setitem(sys.modules, "webview", module)


def test_the_window_takes_the_page_after_the_check_is_passed_and_closes(monkeypatch, tmp_path):
    window = FakeWindow([(URL + "?__cf_chl_tk=x", False), (URL, True)])
    started = {}

    def start(**kwargs):
        started.update(kwargs)
        window.events.loaded.fire()  # the challenge
        assert not window.destroyed
        window.page = 1
        window.events.loaded.fire()  # the page, once the user passed it

    _fake_webview(monkeypatch, window, start)
    out = tmp_path / "page.json"

    assert run_window(URL, out) == EXIT_CAPTURED
    assert window.destroyed
    assert json.loads(out.read_text(encoding="utf-8")) == {"html": f"<html>{URL}</html>"}
    assert started["gui"] == "edgechromium"
    assert started["private_mode"] is False
    assert started["storage_path"] == str(profile_dir())


def test_a_window_the_user_closes_reports_closed(monkeypatch, tmp_path):
    window = FakeWindow([(URL, False)])
    _fake_webview(monkeypatch, window, lambda **kwargs: window.events.loaded.fire())
    out = tmp_path / "page.json"

    assert run_window(URL, out) == EXIT_CLOSED
    assert not out.exists()


def test_a_window_that_cannot_open_writes_why(monkeypatch, tmp_path):
    def start(**kwargs):
        raise RuntimeError("WebView2 runtime not found")

    _fake_webview(monkeypatch, FakeWindow([(URL, False)]), start)
    out = tmp_path / "page.json"

    assert run_window(URL, out) == EXIT_FAILED
    answer = json.loads(out.read_text(encoding="utf-8"))
    assert answer["error"] == OPEN_FAILED
    assert "WebView2 runtime not found" in answer["detail"]


def test_a_page_that_cannot_be_read_writes_why(monkeypatch, tmp_path):
    window = FakeWindow([(URL, True)])

    def evaluate_js(script):
        raise RuntimeError("script failed")

    window.evaluate_js = evaluate_js
    _fake_webview(monkeypatch, window, lambda **kwargs: window.events.loaded.fire())
    out = tmp_path / "page.json"

    assert run_window(URL, out) == EXIT_CAPTURED
    assert window.destroyed
    assert json.loads(out.read_text(encoding="utf-8")) == {
        "error": READ_FAILED,
        "detail": "RuntimeError: script failed",
    }
