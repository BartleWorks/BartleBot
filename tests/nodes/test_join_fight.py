"""Tests for combat_runner.join_fight and FightCenter, shared by handle_combat and the ambient watcher.

handle_combat's own tests cover joining with a mob to aim at; these cover what the ambient
watcher relies on: never teleporting before a center exists, and giving up on a fight that
has already ended.
"""

import pytest
from wizwalker import XYZ

from bartlebot.nodes import combat_runner, node_helpers
from bartlebot.nodes.combat_runner import FightCenter, join_fight
from tests.conftest import FakeClient, FakeEntity, FakeMachine


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    """Compress the retry cadence. A cadence, not a ceiling: each test's timeout is what it races."""
    monkeypatch.setattr(combat_runner, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(combat_runner, "COMBAT_TP_RETRY_INTERVAL", 0.01)


async def test_with_nothing_to_aim_at_a_client_never_teleports_before_a_center_exists():
    client = FakeClient()
    joined = await join_fight(FakeMachine(), client, 1, FightCenter(), timeout=0.1)

    assert joined is False
    client.teleport.assert_not_awaited()
    client.tp_to.assert_not_awaited()


async def test_with_nothing_to_aim_at_a_client_teleports_to_a_published_center():
    machine = FakeMachine()
    fighter, helper = FakeClient(), FakeClient()
    fighter.in_battle.return_value = True
    fight = FightCenter()
    await fight.publish(machine, fighter, 0)

    async def teleport(_where):
        helper.in_battle.return_value = True

    helper.teleport.side_effect = teleport
    assert await join_fight(machine, helper, 1, fight, timeout=1.0) is True
    (where,) = helper.teleport.await_args.args
    assert (where.x, where.y, where.z) == (500.0, 600.0, 0.0)  # the fighter's duel center


async def test_a_client_gives_up_once_the_fight_is_over():
    machine = FakeMachine()
    fighter, late = FakeClient(), FakeClient()
    fighter.in_battle.return_value = True
    fight = FightCenter()
    await fight.publish(machine, fighter, 0)
    fighter.in_battle.return_value = False  # the fight ended before the late client arrived

    assert await join_fight(machine, late, 1, fight, timeout=5.0) is False
    late.teleport.assert_not_awaited()
    assert any("fight ended before it could join" in msg for msg, _ in machine.logs)


async def test_a_fight_is_not_over_while_anyone_who_joined_it_is_still_in_battle():
    """The publisher fleeing or finishing first does not end the fight for the rest."""
    machine = FakeMachine()
    publisher, joiner, late = FakeClient(), FakeClient(), FakeClient()
    publisher.in_battle.return_value = True
    joiner.in_battle.return_value = True
    fight = FightCenter()
    await fight.publish(machine, publisher, 0)
    await fight.publish(machine, joiner, 1)
    publisher.in_battle.return_value = False

    assert await fight.is_over(late) is False
    joiner.in_battle.return_value = False
    assert await fight.is_over(late) is True


async def test_a_center_is_never_shared_across_realms():
    machine = FakeMachine()
    fighter, elsewhere = FakeClient(), FakeClient()
    fighter.in_battle.return_value = True
    fight = FightCenter(share=lambda _a, _b: False)
    await fight.publish(machine, fighter, 0)

    assert fight.center_for(elsewhere) is None
    assert await join_fight(machine, elsewhere, 1, fight, timeout=0.05) is False
    elsewhere.teleport.assert_not_awaited()


# ---------------------------------------------------------------------------
# A center that cannot be stood on (#157)
# ---------------------------------------------------------------------------

_START = XYZ(10.0, 20.0, 30.0)
_SEAT = XYZ(520.0, 600.0, 0.0)


async def _fight_with(machine, *members, seat=_SEAT) -> FightCenter:
    fight = FightCenter()
    for i, member in enumerate(members):
        member.in_battle.return_value = True
        member.body.position.return_value = seat
        await fight.publish(machine, member, i)
    return fight


def _put_back_from_the_center(client, *, joins_at=None):
    """The game puts *client* back on _START after a teleport to the center; *joins_at* gets it in."""
    client.body.position.return_value = _START

    async def teleport(where):
        if joins_at is not None and tuple(where) == tuple(joins_at):
            client.in_battle.return_value = True
            return
        # Seen at the center for one read, then back where it started.
        client.body.position.side_effect = [where, *([_START] * 1000)]

    client.teleport.side_effect = teleport


async def test_a_center_that_puts_the_client_back_is_blocked_and_it_joins_by_a_player():
    machine = FakeMachine()
    fighter, helper = FakeClient(), FakeClient()
    fight = await _fight_with(machine, fighter)
    _put_back_from_the_center(helper, joins_at=_SEAT)

    assert await join_fight(machine, helper, 1, fight, timeout=1.0) is True
    targets = [tuple(c.args[0]) for c in helper.teleport.await_args_list]
    assert targets == [(500.0, 600.0, 0.0), tuple(_SEAT)]
    assert fight.is_center_blocked(helper)
    assert any("put it back where it started" in msg for msg, _ in machine.logs)


async def test_once_the_center_is_blocked_a_later_joiner_goes_straight_to_a_player():
    machine = FakeMachine()
    fighter, late = FakeClient(), FakeClient()
    fight = await _fight_with(machine, fighter)
    fight.mark_center_blocked(late)
    late.teleport.side_effect = lambda _where: setattr(late.in_battle, "return_value", True)

    assert await join_fight(machine, late, 1, fight, timeout=1.0) is True
    (where,) = late.teleport.await_args.args
    assert tuple(where) == tuple(_SEAT)


async def test_a_client_that_never_seems_to_move_does_not_block_the_center():
    """Back on the start with no move seen is not the sign: the teleport may simply not have landed."""
    machine = FakeMachine()
    fighter, helper = FakeClient(), FakeClient()
    fight = await _fight_with(machine, fighter)
    helper.body.position.return_value = _START

    assert await join_fight(machine, helper, 1, fight, timeout=0.05) is False
    assert not fight.is_center_blocked(helper)
    assert all(tuple(c.args[0]) == (500.0, 600.0, 0.0) for c in helper.teleport.await_args_list)


async def test_a_client_left_somewhere_else_does_not_block_the_center():
    machine = FakeMachine()
    fighter, helper = FakeClient(), FakeClient()
    fight = await _fight_with(machine, fighter)
    helper.body.position.return_value = _START

    async def teleport(where):
        helper.body.position.side_effect = [where, *([XYZ(11.0, 20.0, 30.0)] * 1000)]

    helper.teleport.side_effect = teleport

    assert await join_fight(machine, helper, 1, fight, timeout=0.05) is False
    assert not fight.is_center_blocked(helper)


async def test_a_center_that_works_never_falls_back_to_a_player():
    machine = FakeMachine()
    fighter, helper = FakeClient(), FakeClient()
    fight = await _fight_with(machine, fighter)
    helper.body.position.return_value = _START
    helper.teleport.side_effect = lambda _where: setattr(helper.in_battle, "return_value", True)

    assert await join_fight(machine, helper, 1, fight, timeout=1.0) is True
    assert helper.teleport.await_count == 1
    assert not fight.is_center_blocked(helper)


async def test_each_attempt_after_a_blocked_center_tries_the_next_player():
    machine = FakeMachine()
    first, second, late = FakeClient(), FakeClient(), FakeClient()
    fight = await _fight_with(machine, first, second)
    second.body.position.return_value = XYZ(480.0, 600.0, 0.0)
    fight.mark_center_blocked(late)

    await join_fight(machine, late, 1, fight, timeout=0.03)
    targets = [tuple(c.args[0]) for c in late.teleport.await_args_list]
    assert targets[:2] == [tuple(_SEAT), (480.0, 600.0, 0.0)]


async def test_a_player_whose_position_cannot_be_read_is_passed_over():
    machine = FakeMachine()
    first, second, late = FakeClient(), FakeClient(), FakeClient()
    fight = await _fight_with(machine, first, second)
    first.body.position.side_effect = RuntimeError("unreadable")
    fight.mark_center_blocked(late)

    assert tuple(await fight.member_position(late)) == tuple(_SEAT)


async def test_with_no_player_position_readable_a_blocked_center_is_tried_again():
    machine = FakeMachine()
    fighter, late = FakeClient(), FakeClient()
    fight = await _fight_with(machine, fighter)
    fighter.body.position.side_effect = RuntimeError("unreadable")
    fight.mark_center_blocked(late)

    await join_fight(machine, late, 1, fight, timeout=0.02)
    (where,) = late.teleport.await_args.args
    assert tuple(where) == (500.0, 600.0, 0.0)


# ---------------------------------------------------------------------------
# mob_clearance
# ---------------------------------------------------------------------------


async def test_clearance_is_the_distance_to_the_nearest_loaded_mob():
    client = FakeClient()
    far, near = FakeEntity(), FakeEntity()
    far.location.return_value = XYZ(0, 3000, 0)
    near.location.return_value = XYZ(0, 1000, 0)
    client.get_mobs.return_value = [far, near]

    assert await node_helpers.mob_clearance(client, XYZ(0, 0, 0)) == 1000
    assert await node_helpers.mob_clearances(client, [XYZ(0, 0, 0), XYZ(0, 2000, 0)]) == [1000, 1000]
    client.get_mobs.assert_awaited()


async def test_clearance_with_no_mob_loaded_is_infinite():
    assert await node_helpers.mob_clearance(FakeClient(), XYZ(0, 0, 0)) == float("inf")
