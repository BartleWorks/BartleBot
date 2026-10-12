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

from bartlebot import settings_manager as sm_mod
from bartlebot.settings_manager import SettingsManager
from bartlebot.shared.account_store import SCHEMA_VERSION, AccountStore

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


def test_an_empty_store_writes_nothing_until_asked(store):
    """Loading is not a reason to write. Only a user action is."""
    assert store.accounts == []
    assert store.launch_profiles == []
    assert not store.exists


def test_accounts_round_trip_through_the_file(store):
    store.set_accounts(_accounts(ONE, TWO))

    reopened = AccountStore(store.path.parent)

    assert [a["username"] for a in reopened.accounts] == [ONE, TWO]


def test_launch_profiles_round_trip_through_the_file(store):
    store.set_launch_profiles([{"name": "Duo", "accounts": [ONE, TWO], "resolution": [800, 600]}])

    reopened = AccountStore(store.path.parent)

    assert reopened.launch_profiles == [{"name": "Duo", "accounts": [ONE, TWO], "resolution": [800, 600]}]


def test_the_stored_record_is_versioned(store):
    """A reader that has to migrate later needs to know what it is holding."""
    store.set_accounts(_accounts(ONE))

    record = json.loads(store.path.read_text(encoding="utf-8"))

    assert record["version"] == SCHEMA_VERSION
    assert set(record) == {"version", "accounts", "launch_profiles"}


def test_a_write_leaves_no_temporary_behind(store):
    store.set_accounts(_accounts(ONE))

    assert sorted(p.name for p in store.path.parent.iterdir()) == ["accounts.json"]


def test_a_paired_write_reaches_disk_once(store, monkeypatch):
    """Deleting an account changes both lists; the file must never name a missing account."""
    store.set_accounts(_accounts(ONE, TWO))
    store.set_launch_profiles([{"name": "Duo", "accounts": [ONE, TWO]}])
    writes: list[int] = []
    monkeypatch.setattr(AccountStore, "save", lambda self: writes.append(1))

    store.set_accounts(_accounts(TWO), save=False)
    store.set_launch_profiles([{"name": "Duo", "accounts": [TWO]}])

    assert len(writes) == 1


def test_an_unreadable_file_reads_as_nothing_stored_and_is_left_alone(tmp_path):
    """Degrading to the empty launch screen is recoverable; overwriting the file is not."""
    directory = tmp_path / "vendor"
    directory.mkdir()
    (directory / "accounts.json").write_text("{not json", encoding="utf-8")

    store = AccountStore(directory)

    assert store.accounts == []
    assert (directory / "accounts.json").read_text(encoding="utf-8") == "{not json"


def test_a_record_of_the_wrong_shape_is_ignored_entry_by_entry(tmp_path):
    """The file is hand-editable JSON, so a string where an account belongs must not reach the GUI."""
    directory = tmp_path / "vendor"
    directory.mkdir()
    (directory / "accounts.json").write_text(
        json.dumps({"version": 1, "accounts": [{"username": ONE}, "nonsense", 7], "launch_profiles": "nope"}),
        encoding="utf-8",
    )

    store = AccountStore(directory)

    assert store.accounts == [{"username": ONE}]
    assert store.launch_profiles == []


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
    directory = sm_mod.product_dir()
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


def test_an_account_change_and_a_launch_profile_change_from_two_stores_both_survive(tmp_path):
    directory = tmp_path / "vendor"
    first, second = AccountStore(directory), AccountStore(directory)

    first.set_accounts(_accounts(ONE))
    second.set_launch_profiles([{"name": "Duo", "accounts": [ONE]}])

    reopened = AccountStore(directory)
    assert [a["username"] for a in reopened.accounts] == [ONE]
    assert reopened.launch_profiles == [{"name": "Duo", "accounts": [ONE]}]


def test_a_write_keeps_a_key_it_does_not_know(tmp_path):
    directory = tmp_path / "vendor"
    directory.mkdir()
    (directory / "accounts.json").write_text(
        json.dumps({"version": 1, "accounts": [], "launch_profiles": [], "from_later": [1]}), encoding="utf-8"
    )

    AccountStore(directory).set_accounts(_accounts(ONE))

    record = json.loads((directory / "accounts.json").read_text(encoding="utf-8"))
    assert record["from_later"] == [1]


def test_a_write_keeps_a_newer_versions_number(tmp_path):
    """Writing version 1 over a newer product's record would make it migrate its own file again."""
    directory = tmp_path / "vendor"
    directory.mkdir()
    (directory / "accounts.json").write_text(
        json.dumps({"version": SCHEMA_VERSION + 1, "accounts": [], "launch_profiles": []}), encoding="utf-8"
    )

    AccountStore(directory).set_accounts(_accounts(ONE))

    record = json.loads((directory / "accounts.json").read_text(encoding="utf-8"))
    assert record["version"] == SCHEMA_VERSION + 1


def test_a_store_sees_the_other_writers_list_after_its_own_write(tmp_path):
    directory = tmp_path / "vendor"
    first, second = AccountStore(directory), AccountStore(directory)

    second.set_launch_profiles([{"name": "Duo", "accounts": []}])
    first.set_accounts(_accounts(ONE))

    assert first.launch_profiles == [{"name": "Duo", "accounts": []}]


THREE = "three@example.com"


def _two_stores_on(tmp_path, accounts: list[dict]):
    """Two stores that both loaded *accounts*, as two products started on one file."""
    directory = tmp_path / "vendor"
    AccountStore(directory).set_accounts(accounts)
    return directory, AccountStore(directory), AccountStore(directory)


def _usernames(directory) -> list[str]:
    return [a["username"] for a in AccountStore(directory).accounts]


def test_an_edit_from_a_stale_copy_keeps_the_account_the_other_product_added(tmp_path):
    """The client loop learning a fingerprint writes every account it holds, from a copy
    loaded at start. That must not delete one the other product added since."""
    directory, first, second = _two_stores_on(tmp_path, _accounts(ONE))
    second.set_accounts([*second.accounts, {"username": TWO, "alias": ""}])

    stale = first.accounts
    stale[0] = {**stale[0], "fingerprint": "abc"}
    first.set_accounts(stale)

    on_disk = AccountStore(directory).accounts
    assert [a["username"] for a in on_disk] == [ONE, TWO]
    assert on_disk[0]["fingerprint"] == "abc"


def test_an_edit_from_a_stale_copy_does_not_bring_back_a_deleted_account(tmp_path):
    directory, first, second = _two_stores_on(tmp_path, _accounts(ONE, TWO))
    second.set_accounts(_accounts(ONE))

    first.set_accounts([{**a, "alias": "x"} if a["username"] == ONE else a for a in first.accounts])

    assert _usernames(directory) == [ONE]


def test_a_delete_from_a_stale_copy_keeps_the_others_edit_to_another_account(tmp_path):
    directory, first, second = _two_stores_on(tmp_path, _accounts(ONE, TWO, THREE))
    second.set_accounts([{**a, "alias": "edited"} if a["username"] == TWO else a for a in second.accounts])

    first.set_accounts([a for a in first.accounts if a["username"] != THREE])

    on_disk = AccountStore(directory).accounts
    assert [a["username"] for a in on_disk] == [ONE, TWO]
    assert on_disk[1]["alias"] == "edited"


def test_an_account_both_products_edited_is_the_later_writers(tmp_path):
    directory, first, second = _two_stores_on(tmp_path, _accounts(ONE))
    second.set_accounts([{"username": ONE, "alias": "second"}])

    first.set_accounts([{"username": ONE, "alias": "first"}])

    assert AccountStore(directory).accounts == [{"username": ONE, "alias": "first"}]


def test_a_reorder_from_a_stale_copy_keeps_the_others_new_account(tmp_path):
    directory, first, second = _two_stores_on(tmp_path, _accounts(ONE, TWO))
    second.set_accounts([*second.accounts, {"username": THREE, "alias": ""}])

    first.set_accounts(list(reversed(first.accounts)))

    assert _usernames(directory) == [TWO, ONE, THREE]


def test_an_in_place_edit_to_a_profiles_account_list_is_still_a_change(tmp_path):
    """The lists handed out are shallow copies, so the base must not share the inner list."""
    directory = tmp_path / "vendor"
    store = AccountStore(directory)
    store.set_launch_profiles([{"name": "Duo", "accounts": [ONE]}])
    AccountStore(directory).set_launch_profiles([{"name": "Duo", "accounts": [ONE]}, {"name": "Solo", "accounts": []}])

    profiles = store.launch_profiles
    profiles[0]["accounts"].append(TWO)
    store.set_launch_profiles(profiles)

    assert AccountStore(directory).launch_profiles == [
        {"name": "Duo", "accounts": [ONE, TWO]},
        {"name": "Solo", "accounts": []},
    ]


def test_a_list_that_cannot_be_keyed_is_written_whole(tmp_path):
    directory, first, second = _two_stores_on(tmp_path, _accounts(ONE))
    second.set_accounts([*second.accounts, {"username": TWO}])

    first.set_accounts([{"alias": "no username"}])

    assert AccountStore(directory).accounts == [{"alias": "no username"}]
