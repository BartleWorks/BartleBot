"""Tests for the reads the run request makes before it starts a bot (#141, #203)."""

from unittest.mock import AsyncMock

import pytest

from bartlebot.clients import run_probe
from bartlebot.nodes.utility import handle_level_scaling
from tests.conftest import FakeClient


@pytest.fixture
def elixirs(monkeypatch) -> AsyncMock:
    read = AsyncMock(return_value=[2])
    monkeypatch.setattr(handle_level_scaling, "players_with_elixir", read)
    return read


async def test_players_in_a_fight_are_numbered_from_one(elixirs):
    clients = [FakeClient(), FakeClient(), FakeClient()]
    clients[0].in_battle.return_value = True
    clients[2].in_battle.return_value = True

    probe = await run_probe.probe_before_run(clients, [1, 2])

    assert probe.fighting == [1, 3]


async def test_a_fight_skips_the_elixir_read(elixirs):
    """The run is refused either way; the read would only cost time."""
    clients = [FakeClient()]
    clients[0].in_battle.return_value = True

    probe = await run_probe.probe_before_run(clients, [1])

    elixirs.assert_not_awaited()
    assert probe.elixir_players == ()


async def test_with_no_fight_the_elixirs_are_read(elixirs):
    probe = await run_probe.probe_before_run([FakeClient(), FakeClient()], [1, 2])

    assert probe == run_probe.RunProbe([], (2,))


async def test_unreadable_clients_and_a_failed_elixir_read_refuse_nothing(elixirs):
    clients = [FakeClient()]
    clients[0].in_battle.side_effect = RuntimeError("memory read failed")
    elixirs.side_effect = RuntimeError("memory read failed")

    assert await run_probe.probe_before_run(clients, [1]) == run_probe.RunProbe([], ())


@pytest.mark.parametrize(
    ("fighting", "opening"),
    [
        ([2], "Player 2 is in a fight. Finish or flee it,"),
        ([1, 3], "Players 1 and 3 are in a fight. Finish or flee them,"),
        ([1, 2, 4], "Players 1, 2 and 4 are in a fight. Finish or flee them,"),
    ],
)
def test_the_refusal_names_every_player_in_a_fight(fighting, opening):
    assert run_probe.refusal_text(fighting).startswith(opening)
