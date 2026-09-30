"""Unit tests for the handle_combat node."""

import asyncio
from unittest.mock import AsyncMock, call, patch

import pytest
from wizwalker import XYZ

import bartlebot.nodes.combat.handle_combat as hc
from bartlebot.nodes import combat_runner
from tests.conftest import FakeClient, FakeEntity, FakeMachine, see_each_other


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


async def test_named_enemy_is_found_as_a_fight_target():
    """enemy param is set → the fight-target search runs (exact name first), not find_closest_mob."""
    client = FakeClient()
    client.in_battle.return_value = True

    with (
        _patch_run_combat(),
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=AsyncMock(return_value="")),
        patch.object(hc, "POLL_INTERVAL", 0),
        patch.object(
            hc.node_helpers, "find_closest_fight_target", new=AsyncMock(return_value=FakeEntity("Cyclops"))
        ) as find,
    ):
        result = await hc.run(
            FakeMachine(),
            [client],
            {"enemy": "Cyclops", "use_default_strategy": True},
        )

    assert result == "SUCCESS"
    # _find_target is called in step 1 (target check) and again inside _tp_and_wait_for_combat,
    # until some client is in battle: from then on every teleport goes to the fight centre.
    assert find.await_count >= 1
    assert all(c == call(client, "Cyclops") for c in find.await_args_list)
    client.find_closest_mob.assert_not_called()
    client.find_closest_by_vague_name.assert_not_called()


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


async def test_timeout_during_sprinty_combat_fails():
    """Players enter combat but SprintyCombat itself outlasts max_duration → FAILURE."""
    client = FakeClient()
    client.find_closest_mob.return_value = FakeEntity()
    client.in_battle.return_value = True

    async def _hang_combat(_client, _dsl):
        await asyncio.sleep(10)

    with (
        patch("bartlebot.nodes.combat.handle_combat._run_combat", new=AsyncMock(side_effect=_hang_combat)),
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=AsyncMock(return_value="")),
        patch.object(hc, "POLL_INTERVAL", 0),
    ):
        result = await hc.run(
            FakeMachine(),
            [client],
            {"enemy": "", "use_default_strategy": True, "max_duration": 0.05},
        )

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
    c1.teleport.side_effect = _record_tp_c1
    c2.teleport.side_effect = _record_tp_c2

    with (
        _patch_run_combat(),
        _patch_resolve_combat([2, 1]),
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=AsyncMock(return_value="")),
        patch.object(hc, "POLL_INTERVAL", 0),
    ):
        result = await hc.run(FakeMachine(), [c1, c2], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert tp_order == [2, 1]
    # The first in order goes to the mob; the next joins at the centre it published.
    c2.tp_to.assert_awaited_once()
    c1.tp_to.assert_not_called()
    c1.teleport.assert_awaited_once()
    assert _xyz(c1.teleport.await_args.args[0]) == _xyz(_CENTRE)


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
        await machine.wait_for_drop_poll()

    assert result == "SUCCESS"
    assert len(machine.drops) == 1
    item, qty, idx = machine.drops[0]
    assert item == "IronBloom"
    assert qty == 1
    assert idx == 0


async def test_success_does_not_wait_for_the_drop_poll():
    """A fight that drops nothing used to hold the node for the whole drop window.

    The poll is left running on the machine instead, and still reports what it finds.
    """
    client = FakeClient()
    client.find_closest_mob.return_value = FakeEntity()
    client.in_battle.return_value = True
    announce = asyncio.Event()

    async def _poll(machine, clients, slots):
        await announce.wait()
        machine.on_drop("IronBloom", 1, 0)
        return ["IronBloom"]

    with (
        _patch_run_combat(),
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=AsyncMock(return_value="")),
        patch.object(combat_runner, "poll_combat_drops", new=_poll),
        patch.object(hc, "POLL_INTERVAL", 0),
    ):
        machine = FakeMachine()
        result = await asyncio.wait_for(hc.run(machine, [client], _DEFAULT_PARAMS), timeout=2.0)
        assert result == "SUCCESS"
        assert machine.drops == []

        announce.set()
        await machine.wait_for_drop_poll()

    assert machine.drops == [("IronBloom", 1, 0)]


# ---------------------------------------------------------------------------
# Waiting to enter combat
# ---------------------------------------------------------------------------


_CENTRE = XYZ(500.0, 600.0, 0.0)  # FakeClient's duel.position()


def _xyz(point: XYZ) -> tuple[float, float, float]:
    """XYZ has no __eq__, so compare coordinates."""
    return (point.x, point.y, point.z)


def _log_lines(machine: FakeMachine, fragment: str) -> list[str]:
    return [msg for msg, _level in machine.logs if fragment in msg]


async def test_a_target_that_disappears_is_waited_out_until_the_fight_starts():
    """An encounter can hide its mob once the fight begins; the player must not give up then."""
    client = FakeClient()
    entity = FakeEntity()
    state = {"teleported": False, "lost": False}

    async def find():
        if state["teleported"]:
            state["lost"] = True
            return None
        return entity

    async def tp(_target):
        state["teleported"] = True

    client.find_closest_mob.side_effect = find
    client.tp_to.side_effect = tp
    # The fight begins only after the target has gone, which the old early exit never saw.
    client.in_battle.side_effect = lambda: state["lost"]

    with (
        _patch_run_combat(),
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=AsyncMock(return_value="")),
        patch.object(hc, "POLL_INTERVAL", 0),
        patch.object(hc, "COMBAT_TP_RETRY_INTERVAL", 0.001),
    ):
        machine = FakeMachine()
        result = await hc.run(machine, [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert len(_log_lines(machine, "Client 0: target not found")) == 1


async def test_a_target_that_never_returns_fails_at_the_timeout_and_says_so_once():
    client = FakeClient()
    entity = FakeEntity()
    finds = {"n": 0}

    async def find():
        finds["n"] += 1
        return entity if finds["n"] <= 2 else None  # acquisition, then the first teleport

    client.find_closest_mob.side_effect = find
    client.in_battle.return_value = False

    with (
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=AsyncMock(return_value="")),
        patch.object(hc, "ENTER_COMBAT_TIMEOUT", 0.2),
        patch.object(hc, "POLL_INTERVAL", 0),
        patch.object(hc, "COMBAT_TP_RETRY_INTERVAL", 0.01),
    ):
        machine = FakeMachine()
        result = await hc.run(machine, [client], _DEFAULT_PARAMS)

    assert result == "FAILURE"
    assert finds["n"] > 3  # kept looking after the target went, rather than returning at once
    assert len(_log_lines(machine, "Client 0: target not found")) == 1
    client.tp_to.assert_awaited_once()


async def test_a_re_teleport_logs_how_far_it_moved_the_player():
    """Zero means the player was already on the mob, so the retry could not start the fight."""
    client = FakeClient()
    entity = FakeEntity()
    client.find_closest_mob.return_value = entity
    position = {"at": XYZ(0.0, 0.0, 0.0)}
    client.body.position = AsyncMock(side_effect=lambda: position["at"])

    async def tp(target):
        position["at"] = await target.location()

    client.tp_to.side_effect = tp
    client.in_battle.side_effect = lambda: client.tp_to.await_count >= 3

    with (
        _patch_run_combat(),
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=AsyncMock(return_value="")),
        patch.object(hc, "POLL_INTERVAL", 0),
        patch.object(hc, "COMBAT_TP_RETRY_INTERVAL", 0.001),
    ):
        machine = FakeMachine()
        result = await hc.run(machine, [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert _log_lines(machine, "re-teleport") == [
        "Client 0: not in battle; re-teleport 2 moved 0.0 units",
        "Client 0: not in battle; re-teleport 3 moved 0.0 units",
    ]


# ---------------------------------------------------------------------------
# Joining at the fight centre (#110)
# ---------------------------------------------------------------------------


def _leader_and_follower():
    """A leader, and a follower pulled in only by a teleport to the centre.

    The leader gets in once both have made their first mob teleport, so the follower is
    already aiming at the mob when the centre is published. The follower's mob teleport
    never joins it, as when the mob has walked to its seat.
    """
    leader, follower = FakeClient(), FakeClient()
    for client in (leader, follower):
        client.find_closest_mob.return_value = FakeEntity()
    leader.in_battle.side_effect = lambda: leader.tp_to.await_count > 0 and follower.tp_to.await_count > 0
    follower.in_battle.side_effect = lambda: follower.teleport.await_count > 0
    return leader, follower


def _patches_for_fast_retry():
    return (
        _patch_run_combat(),
        patch("bartlebot.utils.drop_detection.snapshot_chat", new=AsyncMock(return_value="")),
        patch.object(hc, "POLL_INTERVAL", 0),
        patch.object(hc, "COMBAT_TP_RETRY_INTERVAL", 0.01),
    )


async def test_a_follower_teleports_to_the_leaders_fight_centre_not_the_mob():
    leader, follower = _leader_and_follower()
    p1, p2, p3, p4 = _patches_for_fast_retry()
    with p1, p2, p3, p4:
        machine = FakeMachine()
        result = await hc.run(machine, [leader, follower], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    follower.tp_to.assert_awaited_once()  # the first attempt, before any centre existed
    follower.teleport.assert_awaited_once()
    assert _xyz(follower.teleport.await_args.args[0]) == _xyz(_CENTRE)
    assert len(_log_lines(machine, "Client 1: teleporting to the fight centre")) == 1


async def test_a_separate_bot_shares_the_centre_only_within_a_realm():
    """On another realm the leader's centre is a fight the follower is not in (#132)."""
    leader, follower = _leader_and_follower()
    # The follower gets in on its own mob, at its second attempt, if it keeps aiming there.
    follower.in_battle.side_effect = lambda: follower.tp_to.await_count > 1
    see_each_other(leader)
    see_each_other(follower)
    p1, p2, p3, p4 = _patches_for_fast_retry()
    with p1, p2, p3, p4:
        machine = FakeMachine(options={"Player Realms": "Separate"})
        result = await hc.run(machine, [leader, follower], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    follower.teleport.assert_not_called()
    assert follower.tp_to.await_count > 1
    follower.duel.position.assert_awaited_once()  # it published a centre of its own


async def test_a_separate_bot_still_shares_the_centre_on_one_realm():
    leader, follower = _leader_and_follower()
    p1, p2, p3, p4 = _patches_for_fast_retry()
    with p1, p2, p3, p4:
        result = await hc.run(FakeMachine(options={"Player Realms": "Separate"}), [leader, follower], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert _xyz(follower.teleport.await_args.args[0]) == _xyz(_CENTRE)


async def test_the_centre_is_never_read_from_a_client_not_in_battle():
    leader, follower = _leader_and_follower()
    p1, p2, p3, p4 = _patches_for_fast_retry()
    with p1, p2, p3, p4:
        await hc.run(FakeMachine(), [leader, follower], _DEFAULT_PARAMS)

    leader.duel.position.assert_awaited_once()
    # The follower got in only after the centre was published, so it never read its own.
    follower.duel.position.assert_not_called()


async def test_with_no_client_in_battle_every_attempt_still_aims_at_the_mob():
    client = FakeClient()
    client.find_closest_mob.return_value = FakeEntity()
    client.in_battle.return_value = False

    p1, p2, p3, p4 = _patches_for_fast_retry()
    with p1, p2, p3, p4, patch.object(hc, "ENTER_COMBAT_TIMEOUT", 0.1):
        result = await hc.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "FAILURE"
    assert client.tp_to.await_count > 1
    client.teleport.assert_not_called()
    client.duel.position.assert_not_called()


async def test_an_unreadable_centre_leaves_the_follower_aiming_at_the_mob():
    leader, follower = _leader_and_follower()
    leader.duel.position.side_effect = RuntimeError("duel read failed")
    follower.in_battle.side_effect = lambda: follower.tp_to.await_count >= 2

    p1, p2, p3, p4 = _patches_for_fast_retry()
    with p1, p2, p3, p4:
        machine = FakeMachine()
        result = await hc.run(machine, [leader, follower], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    follower.teleport.assert_not_called()
    assert len(_log_lines(machine, "Client 0: fight centre unreadable")) == 1
