"""The run's state, the node it is in, and its clock, as Bar B's status line reads them."""

from __future__ import annotations

import pytest
from bartleby.qt.theming.theme import THEME

from bartlebot.gui.canvas import run_status
from bartlebot.gui.canvas.run_status import IDLE, PAUSED, RUNNING, RunStatus, elapsed_text


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(run_status.time, "monotonic", lambda: now[0])
    return now


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(None, "00:00:00"), (0, "00:00:00"), (59.9, "00:00:59"), (61, "00:01:01"), (3 * 3600 + 7, "03:00:07")],
)
def test_elapsed_text(seconds, text):
    assert elapsed_text(seconds) == text


def test_idle_says_neither_where_nor_how_long(qapp):
    status = RunStatus()
    status.enter("hub")

    assert status.describe(str.upper) == (THEME.text_muted, "Idle", "", "")


def test_a_run_names_its_node_and_counts_from_its_start(qapp, clock):
    status = RunStatus()
    status.start()
    status.enter("hub")
    clock[0] += 65

    assert status.describe(str.upper) == (THEME.success, "Running", "HUB", "00:01:05")


def test_a_pause_keeps_the_node_and_the_clock(qapp, clock):
    status = RunStatus()
    status.start()
    status.enter("hub")
    status.pause()
    clock[0] += 5

    assert status.state == PAUSED
    assert status.describe(str.upper) == (THEME.status_paused, "Paused", "HUB", "00:00:05")
    status.resume()
    assert status.state == RUNNING


def test_a_run_before_its_first_node_has_no_label(qapp, clock):
    status = RunStatus()
    status.start()

    assert status.describe(str.upper)[2] == ""


def test_entering_and_stopping_hand_back_the_node_to_dim(qapp):
    status = RunStatus()
    status.start()

    assert status.enter("a") is None
    assert status.enter("b") == "a"
    assert status.stop() == "b"
    assert status.state == IDLE and status.active_state is None


def test_the_clock_ticks_only_while_a_run_goes(qapp):
    status = RunStatus()
    assert not status._timer.isActive()
    status.start()
    assert status._timer.isActive()
    status.stop()
    assert not status._timer.isActive()
