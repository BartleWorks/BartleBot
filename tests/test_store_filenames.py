"""Tests for the readable filenames in bartlebot.shared.store_files (#99).

Files are named after the item and found by the id inside them, so what is under
test is that the two never disagree in a way a caller can see: a rename moves the
file, a collision numbers it, a duplicate or a pre-#99 file still resolves, and
shadowing still matches by id when the two roots name one item differently.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import pytest

from bartlebot.combat.strategy_store import (
    Origin,
    StrategyKind,
    delete_player_strategy,
    fingerprint,
    list_player_strategies,
    load_player_strategy,
    origin_of,
    save_duel_strategy,
    save_player_strategy,
)
from bartlebot.paths import project_root
from bartlebot.shared.strategy_models import DuelCombatStrategy, PlayerCombatStrategy
from bartlebot.store_roots import declared_id, find_by_id, index_dir, save_by_id, slugify
from bartlebot.subgraph_store import SubgraphLibrary
from tests.test_subgraph_store import _defn


def _player_dir(root: Path) -> Path:
    return root / "strategies" / "player"


def _fp(item_id: str, *roots: Path) -> str | None:
    """What a player strategy loads with now: the fingerprint a later save or delete passes."""
    return fingerprint(StrategyKind.PLAYER, item_id, *roots)


def _names(d: Path) -> list[str]:
    return sorted(p.name for p in d.glob("*.json"))


def _write_raw(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _age(path: Path, seconds: int) -> None:
    """Push ``path``'s mtime into the past, so "newer" does not depend on clock resolution."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns - seconds * 1_000_000_000))


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Default Team Boss Duel (No Feints)", "default-team-boss-duel-no-feints"),
        ("Default Turn 1 Scald-Incindiate", "default-turn-1-scald-incindiate"),
        ("  Storm AoE  ", "storm-aoe"),
        ("Pokémon Façade", "pokemon-facade"),
        ("a/b\\c:d*e?f", "a-b-c-d-e-f"),
        ("trailing dots...", "trailing-dots"),
        ("★★★", ""),
    ],
)
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_slugify_caps_the_length_without_a_trailing_hyphen():
    slug = slugify("word " * 40)
    assert len(slug) <= 60
    assert not slug.endswith("-")


# ---------------------------------------------------------------------------
# Naming on save
# ---------------------------------------------------------------------------


def test_save_names_the_file_after_the_item(tmp_path):
    save_player_strategy(PlayerCombatStrategy(name="Default Support"), tmp_path)
    assert _names(_player_dir(tmp_path)) == ["default-support.json"]


def test_rename_moves_the_file(tmp_path):
    s = PlayerCombatStrategy(name="Old Name")
    save_player_strategy(s, tmp_path)
    s.name = "New Name"
    save_player_strategy(s, tmp_path, expected=_fp(s.id, tmp_path))

    assert _names(_player_dir(tmp_path)) == ["new-name.json"]
    assert load_player_strategy(s.id, tmp_path).name == "New Name"


def test_colliding_names_are_numbered(tmp_path):
    a, b, c = (PlayerCombatStrategy(name=n) for n in ("Support", "support", "SUPPORT!"))
    for s in (a, b, c):
        save_player_strategy(s, tmp_path)

    assert _names(_player_dir(tmp_path)) == ["support-2.json", "support-3.json", "support.json"]
    assert {s.name for s in list_player_strategies(tmp_path)} == {"Support", "support", "SUPPORT!"}


def test_a_numbered_file_keeps_its_name_when_the_plain_one_frees_up(tmp_path):
    first, second = PlayerCombatStrategy(name="Support"), PlayerCombatStrategy(name="Support")
    save_player_strategy(first, tmp_path)
    save_player_strategy(second, tmp_path)
    delete_player_strategy(first.id, tmp_path, expected=_fp(first.id, tmp_path))

    save_player_strategy(second, tmp_path, expected=_fp(second.id, tmp_path))

    assert _names(_player_dir(tmp_path)) == ["support-2.json"]


def test_a_name_with_nothing_filename_safe_falls_back_to_the_id(tmp_path):
    s = PlayerCombatStrategy(id="abc-123", name="★★★")
    save_player_strategy(s, tmp_path)
    assert _names(_player_dir(tmp_path)) == ["abc-123.json"]


def test_a_windows_device_name_is_never_a_stem(tmp_path):
    save_player_strategy(PlayerCombatStrategy(name="CON"), tmp_path)
    assert _names(_player_dir(tmp_path)) == ["con-2.json"]


def test_a_failed_write_keeps_the_old_file(tmp_path):
    d = tmp_path / "d"
    s = PlayerCombatStrategy(name="Old")
    save_by_id(d, s.id, s.name, s.save)

    def fail(path: Path) -> None:
        raise OSError("disk full")

    with pytest.raises(OSError):
        save_by_id(d, s.id, "New", fail)

    assert _names(d) == ["old.json"]


# ---------------------------------------------------------------------------
# Files from before #99, and files that disagree
# ---------------------------------------------------------------------------


def test_a_legacy_id_named_file_loads_and_is_renamed_on_save(tmp_path):
    s = PlayerCombatStrategy(name="Legacy")
    s.save(_player_dir(tmp_path) / f"{s.id}.json")

    assert load_player_strategy(s.id, tmp_path).name == "Legacy"
    save_player_strategy(s, tmp_path, expected=_fp(s.id, tmp_path))
    assert _names(_player_dir(tmp_path)) == ["legacy.json"]


def test_a_file_without_an_id_is_indexed_by_its_stem(tmp_path):
    _write_raw(tmp_path / "some-id.json", {"name": "No id"})
    assert declared_id(tmp_path / "some-id.json") == "some-id"
    assert find_by_id(tmp_path, "some-id") == tmp_path / "some-id.json"


def test_an_unparseable_file_is_indexed_by_its_stem(tmp_path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert index_dir(tmp_path) == {"broken": tmp_path / "broken.json"}


def test_two_files_with_one_id_resolve_to_the_newer_with_a_warning(tmp_path, caplog):
    d = _player_dir(tmp_path)
    old = PlayerCombatStrategy(name="Before")
    old.save(d / f"{old.id}.json")
    _age(d / f"{old.id}.json", 60)
    PlayerCombatStrategy(id=old.id, name="After").save(d / "after.json")

    with caplog.at_level(logging.WARNING, logger="bartlebot.shared.store_files"):
        assert load_player_strategy(old.id, tmp_path).name == "After"
    assert "declare id" in caplog.text
    assert [s.name for s in list_player_strategies(tmp_path)] == ["After"]


def test_delete_removes_every_file_declaring_the_id(tmp_path):
    d = _player_dir(tmp_path)
    s = PlayerCombatStrategy(name="Twice")
    s.save(d / "one.json")
    s.save(d / "two.json")

    delete_player_strategy(s.id, tmp_path, expected=_fp(s.id, tmp_path))

    assert _names(d) == []


def test_saving_clears_a_duplicate_left_by_an_interrupted_rename(tmp_path):
    d = _player_dir(tmp_path)
    s = PlayerCombatStrategy(name="New")
    s.save(d / "old.json")
    s.save(d / "new.json")

    save_player_strategy(s, tmp_path, expected=_fp(s.id, tmp_path))

    assert _names(d) == ["new.json"]


def test_a_hand_edit_to_the_id_is_seen(tmp_path):
    path = tmp_path / "item.json"
    _write_raw(path, {"id": "first"})
    assert find_by_id(tmp_path, "first") == path

    _write_raw(path, {"id": "second-and-longer"})

    assert find_by_id(tmp_path, "first") is None
    assert find_by_id(tmp_path, "second-and-longer") == path


def test_loading_a_missing_id_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_player_strategy("nope", tmp_path)


# ---------------------------------------------------------------------------
# Shadowing is by id, not by filename
# ---------------------------------------------------------------------------


def test_a_renamed_user_copy_still_shadows_and_reverts(tmp_path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    original = DuelCombatStrategy(name="Default Duel")
    save_duel_strategy(original, shipped)
    save_duel_strategy(original.model_copy(update={"name": "My Duel"}), root=shipped, user_root=user)

    assert _names(user / "strategies" / "duel") == ["my-duel.json"]
    assert origin_of(StrategyKind.DUEL, original.id, shipped, user) is Origin.SHIPPED_OVERRIDE


def test_revert_finds_a_renamed_user_copy(tmp_path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    original = PlayerCombatStrategy(name="Default")
    save_player_strategy(original, shipped)
    save_player_strategy(original.model_copy(update={"name": "Mine"}), root=shipped, user_root=user)
    assert load_player_strategy(original.id, shipped, user).name == "Mine"

    delete_player_strategy(original.id, shipped, user, expected=_fp(original.id, shipped, user))

    assert load_player_strategy(original.id, shipped, user).name == "Default"
    assert origin_of(StrategyKind.PLAYER, original.id, shipped, user) is Origin.SHIPPED
    assert _names(_player_dir(shipped)) == ["default.json"]


def test_subgraph_rename_moves_the_file_and_keeps_the_id(tmp_path):
    library = SubgraphLibrary(tmp_path)
    library.save(_defn(name="Sync players"))
    library.save(_defn(name="Sync all players"))

    assert _names(tmp_path / "subgraphs") == ["sync-all-players.json"]
    assert library.get("sg-a").name == "Sync all players"


_SHIPPED_DIRS = ["subgraphs", "strategies/player", "strategies/duel", "strategies/decks", "bot_strategies"]


@pytest.mark.parametrize("rel", _SHIPPED_DIRS)
def test_no_two_shipped_files_declare_one_id(rel):
    """A file copied by hand to start a variant keeps its source's id, and one silently hides the other."""
    d = project_root() / rel
    ids = [declared_id(p) for p in d.glob("*.json")]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"{rel}: more than one file declares {dupes}; give the copy a fresh uuid4"


@pytest.mark.parametrize("rel", _SHIPPED_DIRS)
def test_every_shipped_file_is_named_after_its_item(rel):
    """What #99 is for. A file added by hand under its id, or renamed only inside, fails here."""
    d = project_root() / rel
    wrong = []
    for p in d.glob("*.json"):
        name = json.loads(p.read_text(encoding="utf-8")).get("name", "")
        slug = slugify(name)
        if not re.fullmatch(rf"{re.escape(slug)}(-\d+)?", p.stem):
            wrong.append(f"{p.name} (expected {slug}.json)")
    assert not wrong, f"{rel}: re-save these under BARTLEBOT_DEV, or rename them: {', '.join(wrong)}"


def test_subgraph_load_error_is_keyed_by_the_declared_id(tmp_path):
    _write_raw(tmp_path / "subgraphs" / "readable-name.json", {"id": "sg-x", "name": "X", "states": [{"id": "a-b"}]})
    library = SubgraphLibrary(tmp_path)
    assert library.get("sg-x") is None
    assert library.error_for("sg-x")
