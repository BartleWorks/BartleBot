"""The shared strategy library: one copy at the vendor root, and two products writing it (#168).

What these hold: a file another program changed between load and save is never
overwritten, an older build never saves over a newer format, and the library's default
roots are the vendor directory and the install directory with the newest shipped set.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bartlebot.paths import project_root
from bartlebot.shared import official_set
from bartlebot.shared.official_set import InstallKey
from bartlebot.shared.strategy_library import (
    ChangedOnDisk,
    LibraryKind,
    NewerFormat,
    delete,
    entries,
    fingerprint,
    kind_dir,
    load,
    save,
)
from bartlebot.shared.strategy_models import LIBRARY_FORMAT, DeckPreset, DuelCombatStrategy, PlayerCombatStrategy
from bartlebot.shared.vendor import product_dir, vendor_dir
from bartlebot.store_roots import DEV_ENV_VAR, resolve_shared_roots

PLAYER = LibraryKind.PLAYER


def _only_file(root: Path, kind: LibraryKind = PLAYER) -> Path:
    (path,) = kind_dir(root, kind).glob("*.json")
    return path


def _edit_elsewhere(path: Path, **changes) -> None:
    """What BartleBuddy saving the same file looks like from here."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Saving checks before it writes
# ---------------------------------------------------------------------------


def test_a_save_with_the_fingerprint_it_loaded_with_is_written(tmp_path):
    s = PlayerCombatStrategy(name="Blade")
    save(tmp_path, PLAYER, s)
    entry = load(tmp_path, tmp_path, PLAYER, s.id)
    entry.item.dsl = "any<blade> @ self"

    new_fingerprint = save(tmp_path, PLAYER, entry.item, expected=entry.fingerprint)

    assert load(tmp_path, tmp_path, PLAYER, entry.item.id).item.dsl == "any<blade> @ self"
    assert new_fingerprint == fingerprint(tmp_path, PLAYER, entry.item.id)


def test_a_file_changed_by_another_program_since_load_is_never_overwritten(tmp_path):
    s = PlayerCombatStrategy(name="Blade")
    loaded_with = save(tmp_path, PLAYER, s)
    _edit_elsewhere(_only_file(tmp_path), dsl="theirs")
    theirs = _only_file(tmp_path).read_bytes()
    s.dsl = "mine"

    with pytest.raises(ChangedOnDisk) as refused:
        save(tmp_path, PLAYER, s, expected=loaded_with)

    assert refused.value.item_id == s.id
    assert _only_file(tmp_path).read_bytes() == theirs


def test_an_override_another_program_made_since_load_is_never_overwritten(tmp_path):
    """Loaded as shipped, with no user file; the other program then saved its own override."""
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    original = PlayerCombatStrategy(name="Default")
    save(shipped, PLAYER, original)
    entry = load(shipped, user, PLAYER, original.id)
    assert entry.fingerprint is None
    save(user, PLAYER, original.model_copy(update={"dsl": "theirs"}))

    with pytest.raises(ChangedOnDisk):
        save(user, PLAYER, entry.item.model_copy(update={"dsl": "mine"}), expected=entry.fingerprint)

    assert load(shipped, user, PLAYER, original.id).item.dsl == "theirs"


def test_a_file_another_program_deleted_since_load_is_not_written_back(tmp_path):
    s = PlayerCombatStrategy(name="Blade")
    loaded_with = save(tmp_path, PLAYER, s)
    _only_file(tmp_path).unlink()

    with pytest.raises(ChangedOnDisk):
        save(tmp_path, PLAYER, s, expected=loaded_with)

    assert not list(kind_dir(tmp_path, PLAYER).glob("*.json"))


def test_a_delete_of_a_file_changed_since_load_removes_nothing(tmp_path):
    s = PlayerCombatStrategy(name="Blade")
    loaded_with = save(tmp_path, PLAYER, s)
    _edit_elsewhere(_only_file(tmp_path), dsl="theirs")

    with pytest.raises(ChangedOnDisk):
        delete(tmp_path, PLAYER, s.id, expected=loaded_with)

    assert _only_file(tmp_path).exists()


def test_a_save_with_no_fingerprint_only_creates(tmp_path):
    s = PlayerCombatStrategy(name="Blade")
    save(tmp_path, PLAYER, s)

    with pytest.raises(ChangedOnDisk):
        save(tmp_path, PLAYER, s)


def test_entries_and_their_fingerprints_describe_the_same_bytes(tmp_path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    only_shipped, overridden = PlayerCombatStrategy(name="A"), PlayerCombatStrategy(name="B")
    save(shipped, PLAYER, only_shipped)
    save(shipped, PLAYER, overridden)
    save(user, PLAYER, overridden.model_copy(update={"dsl": "mine"}))

    found = entries(shipped, user, PLAYER)

    assert found[only_shipped.id].fingerprint is None
    assert found[overridden.id].item.dsl == "mine"
    assert found[overridden.id].fingerprint == fingerprint(user, PLAYER, overridden.id)


# ---------------------------------------------------------------------------
# Format versions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", [PlayerCombatStrategy, DuelCombatStrategy, DeckPreset])
def test_the_current_format_is_never_written_into_a_file(tmp_path, model):
    """Absent means 1, so no file written before the field existed reads differently or changes."""
    path = tmp_path / "item.json"
    model(name="X").save(path)
    assert "format" not in json.loads(path.read_text(encoding="utf-8"))
    assert model.load(path).format == LIBRARY_FORMAT


def _newer_file(root: Path) -> PlayerCombatStrategy:
    s = PlayerCombatStrategy(name="From the future")
    save(root, PLAYER, s)
    _edit_elsewhere(_only_file(root), format=LIBRARY_FORMAT + 1, new_field="kept")
    return s


def test_a_file_in_a_newer_format_loads_read_only(tmp_path):
    s = _newer_file(tmp_path)
    assert entries(tmp_path, tmp_path, PLAYER)[s.id].read_only
    assert load(tmp_path, tmp_path, PLAYER, s.id).item.format == LIBRARY_FORMAT + 1


def test_a_file_in_a_newer_format_is_never_saved_over(tmp_path):
    s = _newer_file(tmp_path)
    entry = load(tmp_path, tmp_path, PLAYER, s.id)
    before = _only_file(tmp_path).read_bytes()

    with pytest.raises(NewerFormat):
        save(tmp_path, PLAYER, entry.item, expected=entry.fingerprint)
    with pytest.raises(NewerFormat):
        save(tmp_path, PLAYER, entry.item.model_copy(update={"format": 1}), expected=entry.fingerprint)
    with pytest.raises(NewerFormat):
        delete(tmp_path, PLAYER, s.id, expected=entry.fingerprint)

    assert _only_file(tmp_path).read_bytes() == before
    assert json.loads(before)["new_field"] == "kept"


# ---------------------------------------------------------------------------
# Where the library is
# ---------------------------------------------------------------------------


def test_the_library_is_written_at_the_vendor_root_not_the_products():
    shipped, user = resolve_shared_roots(None, None)
    assert user == vendor_dir()
    assert user != product_dir()
    assert shipped == project_root()


def test_developer_mode_keeps_the_library_in_the_repo(monkeypatch):
    monkeypatch.setenv(DEV_ENV_VAR, "1")
    official_set.set_registry(_Keys(InstallKey("BartleBuddy", project_root().parent, 99)))
    assert resolve_shared_roots(None, None) == (project_root(), project_root())


class _Keys:
    def __init__(self, *keys: InstallKey) -> None:
        self.keys = list(keys)

    def install_keys(self) -> list[InstallKey]:
        return list(self.keys)


def test_the_default_shipped_root_is_the_newest_installed_set(monkeypatch, tmp_path):
    buddy = tmp_path / "BartleBuddy"
    buddy.mkdir()
    official_set.set_registry(_Keys(InstallKey("BartleBuddy", buddy, official_set.SHARED_SET_VERSION + 1)))
    assert resolve_shared_roots(None, None) == (buddy, vendor_dir())
