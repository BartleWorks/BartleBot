"""Unit tests for the handle_level_scaling node: its decisions, and each path through run()."""

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from wizwalker import XYZ

import bartlebot.nodes.utility.handle_level_scaling as hls
from bartlebot.game import scales
from bartlebot.nodes.teleportation import mark_location
from bartlebot.nodes.zone_navigation import Outcome
from tests.conftest import FakeClient, FakeMachine, zone_unreadable_at_first
from tests.node_helper_patches import patch_helper

_FARM = "Farm/Zone"
_FARM_SPOT = XYZ(100.0, 200.0, 0.0)
_HUB = "World/Hub"
_OUTER = "World/Dungeon"
_INNER = "World/Interiors/SecretBoss"


def _step(kind: str, from_zone: str, to_zone: str) -> scales.Step:
    return scales.Step(kind, from_zone, to_zone, XYZ(1.0, 2.0, 3.0), XYZ(4.0, 5.0, 6.0))


def _entry(*steps: scales.Step, zone: str) -> scales.Scales:
    return scales.Scales(
        key="test",
        name="Test Dungeon",
        world="Krokotopia",
        level=20,
        entrance_zone=steps[0].from_zone,
        steps=steps,
        zone=zone,
        interact_xyz=XYZ(10.0, 0.0, 0.0),
        reset_xyz=XYZ(20.0, 0.0, 0.0),
    )


_PLAIN = _entry(_step(scales.SIGIL, _HUB, _OUTER), zone=_OUTER)
_NESTED = _entry(_step(scales.SIGIL, _HUB, _OUTER), _step(scales.GATEWAY, _OUTER, _INNER), zone=_INNER)
_INDOOR_SIGIL = _entry(
    _step(scales.GATEWAY, _HUB, "World/Lobby"), _step(scales.SIGIL, "World/Lobby", _OUTER), zone=_OUTER
)

_PARAMS = {"scales": "test"}


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("option", "count", "expected"),
    [
        ([1, 3], 4, [1, 3]),
        ([1, 5], 2, [1]),  # a player number with no client behind it
        ([0, -1, 2], 2, [2]),
        ([], 4, []),
        (None, 4, []),
    ],
)
def test_selected_numbers_keeps_only_players_that_are_hooked(option, count, expected):
    assert hls.selected_numbers(option, count) == expected


def test_clients_somewhere_else_mark_navigate_and_make_every_step():
    trip = hls.plan_trip(_NESTED, [_FARM, _FARM])

    assert trip == hls.Trip(inside=False, navigate=True, steps=_NESTED.steps)


def test_clients_at_the_entrance_still_mark_and_make_every_step():
    """The entrance zone is outside the dungeon: navigation finds them already there."""
    trip = hls.plan_trip(_PLAIN, [_HUB])

    assert trip.inside is False
    assert trip.steps == _PLAIN.steps


def test_clients_in_the_scales_zone_have_nothing_left_and_do_not_mark():
    assert hls.plan_trip(_PLAIN, [_OUTER, _OUTER]) == hls.Trip(inside=True, navigate=False, steps=())


def test_clients_in_the_containing_dungeon_of_a_nested_scales_take_only_the_gateway():
    trip = hls.plan_trip(_NESTED, [_OUTER])

    assert trip == hls.Trip(inside=True, navigate=False, steps=_NESTED.steps[1:])


def test_clients_past_a_gateway_but_before_the_sigil_are_not_inside_yet():
    trip = hls.plan_trip(_INDOOR_SIGIL, ["World/Lobby"])

    assert trip == hls.Trip(inside=False, navigate=False, steps=_INDOOR_SIGIL.steps[1:])


@pytest.mark.parametrize("zones", [[_OUTER, _FARM], [_OUTER, None], [None], []])
def test_clients_split_across_zones_or_unreadable_start_from_navigation(zones):
    trip = hls.plan_trip(_PLAIN, zones)

    assert trip == hls.Trip(inside=False, navigate=True, steps=_PLAIN.steps)


def test_a_glob_in_a_step_matches_the_zone_the_clients_are_in():
    entry = _entry(_step(scales.SIGIL, _HUB, "World/Throne*"), zone="World/ThroneRoomB")

    assert hls.plan_trip(entry, ["World/ThroneRoomB"]).inside is True


def test_recall_mismatches_names_the_clients_off_their_marks():
    marks = [hls.Mark(_FARM, XYZ(0, 0, 0)), hls.Mark(_FARM, XYZ(0, 0, 0)), hls.Mark(_FARM, XYZ(0, 0, 0))]
    zones = [_FARM, _HUB, _FARM]
    positions = [XYZ(1, 0, 0), XYZ(0, 0, 0), XYZ(5000, 0, 0)]

    assert hls.recall_mismatches(marks, zones, positions, margin=10.0) == [2, 3]


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


class _World:
    """The node's collaborators, faked: who has an elixir, and what each reused node does."""

    def __init__(self, clients: list, entry: scales.Scales = _PLAIN, elixirs: set[int] = frozenset()):
        self.clients = clients
        self.entry = entry
        self.elixirs = set(elixirs)  # 1-based players showing an elixir
        self.calls: list[str] = []
        self.applied_to: list[int] = []
        self.sigil_params: dict = {}
        self.fail: set[str] = set()
        self.recall_zone = _FARM
        self.recall_spot = _FARM_SPOT
        for client in clients:
            client.zone_name.return_value = _FARM
            client.body.position.return_value = _FARM_SPOT

    def _move(self, zone: str, spot: XYZ | None = None) -> None:
        for client in self.clients:
            client.zone_name.return_value = zone
            if spot is not None:
                client.body.position.return_value = spot

    async def players_with_elixir(self, clients, numbers):
        return [n for n in numbers if n in self.elixirs]

    async def mark_client(self, machine, client):
        self.calls.append("mark")
        return "mark" not in self.fail

    async def navigate(self, machine, clients, target, **_):
        self.calls.append(f"navigate:{target}")
        if "navigate" in self.fail:
            return Outcome(False, "no route")
        self._move(target)
        return Outcome(True)

    async def enter_dungeon(self, machine, clients, params):
        self.calls.append(f"sigil:{params['dungeon_zone']}")
        self.sigil_params = params
        if "sigil" in self.fail:
            return "FAILURE"
        self._move(params["dungeon_zone"])
        return "SUCCESS"

    async def enter_zone(self, machine, clients, params):
        self.calls.append(f"gateway:{params['to_zone']}")
        self._move(params["to_zone"])
        return "SUCCESS"

    async def apply_elixir(self, machine, numbered, interact, reset, safety_delay):
        self.calls.append("apply")
        self.applied_to = [n for n, _ in numbered]
        if "apply" in self.fail:
            return False
        self.elixirs.update(self.applied_to)
        return True

    async def recall(self, machine, clients):
        self.calls.append("recall")
        if "recall" in self.fail:
            return False
        self._move(self.recall_zone, self.recall_spot)
        return True

    def patched(self) -> ExitStack:
        stack = ExitStack()
        for target, new in (
            ("players_with_elixir", self.players_with_elixir),
            ("mark_location.mark_client", self.mark_client),
            ("zone_navigation.navigate", self.navigate),
            ("enter_dungeon.run", self.enter_dungeon),
            ("enter_zone.run", self.enter_zone),
            ("apply_elixir", self.apply_elixir),
            ("teleport_to_marked_location.recall", self.recall),
        ):
            stack.enter_context(patch(f"bartlebot.nodes.utility.handle_level_scaling.{target}", new=new))
        stack.enter_context(patch("bartlebot.game.scales.get", new=lambda key: self.entry if key == "test" else None))
        return stack


def _machine(players: list[int]) -> FakeMachine:
    return FakeMachine(options={"Level Scaling Players": players, "Safety Delay": 0.0})


async def test_an_unknown_scales_fails_before_touching_a_client():
    client = FakeClient()
    world = _World([client])

    with world.patched():
        result = await hls.run(_machine([1]), [client], {"scales": "no_such_scales"})

    assert result == "FAILURE"
    assert world.calls == []


async def test_an_unset_scales_uses_the_default_of_the_bots_world():
    client = FakeClient()
    world = _World([client])
    machine = _machine([1])
    machine.bot_config = object()
    seen = []

    def default_for(config):
        seen.append(config)
        return _PLAIN

    with world.patched(), patch("bartlebot.game.scales.default_for", new=default_for):
        result = await hls.run(machine, [client], {"scales": ""})

    assert result == "SUCCESS"
    assert seen == [machine.bot_config]
    assert f"sigil:{_OUTER}" in world.calls


async def test_an_unset_scales_with_no_default_fails_and_says_why():
    client = FakeClient()
    world = _World([client])
    machine = _machine([1])

    with world.patched(), patch("bartlebot.game.scales.default_for", new=lambda config: None):
        result = await hls.run(machine, [client], {})

    assert result == "FAILURE"
    assert world.calls == []
    assert any("no default" in message for message, _ in machine.logs)


async def test_everyone_already_scaled_succeeds_at_once():
    clients = [FakeClient(), FakeClient()]
    world = _World(clients, elixirs={1, 2})

    with world.patched():
        result = await hls.run(_machine([1, 2]), clients, _PARAMS)

    assert result == "SUCCESS"
    assert world.calls == []


async def test_no_player_selected_succeeds_at_once():
    client = FakeClient()
    world = _World([client])

    with world.patched():
        result = await hls.run(_machine([]), [client], _PARAMS)

    assert result == "SUCCESS"
    assert world.calls == []


async def test_away_from_the_scales_everyone_marks_travels_and_recalls():
    """The whole trip, in order, with only the player lacking the elixir sent to the Scales."""
    clients = [FakeClient(), FakeClient(), FakeClient()]
    world = _World(clients, elixirs={1})
    machine = _machine([1, 2])

    with world.patched():
        result = await hls.run(machine, clients, _PARAMS)

    assert result == "SUCCESS"
    # Every client marks and travels, selected or not; only player 2 lacked the elixir.
    assert world.calls == ["mark", "mark", "mark", f"navigate:{_HUB}", f"sigil:{_OUTER}", "apply", "recall"]
    assert world.applied_to == [2]
    assert machine.node_scratch == {}


async def test_the_sigil_is_entered_without_team_up_or_challenge_mode():
    client = FakeClient()
    world = _World([client])

    with world.patched():
        await hls.run(_machine([1]), [client], _PARAMS)

    assert set(world.sigil_params) == {"from_zone", "dungeon_zone", "sigil_xyz", "reset_xyz"}
    assert world.sigil_params["sigil_xyz"] == {"x": 1.0, "y": 2.0, "z": 3.0}


async def test_inside_the_dungeon_players_scale_in_place_and_stay():
    client = FakeClient()
    world = _World([client])
    client.zone_name.return_value = _OUTER

    with world.patched():
        result = await hls.run(_machine([1]), [client], _PARAMS)

    assert result == "SUCCESS"
    assert world.calls == ["apply"]
    assert client.zone_name.return_value == _OUTER


async def test_players_in_a_challenge_mode_dungeon_are_left_alone():
    """Challenge Mode scales its players with no elixir, so the missing elixir must not send them out."""
    clients = [FakeClient(), FakeClient()]
    world = _World(clients)
    for client in clients:
        client.zone_name.return_value = "Aquila/AQ_Z03_Tartarus_Hard"

    with world.patched():
        result = await hls.run(_machine([1, 2]), clients, _PARAMS)

    assert result == "SUCCESS"
    assert world.calls == []


async def test_a_challenge_mode_zone_unreadable_at_first_is_still_left_alone():
    """A None is no Challenge Mode zone: read at once, it sent them out of the instance (#282)."""
    clients = [FakeClient(), FakeClient()]
    world = _World(clients)
    for client in clients:
        zone_unreadable_at_first(client, "Aquila/AQ_Z03_Tartarus_Hard")

    with world.patched():
        result = await hls.run(_machine([1, 2]), clients, _PARAMS)

    assert result == "SUCCESS"
    assert world.calls == []


async def test_one_player_outside_the_challenge_dungeon_still_makes_the_trip():
    clients = [FakeClient(), FakeClient()]
    world = _World(clients)
    clients[0].zone_name.return_value = "Aquila/AQ_Z03_Tartarus_Hard"

    with world.patched():
        await hls.run(_machine([1, 2]), clients, _PARAMS)

    assert "mark" in world.calls


async def test_a_nested_scales_is_reached_through_its_gateway():
    client = FakeClient()
    world = _World([client], entry=_NESTED)

    with world.patched():
        result = await hls.run(_machine([1]), [client], _PARAMS)

    assert result == "SUCCESS"
    assert world.calls == ["mark", f"navigate:{_HUB}", f"sigil:{_OUTER}", f"gateway:{_INNER}", "apply", "recall"]


async def test_a_failed_mark_fails_before_anyone_travels():
    client = FakeClient()
    world = _World([client])
    world.fail.add("mark")

    with world.patched():
        result = await hls.run(_machine([1]), [client], _PARAMS)

    assert result == "FAILURE"
    assert world.calls == ["mark"]


async def test_a_client_with_no_mana_fails_without_marking():
    client = FakeClient()
    world = _World([client])
    client.calc_mana_ratio.return_value = 0.0

    with world.patched():
        result = await hls.run(_machine([1]), [client], _PARAMS)

    assert result == "FAILURE"
    assert world.calls == []


@pytest.mark.parametrize(("failing", "last_call"), [("navigate", f"navigate:{_HUB}"), ("sigil", f"sigil:{_OUTER}")])
async def test_a_failed_trip_fails_without_applying(failing, last_call):
    client = FakeClient()
    world = _World([client])
    world.fail.add(failing)

    with world.patched():
        result = await hls.run(_machine([1]), [client], _PARAMS)

    assert result == "FAILURE"
    assert world.calls[-1] == last_call


async def test_a_player_still_unscaled_after_the_retries_fails_without_recalling():
    client = FakeClient()
    world = _World([client])
    world.fail.add("apply")
    machine = _machine([1])

    with world.patched():
        result = await hls.run(machine, [client], _PARAMS)

    assert result == "FAILURE"
    assert world.calls[-1] == "apply"
    assert machine.node_scratch == {}


async def test_a_failed_recall_fails():
    client = FakeClient()
    world = _World([client])
    world.fail.add("recall")

    with world.patched():
        assert await hls.run(_machine([1]), [client], _PARAMS) == "FAILURE"


@pytest.mark.parametrize(("zone", "spot"), [(_HUB, _FARM_SPOT), (_FARM, XYZ(9000.0, 9000.0, 0.0))])
async def test_a_recall_that_lands_somewhere_else_fails(zone, spot):
    client = FakeClient()
    world = _World([client])
    world.recall_zone, world.recall_spot = zone, spot

    with world.patched():
        assert await hls.run(_machine([1]), [client], _PARAMS) == "FAILURE"


async def test_a_recall_that_lands_somewhere_else_forgets_that_mark():
    """The run's record would otherwise have Mark Location pass the client by."""
    machine, client = _machine([1]), FakeClient()
    world = _World([client])
    world.recall_spot = XYZ(9000.0, 9000.0, 0.0)
    # The faked mark_client records nothing, so the record is set as the real one would.
    mark_location._remember(machine, client, mark_location.Mark(_FARM, _FARM_SPOT))

    with world.patched():
        assert await hls.run(machine, [client], _PARAMS) == "FAILURE"

    assert mark_location.marked_at(machine, client) is None


async def test_a_zone_unreadable_at_the_scales_is_waited_for_not_failed():
    """Off the sigil's loading screen the zone reads None for a moment (#282)."""
    client = FakeClient()
    world = _World([client])
    enter = world.enter_dungeon

    async def enter_then_unreadable(machine, clients, params):
        result = await enter(machine, clients, params)
        zone_unreadable_at_first(client, params["dungeon_zone"])
        return result

    world.enter_dungeon = enter_then_unreadable

    with world.patched():
        assert await hls.run(_machine([1]), [client], _PARAMS) == "SUCCESS"

    assert "apply" in world.calls


async def test_a_zone_unreadable_off_the_recall_is_waited_for_and_the_mark_kept():
    """Off the recall's loading screen the zone reads None for a moment (#282). Read at once,
    it counted as a miss, and the mark was forgotten."""
    machine, client = _machine([1]), FakeClient()
    world = _World([client])
    mark_location._remember(machine, client, mark_location.Mark(_FARM, _FARM_SPOT))
    recall = world.recall

    async def recall_then_unreadable(machine, clients):
        landed = await recall(machine, clients)
        zone_unreadable_at_first(client, world.recall_zone)
        return landed

    world.recall = recall_then_unreadable

    with world.patched():
        assert await hls.run(machine, [client], _PARAMS) == "SUCCESS"

    assert mark_location.marked_at(machine, client) is not None


async def test_an_elixir_that_landed_on_the_way_is_not_applied_again():
    """Who lacks the elixir is read again at the Scales, not carried from the first check."""
    client = FakeClient()
    world = _World([client])
    arrive = world.enter_dungeon

    async def arrive_scaled(machine, clients, params):
        world.elixirs.add(1)
        return await arrive(machine, clients, params)

    world.enter_dungeon = arrive_scaled

    with world.patched():
        result = await hls.run(_machine([1]), [client], _PARAMS)

    assert result == "SUCCESS"
    assert "apply" not in world.calls


# ---------------------------------------------------------------------------
# Rerun after a cancel
# ---------------------------------------------------------------------------


async def test_a_rerun_after_a_cancel_does_not_mark_again_and_returns_to_the_first_marks():
    """The ambient combat watcher cancels the node mid-trip; the rerun must not mark the hub."""
    client = FakeClient()
    world = _World([client])
    machine = _machine([1])

    async def cancelled_at_the_sigil(machine, clients, params):
        raise asyncio.CancelledError

    with world.patched():
        with patch("bartlebot.nodes.utility.handle_level_scaling.enter_dungeon.run", new=cancelled_at_the_sigil):
            with pytest.raises(asyncio.CancelledError):
                await hls.run(machine, [client], _PARAMS)
        assert world.calls == ["mark", f"navigate:{_HUB}"]
        assert hls.SCRATCH_KEY in machine.node_scratch

        world.calls.clear()
        result = await hls.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    assert "mark" not in world.calls
    assert world.calls[-2:] == ["apply", "recall"]
    assert machine.node_scratch == {}


async def test_a_rerun_cancelled_after_the_elixirs_landed_only_recalls():
    client = FakeClient()
    world = _World([client], elixirs={1})
    machine = _machine([1])
    machine.node_scratch[hls.SCRATCH_KEY] = ([client], [hls.Mark(_FARM, _FARM_SPOT)])
    client.zone_name.return_value = _OUTER

    with world.patched():
        result = await hls.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    assert world.calls == ["recall"]


async def test_marks_kept_for_other_clients_are_dropped():
    """A relog swaps the client objects, so marks for the old ones say nothing about these."""
    client = FakeClient()
    world = _World([client], elixirs={1})
    machine = _machine([1])
    machine.node_scratch[hls.SCRATCH_KEY] = ([FakeClient()], [hls.Mark(_FARM, _FARM_SPOT)])

    with world.patched():
        result = await hls.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    assert world.calls == []
    assert machine.node_scratch == {}


# ---------------------------------------------------------------------------
# The elixir read
# ---------------------------------------------------------------------------


async def test_players_with_elixir_reads_only_the_players_asked_about():
    clients = [FakeClient(), FakeClient(), FakeClient()]
    shown = {id(clients[0]): True, id(clients[2]): False}
    read = AsyncMock(side_effect=lambda client, path: shown[id(client)])

    with patch_helper("is_visible_by_path", new=read):
        assert await hls.players_with_elixir(clients, [1, 3]) == [1]

    assert read.await_count == 2
