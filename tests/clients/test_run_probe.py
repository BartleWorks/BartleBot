"""Tests for the reads the run request makes before it starts a bot (#141, #203)."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bartlebot.clients import game_keys, run_probe
from bartlebot.game.key_bindings import PREFIX, Binding, KeyMap
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


def _keys(*changes: tuple[str, str]) -> KeyMap:
    """The defaults, with each named action's binding replaced."""
    replaced = {action for action, _ in changes}
    bindings = [b for b in KeyMap.defaults().bindings if b.action not in replaced]
    bindings += [Binding(action, PREFIX + key) for action, key in changes]
    return KeyMap(bindings, source="memory")


def test_the_defaults_have_every_key_a_run_needs():
    assert run_probe.key_problems([KeyMap.defaults(), KeyMap.defaults()]) == ()


def test_each_action_a_player_cannot_have_pressed_is_named_with_the_player():
    problems = run_probe.key_problems(
        [KeyMap.defaults(), _keys(("NPCInteract", "ctrl.x"), ("ToggleDeckConfig", "alt.p"))]
    )
    assert problems == (
        "Player 2: Interact is on Ctrl+X.",
        "Player 2: Deck is on Alt+P.",
    )


def test_a_key_the_chat_line_shares_is_a_problem():
    keys = _keys(("ToggleFurniture", "question"))
    keys = KeyMap([*keys.bindings, Binding("ToggleChatBoxInputSlash", PREFIX + "question")], source="memory")
    (problem,) = run_probe.key_problems([keys])
    assert problem == "Player 1: Housing is on /, which also opens the chat."


def test_a_key_another_action_shares_is_logged_not_refused(caplog):
    keys = KeyMap([*KeyMap.defaults().bindings, Binding("ToggleMapWindow", PREFIX + "x")], source="memory")
    assert run_probe.key_problems([keys]) == ()
    assert "Player 1: Interact's key, X, also does Toggle Map Window in the game" in caplog.text


async def test_the_probe_reads_every_player_s_keys_afresh(elixirs, monkeypatch):
    calls = []

    on_the_mouse = KeyMap(
        [*(b for b in KeyMap.defaults().bindings if b.action != "MoveLeft"), Binding("MoveLeft", "mouse.button3.down")],
        source="memory",
    )

    async def key_map(client, *, refresh=False, warn=True):
        calls.append(refresh)
        return on_the_mouse if len(calls) == 2 else KeyMap.defaults()

    monkeypatch.setattr(game_keys, "key_map", key_map)
    probe = await run_probe.probe_before_run([FakeClient(), FakeClient()], [])

    assert calls == [True, True]
    assert probe.key_problems == ("Player 2: Move Left is on the mouse.",)


def test_the_key_refusal_lists_each_problem_and_how_to_fix_it():
    text = run_probe.key_refusal_text(("Player 1: Interact is on Ctrl+X.",))
    assert "Player 1: Interact is on Ctrl+X." in text
    assert "Keybinding tab" in text
    assert "without Ctrl, Shift or Alt" in text
