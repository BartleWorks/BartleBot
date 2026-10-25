"""Unit tests for the interact_menu node."""

from unittest.mock import AsyncMock, patch

import pytest

import bartlebot.nodes.interaction.interact_menu as im
from bartlebot.nodes import node_helpers
from tests.conftest import FakeClient, FakeMachine, prompt_at, zone_unreadable_at_first

_MENU_PATH = ["WorldView", "main"]

_PARAMS = {
    "zone": "TestZone",
    "interact_xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
    "reset_xyz": {"x": 1.0, "y": 0.0, "z": 0.0},
    "menu_path": _MENU_PATH,
    "menu_timeout": 0.1,
    "interact_clients": [1],
}


@pytest.fixture(autouse=True)
def _no_prompt_clear_settle(monkeypatch):
    """The node polls reach_prompt at the real POLL_INTERVAL, so the settle at reset_xyz
    would cost every test half a second; it still takes two reads to pass."""
    monkeypatch.setattr(node_helpers, "PROMPT_CLEAR_SETTLE", 0.0)


def _ready_client() -> FakeClient:
    """Client with no stale prompt, whose interact prompt shows at interact_xyz and nowhere else."""
    c = FakeClient()
    prompt_at(c, _PARAMS["interact_xyz"])
    return c


async def test_success_menu_appears():
    """Happy path: prompt appears, X pressed, menu visible → SUCCESS."""
    client = _ready_client()

    with (
        patch.object(im, "POLL_INTERVAL", 0),
        patch.object(node_helpers, "is_visible_by_path", AsyncMock(return_value=True)),
    ):
        result = await im.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    # Reset first, whether or not a prompt showed (#152), then the one approach.
    assert [tuple(call.args[0]) for call in client.teleport.call_args_list] == [(1.0, 0.0, 0.0), (0.0, 0.0, 0.0)]
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in client.send_key.call_args_list)


async def test_menu_never_appears_fails():
    """Menu path never becomes visible → FAILURE."""
    client = _ready_client()

    with (
        patch.object(im, "POLL_INTERVAL", 0),
        patch.object(node_helpers, "is_visible_by_path", AsyncMock(return_value=False)),
    ):
        result = await im.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_prompt_never_appears_fails():
    """NPC prompt never shows after MAX_RETRIES → FAILURE; menu check never reached."""
    client = FakeClient()
    client.is_in_npc_range.return_value = False

    with (
        patch.object(im, "POLL_INTERVAL", 0),
        patch.object(node_helpers, "is_visible_by_path", AsyncMock(return_value=True)),
    ):
        result = await im.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    # The first reset, then teleport(interact) + teleport(reset) for each attempt
    assert client.teleport.call_count == 1 + im.MAX_RETRIES * 2


async def test_wrong_zone_fails():
    """Pre-condition failure: client is not in the expected zone."""
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"

    result = await im.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_a_zone_unreadable_at_first_is_waited_for_not_failed():
    """Just off a loading screen the zone reads None for a moment (#282)."""
    client = FakeClient()
    zone_unreadable_at_first(client, _PARAMS["zone"])

    # No interacting player, so the node succeeds as soon as its zone check passes.
    result = await im.run(FakeMachine(), [client], {**_PARAMS, "interact_clients": []})

    assert result == "SUCCESS"


async def test_one_client_wrong_zone_fails():
    """Pre-condition failure: one of two clients is in the wrong zone."""
    good = FakeClient()
    bad = FakeClient()
    bad.zone_name.return_value = "WrongZone"

    result = await im.run(FakeMachine(), [good, bad], _PARAMS)

    assert result == "FAILURE"
    good.teleport.assert_not_called()
    bad.teleport.assert_not_called()


async def test_stale_prompt_cleared():
    """A pre-existing prompt is dismissed via reset_xyz before the interaction loop."""
    client = FakeClient()
    prompt_at(client, _PARAMS["interact_xyz"], initially=True)  # stale on entry

    with (
        patch.object(im, "POLL_INTERVAL", 0),
        patch.object(node_helpers, "is_visible_by_path", AsyncMock(return_value=True)),
    ):
        result = await im.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    first_tp, second_tp = client.teleport.call_args_list[:2]
    assert first_tp.args[0].x == 1.0  # reset_xyz
    assert second_tp.args[0].x == 0.0  # interact_xyz


async def test_prompt_retry_then_success():
    """Prompt times out on first attempt; appears after reset on second attempt → SUCCESS."""
    client = FakeClient()
    reset_visited: list[bool] = []

    async def tracking_teleport(xyz):
        if xyz.x == 1.0:
            reset_visited.append(True)

    client.teleport.side_effect = tracking_teleport

    async def npc_range():
        # Only at interact_xyz, and only once the client has been reset twice: the
        # reset every run starts with, and the one after the first miss.
        calls = client.teleport.call_args_list
        return bool(calls) and calls[-1].args[0].x == 0.0 and len(reset_visited) >= 2

    client.is_in_npc_range.side_effect = npc_range

    with (
        patch.object(im, "POLL_INTERVAL", 0),
        patch.object(node_helpers, "is_visible_by_path", AsyncMock(return_value=True)),
    ):
        result = await im.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert len(reset_visited) == 2
    assert client.teleport.call_count == 4  # reset, interact, reset, interact


async def test_interact_clients_subset():
    """Only the specified client interacts; the other is not touched."""
    c1 = FakeClient()
    c2 = _ready_client()
    params = {**_PARAMS, "interact_clients": [2]}

    with (
        patch.object(im, "POLL_INTERVAL", 0),
        patch.object(node_helpers, "is_visible_by_path", AsyncMock(return_value=True)),
    ):
        result = await im.run(FakeMachine(), [c1, c2], params)

    assert result == "SUCCESS"
    c1.teleport.assert_not_called()
    c1.send_key.assert_not_called()
    c2.teleport.assert_called()
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in c2.send_key.call_args_list)


async def test_all_clients_filtered_succeeds():
    """interact_clients resolves to empty → SUCCESS immediately, no interaction."""
    client = FakeClient()
    params = {**_PARAMS, "interact_clients": [5]}

    result = await im.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"
    client.teleport.assert_not_called()
    client.send_key.assert_not_called()


async def test_multiple_clients_both_must_see_menu():
    """Both active clients must have menu_path visible before SUCCESS."""
    c1 = _ready_client()
    c2 = _ready_client()
    seen: list[object] = []

    async def _visible(client, path):
        seen.append(client)
        return len(seen) >= 2

    with (
        patch.object(im, "POLL_INTERVAL", 0),
        patch.object(node_helpers, "is_visible_by_path", side_effect=_visible),
    ):
        result = await im.run(FakeMachine(), [c1, c2], {**_PARAMS, "interact_clients": [1, 2]})

    assert result == "SUCCESS"
    assert len(seen) >= 2


# ---------------------------------------------------------------------------
# Sequential mode
# ---------------------------------------------------------------------------


def _positional_client() -> FakeClient:
    """Client whose interact prompt shows at interact_xyz (x=0) and not at reset_xyz (x=1)."""
    c = FakeClient()
    state = {"at_interact": False}

    async def _teleport(xyz):
        state["at_interact"] = xyz.x == 0.0

    async def _in_range():
        return state["at_interact"]

    c.teleport.side_effect = _teleport
    c.is_in_npc_range.side_effect = _in_range
    c.at_interact = state
    return c


async def test_sequential_each_client_opens_the_menu_alone():
    """sequential=True: no two clients are in range of the object at the same time."""
    c1, c2 = _positional_client(), _positional_client()
    clients = [c1, c2]
    params = {**_PARAMS, "interact_clients": [1, 2], "sequential": True}
    overlaps: list[bool] = []

    async def _press(key, delay):
        overlaps.append(sum(c.at_interact["at_interact"] for c in clients) > 1)

    c1.send_key.side_effect = _press
    c2.send_key.side_effect = _press

    with (
        patch.object(im, "POLL_INTERVAL", 0),
        patch.object(node_helpers, "OBJECT_USE_DELAY", 0),
        patch.object(node_helpers, "is_visible_by_path", AsyncMock(return_value=True)),
    ):
        result = await im.run(FakeMachine(), clients, params)

    assert result == "SUCCESS"
    assert overlaps == [False, False]


async def test_sequential_menu_never_appears_reports_that_client():
    """A client whose menu never opens is reported as unfinished, not as a missing prompt."""
    client = _positional_client()
    machine = FakeMachine()
    params = {**_PARAMS, "sequential": True}

    with (
        patch.object(im, "POLL_INTERVAL", 0),
        patch.object(node_helpers, "OBJECT_USE_DELAY", 0),
        patch.object(node_helpers, "is_visible_by_path", AsyncMock(return_value=False)),
    ):
        result = await im.run(machine, [client], params)

    assert result == "FAILURE"
    assert any("menu path not visible" in msg for msg, _ in machine.logs)
    assert not any("interact prompt never appeared" in msg for msg, _ in machine.logs)


async def test_sequential_prompt_never_appears_reports_that_client():
    """A client that never gets the prompt is reported as such, with its client number."""
    client = _positional_client()
    client.is_in_npc_range.side_effect = None
    client.is_in_npc_range.return_value = False
    machine = FakeMachine()
    params = {**_PARAMS, "sequential": True}

    with (
        patch.object(im, "POLL_INTERVAL", 0),
        patch.object(node_helpers, "OBJECT_USE_DELAY", 0),
        patch.object(node_helpers, "is_visible_by_path", AsyncMock(return_value=True)),
    ):
        result = await im.run(machine, [client], params)

    assert result == "FAILURE"
    assert any("interact prompt never appeared for client(s) [1]" in msg for msg, _ in machine.logs)
