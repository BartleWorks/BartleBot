"""Unit tests for the mark_location node."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from wizwalker import XYZ

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.teleportation.mark_location as ml
from bartlebot.nodes.paths import RECALL_TIMER_PATH
from tests.conftest import FakeClient, FakeMachine

_ZONE = "TestZone"  # matches FakeClient.zone_name default
_PARAMS = {
    "zone": _ZONE,
    "xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
}


def _window():
    """A fake UI window, enabled, for click_by_path to click."""
    w = MagicMock()
    w.maybe_text = AsyncMock(return_value="")
    w.is_control_grayed = AsyncMock(return_value=False)
    return w


async def _find_window(root, path):
    """Every window the node looks for exists; the teleport timer is never one of them."""
    assert path != RECALL_TIMER_PATH, "the teleport timer was read"
    return _window()


# Patch _get_window_from_path so every button the node clicks is found.
_WINDOWS = patch.object(nh, "_get_window_from_path", new=_find_window)
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
        _WINDOWS,
        _RECALL_ENABLED,
        patch.object(ml, "POST_MARK_WAIT", 0),
        patch.object(ml, "POSITION_STABILIZE_TIMEOUT", 0),
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

    with _WINDOWS, _RECALL_ENABLED, patch.object(ml, "POST_MARK_WAIT", 0):
        result = await ml.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_not_called()


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_success_single_client():
    """Happy path: RecallButton enabled after marking → SUCCESS, MarkButton clicked."""
    client = _ready_client()

    with _WINDOWS, _RECALL_ENABLED, patch.object(ml, "POST_MARK_WAIT", 0):
        result = await ml.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert client.mouse_handler.click_window.call_count == 1


async def test_success_multiple_clients():
    """Happy path: two clients, both mark successfully → SUCCESS."""
    clients = [_ready_client(), _ready_client()]

    with _WINDOWS, _RECALL_ENABLED, patch.object(ml, "POST_MARK_WAIT", 0):
        result = await ml.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"
    for c in clients:
        assert c.mouse_handler.click_window.call_count == 1


# ---------------------------------------------------------------------------
# No cooldown
# ---------------------------------------------------------------------------


async def test_a_running_teleport_timer_does_not_hold_up_the_mark():
    """Marking has no cooldown: the timer under the Recall button is the teleport cooldown, so it is not read.

    _find_window fails the test if the node looks the timer up.
    """
    client = _ready_client()

    with _WINDOWS, _RECALL_ENABLED, patch.object(ml, "POST_MARK_WAIT", 0):
        result = await ml.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert client.mouse_handler.click_window.call_count == 1


# ---------------------------------------------------------------------------
# Same-spot edge case
# ---------------------------------------------------------------------------


async def test_same_spot_clicks_mark_button_twice():
    """Same-spot mark scenario: RecallButton grayed after first mark → re-mark.

    When the client already has a mark at this exact location, clicking MarkButton
    clears it (RecallButton grays out) rather than resetting it. The node must click
    MarkButton a second time.
    """
    client = _ready_client()
    # First check after marking: grayed (mark cleared); second check: enabled (re-mark succeeded).
    recall_state = patch.object(nh, "is_enabled_by_path", new=AsyncMock(side_effect=[False, True]))

    with _WINDOWS, recall_state, patch.object(ml, "POST_MARK_WAIT", 0):
        result = await ml.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert client.mouse_handler.click_window.call_count == 2


async def test_a_mark_still_unset_after_the_second_click_fails():
    """FAILURE when the RecallButton stays grayed after both clicks: the mark never took."""
    client = _ready_client()
    recall_disabled = patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=False))

    with _WINDOWS, recall_disabled, patch.object(ml, "POST_MARK_WAIT", 0):
        result = await ml.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    assert client.mouse_handler.click_window.call_count == 2


# ---------------------------------------------------------------------------
# The run's record of marks
# ---------------------------------------------------------------------------


async def test_a_mark_is_remembered_for_the_rest_of_the_run():
    """mark_client records where the client stood, for this node and the recall to read later."""
    machine, client = FakeMachine(), _ready_client()

    with _WINDOWS, _RECALL_ENABLED, patch.object(ml, "POST_MARK_WAIT", 0):
        await ml.run(machine, [client], _PARAMS)

    mark = ml.marked_at(machine, client)
    assert mark.zone == _ZONE
    assert (mark.position.x, mark.position.y, mark.position.z) == (0.0, 0.0, 0.0)
    assert ml.marked_at(machine, _ready_client()) is None


async def test_a_spot_already_marked_this_run_is_not_marked_again():
    """A second pass marks nothing: clicking the same spot again would only clear the mark."""
    machine, client = FakeMachine(), _ready_client()

    with _WINDOWS, _RECALL_ENABLED, patch.object(ml, "POST_MARK_WAIT", 0):
        assert await ml.run(machine, [client], _PARAMS) == "SUCCESS"
        client.body.position = AsyncMock(return_value=XYZ(400.0, 0.0, 0.0))  # walked off since
        assert await ml.run(machine, [client], _PARAMS) == "SUCCESS"

    assert client.mouse_handler.click_window.call_count == 1
    client.teleport.assert_not_called()


async def test_a_mark_set_since_somewhere_else_is_replaced():
    """A client whose last mark is elsewhere (say, a level scaling trip) marks this spot again."""
    machine, client = FakeMachine(), _ready_client()
    ml._remember(machine, client, ml.Mark(_ZONE, XYZ(5000.0, 0.0, 0.0)))

    with _WINDOWS, _RECALL_ENABLED, patch.object(ml, "POST_MARK_WAIT", 0):
        result = await ml.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    assert client.mouse_handler.click_window.call_count == 1
    assert ml.marked_at(machine, client).position.x == 0.0


async def test_a_remembered_mark_that_is_gone_is_set_again():
    """The record says marked, but the RecallButton is grayed: the mark was cleared, so mark again."""
    machine, client = FakeMachine(), _ready_client()
    ml._remember(machine, client, ml.Mark(_ZONE, XYZ(0.0, 0.0, 0.0)))
    # The kept-mark check reads grayed; the click after marking reads enabled.
    recall_state = patch.object(nh, "is_enabled_by_path", new=AsyncMock(side_effect=[False, True]))

    with _WINDOWS, recall_state, patch.object(ml, "POST_MARK_WAIT", 0):
        result = await ml.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    assert client.mouse_handler.click_window.call_count == 1


async def test_only_the_clients_without_the_mark_check_mana():
    """A client already marked here is not asked for mana it no longer needs."""
    machine, marked, fresh = FakeMachine(), _ready_client(), _ready_client()
    ml._remember(machine, marked, ml.Mark(_ZONE, XYZ(0.0, 0.0, 0.0)))
    marked.calc_mana_ratio.return_value = 0.0

    with _WINDOWS, _RECALL_ENABLED, patch.object(ml, "POST_MARK_WAIT", 0):
        result = await ml.run(machine, [marked, fresh], _PARAMS)

    assert result == "SUCCESS"
    assert marked.mouse_handler.click_window.call_count == 0
    assert fresh.mouse_handler.click_window.call_count == 1
