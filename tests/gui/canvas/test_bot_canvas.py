"""The canvas's own Result line in the Log tab names what chose the edge (#200)."""

import logging

import pytest

LOGGER = "bartlebot.gui.canvas.bot_canvas"


@pytest.fixture
def canvas(qapp):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    c = BotCanvas()
    yield c
    c.deleteLater()


def _results(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and r.getMessage().startswith("Result:")]


def test_the_result_line_names_the_condition_and_the_writes_of_the_edge_taken(canvas, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        canvas.on_transition("which", "SUCCESS", "rack_2", " [swords == 2] / found toggled")
    assert [r.getMessage() for r in _results(caplog)] == ["Result: SUCCESS [swords == 2] / found toggled → rack_2"]


def test_a_retry_names_it_too(canvas, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        canvas.on_transition("which", "FAILURE", "which", " [swords < 4]")
    assert [r.getMessage() for r in _results(caplog)] == ["Result: FAILURE [swords < 4] → (retry which)"]


def test_a_plain_edge_reads_as_before(canvas, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        canvas.on_transition("a", "SUCCESS", "b", "")
    assert [r.getMessage() for r in _results(caplog)] == ["Result: SUCCESS → b"]


def test_the_result_line_stays_out_of_the_session_file(canvas, caplog):
    """The machine's Transition line already says it there."""
    with caplog.at_level(logging.INFO, logger=LOGGER):
        canvas.on_transition("which", "SUCCESS", "rack_2", " [swords == 2]")
    assert all(getattr(r, "gui_only", False) for r in _results(caplog))
