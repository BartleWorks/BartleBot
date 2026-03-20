"""Unit tests for the mark_location node."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from wizwalker import Keycode, XYZ

import bartlebot.nodes.action.mark_location as ml
import bartlebot.nodes.node_helpers as nh

from tests.conftest import FakeClient, FakeMachine

_ZONE = "TestZone"  # matches FakeClient.zone_name default
_PARAMS = {
    "zone": _ZONE,
    "xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
}


def _timer_window(text: str = ""):
    """Fake timer window. Empty text = no cooldown active."""
    w = MagicMock()
    w.maybe_text = AsyncMock(return_value=text)
    return w


# Patch _get_window_from_path so the timer is always reported as clear (empty text).
_TIMER_CLEAR = patch.object(
    nh, "_get_window_from_path", new=AsyncMock(return_value=_timer_window(""))
)
# Patch is_enabled_by_path so the RecallButton appears enabled (mark successfully set).
_RECALL_ENABLED = patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=True))


def _ready_client() -> FakeClient:
    """Client in the correct zone at the target XYZ with full mana."""
    c = FakeClient()
    # body.position at target XYZ → no teleport needed before marking.
    c.body.position = AsyncMock(return_value=XYZ(0.0, 0.0, 0.0))
    return c


# ---------------------------------------------------------------------------
# Mana checks
# ---------------------------------------------------------------------------

async def test_no_mana_returns_failure():
    """FAILURE when a client has no mana; PAGE_DOWN never pressed."""
    client = _ready_client()
    client.calc_mana_ratio.return_value = 0.0

    result = await ml.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.send_key.assert_not_called()


async def test_one_client_no_mana_fails():
    """FAILURE when any one of several clients has no mana."""
    c_ok = _ready_client()
    c_empty = _ready_client()
    c_empty.calc_mana_ratio.return_value = 0.0

    result = await ml.run(FakeMachine(), [c_ok, c_empty], _PARAMS)

    assert result == "FAILURE"
    c_ok.send_key.assert_not_called()
    c_empty.send_key.assert_not_called()


# ---------------------------------------------------------------------------
# Zone checks
# ---------------------------------------------------------------------------

async def test_wrong_zone_returns_failure():
    """FAILURE when client is not in the expected zone."""
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"

    result = await ml.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()
    client.send_key.assert_not_called()


async def test_one_client_wrong_zone_fails():
    """FAILURE when one of two clients is in the wrong zone."""
    good = _ready_client()
    bad = FakeClient()
    bad.zone_name.return_value = "WrongZone"

    result = await ml.run(FakeMachine(), [good, bad], _PARAMS)

    assert result == "FAILURE"
    good.send_key.assert_not_called()


# ---------------------------------------------------------------------------
# XYZ pre-positioning
# ---------------------------------------------------------------------------

async def test_correct_zone_wrong_xyz_teleports_to_target():
    """If in the correct zone but away from the mark XYZ, the client is teleported first."""
    client = _ready_client()
    client.body.position = AsyncMock(return_value=XYZ(100.0, 0.0, 0.0))  # far from target

    with (
        _TIMER_CLEAR,
        _RECALL_ENABLED,
        patch.object(ml, "POST_MARK_WAIT", 0),
        patch.object(ml, "STABILIZE_TIMEOUT", 0),
        patch.object(ml, "POLL_INTERVAL", 0),
    ):
        result = await ml.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_called_once()
    tp_target: XYZ = client.teleport.call_args.args[0]
    assert tp_target.x == pytest.approx(0.0)
    assert tp_target.y == pytest.approx(0.0)


async def test_correct_zone_correct_xyz_no_teleport():
    """No teleport when the client is already at the target XYZ."""
    client = _ready_client()  # body.position defaults to (0, 0, 0) == target

    with _TIMER_CLEAR, _RECALL_ENABLED, patch.object(ml, "POST_MARK_WAIT", 0):
        result = await ml.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_not_called()


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------

async def test_success_single_client():
    """Happy path: timer clear, RecallButton enabled after marking → SUCCESS, PAGE_DOWN pressed."""
    client = _ready_client()

    with _TIMER_CLEAR, _RECALL_ENABLED, patch.object(ml, "POST_MARK_WAIT", 0):
        result = await ml.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    pg_down_calls = [c for c in client.send_key.call_args_list if c.args[0] == Keycode.PAGE_DOWN]
    assert len(pg_down_calls) == 1


async def test_success_multiple_clients():
    """Happy path: two clients, both mark successfully → SUCCESS."""
    clients = [_ready_client(), _ready_client()]

    with _TIMER_CLEAR, _RECALL_ENABLED, patch.object(ml, "POST_MARK_WAIT", 0):
        result = await ml.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"
    for c in clients:
        pg_down_calls = [call for call in c.send_key.call_args_list if call.args[0] == Keycode.PAGE_DOWN]
        assert len(pg_down_calls) == 1


# ---------------------------------------------------------------------------
# Cooldown timer
# ---------------------------------------------------------------------------

async def test_cooldown_timer_timeout_returns_failure():
    """FAILURE when the mark cooldown timer never clears."""
    client = _ready_client()
    active_timer = patch.object(
        nh, "_get_window_from_path", new=AsyncMock(return_value=_timer_window("30"))
    )

    with (
        active_timer,
        patch.object(ml, "MARK_COOLDOWN_TIMEOUT", 0.01),
        patch.object(ml, "POLL_INTERVAL", 0),
    ):
        result = await ml.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.send_key.assert_not_called()


# ---------------------------------------------------------------------------
# Same-spot edge case
# ---------------------------------------------------------------------------

async def test_same_spot_presses_page_down_twice():
    """Same-spot mark scenario: RecallButton grayed after first mark → wait and re-mark.

    When the client already has a mark at this exact location, pressing PAGE_DOWN
    clears it (RecallButton grays out) rather than resetting it. The node must wait
    for the cooldown and press PAGE_DOWN a second time.
    """
    client = _ready_client()
    # First check after marking: grayed (mark cleared); second check: enabled (re-mark succeeded).
    recall_state = patch.object(nh, "is_enabled_by_path", new=AsyncMock(side_effect=[False, True]))

    with _TIMER_CLEAR, recall_state, patch.object(ml, "POST_MARK_WAIT", 0):
        result = await ml.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    pg_down_calls = [c for c in client.send_key.call_args_list if c.args[0] == Keycode.PAGE_DOWN]
    assert len(pg_down_calls) == 2


async def test_same_spot_remark_cooldown_timeout_returns_failure():
    """FAILURE when the second mark attempt's cooldown also never clears.

    Both wait_until calls time out, so the node cannot re-mark.
    """
    client = _ready_client()
    active_timer = patch.object(
        nh, "_get_window_from_path", new=AsyncMock(return_value=_timer_window("30"))
    )
    # Button grayed after first (instant) mark attempt.
    recall_disabled = patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=False))

    with (
        active_timer,
        recall_disabled,
        patch.object(ml, "MARK_COOLDOWN_TIMEOUT", 0.01),
        patch.object(ml, "POLL_INTERVAL", 0),
        patch.object(ml, "POST_MARK_WAIT", 0),
    ):
        result = await ml.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
