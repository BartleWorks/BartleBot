"""Unit tests for the teleport_to_player_if_in_zone node."""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.teleportation.teleport_to_player_if_in_zone as tpz
from bartlebot import wizard_names
from bartlebot.nodes.paths import MESSAGE_BOX_RIGHT
from tests.conftest import FakeClient, FakeMachine, message_box_patches

_ZONE = "DungeonZone"
_ELSEWHERE = "HubZone"
_TARGET_NAME = "Luke SkullHunter"

_PARAMS = {"target_zone": _ZONE}

# Patch the WizWalker function at its imported reference in node_helpers, which now owns
# the shared "port to a friend" mechanics (node_helpers.port_to_friend / teleport_via_friends_list).
_TELEPORT_OK = patch(
    "bartlebot.nodes.node_helpers.teleport_to_friend_from_list",
    new_callable=AsyncMock,
)
_RESOLVE_OK = patch.object(nh, "resolve_friend_name", new=AsyncMock(return_value=_TARGET_NAME))
_NO_DIALOG = patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=False))


def _caption(text: str):
    return patch.object(nh, "get_text_by_path", new=AsyncMock(return_value=text))


_NO_CAPTION = _caption("")


def _target_client() -> FakeClient:
    """Client already in _ZONE, whose wizard name is cached."""
    c = FakeClient()
    c.zone_name = AsyncMock(return_value=_ZONE)
    wizard_names.remember(c, _TARGET_NAME)
    return c


def _mover_client() -> FakeClient:
    """Client elsewhere that teleports in and arrives."""
    c = FakeClient()
    c.zone_name = AsyncMock(side_effect=[_ELSEWHERE, _ZONE])
    c.is_loading = AsyncMock(side_effect=[True, False])
    return c


def _clear_cache_after(fn):
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        finally:
            wizard_names.clear_cache()

    return wrapper


# ---------------------------------------------------------------------------
# No target in zone
# ---------------------------------------------------------------------------


async def test_no_client_in_zone_returns_failure():
    """FAILURE when no hooked client is in target_zone; nothing is touched."""
    clients = [FakeClient(), FakeClient()]
    for c in clients:
        c.zone_name = AsyncMock(return_value=_ELSEWHERE)

    with _TELEPORT_OK as mock_tp:
        result = await tpz.run(FakeMachine(), clients, _PARAMS)

    assert result == "FAILURE"
    mock_tp.assert_not_called()


async def test_empty_target_zone_returns_failure():
    result = await tpz.run(FakeMachine(), [FakeClient()], {"target_zone": ""})
    assert result == "FAILURE"


# ---------------------------------------------------------------------------
# Target name not yet known
# ---------------------------------------------------------------------------


async def test_target_name_not_cached_returns_failure():
    """FAILURE when the client found in target_zone has no cached wizard name."""
    client = FakeClient()
    client.zone_name = AsyncMock(return_value=_ZONE)

    with _TELEPORT_OK as mock_tp:
        result = await tpz.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    mock_tp.assert_not_called()


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_single_target_no_other_clients_returns_success():
    """Only the target is hooked — nothing to move → SUCCESS without any teleport call."""
    target = _target_client()

    with _TELEPORT_OK as mock_tp:
        try:
            result = await tpz.run(FakeMachine(), [target], _PARAMS)
        finally:
            wizard_names.clear_cache()

    assert result == "SUCCESS"
    mock_tp.assert_not_called()


async def test_other_client_teleports_to_target():
    """The non-target client is teleported to the target and arrives → SUCCESS."""
    target = _target_client()
    mover = _mover_client()

    with _TELEPORT_OK as mock_tp, _RESOLVE_OK, _NO_DIALOG, patch.object(tpz, "POLL_INTERVAL", 0):
        try:
            result = await tpz.run(FakeMachine(), [target, mover], _PARAMS)
        finally:
            wizard_names.clear_cache()

    assert result == "SUCCESS"
    assert mock_tp.call_count == 1
    assert mock_tp.call_args[0][0] is mover


async def test_first_in_player_order_is_the_target():
    """Two clients are in target_zone; the lower-numbered one is picked as target."""
    first_in_zone = _target_client()
    wizard_names.remember(first_in_zone, "First Wizard")
    second_in_zone = FakeClient()
    second_in_zone.zone_name = AsyncMock(return_value=_ZONE)
    wizard_names.remember(second_in_zone, "Second Wizard")
    mover = _mover_client()

    resolved_names = []

    async def _resolve(_client, name_lower):
        resolved_names.append(name_lower)
        return "First Wizard"

    params = {"target_zone": _ZONE, "skip_extra_clients_in_zone": True}

    with (
        _TELEPORT_OK as mock_tp,
        patch.object(nh, "resolve_friend_name", new=AsyncMock(side_effect=_resolve)),
        _NO_DIALOG,
        patch.object(tpz, "POLL_INTERVAL", 0),
    ):
        try:
            result = await tpz.run(FakeMachine(), [first_in_zone, second_in_zone, mover], params)
        finally:
            wizard_names.clear_cache()

    assert result == "SUCCESS"
    # Only the mover teleports; first_in_zone is the target and second_in_zone is skipped as an extra.
    assert mock_tp.call_count == 1
    assert resolved_names == ["first wizard"]


# ---------------------------------------------------------------------------
# skip_extra_clients_in_zone
# ---------------------------------------------------------------------------


async def test_extra_client_in_zone_teleports_by_default():
    """skip_extra_clients_in_zone off (default): an extra client already in target_zone is
    still re-teleported to the target, since being in the same zone isn't the same place."""
    target = _target_client()
    extra = FakeClient()
    extra.zone_name = AsyncMock(side_effect=[_ZONE, _ZONE])
    extra.is_loading = AsyncMock(side_effect=[True, False])

    with _TELEPORT_OK as mock_tp, _RESOLVE_OK, _NO_DIALOG, patch.object(tpz, "POLL_INTERVAL", 0):
        try:
            result = await tpz.run(FakeMachine(), [target, extra], _PARAMS)
        finally:
            wizard_names.clear_cache()

    assert result == "SUCCESS"
    assert mock_tp.call_count == 1
    assert mock_tp.call_args[0][0] is extra


async def test_extra_client_in_zone_is_skipped_when_flagged():
    """skip_extra_clients_in_zone on: an extra client already in target_zone is left alone."""
    target = _target_client()
    extra = FakeClient()
    extra.zone_name = AsyncMock(return_value=_ZONE)
    extra.is_loading = AsyncMock(return_value=False)

    params = {"target_zone": _ZONE, "skip_extra_clients_in_zone": True}

    with _TELEPORT_OK as mock_tp:
        try:
            result = await tpz.run(FakeMachine(), [target, extra], params)
        finally:
            wizard_names.clear_cache()

    assert result == "SUCCESS"
    mock_tp.assert_not_called()


# ---------------------------------------------------------------------------
# Friend-busy dialog / failures downstream, mirroring teleport_to_friend
# ---------------------------------------------------------------------------


async def test_friend_busy_dialog_returns_failure():
    target = _target_client()
    mover = FakeClient()
    mover.zone_name = AsyncMock(return_value=_ELSEWHERE)
    mover.is_loading = AsyncMock(return_value=False)
    patches, clicked = message_box_patches((MESSAGE_BOX_RIGHT,))
    machine = FakeMachine()

    with (
        _TELEPORT_OK,
        _RESOLVE_OK,
        patches[0],
        patches[1],
        _caption("Your friend is busy right now."),
        patch.object(tpz, "POLL_INTERVAL", 0),
    ):
        try:
            result = await tpz.run(machine, [target, mover], _PARAMS)
        finally:
            wizard_names.clear_cache()

    assert result == "FAILURE"
    assert clicked == [MESSAGE_BOX_RIGHT]


async def test_friend_not_found_returns_failure():
    target = _target_client()
    mover = FakeClient()
    mover.zone_name = AsyncMock(return_value=_ELSEWHERE)

    not_found = patch.object(
        nh,
        "resolve_friend_name",
        new=AsyncMock(side_effect=ValueError("Could not find friend")),
    )
    with not_found:
        try:
            result = await tpz.run(FakeMachine(), [target, mover], _PARAMS)
        finally:
            wizard_names.clear_cache()

    assert result == "FAILURE"


async def test_trigger_timeout_returns_failure():
    target = _target_client()
    mover = FakeClient()
    mover.zone_name = AsyncMock(return_value=_ELSEWHERE)
    mover.is_loading = AsyncMock(return_value=False)

    with (
        _TELEPORT_OK,
        _RESOLVE_OK,
        _NO_DIALOG,
        patch.object(tpz, "TRIGGER_TIMEOUT", 0),
        patch.object(tpz, "ZONE_CHANGE_TIMEOUT", 0.01),
        patch.object(tpz, "POLL_INTERVAL", 0),
    ):
        try:
            result = await tpz.run(FakeMachine(), [target, mover], _PARAMS)
        finally:
            wizard_names.clear_cache()

    assert result == "FAILURE"


async def test_wrong_zone_after_loading_returns_failure():
    target = _target_client()
    mover = FakeClient()
    mover.zone_name = AsyncMock(return_value=_ELSEWHERE)
    mover.is_loading = AsyncMock(side_effect=[True, False])

    with (
        _TELEPORT_OK,
        _RESOLVE_OK,
        _NO_DIALOG,
        patch.object(tpz, "POLL_INTERVAL", 0),
        patch.object(tpz, "ZONE_CHANGE_TIMEOUT", 0.01),
    ):
        try:
            result = await tpz.run(FakeMachine(), [target, mover], _PARAMS)
        finally:
            wizard_names.clear_cache()

    assert result == "FAILURE"
