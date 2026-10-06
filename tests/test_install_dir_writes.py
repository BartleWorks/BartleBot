"""Nothing the app writes lands in the install directory (#168, #27).

An app update replaces the install directory whole, so anything written there is lost
on the next one. What ships there is the Official content; everything the app writes
goes under ``%APPDATA%/Bartleby``. The suite clears ``BARTLEBOT_DEV`` and points
``APPDATA`` at a temporary folder, so every default below is what an installed app gets.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from bartlebot import bot_roots, card_art
from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.bot.strategy_models import BotCombatStrategy, PlayerCombatStrategy
from bartlebot.logging_setup import session_log_dir
from bartlebot.paths import project_root
from bartlebot.settings_manager import SettingsManager
from bartlebot.shared.vendor import vendor_dir
from bartlebot.store_roots import is_inside, resolve_roots, resolve_shared_roots
from bartlebot.strategy_store import save_bot_strategy, save_player_strategy
from bartlebot.subgraph_store import SubgraphLibrary
from bartlebot.template_store import templates_dir
from bartlebot.ui_state import UiState
from tests.test_subgraph_integration import ControllerHarness
from tests.test_subgraph_store import _defn


def _default_write_paths() -> dict[str, Path]:
    return {
        "user root (subgraphs, Bot Combat Strategies)": resolve_roots(None, None)[1],
        "shared strategy library": resolve_shared_roots(None, None)[1],
        "template drafts and releases": templates_dir(),
        "the user's bots": bot_roots.user_bots_dir(),
        "autosaves": SettingsManager.autosave_dir(),
        "settings": SettingsManager.instance().settings_path,
        "ui state": UiState().path,
        "card art": card_art.art_dir(),
        "accounts": vendor_dir(),
        "session logs": session_log_dir(),
    }


@pytest.mark.parametrize("store", list(_default_write_paths()))
def test_no_default_write_path_falls_inside_the_install_directory(store):
    path = _default_write_paths()[store]
    assert not is_inside(path, project_root()), f"{store} writes to {path}, inside the install directory"


def test_every_store_saved_with_its_defaults_writes_outside_the_install_directory():
    """The paths above, as the stores actually use them."""
    written = [
        *_saved(lambda: save_player_strategy(PlayerCombatStrategy(name="Mine")), vendor_dir()),
        *_saved(lambda: save_bot_strategy(BotCombatStrategy(name="Mine", bot_id="b")), resolve_roots(None, None)[1]),
        *_saved(lambda: SubgraphLibrary().save(_defn(name="Mine")), resolve_roots(None, None)[1]),
    ]
    assert written, "nothing was saved, so this checks nothing"
    assert not [p for p in written if is_inside(p, project_root())]


def _saved(save, under: Path) -> list[Path]:
    before = set(under.rglob("*.json")) if under.exists() else set()
    save()
    return sorted(set(under.rglob("*.json")) - before)


# ---------------------------------------------------------------------------
# Save and Save As on an Official bot
# ---------------------------------------------------------------------------


@pytest.fixture
def install(tmp_path, monkeypatch) -> Path:
    """A stand-in install directory, with one Official bot in it."""
    root = tmp_path / "install"
    monkeypatch.setattr(bot_roots, "project_root", lambda: root)
    monkeypatch.setattr(bot_roots, "shipped_bots_dir", lambda: root / "bots")
    bot = BotConfig(
        id="official",
        name="Jade Oni",
        initial="s0",
        recovery_state="s0",
        states=[StateModel(id="s0", node_type="interact")],
        transitions=[],
    )
    bot.save(root / "bots" / "04_mooshu" / "jade-oni.json")
    return root


@pytest.fixture
def harness(qapp, tmp_path, monkeypatch):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    library = SubgraphLibrary(tmp_path / "library")
    canvas = BotCanvas()
    canvas.set_subgraph_library(library)
    yield ControllerHarness(canvas, library)
    canvas.prepare_for_shutdown()


def _answer_save_as(monkeypatch, path: Path) -> list[str]:
    offered: list[str] = []

    def answer(_parent, _title, start, _filter):
        offered.append(start)
        return str(path), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(answer))
    return offered


def test_save_as_on_an_official_bot_saves_a_copy_in_the_users_folder(install, harness, monkeypatch):
    shipped = install / "bots" / "04_mooshu" / "jade-oni.json"
    before = shipped.read_bytes()
    target = bot_roots.user_bots_dir() / "04_mooshu" / "jade-oni.json"
    offered = _answer_save_as(monkeypatch, target)
    harness._open_bot(str(shipped))

    assert not harness._save_bot(), "an Official bot is read-only: Save has nothing to write (#149)"
    assert offered == []
    assert harness._save_bot_as()

    assert offered == [str(target)], "Save As opens on the same world folder and name, in the user's folder"
    assert shipped.read_bytes() == before
    assert BotConfig.load(target).id != "official"
    assert harness._last_save_path == str(target)


def test_save_as_into_the_install_directory_is_refused(install, harness, monkeypatch):
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda _p, _t, text: told.append(text))
    _answer_save_as(monkeypatch, install / "elsewhere" / "copy.json")
    harness._open_bot(str(install / "bots" / "04_mooshu" / "jade-oni.json"))

    assert not harness._save_bot_as()

    assert not (install / "elsewhere").exists()
    assert told and "every update replaces" in told[0]
