"""Unit tests for the handle_combat node."""

from unittest.mock import AsyncMock, call, patch

import pytest

import bartlebot.nodes.combat.handle_combat as hc
from bartlebot.nodes import combat_runner
from tests.conftest import FakeClient, FakeEntity, FakeMachine


@pytest.fixture(autouse=True)
def _no_drop_wait(monkeypatch):
    """Collapse the post-fight wait for a late drop announcement.

    Every test in this file either has its drop line present on the very first read or
    has none at all, so there is nothing here to wait for; a zero ceiling makes
    poll_new_drops take exactly one look. Without this the file pays
    COMBAT_DROP_MESSAGE_TIMEOUT (15 s) per test. The late-arrival behaviour that ceiling
    exists for is what tests/test_combat_drop_recording.py covers, with a real window.
    """
    monkeypatch.setattr(combat_runner, "COMBAT_DROP_MESSAGE_TIMEOUT", 0)


# A chat line that drop_detection.extract_new_drops recognises as a Reagent drop.
_DROP_LINE = "Art_Chat_System.dds> <>IronBloom<;Reagent>"

# Params that exercise the default bot-level combat DSL path.
_DEFAULT_PARAMS = {"enemy": "", "use_default_strategy": True}


# Convenience: mock resolve_combat to return a specific combat_order.
def _patch_resolve_combat(combat_order: list[int]):
    from unittest.mock import patch  # noqa: PLC0415

    from bartlebot.defaults.combat_config import BARTLEBOT_DEFAULT_COMBAT_CONFIG  # noqa: PLC0415

    return patch(
        "bartlebot.nodes.combat.handle_combat.resolve_combat",
        return_value=([BARTLEBOT_DEFAULT_COMBAT_CONFIG, BARTLEBOT_DEFAULT_COMBAT_CONFIG], combat_order, "test"),
    )


# Convenience context manager: no-op _run_combat so SprintyCombat is never called.
_patch_run_combat = lambda: patch(  # noqa: E731
    "bartlebot.nodes.combat.handle_combat._run_combat", new_callable=AsyncMock
)


async def test_no_dsl_fails():
    """use_default_strategy=False with no BCS assigned → falls back to built-in default DSL."""
    client = FakeClient()
    client.find_closest_mob.return_value = FakeEntity()
    client.in_battle.return_value = True
    with (
        _patch_run_combat(),
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=AsyncMock(return_value="")),
        patch.object(hc, "POLL_INTERVAL", 0),
    ):
        result = await hc.run(
            FakeMachine(),
            [client],
            {"enemy": "", "use_default_strategy": False},
        )
    assert result == "SUCCESS"


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
            {"enemy": "Cyclops", "use_default_strategy": True},
        )

    assert result == "SUCCESS"
    # _find_target is called in step 1 (target check) and again inside
    # _tp_and_wait_for_combat; both calls must use the vague name.
    assert client.find_closest_by_vague_name.call_count >= 1
    assert all(c == call("Cyclops") for c in client.find_closest_by_vague_name.call_args_list)
    client.find_closest_mob.assert_not_called()


async def test_wildcard_enemy_name_searches_by_predicate():
    """A '*' in the enemy field used to be matched literally, so it found nothing."""
    client = FakeClient()
    client.find_closest_by_predicate.return_value = FakeEntity("Bandit/BanditThug")
    client.in_battle.return_value = True

    with (
        _patch_run_combat(),
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=AsyncMock(return_value="")),
        patch.object(hc, "POLL_INTERVAL", 0),
    ):
        result = await hc.run(
            FakeMachine(),
            [client],
            {"enemy": "*Bandit*", "use_default_strategy": True},
        )

    assert result == "SUCCESS"
    assert client.find_closest_by_predicate.call_count >= 1
    # The literal pattern must never reach WizSprinter's substring matcher, which is
    # exactly what made wildcards fail before.
    client.find_closest_by_vague_name.assert_not_called()
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

    with (
        _patch_run_combat(),
        _patch_resolve_combat([2, 1]),
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=AsyncMock(return_value="")),
        patch.object(hc, "POLL_INTERVAL", 0),
    ):
        result = await hc.run(FakeMachine(), [c1, c2], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert tp_order == [2, 1]


async def test_combat_order_invalid_client_number_skipped():
    """combat_order referencing a client that doesn't exist is silently skipped."""
    from bartlebot.defaults.combat_config import BARTLEBOT_DEFAULT_COMBAT_CONFIG

    client = FakeClient()
    client.find_closest_mob.return_value = FakeEntity()

    with (
        _patch_run_combat(),
        patch(
            "bartlebot.nodes.combat.handle_combat.resolve_combat",
            return_value=([BARTLEBOT_DEFAULT_COMBAT_CONFIG], [5], "test"),
        ),
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=AsyncMock(return_value="")),
        patch.object(hc, "POLL_INTERVAL", 0),
    ):
        result = await hc.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    # Client 5 doesn't exist (only 1 client), so the combat_order entry is skipped.
    assert result == "SUCCESS"
    client.tp_to.assert_not_called()


async def test_success_with_drops():
    """Drop appears in chat after combat → on_drop called with the item name."""
    client = FakeClient()
    client.find_closest_mob.return_value = FakeEntity()
    client.in_battle.return_value = True

    # First read seeds the baseline before combat and is empty; the second, after
    # combat, finds a drop line.
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
