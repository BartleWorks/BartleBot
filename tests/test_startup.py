"""How ``bartlebot.py`` starts: core's skeleton, in core's order, with BartleBot's own name and folders.

Core's suite checks each piece; these check that BartleBot's entry point uses them, which
is where #154's crash came from: a hook that existed but was not installed.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

from bartleby.qt.app.single_instance import server_name
from bartleby.qt.testing.startup import raise_in_a_slot_and_a_thread

from bartlebot.paths import project_root

ENTRY = project_root() / "bartlebot.py"

#: What ``bartlebot.py`` runs before its controller, from the script itself: its main block is skipped.
_STARTUP = f"""
import runpy
from bartleby.shared.product import use
entry = runpy.run_path({str(ENTRY)!r})
running = entry["product"]()
use(running)
entry["_start_logging"](running)
"""


def _session_log(appdata: Path) -> str:
    (log,) = (appdata / "Bartleby" / "BartleBot" / "logs").glob("bartlebot_*.log")
    return log.read_text(encoding="utf-8")


def test_an_exception_in_a_slot_or_a_thread_is_logged_at_critical_and_bartlebot_runs_on():
    outcome = raise_in_a_slot_and_a_thread(_STARTUP, cwd=project_root())

    assert outcome.kept_running, outcome.stderr
    text = _session_log(Path(os.environ["APPDATA"]))
    assert "[CRITICAL] qt.app.logging_setup: Uncaught exception\n" in text
    assert "[CRITICAL] qt.app.logging_setup: Uncaught exception in thread worker\n" in text
    assert "RuntimeError: raised in a slot" in text
    assert "RuntimeError: raised in a thread" in text


def test_the_session_log_is_bartlebots_and_names_it(bartlebot_product):
    """In %APPDATA%/Bartleby/BartleBot/logs, as bartlebot_<date>.log, with BartleBot's header."""
    outcome = raise_in_a_slot_and_a_thread(_STARTUP, cwd=project_root())

    assert outcome.kept_running, outcome.stderr
    text = _session_log(Path(os.environ["APPDATA"]))
    assert "=== BartleBot startup ===" in text
    assert f"Version: {bartlebot_product.version}" in text
    assert "qasync==" in text
    assert "transitions==" in text


def _calls_in_main(name: str) -> list[int]:
    """The line of every call to *name* in ``bartlebot.py``'s main block, in order."""
    tree = ast.parse(ENTRY.read_text(encoding="utf-8"))
    main = next(node for node in tree.body if isinstance(node, ast.If))
    lines = []
    for node in ast.walk(main):
        if isinstance(node, ast.Call):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if called == name:
                lines.append(node.lineno)
    return sorted(lines)


def test_the_product_is_in_use_before_anything_else_and_one_copy_runs_before_the_log_starts():
    """A launch that only hands its arguments to the running BartleBot must not start a session log."""
    (use,) = _calls_in_main("use")
    (claim,) = _calls_in_main("claim")
    (logging,) = _calls_in_main("_start_logging")
    (controller,) = _calls_in_main("AppController")

    assert use < _calls_in_main("use_windows_backend")[0]
    assert use < claim < logging < controller


def test_bartlebots_pipe_is_its_own(bartlebot_product):
    assert server_name(bartlebot_product, user="u").startswith("Bartleby.BartleBot.")
