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


def test_a_zone_with_no_walking_map_is_a_warning_naming_the_node_and_the_zone(qapp, monkeypatch):
    """Stage 4 of #322: on load, beside Run's refusal of the same bot."""
    from types import SimpleNamespace  # noqa: PLC0415

    from bartlebot.bot.schema import BotConfig, StateModel  # noqa: PLC0415
    from bartlebot.game import walking_maps  # noqa: PLC0415
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    maps = SimpleNamespace(index={"WizardCity/WC_Hub": {}})
    maps.has = lambda zone: zone in maps.index
    monkeypatch.setattr(walking_maps, "MAPS", maps)
    config = BotConfig(
        id="test-bot",
        name="t",
        initial="a",
        recovery_state="a",
        states=[
            StateModel(
                id="a",
                node_type="enter_zone",
                label="Leave",
                params={"from_zone": "WizardCity/WC_Hub", "to_zone": "Test/T_Nowhere", "xyz": {"x": 1, "y": 2, "z": 3}},
            )
        ],
        transitions=[],
    )
    canvas = BotCanvas()
    canvas.setup(config)
    try:
        assert f"Leave: {walking_maps.warning('Test/T_Nowhere')}" in canvas.validation_warnings()
        assert not any("WC_Hub" in w for w in canvas.validation_warnings())
    finally:
        canvas.prepare_for_shutdown()
