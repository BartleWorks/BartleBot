"""Tests for bartlebot.gui.canvas.recording."""

import asyncio
from unittest.mock import MagicMock, call, patch

import pytest
from wizwalker import XYZ

import bartlebot.gui.canvas.recording as recording
from bartlebot.gui.canvas.recording import (
    RECORDABLE_NODES,
    _wait_for_zone_name,
    _zone_display,
    cancel_active_recording,
    is_recording,
    run_recording,
)
from tests.conftest import FakeClient, FakeParamForm

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_session_guard():
    """Reset module-level _active_task before and after every test."""
    recording._active_task = None
    yield
    recording._active_task = None


@pytest.fixture
def zero_poll():
    """Patch RECORDING_POLL_INTERVAL to 0 so polling loops run without sleeping."""
    with patch.object(recording, "RECORDING_POLL_INTERVAL", 0):
        yield


def _callbacks():
    """Return (on_status, on_btn, try_set_label) as MagicMocks."""
    return MagicMock(), MagicMock(), MagicMock()


def _client(zone="TestZone/Area", pos=None):
    c = FakeClient()
    c.zone_name.return_value = zone
    c.body.position.return_value = pos if pos is not None else XYZ(1.0, 2.0, 3.0)
    return c


# ---------------------------------------------------------------------------
# _zone_display (relocated from test_enter_zone.py)
# ---------------------------------------------------------------------------


def test_zone_display_none_returns_unknown():
    """_zone_display(None) must not raise and must return a readable fallback."""
    assert _zone_display(None) == "(unknown zone)"


def test_zone_display_fallback_uses_last_path_segment():
    """When wizsprinter is unavailable, _zone_display returns the last path segment."""
    with patch.dict("sys.modules", {"wizsprinter": None}):
        result = _zone_display("WizardCity/WC_Hub")
    assert result == "WC_Hub"


def test_zone_display_fallback_no_slash():
    """Fallback for a zone string with no slash returns the full string."""
    with patch.dict("sys.modules", {"wizsprinter": None}):
        result = _zone_display("FlatZone")
    assert result == "FlatZone"


# ---------------------------------------------------------------------------
# _wait_for_zone_name (relocated from test_enter_zone.py)
# ---------------------------------------------------------------------------


async def test_wait_for_zone_name_returns_immediately_when_not_none():
    """If zone_name() is already non-None, the result is returned on the first call."""
    client = FakeClient()
    client.zone_name.return_value = "Dst"

    zone = await _wait_for_zone_name(client, poll=0)

    assert zone == "Dst"
    assert client.zone_name.call_count == 1


async def test_wait_for_zone_name_retries_on_none():
    """If zone_name() returns None initially, polling continues until a valid zone is read."""
    client = FakeClient()
    client.zone_name.side_effect = [None, None, "Dst"]

    zone = await _wait_for_zone_name(client, poll=0)

    assert zone == "Dst"
    assert client.zone_name.call_count == 3


async def test_wait_for_zone_name_timeout_returns_none():
    """If zone_name() never returns a non-None value, None is returned after the timeout."""
    client = FakeClient()
    client.zone_name.return_value = None

    zone = await _wait_for_zone_name(client, poll=0, timeout=0)

    assert zone is None


# ---------------------------------------------------------------------------
# Session guard
# ---------------------------------------------------------------------------


def test_is_recording_false_when_no_task():
    assert is_recording() is False


def test_is_recording_false_when_task_done():
    # An explicit loop, not asyncio.get_event_loop(): there is no ambient loop
    # in a sync test, and relying on one is deprecated.
    loop = asyncio.new_event_loop()
    try:
        task = loop.create_task(asyncio.sleep(0))
        recording._active_task = task
        loop.run_until_complete(asyncio.sleep(0))  # let task complete
        assert is_recording() is False
    finally:
        loop.close()


async def test_is_recording_true_when_task_running():
    task = asyncio.create_task(asyncio.sleep(9999))
    recording._active_task = task
    assert is_recording() is True
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_cancel_active_recording_noop_when_none():
    cancel_active_recording()
    assert recording._active_task is None


async def test_cancel_active_recording_cancels_task():
    task = asyncio.create_task(asyncio.sleep(9999))
    recording._active_task = task
    cancel_active_recording()
    assert recording._active_task is None
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_run_recording_unknown_node_type_returns_immediately():
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()
    await run_recording("nonexistent_node", _client(), form, on_status, on_btn, try_set_label)
    on_status.assert_not_called()
    on_btn.assert_not_called()
    assert recording._active_task is None


async def test_run_recording_skips_if_already_recording():
    task = asyncio.create_task(asyncio.sleep(9999))
    task.done = lambda: False
    recording._active_task = task

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()
    await run_recording("mark_location", _client(), form, on_status, on_btn, try_set_label)

    on_status.assert_not_called()
    assert form.str_fields == {}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_run_recording_clears_active_task_on_completion():
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()
    await run_recording("mark_location", _client(), form, on_status, on_btn, try_set_label)
    assert recording._active_task is None


async def test_run_recording_clears_active_task_on_exception():
    client = _client()
    client.zone_name.side_effect = RuntimeError("boom")
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()
    await run_recording("mark_location", client, form, on_status, on_btn, try_set_label)
    assert recording._active_task is None


def test_recordable_nodes_contains_all_expected_types():
    expected = {
        "enter_zone",
        "teleport_to_coordinates",
        "enter_dungeon",
        "interact",
        "interact_menu",
        "apply_level_scaling",
        "enter_zone_via_object",
        "enter_zone_via_list",
        "mark_location",
        "teleport_to_marked_location",
    }
    assert RECORDABLE_NODES == expected


# ---------------------------------------------------------------------------
# _record_go_to_xyz  (instant, no Stop button)
# ---------------------------------------------------------------------------


async def test_go_to_xyz_happy_path():
    client = _client(zone="Hub/Central", pos=XYZ(10.0, 20.0, 30.0))
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("teleport_to_coordinates", client, form, on_status, on_btn, try_set_label)

    assert form.str_fields["zone"] == "Hub/Central"
    assert form.xyz_fields["xyz"] == (10.0, 20.0, 30.0)
    on_btn.assert_called_once_with("Record")
    assert call("Stop") not in on_btn.call_args_list


async def test_go_to_xyz_status_contains_zone_suffix():
    client = _client(zone="Hub/Central")
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("teleport_to_coordinates", client, form, on_status, on_btn, try_set_label)

    last_status = on_status.call_args_list[-1][0][0]
    assert "Central" in last_status


async def test_go_to_xyz_cancellation():
    client = _client()
    client.zone_name.side_effect = asyncio.CancelledError
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("teleport_to_coordinates", client, form, on_status, on_btn, try_set_label)

    on_status.assert_called_with("")
    on_btn.assert_called_with("Record")
    assert form.str_fields == {}


async def test_go_to_xyz_exception_shows_error():
    client = _client()
    client.zone_name.side_effect = RuntimeError("connection lost")
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("teleport_to_coordinates", client, form, on_status, on_btn, try_set_label)

    last_status = on_status.call_args_list[-1][0][0]
    assert last_status.startswith("Error:")
    on_btn.assert_called_with("Record")


# ---------------------------------------------------------------------------
# _record_mark_location  (instant, structurally identical to go_to_xyz)
# ---------------------------------------------------------------------------


async def test_mark_location_happy_path():
    client = _client(zone="Zone/Name", pos=XYZ(5.0, 6.0, 7.0))
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("mark_location", client, form, on_status, on_btn, try_set_label)

    assert form.str_fields["zone"] == "Zone/Name"
    assert form.xyz_fields["xyz"] == (5.0, 6.0, 7.0)
    on_btn.assert_called_once_with("Record")
    assert call("Stop") not in on_btn.call_args_list


async def test_mark_location_cancellation():
    client = _client()
    client.zone_name.side_effect = asyncio.CancelledError
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("mark_location", client, form, on_status, on_btn, try_set_label)

    on_status.assert_called_with("")
    on_btn.assert_called_with("Record")
    assert form.str_fields == {}


async def test_mark_location_exception():
    client = _client()
    client.body.position.side_effect = RuntimeError("memory error")
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("mark_location", client, form, on_status, on_btn, try_set_label)

    last_status = on_status.call_args_list[-1][0][0]
    assert last_status.startswith("Error:")
    on_btn.assert_called_with("Record")


# ---------------------------------------------------------------------------
# _record_teleport_to_marked_location  (instant but shows Stop button)
# ---------------------------------------------------------------------------


async def test_teleport_to_marked_location_happy_path():
    client = _client(zone="Zone/Dest", pos=XYZ(1.0, 2.0, 3.0))
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("teleport_to_marked_location", client, form, on_status, on_btn, try_set_label)

    assert on_btn.call_args_list[0] == call("Stop")
    assert form.str_fields["zone"] == "Zone/Dest"
    assert form.xyz_fields["xyz"] == (1.0, 2.0, 3.0)
    try_set_label.assert_called_once()
    assert on_btn.call_args_list[-1] == call("Record")
    # Verify fix: status must NOT say "Recall button"
    all_statuses = [c[0][0] for c in on_status.call_args_list]
    assert not any("Recall button" in s for s in all_statuses)


async def test_teleport_to_marked_location_cancellation():
    client = _client()
    client.zone_name.side_effect = asyncio.CancelledError
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("teleport_to_marked_location", client, form, on_status, on_btn, try_set_label)

    on_status.assert_called_with("")
    on_btn.assert_called_with("Record")


async def test_teleport_to_marked_location_exception():
    client = _client()
    client.zone_name.side_effect = RuntimeError("boom")
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("teleport_to_marked_location", client, form, on_status, on_btn, try_set_label)

    last_status = on_status.call_args_list[-1][0][0]
    assert last_status.startswith("Error:")
    on_btn.assert_called_with("Record")


# ---------------------------------------------------------------------------
# _record_enter_zone_gateway  (loading-screen polling loop)
# ---------------------------------------------------------------------------


async def test_enter_zone_gateway_happy_path(zero_poll):
    client = FakeClient()
    # zone_name: from_zone initially, then to_zone after loading clears
    client.zone_name.side_effect = ["Src/Hub", "Dst/Zone"]
    client.body.position.side_effect = [
        XYZ(0.0, 0.0, 0.0),  # start_pos / reset_xyz
        XYZ(5.0, 5.0, 0.0),  # poll 1 (not loading)
        XYZ(9.0, 9.0, 0.0),  # poll 2 (last before loading)
    ]
    client.is_loading.side_effect = [False, False, True, False]

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("enter_zone", client, form, on_status, on_btn, try_set_label)

    assert form.str_fields["from_zone"] == "Src/Hub"
    assert form.str_fields["to_zone"] == "Dst/Zone"
    assert form.xyz_fields["reset_xyz"] == (0.0, 0.0, 0.0)
    assert form.xyz_fields["xyz"] == (9.0, 9.0, 0.0)
    try_set_label.assert_called_once()
    assert on_btn.call_args_list[0] == call("Stop")
    assert on_btn.call_args_list[-1] == call("Record")


async def test_enter_zone_gateway_loading_fires_immediately(zero_poll):
    """When is_loading() is True on the very first poll, xyz must equal start_pos."""
    client = FakeClient()
    client.zone_name.side_effect = ["Src/A", "Dst/B"]
    start = XYZ(0.0, 0.0, 0.0)
    client.body.position.return_value = start
    client.is_loading.side_effect = [True, False]  # loading on first poll

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("enter_zone", client, form, on_status, on_btn, try_set_label)

    assert form.xyz_fields["xyz"] == (0.0, 0.0, 0.0)


async def test_enter_zone_gateway_zone_name_timeout(zero_poll):
    """If _wait_for_zone_name times out, to_zone is None and status says unknown."""
    client = FakeClient()
    client.zone_name.side_effect = ["Src/A", None]  # second call (after load) returns None
    client.body.position.return_value = XYZ(0.0, 0.0, 0.0)
    client.is_loading.side_effect = [True, False]

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    with patch.object(recording, "RECORDING_POLL_INTERVAL", 0):
        # Also patch _wait_for_zone_name to return None immediately (timeout=0)
        orig = recording._wait_for_zone_name

        async def _instant_timeout(client, poll, timeout=10.0):
            return await orig(client, poll, timeout=0)

        with patch.object(recording, "_wait_for_zone_name", _instant_timeout):
            await run_recording("enter_zone", client, form, on_status, on_btn, try_set_label)

    assert form.str_fields.get("to_zone") is None
    last_status = on_status.call_args_list[-1][0][0]
    assert "(unknown zone)" in last_status


async def test_enter_zone_gateway_cancellation(zero_poll):
    client = FakeClient()
    client.zone_name.return_value = "Src/Hub"
    client.body.position.return_value = XYZ(0.0, 0.0, 0.0)
    client.is_loading.return_value = False  # never loads — loop runs forever

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    task = asyncio.create_task(run_recording("enter_zone", client, form, on_status, on_btn, try_set_label))
    await asyncio.sleep(0)
    task.cancel()
    await task  # coroutine catches CancelledError internally; task completes normally

    on_status.assert_called_with("")
    on_btn.assert_called_with("Record")


async def test_enter_zone_gateway_exception(zero_poll):
    client = FakeClient()
    client.zone_name.return_value = "Src/Hub"
    client.body.position.return_value = XYZ(0.0, 0.0, 0.0)
    client.is_loading.side_effect = RuntimeError("WizWalker error")

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("enter_zone", client, form, on_status, on_btn, try_set_label)

    last_status = on_status.call_args_list[-1][0][0]
    assert last_status.startswith("Error:")
    on_btn.assert_called_with("Record")


# ---------------------------------------------------------------------------
# _record_enter_dungeon  (same loading-screen structure, different field names)
# ---------------------------------------------------------------------------


async def test_enter_dungeon_happy_path(zero_poll):
    client = FakeClient()
    client.zone_name.side_effect = ["Src/World", "Dungeon/Zone"]
    client.body.position.side_effect = [
        XYZ(0.0, 0.0, 0.0),  # reset_xyz
        XYZ(7.0, 8.0, 0.0),  # sigil_xyz (last before loading)
    ]
    client.is_loading.side_effect = [False, True, False]

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("enter_dungeon", client, form, on_status, on_btn, try_set_label)

    assert form.str_fields["from_zone"] == "Src/World"
    assert form.str_fields["dungeon_zone"] == "Dungeon/Zone"
    assert form.xyz_fields["reset_xyz"] == (0.0, 0.0, 0.0)
    assert form.xyz_fields["sigil_xyz"] == (7.0, 8.0, 0.0)
    try_set_label.assert_called_once()


async def test_enter_dungeon_cancellation(zero_poll):
    client = FakeClient()
    client.zone_name.return_value = "Src/World"
    client.body.position.return_value = XYZ(0.0, 0.0, 0.0)
    client.is_loading.return_value = False

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    task = asyncio.create_task(run_recording("enter_dungeon", client, form, on_status, on_btn, try_set_label))
    await asyncio.sleep(0)
    task.cancel()
    await task  # coroutine catches CancelledError internally; task completes normally

    on_status.assert_called_with("")
    on_btn.assert_called_with("Record")


async def test_enter_dungeon_exception(zero_poll):
    client = FakeClient()
    client.zone_name.return_value = "Src/World"
    client.body.position.side_effect = RuntimeError("read error")

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("enter_dungeon", client, form, on_status, on_btn, try_set_label)

    last_status = on_status.call_args_list[-1][0][0]
    assert last_status.startswith("Error:")
    on_btn.assert_called_with("Record")


# ---------------------------------------------------------------------------
# _record_interact  (NPC-range polling loop)
# ---------------------------------------------------------------------------


async def test_interact_happy_path(zero_poll):
    client = FakeClient()
    client.zone_name.return_value = "Zone/Name"
    client.body.position.side_effect = [
        XYZ(0.0, 0.0, 0.0),  # start_pos / reset_xyz
        XYZ(5.0, 5.0, 0.0),  # in-range poll 1
        XYZ(6.0, 6.0, 0.0),  # in-range poll 2 (last)
    ]
    client.is_in_npc_range.side_effect = [False, False, True, True, False]

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("interact", client, form, on_status, on_btn, try_set_label)

    assert form.str_fields["zone"] == "Zone/Name"
    assert form.xyz_fields["reset_xyz"] == (0.0, 0.0, 0.0)
    assert form.xyz_fields["interact_xyz"] == (6.0, 6.0, 0.0)
    try_set_label.assert_called_once()
    last_status = on_status.call_args_list[-1][0][0]
    assert "Name" in last_status


async def test_interact_xyz_tracks_updates_while_in_range(zero_poll):
    """interact_xyz is updated on every in-range poll; last value wins."""
    client = FakeClient()
    client.zone_name.return_value = "Z/A"
    client.body.position.side_effect = [
        XYZ(0.0, 0.0, 0.0),  # reset_xyz
        XYZ(1.0, 0.0, 0.0),
        XYZ(2.0, 0.0, 0.0),
        XYZ(3.0, 0.0, 0.0),
    ]
    client.is_in_npc_range.side_effect = [True, True, True, False]

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("interact", client, form, on_status, on_btn, try_set_label)

    assert form.xyz_fields["interact_xyz"] == (3.0, 0.0, 0.0)


async def test_interact_cancellation(zero_poll):
    client = FakeClient()
    client.zone_name.return_value = "Z/A"
    client.body.position.return_value = XYZ(0.0, 0.0, 0.0)
    client.is_in_npc_range.return_value = False  # never enters range

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    task = asyncio.create_task(run_recording("interact", client, form, on_status, on_btn, try_set_label))
    await asyncio.sleep(0)
    task.cancel()
    await task  # coroutine catches CancelledError internally; task completes normally

    on_status.assert_called_with("")
    on_btn.assert_called_with("Record")


async def test_interact_exception(zero_poll):
    client = FakeClient()
    client.zone_name.return_value = "Z/A"
    client.body.position.return_value = XYZ(0.0, 0.0, 0.0)
    client.is_in_npc_range.side_effect = RuntimeError("npc error")

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("interact", client, form, on_status, on_btn, try_set_label)

    assert on_status.call_args_list[-1][0][0].startswith("Error:")
    on_btn.assert_called_with("Record")


# ---------------------------------------------------------------------------
# _record_interact_menu  (same loop, different final status)
# ---------------------------------------------------------------------------


async def test_interact_menu_happy_path(zero_poll):
    client = FakeClient()
    client.zone_name.return_value = "Zone/Shop"
    client.body.position.side_effect = [XYZ(0.0, 0.0, 0.0), XYZ(4.0, 4.0, 0.0)]
    client.is_in_npc_range.side_effect = [True, False]

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("interact_menu", client, form, on_status, on_btn, try_set_label)

    assert form.str_fields["zone"] == "Zone/Shop"
    assert "interact_xyz" in form.xyz_fields
    last_status = on_status.call_args_list[-1][0][0]
    assert "Menu Path" in last_status


async def test_interact_menu_cancellation(zero_poll):
    client = FakeClient()
    client.zone_name.return_value = "Z/A"
    client.body.position.return_value = XYZ(0.0, 0.0, 0.0)
    client.is_in_npc_range.return_value = False

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    task = asyncio.create_task(run_recording("interact_menu", client, form, on_status, on_btn, try_set_label))
    await asyncio.sleep(0)
    task.cancel()
    await task  # coroutine catches CancelledError internally; task completes normally

    on_status.assert_called_with("")
    on_btn.assert_called_with("Record")


async def test_interact_menu_exception(zero_poll):
    client = FakeClient()
    client.zone_name.side_effect = RuntimeError("err")

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("interact_menu", client, form, on_status, on_btn, try_set_label)

    assert on_status.call_args_list[-1][0][0].startswith("Error:")


# ---------------------------------------------------------------------------
# _record_apply_level_scaling  (same loop, distinctive status messages)
# ---------------------------------------------------------------------------


async def test_apply_level_scaling_happy_path(zero_poll):
    client = FakeClient()
    client.zone_name.return_value = "Zone/Scale"
    client.body.position.side_effect = [XYZ(0.0, 0.0, 0.0), XYZ(2.0, 2.0, 0.0)]
    client.is_in_npc_range.side_effect = [True, False]

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("apply_level_scaling", client, form, on_status, on_btn, try_set_label)

    assert form.str_fields["zone"] == "Zone/Scale"
    assert "interact_xyz" in form.xyz_fields
    all_statuses = [c[0][0] for c in on_status.call_args_list]
    assert any("level-scaling NPC" in s for s in all_statuses)


async def test_apply_level_scaling_cancellation(zero_poll):
    client = FakeClient()
    client.zone_name.return_value = "Z/A"
    client.body.position.return_value = XYZ(0.0, 0.0, 0.0)
    client.is_in_npc_range.return_value = False

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    task = asyncio.create_task(run_recording("apply_level_scaling", client, form, on_status, on_btn, try_set_label))
    await asyncio.sleep(0)
    task.cancel()
    await task  # coroutine catches CancelledError internally; task completes normally

    on_status.assert_called_with("")
    on_btn.assert_called_with("Record")


async def test_apply_level_scaling_exception(zero_poll):
    client = FakeClient()
    client.zone_name.side_effect = RuntimeError("err")

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("apply_level_scaling", client, form, on_status, on_btn, try_set_label)

    assert on_status.call_args_list[-1][0][0].startswith("Error:")


# ---------------------------------------------------------------------------
# _record_enter_zone_interact  (NPC-range + loading-screen, combined)
# ---------------------------------------------------------------------------


async def test_enter_zone_interact_happy_path(zero_poll):
    client = FakeClient()
    client.zone_name.side_effect = ["Src/Zone", "Dst/Zone"]
    client.body.position.side_effect = [
        XYZ(0.0, 0.0, 0.0),  # reset_xyz
        XYZ(4.0, 4.0, 0.0),  # interact_xyz (in-range)
    ]
    client.is_in_npc_range.side_effect = [True, False]
    client.is_loading.side_effect = [False, True, False]

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("enter_zone_via_object", client, form, on_status, on_btn, try_set_label)

    assert form.str_fields["from_zone"] == "Src/Zone"
    assert form.str_fields["to_zone"] == "Dst/Zone"
    assert form.xyz_fields["reset_xyz"] == (0.0, 0.0, 0.0)
    assert "interact_xyz" in form.xyz_fields
    try_set_label.assert_called_once()


async def test_enter_zone_interact_cancellation_during_npc_wait(zero_poll):
    client = FakeClient()
    client.zone_name.return_value = "Src/Zone"
    client.body.position.return_value = XYZ(0.0, 0.0, 0.0)
    client.is_in_npc_range.return_value = False  # never in range

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    task = asyncio.create_task(run_recording("enter_zone_via_object", client, form, on_status, on_btn, try_set_label))
    await asyncio.sleep(0)
    task.cancel()
    await task  # coroutine catches CancelledError internally; task completes normally

    on_status.assert_called_with("")
    on_btn.assert_called_with("Record")


async def test_enter_zone_interact_cancellation_during_loading_wait(zero_poll):
    client = FakeClient()
    client.zone_name.return_value = "Src/Zone"
    client.body.position.side_effect = [XYZ(0.0, 0.0, 0.0), XYZ(1.0, 0.0, 0.0)]
    client.is_in_npc_range.side_effect = [True, False]
    client.is_loading.return_value = False  # "wait for loading to start" loop never exits

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    task = asyncio.create_task(run_recording("enter_zone_via_object", client, form, on_status, on_btn, try_set_label))
    await asyncio.sleep(0)
    task.cancel()
    await task  # coroutine catches CancelledError internally; task completes normally

    on_status.assert_called_with("")
    on_btn.assert_called_with("Record")


async def test_enter_zone_interact_exception(zero_poll):
    client = FakeClient()
    client.zone_name.return_value = "Src/Zone"
    client.body.position.side_effect = RuntimeError("err")

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("enter_zone_via_object", client, form, on_status, on_btn, try_set_label)

    assert on_status.call_args_list[-1][0][0].startswith("Error:")


# ---------------------------------------------------------------------------
# _record_enter_zone_via_list  (NPC-range + interact-until-loading + zone capture)
# ---------------------------------------------------------------------------


async def test_enter_zone_via_list_happy_path(zero_poll):
    client = FakeClient()
    client.zone_name.side_effect = ["Zone/List", "Dst/World"]
    client.body.position.side_effect = [
        XYZ(0.0, 0.0, 0.0),  # reset_xyz
        XYZ(3.0, 3.0, 0.0),  # interact_xyz (captured during interact-loop body)
    ]
    client.is_in_npc_range.side_effect = [True]  # already in range immediately
    client.is_loading.side_effect = [False, True, False]  # one interact-loop body, then loading

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("enter_zone_via_list", client, form, on_status, on_btn, try_set_label)

    assert form.str_fields["zone"] == "Zone/List"
    assert form.str_fields["to_zone"] == "Dst/World"
    assert "interact_xyz" in form.xyz_fields
    assert "zone_list_entry" in form.str_fields
    try_set_label.assert_called_once()
    last_status = on_status.call_args_list[-1][0][0]
    assert "Zone List Entry" in last_status


async def test_enter_zone_via_list_interact_xyz_is_pre_loading_position(zero_poll):
    """interact_xyz must be the position captured BEFORE is_loading() returned True."""
    client = FakeClient()
    client.zone_name.side_effect = ["Zone/List", "Dst/World"]
    client.body.position.side_effect = [
        XYZ(0.0, 0.0, 0.0),  # reset_xyz
        XYZ(1.0, 0.0, 0.0),  # interact-loop iteration 1 (not loading yet)
        XYZ(2.0, 0.0, 0.0),  # interact-loop iteration 2 (loading fires after this)
    ]
    client.is_in_npc_range.side_effect = [True]
    # is_loading: first two calls False (during interact loop), then True, then False
    client.is_loading.side_effect = [False, False, True, False]

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("enter_zone_via_list", client, form, on_status, on_btn, try_set_label)

    # The last position set before loading fired
    assert form.xyz_fields["interact_xyz"] == (2.0, 0.0, 0.0)


async def test_enter_zone_via_list_cancellation_during_npc_wait(zero_poll):
    client = FakeClient()
    client.zone_name.return_value = "Zone/List"
    client.body.position.return_value = XYZ(0.0, 0.0, 0.0)
    client.is_in_npc_range.return_value = False

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    task = asyncio.create_task(run_recording("enter_zone_via_list", client, form, on_status, on_btn, try_set_label))
    await asyncio.sleep(0)
    task.cancel()
    await task  # coroutine catches CancelledError internally; task completes normally

    on_status.assert_called_with("")
    on_btn.assert_called_with("Record")


async def test_enter_zone_via_list_cancellation_during_interact_loop(zero_poll):
    client = FakeClient()
    client.zone_name.return_value = "Zone/List"
    client.body.position.return_value = XYZ(0.0, 0.0, 0.0)
    client.is_in_npc_range.return_value = True  # already in range
    client.is_loading.return_value = False  # never loads

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    task = asyncio.create_task(run_recording("enter_zone_via_list", client, form, on_status, on_btn, try_set_label))
    await asyncio.sleep(0)
    task.cancel()
    await task  # coroutine catches CancelledError internally; task completes normally

    on_status.assert_called_with("")
    on_btn.assert_called_with("Record")


async def test_enter_zone_via_list_exception(zero_poll):
    client = FakeClient()
    client.zone_name.return_value = "Zone/List"
    client.body.position.side_effect = RuntimeError("read error")

    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("enter_zone_via_list", client, form, on_status, on_btn, try_set_label)

    assert on_status.call_args_list[-1][0][0].startswith("Error:")
    on_btn.assert_called_with("Record")
