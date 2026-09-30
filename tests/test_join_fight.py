"""Tests for combat_runner.join_fight and FightCentre, shared by handle_combat and the ambient watcher.

handle_combat's own tests cover joining with a mob to aim at; these cover what the ambient
watcher relies on: never teleporting before a centre exists, and giving up on a fight that
has already ended.
"""

import pytest
from wizwalker import XYZ

from bartlebot.nodes import combat_runner, node_helpers
from bartlebot.nodes.combat_runner import FightCentre, join_fight
from tests.conftest import FakeClient, FakeEntity, FakeMachine


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    """Compress the retry cadence. A cadence, not a ceiling: each test's timeout is what it races."""
    monkeypatch.setattr(combat_runner, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(combat_runner, "COMBAT_TP_RETRY_INTERVAL", 0.01)


async def test_with_nothing_to_aim_at_a_client_never_teleports_before_a_centre_exists():
    client = FakeClient()
    joined = await join_fight(FakeMachine(), client, 1, FightCentre(), timeout=0.1)

    assert joined is False
    client.teleport.assert_not_awaited()
    client.tp_to.assert_not_awaited()


async def test_with_nothing_to_aim_at_a_client_teleports_to_a_published_centre():
    machine = FakeMachine()
    fighter, helper = FakeClient(), FakeClient()
    fighter.in_battle.return_value = True
    fight = FightCentre()
    await fight.publish(machine, fighter, 0)

    async def teleport(_where):
        helper.in_battle.return_value = True

    helper.teleport.side_effect = teleport
    assert await join_fight(machine, helper, 1, fight, timeout=1.0) is True
    (where,) = helper.teleport.await_args.args
    assert (where.x, where.y, where.z) == (500.0, 600.0, 0.0)  # the fighter's duel centre


async def test_a_client_gives_up_once_the_fight_is_over():
    machine = FakeMachine()
    fighter, late = FakeClient(), FakeClient()
    fighter.in_battle.return_value = True
    fight = FightCentre()
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
    fight = FightCentre()
    await fight.publish(machine, publisher, 0)
    await fight.publish(machine, joiner, 1)
    publisher.in_battle.return_value = False

    assert await fight.is_over(late) is False
    joiner.in_battle.return_value = False
    assert await fight.is_over(late) is True


async def test_a_centre_is_never_shared_across_realms():
    machine = FakeMachine()
    fighter, elsewhere = FakeClient(), FakeClient()
    fighter.in_battle.return_value = True
    fight = FightCentre(share=lambda _a, _b: False)
    await fight.publish(machine, fighter, 0)

    assert fight.centre_for(elsewhere) is None
    assert await join_fight(machine, elsewhere, 1, fight, timeout=0.05) is False
    elsewhere.teleport.assert_not_awaited()


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
