"""Tests for ClientManager._refresh_account_identity — learning and adopting accounts.

The title path can only place a client BartleBot launched. This step places the rest,
by the account-level user id the client reports in memory, so a window launched
elsewhere and adopted into BartleBot can still be recovered when it crashes.

Exercises the real method against a stub holding only the attributes it touches.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bartlebot.clients import account_identity
from bartlebot.clients.client_manager import ClientManager

KEY = b"\x01" * 32
UID_ONE = 196751008724043973
UID_TWO = 196751008724043974


@pytest.fixture(autouse=True)
def _fixed_key(monkeypatch):
    """Pin the HMAC key so fingerprints are predictable and no keyring is touched."""
    monkeypatch.setattr(account_identity, "account_key", lambda: KEY)


def _fp(user_id: int) -> str:
    return account_identity.fingerprint(user_id, KEY)


def _client(hwnd: int, user_id: int | None):
    """A client whose game_client reports *user_id*, or raises if None."""
    client = MagicMock(window_handle=hwnd)
    if user_id is None:
        client.game_client.user_id = AsyncMock(side_effect=RuntimeError("client is dying"))
    else:
        client.game_client.user_id = AsyncMock(return_value=user_id)
    return client


class IdentityHarness:
    """The attributes _refresh_account_identity and _read_user_id use on self."""

    _refresh_account_identity = ClientManager._refresh_account_identity
    _read_user_id = ClientManager._read_user_id
    _assign_slot = ClientManager._assign_slot

    def __init__(self, accounts: list[dict]) -> None:
        self._hwnd_account_map: dict[int, str] = {}
        self._hwnd_user_ids: dict[int, int] = {}
        self._client_order: list[str | None] = []
        self._vacated_slots: dict[str, int] = {}
        self.settings = MagicMock()
        self.settings.accounts = accounts


async def _run(harness: IdentityHarness, clients: list) -> None:
    """Run the real method with SettingsManager pointed at the harness's stub.

    An async helper rather than asyncio.run(): the suite runs under asyncio_mode="auto",
    and opening a second loop inside a sync test leaves a closed loop behind that breaks
    unrelated tests later in the session.
    """
    with patch("bartlebot.clients.client_manager.SettingsManager") as sm:
        sm.instance.return_value = harness.settings
        await ClientManager._refresh_account_identity(harness, clients)


def _accounts(*entries: tuple[str, int | None]) -> list[dict]:
    """Build account dicts; a None user id means the account has no fingerprint yet."""
    out = []
    for username, user_id in entries:
        entry = {"username": username, "alias": username.title()}
        if user_id is not None:
            entry["uid_fingerprint"] = _fp(user_id)
            entry["uid_scheme"] = account_identity.SCHEME
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Adopting
# ---------------------------------------------------------------------------


async def test_adopts_a_client_whose_fingerprint_is_known():
    """The whole point: a window BartleBot never launched becomes recoverable."""
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    await _run(harness, [_client(100, UID_ONE)])

    assert harness._hwnd_account_map == {100: "user1"}
    assert harness._client_order == ["user1"], "an adopted client takes a slot"


async def test_ignores_a_client_whose_fingerprint_is_unknown():
    """An account never launched from here has nothing on file — the known limitation."""
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    await _run(harness, [_client(100, UID_TWO)])

    assert harness._hwnd_account_map == {}
    assert harness._client_order == []


async def test_will_not_adopt_an_account_a_live_client_already_answers_for(caplog):
    """Two windows cannot be one account; guessing would relaunch the wrong one."""
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    harness._hwnd_account_map = {100: "user1"}

    with caplog.at_level("WARNING", logger="bartlebot.clients.client_manager"):
        await _run(harness, [_client(100, UID_ONE), _client(200, UID_ONE)])

    assert 200 not in harness._hwnd_account_map
    assert any("already answering" in r.getMessage() for r in caplog.records)


async def test_a_client_that_has_not_logged_in_is_left_alone():
    """user_id reads 0 until login completes."""
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    await _run(harness, [_client(100, 0)])

    assert harness._hwnd_account_map == {}
    assert harness._hwnd_user_ids == {}, "zero must not be cached, or it never resolves"


async def test_a_failing_read_does_not_raise():
    """This runs in the client loop, where an exception would cost the rest of the pass."""
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    await _run(harness, [_client(100, None)])

    assert harness._hwnd_account_map == {}


async def test_no_configured_accounts_reads_nothing():
    harness = IdentityHarness([])
    client = _client(100, UID_ONE)
    await _run(harness, [client])

    client.game_client.user_id.assert_not_awaited()


# ---------------------------------------------------------------------------
# Learning
# ---------------------------------------------------------------------------


async def test_learns_the_fingerprint_of_a_launched_client():
    """The title placed it, so the account is certain — this is the only moment we can learn."""
    harness = IdentityHarness(_accounts(("user1", None)))
    harness._hwnd_account_map = {100: "user1"}

    await _run(harness, [_client(100, UID_ONE)])

    written = harness.settings.accounts
    assert account_identity.stored_fingerprint(written, "user1") == _fp(UID_ONE)


async def test_learning_replaces_a_stale_fingerprint():
    """Launch is authoritative: we typed the credentials, so what it reports is the truth."""
    harness = IdentityHarness(_accounts(("user1", UID_TWO)))
    harness._hwnd_account_map = {100: "user1"}

    await _run(harness, [_client(100, UID_ONE)])

    assert account_identity.stored_fingerprint(harness.settings.accounts, "user1") == _fp(UID_ONE)


async def test_an_unchanged_fingerprint_is_not_rewritten():
    """Settings are written to disk; a write per second would be absurd."""
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    harness._hwnd_account_map = {100: "user1"}
    accounts_before = harness.settings.accounts

    await _run(harness, [_client(100, UID_ONE)])

    assert harness.settings.accounts is accounts_before, "no setter call means no disk write"


async def test_adopted_client_is_not_relearned_on_the_next_pass():
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    client = _client(100, UID_ONE)

    await _run(harness, [client])
    accounts_after_adopt = harness.settings.accounts
    await _run(harness, [client])

    assert harness.settings.accounts is accounts_after_adopt
    assert harness._hwnd_account_map == {100: "user1"}


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


async def test_user_id_is_read_once_per_window():
    """The first read resolves GameClient's base by pattern scan — far too costly per tick."""
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    client = _client(100, UID_ONE)

    await _run(harness, [client])
    await _run(harness, [client])
    await _run(harness, [client])

    assert client.game_client.user_id.await_count == 1
    assert harness._hwnd_user_ids == {100: UID_ONE}


async def test_a_pending_login_is_retried_until_it_resolves():
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    client = MagicMock(window_handle=100)
    client.game_client.user_id = AsyncMock(side_effect=[0, 0, UID_ONE])

    await _run(harness, [client])
    await _run(harness, [client])
    assert harness._hwnd_account_map == {}

    await _run(harness, [client])
    assert harness._hwnd_account_map == {100: "user1"}
