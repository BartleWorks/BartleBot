"""The walking-map check: which zones a bot names have no map, read from the index alone."""

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from bartleby.runtime.movement.zone_maps import ZoneMaps

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.game import walking_maps

_ROOT = Path(__file__).resolve().parents[2]
#: The shipped bot with the most states.
_LARGEST = _ROOT / "bots" / "13_aquila" / "mt-olympus.json"


def _state(node_id: str, **params) -> StateModel:
    return StateModel(id=node_id, node_type="enter_zone", label=node_id.title(), params=params)


@pytest.fixture
def index(monkeypatch):
    maps = SimpleNamespace(index={"WizardCity/WC_Hub": {}, "WizardCity/WC_Streets/Interiors/WC_Bank": {}})
    maps.has = lambda zone: zone in maps.index
    monkeypatch.setattr(walking_maps, "MAPS", maps)


def test_a_zone_with_no_map_is_named_with_its_node(index):
    states = [_state("a", from_zone="WizardCity/WC_Hub", to_zone="Test/T_Nowhere"), _state("b", zone="")]

    assert walking_maps.unmapped_zones(states) == [("A", "Test/T_Nowhere")]


def test_a_glob_has_a_map_when_a_mapped_zone_matches_it(index):
    states = [_state("a", zone="WizardCity/WC_Streets/*"), _state("b", zone="Test/*")]

    assert walking_maps.unmapped_zones(states) == [("B", "Test/*")]


def test_every_shipped_bot_and_subgraph_names_only_mapped_zones():
    found = []
    for path in [*(_ROOT / "bots").rglob("*.json"), *(_ROOT / "subgraphs").rglob("*.json")]:
        data = json.loads(path.read_text(encoding="utf-8"))
        states = [StateModel.model_validate(s) for s in data.get("states", [])]
        found.extend((path.name, *hit) for hit in walking_maps.unmapped_zones(states))
    assert found == []


def test_the_check_opens_no_map_and_is_fast_on_the_largest_shipped_bot(monkeypatch):
    """Its budget is 10 ms (Stage 4 of #322). Measured 2026-11-25: see docs/ZONE_NAVIGATION.md."""
    maps = ZoneMaps()
    maps.index  # noqa: B018 - read once a session, as the app does, before the clock starts
    monkeypatch.setattr(walking_maps, "MAPS", maps)
    opened = MagicMock(side_effect=AssertionError("a map was opened"))
    monkeypatch.setattr(maps, "planner", opened)
    monkeypatch.setattr(maps, "_files", opened)
    config = BotConfig.model_validate(json.loads(_LARGEST.read_text(encoding="utf-8")))

    started = time.perf_counter()
    found = walking_maps.unmapped_zones(config.states)
    elapsed = time.perf_counter() - started

    assert found == []
    assert elapsed < 0.010
    opened.assert_not_called()


def _controller(config):
    from bartlebot.controller.run import Run  # noqa: PLC0415

    host = SimpleNamespace(_documents=SimpleNamespace(loaded_config=config), _window=None)
    return Run._check_walking_maps.__get__(host)


def test_run_refuses_a_bot_naming_a_zone_with_no_map(index, monkeypatch):
    import bartlebot.controller.run as run_module  # noqa: PLC0415

    shown = []
    monkeypatch.setattr(run_module.QMessageBox, "critical", lambda parent, title, text: shown.append((title, text)))
    config = BotConfig(
        id="b",
        name="B",
        initial="a",
        recovery_state="a",
        states=[_state("a", from_zone="WizardCity/WC_Hub", to_zone="Test/T_Nowhere")],
        transitions=[],
    )

    assert _controller(config)() is False
    assert shown and shown[0][0] == "No Walking Map"
    assert "• A: Test/T_Nowhere" in shown[0][1]


def test_run_lets_a_bot_whose_zones_all_have_maps_through(index, monkeypatch):
    import bartlebot.controller.run as run_module  # noqa: PLC0415

    monkeypatch.setattr(run_module.QMessageBox, "critical", MagicMock(side_effect=AssertionError("refused")))
    config = BotConfig(
        id="b",
        name="B",
        initial="a",
        recovery_state="a",
        states=[_state("a", from_zone="WizardCity/WC_Hub", to_zone="WizardCity/WC_Hub")],
        transitions=[],
    )

    assert _controller(config)() is True
