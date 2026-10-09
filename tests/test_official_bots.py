"""Official bots are read-only, and Make my own copy forks one; every list says where things come from (#149).

An Official bot is one in the install directory (``bot_roots.is_shipped``), which every
app update replaces. It runs as any bot does -- option values and combat setup live in
settings -- but never enters Edit mode and is never saved: asking to edit it offers Make
my own copy, which is :mod:`bartlebot.content.bot_fork` behind the controller.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QDialog, QLabel

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.combat.profile_models import BotCombatStrategy
from bartlebot.combat.strategy_store import list_bot_strategies, save_bot_strategy
from bartlebot.content import bot_roots
from bartlebot.content.store_roots import DEV_ENV_VAR, SOURCE_LABELS, Origin
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary
from bartlebot.content.templates import template_diff
from bartlebot.gui.canvas.bot_canvas import (
    BotMode,
    _BotCard,
    _BotLibraryPanel,
    _BotMeta,
    _read_bot_meta,
    filter_bot_metas,
    forked_from_text,
    name_forks,
)
from bartlebot.gui.dialogs.make_copy_dialog import MakeCopyDialog
from bartlebot.settings_manager import SettingsManager
from tests.content.subgraphs.test_subgraph_integration import ControllerHarness

OFFICIAL_ID = "official"


def _bot(bot_id: str = OFFICIAL_ID, name: str = "Jade Oni") -> BotConfig:
    return BotConfig(
        id=bot_id,
        name=name,
        initial="s0",
        recovery_state="s0",
        states=[StateModel(id="s0", node_type="interact")],
        transitions=[],
    )


@pytest.fixture
def install(tmp_path, monkeypatch) -> Path:
    """A stand-in install directory, with one Official bot in it."""
    root = tmp_path / "install"
    monkeypatch.setattr(bot_roots, "project_root", lambda: root)
    monkeypatch.setattr(bot_roots, "shipped_bots_dir", lambda: root / "bots")
    _bot().save(root / "bots" / "04_mooshu" / "jade-oni.json")
    return root


@pytest.fixture
def official_path(install) -> Path:
    return install / "bots" / "04_mooshu" / "jade-oni.json"


@pytest.fixture
def harness(qapp, tmp_path):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    library = SubgraphLibrary(tmp_path / "library")
    canvas = BotCanvas()
    canvas.set_subgraph_library(library)
    yield ControllerHarness(canvas, library)
    canvas.prepare_for_shutdown()


class _Answer:
    """Stands in for MakeCopyDialog: accepted with ``name``, or cancelled when ``name`` is None."""

    def __init__(self, name: str | None) -> None:
        self.name = name
        self.asked: list[str] = []

    def __call__(self, official_name, _parent=None):
        self.asked.append(official_name)
        return self

    def exec(self):
        return QDialog.DialogCode.Accepted if self.name is not None else QDialog.DialogCode.Rejected


@pytest.fixture
def answer(monkeypatch):
    def install_answer(name: str | None) -> _Answer:
        fake = _Answer(name)
        monkeypatch.setattr("bartlebot.app_controller.MakeCopyDialog", fake)
        return fake

    return install_answer


# ---------------------------------------------------------------------------
# Read-only
# ---------------------------------------------------------------------------


def test_an_official_bot_opens_read_only_and_in_run_mode(harness, official_path):
    harness._open_bot(str(official_path), mode=BotMode.EDIT)

    canvas = harness._window.bot_canvas
    assert canvas.is_read_only
    assert canvas.mode is BotMode.RUN, "an Official bot never opens in Edit mode"
    harness._window.set_save_enabled.assert_called_with(False, save_as=True)


def test_asking_for_edit_mode_asks_for_a_copy_instead(harness, official_path):
    harness._open_bot(str(official_path))
    canvas = harness._window.bot_canvas
    asked: list[bool] = []
    canvas.copy_requested.connect(lambda: asked.append(True))

    canvas.enter_edit_mode()
    canvas.show_setup_target("s0")

    assert asked == [True, True], "the button and a setup step both ask, and neither edits"
    assert canvas.mode is BotMode.RUN


def test_an_official_bot_is_never_marked_unsaved_or_written(harness, official_path):
    before = official_path.read_bytes()
    harness._open_bot(str(official_path))

    harness._window.bot_canvas.config_changed.emit()

    assert not harness._has_unsaved_changes
    assert not harness._save_bot()
    assert official_path.read_bytes() == before


def test_run_mode_option_values_still_apply_to_an_official_bot(harness, official_path):
    """Option values live in settings, keyed on the bot's id, so nothing of the file changes."""
    from bartlebot.bot.schema import BotOptionModel  # noqa: PLC0415
    from bartlebot.options.option_values import option_value  # noqa: PLC0415

    bot = _bot()
    bot.options = [BotOptionModel(name="laps", type="int", default=1)]
    bot.save(official_path)
    SettingsManager.instance().set_option_override(OFFICIAL_ID, "laps", 3)
    harness._open_bot(str(official_path))

    assert option_value(harness._loaded_config, "laps") == 3


def test_the_template_review_is_not_offered_on_an_official_bot(harness, official_path, tmp_path, monkeypatch):
    monkeypatch.setattr(template_diff, "load_update", lambda *_a, **_k: "update")
    offered: list[object] = []
    monkeypatch.setattr(harness._window.bot_canvas, "set_template_update", offered.append)
    user_bot = bot_roots.user_bots_dir() / "mine.json"
    _bot("mine", "Mine").save(user_bot)

    harness._open_bot(str(official_path))
    harness._open_bot(str(user_bot))

    assert offered == [None, "update"]


def test_developer_mode_edits_an_official_bot_in_place(harness, official_path, monkeypatch):
    monkeypatch.setenv(DEV_ENV_VAR, "1")

    harness._open_bot(str(official_path), mode=BotMode.EDIT)

    assert not harness._window.bot_canvas.is_read_only
    assert harness._window.bot_canvas.mode is BotMode.EDIT


def test_save_as_leaves_a_plain_copy_that_is_the_users_to_edit(harness, official_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog  # noqa: PLC0415

    target = bot_roots.user_bots_dir() / "copy.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *_a: (str(target), "")))
    SettingsManager.instance().set_option_override(OFFICIAL_ID, "laps", 3)
    harness._open_bot(str(official_path))

    assert harness._save_bot_as()

    assert not harness._window.bot_canvas.is_read_only
    copy = BotConfig.load(target)
    assert copy.forked_from is None, "Save As is unchanged: not a fork"
    assert SettingsManager.instance().get("bot_option_overrides", copy.id) is None, "and takes no option values"


def test_save_as_starts_the_copy_with_no_history_and_leaves_the_original_its_own(harness, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog  # noqa: PLC0415

    original = bot_roots.user_bots_dir() / "original.json"
    target = bot_roots.user_bots_dir() / "copy.json"
    bot = _bot("mine", "Mine")
    bot.history = ["an-older-id"]
    bot.save(original)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *_a: (str(target), "")))
    harness._open_bot(str(original))

    assert harness._save_bot_as()

    assert BotConfig.load(target).history == []
    assert BotConfig.load(original).history == ["an-older-id"]


# ---------------------------------------------------------------------------
# Make my own copy
# ---------------------------------------------------------------------------


def test_make_my_own_copy_writes_a_fork_and_opens_it_in_edit_mode(harness, official_path, answer):
    before = official_path.read_bytes()
    settings = SettingsManager.instance()
    settings.set_option_override(OFFICIAL_ID, "laps", 3)
    bcs = BotCombatStrategy(name="Solo", bot_id=OFFICIAL_ID)
    save_bot_strategy(bcs)
    settings.set_active_strategy(OFFICIAL_ID, bcs.id)
    asked = answer("My Jade Oni")
    harness._open_bot(str(official_path))
    # As AppController._wire connects it.
    harness._window.bot_canvas.copy_requested.connect(lambda: harness._make_own_copy())

    harness._window.bot_canvas.enter_edit_mode()

    target = bot_roots.user_bots_dir() / "04_mooshu" / "jade-oni.json"
    assert asked.asked == ["Jade Oni"], "the name is prefilled with the Official bot's"
    fork = BotConfig.load(target)
    assert fork.name == "My Jade Oni"
    assert fork.id != OFFICIAL_ID
    assert fork.forked_from is not None and fork.forked_from.id == OFFICIAL_ID
    assert harness._last_save_path == str(target)
    assert harness._window.bot_canvas.mode is BotMode.EDIT
    assert not harness._window.bot_canvas.is_read_only
    assert settings.get_option_override(fork.id, "laps") == 3
    [copy] = list_bot_strategies(fork.id)
    assert settings.get_active_strategy(fork.id) == copy.id
    assert official_path.read_bytes() == before


def test_cancelling_make_my_own_copy_writes_nothing(harness, official_path, answer):
    answer(None)
    harness._open_bot(str(official_path))

    assert not harness._make_own_copy()

    assert not bot_roots.user_bots_dir().exists() or not any(bot_roots.user_bots_dir().rglob("*.json"))
    assert harness._last_save_path == str(official_path)
    assert harness._window.bot_canvas.mode is BotMode.RUN


def test_make_my_own_copy_is_only_offered_on_an_official_bot(harness, answer):
    asked = answer("Mine")
    user_bot = bot_roots.user_bots_dir() / "mine.json"
    _bot("mine", "Mine").save(user_bot)
    harness._open_bot(str(user_bot))

    assert not harness._make_own_copy()
    assert asked.asked == []


def test_the_dialog_prefills_the_name_and_needs_one(qapp):
    dialog = MakeCopyDialog("Jade Oni")

    assert dialog.name == "Jade Oni"
    assert dialog.commit_button.isEnabled()
    dialog.name_edit.setText("   ")
    assert not dialog.commit_button.isEnabled()
    assert any("Official" in label.text() for label in dialog.findChildren(QLabel))


# ---------------------------------------------------------------------------
# The bot library: Source pills, the Source filter, and a copy's line
# ---------------------------------------------------------------------------


def _meta(name: str, source: Origin, **kw) -> _BotMeta:
    return _BotMeta(
        name=name,
        description="",
        authors="",
        drops=[],
        tags=[],
        version="1.0",
        path=Path(f"/{name}"),
        source=source,
        **kw,
    )


def test_the_source_filter_narrows_and_ands_with_the_tags():
    official, mine = _meta("Jade Oni", Origin.SHIPPED), _meta("Mine", Origin.USER)

    assert filter_bot_metas([official, mine], "", set(), {"Official"})[0] == [official]
    assert filter_bot_metas([official, mine], "", set(), {"Custom"})[0] == [mine]
    assert filter_bot_metas([official, mine], "", set(), {"Official", "Custom"})[0] == [official, mine]
    assert filter_bot_metas([official, mine], "", set())[0] == [official, mine], "nothing selected passes all"
    assert filter_bot_metas([official, mine], "", {"Boss"}, {"Official"})[0] == []


def test_a_bot_reads_official_from_where_it_is_never_from_the_file(official_path, tmp_path):
    claims = json.loads(official_path.read_text(encoding="utf-8")) | {"source": "Official", "official": True}
    elsewhere = tmp_path / "received.json"
    elsewhere.write_text(json.dumps(claims), encoding="utf-8")

    assert _read_bot_meta(official_path).source is Origin.SHIPPED
    assert _read_bot_meta(elsewhere).source is Origin.USER


def test_an_official_bot_never_lists_a_template_update(official_path, monkeypatch):
    monkeypatch.setattr(template_diff, "behind_in_file", lambda *_a: True)

    assert not _read_bot_meta(official_path).update_available
    assert _read_bot_meta(official_path, shipped=False).update_available


def test_a_copy_says_which_official_bot_it_came_from(qapp):
    official = _meta("Jade Oni", Origin.SHIPPED, bot_id="official")
    fork = _meta("Mine", Origin.USER, bot_id="fork", forked_from_id="official")
    stray = _meta("Stray", Origin.USER, bot_id="stray", forked_from_id="gone")

    name_forks([official, fork, stray], [official, fork, stray])

    assert fork.forked_from == "Jade Oni"
    assert stray.forked_from == "", "an Official bot not listed is not named"
    card = _BotCard(fork)
    assert card.source_text == "Custom"
    assert card.meta_text == forked_from_text("Jade Oni")


def _cards(panel: _BotLibraryPanel) -> list[_BotCard]:
    layout = panel._cards_layout
    return [w for i in range(layout.count()) if isinstance(w := layout.itemAt(i).widget(), _BotCard)]


def test_the_library_shows_each_bots_source_and_filters_by_it(qapp, official_path, harness, answer):
    answer("My Jade Oni")
    harness._open_bot(str(official_path))
    assert harness._make_own_copy()
    panel = _BotLibraryPanel()
    panel.refresh()
    panel._cat_list.setCurrentRow(
        next(i for i in range(panel._cat_list.count()) if panel._cat_list.item(i).text() == "All")
    )

    shown = {c.findChild(QLabel).text(): c for c in _cards(panel)}
    assert {name: card.source_text for name, card in shown.items()} == {"Jade Oni": "Official", "My Jade Oni": "Custom"}
    assert shown["My Jade Oni"].meta_text == forked_from_text("Jade Oni")

    panel.set_source_filter(["Official"])
    assert [c.findChild(QLabel).text() for c in _cards(panel)] == ["Jade Oni"]


def test_every_source_word_is_one_of_the_three():
    assert [SOURCE_LABELS[o] for o in (Origin.SHIPPED, Origin.SHIPPED_OVERRIDE, Origin.USER)] == [
        "Official",
        "Official (modified)",
        "Custom",
    ]


def test_the_subgraphs_folder_shows_each_definitions_source(qapp, tmp_path):
    from tests.content.subgraphs.test_subgraphs import sync_players_definition  # noqa: PLC0415

    shipped, user = tmp_path / "shipped", tmp_path / "user"
    SubgraphLibrary(shipped).save(sync_players_definition())
    SubgraphLibrary(user).save(sync_players_definition().model_copy(update={"id": "mine", "name": "My sync"}))
    panel = _BotLibraryPanel()
    panel.set_subgraph_library(SubgraphLibrary(shipped, user))
    panel.refresh()
    panel._cat_list.setCurrentRow(
        next(i for i in range(panel._cat_list.count()) if panel._cat_list.item(i).text() == "Subgraphs")
    )

    assert {c.findChild(QLabel).text(): c.source_text for c in _cards(panel)} == {
        "Sync players": "Official",
        "My sync": "Custom",
    }
    panel.set_source_filter(["Custom"])
    assert [c.findChild(QLabel).text() for c in _cards(panel)] == ["My sync"]
