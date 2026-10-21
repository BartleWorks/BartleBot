"""Tests for ending the process without Python's teardown, which crashed in sip on quit."""

import logging

import pytest

from bartlebot.gui import app_exit


class _Exited(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


@pytest.fixture
def exits(monkeypatch):
    """os._exit raises instead, so the test process carries on."""

    def _exit(code: int):
        raise _Exited(code)

    monkeypatch.setattr(app_exit.os, "_exit", _exit)


def test_ends_the_process_with_the_code_given(exits):
    with pytest.raises(_Exited) as ended:
        app_exit.exit_skipping_teardown(3)
    assert ended.value.code == 3


def test_says_so_in_the_log_and_shuts_logging_down_first(exits, monkeypatch, caplog):
    """The last line is what tells a crash during the quit from one after it."""
    order: list[str] = []
    monkeypatch.setattr(app_exit.logging, "shutdown", lambda: order.append("logging shut down"))

    with caplog.at_level(logging.INFO, logger=app_exit.__name__), pytest.raises(_Exited):
        app_exit.exit_skipping_teardown()

    assert "=== BartleBot shutdown ===" in caplog.text
    assert order == ["logging shut down"]


def test_a_stream_that_cannot_flush_does_not_stop_the_exit(exits, monkeypatch):
    class _Broken:
        def flush(self):
            raise OSError("pipe gone")

    monkeypatch.setattr(app_exit.logging, "shutdown", lambda: None)
    monkeypatch.setattr(app_exit.sys, "stdout", _Broken())
    monkeypatch.setattr(app_exit.sys, "stderr", None)  # pythonw has none
    with pytest.raises(_Exited):
        app_exit.exit_skipping_teardown()
