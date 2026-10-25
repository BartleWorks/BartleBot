"""The game hang tracer: what a wait chain says, when a window is reported, and the Win32 it reads."""

import logging
import threading

from PyQt6.QtWidgets import QWidget

from bartlebot.gui.debug import game_hang_trace
from bartlebot.gui.debug.game_hang_trace import ChainNode, GameHangTracer, describe_chain

RUNNING, BLOCKED, THREAD, SEND_MESSAGE, ALPC = 2, 3, 8, 2, 4


def _describe(nodes):
    return describe_chain(nodes, own_pid=10, gui_tid=0x20, exe_name=lambda pid: {30: "svchost.exe"}.get(pid, ""))


def test_a_running_thread_is_running():
    assert _describe([ChainNode(THREAD, RUNNING, 99, 1)]) == "running"


def test_a_send_to_this_apps_gui_thread_says_so():
    chain = [ChainNode(THREAD, BLOCKED, 99, 1), ChainNode(SEND_MESSAGE, BLOCKED), ChainNode(THREAD, RUNNING, 10, 0x20)]
    assert _describe(chain) == "blocked: SendMessage -> this app's GUI thread 0x20"


def test_another_process_is_named_by_its_executable():
    chain = [ChainNode(THREAD, BLOCKED, 99, 1), ChainNode(ALPC, BLOCKED), ChainNode(THREAD, BLOCKED, 30, 0x44)]
    assert _describe(chain) == "blocked: ALPC (an RPC) -> thread 0x44 of svchost.exe (pid 30)"


def test_a_blocked_thread_with_no_chain_says_windows_could_not_name_it():
    assert _describe([ChainNode(THREAD, BLOCKED, 99, 1)]) == "blocked on nothing Windows could name"


def _tracer(monkeypatch):
    tracer = GameHangTracer(lambda: [])
    monkeypatch.setattr(tracer, "_cpu_time", lambda tid: None)
    return tracer


def test_a_window_unanswering_past_the_threshold_is_reported_once_it_answers(monkeypatch, caplog):
    tracer = _tracer(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="bartlebot.gui.debug.game_hang_trace"):
        tracer._note("P2", 7, (1, "running"), now=10.0)
        tracer._note("P2", 7, (1, "blocked: SendMessage"), now=10.5)
        tracer._note("P2", 7, (1, "running"), now=11.0)
        assert not caplog.records, "nothing is said while it is still unanswering"
        tracer._note("P2", 7, None, now=12.0)

    [record] = caplog.records
    assert record.getMessage() == (
        "P2's game did not answer for 2.2s; its window thread was running (2x), blocked: SendMessage (1x)"
    )


def test_a_short_miss_is_not_reported(monkeypatch, caplog):
    tracer = _tracer(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="bartlebot.gui.debug.game_hang_trace"):
        tracer._note("P1", 7, (1, "running"), now=10.0)
        tracer._note("P1", 7, None, now=10.5)
    assert not caplog.records


def test_a_window_that_answers_answers(qapp):
    window = QWidget()
    assert GameHangTracer(lambda: [])._probe(int(window.winId())) is None


def test_the_wait_chain_of_a_waiting_thread_reads_as_blocked():
    """The structures are laid out as Windows writes them: a thread parked on an event reads blocked."""
    started, release = threading.Event(), threading.Event()

    def park():
        started.set()
        release.wait(5)

    thread = threading.Thread(target=park)
    thread.start()
    try:
        started.wait(5)
        chain = GameHangTracer(lambda: [])._wait_chain(thread.native_id)
    finally:
        release.set()
        thread.join()

    assert chain, "Windows returned a chain"
    assert chain[0].kind == game_hang_trace._THREAD
    assert chain[0].status == BLOCKED
