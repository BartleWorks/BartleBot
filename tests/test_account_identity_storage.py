"""Fingerprint storage through a real SettingsManager, to disk and back.

The adoption tests stub SettingsManager, so they prove the learn/adopt logic but say
nothing about whether a fingerprint actually lands in settings.json — and a fingerprint
that does not survive the process is worthless, since recognising a window a *previous*
session launched is the entire reason it exists.

Each test builds a private SettingsManager against tmp_path rather than touching the
%APPDATA% singleton.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bartlebot import account_identity
from bartlebot import settings_manager as sm_mod
from bartlebot.client_manager import ClientManager
from bartlebot.settings_manager import SettingsManager

KEY = b"\x03" * 32
UID = 196751008724043973


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """A SettingsManager writing to tmp_path, isolated from the real singleton."""
    monkeypatch.setattr(sm_mod, "_appdata_dir", lambda: tmp_path)
    return SettingsManager()


@pytest.fixture(autouse=True)
def _fixed_key(monkeypatch):
    monkeypatch.setattr(account_identity, "account_key", lambda: KEY)


class IdentityHarness:
    """The attributes _refresh_account_identity and _read_user_id use on self."""

    _refresh_account_identity = ClientManager._refresh_account_identity
    _read_user_id = ClientManager._read_user_id
    _assign_slot = ClientManager._assign_slot

    def __init__(self) -> None:
        self._hwnd_account_map: dict[int, str] = {}
        self._hwnd_user_ids: dict[int, int] = {}
        self._client_order: list[str | None] = []
        self._vacated_slots: dict[str, int] = {}


def _client(hwnd: int, user_id: int):
    client = MagicMock(window_handle=hwnd)
    client.game_client.user_id = AsyncMock(return_value=user_id)
    return client


async def _identify(harness, settings, clients) -> None:
    with patch("bartlebot.client_manager.SettingsManager") as sm:
        sm.instance.return_value = settings
        await ClientManager._refresh_account_identity(harness, clients)


async def test_a_learned_fingerprint_reaches_the_file(settings, tmp_path):
    """The bug this file exists for: the accounts setter only writes memory."""
    settings.accounts = [{"username": "user1", "alias": "Jeff"}]
    settings.save()

    harness = IdentityHarness()
    harness._hwnd_account_map = {100: "user1"}
    await _identify(harness, settings, [_client(100, UID)])

    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    stored = account_identity.stored_fingerprint(on_disk["accounts"], "user1")
    assert stored == account_identity.fingerprint(UID, KEY)


async def test_a_learned_fingerprint_survives_a_restart(settings, tmp_path, monkeypatch):
    """Saturday's case: the session that launched the clients is not the one that adopts them."""
    settings.accounts = [{"username": "user1", "alias": "Jeff"}]
    settings.save()

    launching = IdentityHarness()
    launching._hwnd_account_map = {100: "user1"}
    await _identify(launching, settings, [_client(100, UID)])

    # A fresh manager reading the same file — a new app session.
    reloaded = SettingsManager()
    adopting = IdentityHarness()
    await _identify(adopting, reloaded, [_client(555, UID)])

    assert adopting._hwnd_account_map == {555: "user1"}, "the window should be adopted after a restart"


async def test_the_user_id_itself_is_never_written(settings, tmp_path):
    settings.accounts = [{"username": "user1", "alias": "Jeff"}]
    settings.save()

    harness = IdentityHarness()
    harness._hwnd_account_map = {100: "user1"}
    await _identify(harness, settings, [_client(100, UID)])

    raw = (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert str(UID) not in raw
    assert account_identity.fingerprint(UID, KEY) in raw


async def test_learning_leaves_other_account_fields_intact(settings, tmp_path):
    settings.accounts = [
        {"username": "user1", "alias": "Jeff"},
        {"username": "user2", "alias": "Emma"},
    ]
    settings.save()

    harness = IdentityHarness()
    harness._hwnd_account_map = {100: "user1"}
    await _identify(harness, settings, [_client(100, UID)])

    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["accounts"][0]["alias"] == "Jeff"
    assert on_disk["accounts"][1] == {"username": "user2", "alias": "Emma"}


def test_settings_load_preserves_unknown_account_keys(tmp_path, monkeypatch):
    """accounts is not a validated section, so extra keys must survive a load untouched.

    If it ever gains a pydantic model, this fails rather than silently dropping every
    stored fingerprint on the next start.
    """
    monkeypatch.setattr(sm_mod, "_appdata_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text(
        json.dumps({"accounts": [{"username": "user1", "alias": "Jeff", "uid_fingerprint": "abc", "uid_scheme": 1}]}),
        encoding="utf-8",
    )

    loaded = SettingsManager().accounts

    assert loaded[0]["uid_fingerprint"] == "abc"
    assert loaded[0]["uid_scheme"] == 1
