"""Unit tests for the collect_reagent node (entity-name API)."""

from unittest.mock import AsyncMock, patch

from wizwalker import XYZ

import bartlebot.nodes.interaction.collect_reagent as cr
from tests.conftest import FakeClient, FakeEntity, FakeMachine

_ENTITY_NAME = "FungusWort"
_PARAMS = {"zone": "TestZone", "entity_name": _ENTITY_NAME}

_XYZ_A = XYZ(100.0, 200.0, 0.0)
_XYZ_B = XYZ(-300.0, 500.0, 50.0)

# Patch targets for drop-detection I/O
_SNAP = "bartlebot.utils.drop_detection.snapshot_chat"
_EXTRACT = "bartlebot.utils.drop_detection.extract_new_drops"


def _entity(xyz: XYZ = _XYZ_A) -> FakeEntity:
    e = FakeEntity("FungusWort/FungusWort")
    e.location = AsyncMock(return_value=xyz)
    return e


async def test_wrong_zone_fails():
    """FAILURE when any client is not in the expected zone."""
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"

    result = await cr.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_blank_entity_name_fails():
    """FAILURE when entity_name is empty."""
    client = FakeClient()

    result = await cr.run(FakeMachine(), [client], {"zone": "TestZone", "entity_name": ""})

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_no_entities_found_fails():
    """FAILURE when no matching entities are found on any client."""
    client = FakeClient()
    client.get_base_entities_with_vague_name.return_value = []

    result = await cr.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_success_immediate_prompt():
    """Entity found, prompt appears immediately — X is pressed and SUCCESS returned."""
    client = FakeClient()
    client.get_base_entities_with_vague_name.return_value = [_entity()]
    client.is_in_npc_range.side_effect = [True, False]  # True: in range (prompted); False: gone (dismissed)

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch(_SNAP, new=AsyncMock(return_value="")),
        patch(_EXTRACT, return_value=[]),
    ):
        result = await cr.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_called_once()
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in client.send_key.call_args_list)


async def test_success_prompt_appears_after_polling():
    """Prompt not visible on first check, appears within the poll window — X is still pressed."""
    client = FakeClient()
    client.get_base_entities_with_vague_name.return_value = [_entity()]
    call_count = 0

    async def npc_range():
        nonlocal call_count
        call_count += 1
        return call_count == 3  # False for calls 1-2 (polling); True for call 3 (prompted); False after (dismissed)

    client.is_in_npc_range.side_effect = npc_range

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch(_SNAP, new=AsyncMock(return_value="")),
        patch(_EXTRACT, return_value=[]),
    ):
        result = await cr.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in client.send_key.call_args_list)


async def test_success_multiple_entities():
    """Three entities found, each has a prompt — X pressed three times."""
    client = FakeClient()
    client.get_base_entities_with_vague_name.return_value = [
        _entity(_XYZ_A),
        _entity(_XYZ_B),
        _entity(XYZ(0.0, 0.0, 10.0)),
    ]
    client.is_in_npc_range.side_effect = [True, False, True, False, True, False]  # in/out pair per entity

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch(_SNAP, new=AsyncMock(return_value="")),
        patch(_EXTRACT, return_value=[]),
    ):
        result = await cr.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert client.teleport.call_count == 3
    from wizwalker import Keycode

    x_presses = [c for c in client.send_key.call_args_list if c.args[0] == Keycode.X]
    assert len(x_presses) == 3


async def test_entity_found_no_prompt_fails():
    """Entity found but no prompt appears within the poll window — total = 0 → FAILURE."""
    client = FakeClient()
    client.get_base_entities_with_vague_name.return_value = [_entity()]
    client.is_in_npc_range.return_value = False  # prompt never appears

    with patch.object(cr, "POLL_INTERVAL", 0):
        result = await cr.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_called_once()
    client.send_key.assert_not_called()


async def test_partial_collection_counts_as_success():
    """Two entities: first has a prompt, second doesn't — one collected → SUCCESS."""
    client = FakeClient()
    client.get_base_entities_with_vague_name.return_value = [_entity(_XYZ_A), _entity(_XYZ_B)]
    call_count = 0

    async def npc_range():
        nonlocal call_count
        call_count += 1
        return call_count == 1  # only first entity gets the prompt

    client.is_in_npc_range.side_effect = npc_range

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch(_SNAP, new=AsyncMock(return_value="")),
        patch(_EXTRACT, return_value=[]),
    ):
        result = await cr.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert client.teleport.call_count == 2
    from wizwalker import Keycode

    x_presses = [c for c in client.send_key.call_args_list if c.args[0] == Keycode.X]
    assert len(x_presses) == 1


async def test_multiple_clients_independent():
    """Two clients on separate servers each find their own entity and collect it."""
    c1 = FakeClient()
    c2 = FakeClient()
    c1.get_base_entities_with_vague_name.return_value = [_entity(_XYZ_A)]
    c2.get_base_entities_with_vague_name.return_value = [_entity(_XYZ_B)]
    c1.is_in_npc_range.side_effect = [True, False]  # in range (prompted), then gone (dismissed)
    c2.is_in_npc_range.side_effect = [True, False]

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch(_SNAP, new=AsyncMock(return_value="")),
        patch(_EXTRACT, return_value=[]),
    ):
        result = await cr.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    for c in (c1, c2):
        assert any(call.args[0] == Keycode.X for call in c.send_key.call_args_list)


async def test_multiple_clients_one_misses():
    """Same server: c2's entity is already gone — c1 still collected → SUCCESS."""
    c1 = FakeClient()
    c2 = FakeClient()
    c1.get_base_entities_with_vague_name.return_value = [_entity()]
    c2.get_base_entities_with_vague_name.return_value = []
    c1.is_in_npc_range.side_effect = [True, False]  # in range (prompted), then gone (dismissed)

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch(_SNAP, new=AsyncMock(return_value="")),
        patch(_EXTRACT, return_value=[]),
    ):
        result = await cr.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in c1.send_key.call_args_list)
    c2.send_key.assert_not_called()


async def test_drop_tracking():
    """Items appearing in chat after pressing X are reported via machine.on_drop()."""
    client = FakeClient()
    client.get_base_entities_with_vague_name.return_value = [_entity()]
    client.is_in_npc_range.side_effect = [True, False]  # in range (prompted), then gone (dismissed)
    machine = FakeMachine()

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch(_SNAP, new=AsyncMock(return_value="")),
        patch(_EXTRACT, return_value=["Mandrake"]),
    ):
        await cr.run(machine, [client], _PARAMS)

    assert ("Mandrake", 1, 0) in machine.drops
