"""Stage 2 of #37: linked regions compose on open and before a run, save as a snapshot, and flag.

The controller's file and run methods are bound onto a harness that holds a real
``BotCanvas`` and a mock window around it, the way ``PreRunDeckHarness`` hosts the
pre-run deck methods: the production code paths run, nothing else is constructed.
The subgraph library is a ``tmp_path`` one, so no test reads the developer's roots.
The definition is the synthetic seven-node "sync players" chain from
``test_subgraphs``; nothing here is a real character.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bartlebot.bot.schema import BotConfig, RegionModel, StateModel, SubgraphSource
from bartlebot.gui.canvas.bot_canvas import BotMode
from bartlebot.subgraph_store import SubgraphLibrary
from bartlebot.subgraphs import ComposeReport, compose_bot
from tests.test_subgraphs import REGION, SYNC_ID, _all_bot_files, _t, linked_bot, m, sync_players_definition


class ControllerHarness:
    """Hosts the AppController methods under test without constructing the app."""

    def __init__(self, canvas, library: SubgraphLibrary) -> None:
        from bartlebot.app_controller import AppController  # noqa: PLC0415

        cls = AppController
        for name in (
            "_compose",
            "_recompose_before_run",
            "_open_bot",
            "_save_bot",
            "_save_bot_as",
            "_do_autosave",
            "_delete_autosave",
            "_autosave_path",
            "_on_config_changed",
            "_on_run_requested",
            "_check_restricted_zones",
            "_start_enabled",
        ):
            setattr(self, name, getattr(cls, name).__get__(self))
        self._subgraph_library = library
        self._loaded_config: BotConfig | None = None
        self._last_save_path: str | None = None
        self._has_unsaved_changes = False
        self._current_machine = None
        self._bot_task = None
        self._client_manager = MagicMock(clients=[])
        self._window = MagicMock()
        self._window.bot_canvas = canvas
        canvas.config_changed.connect(self._on_config_changed)


def _v2_definition():
    """Version 2 adds an eighth member after ``wisps`` and changes the world; nothing vanishes."""
    defn = sync_players_definition(version=2, world="Mooshu")
    defn.states.append(StateModel(id="rest", node_type="handle_potions", position={"x": 3080.0, "y": 0.0}, params={}))
    defn.transitions.append(_t("wisps", "rest"))
    return defn


@pytest.fixture
def library(tmp_path) -> SubgraphLibrary:
    lib = SubgraphLibrary(tmp_path / "library")
    lib.save(sync_players_definition())
    return lib


@pytest.fixture
def bot_file(tmp_path, library) -> Path:
    """A bot file whose snapshot is the version 1 composition."""
    path = tmp_path / "linked.json"
    linked_bot(library.get(SYNC_ID), library).save(path)
    return path


@pytest.fixture
def harness(qapp, library):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    canvas = BotCanvas()
    h = ControllerHarness(canvas, library)
    yield h
    canvas.prepare_for_shutdown()


# ---------------------------------------------------------------------------
# Open
# ---------------------------------------------------------------------------


def test_open_composes_linked_region_from_library(harness, library, bot_file):
    library.save(_v2_definition())
    harness._open_bot(str(bot_file))

    config = harness._window.bot_canvas.config
    assert harness._loaded_config is config
    assert config.regions[0].source.version == 2
    assert m("rest") in config.regions[0].members
    assert {s.id: s for s in config.states}[m("go_to_world")].params["world"] == "Mooshu"
    assert harness._window.bot_canvas.scene.node_items[m("rest")] is not None
    assert harness._window.bot_canvas.compose_report.regions[REGION].kind == "linked"
    assert not harness._has_unsaved_changes, "an open is never an edit"


def test_open_does_not_rewrite_the_file(harness, library, bot_file):
    before = bot_file.read_bytes()
    library.save(_v2_definition())
    harness._open_bot(str(bot_file))
    assert bot_file.read_bytes() == before


def test_save_writes_refreshed_snapshot_and_version(harness, library, bot_file):
    library.save(_v2_definition())
    harness._open_bot(str(bot_file))
    harness._save_bot()

    raw = json.loads(bot_file.read_text(encoding="utf-8"))
    region = next(r for r in raw["regions"] if r["id"] == REGION)
    assert region["source"]["version"] == 2
    assert m("rest") in region["members"]
    assert any(s["id"] == m("rest") for s in raw["states"])
    assert BotConfig.load(bot_file).model_dump() == harness._loaded_config.model_dump()


def test_missing_definition_keeps_snapshot_and_warns(harness, library, bot_file):
    snapshot = BotConfig.load(bot_file)
    library.delete(SYNC_ID)
    harness._open_bot(str(bot_file))

    canvas = harness._window.bot_canvas
    assert canvas.config.model_dump() == snapshot.model_dump()
    assert canvas.compose_report.regions[REGION].kind == "missing"
    assert any(line.startswith("Region Sync players: needs attention") for line in canvas.validation_warnings())
    assert not any("Region Sync players" in line for line in canvas.validation_errors())


def test_held_region_when_initial_would_vanish(harness, library, bot_file):
    defn = sync_players_definition(version=2)
    defn.states = [s for s in defn.states if s.id != "relog"]
    defn.transitions = [t for t in defn.transitions if "relog" not in (t.source, t.dest)]
    library.save(defn)
    harness._open_bot(str(bot_file))

    canvas = harness._window.bot_canvas
    assert canvas.compose_report.regions[REGION].kind == "held"
    assert canvas.config.regions[0].source.version == 1
    assert m("relog") in canvas.scene.node_items
    warnings = canvas.validation_warnings()
    assert any("initial state" in line and line.startswith("Region Sync players: needs attention") for line in warnings)


def _cycle_bot(library: SubgraphLibrary) -> BotConfig:
    from tests.test_subgraphs import _outer_definition  # noqa: PLC0415

    library.save(_outer_definition(inner_id="loop"))
    loop = _outer_definition(inner_id="outer")
    loop.id = "loop"
    library.save(loop)
    source = SubgraphSource(subgraph_id="outer", version=1)
    return BotConfig(
        id="b",
        name="B",
        initial="x",
        recovery_state="x",
        states=[StateModel(id="x", node_type="relog"), StateModel(id="y", node_type="relog")],
        transitions=[],
        regions=[RegionModel(id="R", label="Outer", members=["y"], color=1, source=source)],
    )


def test_cycle_is_an_error_and_disables_run(harness, library, tmp_path):
    path = tmp_path / "cycle.json"
    _cycle_bot(library).save(path)
    harness._open_bot(str(path))

    canvas = harness._window.bot_canvas
    canvas.set_run_enabled(True)
    assert canvas.compose_report.has_errors
    assert any(
        line.startswith("Region Outer: subgraph cycle outer -> loop -> outer") for line in canvas.validation_errors()
    )
    assert not canvas._run_pause_btn.isEnabled()
    assert "fix 1 error" in canvas._run_pause_btn.toolTip()


def test_cycle_refuses_the_run_with_a_dialog(harness, library, tmp_path, monkeypatch):
    from bartlebot import app_controller  # noqa: PLC0415

    path = tmp_path / "cycle.json"
    _cycle_bot(library).save(path)
    harness._open_bot(str(path))
    shown = []
    monkeypatch.setattr(app_controller.QMessageBox, "critical", lambda *a, **k: shown.append(a))
    assert harness._recompose_before_run() is False
    assert len(shown) == 1 and "Subgraph Cycle" in shown[0][1]


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


async def test_run_recompose_swaps_config_before_machine_build(harness, library, bot_file, monkeypatch):
    from bartlebot.gui import safety_warning_dialog  # noqa: PLC0415

    harness._open_bot(str(bot_file))
    library.save(_v2_definition())
    seen: list[BotConfig] = []

    async def fake_start() -> None:
        seen.append(harness._loaded_config)

    harness._start_bot = fake_start
    harness._collect_combat_warnings = lambda: []
    harness._confirm_recovery_gaps = lambda: True
    harness._client_manager.clients = [MagicMock()]
    monkeypatch.setattr(safety_warning_dialog, "collect_safety_warnings", lambda *a: [])

    harness._on_run_requested()
    await harness._bot_task

    assert len(seen) == 1
    started = seen[0]
    assert started is harness._window.bot_canvas.config
    assert started.regions[0].source.version == 2
    assert m("rest") in {s.id for s in started.states}


def test_run_recompose_marks_unsaved_and_logs_once_per_region(harness, library, bot_file, caplog):
    harness._open_bot(str(bot_file))
    assert not harness._has_unsaved_changes
    library.save(_v2_definition())

    with caplog.at_level(logging.INFO, logger="bartlebot.app_controller"):
        assert harness._recompose_before_run() is True

    assert harness._has_unsaved_changes
    harness._window.set_save_enabled.assert_called_with(True)
    lines = [r.getMessage() for r in caplog.records if "Subgraph region" in r.getMessage()]
    assert lines == ["Subgraph region 'Sync players' updated to library version 2"]
    assert harness._window.bot_canvas.compose_report.regions[REGION].library_version == 2


def test_run_recompose_is_silent_when_nothing_changed(harness, bot_file, caplog):
    harness._open_bot(str(bot_file))
    before = harness._loaded_config
    with caplog.at_level(logging.INFO, logger="bartlebot.app_controller"):
        assert harness._recompose_before_run() is True
    assert harness._loaded_config is before
    assert not harness._has_unsaved_changes
    assert not [r for r in caplog.records if "Subgraph region" in r.getMessage()]


# ---------------------------------------------------------------------------
# Save As, undo, autosave
# ---------------------------------------------------------------------------


def test_save_as_keeps_member_ids(harness, library, bot_file, tmp_path, monkeypatch):
    from bartlebot import app_controller  # noqa: PLC0415

    library.save(_v2_definition())
    harness._open_bot(str(bot_file))
    original_id = harness._loaded_config.id
    fork = tmp_path / "fork.json"
    monkeypatch.setattr(app_controller.QFileDialog, "getSaveFileName", lambda *a, **k: (str(fork), ""))

    harness._save_bot_as()

    saved = BotConfig.load(fork)
    assert saved.id != original_id
    assert saved.regions[0].members == harness._loaded_config.regions[0].members
    assert saved.regions[0].source.subgraph_id == SYNC_ID
    assert saved.regions[0].source.version == 2
    assert all(s.id.startswith("sg_") for s in saved.states if s.id in saved.regions[0].members)


def test_undo_after_replace_config_restores_previous_graph(harness, library, bot_file):
    harness._open_bot(str(bot_file))
    canvas = harness._window.bot_canvas
    canvas.enter_edit_mode()
    before = canvas.config.model_dump()

    library.save(_v2_definition())
    library.reload()
    composed, _ = compose_bot(canvas.config, library)
    canvas.replace_config(composed, "Update subgraphs")
    assert m("rest") in canvas.scene.node_items
    assert canvas.can_undo()

    canvas.undo()
    assert canvas.config.model_dump() == before
    assert m("rest") not in canvas.scene.node_items
    assert harness._loaded_config is canvas.config, "the controller follows the swap both ways"


def test_replace_config_in_run_mode_pushes_no_undo(harness, library, bot_file):
    harness._open_bot(str(bot_file))
    canvas = harness._window.bot_canvas
    assert canvas.mode == BotMode.RUN
    library.save(_v2_definition())
    library.reload()
    composed, _ = compose_bot(canvas.config, library)
    canvas.replace_config(composed, "Update subgraphs")
    assert canvas.config is composed
    canvas.enter_edit_mode()
    assert not canvas.can_undo()


def test_autosave_carries_composed_members(harness, library, bot_file):
    harness._open_bot(str(bot_file))
    library.save(_v2_definition())
    harness._recompose_before_run()

    harness._do_autosave()
    autosave = harness._autosave_path()
    assert autosave.exists()
    recovered = BotConfig.load(autosave)
    assert m("rest") in recovered.regions[0].members
    assert recovered.regions[0].source.version == 2
    assert bot_file.stat().st_size == len(bot_file.read_bytes()), "the original is untouched"
    assert BotConfig.load(bot_file).regions[0].source.version == 1


def test_setup_and_close_clear_the_report(harness, library, bot_file):
    library.delete(SYNC_ID)
    harness._open_bot(str(bot_file))
    canvas = harness._window.bot_canvas
    assert canvas.compose_report.regions
    canvas.setup(BotConfig.load(bot_file))
    assert canvas.compose_report.regions == {}
    canvas.set_compose_report(ComposeReport())
    canvas.close_bot()
    assert canvas.compose_report.regions == {}


# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------


def test_flush_regions_keeps_single_member_linked_region(qapp, library, bot_file):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    canvas = BotCanvas()
    config = BotConfig.load(bot_file)
    canvas.setup(config)
    canvas.enter_edit_mode()
    scene = canvas.scene
    members = list(config.regions[0].members)
    for sid in members[1:]:
        scene.delete_state(sid, confirm=False)
    assert [r.id for r in canvas.config.regions if r.source is not None] == [REGION]
    assert canvas.config.regions[0].members == members[:1]
    # the ordinary region still goes when it falls below two members
    scene.delete_state("state_2", confirm=False)
    assert [r.id for r in canvas.config.regions] == [REGION]
    canvas.prepare_for_shutdown()


# ---------------------------------------------------------------------------
# Library-wide, against the real roots
# ---------------------------------------------------------------------------


def test_every_shipped_bot_opens_with_empty_report():
    """No shipped bot links a subgraph yet (#59 is where that starts), so composing
    each one against the real roots must be the identity with nothing to report."""
    library = SubgraphLibrary()
    for path in _all_bot_files():
        config = BotConfig.load(path)
        composed, report = compose_bot(config, library)
        assert composed is config, path
        assert report.regions == {}, path
