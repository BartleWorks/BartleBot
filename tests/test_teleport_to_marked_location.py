"""Unit tests for the teleport_to_marked_location node."""

import itertools
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.teleportation.teleport_to_marked_location as rtm
from bartlebot import dialog_captions
from bartlebot.nodes.paths import MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT, RECALL_BUTTON_PATH
from tests.conftest import FakeClient, FakeMachine, message_box_patches

_ZONE = "TestZone"  # matches FakeClient.zone_name default
_PARAMS = {
    "zone": _ZONE,
    "xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
}

# Patch is_enabled_by_path so the RecallButton is enabled (mark is set) by default.
_RECALL_ENABLED = patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=True))


def _ready_client() -> FakeClient:
    """Client with mana, mark set, loads immediately, and arrives at the expected location."""
    c = FakeClient()
    # is_loading: trigger loop → loading; none_loading wait → done; then done for every
    # later read, including the zone check's own straggler test.
    c.is_loading.side_effect = itertools.chain([True], itertools.repeat(False))
    # zone_name: final zone check → correct zone.
    c.zone_name.return_value = _ZONE
    # body.position: final XYZ check → at target.
    c.body.position = AsyncMock(return_value=XYZ(0.0, 0.0, 0.0))
    return c


# ---------------------------------------------------------------------------
# Mana checks
# ---------------------------------------------------------------------------


async def test_no_mana_returns_failure():
    """FAILURE when a client has no mana; PAGE_UP never pressed."""
    client = FakeClient()
    client.calc_mana_ratio.return_value = 0.0

    result = await rtm.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.send_key.assert_not_called()


async def test_one_client_no_mana_fails():
    """FAILURE when any one of several clients has no mana."""
    c_ok = _ready_client()
    c_empty = _ready_client()
    c_empty.calc_mana_ratio.return_value = 0.0

    result = await rtm.run(FakeMachine(), [c_ok, c_empty], _PARAMS)

    assert result == "FAILURE"
    c_ok.send_key.assert_not_called()
    c_empty.send_key.assert_not_called()


# ---------------------------------------------------------------------------
# No mark set
# ---------------------------------------------------------------------------


async def test_no_mark_set_returns_failure():
    """FAILURE when RecallButton never becomes enabled (no mark set)."""
    client = FakeClient()
    no_mark = patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=False))

    with (
        no_mark,
        patch.object(rtm, "COMPASS_TELEPORT_TIMEOUT", 0.01),
        patch.object(rtm, "POLL_INTERVAL", 0),
    ):
        result = await rtm.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.send_key.assert_not_called()


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_success_single_client():
    """Happy path: mark set, client loads and arrives at expected zone and XYZ → SUCCESS."""
    client = _ready_client()

    with _RECALL_ENABLED, patch.object(rtm, "POLL_INTERVAL", 0):
        result = await rtm.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"


async def test_success_multiple_clients():
    """Happy path: two clients, both recall and arrive correctly → SUCCESS."""
    clients = [_ready_client(), _ready_client()]

    with _RECALL_ENABLED, patch.object(rtm, "POLL_INTERVAL", 0):
        result = await rtm.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"


# ---------------------------------------------------------------------------
# Loading screen
# ---------------------------------------------------------------------------


async def test_trigger_timeout_returns_failure():
    """FAILURE when clients neither load nor move after PAGE_UP is pressed."""
    client = FakeClient()
    client.is_loading.return_value = False  # never starts loading
    client.body.position.return_value = XYZ(0.0, 0.0, 0.0)  # never moves

    with (
        _RECALL_ENABLED,
        patch.object(rtm, "TRIGGER_TIMEOUT", 0.01),
        patch.object(rtm, "POLL_INTERVAL", 0),
    ):
        result = await rtm.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_same_zone_recall_success():
    """SUCCESS when the mark is in the same zone — position changes but no loading screen."""
    client = FakeClient()
    client.is_loading.return_value = False  # never loads (same-zone recall)
    # start_positions snapshot → far from target; trigger poll → at target; final verify → at target.
    client.body.position = AsyncMock(side_effect=[XYZ(100.0, 100.0, 0.0), XYZ(0.0, 0.0, 0.0), XYZ(0.0, 0.0, 0.0)])

    with _RECALL_ENABLED, patch.object(rtm, "POLL_INTERVAL", 0):
        result = await rtm.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"


async def test_loading_screen_awaited():
    """Node waits for is_loading to clear before checking zone and XYZ."""
    client = _ready_client()
    # Loading takes two polls to finish.
    client.is_loading.side_effect = [True, True, False]

    with _RECALL_ENABLED, patch.object(rtm, "POLL_INTERVAL", 0):
        result = await rtm.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert client.is_loading.call_count >= 3


# ---------------------------------------------------------------------------
# Post-recall verification
# ---------------------------------------------------------------------------


async def test_wrong_zone_after_loading_returns_failure():
    """FAILURE when clients load but arrive in an unexpected zone."""
    client = _ready_client()
    client.zone_name.return_value = "WrongZone"

    with _RECALL_ENABLED, patch.object(rtm, "POLL_INTERVAL", 0):
        result = await rtm.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_wrong_xyz_after_loading_returns_failure():
    """FAILURE when clients arrive in the correct zone but at the wrong XYZ."""
    client = _ready_client()
    client.body.position = AsyncMock(return_value=XYZ(999.0, 0.0, 0.0))

    with _RECALL_ENABLED, patch.object(rtm, "POLL_INTERVAL", 0):
        result = await rtm.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


# ---------------------------------------------------------------------------
# PAGE_UP re-press (lagging clients)
# ---------------------------------------------------------------------------


async def test_page_up_resent_to_lagging_client():
    """One client loads immediately; the other needs PAGE_UP re-pressed."""
    c1 = _ready_client()
    # Trigger loop iter1: loading; iter2: still loading; none_loading: done.
    c1.is_loading.side_effect = [True, True, False]

    c2 = _ready_client()
    # Trigger loop iter1: not loading yet (gets re-press); iter2: loading; none_loading: done.
    c2.is_loading.side_effect = [False, True, False]

    with _RECALL_ENABLED, patch.object(rtm, "POLL_INTERVAL", 0):
        result = await rtm.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "SUCCESS"


async def test_fast_client_finishes_before_lagging_client_starts():
    """Client 1 finishes its loading screen before client 2 even starts loading.

    Without the seen_loading pattern, the trigger loop could never satisfy the
    all-loading condition (c1 is False/done while c2 is False/not started).
    """
    c1 = _ready_client()
    # iter1: loading (seen); iter2: already done; iter3: done; none_loading: done.
    c1.is_loading.side_effect = [True, False, False, False]

    c2 = _ready_client()
    # iter1: not started; iter2: not started (gets re-press); iter3: loading (seen); none_loading: done.
    c2.is_loading.side_effect = [False, False, True, False]

    with _RECALL_ENABLED, patch.object(rtm, "POLL_INTERVAL", 0):
        result = await rtm.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "SUCCESS"


# ---------------------------------------------------------------------------
# Dungeon exit warning
# ---------------------------------------------------------------------------


async def _recall_with_modal(visible_buttons):
    """Run one stuck tick then a loading tick; return (result, clicked paths)."""
    c = _ready_client()
    c.is_loading.side_effect = [False, True, False]
    patches, clicked = message_box_patches(visible_buttons)

    with _RECALL_ENABLED, patch.object(rtm, "POLL_INTERVAL", 0), patches[0], patches[1]:
        result = await rtm.run(FakeMachine(), [c], _PARAMS)

    return result, clicked


async def test_dungeon_exit_warning_is_confirmed_rather_than_re_clicked():
    """Recalling out of a dungeon: the warning holds the recall until it is confirmed."""
    result, clicked = await _recall_with_modal((MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT))

    assert result == "SUCCESS"
    assert clicked == [RECALL_BUTTON_PATH, MESSAGE_BOX_CENTER]


async def test_no_warning_still_re_clicks_the_recall_button():
    """Outside a dungeon the straggler retry must behave exactly as it did before."""
    result, clicked = await _recall_with_modal(())

    assert result == "SUCCESS"
    assert clicked == [RECALL_BUTTON_PATH, RECALL_BUTTON_PATH]


async def test_expired_dungeon_recall_is_named_rather_than_timing_out():
    """A mark outlives its dungeon, so this is normal after a long run.

    Before, the refusal was confirmed and the node spent TRIGGER_TIMEOUT before reporting
    "clients did not recall" — true, but not the reason.
    """
    c = _ready_client()
    # side_effect wins over return_value, so clear the helper's scripted sequence first.
    c.is_loading.side_effect = None
    c.is_loading.return_value = False
    patches, _clicked = message_box_patches((MESSAGE_BOX_CENTER,))
    machine = FakeMachine()

    with (
        _RECALL_ENABLED,
        patch.object(rtm, "POLL_INTERVAL", 0),
        patch.object(nh, "identify_modal", new=AsyncMock(return_value=dialog_captions.RECALL_DUNGEON_EXPIRED)),
        patches[0],
        patches[1],
    ):
        result = await rtm.run(machine, [c], _PARAMS)

    assert result == "FAILURE"
    assert any("timer ended" in msg for msg, _lvl in machine.logs)
    assert not any("did not recall" in msg for msg, _lvl in machine.logs)
