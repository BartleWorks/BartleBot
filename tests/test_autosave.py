"""#122: an autosave is written only when the bot differs from what was opened or saved.

Leaving Edit mode announces a change only when the session made one. The dirty flag
can still be raised by an edit that changes nothing, so the autosave and the close
prompts compare against the clean snapshot, and the startup scan compares a recovery
file against its original before offering it.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QMessageBox

from bartlebot import app_controller
from bartlebot.app_controller import AppController
from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary
from bartlebot.settings_manager import SettingsManager
from tests.content.subgraphs.test_subgraph_integration import ControllerHarness
from tests.content.subgraphs.test_subgraphs import SYNC_ID, linked_bot, sync_players_definition


@pytest.fixture
def library(tmp_path) -> SubgraphLibrary:
    lib = SubgraphLibrary(tmp_path / "library")
    lib.save(sync_players_definition())
    return lib


@pytest.fixture
def harness(qapp, library, tmp_path):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    canvas = BotCanvas()
    h = ControllerHarness(canvas, library)
    path = tmp_path / "linked.json"
    linked_bot(library.get(SYNC_ID), library).save(path)
    h._close_bot = AppController._close_bot.__get__(h)
    h._open_bot(str(path))
    yield h
    canvas.prepare_for_shutdown()


def _autosave():
    return SettingsManager.autosave_dir() / "linked.autosave.json"


@pytest.fixture
def asked(monkeypatch) -> list[str]:
    """Every unsaved-changes question the controller puts, answered Discard."""
    titles: list[str] = []

    def question(parent, title, *args, **kwargs):
        titles.append(title)
        return QMessageBox.StandardButton.Discard

    monkeypatch.setattr(app_controller.QMessageBox, "question", question)
    return titles


def test_edit_then_done_is_not_a_change(harness):
    canvas = harness._window.bot_canvas
    canvas.enter_edit_mode()
    canvas.exit_edit_mode()

    assert not harness._has_unsaved_changes
    harness._do_autosave()
    assert not _autosave().exists()


def test_done_announces_an_edit_that_did_not_announce_itself(harness):
    canvas = harness._window.bot_canvas
    canvas.enter_edit_mode()
    canvas.config.name = "Renamed"
    canvas.exit_edit_mode()

    assert harness._has_unsaved_changes


def test_the_autosave_skips_a_flag_raised_without_a_change(harness):
    harness._on_config_changed()

    harness._do_autosave()

    assert not _autosave().exists()
    assert not harness._has_unsaved_changes, "the flag comes down once the check finds nothing"


def test_close_does_not_ask_about_a_flag_raised_without_a_change(harness, asked):
    harness._on_config_changed()

    harness._close_bot()

    assert asked == []
    assert harness._loaded_config is None


def test_close_asks_about_a_real_edit(harness, asked):
    harness._loaded_config.name = "Renamed"
    harness._on_config_changed()

    harness._close_bot()

    assert asked == ["Close Bot"]


def test_a_real_edit_is_autosaved(harness):
    harness._loaded_config.name = "Renamed"
    harness._on_config_changed()

    harness._do_autosave()

    assert BotConfig.load(_autosave()).name == "Renamed"


def test_undoing_back_to_the_saved_bot_removes_the_autosave(harness):
    original = harness._loaded_config.name
    harness._loaded_config.name = "Renamed"
    harness._on_config_changed()
    harness._do_autosave()
    assert _autosave().exists()

    harness._loaded_config.name = original
    harness._on_config_changed()
    harness._do_autosave()

    assert not _autosave().exists()
    assert not harness._has_unsaved_changes


def test_a_save_moves_the_baseline(harness):
    harness._loaded_config.name = "Renamed"
    harness._on_config_changed()
    harness._save_bot()
    harness._on_config_changed()  # e.g. Done after Save

    harness._do_autosave()

    assert not _autosave().exists()


# ---------------------------------------------------------------------------
# The startup scan
# ---------------------------------------------------------------------------


def _bot(name: str = "Bot") -> BotConfig:
    return BotConfig(
        id="00000000-0000-0000-0000-000000000122",
        name=name,
        initial="state_1",
        recovery_state="state_1",
        states=[StateModel(id="state_1", node_type="teleport_to_world_hub")],
        transitions=[],
    )


def _scan(tmp_path, autosaved: BotConfig | str) -> tuple[list, object]:
    """Write an original and a newer recovery file, run the startup scan, and return what it offered.

    A string is written as the recovery file's raw text.
    """
    original = tmp_path / "farm.json"
    _bot().save(original)
    recovery = SettingsManager.autosave_dir() / "farm.autosave.json"
    if isinstance(autosaved, str):
        recovery.write_text(autosaved, encoding="utf-8")
    else:
        autosaved.save(recovery)
    mtime = original.stat().st_mtime
    os.utime(recovery, (mtime + 60, mtime + 60))
    SettingsManager.instance().set_autosave_origin("farm", str(original))

    offered: list = []
    ns = SimpleNamespace(_offer_recovery=offered.append)
    for name in ("_check_crash_recovery", "_find_original_for_stem", "_delete_autosave"):
        setattr(ns, name, getattr(AppController, name).__get__(ns))
    ns._check_crash_recovery()
    return offered, recovery


def test_startup_drops_a_recovery_file_that_matches_its_original(tmp_path):
    offered, recovery = _scan(tmp_path, _bot())

    assert offered == []
    assert not recovery.exists()
    assert SettingsManager.instance().get_autosave_origin("farm") is None


def test_startup_offers_a_recovery_file_that_differs(tmp_path):
    offered, recovery = _scan(tmp_path, _bot("Edited"))

    assert len(offered) == 1
    assert offered[0][0]["autosave_path"] == recovery
    assert recovery.exists()


def test_startup_offers_a_recovery_file_that_will_not_load(tmp_path):
    offered, _ = _scan(tmp_path, "{not json")

    assert len(offered) == 1, "a comparison that cannot be made leaves it to the user"
