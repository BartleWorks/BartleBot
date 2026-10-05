"""Tests for tools/scales_table.py: building a Scales of Knowledge entry from a recorded bot."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from bartlebot import scales, zone_nav  # noqa: E402
from tools import scales_table  # noqa: E402
from tools.scales_table import TableError  # noqa: E402

_HUB = "World/Hub"
_LOBBY = "World/Interiors/Lobby"
_DUNGEON = "World/Dungeon"
_SECRET = "World/Interiors/Secret"
_SIDE_ROOM = "World/Interiors/SideRoom"

_POINT = {"x": 1.0, "y": 2.0, "z": 3.0}
_RESET = {"x": 4.0, "y": 5.0, "z": 6.0}


@pytest.fixture
def nav() -> zone_nav.NavMap:
    """A map where the hub is a destination and nothing indoors is."""
    return zone_nav.parse({"worlds": {"World": {"hub": _HUB, "zones": {_HUB: {}}}}})


def _sigil(from_zone: str, to_zone: str, name: str = "The Dungeon") -> dict:
    params = {"from_zone": from_zone, "dungeon_zone": to_zone, "dungeon_name": name}
    return {"node_type": "enter_dungeon", "params": {**params, "sigil_xyz": _POINT, "reset_xyz": _RESET}}


def _walk(from_zone: str, to_zone: str) -> dict:
    params = {"from_zone": from_zone, "to_zone": to_zone, "xyz": _POINT, "reset_xyz": _RESET}
    return {"node_type": "enter_zone", "params": params}


def _station(zone: str) -> dict:
    return {"node_type": "interact", "params": {"zone": zone, "interact_xyz": _POINT, "reset_xyz": _RESET}}


def _bot(*states: dict) -> dict:
    return {"states": [{"id": f"s{i}", **state} for i, state in enumerate(states)]}


def _route(entry: dict) -> list[tuple[str, str]]:
    return [(step["kind"], step["to_zone"]) for step in entry["steps"]]


# ---------------------------------------------------------------------------
# Building an entry
# ---------------------------------------------------------------------------


def test_a_sigil_and_a_station_make_an_entry(nav):
    entry = scales_table.entry_from_bot(_bot(_sigil(_HUB, _DUNGEON), _station(_DUNGEON)), level=50, nav=nav)

    assert entry == {
        "key": "the_dungeon",
        "name": "The Dungeon",
        "world": "World",
        "level": 50,
        "entrance_zone": _HUB,
        "steps": [{"kind": "sigil", "from_zone": _HUB, "to_zone": _DUNGEON, "xyz": _POINT, "reset_xyz": _RESET}],
        "zone": _DUNGEON,
        "interact_xyz": _POINT,
        "reset_xyz": _RESET,
    }


def test_the_entry_it_builds_is_one_the_loader_reads(nav):
    entry = scales_table.entry_from_bot(_bot(_sigil(_HUB, _DUNGEON), _station(_DUNGEON)), nav=nav)

    assert scales_table.problems([entry], nav) == []


def test_the_nodes_may_be_recorded_in_any_order(nav):
    bot = _bot(_station(_SECRET), _walk(_DUNGEON, _SECRET), _sigil(_HUB, _DUNGEON))

    entry = scales_table.entry_from_bot(bot, nav=nav)

    assert _route(entry) == [("sigil", _DUNGEON), ("gateway", _SECRET)]
    assert entry["entrance_zone"] == _HUB


def test_a_sigil_indoors_is_reached_through_the_walks_before_it(nav):
    """The walk back stops at the first zone navigation can reach, not at the sigil."""
    bot = _bot(_walk(_HUB, _LOBBY), _sigil(_LOBBY, _DUNGEON), _station(_DUNGEON))

    entry = scales_table.entry_from_bot(bot, nav=nav)

    assert _route(entry) == [("gateway", _LOBBY), ("sigil", _DUNGEON)]
    assert entry["entrance_zone"] == _HUB


def test_the_sigil_is_taken_over_a_walk_back_out_of_a_side_room(nav):
    """A bot that farms the dungeon also records the way back in from each side room."""
    bot = _bot(_walk(_SIDE_ROOM, _DUNGEON), _sigil(_HUB, _DUNGEON), _station(_DUNGEON))

    assert _route(scales_table.entry_from_bot(bot, nav=nav)) == [("sigil", _DUNGEON)]


def test_a_glob_dungeon_zone_is_written_as_the_one_zone_the_scales_are_in(nav):
    """A bot globs to take in the Challenge Mode copy; the table must not, since that copy has no Scales."""
    bot = _bot(_sigil(_HUB, "World/Dungeon*"), _station(_DUNGEON))

    entry = scales_table.entry_from_bot(bot, nav=nav)

    assert _route(entry) == [("sigil", _DUNGEON)]
    assert scales_table.problems([entry], nav) == []


def test_a_pattern_in_the_table_is_a_problem(nav):
    entry = _entry(nav)
    entry["steps"][0]["to_zone"] = "World/Dungeon*"

    assert any("is a pattern" in line for line in scales_table.problems([entry], nav))


def test_a_challenge_mode_copy_in_the_table_is_a_problem(nav):
    """The node enters in normal mode, and the copy has no Scales."""
    entry = _entry(nav)
    entry["steps"][0]["to_zone"] = "Aquila/AQ_Z02_Atlantea_Hard"

    assert any("is a Challenge Mode copy" in line for line in scales_table.problems([entry], nav))


def test_the_name_key_level_and_default_can_be_given(nav):
    bot = _bot(_sigil(_HUB, _DUNGEON), _station(_DUNGEON))

    entry = scales_table.entry_from_bot(bot, key="k", name="Named", level=90, world_default=True, nav=nav)

    assert (entry["key"], entry["name"], entry["level"], entry["world_default"]) == ("k", "Named", 90, True)


def test_world_default_is_left_out_unless_set(nav):
    entry = scales_table.entry_from_bot(_bot(_sigil(_HUB, _DUNGEON), _station(_DUNGEON)), nav=nav)

    assert "world_default" not in entry


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Emperor's Throne Room", "emperors_throne_room"), ("  The Poseidon Adventure ", "the_poseidon_adventure")],
)
def test_a_key_is_made_from_the_name(name, expected):
    assert scales_table.slug(name) == expected


# ---------------------------------------------------------------------------
# Bots it cannot use
# ---------------------------------------------------------------------------


def test_a_bot_with_no_station_is_refused(nav):
    with pytest.raises(TableError, match="no interact"):
        scales_table.entry_from_bot(_bot(_sigil(_HUB, _DUNGEON)), nav=nav)


def test_an_apply_level_scaling_is_not_read_as_the_station(nav):
    """The recipe records the Scales with an interact; the old node is on its way out."""
    station = {
        "node_type": "apply_level_scaling",
        "params": {"zone": _DUNGEON, "interact_xyz": _POINT, "reset_xyz": _RESET},
    }

    with pytest.raises(TableError, match="no interact"):
        scales_table.entry_from_bot(_bot(_sigil(_HUB, _DUNGEON), station), nav=nav)


def test_a_bot_with_several_stations_asks_which(nav):
    bot = _bot(_sigil(_HUB, _DUNGEON), _station(_DUNGEON), _station(_DUNGEON))

    with pytest.raises(TableError, match="--state"):
        scales_table.entry_from_bot(bot, nav=nav)

    assert scales_table.entry_from_bot(bot, state_id="s2", nav=nav)["zone"] == _DUNGEON


def test_an_unrecorded_station_is_refused(nav):
    bot = _bot(_sigil(_HUB, _DUNGEON), {"node_type": "interact", "params": {"zone": _DUNGEON}})

    with pytest.raises(TableError, match="not recorded"):
        scales_table.entry_from_bot(bot, nav=nav)


def test_a_station_nothing_leads_into_says_what_to_record(nav):
    with pytest.raises(TableError, match=f"nothing in the bot leads into {_DUNGEON}"):
        scales_table.entry_from_bot(_bot(_station(_DUNGEON)), nav=nav)


def test_a_sigil_that_starts_somewhere_navigation_cannot_reach_says_so(nav):
    with pytest.raises(TableError, match=f"nothing in the bot leads into {_LOBBY}"):
        scales_table.entry_from_bot(_bot(_sigil(_LOBBY, _DUNGEON), _station(_DUNGEON)), nav=nav)


def test_a_route_with_no_sigil_is_refused(nav):
    """A walk straight from a destination is not a dungeon; the walk back keeps looking for the sigil."""
    with pytest.raises(TableError, match="no dungeon sigil"):
        scales_table.entry_from_bot(_bot(_walk(_HUB, _DUNGEON), _station(_DUNGEON)), nav=nav)


def test_zones_that_loop_are_refused(nav):
    bot = _bot(_walk(_SECRET, _LOBBY), _walk(_LOBBY, _SECRET), _walk(_SIDE_ROOM, _SECRET), _station(_SECRET))

    with pytest.raises(TableError):
        scales_table.entry_from_bot(bot, nav=nav)


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


def _entry(nav, key: str = "a", **given) -> dict:
    return scales_table.entry_from_bot(_bot(_sigil(_HUB, _DUNGEON), _station(_DUNGEON)), key=key, nav=nav, **given)


def test_adding_a_key_the_table_has_is_refused_unless_replacing(nav):
    old, new = _entry(nav, level=1), _entry(nav, level=2)

    with pytest.raises(TableError, match="--replace"):
        scales_table.add_entry([old], new)

    assert scales_table.add_entry([old, _entry(nav, "b")], new, replace=True)[0]["level"] == 2


def test_a_new_entry_goes_last(nav):
    assert [e["key"] for e in scales_table.add_entry([_entry(nav, "a")], _entry(nav, "b"))] == ["a", "b"]


def test_set_changes_a_label_field_and_nothing_else(nav):
    changed = scales_table.set_fields([_entry(nav, "a"), _entry(nav, "b")], "b", level=90)

    assert [e["level"] for e in changed] == [None, 90]
    with pytest.raises(TableError, match="no 'gone'"):
        scales_table.set_fields(changed, "gone", level=1)


def test_two_defaults_for_one_world_are_a_problem(nav):
    entries = [_entry(nav, "a", world_default=True), _entry(nav, "b", world_default=True)]

    assert any("already has a default" in line for line in scales_table.problems(entries, nav))


def test_an_entrance_navigation_cannot_reach_is_a_problem(nav):
    entry = {**_entry(nav), "entrance_zone": _LOBBY}

    found = scales_table.problems([entry], nav)

    assert any("not a destination" in line for line in found)
    assert any("does not start in the entrance zone" in line for line in found)


def test_a_broken_chain_a_bad_key_and_an_unreadable_entry_are_problems(nav):
    broken = {**_entry(nav, "Bad Key"), "zone": _SECRET}

    found = scales_table.problems([broken, {"key": "half"}], nav)

    assert any("does not end in the Scales' zone" in line for line in found)
    assert any("lower-case" in line for line in found)
    assert any(line.startswith("half: cannot be read") for line in found)


def test_what_is_written_reads_back_the_same_and_loads(nav, tmp_path, monkeypatch):
    path = tmp_path / "scales.json"
    entries = [_entry(nav, "a", level=30, world_default=True), _entry(nav, "b")]

    scales_table.write_entries(path, entries)

    assert scales_table.read_entries(path) == entries
    assert path.read_text(encoding="utf-8") == scales_table.render(entries)
    monkeypatch.setattr(scales, "SCALES_PATH", path)
    scales.load.cache_clear()
    try:
        assert [(e.key, e.world_default) for e in scales.load().values()] == [("a", True), ("b", False)]
    finally:
        scales.load.cache_clear()


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


@pytest.fixture
def files(tmp_path, monkeypatch, nav):
    """A scratch bot and an empty table path, with the tool reading the fixture map."""
    monkeypatch.setattr(zone_nav, "load", lambda: nav)
    bot = tmp_path / "scratch.json"
    bot.write_text(json.dumps(_bot(_sigil(_HUB, _DUNGEON), _station(_DUNGEON))), encoding="utf-8")
    return bot, tmp_path / "table.json"


def test_add_without_write_prints_the_entry_and_writes_nothing(files, capsys):
    bot, table = files

    assert scales_table.main(["--table", str(table), "add", str(bot), "--level", "50"]) == 0

    assert not table.exists()
    out = capsys.readouterr().out
    assert "the_dungeon: The Dungeon (World, level 50)" in out
    assert "Nothing written" in out


def test_add_with_write_saves_the_entry(files):
    bot, table = files

    assert scales_table.main(["--table", str(table), "add", str(bot), "--level", "50", "--write"]) == 0

    assert [e["key"] for e in scales_table.read_entries(table)] == ["the_dungeon"]


def test_adding_the_same_scales_twice_is_refused(files, capsys):
    bot, table = files
    scales_table.main(["--table", str(table), "add", str(bot), "--write"])

    assert scales_table.main(["--table", str(table), "add", str(bot), "--write"]) == 1
    assert "--replace" in capsys.readouterr().out


def test_set_and_remove_change_the_saved_table(files):
    bot, table = files
    scales_table.main(["--table", str(table), "add", str(bot), "--write"])

    assert scales_table.main(["--table", str(table), "set", "the_dungeon", "--world-default", "--write"]) == 0
    assert scales_table.read_entries(table)[0]["world_default"] is True

    assert scales_table.main(["--table", str(table), "remove", "the_dungeon", "--write"]) == 0
    assert scales_table.read_entries(table) == []


def test_check_reports_a_hand_edited_file(files, capsys):
    bot, table = files
    scales_table.main(["--table", str(table), "add", str(bot), "--write"])
    assert scales_table.main(["--table", str(table), "check"]) == 0

    table.write_text(json.dumps(json.loads(table.read_text(encoding="utf-8"))), encoding="utf-8")

    assert scales_table.main(["--table", str(table), "check"]) == 1
    assert "format --write" in capsys.readouterr().out
