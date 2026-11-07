"""The unsaved marks (#267): the window is told whenever the open document starts or
stops differing from its file, from whichever of the flag and the snapshot moved.

``Documents`` gets a stand-in window that records what it was told, and the document's
config and snapshot are set directly, as an open would leave them.
"""

from __future__ import annotations

from types import SimpleNamespace

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary
from bartlebot.controller.documents import Documents, file_snapshot
from bartlebot.controller.templates import TemplateRoots


def _config() -> BotConfig:
    return BotConfig(
        id="bot-1",
        name="Test Bot",
        initial="s0",
        recovery_state="s0",
        states=[StateModel(id="s0", node_type="collect_reagent", params={})],
        transitions=[],
    )


def _documents(config: BotConfig) -> Documents:
    told: list[bool] = []
    window = SimpleNamespace(set_unsaved=told.append, told=told)
    docs = Documents(window, SubgraphLibrary(), TemplateRoots(), clients=lambda: [], is_running=lambda: False)
    docs._loaded_config = config
    docs._clean = file_snapshot(config)
    return docs


def test_an_edit_marks_the_window_and_a_save_clears_it():
    config = _config()
    docs = _documents(config)

    config.name = "Renamed"
    docs.has_unsaved_changes = True
    docs.has_unsaved_changes = False
    docs.clean_snapshot = file_snapshot(config)

    assert docs._window.told == [True, False, False]


def test_an_edit_undone_back_to_the_file_clears_the_mark():
    """The flag stays raised by an edit that changes nothing (#122); the mark does not."""
    config = _config()
    docs = _documents(config)

    config.name = "Renamed"
    docs.has_unsaved_changes = True
    config.name = "Test Bot"
    docs.has_unsaved_changes = True

    assert docs.has_unsaved_changes
    assert docs._window.told == [True, False]


def test_a_document_with_no_snapshot_is_marked_while_its_flag_is_up():
    """A recovered file has no clean state to return to, so any raised flag is a change."""
    docs = _documents(_config())

    docs.clean_snapshot = None
    docs.has_unsaved_changes = True

    assert docs._window.told == [False, True]
