"""Unit tests for the teleport_to_friend node."""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.teleportation.teleport_to_friend as ttf
from tests.conftest import FakeClient, FakeMachine

_FROM_ZONE = "HubZone"
_TO_ZONE = "DungeonZone"
_FRIEND = "WizardFriend"

_PARAMS = {"friend_name": _FRIEND, "to_zone": _TO_ZONE}

# Patch the WizWalker function at its imported reference in the module under test.
_TELEPORT_OK = patch(
    "bartlebot.nodes.teleportation.teleport_to_friend.teleport_to_friend_from_list",
    new_callable=AsyncMock,
)

# Patch _resolve_exact_friend_name to bypass WizWalker internals.
# Returns the exact-case friend name (simulating a successful friends list lookup).
_RESOLVE_OK = patch.object(ttf, "_resolve_exact_friend_name", new=AsyncMock(return_value=_FRIEND))

# Default dialog patch: no friend-busy dialog visible.
_NO_DIALOG = patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=False))
_CLICK_BY_PATH = patch.object(nh, "click_by_path", new=AsyncMock(return_value=True))


def _client_needs_tp() -> FakeClient:
    """Client in _FROM_ZONE that loads and arrives in _TO_ZONE."""
    c = FakeClient()
    # zone_name: initial check → not there; wait_for_zone poll → arrived.
    c.zone_name = AsyncMock(side_effect=[_FROM_ZONE, _TO_ZONE])
    # is_loading: trigger loop → True (seen); none_loading wait → False (done).
    c.is_loading = AsyncMock(side_effect=[True, False])
    return c


def _client_already_there() -> FakeClient:
    """Client already in _TO_ZONE — should never call teleport."""
    c = FakeClient()
    c.zone_name = AsyncMock(return_value=_TO_ZONE)
    c.is_loading = AsyncMock(return_value=False)
    return c


# ---------------------------------------------------------------------------
# Early exit
# ---------------------------------------------------------------------------


async def test_all_already_in_zone_returns_success():
    """SUCCESS without any teleport call when all clients are already in to_zone."""
    clients = [_client_already_there(), _client_already_there()]

    with _TELEPORT_OK as mock_tp:
        result = await ttf.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"
    mock_tp.assert_not_called()


async def test_one_client_already_there_is_skipped():
    """Client already in to_zone is skipped; only the other client teleports."""
    c_skip = _client_already_there()
    c_move = _client_needs_tp()

    with _TELEPORT_OK as mock_tp, _RESOLVE_OK, _NO_DIALOG, patch.object(ttf, "POLL_INTERVAL", 0):
        result = await ttf.run(FakeMachine(), [c_skip, c_move], _PARAMS)

    assert result == "SUCCESS"
    # teleport_to_friend_from_list called exactly once (for c_move only)
    assert mock_tp.call_count == 1


# ---------------------------------------------------------------------------
# Case-insensitive friend name
# ---------------------------------------------------------------------------


async def test_friend_name_is_lowercased():
    """friend_name is lowercased before being passed to _resolve_exact_friend_name."""
    client = _client_needs_tp()

    with _TELEPORT_OK, _NO_DIALOG, patch.object(ttf, "POLL_INTERVAL", 0):
        resolve = patch.object(ttf, "_resolve_exact_friend_name", new=AsyncMock(return_value=_FRIEND))
        with resolve as mock_resolve:
            await ttf.run(FakeMachine(), [client], {"friend_name": "WizardFriend", "to_zone": _TO_ZONE})

    # _resolve_exact_friend_name receives the lowercased input name.
    assert mock_resolve.call_args[0][1] == "wizardfriend"


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_success_single_client():
    """Single client teleports, loads, and arrives in to_zone → SUCCESS."""
    client = _client_needs_tp()

    with _TELEPORT_OK, _RESOLVE_OK, _NO_DIALOG, patch.object(ttf, "POLL_INTERVAL", 0):
        result = await ttf.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"


async def test_success_multiple_clients():
    """Two clients both teleport and arrive → SUCCESS."""
    clients = [_client_needs_tp(), _client_needs_tp()]

    with _TELEPORT_OK, _RESOLVE_OK, _NO_DIALOG, patch.object(ttf, "POLL_INTERVAL", 0):
        result = await ttf.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"


async def test_mouse_handler_context_activated():
    """The mouse handler context manager is entered for each teleporting client."""
    client = _client_needs_tp()

    with _TELEPORT_OK, _RESOLVE_OK, _NO_DIALOG, patch.object(ttf, "POLL_INTERVAL", 0):
        await ttf.run(FakeMachine(), [client], _PARAMS)

    client.mouse_handler.__aenter__.assert_called()
    client.mouse_handler.__aexit__.assert_called()


# ---------------------------------------------------------------------------
# Friend not found / no friends online
# ---------------------------------------------------------------------------


async def test_friend_not_found_returns_failure():
    """FAILURE when _resolve_exact_friend_name raises ValueError (friend not on list)."""
    client = FakeClient()
    client.zone_name = AsyncMock(return_value=_FROM_ZONE)

    not_found = patch.object(
        ttf,
        "_resolve_exact_friend_name",
        new=AsyncMock(side_effect=ValueError("Could not find friend")),
    )
    with not_found:
        result = await ttf.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_no_friends_online_returns_failure():
    """FAILURE when _resolve_exact_friend_name raises ValueError (no friends online)."""
    client = FakeClient()
    client.zone_name = AsyncMock(return_value=_FROM_ZONE)

    no_friends = patch.object(
        ttf,
        "_resolve_exact_friend_name",
        new=AsyncMock(side_effect=ValueError("No friends online")),
    )
    with no_friends:
        result = await ttf.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


# ---------------------------------------------------------------------------
# Friend-busy dialog
# ---------------------------------------------------------------------------


async def test_friend_busy_dialog_returns_failure():
    """FAILURE when the friend-busy dialog appears after initiating the teleport."""
    client = FakeClient()
    client.zone_name = AsyncMock(return_value=_FROM_ZONE)
    client.is_loading = AsyncMock(return_value=False)

    dialog_visible = patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=True))
    click = patch.object(nh, "click_by_path", new=AsyncMock(return_value=True))

    with _TELEPORT_OK, _RESOLVE_OK, dialog_visible, click as mock_click, patch.object(ttf, "POLL_INTERVAL", 0):
        result = await ttf.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    mock_click.assert_called_once()


async def test_friend_busy_dialog_only_checked_for_not_yet_loading():
    """Once a client has been seen loading, the dialog check is skipped for it."""
    c1 = _client_needs_tp()  # loads on first poll
    c2 = FakeClient()
    c2.zone_name = AsyncMock(return_value=_FROM_ZONE)
    c2.is_loading = AsyncMock(return_value=False)  # never loads → will hit dialog check

    dialog_for_c2 = AsyncMock(side_effect=lambda client, path: client is c2)
    click = patch.object(nh, "click_by_path", new=AsyncMock(return_value=True))

    with (
        _TELEPORT_OK,
        _RESOLVE_OK,
        patch.object(nh, "is_visible_by_path", dialog_for_c2),
        click,
        patch.object(ttf, "POLL_INTERVAL", 0),
    ):
        result = await ttf.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "FAILURE"


# ---------------------------------------------------------------------------
# Trigger timeout (never loads)
# ---------------------------------------------------------------------------


async def test_trigger_timeout_returns_failure():
    """FAILURE when clients never enter a loading screen within ZONE_CHANGE_TIMEOUT."""
    client = FakeClient()
    client.zone_name = AsyncMock(return_value=_FROM_ZONE)
    client.is_loading = AsyncMock(return_value=False)

    with (
        _TELEPORT_OK,
        _RESOLVE_OK,
        _NO_DIALOG,
        patch.object(ttf, "TRIGGER_TIMEOUT", 0),
        patch.object(ttf, "ZONE_CHANGE_TIMEOUT", 0.01),
        patch.object(ttf, "POLL_INTERVAL", 0),
    ):
        result = await ttf.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


# ---------------------------------------------------------------------------
# Post-teleport zone verification
# ---------------------------------------------------------------------------


async def test_wrong_zone_after_loading_returns_failure():
    """FAILURE when clients load but arrive in the wrong zone."""
    client = FakeClient()
    # zone_name always returns wrong zone (initial check + wait_for_zone timeout)
    client.zone_name = AsyncMock(return_value=_FROM_ZONE)
    client.is_loading = AsyncMock(side_effect=[True, False])

    with (
        _TELEPORT_OK,
        _RESOLVE_OK,
        _NO_DIALOG,
        patch.object(ttf, "POLL_INTERVAL", 0),
        patch.object(ttf, "ZONE_CHANGE_TIMEOUT", 0.01),
    ):
        result = await ttf.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


# ---------------------------------------------------------------------------
# Straggler re-attempt
# ---------------------------------------------------------------------------


async def test_straggler_is_retried_and_succeeds():
    """Client 1 loads immediately; client 2 needs a re-attempt before loading → SUCCESS."""
    c1 = FakeClient()
    c1.zone_name = AsyncMock(side_effect=[_FROM_ZONE, _TO_ZONE])
    # iter1: loading (seen); iter2: already in set (re-polled but ignored); none_loading: done.
    c1.is_loading = AsyncMock(side_effect=[True, True, False])

    c2 = FakeClient()
    c2.zone_name = AsyncMock(side_effect=[_FROM_ZONE, _TO_ZONE])
    # iter1: not loading (grace period, no retry); iter2: loading after retry → seen; none_loading: done.
    c2.is_loading = AsyncMock(side_effect=[False, True, False])

    with (
        _TELEPORT_OK as mock_tp,
        _RESOLVE_OK,
        _NO_DIALOG,
        patch.object(ttf, "POLL_INTERVAL", 0),
        patch.object(ttf, "TRIGGER_TIMEOUT", 0),  # collapse grace period so straggler fires immediately
        patch.object(ttf, "ZONE_CHANGE_TIMEOUT", 100),  # prevent timeout from firing
    ):
        result = await ttf.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "SUCCESS"
    # Initial call for both + one retry for c2 = 3 total calls.
    assert mock_tp.call_count == 3


async def test_straggler_retry_raises_value_error_returns_failure():
    """FAILURE when the straggler re-attempt finds the friend is no longer available."""
    c1 = _client_needs_tp()  # loads normally

    c2 = FakeClient()
    c2.zone_name = AsyncMock(return_value=_FROM_ZONE)
    c2.is_loading = AsyncMock(return_value=False)  # never loads → gets retried

    call_count = 0

    async def resolve_side_effect(client, name):
        nonlocal call_count
        call_count += 1
        # Initial calls succeed; the straggler retry for c2 fails
        if call_count > 2:
            raise ValueError("Could not find friend")
        return _FRIEND

    with (
        _TELEPORT_OK,
        patch.object(ttf, "_resolve_exact_friend_name", side_effect=resolve_side_effect),
        _NO_DIALOG,
        patch.object(ttf, "POLL_INTERVAL", 0),
        patch.object(ttf, "TRIGGER_TIMEOUT", 0),  # collapse grace period so straggler fires immediately
        patch.object(ttf, "ZONE_CHANGE_TIMEOUT", 100),  # prevent timeout from firing
    ):
        result = await ttf.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "FAILURE"
