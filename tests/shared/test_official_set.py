"""Which install directory's copy of the shared Official set is read (#168, #27).

The rule: the higher set version is used whole, the running product's own copy on a
tie, and a product whose key is gone (uninstalled) or whose folder is missing is
passed over, so the other falls back to its own set. Each set's version is read from
the ``official_set.json`` beside it, never from the registry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bartlebot.paths import project_root
from bartlebot.shared import install_key, official_set
from bartlebot.shared.install_key import InstallRecord
from bartlebot.shared.official_set import SET_FILE, InstallKey, NoRegistry, set_version, shipped_root

#: The version of the running product's own set, in these tests.
SHARED_SET_VERSION = 8


def _ship_set(root: Path, version: object) -> Path:
    """Give ``root`` a shared set that says ``version``."""
    path = root / SET_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": version}), encoding="utf-8")
    return root


class _Keys:
    """A registry holding exactly these install keys."""

    def __init__(self, *keys: InstallKey) -> None:
        self.keys = list(keys)

    def install_keys(self) -> list[InstallKey]:
        return list(self.keys)


@pytest.fixture
def own(tmp_path) -> Path:
    return _ship_set(tmp_path / "BartleBot", SHARED_SET_VERSION)


def _installed(tmp_path: Path, product: str, version: int) -> InstallKey:
    path = tmp_path / product
    path.mkdir(exist_ok=True)
    return InstallKey(product, path, version)


def test_with_nothing_else_installed_the_own_set_is_used(own):
    assert shipped_root(own, reg=NoRegistry()) == own


def test_a_newer_set_from_the_other_product_is_used_whole(own, tmp_path):
    buddy = _installed(tmp_path, "BartleBuddy", SHARED_SET_VERSION + 1)
    assert shipped_root(own, reg=_Keys(buddy)) == buddy.install_dir


@pytest.mark.parametrize("version", [SHARED_SET_VERSION, SHARED_SET_VERSION - 1])
def test_an_equal_or_older_set_leaves_the_own_one_in_use(own, tmp_path, version):
    """A set's version decides, not which was installed last: reinstalling the older product changes nothing."""
    buddy = _installed(tmp_path, "BartleBuddy", version)
    assert shipped_root(own, reg=_Keys(buddy)) == own


def test_the_highest_of_several_sets_is_used(own, tmp_path):
    keys = _Keys(
        _installed(tmp_path, "BartleBuddy", SHARED_SET_VERSION + 1),
        _installed(tmp_path, "BartleBuilder", SHARED_SET_VERSION + 3),
        _installed(tmp_path, "Other", SHARED_SET_VERSION + 2),
    )
    assert shipped_root(own, reg=keys) == tmp_path / "BartleBuilder"


def test_when_the_other_product_is_uninstalled_the_own_set_comes_back(own, tmp_path):
    registry = _Keys(_installed(tmp_path, "BartleBuddy", SHARED_SET_VERSION + 1))
    assert shipped_root(own, reg=registry) == tmp_path / "BartleBuddy"

    registry.keys.clear()  # uninstall removes its key

    assert shipped_root(own, reg=registry) == own


def test_a_key_whose_folder_is_gone_is_passed_over(own, tmp_path):
    stale = InstallKey("BartleBuddy", tmp_path / "removed", SHARED_SET_VERSION + 1)
    assert shipped_root(own, reg=_Keys(stale)) == own


def test_the_running_products_own_key_never_redirects_it(own, tmp_path):
    """A checkout run beside an installed copy of itself reads its own files, not the install's."""
    installed_self = _installed(tmp_path, official_set.PRODUCT, SHARED_SET_VERSION + 5)
    assert shipped_root(own, reg=_Keys(installed_self)) == own


def test_the_registry_can_be_swapped_and_restored(monkeypatch):
    monkeypatch.setattr(official_set, "_registry", None)
    fake = _Keys()
    official_set.set_registry(fake)
    assert official_set.registry() is fake
    official_set.set_registry(None)
    assert official_set.registry() is not fake


def test_the_windows_registry_reads_nothing_when_no_vendor_key_exists(monkeypatch):
    """The real reader, against a key that is not there: no install keys, no error."""
    pytest.importorskip("winreg")
    monkeypatch.setattr(install_key, "REGISTRY_KEY", r"Software\Bartleby-test-key-that-does-not-exist")
    assert official_set.WindowsRegistry().install_keys() == []


def _records(monkeypatch, *records: InstallRecord) -> None:
    monkeypatch.setattr(install_key, "read_all", lambda **_: list(records))


def test_each_install_keys_version_is_read_from_its_directory(monkeypatch, tmp_path):
    buddy = _ship_set(tmp_path / "BartleBuddy", 11)
    _records(monkeypatch, InstallRecord("BartleBuddy", buddy, buddy / "BartleBuddy.exe"))

    assert official_set.WindowsRegistry().install_keys() == [InstallKey("BartleBuddy", buddy, 11)]


def test_an_install_key_whose_directory_is_gone_is_skipped(monkeypatch, tmp_path):
    _records(monkeypatch, InstallRecord("BartleBuddy", tmp_path / "removed", tmp_path / "removed" / "x.exe"))

    assert official_set.WindowsRegistry().install_keys() == []


def test_an_install_whose_set_says_no_version_is_skipped(monkeypatch, tmp_path):
    bare = tmp_path / "BartleBuddy"
    bare.mkdir()
    _records(monkeypatch, InstallRecord("BartleBuddy", bare, bare / "BartleBuddy.exe"))

    assert official_set.WindowsRegistry().install_keys() == []


def test_with_two_install_keys_the_higher_directory_version_is_used_whole(monkeypatch, own, tmp_path):
    """Done when (#237): the version comes from each install's ``official_set.json``."""
    older = _ship_set(tmp_path / "Older", SHARED_SET_VERSION - 1)
    newer = _ship_set(tmp_path / "Newer", SHARED_SET_VERSION + 2)
    _records(
        monkeypatch,
        InstallRecord("Older", older, older / "Older.exe"),
        InstallRecord("Newer", newer, newer / "Newer.exe"),
        InstallRecord("Gone", tmp_path / "gone", tmp_path / "gone" / "Gone.exe"),
    )

    assert shipped_root(own, reg=official_set.WindowsRegistry()) == newer


@pytest.mark.parametrize("content", ["{}", '{"version": "8"}', '{"version": true}', "not json"])
def test_a_set_file_that_says_no_whole_number_has_no_version(tmp_path, content):
    (tmp_path / SET_FILE).parent.mkdir(parents=True)
    (tmp_path / SET_FILE).write_text(content, encoding="utf-8")

    assert set_version(tmp_path) is None


def test_a_root_with_no_set_file_has_no_version(tmp_path):
    assert set_version(tmp_path) is None


def test_a_set_with_no_version_of_its_own_loses_to_any_other(tmp_path):
    bare = tmp_path / "BartleBot"
    bare.mkdir()
    buddy = _installed(tmp_path, "BartleBuddy", 1)

    assert shipped_root(bare, reg=_Keys(buddy)) == buddy.install_dir


def test_the_shipped_set_says_its_version():
    assert set_version(project_root()) == json.loads((project_root() / SET_FILE).read_bytes())["version"]
    assert set_version(project_root()) >= 8


def test_the_default_registry_is_read_once_per_session(tmp_path):
    own = tmp_path / "own"
    buddy = _installed(tmp_path, "BartleBuddy", SHARED_SET_VERSION + 1)
    keys = _Keys()
    official_set.set_registry(keys)
    assert shipped_root(own) == own
    keys.keys.append(buddy)  # installed while we run: the set in use does not change
    assert shipped_root(own) == own
    official_set.reset_shipped_root()
    assert shipped_root(own) == buddy.install_dir
