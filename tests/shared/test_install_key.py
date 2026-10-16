"""Each installed product's key under ``HKCU\\Software\\Bartleby`` (#27).

These write the real registry, under a scratch key of their own that each test removes,
never under the vendor's.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from bartlebot.shared import install_key
from bartlebot.shared.install_key import InstallRecord

winreg = pytest.importorskip("winreg")


@pytest.fixture
def base():
    key = rf"Software\Bartleby-test-{uuid.uuid4().hex}"
    yield key
    install_key.delete_tree(winreg.HKEY_CURRENT_USER, key)


def _exists(path: str) -> bool:
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, path))
    except OSError:
        return False
    return True


def test_a_written_key_reads_back_with_both_values(base, tmp_path):
    install_key.write("BartleBot", tmp_path / "current" / "_internal", tmp_path / "BartleBot.exe", base=base)

    assert install_key.read_all(base=base) == [
        InstallRecord("BartleBot", tmp_path / "current" / "_internal", tmp_path / "BartleBot.exe")
    ]


def test_the_key_holds_nothing_but_the_two_values(base, tmp_path):
    install_key.write("BartleBot", tmp_path, tmp_path / "BartleBot.exe", base=base)

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{base}\BartleBot") as key:
        names = {winreg.EnumValue(key, i)[0] for i in range(winreg.QueryInfoKey(key)[1])}
    assert names == {install_key.INSTALL_DIR_VALUE, install_key.EXECUTABLE_VALUE}


def test_writing_again_replaces_the_record(base, tmp_path):
    install_key.write("BartleBot", tmp_path / "old", tmp_path / "old.exe", base=base)
    install_key.write("BartleBot", tmp_path / "new", tmp_path / "new.exe", base=base)

    assert install_key.read_all(base=base) == [InstallRecord("BartleBot", tmp_path / "new", tmp_path / "new.exe")]


def test_delete_removes_only_that_products_key(base, tmp_path):
    install_key.write("BartleBot", tmp_path, tmp_path / "BartleBot.exe", base=base)
    install_key.write("BartleBuddy", tmp_path, tmp_path / "BartleBuddy.exe", base=base)

    install_key.delete("BartleBot", base=base)

    assert [r.product for r in install_key.read_all(base=base)] == ["BartleBuddy"]


def test_deleting_the_last_product_removes_the_vendor_key_too(base, tmp_path):
    install_key.write("BartleBot", tmp_path, tmp_path / "BartleBot.exe", base=base)

    install_key.delete("BartleBot", base=base)

    assert not _exists(base)


def test_deleting_a_key_that_is_not_there_is_nothing(base):
    install_key.delete("BartleBot", base=base)

    assert install_key.read_all(base=base) == []


def test_a_key_missing_a_value_is_skipped(base, tmp_path):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base}\Half") as key:
        winreg.SetValueEx(key, install_key.INSTALL_DIR_VALUE, 0, winreg.REG_SZ, str(tmp_path))
    install_key.write("BartleBot", tmp_path, tmp_path / "BartleBot.exe", base=base)

    assert [r.product for r in install_key.read_all(base=base)] == ["BartleBot"]


def test_the_default_is_the_vendor_key_read_when_called(monkeypatch, base, tmp_path):
    monkeypatch.setattr(install_key, "REGISTRY_KEY", base)

    install_key.write("BartleBot", tmp_path, Path("x.exe"))

    assert _exists(rf"{base}\BartleBot")
    assert install_key.REGISTRY_KEY == base
