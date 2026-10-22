"""Tests for the reads the run request makes before it starts a bot (#141, #203)."""

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
