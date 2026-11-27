"""The walking-map check: which zones a bot names have no map, read from the index alone."""

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from bartleby.runtime.movement.zone_maps import ZoneMaps

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.game import walking_maps, zone_names

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


def test_every_challenge_mode_copy_of_a_shipped_zone_has_a_map():
    """Challenge Mode loads a dungeon as a second zone, and a walk plans on the zone it is in. A
    bot names only the ordinary one, so neither the test above nor the check at Run sees a copy
    with no map. Rebuild core's maps with its ``tools/build_zone_maps.py``, which adds each copy
    of a zone it is given (core's ``docs/WALKING.md``, *Which zones*), and pin the release."""
    named = set()
    for path in [*(_ROOT / "bots").rglob("*.json"), *(_ROOT / "subgraphs").rglob("*.json")]:
        data = json.loads(path.read_text(encoding="utf-8"))
        named.update(walking_maps.bot_zones(StateModel.model_validate(s) for s in data.get("states", [])))
    # A copy is one the game has a name for: challenge_variant_of takes a zone read from a
    # client, so it asks only that the ordinary zone exists.
    copies = {
        copy
        for zone in named
        for suffix in zone_names.CHALLENGE_SUFFIXES
        if zone_names.display_names(copy := zone + suffix) and zone_names.challenge_variant_of(copy) == zone
    }

    assert copies, "no shipped zone has a Challenge Mode copy, so this checks nothing"
    assert sorted(copy for copy in copies if not walking_maps.MAPS.has(copy)) == []


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


# --- the warmer --------------------------------------------------------------------------------


class _Maps:
    """An index of zones, whose warm records the order it was asked in."""

    def __init__(self, zones, fail=()):
        self.index = {z: {} for z in zones}
        self.asked: list[str] = []
        self.fail = set(fail)
        self.gate = threading.Event()
        self.gate.set()

    def has(self, zone):
        return zone in self.index

    def zones(self):
        return sorted(self.index)

    def warm(self, zones, stop=None):
        self.gate.wait(5)
        for zone in zones:
            self.asked.append(zone)
            if zone in self.fail:
                raise OSError("disk full")
        return len(zones)


def _until(test, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not test():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)


def test_the_warmer_builds_the_preferred_zones_then_the_network_then_the_rest():
    maps = _Maps(["A", "B", "C", "D", "E"])
    warmer = walking_maps.MapWarmer(maps)
    warmer.prefer(["D", "Nowhere", "D*"])
    warmer.start(network=["C", "B"])
    try:
        _until(lambda: len(maps.asked) == 5)
    finally:
        warmer.stop()
    assert maps.asked == ["D", "C", "B", "A", "E"]
    assert warmer.built == 5


def test_a_bot_opened_mid_warm_jumps_the_queue():
    maps = _Maps(["A", "B", "C", "D"])
    maps.gate.clear()
    warmer = walking_maps.MapWarmer(maps)
    warmer.start()
    try:
        warmer.prefer(["D"])
        maps.gate.set()
        _until(lambda: len(maps.asked) == 4)
    finally:
        warmer.stop()
    # A was already taken when D was preferred; D is next.
    assert maps.asked[:2] in (["A", "D"], ["D", "A"])
    assert sorted(maps.asked) == ["A", "B", "C", "D"]


def test_a_zone_preferred_after_the_warm_finished_is_built_then():
    maps = _Maps(["A"])
    warmer = walking_maps.MapWarmer(maps)
    warmer.start()
    try:
        _until(lambda: maps.asked == ["A"])
        maps.index["B"] = {}
        warmer.prefer(["B"])
        _until(lambda: maps.asked == ["A", "B"])
    finally:
        warmer.stop()


def test_one_map_that_fails_to_build_does_not_stop_the_rest():
    maps = _Maps(["A", "B", "C"], fail={"B"})
    warmer = walking_maps.MapWarmer(maps)
    warmer.start()
    try:
        _until(lambda: len(maps.asked) == 3)
    finally:
        warmer.stop()
    assert maps.asked == ["A", "B", "C"]


def test_bot_zones_lists_every_zone_param_in_order():
    states = [_state("a", from_zone="X/A", to_zone="X/B"), _state("b", zone="X/C", label_text="no")]

    assert walking_maps.bot_zones(states) == ["X/A", "X/B", "X/C"]
