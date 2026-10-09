"""The Scales of Knowledge table: the shipped data holds together, and the loader's rules."""

import json
import re
from types import SimpleNamespace

import pytest

from bartlebot.game import scales, worlds, zone_nav
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


def test_no_world_has_two_defaults():
    defaults = [e.world for e in scales.load().values() if e.world_default]

    assert defaults
    assert len(defaults) == len(set(defaults))


def test_the_shipped_table_passes_the_tools_checks_and_is_in_its_format():
    """What `tools/scales_table.py check` reports. A hand edit that reflows the file fails here."""
    import sys

    from bartlebot.paths import project_root

    sys.path.insert(0, str(project_root()))
    from tools import scales_table

    entries = scales_table.read_entries(scales.SCALES_PATH)

    assert scales_table.problems(entries) == []
    assert scales.SCALES_PATH.read_text(encoding="utf-8") == scales_table.render(entries)


def test_no_entry_names_a_challenge_mode_zone():
    """The Challenge Mode copy of a dungeon has no Scales, so no entry may lead into one."""
    from bartlebot.game import zone_names
    from bartlebot.nodes.node_helpers import has_glob

    for entry in scales.load().values():
        for zone in (entry.entrance_zone, entry.zone, *(z for s in entry.steps for z in (s.from_zone, s.to_zone))):
            assert not has_glob(zone), entry.key
            assert zone_names.challenge_variant_of(zone) is None, entry.key


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


# ---------------------------------------------------------------------------
# The default for a bot's world
# ---------------------------------------------------------------------------


def _bot(*zone_params: dict) -> SimpleNamespace:
    return SimpleNamespace(states=[SimpleNamespace(params=params) for params in zone_params])


def test_a_bots_world_is_the_one_most_of_its_zones_are_in():
    bot = _bot(
        {"zone": "Azteca/AZ_Z08_PitchBlackLake"},
        {"from_zone": "Azteca/AZ_Z08_PitchBlackLake", "dungeon_zone": "Azteca/Interiors/Pyramid"},
        {"from_zone": "Aquila/AQ_Z00_Hub", "dungeon_zone": "Aquila/AQ_Z03_Tartarus"},
    )

    assert scales.bot_world(bot) == "Azteca"


def test_only_zone_params_count_toward_a_bots_world():
    bot = _bot({"enemy": "Aquila/NotAZone", "label": "Aquila/Also", "zone": "MooShu/MS_Hub"})

    assert scales.bot_world(bot) == "MooShu"


@pytest.mark.parametrize(
    "bot",
    [_bot(), _bot({"zone": ""}), _bot({"zone": "MooShu/MS_Hub"}, {"zone": "Aquila/AQ_Z00_Hub"}), None],
    ids=["no nodes", "blank zones", "a tie", "no bot"],
)
def test_a_bot_with_no_leading_world_has_none(bot):
    assert scales.bot_world(bot) is None


def test_an_empty_scales_resolves_to_the_default_of_the_bots_world(table_file):
    _write(table_file, _raw("final", world_default=True), _raw("side"))
    bot = _bot({"zone": "Krokotopia/KT_Hub"})

    assert scales.resolve("", bot).key == "final"
    assert scales.resolve("side", bot).key == "side"


def test_an_empty_scales_resolves_to_nothing_when_the_world_marks_no_default(table_file):
    _write(table_file, _raw("side"))

    assert scales.resolve("", _bot({"zone": "Krokotopia/KT_Hub"})) is None


def test_an_empty_scales_never_borrows_another_worlds_default(table_file):
    _write(table_file, _raw("final", world_default=True))

    assert scales.resolve("", _bot({"zone": "MooShu/MS_Hub"})) is None


@pytest.mark.parametrize(("value", "known"), [("test", True), ("", True), (None, True), ("gone", False), (7, True)])
def test_is_known_passes_an_empty_value_and_refuses_a_key_the_table_lacks(table_file, value, known):
    _write(table_file, _raw())

    assert scales.is_known(value) is known


def test_is_known_passes_everything_when_the_table_cannot_be_read(table_file):
    """An unreadable table is one fault, logged once; it must not also mark every node that names a Scales."""
    assert scales.is_known("anything") is True
