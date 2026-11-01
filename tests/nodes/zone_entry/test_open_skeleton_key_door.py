"""Unit tests for the open_skeleton_key_door node."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from wizwalker import Keycode

import bartlebot.nodes.node_helpers as _nh
import bartlebot.nodes.zone_entry.enter_zone as enter_zone
import bartlebot.nodes.zone_entry.open_skeleton_key_door as door_node
from bartlebot.bot.schema import BotConfig
from tests.conftest import FakeClient, FakeMachine, zone_unreadable_at_first
from tests.node_helper_patches import patch_helper, set_helper

_PARAMS = {
    "from_zone": "TestZone",
    "to_zone": "KeyRoom",
    "interact_xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
    "xyz": {"x": 2.0, "y": 0.0, "z": 0.0},
    "reset_xyz": {"x": 1.0, "y": 0.0, "z": 0.0},
}
_DOOR = (0.0, 0.0, 0.0)
_TRIGGER = (2.0, 0.0, 0.0)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    """Compress the cadences; the ceilings (Safety Delay, TRIGGER_TIMEOUT) stay real."""
    monkeypatch.setattr(_nh, "PROMPT_CLEAR_SETTLE", 0.0)
    set_helper(monkeypatch, "DIALOG_PAGE_SETTLE", 0.0)
    set_helper(monkeypatch, "DIALOG_FAST_POLL_INTERVAL", 0.0)
    set_helper(monkeypatch, "POLL_INTERVAL", 0.0)
    monkeypatch.setattr(door_node, "POLL_INTERVAL", 0.0)
    monkeypatch.setattr(enter_zone, "POLL_INTERVAL", 0.0)
    monkeypatch.setattr(door_node, "KEY_DOOR_PROMPT_SETTLE", 0.0)
    monkeypatch.setattr(door_node, "KEY_DOOR_RETRY_INTERVAL", 0.02)
    # Long enough that nothing reaches the cutoff unless a test says so.
    monkeypatch.setattr(door_node, "KEY_DOOR_TELEPORT_CUTOFF", 5.0)
    monkeypatch.setattr(door_node, "KEY_DOOR_ENTRY_TIMEOUT", 15.0)
    with patch.object(enter_zone, "watch_group", new=AsyncMock()):
        yield


class _Door:
    """A skeleton key door: its prompt shows at its spot, to a player with a key, while locked.

    A press from a player with a key unlocks it and costs that player one key. A press once it
    is unlocked, or from a player with none, does nothing, as in the game. Its trigger fires only
    once it is unlocked, and takes the player to the room. The game's carry-in is the test's to
    call, by ``carry``.
    """

    def __init__(self, keys: list[int], *, locked: bool = True, safety_delay: float = 0.0):
        self.locked = locked
        self.keys = list(keys)
        self.events: list[str] = []
        self.clients = [FakeClient() for _ in keys]
        self.machine = FakeMachine(options={"Safety Delay": safety_delay})
        for index, client in enumerate(self.clients):
            client.is_in_npc_range.side_effect = self._showing(index)
            client.send_key.side_effect = self._pressed(index)
            client.teleport.side_effect = self._teleported(index)

    def _showing(self, index: int):
        client = self.clients[index]

        async def _showing() -> bool:
            if not client.teleport.call_args_list or client.zone_name.return_value != "TestZone":
                return False
            at_door = tuple(client.teleport.call_args_list[-1].args[0]) == _DOOR
            return at_door and self.locked and self.keys[index] > 0

        return _showing

    def _pressed(self, index: int):
        async def _pressed(key, *_args) -> None:
            if key == Keycode.X and self.locked and self.keys[index] > 0:
                self.keys[index] -= 1
                self.locked = False

        return _pressed

    def _teleported(self, index: int):
        async def _teleported(point) -> None:
            if (
                tuple(point) == _TRIGGER
                and not self.locked
                and self.clients[index].zone_name.return_value == "TestZone"
            ):
                self.carry(index)

        return _teleported

    def carry(self, index: int) -> None:
        self.events.append(f"P{index + 1} in")
        self.clients[index].zone_name.return_value = "KeyRoom"

    def presses(self, index: int) -> int:
        return sum(1 for call in self.clients[index].send_key.call_args_list if call.args[0] == Keycode.X)

    def total_presses(self) -> int:
        return sum(self.presses(i) for i in range(len(self.clients)))

    def teleports_to_trigger(self, index: int) -> int:
        return sum(1 for call in self.clients[index].teleport.call_args_list if tuple(call.args[0]) == _TRIGGER)


async def _run(door: _Door) -> str:
    with patch_helper("is_visible_by_path", return_value=False):
        return await door_node.run(door.machine, door.clients, _PARAMS)


def _in_room(door: _Door) -> bool:
    return all(c.zone_name.return_value == "KeyRoom" for c in door.clients)


# ---------------------------------------------------------------------------
# Who uses the key
# ---------------------------------------------------------------------------


async def test_only_the_lowest_numbered_player_with_a_key_presses():
    door = _Door([10, 10, 10, 10])
    assert await _run(door) == "SUCCESS"
    assert door.keys == [9, 10, 10, 10]
    assert [door.presses(i) for i in range(4)] == [1, 0, 0, 0]


async def test_a_player_out_of_keys_is_skipped_for_the_next():
    door = _Door([0, 5, 0, 5])
    assert await _run(door) == "SUCCESS"
    assert door.keys == [0, 4, 0, 5]
    assert [door.presses(i) for i in range(4)] == [0, 1, 0, 0]


async def test_one_player_holding_the_only_key_opens_it():
    door = _Door([0, 0, 0, 1])
    assert await _run(door) == "SUCCESS"
    assert door.keys == [0, 0, 0, 0]
    assert _in_room(door)


async def test_the_choice_waits_for_the_first_players_prompt_to_render(monkeypatch):
    """P2's prompt came up first, but P1 has a key and should be the one to spend it."""
    monkeypatch.setattr(door_node, "KEY_DOOR_PROMPT_SETTLE", 1.0)
    door = _Door([5, 5])
    showing = door.clients[0].is_in_npc_range.side_effect
    reads = {"count": 0}

    async def _p1_a_moment_late() -> bool:
        reads["count"] += 1
        return reads["count"] > 2 and await showing()

    door.clients[0].is_in_npc_range.side_effect = _p1_a_moment_late

    assert await _run(door) == "SUCCESS"
    assert (door.presses(0), door.presses(1)) == (1, 0)


async def test_a_player_without_a_key_does_not_cost_a_retry():
    """The wait for the prompt ends at the first player to have it, not when all do.

    Waiting for all would find the keyless player's prompt missing, reset, and approach
    again, MAX_RETRIES times over, before giving up on it.
    """
    door = _Door([0, 3])
    assert await _run(door) == "SUCCESS"
    approaches = [c for c in door.clients[1].teleport.call_args_list if tuple(c.args[0]) == _DOOR]
    assert len(approaches) == 1


async def test_a_dropped_press_is_repeated_while_the_prompt_stays():
    """One press has nothing behind it, and the game drops them."""
    door = _Door([5, 5])
    door_press = door.clients[0].send_key.side_effect
    dropped = {"left": 1}

    async def _drops_the_first(key, *args) -> None:
        if key == Keycode.X and dropped["left"]:
            dropped["left"] -= 1
            return
        await door_press(key, *args)

    door.clients[0].send_key.side_effect = _drops_the_first

    assert await _run(door) == "SUCCESS"
    assert door.presses(0) == 2
    assert door.keys == [4, 5], "the press that landed spent the only key"


async def test_a_prompt_that_stays_after_x_fails():
    """Every press is swallowed, so the prompt never goes."""
    door = _Door([5, 5])
    for client in door.clients:
        client.send_key.side_effect = None
    with patch.object(door_node, "TRIGGER_TIMEOUT", 0.05):
        assert await _run(door) == "FAILURE"
    assert door.locked


# ---------------------------------------------------------------------------
# No prompt
# ---------------------------------------------------------------------------


async def test_no_player_has_a_key_returns_no_key_without_pressing_anything():
    door = _Door([0, 0, 0, 0])
    assert await _run(door) == "NO_KEY"
    assert door.total_presses() == 0
    assert any("NO_KEY" in str(log) for log in door.machine.logs)


async def test_a_door_already_open_is_entered_with_no_key_spent():
    """A rerun after a fight finds no prompt on an open door, which reads like having no key."""
    door = _Door([5, 5], locked=False)
    assert await _run(door) == "SUCCESS"
    assert door.total_presses() == 0
    assert door.keys == [5, 5]
    assert _in_room(door)


# ---------------------------------------------------------------------------
# Entering
# ---------------------------------------------------------------------------


async def test_the_unlocker_goes_first_with_four_players_and_the_rest_wait_for_it():
    door = _Door([0, 5, 5, 5])
    assert await _run(door) == "SUCCESS"
    assert door.events[0] == "P2 in"
    assert sorted(door.events[1:]) == ["P1 in", "P3 in", "P4 in"]
    assert door.keys == [0, 4, 5, 5]


async def test_the_others_do_not_move_until_the_unlocker_has_finished_loading():
    door = _Door([5, 5, 5, 5])
    teleports_when_landed: list[int] = []
    loads = {"left": 3}

    async def _unlocker_loading() -> bool:
        # Loading for a few reads after it reached the room, then landed.
        if door.clients[0].zone_name.return_value != "KeyRoom":
            return False
        if loads["left"]:
            loads["left"] -= 1
            teleports_when_landed.append(sum(door.teleports_to_trigger(i) for i in (1, 2, 3)))
            return True
        return False

    door.clients[0].is_loading.side_effect = _unlocker_loading

    assert await _run(door) == "SUCCESS"
    assert teleports_when_landed, "the unlocker was seen loading"
    assert set(teleports_when_landed) == {0}
    assert all(door.teleports_to_trigger(i) >= 1 for i in (1, 2, 3))


@pytest.mark.parametrize("players", [1, 2, 3])
async def test_fewer_than_four_players_all_get_there_with_one_key(players):
    door = _Door([5] * players)
    assert await _run(door) == "SUCCESS"
    assert _in_room(door)
    assert door.total_presses() == 1


async def test_fewer_than_four_players_go_together_rather_than_after_the_unlocker():
    door = _Door([5, 5], safety_delay=0.05)
    entered_before_unlocker_landed: list[bool] = []
    real_arrival = enter_zone.await_arrival

    async def _arrival(machine, group, numbers, to_zone):
        if len(group) == 1 and group[0] is door.clients[0]:
            # The follower's trigger attempt started alongside the unlocker's.
            await asyncio.sleep(0.05)
            entered_before_unlocker_landed.append(door.clients[1].teleport.called)
        return await real_arrival(machine, group, numbers, to_zone)

    with patch.object(enter_zone, "await_arrival", new=_arrival):
        assert await _run(door) == "SUCCESS"
    assert entered_before_unlocker_landed == [True]


async def test_the_unlocker_waits_safety_delay_before_it_is_moved():
    door = _Door([5], safety_delay=0.1)
    pressed = {}
    door_press = door.clients[0].send_key.side_effect

    async def _note_press(key, *args):
        if key == Keycode.X:
            pressed["at"] = asyncio.get_running_loop().time()
        await door_press(key, *args)

    moved = {}
    door_teleport = door.clients[0].teleport.side_effect

    async def _note_move(point):
        if tuple(point) == _TRIGGER and "at" not in moved:
            moved["at"] = asyncio.get_running_loop().time()
        await door_teleport(point)

    door.clients[0].send_key.side_effect = _note_press
    door.clients[0].teleport.side_effect = _note_move

    assert await _run(door) == "SUCCESS"
    assert moved["at"] - pressed["at"] >= 0.09


def _trigger_never_fires(door: _Door, index: int) -> None:
    async def _nothing(_point) -> None:
        return None

    door.clients[index].teleport.side_effect = _nothing


async def test_no_teleport_lands_between_the_cutoff_and_the_carry_in(monkeypatch):
    """A teleport crossing the carry-in lands after it, and puts them back out (#284)."""
    monkeypatch.setattr(door_node, "KEY_DOOR_TELEPORT_CUTOFF", 0.05)
    monkeypatch.setattr(door_node, "KEY_DOOR_ENTRY_TIMEOUT", 1.0)
    door = _Door([5])
    _trigger_never_fires(door, 0)
    counts: dict[str, int] = {}

    async def _carry() -> None:
        await asyncio.sleep(0.1)
        counts["after_cutoff"] = door.clients[0].teleport.call_count
        await asyncio.sleep(0.1)
        counts["at_carry"] = door.clients[0].teleport.call_count
        door.carry(0)

    carrying = asyncio.create_task(_carry())
    assert await _run(door) == "SUCCESS"
    await carrying
    assert counts["at_carry"] == counts["after_cutoff"]


async def test_a_safety_delay_past_the_cutoff_leaves_the_unlocker_to_the_game(monkeypatch):
    """A slow setting cannot walk a teleport into the carry-in: it skips the teleport."""
    monkeypatch.setattr(door_node, "KEY_DOOR_TELEPORT_CUTOFF", 0.02)
    monkeypatch.setattr(door_node, "KEY_DOOR_ENTRY_TIMEOUT", 1.0)
    door = _Door([5], safety_delay=0.5)

    async def _carry() -> None:
        await asyncio.sleep(0.1)
        door.carry(0)

    carrying = asyncio.create_task(_carry())
    assert await _run(door) == "SUCCESS"
    await carrying
    assert door.teleports_to_trigger(0) == 0


async def test_a_carry_in_that_never_comes_gets_a_normal_try_at_the_trigger(monkeypatch):
    """Past the entry timeout nothing can collide with it, for a door that does not carry."""
    monkeypatch.setattr(door_node, "KEY_DOOR_TELEPORT_CUTOFF", 0.0)
    monkeypatch.setattr(door_node, "KEY_DOOR_ENTRY_TIMEOUT", 0.05)
    door = _Door([5], safety_delay=0.0)
    assert await _run(door) == "SUCCESS"
    assert door.teleports_to_trigger(0) >= 1
    assert _in_room(door)


async def test_an_unlocker_who_never_arrives_fails_rather_than_reporting_no_key(monkeypatch):
    monkeypatch.setattr(door_node, "KEY_DOOR_TELEPORT_CUTOFF", 0.01)
    monkeypatch.setattr(door_node, "KEY_DOOR_ENTRY_TIMEOUT", 0.02)
    door = _Door([5])
    _trigger_never_fires(door, 0)
    with patch_helper("wait_for_zone_with_recovery", return_value=["TestZone"]):
        assert await _run(door) == "FAILURE"
    assert door.total_presses() == 1


async def test_recovery_can_port_a_follower_to_the_unlocker():
    """The arrival check covers the whole team, or a follower has nobody to be ported to."""
    door = _Door([5, 5, 5, 5])
    groups: list[list] = []
    real = _nh.wait_for_zone_with_recovery

    async def _seen(machine, clients, zone, *args, **kwargs):
        groups.append(list(clients))
        return await real(machine, clients, zone, *args, **kwargs)

    with patch.object(enter_zone.node_helpers, "wait_for_zone_with_recovery", new=_seen):
        assert await _run(door) == "SUCCESS"
    assert groups[-1] == door.clients


async def test_dialogue_is_clicked_through_on_the_players_waiting_and_never_the_unlocker():
    """The unlocker is about to load, and the run's ambient dialog watcher has it covered."""
    door = _Door([5, 5])
    seen: list[bool] = []

    async def _visible(_client, path) -> bool:
        # One page of dialogue the first time it is asked after the door opens.
        if door.locked or seen:
            return False
        seen.append(True)
        return True

    with patch_helper("is_visible_by_path", side_effect=_visible):
        result = await door_node.run(door.machine, door.clients, _PARAMS)
    assert result == "SUCCESS"
    assert any(call.args[0] == Keycode.SPACEBAR for call in door.clients[1].send_key.call_args_list)
    assert not any(call.args[0] == Keycode.SPACEBAR for call in door.clients[0].send_key.call_args_list)


# ---------------------------------------------------------------------------
# Where everyone starts
# ---------------------------------------------------------------------------


async def test_a_player_in_neither_zone_fails_before_moving_anyone():
    door = _Door([5, 5])
    door.clients[1].zone_name.return_value = "OtherZone"
    assert await _run(door) == "FAILURE"
    assert all(not c.teleport.called for c in door.clients)
    assert door.total_presses() == 0


async def test_a_zone_unreadable_at_first_is_waited_for_not_failed():
    """Just off a loading screen the zone reads None for a moment (#282)."""
    door = _Door([5])
    zone_unreadable_at_first(door.clients[0], _PARAMS["from_zone"])
    assert await _run(door) == "SUCCESS"


async def test_everyone_already_inside_is_success_with_nothing_done():
    door = _Door([5, 5])
    for index in range(2):
        door.carry(index)
    assert await _run(door) == "SUCCESS"
    assert door.total_presses() == 0
    assert all(not c.teleport.called for c in door.clients)


async def test_a_rerun_with_someone_inside_sends_only_the_rest():
    """A fight cut the node short after the door opened: no prompt is coming, and no key is spent."""
    door = _Door([5, 5, 5, 5], locked=False)
    door.carry(1)
    assert await _run(door) == "SUCCESS"
    assert _in_room(door)
    assert door.total_presses() == 0
    assert not door.clients[1].teleport.called


async def test_a_challenge_mode_copy_of_to_zone_counts_as_inside():
    """A rerun must not take a player in the copy for one in neither zone (#143)."""
    clients = [FakeClient(), FakeClient()]
    for client in clients:
        client.zone_name.return_value = "Aquila/Interiors/AQ_Z02_ThroneRoom_Hard"
    params = {**_PARAMS, "from_zone": "Aquila/AQ_Z02_Atlantea", "to_zone": "Aquila/Interiors/AQ_Z02_ThroneRoom"}

    assert await door_node.run(FakeMachine(), clients, params) == "SUCCESS"
    assert all(not c.teleport.called for c in clients)


def test_every_shipped_door_has_a_stop_or_a_route_for_no_key():
    """An unrouted NO_KEY does not fall through to recovery: the run would spin to the no-match limit."""
    doors = 0
    for path in Path("bots").rglob("*.json"):
        config = BotConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for state in config.states:
            if state.node_type != "open_skeleton_key_door":
                continue
            doors += 1
            routed = any(t.source == state.id and t.when == "NO_KEY" for t in config.transitions)
            assert routed or state.stop_reason("NO_KEY") is not None, f"{path} {state.id}"
    assert doors >= 2
