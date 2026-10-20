"""The controller's unsaved marks (#267): the window is told whenever the open document
starts or stops differing from its file, from whichever of the flag and the snapshot moved.

The controller is built without ``__init__``, which would build the whole app: only the
three attributes the marks read are set, and the window records what it was told.
"""

from __future__ import annotations

from types import SimpleNamespace

from bartlebot.app_controller import AppController, _file_snapshot
from bartlebot.bot.schema import BotConfig, StateModel


def _config() -> BotConfig:
    return BotConfig(
        id="bot-1",
        name="Test Bot",
        initial="s0",
        recovery_state="s0",
        states=[StateModel(id="s0", node_type="collect_reagent", params={})],
        transitions=[],
    )


def _controller(config: BotConfig) -> AppController:
    told: list[bool] = []
    controller = AppController.__new__(AppController)
    controller._window = SimpleNamespace(set_unsaved=told.append, told=told)
    controller._loaded_config = config
    controller._unsaved = False
    controller._clean = _file_snapshot(config)
    return controller


def test_an_edit_marks_the_window_and_a_save_clears_it():
    config = _config()
    controller = _controller(config)

    config.name = "Renamed"
    controller._has_unsaved_changes = True
    controller._has_unsaved_changes = False
    controller._clean_snapshot = _file_snapshot(config)

    assert controller._window.told == [True, False, False]


def test_an_edit_undone_back_to_the_file_clears_the_mark():
    """The flag stays raised by an edit that changes nothing (#122); the mark does not."""
    config = _config()
    controller = _controller(config)

    config.name = "Renamed"
    controller._has_unsaved_changes = True
    config.name = "Test Bot"
    controller._has_unsaved_changes = True

    assert controller._has_unsaved_changes
    assert controller._window.told == [True, False]


def test_a_document_with_no_snapshot_is_marked_while_its_flag_is_up():
    """A recovered file has no clean state to return to, so any raised flag is a change."""
    controller = _controller(_config())

    controller._clean_snapshot = None
    controller._has_unsaved_changes = True

    assert controller._window.told == [False, True]
