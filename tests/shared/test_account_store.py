"""The shared accounts file: what it holds, how it is written, and the move into it.

``accounts`` and ``launch_profiles`` are about the *account*; everything else in
settings.json is about this app. They now live one level up, at
``%APPDATA%/Bartleby/accounts.json``, so a second product by the same publisher can read
them without parsing around a bot canvas and a set of node timings.

Three things have to hold, and they are the three sections below:

* the record round-trips, and a damaged one degrades to "nothing stored" without taking
  the file with it;
* a settings file that still carries the two keys hands them over exactly once;
* ``SettingsManager.accounts`` is a facade, so every existing call site is unaffected.
"""

from __future__ import annotations

import json

import pytest
from bartleby.shared.account_store import AccountStore

from bartlebot import settings_manager as sm_mod
from bartlebot.packaging.product import NAME as PRODUCT
from bartlebot.settings_manager import SettingsManager

ONE = "one@example.com"
TWO = "two@example.com"


def _accounts(*usernames: str) -> list[dict]:
    return [{"username": u, "alias": ""} for u in usernames]


@pytest.fixture
def store(tmp_path):
    """A store in a directory of its own, so no other test's %APPDATA% is involved."""
    return AccountStore(tmp_path / "vendor")


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The migration out of settings.json
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_dir(tmp_path, monkeypatch):
    """A private settings.json and a private vendor root, both under tmp_path.

    ``APPDATA`` is redirected rather than ``_appdata_dir`` patched, because the store
    resolves the vendor root itself — patching one and not the other is what would make
    this test lie about where the two files land relative to each other.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(SettingsManager, "_instance", None)
    directory = sm_mod.product_dir(PRODUCT)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write_settings(directory, data: dict) -> None:
    (directory / "settings.json").write_text(json.dumps(data), encoding="utf-8")


def test_accounts_still_in_settings_are_moved_across_once(settings_dir, tmp_path):
    _write_settings(
        settings_dir,
        {"accounts": _accounts(ONE), "launch_profiles": [{"name": "Solo", "accounts": [ONE]}]},
    )

    manager = SettingsManager()

    assert [a["username"] for a in manager.accounts] == [ONE]
    moved = json.loads((tmp_path / "Bartleby" / "accounts.json").read_text(encoding="utf-8"))
    assert moved["launch_profiles"] == [{"name": "Solo", "accounts": [ONE]}]


def test_the_settings_file_stops_carrying_them_after_the_move(settings_dir):
    _write_settings(settings_dir, {"accounts": _accounts(ONE), "launch_profiles": []})

    manager = SettingsManager()
    manager.save()

    on_disk = json.loads((settings_dir / "settings.json").read_text(encoding="utf-8"))
    assert "accounts" not in on_disk, "two files holding accounts, with no rule about which wins"
    assert "launch_profiles" not in on_disk


def test_a_stale_settings_copy_cannot_overwrite_a_real_store(settings_dir, tmp_path):
    """The migration is one-time because it runs only when there is no shared file yet."""
    (tmp_path / "Bartleby").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Bartleby" / "accounts.json").write_text(
        json.dumps({"version": 1, "accounts": _accounts(TWO), "launch_profiles": []}), encoding="utf-8"
    )
    _write_settings(settings_dir, {"accounts": _accounts(ONE)})

    manager = SettingsManager()

    assert [a["username"] for a in manager.accounts] == [TWO]


def test_a_settings_file_with_no_accounts_writes_no_store(settings_dir, tmp_path):
    """A fresh install has nothing to move, and an empty file on disk would only mislead."""
    _write_settings(settings_dir, {"general": {"theme": "dark"}})

    SettingsManager()

    assert not (tmp_path / "Bartleby" / "accounts.json").exists()


def test_a_pre_vendor_appdata_directory_is_adopted(tmp_path, monkeypatch):
    """``%APPDATA%/BartleBot`` moves under the vendor root, auto-saves and all.

    A directory rename rather than a per-file migration, so a dev machine keeps its
    unsaved recovery files rather than paying for the rename with them.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(SettingsManager, "_instance", None)
    legacy = tmp_path / "BartleBot"
    (legacy / "autosave").mkdir(parents=True)
    (legacy / "autosave" / "bot.json").write_text("{}", encoding="utf-8")
    _write_settings(legacy, {"general": {"theme": "dark"}, "accounts": _accounts(ONE)})

    manager = SettingsManager()

    assert manager.get("general", "theme") == "dark"
    assert (tmp_path / "Bartleby" / "BartleBot" / "autosave" / "bot.json").exists()
    assert not legacy.exists()
    assert [a["username"] for a in manager.accounts] == [ONE], "the accounts move on through"


# ---------------------------------------------------------------------------
# The facade
# ---------------------------------------------------------------------------


def test_settings_accounts_read_and_write_the_store(settings_dir):
    manager = SettingsManager()

    manager.accounts = _accounts(ONE, TWO)

    assert manager.account_store.accounts == manager.accounts
    assert [a["username"] for a in AccountStore(manager.account_store.path.parent).accounts] == [ONE, TWO]


def test_the_accounts_setter_persists_without_a_settings_save(settings_dir):
    """The launch screen has no Settings dialog behind it to call save() for it."""
    manager = SettingsManager()

    manager.launch_profiles = [{"name": "Solo", "accounts": [ONE]}]

    assert AccountStore(manager.account_store.path.parent).launch_profiles[0]["name"] == "Solo"


def test_the_accounts_list_is_a_copy(settings_dir):
    """A caller that appends to what it read must not have written anything yet."""
    manager = SettingsManager()
    manager.accounts = _accounts(ONE)

    borrowed = manager.accounts
    borrowed.append({"username": TWO})

    assert [a["username"] for a in manager.accounts] == [ONE]


# ---------------------------------------------------------------------------
# Two products at once (#205)
# ---------------------------------------------------------------------------


THREE = "three@example.com"
