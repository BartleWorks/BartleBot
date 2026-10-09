"""A template draft open for editing (10.2): the crumb, Bar B, saving, refusing, and Release.

Real widget tests (QApplication required) against the controller's own methods, through
the same harness the subgraph document tests use. Nothing reads pixels.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QInputDialog, QMessageBox

from bartlebot import app_controller
from bartlebot.bot.schema import BotConfig, StateModel, TemplateMeta, TransitionModel
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary
from bartlebot.content.templates.template_store import find_template, load_version, release, save_draft
from bartlebot.gui.canvas.bot_canvas import BotMode
from bartlebot.gui.canvas.subgraph_widgets import CrumbMode
from tests.content.subgraphs.test_subgraph_integration import ControllerHarness

TEMPLATE_ID = "tpl-boss"


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """Answer any message box a test did not stub, so a missed prompt fails fast instead of blocking the run."""
    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.No)


@pytest.fixture
def dev(monkeypatch):
    monkeypatch.setattr(app_controller, "dev_mode", lambda: True)


def _draft(*extra: StateModel) -> BotConfig:
    return BotConfig(
        id=TEMPLATE_ID,
        name="Boss Farmer",
        initial="t_1",
        recovery_state="t_1",
        tags=["Boss"],
        states=[
            StateModel(id="t_1", node_type="quicksell", position={"x": 0.0, "y": 0.0}),
            StateModel(id="t_2", node_type="quicksell", position={"x": 440.0, "y": 0.0}),
            *extra,
        ],
        transitions=[TransitionModel(trigger="advance", source="t_1", dest="t_2", when="SUCCESS")],
        template_meta=TemplateMeta(blurb="Farms one boss."),
    )


@pytest.fixture
def store(tmp_path):
    save_draft(_draft(), tmp_path)
    return tmp_path


@pytest.fixture
def harness(qapp, store, tmp_path):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    library = SubgraphLibrary(tmp_path / "library")
    canvas = BotCanvas()
    canvas.set_subgraph_library(library)
    h = ControllerHarness(canvas, library)
    h._templates_root = store
    canvas.template_save_requested.connect(lambda: h._save_bot())
    canvas.template_release_requested.connect(lambda: h._release_template())
    yield h
    canvas.prepare_for_shutdown()


def _draft_path(store) -> str:
    return str(find_template(TEMPLATE_ID, store).draft_path)


def _open(h, monkeypatch) -> None:
    """Open the draft as File › Templates…'s Edit does: as the top-level document, in Edit mode."""
    assert h._edit_template(find_template(TEMPLATE_ID, h._templates_root))
    assert h._window.bot_canvas.mode is BotMode.EDIT


# ---------------------------------------------------------------------------
# Opening
# ---------------------------------------------------------------------------


def test_edit_template_opens_the_draft_as_the_top_level_document(harness, dev, monkeypatch, store):
    _open(harness, monkeypatch)

    canvas = harness._window.bot_canvas
    assert harness._loaded_config.is_template
    assert harness._last_save_path == _draft_path(store)
    assert harness._doc_stack == [], "a draft is a whole bot, never stacked on one"
    crumb = canvas.crumb_bar
    assert crumb.mode is CrumbMode.TEMPLATE
    assert crumb.text == "Template draft · Boss Farmer"
    assert crumb.hint_text == "not released yet"
    assert crumb.save_button.text() == "Save draft"
    assert not crumb.save_button.isHidden() and not crumb.release_button.isHidden()
    assert crumb.save_as_button.isHidden() and crumb.revert_button.isHidden()
    assert not crumb.glyph_visible, "a template is copied, never linked"
    assert not canvas.crumb_bar.isHidden()
    assert canvas.shared_frame.isHidden()


def test_a_draft_offers_no_rewards_tags(harness, dev, monkeypatch):
    _open(harness, monkeypatch)

    assert not harness._window.bot_canvas.header.rewards_offered


def test_a_bot_offers_rewards_tags_and_shows_no_crumb(harness, tmp_path):
    bot = _draft()
    bot.template_meta = None
    bot.id = "a-bot"
    bot.save(tmp_path / "bot.json")

    harness._open_bot(str(tmp_path / "bot.json"))

    canvas = harness._window.bot_canvas
    assert canvas.header.rewards_offered
    assert canvas.crumb_bar.isHidden()


def test_the_users_own_draft_opens_outside_dev_mode(harness, monkeypatch, store):
    """Since #164 anyone edits their own templates; the store here is one root, so this one is Custom."""
    monkeypatch.setattr(app_controller, "dev_mode", lambda: False)

    _open(harness, monkeypatch)

    assert harness._loaded_config.is_template
    assert harness._last_save_path == _draft_path(store)


def test_a_draft_mints_past_every_id_a_release_used(harness, dev, monkeypatch, store):
    release(TEMPLATE_ID, "First", root=store)
    _open(harness, monkeypatch)
    scene = harness._window.bot_canvas.scene

    assert scene.reserved_ids == {"t_1", "t_2"}
    assert harness._window.bot_canvas.crumb_bar.hint_text == "latest v1"


# ---------------------------------------------------------------------------
# What Open Bot refuses
# ---------------------------------------------------------------------------


def test_open_bot_refuses_a_released_version(harness, dev, monkeypatch, store):
    release(TEMPLATE_ID, "First", root=store)
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a[2]))

    harness._open_bot(str(find_template(TEMPLATE_ID, store).version_path(1)))

    assert harness._loaded_config is None
    assert "released version" in told[0] and "New Bot" in told[0]


def test_open_bot_refuses_an_official_draft_outside_dev_mode(harness, monkeypatch, store, tmp_path):
    """An Official template changes only with an app update: its draft is the maintainer's (#164)."""
    monkeypatch.setattr(app_controller, "dev_mode", lambda: False)
    harness._templates_user_root = tmp_path / "user"
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a[2]))

    harness._open_bot(_draft_path(store))

    assert harness._loaded_config is None
    assert "Official" in told[0] and "Duplicate" in told[0]


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


def test_save_draft_writes_the_draft_and_releases_nothing(harness, dev, monkeypatch, store):
    _open(harness, monkeypatch)
    harness._loaded_config.name = "Boss Farmer, Revised"
    harness._has_unsaved_changes = True

    harness._window.bot_canvas.crumb_bar.save_button.click()

    assert BotConfig.load(_draft_path(store)).name == "Boss Farmer, Revised"
    assert find_template(TEMPLATE_ID, store).latest_version is None
    assert not harness._has_unsaved_changes


def test_save_as_is_refused_for_a_draft(harness, dev, monkeypatch, store, tmp_path):
    _open(harness, monkeypatch)
    monkeypatch.setattr(app_controller.QFileDialog, "getSaveFileName", lambda *a, **k: pytest.fail("asked for a file"))

    assert not harness._save_bot_as()
    assert harness._loaded_config.id == TEMPLATE_ID


def test_a_draft_autosaves_under_its_template_id(harness, dev, monkeypatch):
    _open(harness, monkeypatch)

    assert harness._autosave_stem() == f"template_{TEMPLATE_ID}"


# ---------------------------------------------------------------------------
# The crumb's way out
# ---------------------------------------------------------------------------


def test_the_close_button_asks_to_close_the_draft_and_esc_does_not(harness, dev, monkeypatch):
    _open(harness, monkeypatch)
    canvas = harness._window.bot_canvas
    closed = []
    canvas.template_close_requested.connect(lambda: closed.append(True))

    canvas.crumb_bar.close_button.click()
    assert closed == [True]

    canvas.scene.clearSelection()
    canvas._on_escape()
    assert closed == [True], "Esc never closes a whole bot, and a draft is one"


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------


def test_release_from_the_crumb_writes_the_next_version(harness, dev, monkeypatch, store):
    _open(harness, monkeypatch)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("First release", True))

    harness._window.bot_canvas.crumb_bar.release_button.click()

    template = find_template(TEMPLATE_ID, store)
    assert template.latest_version == 1
    assert template.index.latest.note == "First release"
    assert [s.id for s in load_version(TEMPLATE_ID, 1, store).states] == ["t_1", "t_2"]
    assert harness._window.bot_canvas.crumb_bar.hint_text == "latest v1"
    assert harness._window.bot_canvas.scene.reserved_ids == {"t_1", "t_2"}


def test_release_saves_unsaved_changes_first_or_does_not_happen(harness, dev, monkeypatch, store):
    _open(harness, monkeypatch)
    harness._loaded_config.name = "Boss Farmer, Revised"
    harness._has_unsaved_changes = True
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Renamed", True))

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    assert not harness._release_template()
    assert find_template(TEMPLATE_ID, store).latest_version is None

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save)
    assert harness._release_template()
    assert load_version(TEMPLATE_ID, 1, store).name == "Boss Farmer, Revised"


def test_release_refuses_a_draft_with_a_canvas_error(harness, dev, monkeypatch, store):
    save_draft(_draft(StateModel(id="t_3", node_type="not_a_node", position={"x": 880.0, "y": 0.0})), store)
    _open(harness, monkeypatch)
    refused = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: refused.append(a[2]))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: pytest.fail("asked for a note"))

    assert not harness._release_template()

    assert "Unknown node type" in refused[0]
    assert find_template(TEMPLATE_ID, store).latest_version is None


def test_cancelling_the_note_releases_nothing(harness, dev, monkeypatch, store):
    _open(harness, monkeypatch)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("", False))

    assert not harness._release_template()
    assert find_template(TEMPLATE_ID, store).latest_version is None
