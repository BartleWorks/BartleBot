"""Tests for bartlebot.store_roots — default root resolution and developer mode."""

from __future__ import annotations

import pytest

from bartlebot import store_roots
from bartlebot.bot.strategy_models import DeckPreset
from bartlebot.store_roots import DEV_ENV_VAR, Origin, dev_mode, resolve_roots
from bartlebot.strategy_store import StrategyKind, save_deck_preset
from bartlebot.strategy_store import origin_of as strategy_origin_of
from bartlebot.subgraph_store import SubgraphLibrary
from tests.test_subgraph_store import _defn


@pytest.fixture
def fake_roots(tmp_path, monkeypatch):
    """Point the default shipped and user roots at two private directories."""
    project, appdata = tmp_path / "project", tmp_path / "appdata"
    monkeypatch.setattr(store_roots, "project_root", lambda: project)
    monkeypatch.setattr(store_roots, "product_dir", lambda: appdata)
    return project, appdata


def test_the_suite_never_runs_in_dev_mode():
    assert not dev_mode(), "conftest clears BARTLEBOT_DEV so no test writes into the working tree"


@pytest.mark.parametrize(("value", "expected"), [("1", True), ("yes", True), ("0", False), ("", False)])
def test_dev_mode_reads_the_env_var(monkeypatch, value, expected):
    monkeypatch.setenv(DEV_ENV_VAR, value)
    assert dev_mode() is expected


def test_default_user_root_is_appdata(fake_roots):
    project, appdata = fake_roots
    assert resolve_roots(None, None) == (project, appdata)


def test_dev_mode_makes_the_user_root_the_project_root(fake_roots, monkeypatch):
    project, _ = fake_roots
    monkeypatch.setenv(DEV_ENV_VAR, "1")
    assert resolve_roots(None, None) == (project, project)


def test_dev_mode_leaves_explicit_roots_alone(tmp_path, monkeypatch):
    monkeypatch.setenv(DEV_ENV_VAR, "1")
    assert resolve_roots(tmp_path / "a", tmp_path / "b") == (tmp_path / "a", tmp_path / "b")
    assert resolve_roots(tmp_path / "a", None) == (tmp_path / "a", tmp_path / "a")


def test_dev_mode_subgraph_save_lands_in_the_shipped_root(fake_roots, monkeypatch):
    project, appdata = fake_roots
    monkeypatch.setenv(DEV_ENV_VAR, "1")
    library = SubgraphLibrary()
    library.save(_defn())

    assert (project / "subgraphs" / "a.json").exists()
    assert not appdata.exists()
    assert library.origin("sg-a") is not Origin.SHIPPED_OVERRIDE


def test_dev_mode_strategy_save_lands_in_the_shipped_root(fake_roots, monkeypatch):
    project, appdata = fake_roots
    monkeypatch.setenv(DEV_ENV_VAR, "1")
    preset = DeckPreset(name="Deck")
    save_deck_preset(preset)

    assert (project / "strategies" / "decks" / "deck.json").exists()
    assert not appdata.exists()
    assert strategy_origin_of(StrategyKind.DECK, preset.id) is not Origin.SHIPPED_OVERRIDE


def test_without_dev_mode_a_save_shadows_the_shipped_file(fake_roots):
    project, appdata = fake_roots
    _defn(name="Shipped").save(project / "subgraphs" / "sg-a.json")
    library = SubgraphLibrary()
    library.save(_defn(name="Mine"))

    assert (appdata / "subgraphs" / "mine.json").exists()
    assert library.origin("sg-a") is Origin.SHIPPED_OVERRIDE
