"""The Scales of Knowledge table: the shipped data holds together, and the loader's rules."""

import json
import re

import pytest

from bartlebot import scales, worlds, zone_nav
from bartlebot.nodes.node_helpers import zone_matches


@pytest.fixture
def table_file(tmp_path, monkeypatch):
    """Point the loader at a file a test writes, and drop the cache either side."""
    path = tmp_path / "scales.json"
    monkeypatch.setattr(scales, "SCALES_PATH", path)
    scales.load.cache_clear()
    yield path
    scales.load.cache_clear()


def _raw(key: str = "test", **over) -> dict:
    point = {"x": 1.0, "y": 2.0, "z": 3.0}
    entry = {
        "key": key,
        "name": "Test Dungeon",
        "world": "Krokotopia",
        "level": 20,
        "entrance_zone": "A",
        "steps": [{"kind": "sigil", "from_zone": "A", "to_zone": "B", "xyz": point, "reset_xyz": point}],
        "zone": "B",
        "interact_xyz": point,
        "reset_xyz": point,
    }
    return {**entry, **over}


def _write(path, *entries: dict) -> None:
    path.write_text(json.dumps({"scales": list(entries)}), encoding="utf-8")


# ---------------------------------------------------------------------------
# The shipped table
# ---------------------------------------------------------------------------


def test_the_shipped_table_loads_every_entry_it_holds():
    """A malformed entry is skipped with a log line, which would silently drop a Scales from the picker."""
    written = json.loads(scales.SCALES_PATH.read_text(encoding="utf-8"))["scales"]

    assert written
    assert list(scales.load()) == [entry["key"] for entry in written]


def test_every_key_is_a_plain_slug():
    """A bot file stores the key, so it has to survive as one: no spaces, no capitals."""
    assert [key for key in scales.load() if not re.fullmatch(r"[a-z0-9_]+", key)] == []


def test_every_entrance_is_a_destination_zone_navigation_can_reach():
    nav = zone_nav.load()

    unreachable = [e.key for e in scales.load().values() if not zone_nav.is_destination(nav, e.entrance_zone)]

    assert unreachable == []


def test_every_entrys_steps_chain_from_its_entrance_to_its_scales():
    for entry in scales.load().values():
        assert entry.steps[0].from_zone == entry.entrance_zone, entry.key
        for before, after in zip(entry.steps, entry.steps[1:]):
            assert zone_matches(after.from_zone, before.to_zone), entry.key
        assert zone_matches(entry.zone, entry.steps[-1].to_zone), entry.key


def test_every_world_has_a_display_name():
    assert [e.key for e in scales.load().values() if not worlds.display_names(e.world)] == []


def test_no_two_entries_share_a_scales():
    stations = [(e.zone, e.interact_xyz.x, e.interact_xyz.y) for e in scales.load().values()]

    assert len(stations) == len(set(stations))


# ---------------------------------------------------------------------------
# The loader
# ---------------------------------------------------------------------------


def test_an_entry_that_cannot_be_read_is_left_out_and_the_rest_load(table_file, caplog):
    _write(table_file, _raw("good"), {"key": "broken"})

    assert list(scales.load()) == ["good"]
    assert "broken" in caplog.text


def test_an_entry_with_no_sigil_is_refused(table_file):
    """Without a sigil there is no dungeon to be inside of, and the mark decision has no answer."""
    _write(table_file, _raw(steps=[{**_raw()["steps"][0], "kind": "gateway"}]))

    assert scales.load() == {}


def test_an_unknown_step_kind_is_refused(table_file):
    sigil = _raw()["steps"][0]
    _write(table_file, _raw(steps=[sigil, {**sigil, "kind": "ride"}]))

    assert scales.load() == {}


def test_a_missing_table_loads_as_empty(table_file):
    assert scales.load() == {}
    assert scales.get("anything") is None


def test_a_level_may_be_left_unrecorded(table_file):
    _write(table_file, _raw(level=None))

    entry = scales.get("test")

    assert entry.level is None
    assert scales.describe(entry) == "Krokotopia · Test Dungeon"


def test_describe_leads_with_the_world_and_ends_with_the_level(table_file):
    _write(table_file, _raw())

    assert scales.describe(scales.get("test")) == "Krokotopia · Test Dungeon (level 20)"


def test_dungeon_from_is_the_first_sigil(table_file):
    sigil = _raw()["steps"][0]
    _write(table_file, _raw(steps=[{**sigil, "kind": "gateway"}, sigil, {**sigil, "kind": "gateway"}]))

    assert scales.get("test").dungeon_from == 1


@pytest.mark.parametrize(("value", "known"), [("test", True), ("", True), (None, True), ("gone", False), (7, True)])
def test_is_known_passes_an_empty_value_and_refuses_a_key_the_table_lacks(table_file, value, known):
    _write(table_file, _raw())

    assert scales.is_known(value) is known


def test_is_known_passes_everything_when_the_table_cannot_be_read(table_file):
    """An unreadable table is one fault, logged once; it must not also mark every node that names a Scales."""
    assert scales.is_known("anything") is True
