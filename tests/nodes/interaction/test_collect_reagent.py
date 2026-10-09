"""Unit tests for the collect_reagent node (entity-name API)."""

from unittest.mock import AsyncMock, MagicMock, patch

from wizwalker import XYZ

import bartlebot.nodes.interaction.collect_reagent as cr
from tests.conftest import FakeClient, FakeEntity, FakeMachine

_ENTITY_NAME = "FungusWort"
_PARAMS = {"zone": "TestZone", "entity_names": [_ENTITY_NAME]}

_XYZ_A = XYZ(100.0, 200.0, 0.0)
_XYZ_B = XYZ(-300.0, 500.0, 50.0)

# Patch targets for drop-detection I/O
_SNAP = "bartlebot.game.drop_detection.snapshot_chat"
_EXTRACT = "bartlebot.game.drop_detection.extract_new_drops"
_AVAIL = "bartlebot.game.drop_detection.chat_available"


def _entity(xyz: XYZ = _XYZ_A, name: str = _ENTITY_NAME) -> FakeEntity:
    e = FakeEntity(name)
    e.location = AsyncMock(return_value=xyz)
    return e


def _spawn(client: FakeClient, entities: list[FakeEntity]) -> None:
    """Load *entities* into the client's zone; each search keeps those its predicate accepts.

    The node's own predicate runs, rather than a canned answer per search, so a test sees
    exactly which entities a name picks out of the zone.
    """

    async def _search(predicate):
        return [e for e in entities if await predicate(e)]

    client.get_base_entities_with_predicate.side_effect = _search


async def test_wrong_zone_fails():
    """FAILURE when any client is not in the expected zone."""
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"

    result = await cr.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_blank_entity_names_fails():
    """FAILURE when entity_names is empty."""
    client = FakeClient()

    result = await cr.run(FakeMachine(), [client], {"zone": "TestZone", "entity_names": []})

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_no_entities_found_succeeds():
    """An empty spawn is a normal outcome, not an error — SUCCESS with nothing collected.

    Spawns are random, so a failure here would be indistinguishable from a real
    problem and would send routes to their recovery branch on an ordinary pass.
    """
    client = FakeClient()
    _spawn(client, [])

    result = await cr.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_not_called()


async def test_plain_name_skips_entities_whose_id_only_contains_it():
    """#144: "Stone" teleported a player to ZF-STONE-NPC10, and would take Sandstone too."""
    client = FakeClient()
    _spawn(
        client,
        [
            _entity(_XYZ_A, name="ZF-STONE-NPC10"),
            _entity(_XYZ_B, name="Stone"),
            _entity(XYZ(0.0, 0.0, 10.0), name="Sandstone"),
        ],
    )
    client.is_in_npc_range.side_effect = [True, False]

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch(_SNAP, new=AsyncMock(return_value="")),
        patch(_EXTRACT, return_value=[]),
    ):
        result = await cr.run(FakeMachine(), [client], {"zone": "TestZone", "entity_names": ["Stone"]})

    assert result == "SUCCESS"
    client.teleport.assert_called_once_with(_XYZ_B)
    # WizSprinter's vague-name search is the substring match this replaced.
    client.get_base_entities_with_vague_name.assert_not_called()


async def test_plain_name_ignores_case():
    client = FakeClient()
    _spawn(client, [_entity(name="Black Lotus")])
    client.is_in_npc_range.side_effect = [True, False]

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch(_SNAP, new=AsyncMock(return_value="")),
        patch(_EXTRACT, return_value=[]),
    ):
        result = await cr.run(FakeMachine(), [client], {"zone": "TestZone", "entity_names": ["black lotus"]})

    assert result == "SUCCESS"
    client.teleport.assert_called_once()


async def test_wildcard_entity_name_matches_part_of_an_id():
    """Wildcards in reagent names glob, the same as the enemy name field, and are the way to ask
    for a partial match."""
    client = FakeClient()
    _spawn(client, [_entity(_XYZ_A, name="Sandstone"), _entity(_XYZ_B, name="Ore")])
    client.is_in_npc_range.side_effect = [True, False]

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch(_SNAP, new=AsyncMock(return_value="")),
        patch(_EXTRACT, return_value=[]),
    ):
        result = await cr.run(FakeMachine(), [client], {"zone": "TestZone", "entity_names": ["*Stone*"]})

    assert result == "SUCCESS"
    client.teleport.assert_called_once_with(_XYZ_A)
    client.get_base_entities_with_vague_name.assert_not_called()


async def test_success_immediate_prompt():
    """Entity found, prompt appears immediately — X is pressed and SUCCESS returned."""
    client = FakeClient()
    _spawn(client, [_entity()])
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
    _spawn(client, [_entity()])
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
    _spawn(client, [_entity(_XYZ_A), _entity(_XYZ_B), _entity(XYZ(0.0, 0.0, 10.0))])
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


async def test_multiple_entity_names_combined():
    """Two entity names provided — entities from both are collected in one pass."""
    client = FakeClient()
    _spawn(client, [_entity(_XYZ_A, name="FungusWort"), _entity(_XYZ_B, name="Mushroom")])
    client.is_in_npc_range.side_effect = [True, False, True, False]  # in/out per entity

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch(_SNAP, new=AsyncMock(return_value="")),
        patch(_EXTRACT, return_value=[]),
    ):
        result = await cr.run(FakeMachine(), [client], {"zone": "TestZone", "entity_names": ["FungusWort", "Mushroom"]})

    assert result == "SUCCESS"
    assert client.teleport.call_count == 2
    from wizwalker import Keycode

    x_presses = [c for c in client.send_key.call_args_list if c.args[0] == Keycode.X]
    assert len(x_presses) == 2


async def test_entity_found_no_prompt_still_succeeds():
    """Entity found but no prompt appears — the reagent was taken by someone else mid-teleport.

    Also an ordinary outcome in a shared realm, so it does not fail the node.
    """
    client = FakeClient()
    _spawn(client, [_entity()])
    client.is_in_npc_range.return_value = False  # prompt never appears

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch(_AVAIL, new=AsyncMock(return_value=False)),
    ):
        result = await cr.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_called_once()
    client.send_key.assert_not_called()


async def test_partial_collection_counts_as_success():
    """Two entities: first has a prompt, second doesn't — one collected → SUCCESS."""
    client = FakeClient()
    _spawn(client, [_entity(_XYZ_A), _entity(_XYZ_B)])
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
    _spawn(c1, [_entity(_XYZ_A)])
    _spawn(c2, [_entity(_XYZ_B)])
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
    _spawn(c1, [_entity()])
    _spawn(c2, [])
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
    _spawn(client, [_entity()])
    client.is_in_npc_range.side_effect = [True, False]  # in range (prompted), then gone (dismissed)
    machine = FakeMachine()
    calls = 0

    def extract(before, after):
        nonlocal calls
        calls += 1
        return ["Mandrake"] if calls == 2 else []  # absent on the first read, present on the second

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch.object(cr, "REAGENT_DROP_MESSAGE_TIMEOUT", 0.3),
        patch(_AVAIL, new=AsyncMock(return_value=True)),
        patch(_SNAP, new=AsyncMock(return_value="")),
        patch(_EXTRACT, new=extract),
    ):
        await cr.run(machine, [client], _PARAMS)

    assert machine.drops.count(("Mandrake", 1, 0)) == 1


async def test_drop_arriving_after_its_own_poll_window_is_still_logged():
    """A message that misses one pickup's poll window is caught on the next pickup.

    The chat baseline is carried across the pass, so a late announcement is not
    absorbed into a fresh baseline and lost.
    """
    client = FakeClient()
    _spawn(client, [_entity(_XYZ_A), _entity(_XYZ_B)])
    client.is_in_npc_range.side_effect = [True, False, True, False]
    machine = FakeMachine()

    # First pickup's poll times out empty; the message shows up during the second.
    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch.object(cr, "REAGENT_DROP_MESSAGE_TIMEOUT", 0),
        patch(_AVAIL, new=AsyncMock(return_value=True)),
        patch(_SNAP, new=AsyncMock(return_value="")),
        patch(_EXTRACT, side_effect=[[], ["Mandrake"], []]),
    ):
        await cr.run(machine, [client], _PARAMS)

    assert machine.drops.count(("Mandrake", 1, 0)) == 1


async def test_hidden_chat_skips_drop_detection():
    """With the chat log hidden there is nothing to poll, so no time is spent trying."""
    client = FakeClient()
    _spawn(client, [_entity()])
    client.is_in_npc_range.side_effect = [True, False]
    machine = FakeMachine()
    extract = MagicMock()

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch(_AVAIL, new=AsyncMock(return_value=False)),
        patch(_EXTRACT, new=extract),
    ):
        result = await cr.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    assert machine.drops == []
    extract.assert_not_called()
