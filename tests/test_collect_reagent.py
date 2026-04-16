"""Unit tests for the collect_reagent node."""

import json
from unittest.mock import patch

import bartlebot.nodes.action.collect_reagent as cr
from tests.conftest import FakeClient, FakeMachine

_SPAWN_A = {"x": 100.0, "y": 200.0, "z": 0.0}
_SPAWN_B = {"x": -300.0, "y": 500.0, "z": 50.0}

_PARAMS_ONE = {
    "zone": "TestZone",
    "spawns": json.dumps(_SPAWN_A),
}

_PARAMS_TWO = {
    "zone": "TestZone",
    "spawns": "\n".join([json.dumps(_SPAWN_A), json.dumps(_SPAWN_B)]),
}


async def test_success_all_collected():
    """Prompt appears immediately at the spawn — X is pressed and SUCCESS returned."""
    client = FakeClient()
    client.is_in_npc_range.return_value = True

    with patch.object(cr, "POLL_INTERVAL", 0), patch.object(cr, "REAGENT_COLLECT_DELAY", 0):
        result = await cr.run(FakeMachine(), [client], _PARAMS_ONE)

    assert result == "SUCCESS"
    client.teleport.assert_called_once()
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in client.send_key.call_args_list)


async def test_success_no_reagent_at_spawn():
    """No prompt ever appears — the spawn is skipped and SUCCESS returned (absence is expected)."""
    client = FakeClient()
    client.is_in_npc_range.return_value = False

    with patch.object(cr, "POLL_INTERVAL", 0):
        result = await cr.run(FakeMachine(), [client], _PARAMS_ONE)

    assert result == "SUCCESS"
    client.teleport.assert_called_once()
    client.send_key.assert_not_called()


async def test_success_partial_collection():
    """Two spawns: prompt appears at first only. X pressed once; teleport called twice."""
    client = FakeClient()
    # First spawn: prompt appears; second spawn: no prompt.
    call_count = 0

    async def npc_range():
        nonlocal call_count
        call_count += 1
        return call_count == 1

    client.is_in_npc_range.side_effect = npc_range

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch.object(cr, "REAGENT_COLLECT_DELAY", 0),
    ):
        result = await cr.run(FakeMachine(), [client], _PARAMS_TWO)

    assert result == "SUCCESS"
    assert client.teleport.call_count == 2
    from wizwalker import Keycode

    x_presses = [call for call in client.send_key.call_args_list if call.args[0] == Keycode.X]
    assert len(x_presses) == 1


async def test_success_empty_spawns():
    """Empty spawns string — no teleports, immediate SUCCESS."""
    client = FakeClient()
    params = {"zone": "TestZone", "spawns": ""}

    result = await cr.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"
    client.teleport.assert_not_called()
    client.send_key.assert_not_called()


async def test_success_blank_lines_ignored():
    """Blank lines between spawn entries are silently ignored."""
    client = FakeClient()
    client.is_in_npc_range.return_value = True
    spawns_text = "\n".join(["", json.dumps(_SPAWN_A), "", json.dumps(_SPAWN_B), ""])
    params = {"zone": "TestZone", "spawns": spawns_text}

    with patch.object(cr, "POLL_INTERVAL", 0), patch.object(cr, "REAGENT_COLLECT_DELAY", 0):
        result = await cr.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"
    assert client.teleport.call_count == 2


async def test_wrong_zone_fails():
    """FAILURE when any client is not in the expected zone."""
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"

    result = await cr.run(FakeMachine(), [client], _PARAMS_ONE)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_multiple_clients_different_servers():
    """Two clients on different servers each independently receive the prompt and collect."""
    c1 = FakeClient()
    c2 = FakeClient()
    c1.is_in_npc_range.return_value = True
    c2.is_in_npc_range.return_value = True

    with patch.object(cr, "POLL_INTERVAL", 0), patch.object(cr, "REAGENT_COLLECT_DELAY", 0):
        result = await cr.run(FakeMachine(), [c1, c2], _PARAMS_ONE)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    for c in (c1, c2):
        assert any(call.args[0] == Keycode.X for call in c.send_key.call_args_list)


async def test_multiple_clients_same_server():
    """Two clients on the same server: only client 1 gets the prompt (client 2 narrowly misses).
    X is pressed on client 1 only; client 2 is unaffected."""
    c1 = FakeClient()
    c2 = FakeClient()
    c1.is_in_npc_range.return_value = True
    c2.is_in_npc_range.return_value = False  # Same server — reagent already taken by c1

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch.object(cr, "REAGENT_COLLECT_DELAY", 0),
    ):
        result = await cr.run(FakeMachine(), [c1, c2], _PARAMS_ONE)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in c1.send_key.call_args_list)
    c2.send_key.assert_not_called()


async def test_prompt_appears_after_poll():
    """Prompt is not immediate but appears within poll_until — X is still pressed."""
    client = FakeClient()
    call_count = 0

    async def npc_range():
        nonlocal call_count
        call_count += 1
        return call_count >= 3  # Not visible on first two checks; appears on third.

    client.is_in_npc_range.side_effect = npc_range

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch.object(cr, "REAGENT_COLLECT_DELAY", 0),
    ):
        result = await cr.run(FakeMachine(), [client], _PARAMS_ONE)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in client.send_key.call_args_list)


async def test_malformed_spawn_line_fails():
    """A line that cannot be parsed as a JSON XYZ dict causes FAILURE."""
    client = FakeClient()
    params = {"zone": "TestZone", "spawns": "not json at all"}

    result = await cr.run(FakeMachine(), [client], params)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_partial_valid_then_invalid_fails():
    """First line is valid, second line is bad — FAILURE before any teleport to second spawn."""
    client = FakeClient()
    params = {
        "zone": "TestZone",
        "spawns": "\n".join([json.dumps(_SPAWN_A), "oops"]),
    }

    result = await cr.run(FakeMachine(), [client], params)

    assert result == "FAILURE"
    # Parsing happens before any teleport, so no teleports should occur.
    client.teleport.assert_not_called()
