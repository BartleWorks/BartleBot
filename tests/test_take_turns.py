"""Unit tests for node_helpers.take_turns — the sequential shared-object driver."""

from unittest.mock import patch

from wizwalker import XYZ

import bartlebot.nodes.node_helpers as nh
from tests.conftest import FakeClient, FakeMachine

_INTERACT = XYZ(0.0, 0.0, 0.0)
_RESET = XYZ(1.0, 0.0, 0.0)


def _positional_client(prompts: bool = True) -> FakeClient:
    """Client whose interact prompt tracks where it has been teleported.

    The prompt shows at _INTERACT and not at _RESET, which is the contract the interact/
    reset coordinate pair has in every node that uses it. `prompts=False` stands in for a
    client that can never raise the prompt — bad coordinates, or a missing object.
    """
    client = FakeClient()
    state = {"at_interact": False}

    async def _teleport(xyz):
        state["at_interact"] = xyz.x == _INTERACT.x

    async def _in_range():
        return state["at_interact"] and prompts

    client.teleport.side_effect = _teleport
    client.is_in_npc_range.side_effect = _in_range
    client.at_interact = state
    return client


async def _run(clients, turn_fn, **kwargs):
    return await nh.take_turns(
        clients,
        _INTERACT,
        _RESET,
        safety_delay=0.01,
        turn_fn=turn_fn,
        poll_interval=0,
        object_use_delay=0,
        **kwargs,
    )


async def test_every_client_takes_a_turn():
    """Happy path: each client is prompted, takes its turn, and reports completed."""
    clients = [_positional_client() for _ in range(3)]
    turns: list[int] = []

    async def _turn(client) -> bool:
        turns.append(clients.index(client))
        return True

    results = await _run(clients, _turn)

    assert turns == [0, 1, 2]
    assert results == [nh.TurnResult(prompted=True, completed=True)] * 3


async def test_only_one_client_at_the_object_at_a_time():
    """The whole point: no other client is standing at interact_xyz during a turn.

    This is what a contended object needs — a client merely waiting in range is enough to
    swallow the next one's keypress, so it is not sufficient to serialize only the presses.
    """
    clients = [_positional_client() for _ in range(3)]
    overlaps: list[int] = []

    async def _turn(client) -> bool:
        others = [c for c in clients if c is not client]
        overlaps.extend(i for i, c in enumerate(others) if c.at_interact["at_interact"])
        return True

    await _run(clients, _turn)

    assert overlaps == []


async def test_client_is_parked_off_the_object_after_its_turn():
    """A finished client is teleported back to reset, not left standing in range."""
    client = _positional_client()

    await _run([client], lambda c: _record([], c))

    assert client.teleport.call_args_list[-1].args[0].x == _RESET.x
    assert client.at_interact["at_interact"] is False


async def test_failed_turn_is_still_parked():
    """A client whose turn failed is the likeliest to be loitering at the object."""
    client = _positional_client()

    async def _turn(_client) -> bool:
        return False

    results = await _run([client], _turn)

    assert results == [nh.TurnResult(prompted=True, completed=False)]
    assert client.teleport.call_args_list[-1].args[0].x == _RESET.x


async def test_park_after_turn_disabled_leaves_the_client_alone():
    """Zone-transition callers turn parking off — the client removes itself."""
    client = _positional_client()

    await _run([client], lambda c: _record([], c), park_after_turn=False)

    assert client.teleport.call_args_list[-1].args[0].x == _INTERACT.x


async def test_all_clients_parked_at_reset_before_the_first_turn():
    """Everyone is moved off the object up front, even without a stale prompt showing."""
    clients = [_positional_client() for _ in range(2)]

    async def _turn(client) -> bool:
        return True

    await _run(clients, _turn)

    # First teleport for every client is the unconditional park at reset_xyz.
    for client in clients:
        assert client.teleport.call_args_list[0].args[0].x == _RESET.x


async def test_client_that_never_prompts_is_recorded_and_skipped():
    """A client that cannot raise the prompt does not deny the object to the others."""
    good_before = _positional_client()
    never = _positional_client(prompts=False)
    good_after = _positional_client()
    clients = [good_before, never, good_after]
    turns: list[int] = []

    async def _turn(client) -> bool:
        turns.append(clients.index(client))
        return True

    results = await _run(clients, _turn)

    assert turns == [0, 2]  # the middle client's turn never ran
    assert results[1] == nh.TurnResult(prompted=False, completed=False)
    assert results[0].completed and results[2].completed
    # MAX_RETRIES attempts, each an interact teleport plus a reset teleport, after the park.
    assert never.teleport.call_count == 1 + nh.MAX_RETRIES * 2


async def test_prompt_retried_after_a_reset():
    """A prompt that fails once and appears on the next attempt still takes its turn."""
    client = _positional_client()
    attempts = {"n": 0}

    async def _in_range():
        # Swallow the first arrival at the object, the way a dropped prompt behaves.
        if client.at_interact["at_interact"]:
            attempts["n"] += 1
            return attempts["n"] > 1
        return False

    client.is_in_npc_range.side_effect = _in_range
    ran = []

    results = await _run([client], lambda c: _record(ran, c))

    assert results == [nh.TurnResult(prompted=True, completed=True)]
    assert ran == [client]


async def _record(sink: list, client) -> bool:
    sink.append(client)
    return True


async def test_unfinished_turn_is_distinguished_from_an_absent_prompt():
    """turn_fn returning falsey records prompted=True, completed=False.

    The two failures point at different fixes, so summarize_turns must be able to tell a
    client that never reached the object from one the object would not serve.
    """
    clients = [_positional_client(), _positional_client(), _positional_client(prompts=False)]

    async def _turn(client) -> bool:
        return client is clients[0]

    results = await _run(clients, _turn, numbers=[3, 5, 7])

    assert results == [
        nh.TurnResult(prompted=True, completed=True),
        nh.TurnResult(prompted=True, completed=False),
        nh.TurnResult(prompted=False, completed=False),
    ]
    assert nh.summarize_turns(results, [3, 5, 7]) == ([7], [5])


async def test_the_delay_between_uses_is_paid_between_turns_only():
    """The gap between clients is waited out once per handover, not before the first turn."""
    clients = [_positional_client() for _ in range(3)]
    sleeps: list[float] = []

    async def _sleep(seconds):
        sleeps.append(seconds)

    async def _turn(client) -> bool:
        return True

    with patch.object(nh.asyncio, "sleep", side_effect=_sleep):
        await nh.take_turns(
            clients, _INTERACT, _RESET, safety_delay=0.01, turn_fn=_turn, poll_interval=0, object_use_delay=2.5
        )

    assert sleeps.count(2.5) == 2  # three clients, two handovers


async def test_logs_use_the_supplied_client_numbers():
    """A caller passing a subset gets its own client numbers back in the log, not positions."""
    machine = FakeMachine()
    clients = [_positional_client(prompts=False)]

    async def _turn(client) -> bool:
        return True

    await _run(clients, _turn, numbers=[4], log=machine.log)

    assert any("Client 4" in message for message, _level in machine.logs)
    assert not any("Client 1" in message for message, _level in machine.logs)


async def test_a_client_whose_prompt_will_not_clear_at_reset_loses_its_turn():
    """A prompt still up at reset_xyz is some other object's, and an approach from there
    would press X on it (#152). That client is skipped; the rest still get their turns."""
    clients = [_positional_client() for _ in range(3)]
    clients[1].is_in_npc_range.side_effect = None
    clients[1].is_in_npc_range.return_value = True
    turns: list[int] = []
    logs: list[tuple[str, int]] = []

    async def _turn(client) -> bool:
        turns.append(clients.index(client))
        return True

    with patch.object(nh, "PROMPT_CLEAR_SETTLE", 0):
        results = await _run(clients, _turn, log=lambda msg, level=0: logs.append((msg, level)))

    assert turns == [0, 2]
    assert results[1] == nh.TurnResult(prompted=False, completed=False)
    clients[1].teleport.assert_called_once_with(_RESET)
    assert any("Client(s) [2]: interact prompt would not clear" in msg for msg, _ in logs)
