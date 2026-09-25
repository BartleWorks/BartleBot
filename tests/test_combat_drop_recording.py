"""Tests for post-fight drop recording.

Drops are announced by the server after the duel has already ended locally, and
end-of-fight is exactly when a long-running session lags hardest. Two behaviours keep
that from losing drops, and both are tested here:

  * the post-fight read *polls* instead of looking once, so an announcement that lands
    a few seconds late is still caught by the fight that earned it;
  * the chat baseline is *carried* between fights, so one that beats even the poll
    window is recorded by the next fight rather than absorbed into a fresh snapshot.

The second is what makes the first non-critical: no timeout value can be long enough
for arbitrary lag, but a carried baseline is correct at any lag.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from bartlebot.nodes import combat_runner
from tests.conftest import FakeClient, FakeMachine

_SNAP = "bartlebot.utils.drop_detection.snapshot_chat"

# A chat line drop_detection.extract_new_drops recognises as a Reagent drop.
_DROP = "Art_Chat_System.dds> <>Iron Bloom<;Reagent>"
_DROP2 = "Art_Chat_System.dds> <>Sandstone<;Reagent>"


@pytest.fixture
def fast(monkeypatch):
    """Compress the poll cadence, leaving the ceiling wide.

    Only the interval is compressed. The timeout is what these tests race against, so
    it stays an order of magnitude above what the polls nominally cost — undershooting
    it would send the code down the give-up path while the test still named the
    late-arrival one.
    """
    monkeypatch.setattr(combat_runner, "COMBAT_DROP_MESSAGE_POLL", 0.01)
    monkeypatch.setattr(combat_runner, "COMBAT_DROP_MESSAGE_TIMEOUT", 2.0)


async def test_late_announcement_is_still_caught(fast):
    """The drop line is absent for several polls, then appears — what the fix is for."""
    machine, client = FakeMachine(), FakeClient()
    machine.set_chat_baseline(client, "")

    with patch(_SNAP, new=AsyncMock(side_effect=["", "", "", _DROP])):
        found = await combat_runner.poll_combat_drops(machine, [client], [0])

    assert found == ["Iron Bloom"]
    assert machine.drops == [("Iron Bloom", 1, 0)]


async def test_announcement_slower_than_the_window_is_recorded_by_the_next_fight(fast):
    """The core of the drop-recording fix: a miss must be late, never lost.

    Fight one times out with nothing in chat. Fight two takes no fresh snapshot, so
    the message that arrived in between is still ahead of the carried baseline.
    """
    machine, client = FakeMachine(), FakeClient()

    with patch(_SNAP, new=AsyncMock(return_value="")):
        await combat_runner.seed_chat_baselines(machine, [client])
        assert await combat_runner.poll_combat_drops(machine, [client], [0]) == []

    # The announcement lands during the gap between the two fights.
    with patch(_SNAP, new=AsyncMock(return_value=_DROP)):
        await combat_runner.seed_chat_baselines(machine, [client])  # must not re-seed
        found = await combat_runner.poll_combat_drops(machine, [client], [0])

    assert found == ["Iron Bloom"]
    assert machine.drops == [("Iron Bloom", 1, 0)]


async def test_a_second_item_from_one_fight_survives_to_the_next_poll(fast):
    """poll_new_drops returns on the first new line; the rest must not be swallowed."""
    machine, client = FakeMachine(), FakeClient()
    machine.set_chat_baseline(client, "")

    with patch(_SNAP, new=AsyncMock(return_value=_DROP)):
        assert await combat_runner.poll_combat_drops(machine, [client], [0]) == ["Iron Bloom"]

    with patch(_SNAP, new=AsyncMock(return_value=f"{_DROP}\n{_DROP2}")):
        assert await combat_runner.poll_combat_drops(machine, [client], [0]) == ["Sandstone"]

    assert machine.drops == [("Iron Bloom", 1, 0), ("Sandstone", 1, 0)]


async def test_seeding_leaves_an_existing_baseline_alone(fast):
    """Re-seeding is the bug, not the fix — it absorbs whatever arrived in the gap."""
    machine, client = FakeMachine(), FakeClient()
    machine.set_chat_baseline(client, "old")

    with patch(_SNAP, new=AsyncMock(return_value="something else entirely")) as snap:
        await combat_runner.seed_chat_baselines(machine, [client])

    snap.assert_not_called()
    assert machine.chat_baseline(client) == "old"


async def test_an_unseeded_client_does_not_report_pre_existing_chat(fast):
    """A baseline of None must not be treated as an empty one — that reports the whole log."""
    machine, client = FakeMachine(), FakeClient()
    assert machine.chat_baseline(client) is None

    with patch(_SNAP, new=AsyncMock(return_value=f"{_DROP}\n{_DROP2}")):
        found = await combat_runner.poll_combat_drops(machine, [client], [0])

    assert found == []
    assert machine.drops == []
    # …but it is seeded now, so the next fight's drops are detected normally.
    assert machine.chat_baseline(client) == f"{_DROP}\n{_DROP2}"


async def test_seeding_an_unreadable_chat_log_stores_an_empty_baseline(fast):
    """Staying unseeded would make the *next* seed absorb this fight's drops."""
    machine, client = FakeMachine(), FakeClient()

    with patch(_SNAP, new=AsyncMock(side_effect=RuntimeError("read failed"))):
        await combat_runner.seed_chat_baselines(machine, [client])

    assert machine.chat_baseline(client) == ""


async def test_drops_are_attributed_to_the_slot_not_the_position(fast):
    """The ambient watcher polls a subset; slot 2's drop must not land on player 1."""
    machine = FakeMachine()
    battling = [FakeClient(), FakeClient()]
    for c in battling:
        machine.set_chat_baseline(c, "")

    with patch(_SNAP, new=AsyncMock(return_value=_DROP)):
        await combat_runner.poll_combat_drops(machine, battling, [2, 3])

    assert sorted(idx for _item, _qty, idx in machine.drops) == [2, 3]


async def test_clients_are_polled_concurrently(fast):
    """Serially, a group of four would pay four full timeouts on a dropless fight."""
    machine = FakeMachine()
    clients = [FakeClient() for _ in range(4)]
    for c in clients:
        machine.set_chat_baseline(c, "")

    started = 0

    async def _slow_snap(_client):
        nonlocal started
        started += 1
        await asyncio.sleep(0.05)
        return ""

    with patch(_SNAP, new=_slow_snap):
        task = asyncio.create_task(combat_runner.poll_combat_drops(machine, clients, [0, 1, 2, 3]))
        await asyncio.sleep(0.02)  # inside the first read, before any could finish
        assert started == 4, "reads did not overlap — the clients are being polled in series"
        await task


async def test_one_clients_failure_does_not_lose_the_others_drops(fast):
    """A memory read that fails must not take down a fight that already succeeded."""
    machine = FakeMachine()
    bad, good = FakeClient(), FakeClient()
    machine.set_chat_baseline(bad, "")
    machine.set_chat_baseline(good, "")

    async def _snap(client):
        if client is bad:
            raise RuntimeError("read failed")
        return _DROP

    with patch(_SNAP, new=_snap):
        found = await combat_runner.poll_combat_drops(machine, [bad, good], [0, 1])

    assert found == ["Iron Bloom"]
    assert machine.drops == [("Iron Bloom", 1, 1)]


async def test_set_clients_prunes_departed_baselines_and_keeps_survivors():
    """Baselines are keyed by client, so a swap or a reconnect must not shuffle them."""
    from bartlebot.bot.machine import BotMachine

    staying, leaving = FakeClient(), FakeClient()
    machine = BotMachine(
        clients=[staying, leaving],
        config={"states": [], "transitions": [], "initial": ""},
        node_registry={},
    )
    machine.set_chat_baseline(staying, "kept")
    machine.set_chat_baseline(leaving, "dropped")

    machine.set_clients([staying])

    assert machine.chat_baseline(staying) == "kept"
    assert machine.chat_baseline(leaving) is None


async def test_a_new_fights_poll_replaces_the_last_without_reporting_a_drop_twice(fast):
    """The last fight's poll is cancelled, and the new one still sees the line it missed.

    Both polls running would diff the same baseline and each report the drop.
    """
    machine, client = FakeMachine(), FakeClient()
    machine.set_chat_baseline(client, "")
    chat = ""

    async def _snap(_client):
        return chat

    with patch(_SNAP, new=_snap):
        combat_runner.start_combat_drop_poll(machine, [client], [0])
        first = machine.drop_poll
        await asyncio.sleep(0.03)  # the first poll is mid-window, having seen nothing

        chat = _DROP
        combat_runner.start_combat_drop_poll(machine, [client], [0])
        await machine.wait_for_drop_poll()

    assert first.cancelled()
    assert machine.drops == [("Iron Bloom", 1, 0)]


async def test_the_machine_cancels_the_previous_drop_poll_and_waits_without_cancelling():
    from bartlebot.bot.machine import BotMachine

    machine = BotMachine(clients=[], config={"states": [], "transitions": [], "initial": ""}, node_registry={})
    release = asyncio.Event()
    old = asyncio.create_task(asyncio.Event().wait())
    new = asyncio.create_task(release.wait())

    machine.start_drop_poll(old)
    machine.start_drop_poll(new)
    await asyncio.sleep(0)
    assert old.cancelled()

    # A waiter that gives up must leave the poll running for the next one.
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(machine.wait_for_drop_poll(), timeout=0.01)
    assert not new.done()

    release.set()
    await asyncio.wait_for(machine.wait_for_drop_poll(), timeout=1.0)
    assert new.done()
