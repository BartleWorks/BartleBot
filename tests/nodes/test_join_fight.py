"""Tests for combat_runner.join_fight and FightCenter, shared by handle_combat and the ambient watcher.

handle_combat's own tests cover joining with a mob to aim at; these cover what the ambient
watcher relies on: never walking before a center exists, and giving up on a fight that has
already ended. Every walk is the suite's walking double (``walk_double``), recorded on
``FakeClient.walk_to``.
"""

import asyncio

import pytest
from bartleby.runtime.movement.walk import WalkEnd
from wizwalker import XYZ

from bartlebot.nodes import combat_runner, node_helpers
from bartlebot.nodes.combat_runner import FightCenter, join_fight
from tests.conftest import FakeClient, FakeEntity, FakeMachine


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    """Compress the retry cadence. A cadence, not a ceiling: each test's timeout is what it races."""
    monkeypatch.setattr(combat_runner, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(combat_runner, "COMBAT_TP_RETRY_INTERVAL", 0.01)


async def test_with_nothing_to_aim_at_a_client_never_walks_before_a_center_exists():
    client = FakeClient()
    joined = await join_fight(FakeMachine(), client, 1, FightCenter(), timeout=0.1)

    assert joined is False
    client.walk_to.assert_not_awaited()
    client.teleport.assert_not_awaited()
    client.tp_to.assert_not_awaited()


async def test_with_nothing_to_aim_at_a_client_walks_into_a_published_center():
    machine = FakeMachine()
    fighter, helper = FakeClient(), FakeClient()
    fighter.in_battle.return_value = True
    fight = FightCenter()
    await fight.publish(machine, fighter, 0)

    async def walk(_where):
        helper.in_battle.return_value = True
        return WalkEnd.FIGHT

    helper.walk_to.side_effect = walk
    assert await join_fight(machine, helper, 1, fight, timeout=1.0) is True
    (where,) = helper.walk_to.await_args.args
    assert (where.x, where.y, where.z) == (500.0, 600.0, 0.0)  # the fighter's duel center
    helper.teleport.assert_not_awaited()


async def test_the_time_spent_walking_is_not_counted_against_the_timeout():
    """A mob across the room takes seconds to reach; the timeout was sized for a teleport."""
    machine = FakeMachine()
    fighter, helper = FakeClient(), FakeClient()
    fighter.in_battle.return_value = True
    fight = FightCenter()
    await fight.publish(machine, fighter, 0)

    async def walk(_where):
        await asyncio.sleep(0.3)  # longer than the whole timeout
        asyncio.get_running_loop().call_later(0.02, setattr, helper.in_battle, "return_value", True)

    helper.walk_to.side_effect = walk
    assert await join_fight(machine, helper, 1, fight, timeout=0.1) is True


async def test_a_client_gives_up_once_the_fight_is_over():
    machine = FakeMachine()
    fighter, late = FakeClient(), FakeClient()
    fighter.in_battle.return_value = True
    fight = FightCenter()
    await fight.publish(machine, fighter, 0)
    fighter.in_battle.return_value = False  # the fight ended before the late client arrived

    assert await join_fight(machine, late, 1, fight, timeout=5.0) is False
    late.walk_to.assert_not_awaited()
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
    elsewhere.walk_to.assert_not_awaited()


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


def _not_taken_in_at_the_center(client, *, joins_at=None, end=None):
    """The walk to the center arrives (or ends *end*) and the circle does not take *client* in;
    a walk to *joins_at* gets it into the fight."""

    async def walk(where):
        if joins_at is not None and tuple(where) == tuple(joins_at):
            client.in_battle.return_value = True
            return WalkEnd.FIGHT
        return end

    client.walk_to.side_effect = walk


_CENTER = (500.0, 600.0, 0.0)


async def test_a_center_reached_without_being_taken_in_is_blocked_and_it_joins_by_a_player():
    machine = FakeMachine()
    fighter, helper = FakeClient(), FakeClient()
    fight = await _fight_with(machine, fighter)
    _not_taken_in_at_the_center(helper, joins_at=_SEAT)

    assert await join_fight(machine, helper, 1, fight, timeout=5.0) is True
    targets = [tuple(c.args[0]) for c in helper.walk_to.await_args_list]
    assert targets == [_CENTER, tuple(_SEAT)]
    assert fight.is_center_blocked(helper)
    assert any("the fight center did not take it in (arrived)" in msg for msg, _ in machine.logs)


async def test_a_center_the_walk_cannot_reach_is_blocked_too():
    """The obstacle the fight cleared (#157) is still solid in the zone's map."""
    machine = FakeMachine()
    fighter, helper = FakeClient(), FakeClient()
    fight = await _fight_with(machine, fighter)
    _not_taken_in_at_the_center(helper, joins_at=_SEAT, end=WalkEnd.NO_ROUTE)

    assert await join_fight(machine, helper, 1, fight, timeout=5.0) is True
    assert [tuple(c.args[0]) for c in helper.walk_to.await_args_list] == [_CENTER, tuple(_SEAT)]
    assert any("did not take it in (no route)" in msg for msg, _ in machine.logs)


async def test_once_the_center_is_blocked_a_later_joiner_goes_straight_to_a_player():
    machine = FakeMachine()
    fighter, late = FakeClient(), FakeClient()
    fight = await _fight_with(machine, fighter)
    fight.mark_center_blocked(late)
    _not_taken_in_at_the_center(late, joins_at=_SEAT)

    assert await join_fight(machine, late, 1, fight, timeout=1.0) is True
    (where,) = late.walk_to.await_args.args
    assert tuple(where) == tuple(_SEAT)


async def test_a_center_that_works_never_falls_back_to_a_player():
    machine = FakeMachine()
    fighter, helper = FakeClient(), FakeClient()
    fight = await _fight_with(machine, fighter)
    _not_taken_in_at_the_center(helper, joins_at=_CENTER)

    assert await join_fight(machine, helper, 1, fight, timeout=1.0) is True
    assert helper.walk_to.await_count == 1
    assert not fight.is_center_blocked(helper)


async def test_each_attempt_after_a_blocked_center_tries_the_next_player():
    # The second walk gets it in, so the run ends on the attempt count, not on a timeout
    # a loaded worker could reach after the first attempt (#199).
    machine = FakeMachine()
    first, second, late = FakeClient(), FakeClient(), FakeClient()
    fight = await _fight_with(machine, first, second)
    second.body.position.return_value = XYZ(480.0, 600.0, 0.0)
    fight.mark_center_blocked(late)

    async def walk(_where):
        late.in_battle.return_value = late.walk_to.await_count >= 2

    late.walk_to.side_effect = walk

    assert await join_fight(machine, late, 1, fight, timeout=5.0) is True
    targets = [tuple(c.args[0]) for c in late.walk_to.await_args_list]
    assert targets == [tuple(_SEAT), (480.0, 600.0, 0.0)]


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
    (where,) = late.walk_to.await_args.args
    assert tuple(where) == _CENTER


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
