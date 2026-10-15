"""Card art extraction from a made-up install: what it writes, and that it never leaves half (epic #215, Stage 7, #232).

The install is ``tests/art_fixture.py``'s: real KIWAD archives and a real deserializer,
with synthetic contents, so nothing of the game's is in the repo.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from bartlebot.game import art_extraction, card_art
from bartlebot.game.art_extraction import (
    STEP_ICONS,
    STEP_INDEXING,
    STEP_READING,
    STEP_SAVING,
    STEP_SPELLS,
    ExtractionCancelled,
    ExtractionError,
    extract_art,
    installed_game_data_dir,
    type_dump,
)
from bartlebot.game.card_art import INDEX_FILENAME
from bartlebot.paths import project_root
from bartlebot.shared.game_install import set_install_dir
from bartlebot.shared.preferences import PreferenceStore
from tests.art_fixture import ICON_KEYS, SPELLS, game_install
from tools import extract_card_art


@pytest.fixture
def game(tmp_path):
    return game_install(tmp_path / "game")


def _files(folder: Path) -> dict[str, bytes]:
    return {str(path.relative_to(folder)): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


def test_each_image_is_written_once_and_every_template_finds_it(game, tmp_path):
    out = tmp_path / "art"
    result = extract_art(game.game_data, types_path=game.types, out_dir=out)

    index = json.loads((out / INDEX_FILENAME).read_text(encoding="utf-8"))
    assert index["images"] == {
        "Fixture Bolt": "Spell_Fixture_Bolt.png",
        "Fixture Bolt - T02": "Spell_Fixture_Bolt.png",
        "Fixture Ward": "Spell_Fixture_Ward.png",
    }
    assert sorted(p.name for p in (out / "spells").iterdir()) == ["Spell_Fixture_Bolt.png", "Spell_Fixture_Ward.png"]
    assert Image.open(out / "spells" / "Spell_Fixture_Bolt.png").mode == "RGBA"
    assert (result.templates, result.files) == (3, 2)
    assert result.size_bytes > 0


def test_a_texture_no_archive_holds_costs_that_card_its_art_and_nothing_else(game, tmp_path):
    result = extract_art(game.game_data, types_path=game.types, out_dir=tmp_path / "art")

    assert result.index["missing"]["Fixture Ghost"] == SPELLS["Fixture Ghost"]
    assert result.failed == 0


def test_the_icons_the_archives_hold_are_written_by_key(game, tmp_path):
    out = tmp_path / "art"
    result = extract_art(game.game_data, types_path=game.types, out_dir=out)

    for key in ICON_KEYS:
        assert result.index["icons"][key] == f"{key}.png"
        assert (out / "icons" / f"{key}.png").is_file()


def test_the_tool_and_the_app_write_the_same_files(game, tmp_path):
    """One function behind both: the command line is only its arguments and its output."""
    by_tool = tmp_path / "tool"
    by_app = tmp_path / "app"

    code = extract_card_art.main(["--game-dir", str(game.game_data), "--types", str(game.types), "--out", str(by_tool)])
    extract_art(game.game_data, types_path=game.types, out_dir=by_app)

    assert code == 0
    assert _files(by_tool) == _files(by_app)
    assert _files(by_app)


def test_steps_are_reported_in_order(game, tmp_path):
    seen: list[str] = []
    extract_art(game.game_data, lambda p: seen.append(p.step), types_path=game.types, out_dir=tmp_path / "art")

    steps = list(dict.fromkeys(seen))
    assert steps == [STEP_READING, STEP_INDEXING, STEP_SPELLS, STEP_ICONS, STEP_SAVING]


def test_a_run_replaces_the_art_before_it_and_leaves_no_partial_folder(game, tmp_path):
    out = tmp_path / "art"
    (out / "spells").mkdir(parents=True)
    (out / "spells" / "Stale.png").write_bytes(b"old")

    extract_art(game.game_data, types_path=game.types, out_dir=out)

    assert not (out / "spells" / "Stale.png").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["art", "game"]


@pytest.mark.parametrize("stop_after", [1, 3, 6])
def test_cancelling_leaves_the_art_before_it_and_nothing_half_written(game, tmp_path, stop_after):
    out = tmp_path / "art"
    out.mkdir()
    (out / INDEX_FILENAME).write_text('{"images": {}}', encoding="utf-8")
    before = _files(out)
    asked = 0

    def cancelled() -> bool:
        nonlocal asked
        asked += 1
        return asked >= stop_after

    with pytest.raises(ExtractionCancelled):
        extract_art(game.game_data, types_path=game.types, out_dir=out, cancelled=cancelled)

    assert _files(out) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["art", "game"]


def test_a_failure_part_way_leaves_the_art_before_it(game, tmp_path, monkeypatch):
    out = tmp_path / "art"
    out.mkdir()
    (out / INDEX_FILENAME).write_text('{"images": {}}', encoding="utf-8")

    def broken(_game_dir):
        raise OSError("the archive went away")

    monkeypatch.setattr(art_extraction, "index_textures", broken)
    with pytest.raises(OSError):
        extract_art(game.game_data, types_path=game.types, out_dir=out)

    assert (out / INDEX_FILENAME).read_text(encoding="utf-8") == '{"images": {}}'
    assert not (tmp_path / "art.partial").exists()


def test_a_folder_without_root_wad_is_refused_with_a_reason(tmp_path):
    with pytest.raises(ExtractionError, match="Root.wad"):
        extract_art(tmp_path, out_dir=tmp_path / "art")


def test_a_missing_type_list_is_refused_with_a_reason(game, tmp_path):
    with pytest.raises(ExtractionError, match="type list"):
        extract_art(game.game_data, types_path=tmp_path / "nope.json", out_dir=tmp_path / "art")


def test_a_dry_run_writes_nothing(game, tmp_path):
    result = extract_art(game.game_data, types_path=game.types, out_dir=tmp_path / "art", dry_run=True)

    assert result.templates == 3
    assert not (tmp_path / "art").exists()


def test_new_art_is_seen_at_once_in_a_running_app(game):
    """The run ends by forgetting the cached index, so lookups read the new one."""
    assert card_art.art_path("Fixture Bolt") is None

    extract_art(game.game_data, types_path=game.types)

    assert card_art.art_path("Fixture Bolt") == card_art.spells_dir() / "Spell_Fixture_Bolt.png"


def test_it_reads_the_game_from_the_folder_the_app_launches_from(game, tmp_path):
    prefs = PreferenceStore(tmp_path / "vendor")
    assert installed_game_data_dir(prefs) is None

    set_install_dir(prefs, game.install)

    assert installed_game_data_dir(prefs) == game.game_data


def test_the_newest_type_dump_is_used(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("r9_WizardDev.json", "r800294_WizardDev.json", "r81_WizardDev.json", "notes.json"):
        (tools / name).write_text("{}", encoding="utf-8")

    assert type_dump(tmp_path) == tools / "r800294_WizardDev.json"
    assert type_dump(tmp_path / "elsewhere") is None


def test_bartlebots_own_folder_holds_a_type_dump():
    """The app reads it from its install directory, so one has to ship there."""
    found = type_dump()

    assert found is not None
    assert found.parent == project_root() / "tools"
