"""Which install directory's copy of the shared Official set is read (#168, #27).

The rule: the higher set version is used whole, the running product's own copy on a
tie, and a product whose key is gone (uninstalled) or whose folder is missing is
passed over, so the other falls back to its own set.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bartlebot.shared import official_set
from bartlebot.shared.official_set import SHARED_SET_VERSION, InstallKey, NoRegistry, shipped_root


class _Keys:
    """A registry holding exactly these install keys."""

    def __init__(self, *keys: InstallKey) -> None:
        self.keys = list(keys)

    def install_keys(self) -> list[InstallKey]:
        return list(self.keys)


@pytest.fixture
def own(tmp_path) -> Path:
    path = tmp_path / "BartleBot"
    path.mkdir()
    return path


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
    monkeypatch.setattr(official_set, "REGISTRY_KEY", r"Software\Bartleby-test-key-that-does-not-exist")
    assert official_set.WindowsRegistry().install_keys() == []
