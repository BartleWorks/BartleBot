"""Unit tests for the enter_zone_via_ride node."""

from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode

import bartlebot.nodes.zone_entry.enter_zone_via_ride as ride
from tests.conftest import FakeClient, FakeMachine

_FROM, _RIDE, _TO = "World/Dock", "World/Flight", "World/Island"
_BOARD = {"x": 0.0, "y": 0.0, "z": 0.0}
_RESET = {"x": 1.0, "y": 0.0, "z": 0.0}
_NEVER = 10**9


class _Rider(FakeClient):
    """A player the ride moves.

    On the ground it is teleported about; at Board XYZ the prompt shows (or, boarded by walking,
    the gate loads the ride zone). X on the prompt boards it, and X on the ride's skip prompt
    sends it to _TO. Every zone change is two loading-screen reads, during which the old zone
    is still reported.
    """

    def __init__(
        self,
        *,
        board="object",
        ride_zone=_RIDE,
        zone=_FROM,
        stage="ground",
        skip_prompt_after=0,
        ride_ends_after=None,
        gate_misses=0,
    ):
        super().__init__()
        self.board, self.ride_zone, self.where, self.stage = board, ride_zone, zone, stage
        self.skip_prompt_after = skip_prompt_after  # prompt reads on the ride before the skip prompt shows
        self.ride_ends_after = ride_ends_after  # loading reads on the ride before it ends on its own
        self.gate_misses = gate_misses  # teleports into the gate that do not fire it
        self.at = None
        self.boat_moves = True  # an in-zone ride carries the player off the dock once boarded
        self.loads_left, self.pending = 0, None
        self.body.position = AsyncMock(side_effect=lambda: XYZ(self.at or 0.0, 0.0, 0.0))
        self.zone_name = AsyncMock(side_effect=lambda: self.where)
        self.teleport = AsyncMock(side_effect=self._teleport)
        self.is_in_npc_range = AsyncMock(side_effect=self._prompt)
        self.send_key = AsyncMock(side_effect=self._key)
        self.is_loading = AsyncMock(side_effect=self._loading)

    def _load(self, zone: str) -> None:
        self.loads_left, self.pending = 2, zone

    async def _teleport(self, xyz) -> None:
        assert self.stage == "ground", "a player on the ride was teleported"
        self.at = xyz.x
        if self.board == "walk" and xyz.x == _BOARD["x"]:
            if self.gate_misses:
                self.gate_misses -= 1
                return
            self.stage = "riding"
            self._load(self.ride_zone)

    async def _prompt(self) -> bool:
        if self.loads_left:
            return False
        if self.stage == "ground":
            return self.board == "object" and self.at == _BOARD["x"]
        if self.stage == "riding":
            if self.skip_prompt_after > 0:
                self.skip_prompt_after -= 1
                return False
            return True
        return False

    async def _key(self, key, delay) -> None:
        if key != Keycode.X or not await self._prompt():
            return
        if self.stage == "ground":
            if not self.ride_zone and not self.boat_moves:
                return
            self.stage = "riding"
            if self.ride_zone:
                self._load(self.ride_zone)
            else:
                self.at = 5000.0  # the boat pulls away from the dock
        else:
            self.stage = "gone"
            self._load(_TO)

    async def _loading(self) -> bool:
        if self.loads_left:
            self.loads_left -= 1
            if not self.loads_left:
                self.where = self.pending
            return True
        if self.stage == "riding" and self.ride_ends_after is not None:
            self.ride_ends_after -= 1
            if self.ride_ends_after <= 0:
                self.stage = "gone"
                self._load(_TO)
        return False

    @property
    def presses(self) -> int:
        return sum(1 for call in self.send_key.call_args_list if call.args[0] == Keycode.X)


def _params(**extra) -> dict:
    return {"from_zone": _FROM, "to_zone": _TO, "board_xyz": _BOARD, "reset_xyz": _RESET, "ride_zone": _RIDE, **extra}


async def _run(clients, params) -> str:
    with patch.object(ride, "POLL_INTERVAL", 0):
        return await ride.run(FakeMachine(), clients, params)


async def test_the_dragon_is_boarded_by_interacting_then_skipped():
    riders = [_Rider(), _Rider()]

    assert await _run(riders, _params()) == "SUCCESS"

    for rider in riders:
        assert rider.where == _TO
        assert rider.presses == 2  # one to board, one to skip
        assert [call.args[0].x for call in rider.teleport.call_args_list] == [_BOARD["x"]]


async def test_an_airship_is_boarded_through_its_gate():
    rider = _Rider(board="walk")

    assert await _run([rider], _params(board="walk")) == "SUCCESS"

    assert rider.where == _TO
    assert rider.presses == 1  # only the skip


async def test_a_gate_that_does_not_fire_is_retried_from_the_reset():
    rider = _Rider(board="walk", gate_misses=1)

    assert await _run([rider], _params(board="walk")) == "SUCCESS"

    assert [call.args[0].x for call in rider.teleport.call_args_list] == [_BOARD["x"], _RESET["x"], _BOARD["x"]]


async def test_a_boat_rides_inside_its_zone_and_the_late_skip_prompt_is_pressed_once():
    """Nothing loads on boarding; X must not land again until the skip prompt comes back up."""
    rider = _Rider(ride_zone="", skip_prompt_after=5)

    assert await _run([rider], _params(ride_zone="")) == "SUCCESS"

    assert rider.where == _TO
    assert rider.presses == 2


async def test_an_x_that_leaves_the_player_on_the_dock_is_not_boarding():
    """No loading screen and no movement: the press did nothing, so it is retried from the reset."""
    rider = _Rider(ride_zone="")
    rider.boat_moves = False

    assert await _run([rider], _params(ride_zone="")) == "FAILURE"

    assert [call.args[0].x for call in rider.teleport.call_args_list] == [
        _BOARD["x"],
        *[_RESET["x"], _BOARD["x"]] * (ride.MAX_RETRIES - 1),
    ]


async def test_a_player_already_on_the_ride_just_skips_it():
    rider = _Rider(zone=_RIDE, stage="riding")

    assert await _run([rider], _params()) == "SUCCESS"

    rider.teleport.assert_not_called()
    assert rider.presses == 1


async def test_a_ride_nobody_skips_still_arrives_when_it_ends():
    rider = _Rider(skip_prompt_after=_NEVER, ride_ends_after=20)

    assert await _run([rider], _params()) == "SUCCESS"

    assert rider.where == _TO
    assert rider.presses == 1  # boarding only


async def test_no_prompt_at_the_boarding_point_fails_after_its_retries():
    rider = _Rider()
    rider.is_in_npc_range = AsyncMock(return_value=False)

    assert await _run([rider], _params()) == "FAILURE"

    rider.send_key.assert_not_called()
    # Board, then reset and board again for each retry.
    assert rider.teleport.call_count == 1 + 2 * (ride.MAX_RETRIES - 1)


async def test_a_player_that_cannot_board_is_left_to_the_arrival_check():
    boarded, stuck = _Rider(), _Rider()
    stuck.is_in_npc_range = AsyncMock(return_value=False)

    with patch(
        "bartlebot.nodes.node_helpers.wait_for_zone_with_recovery", new=AsyncMock(return_value=None)
    ) as recovery:
        assert await _run([boarded, stuck], _params()) == "SUCCESS"

    recovery.assert_awaited_once()
    assert boarded.where == _TO


async def test_a_player_somewhere_else_fails_before_moving():
    rider = _Rider(zone="World/Elsewhere")

    assert await _run([rider], _params()) == "FAILURE"

    rider.teleport.assert_not_called()


async def test_a_gate_ride_without_its_ride_zone_is_refused():
    rider = _Rider(board="walk")

    assert await _run([rider], _params(board="walk", ride_zone="")) == "FAILURE"

    rider.teleport.assert_not_called()
