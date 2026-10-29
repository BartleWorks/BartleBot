"""Load a wiki page in a WebView2 window, for a user Cloudflare challenges (#278).

The drop import fetches a page with curl_cffi, which cannot run a challenge. For most
connections none is asked of it. For some, Cloudflare answers with its "Just a moment…"
page and a 403, and only a person can get past it: in their own browser they tick a box
and the page loads. This module puts the same box in front of them inside the app. It
opens the page in an Edge WebView2 window, the user passes the check themselves, and the
page's HTML comes back to the import once the wiki's content is on it. Nothing here
answers a check for the user.

The window runs in a child process. pywebview's WinForms backend holds the thread that
starts it until its last window closes, and in this process that thread is Qt's. The
child is this app again, started with :data:`FLAG`: ``bartlebot.py`` dispatches on it
before the single-instance check, which would otherwise hand the arguments to the running
app and exit. The child writes its answer to a JSON file the parent names rather than to
stdout, which a windowed build does not have.

The browser's profile is kept in :func:`profile_dir`, so the clearance Cloudflare grants
lasts as long as the wiki lets it, and an import soon after needs no check at all.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from bartleby.shared.vendor import product_dir

from bartlebot.game.wiki_pages import WIKI_HOST, wiki_page_path
from bartlebot.packaging.build_kind import is_packaged

logger = logging.getLogger(__name__)

#: The argument that makes a launch of the app this module's window, and nothing else.
FLAG = "--wiki-browser"

PROFILE_DIRNAME = "webview"

#: How long the parent waits for the window. Long, because a person is answering a check.
WAIT_SECONDS = 600

WINDOW_TITLE = "Wizard101 Central Wiki — the drops import when the page loads"

#: The title of Cloudflare's challenge page, for a response that lacks its header.
_CHALLENGE_TITLE = "Just a moment"

#: Whether the wiki's own content is on the page: absent from the challenge page.
_CONTENT_READY_JS = "!!document.getElementById('mw-content-text')"
_PAGE_HTML_JS = "document.documentElement.outerHTML"

#: The child's exit codes. The answer itself is in the file; these are for the log.
EXIT_CAPTURED = 0
EXIT_CLOSED = 1
EXIT_FAILED = 2


class WikiBrowserError(Exception):
    """The window could not be opened, or failed before the page loaded."""


def is_challenge(status: int, headers: Mapping[str, str], text: str) -> bool:
    """Whether a response is Cloudflare asking for a check rather than the page itself.

    ``cf-mitigated: challenge`` is the header Cloudflare documents for this. The title is
    the fallback for a response without it, and only on the status a challenge comes with,
    so a wiki page that merely mentions the phrase is never taken for one.
    """
    for key, value in headers.items():
        if key.lower() == "cf-mitigated" and value.strip().lower() == "challenge":
            return True
    return status in (403, 503) and _CHALLENGE_TITLE in text[:4096]


def profile_dir() -> Path:
    """``%APPDATA%/Bartleby/BartleBot/webview`` — the window's cookies and cache."""
    return product_dir() / PROFILE_DIRNAME


def command(url: str, out_path: Path) -> list[str]:
    """The command line that opens *url* in the window and writes its answer to *out_path*."""
    if is_packaged():
        return [sys.executable, FLAG, url, str(out_path)]
    from bartlebot.paths import project_root  # noqa: PLC0415

    return [sys.executable, str(project_root() / "bartlebot.py"), FLAG, url, str(out_path)]


# ---------------------------------------------------------------------------
# The parent: start the window and wait for its answer
# ---------------------------------------------------------------------------


class BrowserFetch:
    """One page fetched through the window. :meth:`run` blocks, so call it off the GUI thread."""

    def __init__(self, url: str, command_for: Callable[[str, Path], list[str]] = command):
        self._url = url
        self._command_for = command_for
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._cancelled = False

    def run(self) -> str | None:
        """The page's HTML, or None when the window was closed or :meth:`cancel` called first.

        Raises :class:`WikiBrowserError` when the window could not do its job.
        """
        with tempfile.TemporaryDirectory(prefix="bartlebot-wiki-") as tmp:
            out_path = Path(tmp) / "page.json"
            with self._lock:
                if self._cancelled:
                    return None
                try:
                    self._proc = subprocess.Popen(
                        self._command_for(self._url, out_path),
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                except OSError as exc:
                    raise WikiBrowserError(f"could not start the wiki window: {exc}") from exc
            try:
                code = self._proc.wait(WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
                raise WikiBrowserError("the wiki window was left open too long") from None
            if self._cancelled:
                return None
            return self._answer(out_path, code)

    def cancel(self) -> None:
        """Close the window, if it is open. Safe from any thread, and before :meth:`run`."""
        with self._lock:
            self._cancelled = True
            if self._proc is not None and self._proc.poll() is None:
                self._proc.kill()

    @staticmethod
    def _answer(out_path: Path, code: int) -> str | None:
        if not out_path.exists():
            logger.info("The wiki window closed before the page loaded (exit %d)", code)
            return None
        answer = json.loads(out_path.read_text(encoding="utf-8"))
        if "error" in answer:
            raise WikiBrowserError(answer["error"])
        return answer["html"]


# ---------------------------------------------------------------------------
# The child: the window itself
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    """The child's entry point: ``FLAG <url> <out_path>`` somewhere in *argv*."""
    try:
        at = argv.index(FLAG)
        url, out = argv[at + 1], argv[at + 2]
    except (ValueError, IndexError):
        return EXIT_FAILED
    # Anything can start the app with this flag, so the URL is held to the wiki here too.
    if wiki_page_path(url) is None or not url.lower().startswith("https://"):
        return EXIT_FAILED
    return run_window(url, Path(out))


def run_window(url: str, out_path: Path) -> int:
    """Show *url* until the wiki's content is on it, write its HTML to *out_path*, and close."""
    import webview  # noqa: PLC0415

    window = webview.create_window(WINDOW_TITLE, url, width=1000, height=760)
    done = threading.Event()

    def on_loaded() -> None:
        # Fires for the challenge page too; Cloudflare reloads the real one once it is passed.
        if done.is_set():
            return
        try:
            captured = capture(window, out_path)
        except Exception as exc:
            _write(out_path, {"error": f"{type(exc).__name__}: {exc}"})
            captured = True
        if captured:
            done.set()
            window.destroy()

    window.events.loaded += on_loaded
    try:
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(profile_dir()))
    except Exception as exc:
        _write(out_path, {"error": f"could not open the wiki window ({type(exc).__name__}: {exc})"})
        return EXIT_FAILED
    if not out_path.exists():
        return EXIT_CLOSED
    return EXIT_CAPTURED


def capture(window, out_path: Path) -> bool:
    """Write the page's HTML to *out_path* if the wiki's content is on it; whether it was.

    The window is a browser and the user can click away, so the page has to still be on
    the wiki as well.
    """
    if not _on_wiki(window.get_current_url() or ""):
        return False
    if not window.evaluate_js(_CONTENT_READY_JS):
        return False
    _write(out_path, {"html": window.evaluate_js(_PAGE_HTML_JS)})
    return True


def _on_wiki(url: str) -> bool:
    return url.lower().startswith(f"https://{WIKI_HOST}/")


def _write(out_path: Path, answer: dict) -> None:
    # Written whole and then renamed, so the parent never reads half an answer.
    partial = out_path.with_suffix(".partial")
    partial.write_text(json.dumps(answer), encoding="utf-8")
    partial.replace(out_path)
