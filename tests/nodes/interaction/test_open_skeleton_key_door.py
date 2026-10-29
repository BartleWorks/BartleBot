"""Unit tests for the open_skeleton_key_door node."""

from unittest.mock import patch

import pytest
from wizwalker import Keycode

import bartlebot.nodes.interaction.open_skeleton_key_door as door_node
import bartlebot.nodes.node_helpers as _nh
from tests.conftest import FakeClient, FakeMachine, zone_unreadable_at_first
from tests.node_helper_patches import patch_helper, set_helper

_PARAMS = {
    "zone": "TestZone",
    "interact_xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
    "reset_xyz": {"x": 1.0, "y": 0.0, "z": 0.0},
}
_DOOR = (0.0, 0.0, 0.0)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    """Compress the cadences; the ceilings (Safety Delay, TRIGGER_TIMEOUT) stay real."""
    monkeypatch.setattr(_nh, "PROMPT_CLEAR_SETTLE", 0.0)
    set_helper(monkeypatch, "DIALOG_PAGE_SETTLE", 0.0)
    set_helper(monkeypatch, "DIALOG_FAST_POLL_INTERVAL", 0.0)
    set_helper(monkeypatch, "POLL_INTERVAL", 0.0)
    monkeypatch.setattr(door_node, "POLL_INTERVAL", 0.0)


class _Door:
    """A skeleton key door: its prompt shows at its spot, to a player with a key, while locked.

    A press from a player with a key unlocks it and costs that player one key. A press once it
    is unlocked, or from a player with none, does nothing, as in the game.
    """

    def __init__(self, keys: list[int], *, locked: bool = True):
        self.locked = locked
        self.keys = list(keys)
        self.clients = [FakeClient() for _ in keys]
        for index, client in enumerate(self.clients):
            client.is_in_npc_range.side_effect = self._showing(index)
            client.send_key.side_effect = self._pressed(index)

    def _showing(self, index: int):
        client = self.clients[index]

        async def _showing() -> bool:
            if not client.teleport.call_args_list:
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

    def presses(self, index: int) -> int:
        return sum(1 for call in self.clients[index].send_key.call_args_list if call.args[0] == Keycode.X)

    def total_presses(self) -> int:
        return sum(self.presses(i) for i in range(len(self.clients)))


async def _run(door: _Door, machine: FakeMachine | None = None) -> str:
    with patch_helper("is_visible_by_path", return_value=False):
        return await door_node.run(machine or FakeMachine(), door.clients, _PARAMS)


async def test_every_player_has_a_key_and_exactly_one_key_is_spent():
    door = _Door([10, 10, 10, 10])
    assert await _run(door) == "SUCCESS"
    assert not door.locked
    assert sum(10 - k for k in door.keys) == 1
    assert all(door.presses(i) >= 1 for i in range(4))


async def test_a_player_out_of_keys_is_skipped_and_the_door_still_opens():
    door = _Door([0, 5, 0, 5])
    machine = FakeMachine()
    assert await _run(door, machine) == "SUCCESS"
    assert not door.locked
    assert door.presses(0) == 0
    assert door.presses(2) == 0
    assert door.keys[0] == 0 and door.keys[2] == 0
    assert sum(5 - k for k in (door.keys[1], door.keys[3])) == 1


async def test_one_player_holding_the_only_key_opens_it():
    door = _Door([0, 0, 0, 1])
    assert await _run(door) == "SUCCESS"
    assert not door.locked
    assert door.keys == [0, 0, 0, 0]


async def test_a_player_without_a_key_does_not_cost_a_retry():
    """The wait for the prompt ends at the first player to have it, not when all do.

    Waiting for all would find the keyless player's prompt missing, reset, and approach
    again, MAX_RETRIES times over, before giving up on it.
    """
    door = _Door([0, 3])
    assert await _run(door) == "SUCCESS"
    approaches = [c for c in door.clients[1].teleport.call_args_list if tuple(c.args[0]) == _DOOR]
    assert len(approaches) == 1


async def test_no_player_has_a_key_fails_without_pressing_anything():
    door = _Door([0, 0, 0, 0])
    machine = FakeMachine()
    assert await _run(door, machine) == "FAILURE"
    assert door.total_presses() == 0
    assert any("skeleton key" in str(log) for log in machine.logs)


async def test_a_door_that_is_already_open_fails_without_pressing_anything():
    door = _Door([5, 5], locked=False)
    machine = FakeMachine()
    assert await _run(door, machine) == "FAILURE"
    assert door.total_presses() == 0


async def test_a_prompt_that_stays_after_x_fails():
    """Every press is swallowed, so the prompt never goes."""
    door = _Door([5, 5])
    for client in door.clients:
        client.send_key.side_effect = None
    machine = FakeMachine()
    with patch.object(door_node, "TRIGGER_TIMEOUT", 0.05):
        assert await _run(door, machine) == "FAILURE"
    assert door.locked


async def test_a_player_outside_the_zone_fails_before_moving_anyone():
    door = _Door([5, 5])
    door.clients[1].zone_name.return_value = "OtherZone"
    assert await _run(door) == "FAILURE"
    assert all(not c.teleport.called for c in door.clients)
    assert door.total_presses() == 0


async def test_a_zone_unreadable_at_first_is_waited_for_not_failed():
    """Just off a loading screen the zone reads None for a moment (#282)."""
    door = _Door([5])
    zone_unreadable_at_first(door.clients[0], _PARAMS["zone"])
    assert await _run(door) == "SUCCESS"


async def test_dialogue_the_door_opens_is_clicked_through():
    door = _Door([5])
    seen: list[bool] = []

    async def _visible(_client, path) -> bool:
        # One page of dialogue the first time it is asked after the door opens.
        if door.locked or seen:
            return False
        seen.append(True)
        return True

    with patch_helper("is_visible_by_path", side_effect=_visible):
        result = await door_node.run(FakeMachine(), door.clients, _PARAMS)
    assert result == "SUCCESS"
    assert any(call.args[0] == Keycode.SPACEBAR for call in door.clients[0].send_key.call_args_list)
