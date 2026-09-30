"""Unit tests for the sync_in_zone node.

Route planning, the leader, the realm hop and the gathering are zone_navigation's and are
tested there. This node only has to refuse an empty zone, say which realm the leader hops
to, and report navigate()'s outcome.
"""

from unittest.mock import AsyncMock, patch

import pytest

import bartlebot.nodes.teleportation.sync_in_zone as siz
from bartlebot.nodes.zone_navigation import Outcome
from tests.conftest import FakeClient, FakeMachine

_ZONE = "WizardCity/WC_Hub"


def _navigate(success: bool):
    return patch.object(siz.zone_navigation, "navigate", new=AsyncMock(return_value=Outcome(success=success)))


async def test_success_when_every_player_arrives():
    clients = [FakeClient(), FakeClient()]
    machine = FakeMachine()

    with _navigate(True) as navigate:
        result = await siz.run(machine, clients, {"zone": _ZONE})

    assert result == "SUCCESS"
    navigate.assert_awaited_once_with(machine, clients, _ZONE, realm=None)


async def test_failure_when_navigation_fails():
    with _navigate(False):
        result = await siz.run(FakeMachine(), [FakeClient()], {"zone": _ZONE})

    assert result == "FAILURE"


@pytest.mark.parametrize("params", [{}, {"zone": ""}, {"zone": None}], ids=["missing", "empty", "none"])
async def test_no_zone_fails_without_moving_anyone(params):
    machine = FakeMachine()

    with _navigate(True) as navigate:
        result = await siz.run(machine, [FakeClient()], params)

    assert result == "FAILURE"
    navigate.assert_not_awaited()
    assert any("no zone" in msg for msg, _ in machine.logs)


# ---------------------------------------------------------------------------
# The realm the leader hops to (#133)
# ---------------------------------------------------------------------------


async def test_the_leader_hops_to_the_favorite_realm():
    machine = FakeMachine(options={"Favorite Realm": "Balestrom"})

    with _navigate(True) as navigate:
        await siz.run(machine, [FakeClient()], {"zone": _ZONE})

    assert navigate.await_args.kwargs["realm"] == "Balestrom"


async def test_keep_current_realm_never_hops():
    machine = FakeMachine(options={"Favorite Realm": "Balestrom"})

    with _navigate(True) as navigate:
        await siz.run(machine, [FakeClient()], {"zone": _ZONE, siz.PARAM_KEEP_CURRENT_REALM: True})

    assert navigate.await_args.kwargs["realm"] is None


async def test_no_favorite_set_is_no_hop_rather_than_a_failure():
    """Change Realm fails without a favorite; the sync still puts everyone on one realm."""
    with _navigate(True) as navigate:
        result = await siz.run(FakeMachine(options={"Favorite Realm": ""}), [FakeClient()], {"zone": _ZONE})

    assert result == "SUCCESS"
    assert navigate.await_args.kwargs["realm"] is None


async def test_an_unknown_favorite_fails_before_moving_anyone():
    machine = FakeMachine(options={"Favorite Realm": "Nowhere"})

    with _navigate(True) as navigate:
        result = await siz.run(machine, [FakeClient()], {"zone": _ZONE})

    assert result == "FAILURE"
    navigate.assert_not_awaited()


def test_the_favorite_realm_is_offered_only_while_the_node_may_hop():
    spec = next(o for o in siz.NODE_SPEC.required_bot_options if o.name == "Favorite Realm")
    assert spec.default is None
    assert spec.required_unless == siz.PARAM_KEEP_CURRENT_REALM


# ---------------------------------------------------------------------------
# Player Sync, the shipped definition built on this node (#133)
# ---------------------------------------------------------------------------


def test_player_sync_is_a_relog_then_a_sync_and_nothing_else():
    from bartlebot.bot.schema import SubgraphDefinition  # noqa: PLC0415
    from bartlebot.paths import project_root  # noqa: PLC0415

    defn = SubgraphDefinition.load(project_root() / "subgraphs" / "sync-players.json")
    members = {s.id: s.node_type for s in defn.states if not s.node_type.startswith("subgraph_")}
    ports = {s.label: s.id for s in defn.states if s.node_type.startswith("subgraph_")}
    edges = {(t.source, t.dest) for t in defn.transitions}

    assert sorted(members.values()) == ["relog", "sync_in_zone"], "change_realm left Player Sync (#133)"
    sync = next(sid for sid, kind in members.items() if kind == "sync_in_zone")
    relog = next(sid for sid, kind in members.items() if kind == "relog")
    assert (ports["Start"], sync) in edges
    assert (ports["Recover"], relog) in edges
    assert (relog, sync) in edges
    assert (sync, ports["Exit"]) in edges
