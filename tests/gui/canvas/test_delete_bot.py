"""Delete Bot… on a library card, and the controller behind it (#268)."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMessageBox

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.combat.bot_strategy_models import BotCombatStrategy
from bartlebot.combat.strategy_store import list_bot_strategies, save_bot_strategy
from bartlebot.content import bot_roots, store_roots
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary
from bartlebot.gui.canvas.bot_canvas import BotCanvas, _BotLibraryPanel
from bartlebot.settings_manager import SettingsManager
from tests.content.subgraphs.test_subgraph_integration import ControllerHarness

DELETE_TEXT = "Delete Bot…"


def _bot(bot_id: str = "mine", name: str = "Jade Oni") -> BotConfig:
    return BotConfig(
        id=bot_id,
        name=name,
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="relog")],
        transitions=[],
    )


def _action(menu, text: str):
    return next(a for a in menu.actions() if a.text() == text)


@pytest.fixture
def folders(tmp_path, monkeypatch):
    """An install directory and a user folder, kept apart, with both set as the bot roots."""
    shipped, user = tmp_path / "install" / "bots", tmp_path / "user" / "bots"
    shipped.mkdir(parents=True)
    user.mkdir(parents=True)
    monkeypatch.delenv(store_roots.DEV_ENV_VAR, raising=False)
    monkeypatch.setattr(bot_roots, "shipped_bots_dir", lambda: shipped)
    monkeypatch.setattr(bot_roots, "user_bots_dir", lambda configured=None: user)
    return shipped, user


def test_a_users_bot_card_offers_delete(qapp, folders):
    _, user = folders
    path = user / "mine.json"
    _bot().save(path)
    panel = _BotLibraryPanel()
    asked: list[str] = []
    panel.delete_bot_requested.connect(asked.append)

    action = _action(panel.bot_menu(path), DELETE_TEXT)
    assert action.isEnabled()
    action.trigger()

    assert asked == [str(path)]


def test_an_official_bots_delete_is_grayed_out_with_the_reason(qapp, folders):
    shipped, _ = folders
    path = shipped / "official.json"
    _bot("official").save(path)

    action = _action(_BotLibraryPanel().bot_menu(path), DELETE_TEXT)

    assert not action.isEnabled()
    assert "Official bots can't be deleted" in action.toolTip()


@pytest.fixture
def harness(qapp, tmp_path, folders):
    canvas = BotCanvas()
    yield ControllerHarness(canvas, SubgraphLibrary(tmp_path / "library"))
    canvas.prepare_for_shutdown()


def _answer(monkeypatch, reply):
    asked: list[str] = []

    def question(*args, **kwargs):
        asked.append(args[2])
        return reply

    monkeypatch.setattr(QMessageBox, "question", question)
    return asked


def test_the_confirmation_names_the_bot_and_counts_its_strategies(harness, folders, monkeypatch):
    _, user = folders
    path = user / "mine.json"
    _bot().save(path)
    save_bot_strategy(BotCombatStrategy(name="Solo", bot_id="mine"))
    asked = _answer(monkeypatch, QMessageBox.StandardButton.Ok)

    assert harness._delete_bot(str(path))

    assert "'Jade Oni'" in asked[0] and "1 Bot Combat Strategy" in asked[0]
    assert not path.exists()
    assert list_bot_strategies("mine") == []


def test_cancelling_deletes_nothing(harness, folders, monkeypatch):
    _, user = folders
    path = user / "mine.json"
    _bot().save(path)
    SettingsManager.instance().set_option_override("mine", "safety_delay", 5.0)
    _answer(monkeypatch, QMessageBox.StandardButton.Cancel)

    assert not harness._delete_bot(str(path))

    assert path.exists()
    assert SettingsManager.instance().get_option_override("mine", "safety_delay") == 5.0


def test_an_official_bot_is_never_deleted(harness, folders, monkeypatch):
    shipped, _ = folders
    path = shipped / "official.json"
    _bot("official").save(path)
    asked = _answer(monkeypatch, QMessageBox.StandardButton.Ok)

    assert not harness._delete_bot(str(path))

    assert path.exists() and not asked


def test_the_open_bot_is_not_deleted(harness, folders, monkeypatch):
    _, user = folders
    path = user / "mine.json"
    _bot().save(path)
    told: list[tuple] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a))
    asked = _answer(monkeypatch, QMessageBox.StandardButton.Ok)
    harness._last_save_path = str(path)

    assert not harness._delete_bot(str(path))

    assert path.exists() and told and not asked


def test_a_deleted_bots_recovery_file_goes_with_it(harness, folders, monkeypatch):
    _, user = folders
    path = user / "mine.json"
    _bot().save(path)
    recovery = SettingsManager.autosave_dir() / "mine.autosave.json"
    recovery.parent.mkdir(parents=True, exist_ok=True)
    recovery.write_text("{}", encoding="utf-8")
    _answer(monkeypatch, QMessageBox.StandardButton.Ok)

    assert harness._delete_bot(str(path))

    assert not recovery.exists()
