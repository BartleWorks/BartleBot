"""A Teleport to XYZ that spans past HOP_SPAN: an error on the canvas, and Run refuses the bot (Stage 7 of #322)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.gui.canvas.edit_scene import _validate_node


def _xyz(x):
    return {"x": x, "y": 0.0, "z": 0.0}


def _bot(end_x: float) -> BotConfig:
    hop = StateModel(
        id="h",
        node_type="teleport_to_xyz",
        label="Past the gate",
        params={"zone": "Z/One", "start_xyz": _xyz(0.0), "end_xyz": _xyz(end_x)},
    )
    return BotConfig(id="b", name="B", initial="h", recovery_state="h", states=[hop], transitions=[])


def _check(config):
    from bartlebot.controller.run import Run  # noqa: PLC0415

    host = SimpleNamespace(_documents=SimpleNamespace(loaded_config=config), _window=None)
    return Run._check_hops.__get__(host)


def test_the_canvas_marks_a_hop_spanning_too_far_as_an_error():
    config = _bot(301.0)
    is_error, _warn, errors, _warnings = _validate_node(config.states[0], config)
    assert is_error and any("301 apart" in e for e in errors)

    config = _bot(300.0)
    _is_error, _warn, errors, _warnings = _validate_node(config.states[0], config)
    assert not any("apart" in e for e in errors)


def test_run_refuses_a_bot_whose_hop_spans_too_far(monkeypatch):
    import bartlebot.controller.run as run_module  # noqa: PLC0415

    shown = []
    monkeypatch.setattr(run_module.QMessageBox, "critical", lambda parent, title, text: shown.append((title, text)))

    assert _check(_bot(400.0))() is False
    assert shown and shown[0][0] == "Teleport to XYZ"
    assert "Past the gate: Start XYZ and End XYZ are 400 apart" in shown[0][1]


def test_run_lets_a_short_hop_through(monkeypatch):
    import bartlebot.controller.run as run_module  # noqa: PLC0415

    monkeypatch.setattr(run_module.QMessageBox, "critical", MagicMock(side_effect=AssertionError("refused")))

    assert _check(_bot(250.0))() is True
