"""Tests for ending the process without Python's teardown, which crashed in sip on quit."""

import logging
import subprocess
import sys

import pytest

from bartlebot.gui import app_exit


class _Exited(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


@pytest.fixture
def terminated(monkeypatch) -> list[int]:
    """TerminateProcess is recorded and returns, and os._exit raises, so the test carries on."""
    calls: list[int] = []

    def _exit(code: int):
        raise _Exited(code)

    monkeypatch.setattr(app_exit, "_terminate", calls.append)
    monkeypatch.setattr(app_exit.os, "_exit", _exit)
    return calls


def test_ends_the_process_with_the_code_given(terminated):
    with pytest.raises(_Exited):
        app_exit.exit_skipping_teardown(3)
    assert terminated == [3]


def test_falls_back_to_os_exit_when_terminate_process_returns(terminated):
    with pytest.raises(_Exited) as ended:
        app_exit.exit_skipping_teardown(3)
    assert ended.value.code == 3


def test_says_so_in_the_log_and_shuts_logging_down_first(terminated, monkeypatch, caplog):
    """The last line is what tells a crash during the quit from one after it."""
    order: list[str] = []
    monkeypatch.setattr(app_exit.logging, "shutdown", lambda: order.append("logging shut down"))

    with caplog.at_level(logging.INFO, logger=app_exit.__name__), pytest.raises(_Exited):
        app_exit.exit_skipping_teardown()

    assert "=== BartleBot shutdown ===" in caplog.text
    assert order == ["logging shut down"]


def test_a_stream_that_cannot_flush_does_not_stop_the_exit(terminated, monkeypatch):
    class _Broken:
        def flush(self):
            raise OSError("pipe gone")

    monkeypatch.setattr(app_exit.logging, "shutdown", lambda: None)
    monkeypatch.setattr(app_exit.sys, "stdout", _Broken())
    monkeypatch.setattr(app_exit.sys, "stderr", None)  # pythonw has none
    with pytest.raises(_Exited):
        app_exit.exit_skipping_teardown()
    assert terminated == [0]


def test_a_real_process_ends_with_the_code_and_its_output_flushed():
    """TerminateProcess skips the CRT's own flush, so what was printed must already be out."""
    script = (
        "import atexit, sys\n"
        "from bartlebot.gui.app_exit import exit_skipping_teardown\n"
        "atexit.register(lambda: print('atexit ran'))\n"
        "print('before', end='')\n"
        "exit_skipping_teardown(7)\n"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert done.returncode == 7
    assert done.stdout == "before"
