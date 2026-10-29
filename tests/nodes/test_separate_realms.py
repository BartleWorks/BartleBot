"""Straggler recovery under Player Realms: Separate (#132).

A friends-list port moves the porter onto the target's realm, so in a Separate bot a player
who missed a transition may only be ported to one who made it on its own realm. Who that is
comes from note_realm_peers, taken as the transition begins: a door keeps each player's realm.
"""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.node_helpers as nh
from tests.conftest import FakeClient, FakeMachine, see_each_other
from tests.node_helper_patches import patch_helper

SEPARATE = {"Player Realms": "Separate"}


def _two_realms():
    """P1 and P2 on one realm, P3 and P4 on another, all standing at the same door."""
    clients = [FakeClient() for _ in range(4)]
    see_each_other(clients[0], clients[1])
    see_each_other(clients[2], clients[3])
    return clients


async def _recover(machine, clients, zones) -> list[tuple[list, str]]:
    """Run one recovery attempt and return each port as (movers, target name)."""
    names = {c: f"Wizard {i + 1}" for i, c in enumerate(clients)}
    port = AsyncMock(return_value=nh.PortToFriendResult(seen_loading={0, 1, 2}))
    first = next(i for i, z in enumerate(zones) if z == "Dst")
    with (
        patch.object(
            nh, "find_target_in_zone", AsyncMock(return_value=nh.ZoneTarget(index=first, name=names[clients[first]]))
        ),
        patch.object(nh.wizard_names, "cached_name", side_effect=lambda c: names[c]),
        patch_helper("port_to_friend", port),
        patch_helper("none_loading", AsyncMock(return_value=True)),
    ):
        await nh._attempt_zone_recovery(machine, clients, "Dst", zones, 0)
    return [(call.args[0], call.args[1]) for call in port.await_args_list]


async def test_together_every_straggler_ports_to_the_first_to_arrive():
    clients = _two_realms()
    ports = await _recover(FakeMachine(), clients, ["Dst", "Src", "Dst", "Src"])
    assert ports == [([clients[1], clients[3]], "wizard 1")]


async def test_separate_each_straggler_ports_to_someone_on_its_own_realm():
    clients = _two_realms()
    machine = FakeMachine(options=SEPARATE)
    await nh.note_realm_peers(machine, clients)
    ports = await _recover(machine, clients, ["Dst", "Src", "Dst", "Src"])
    assert ports == [([clients[1]], "wizard 1"), ([clients[3]], "wizard 3")]


async def test_separate_a_straggler_with_nobody_arrived_on_its_realm_stays():
    clients = _two_realms()
    machine = FakeMachine(options=SEPARATE)
    await nh.note_realm_peers(machine, clients)
    ports = await _recover(machine, clients, ["Dst", "Src", "Src", "Src"])
    assert ports == [([clients[1]], "wizard 1")]
    assert any("not porting it across realms" in msg for msg, _level in machine.logs)


async def test_separate_without_a_look_before_the_transition_nobody_is_ported():
    clients = _two_realms()
    ports = await _recover(FakeMachine(options=SEPARATE), clients, ["Dst", "Src", "Dst", "Src"])
    assert ports == []


async def test_noting_peers_costs_a_together_bot_nothing():
    clients = _two_realms()
    await nh.note_realm_peers(FakeMachine(), clients)
    for client in clients:
        client.get_base_entity_list.assert_not_called()


async def test_a_failed_port_does_not_log_the_name_it_could_not_find():
    clients = [FakeClient(), FakeClient()]
    machine = FakeMachine()
    with (
        patch.object(
            nh, "find_target_in_zone", AsyncMock(return_value=nh.ZoneTarget(index=0, name="Luke SkullHunter"))
        ),
        patch_helper("port_to_friend", AsyncMock(side_effect=ValueError("Luke SkullHunter not found"))),
    ):
        await nh._attempt_zone_recovery(machine, clients, "Dst", ["Dst", "Src"], 0)
    assert machine.logs
    assert not any("Luke" in msg for msg, _level in machine.logs)
