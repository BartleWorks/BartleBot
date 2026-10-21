"""Tests for the reads the run request makes before it starts a bot (#141, #203)."""

from pathlib import Path
from types import SimpleNamespace
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


# ---------------------------------------------------------------------------
# Dblclick Go To Friend, read from each client's own install
# ---------------------------------------------------------------------------


@pytest.fixture
def installs(monkeypatch):
    """Each client's install by its window handle, and every config.xml read, by install."""
    state = SimpleNamespace(by_handle={}, on=set(), reads=[])

    def read(install):
        state.reads.append(install)
        return install in state.on

    monkeypatch.setattr(run_probe, "client_install", lambda c: state.by_handle.get(c.window_handle))
    monkeypatch.setattr(run_probe, "quick_friend_teleport_on", read)
    return state


def test_the_players_whose_install_has_it_on_are_numbered_from_one(installs):
    clients = [FakeClient(), FakeClient(), FakeClient()]
    live, test = Path("C:/live"), Path("C:/test")
    installs.by_handle = {clients[0].window_handle: live, clients[1].window_handle: test}
    installs.by_handle[clients[2].window_handle] = test
    installs.on = {test}

    assert run_probe.quick_teleport_players(clients) == (2, 3)


def test_clients_sharing_an_install_read_its_config_once(installs):
    clients = [FakeClient(), FakeClient()]
    installs.by_handle = {c.window_handle: Path("C:/live") for c in clients}

    run_probe.quick_teleport_players(clients)

    assert installs.reads == [Path("C:/live")]


def test_a_client_whose_install_cannot_be_found_is_not_counted(installs):
    assert run_probe.quick_teleport_players([FakeClient()]) == ()
    assert installs.reads == []


def test_a_client_with_no_process_has_no_install():
    """FakeClient has no process_id, which is a read that fails like any other."""
    assert run_probe.client_install(FakeClient()) is None


def test_a_client_is_traced_to_the_folder_above_its_bin(monkeypatch):
    exe = Path("C:/Games/Wizard101 Test/Bin/WizardGraphicalClient.exe")
    monkeypatch.setattr(run_probe, "process_executable", lambda pid: exe if pid == 42 else None)

    assert run_probe.client_install(SimpleNamespace(process_id=42)) == Path("C:/Games/Wizard101 Test")


async def test_a_run_that_uses_the_friends_list_reads_the_setting(installs, elixirs):
    clients = [FakeClient(), FakeClient()]
    installs.by_handle = {c.window_handle: Path("C:/live") for c in clients}
    installs.on = {Path("C:/live")}

    probe = await run_probe.probe_before_run(clients, [], uses_friends_list=True)

    assert probe.quick_teleport_players == (1, 2)


async def test_a_run_that_does_not_use_the_friends_list_reads_nothing(installs, elixirs):
    clients = [FakeClient()]
    installs.by_handle = {clients[0].window_handle: Path("C:/live")}
    installs.on = {Path("C:/live")}

    probe = await run_probe.probe_before_run(clients, [])

    assert probe.quick_teleport_players == ()
    assert installs.reads == []


async def test_a_fight_skips_the_setting_read(installs, elixirs):
    clients = [FakeClient()]
    clients[0].in_battle.return_value = True
    installs.by_handle = {clients[0].window_handle: Path("C:/live")}

    await run_probe.probe_before_run(clients, [], uses_friends_list=True)

    assert installs.reads == []


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
