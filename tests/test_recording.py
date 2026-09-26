"""Tests for bartlebot.gui.canvas.recording."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from wizwalker import XYZ

import bartlebot.gui.canvas.recording as recording
from bartlebot.gui.canvas.recording import (
    RECORDABLE_NODES,
    _wait_for_zone_name,
    _zone_display,
    cancel_active_recording,
    is_recorded_label,
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


def test_zone_display_is_the_zones_game_name():
    assert _zone_display("WizardCity/WC_Streets/WC_Triton") == "Triton Avenue"


def test_zone_display_of_an_unknown_zone_is_its_last_path_segment():
    assert _zone_display("Nowhere/NW_Hub") == "NW_Hub"
    assert _zone_display("FlatZone") == "FlatZone"


# Two zones whose game names differ, and a dungeon interior that shares its street's name.
_TEMPLE, _WELL = "Krokotopia/KT_Tomb/KT_TempleOfStorms", "Krokotopia/KT_Tomb/KT_WellOfSpirits"
_TRITON, _TRITON_TOWER = "WizardCity/WC_Streets/WC_Triton", "WizardCity/WC_Streets/Interiors/WC_Triton_T1"


def test_a_zone_change_between_differently_named_zones_is_labelled_by_name():
    assert recording._transition_label(_TEMPLE, _WELL) == "Temple of Storms → Well of Spirits"


@pytest.mark.parametrize(
    ("from_zone", "to_zone", "expected"),
    [
        (_TRITON, _TRITON_TOWER, "WC_Triton → WC_Triton_T1"),  # same game name
        (_TEMPLE, "Nowhere/NW_Hub", "KT_TempleOfStorms → NW_Hub"),  # one has no game name
    ],
)
def test_a_zone_change_names_cannot_tell_apart_is_labelled_by_path_segment(from_zone, to_zone, expected):
    assert recording._transition_label(from_zone, to_zone) == expected


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
        "enter_gauntlet",
        "interact",
        "interact_menu",
        "apply_level_scaling",
        "enter_zone_via_object",
        "mark_location",
        "teleport_to_marked_location",
        "handle_combat",
    }
    assert RECORDABLE_NODES == expected


async def test_a_cancelled_recording_puts_back_what_the_form_held(zero_poll):
    """Half a recording is values never recorded together: a cancel puts every field
    the workflow fills back as it was, including one that was blank."""
    form = FakeParamForm()
    form.set_str("from_zone", "Old/Zone")
    form.set_str("to_zone", "Old/Dest")
    form.set_xyz("reset_xyz", 9.0, 9.0, 9.0)  # interact_xyz starts blank

    client = FakeClient()
    client.zone_name.return_value = "Src/Zone"
    client.body.position.return_value = XYZ(1.0, 2.0, 3.0)
    client.is_in_npc_range.side_effect = [True, False]
    waiting_for_load = asyncio.Event()

    def not_loading_yet():
        waiting_for_load.set()
        return False

    client.is_loading.side_effect = not_loading_yet
    on_status, on_btn, try_set_label = _callbacks()

    task = asyncio.create_task(run_recording("enter_zone_via_object", client, form, on_status, on_btn, try_set_label))
    await waiting_for_load.wait()
    assert form.xyz_fields["interact_xyz"] == (1.0, 2.0, 3.0), "recorded values show as they are captured"
    task.cancel()
    await task

    assert form.str_fields == {"from_zone": "Old/Zone", "to_zone": "Old/Dest"}
    assert form.xyz_fields == {"reset_xyz": (9.0, 9.0, 9.0)}
    on_btn.assert_called_with("Record")


async def test_a_failed_recording_puts_back_what_the_form_held(zero_poll):
    form = FakeParamForm()
    form.set_str("from_zone", "Old/Zone")
    form.set_xyz("xyz", 5.0, 5.0, 5.0)

    client = FakeClient()
    client.zone_name.return_value = "Src/Zone"
    client.body.position.side_effect = [XYZ(0.0, 0.0, 0.0), RuntimeError("read error")]
    client.is_loading.return_value = False
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("enter_zone", client, form, on_status, on_btn, try_set_label)

    assert form.str_fields == {"from_zone": "Old/Zone"}
    assert form.xyz_fields == {"xyz": (5.0, 5.0, 5.0)}
    assert on_status.call_args_list[-1][0][0].startswith("Error:")


@pytest.mark.parametrize("node_type", sorted(RECORDABLE_NODES))
async def test_a_recording_fills_exactly_the_nodes_zone_and_xyz_params(node_type, zero_poll):
    """The keys a workflow fills are data in _REGISTRY, so a misspelt one would record
    into a field the node does not have while its real one stays empty."""
    from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS  # noqa: PLC0415

    client = _client()
    # Enough for any workflow: the prompt shows then goes, a load starts then ends, a mob is near.
    client.is_in_npc_range.side_effect = [True, False]
    client.is_loading.side_effect = [True, False]
    client.find_closest_mob.return_value = _mob("Test-Mob")
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording(node_type, client, form, on_status, on_btn, try_set_label, player=1)

    fields = NODE_PARAM_SCHEMAS[node_type]
    # A plain-text param is filled only by the workflow that names it; every other is a zone.
    assert set(form.str_fields) == {f.key for f in fields if f.type == "zone"} | (
        {f.key for f in fields if f.type == "str"} & set(recording._REGISTRY[node_type].keys())
    )
    assert set(form.str_fields) <= {f.key for f in fields}
    assert set(form.xyz_fields) == {f.key for f in fields if f.type == "xyz"}
    assert set(form.player_fields) <= {f.key for f in fields if f.type == "player_list"}
    assert on_status.call_args_list[-1][0][0].startswith("Done")


# ---------------------------------------------------------------------------
# teleport_to_coordinates  (_Point: instant, no Stop button)
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
# mark_location  (_Point, as teleport_to_coordinates)
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
# teleport_to_marked_location  (_Point, but shows Stop and sets the label)
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
# enter_zone  (_WalkIn: loading-screen polling loop)
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
# enter_dungeon  (_WalkIn, different field names)
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
# interact  (_Interact: NPC-range polling loop)
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
# interact_menu  (_Interact, different final status)
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
# apply_level_scaling  (_Interact, distinctive status messages)
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
# enter_zone_via_object  (_Interact with a destination: NPC range, then loading screen)
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
# interact / interact_menu — naming the target and the player who used it
# ---------------------------------------------------------------------------


def _interact_client():
    """A client that walks into range for one poll, then out."""
    client = FakeClient()
    client.zone_name.return_value = "Zone/Name"
    client.body.position.return_value = XYZ(1.0, 2.0, 3.0)
    client.is_in_npc_range.side_effect = [True, False]
    return client


@pytest.fixture
def prompt_title(monkeypatch):
    """The interact prompt's title, as the game would show it."""
    read = AsyncMock(return_value="<center>Cyrus Drake")
    monkeypatch.setattr(recording.node_helpers, "get_text_by_path", read)
    return read


@pytest.mark.parametrize("node_type", ["interact", "interact_menu"])
async def test_interact_is_named_after_the_prompt_title(zero_poll, prompt_title, node_type):
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording(node_type, _interact_client(), FakeParamForm(), on_status, on_btn, try_set_label)

    try_set_label.assert_called_once_with("Interact with Cyrus Drake")
    assert prompt_title.await_args.args[1] == recording.NPC_RANGE_TITLE_PATH


async def test_interact_with_no_readable_title_is_named_after_the_zone(zero_poll, prompt_title):
    prompt_title.return_value = ""
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("interact", _interact_client(), FakeParamForm(), on_status, on_btn, try_set_label)

    try_set_label.assert_called_once_with(_zone_display("Zone/Name"))


async def test_a_title_read_that_fails_does_not_fail_the_recording(zero_poll, prompt_title):
    prompt_title.side_effect = RuntimeError("stale window")
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("interact", _interact_client(), form, on_status, on_btn, try_set_label)

    try_set_label.assert_called_once_with(_zone_display("Zone/Name"))
    assert form.xyz_fields["interact_xyz"] == (1.0, 2.0, 3.0)


async def test_apply_level_scaling_keeps_its_zone_label(zero_poll, prompt_title):
    """Only interact and interact_menu are named after their target."""
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("apply_level_scaling", _interact_client(), FakeParamForm(), on_status, on_btn, try_set_label)

    try_set_label.assert_called_once_with(_zone_display("Zone/Name"))


@pytest.mark.parametrize("node_type", ["interact", "interact_menu"])
async def test_an_empty_player_list_becomes_the_recorded_player(zero_poll, node_type):
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording(node_type, _interact_client(), form, on_status, on_btn, try_set_label, player=2)

    assert form.player_fields["interact_clients"] == [2]


async def test_a_filled_player_list_is_left_alone(zero_poll):
    form = FakeParamForm()
    form.set_players("interact_clients", [1, 3])
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("interact", _interact_client(), form, on_status, on_btn, try_set_label, player=2)

    assert form.player_fields["interact_clients"] == [1, 3]


async def test_no_player_named_leaves_the_player_list_empty(zero_poll):
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("interact", _interact_client(), form, on_status, on_btn, try_set_label)

    assert "interact_clients" not in form.player_fields


# ---------------------------------------------------------------------------
# enter_dungeon / enter_gauntlet / enter_zone_via_object — named after the entrance
# ---------------------------------------------------------------------------


def _walk_in_client(from_zone=_TRITON, to_zone=_TRITON_TOWER):
    """A client standing in an entrance prompt for two polls, then loading into *to_zone*."""
    client = FakeClient()
    client.zone_name.side_effect = [from_zone, to_zone]
    client.body.position.return_value = XYZ(1.0, 2.0, 3.0)
    client.is_in_npc_range.return_value = True
    client.is_loading.side_effect = [False, False, True, False]
    return client


@pytest.mark.parametrize("node_type", ["enter_dungeon", "enter_gauntlet"])
async def test_a_dungeon_entrance_is_named_after_the_prompt_title(zero_poll, prompt_title, node_type):
    prompt_title.return_value = "<center>Skull Fort"
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording(node_type, _walk_in_client(), FakeParamForm(), on_status, on_btn, try_set_label)

    try_set_label.assert_called_once_with("Enter Skull Fort")


@pytest.mark.parametrize("mode", ["Standard", "Challenge"])
async def test_a_sigils_mode_is_left_out_of_the_name(zero_poll, prompt_title, mode):
    prompt_title.return_value = f"<center>Skull Fort ({mode})"
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("enter_dungeon", _walk_in_client(), FakeParamForm(), on_status, on_btn, try_set_label)

    try_set_label.assert_called_once_with("Enter Skull Fort")


async def test_the_title_is_kept_when_the_prompt_closes_before_the_load(zero_poll, prompt_title):
    prompt_title.side_effect = ["<center>Skull Fort", ""]
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("enter_dungeon", _walk_in_client(), FakeParamForm(), on_status, on_btn, try_set_label)

    try_set_label.assert_called_once_with("Enter Skull Fort")


async def test_a_dungeon_entrance_with_no_readable_title_is_named_after_the_zone_change(zero_poll, prompt_title):
    prompt_title.return_value = ""
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("enter_dungeon", _walk_in_client(), FakeParamForm(), on_status, on_btn, try_set_label)

    try_set_label.assert_called_once_with("WC_Triton → WC_Triton_T1")


async def test_enter_gauntlet_records_only_the_dungeon_zone(zero_poll, prompt_title):
    """The player walks to a gauntlet's sigil each run, so there is no position to keep."""
    client = _walk_in_client()
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("enter_gauntlet", client, form, on_status, on_btn, try_set_label)

    assert form.str_fields == {"dungeon_zone": _TRITON_TOWER}
    assert form.xyz_fields == {}
    client.body.position.assert_not_awaited()


@pytest.mark.parametrize(
    ("from_zone", "to_zone", "expected"),
    [
        (_TEMPLE, _WELL, "Enter Well of Spirits from Temple of Storms"),
        (_TRITON, _TRITON_TOWER, "WC_Triton → WC_Triton_T1"),
    ],
)
async def test_enter_zone_is_named_after_both_zones(zero_poll, from_zone, to_zone, expected):
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording(
        "enter_zone", _walk_in_client(from_zone, to_zone), FakeParamForm(), on_status, on_btn, try_set_label
    )

    try_set_label.assert_called_once_with(expected)


def _object_client(from_zone, to_zone):
    """A client in range of an object for one poll, then loading into *to_zone*."""
    client = FakeClient()
    client.zone_name.side_effect = [from_zone, to_zone]
    client.body.position.return_value = XYZ(1.0, 2.0, 3.0)
    client.is_in_npc_range.side_effect = [True, False]
    client.is_loading.side_effect = [True, False]
    return client


@pytest.mark.parametrize(
    ("from_zone", "to_zone", "expected"),
    [
        (_TEMPLE, _WELL, "Enter Well of Spirits via Teleporter"),
        (_TRITON, _TRITON_TOWER, "WC_Triton → WC_Triton_T1 via Teleporter"),
    ],
)
async def test_enter_zone_via_object_is_named_after_the_object(zero_poll, prompt_title, from_zone, to_zone, expected):
    prompt_title.return_value = "<center>Teleporter"
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording(
        "enter_zone_via_object", _object_client(from_zone, to_zone), FakeParamForm(), on_status, on_btn, try_set_label
    )

    try_set_label.assert_called_once_with(expected)


async def test_enter_zone_via_object_with_no_readable_title_is_named_after_the_zone_change(zero_poll, prompt_title):
    prompt_title.return_value = ""
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording(
        "enter_zone_via_object", _object_client(_TEMPLE, _WELL), FakeParamForm(), on_status, on_btn, try_set_label
    )

    try_set_label.assert_called_once_with("Temple of Storms → Well of Spirits")


# ---------------------------------------------------------------------------
# handle_combat  (_Enemy: the closest mob, at once)
# ---------------------------------------------------------------------------


def _mob(name):
    mob = MagicMock()
    mob.object_name = AsyncMock(return_value=name)
    return mob


async def test_handle_combat_records_the_closest_mob_and_its_display_name(monkeypatch):
    names = {"AQ-Centaur": "Crescent Moon Centaur"}
    monkeypatch.setattr(recording.entity_names, "display_name", names.get)
    client = FakeClient()
    client.find_closest_mob.return_value = _mob("AQ-Centaur")
    form = FakeParamForm()
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("handle_combat", client, form, on_status, on_btn, try_set_label)

    assert form.str_fields["enemy"] == "AQ-Centaur"
    try_set_label.assert_called_once_with("Defeat Crescent Moon Centaur")
    assert on_status.call_args_list[-1][0][0] == "Done — Crescent Moon Centaur"
    assert call("Stop") not in on_btn.call_args_list, "an instant capture has nothing to stop"


async def test_handle_combat_falls_back_to_the_object_name(monkeypatch):
    monkeypatch.setattr(recording.entity_names, "display_name", lambda *_: None)
    client = FakeClient()
    client.find_closest_mob.return_value = _mob("Test-Unnamed-Mob")
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("handle_combat", client, FakeParamForm(), on_status, on_btn, try_set_label)

    try_set_label.assert_called_once_with("Defeat Test-Unnamed-Mob")


async def test_handle_combat_with_no_mob_nearby_changes_nothing():
    form = FakeParamForm()
    form.set_str("enemy", "Kept")
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("handle_combat", FakeClient(), form, on_status, on_btn, try_set_label)

    assert form.str_fields["enemy"] == "Kept"
    try_set_label.assert_not_called()
    assert on_status.call_args_list[-1][0][0] == "No enemy nearby."


async def test_handle_combat_read_failure_reports_and_restores():
    client = FakeClient()
    client.find_closest_mob.side_effect = RuntimeError("stale entity list")
    form = FakeParamForm()
    form.set_str("enemy", "Kept")
    on_status, on_btn, try_set_label = _callbacks()

    await run_recording("handle_combat", client, form, on_status, on_btn, try_set_label)

    assert form.str_fields["enemy"] == "Kept"
    assert on_status.call_args_list[-1][0][0].startswith("Error:")


# ---------------------------------------------------------------------------
# is_recorded_label — which labels recording again may replace
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label",
    [
        "Interact with Cyrus Drake",
        "Defeat Rattlebones",
        "Enter Skull Fort",
        "Enter Well of Spirits via Teleporter",
        "Enter Well of Spirits from Temple of Storms",
        "WC_Triton → WC_Triton_T1 via Teleporter",
        "WC_Hub → WC_Streets",
        "WC_Hub",
        "Triton Avenue",
        "(unknown zone)",
    ],
)
def test_labels_record_writes_are_recognised(label):
    assert is_recorded_label(label)


@pytest.mark.parametrize("label", ["Talk to the headmaster", "Boss", "Defeat", "Interact with", "Enter", "Loot"])
def test_labels_an_author_typed_are_kept(label):
    assert not is_recorded_label(label)
