"""Unit tests for the handle_combat node."""

from unittest.mock import AsyncMock, call, patch

import bartlebot.nodes.combat.handle_combat as hc
from tests.conftest import FakeClient, FakeEntity, FakeMachine

# A chat line that drop_detection.extract_new_drops recognises as a Reagent drop.
_DROP_LINE = "Art_Chat_System.dds> <>IronBloom<;Reagent>"

# Params that exercise the default bot-level combat DSL path.
_DEFAULT_PARAMS = {"enemy": "", "use_bot_default": True, "combat_config": ""}

# Convenience context manager: no-op _run_combat so SprintyCombat is never called.
_patch_run_combat = lambda: patch(  # noqa: E731
    "bartlebot.nodes.combat.handle_combat._run_combat", new_callable=AsyncMock
)


async def test_no_dsl_fails():
    """No combat DSL available (use_bot_default=False, combat_config="") → FAILURE."""
    client = FakeClient()
    result = await hc.run(
        FakeMachine(),
        [client],
        {"enemy": "", "use_bot_default": False, "combat_config": ""},
    )
    assert result == "FAILURE"
    client.tp_to.assert_not_called()


async def test_no_target_found_fails():
    """find_closest_mob returns None (no enemies nearby) → FAILURE."""
    client = FakeClient()
    client.find_closest_mob.return_value = None

    with _patch_run_combat(), patch.object(hc, "TARGET_ACQUIRE_DELAY", 0):
        result = await hc.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "FAILURE"
    client.tp_to.assert_not_called()


async def test_named_enemy_uses_vague_name():
    """enemy param is set → find_closest_by_vague_name called, not find_closest_mob."""
    client = FakeClient()
    client.find_closest_by_vague_name.return_value = FakeEntity("Cyclops/CyclopsMinion")
    client.in_battle.return_value = True

    with (
        _patch_run_combat(),
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=AsyncMock(return_value="")),
        patch.object(hc, "POLL_INTERVAL", 0),
    ):
        result = await hc.run(
            FakeMachine(),
            [client],
            {"enemy": "Cyclops", "use_bot_default": True, "combat_config": ""},
        )

    assert result == "SUCCESS"
    # _find_target is called in step 1 (target check) and again inside
    # _tp_and_wait_for_combat; both calls must use the vague name.
    assert client.find_closest_by_vague_name.call_count >= 1
    assert all(c == call("Cyclops") for c in client.find_closest_by_vague_name.call_args_list)
    client.find_closest_mob.assert_not_called()


async def test_timeout_entering_combat_fails():
    """Clients never enter battle within the timeout window → FAILURE."""
    client = FakeClient()
    client.find_closest_mob.return_value = FakeEntity()
    client.in_battle.return_value = False  # never enters combat

    with (
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=AsyncMock(return_value="")),
        patch.object(hc, "ENTER_COMBAT_TIMEOUT", 0.01),
        patch.object(hc, "POLL_INTERVAL", 0),
    ):
        result = await hc.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "FAILURE"


async def test_success_no_drops():
    """Happy path: combat completes but no items appear in chat → SUCCESS, no drops."""
    client = FakeClient()
    client.find_closest_mob.return_value = FakeEntity()
    client.in_battle.return_value = True

    with (
        _patch_run_combat(),
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=AsyncMock(return_value="")),
        patch.object(hc, "POLL_INTERVAL", 0),
    ):
        machine = FakeMachine()
        result = await hc.run(machine, [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert machine.drops == []


async def test_combat_order_sequential():
    """combat_order sends clients one at a time in the specified order."""
    c1 = FakeClient()
    c2 = FakeClient()
    c1.find_closest_mob.return_value = FakeEntity()
    c2.find_closest_mob.return_value = FakeEntity()
    c1.in_battle.return_value = True
    c2.in_battle.return_value = True

    tp_order: list[int] = []

    async def _record_tp_c1(target):
        tp_order.append(1)

    async def _record_tp_c2(target):
        tp_order.append(2)

    c1.tp_to.side_effect = _record_tp_c1
    c2.tp_to.side_effect = _record_tp_c2

    machine = FakeMachine()
    machine.get_option = lambda name, default=None: [2, 1] if name == "Default Combat Order" else default

    with (
        _patch_run_combat(),
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=AsyncMock(return_value="")),
        patch.object(hc, "POLL_INTERVAL", 0),
    ):
        result = await hc.run(machine, [c1, c2], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert tp_order == [2, 1]


async def test_combat_order_invalid_client_number_skipped():
    """combat_order referencing a client that doesn't exist is silently skipped."""
    client = FakeClient()
    client.find_closest_mob.return_value = FakeEntity()

    machine = FakeMachine()
    machine.get_option = lambda name, default=None: [5] if name == "Default Combat Order" else default

    with (
        _patch_run_combat(),
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=AsyncMock(return_value="")),
        patch.object(hc, "POLL_INTERVAL", 0),
    ):
        result = await hc.run(machine, [client], _DEFAULT_PARAMS)

    # Client 5 doesn't exist (only 1 client), so the combat_order entry is skipped.
    assert result == "SUCCESS"
    client.tp_to.assert_not_called()


async def test_success_with_drops():
    """Drop appears in chat after combat → on_drop called with the item name."""
    client = FakeClient()
    client.find_closest_mob.return_value = FakeEntity()
    client.in_battle.return_value = True

    # First snapshot (before combat) is empty; second (after) contains a drop line.
    mock_snap = AsyncMock(side_effect=["", _DROP_LINE])

    with (
        _patch_run_combat(),
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=mock_snap),
        patch.object(hc, "POLL_INTERVAL", 0),
    ):
        machine = FakeMachine()
        result = await hc.run(machine, [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert len(machine.drops) == 1
    item, qty, idx = machine.drops[0]
    assert item == "IronBloom"
    assert qty == 1
    assert idx == 0
