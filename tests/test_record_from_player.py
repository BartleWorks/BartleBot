"""Recording from any connected player (#85), and the player menu Fill and Record share.

Record used to take Player 1 whatever the author was doing. The button now asks, as
the Fill buttons do; Ctrl+R, with no menu to open, takes the preferred player (the
game window last in focus); and ``start_record`` takes the client outright, which is
what speed-authoring passes.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMenu, QWidget

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.gui.canvas import recording
from bartlebot.gui.widgets.player_picker import pick_player, picker_label
from bartlebot.gui.widgets.record_button import IDLE_TEXT, RecordButton

# ---------------------------------------------------------------------------
# pick_player
# ---------------------------------------------------------------------------


@pytest.fixture
def menu_titles(monkeypatch):
    """Stand in for ``QMenu.exec``: record the menu's entries and trigger the one asked for."""
    shown: list[list[str]] = []
    choose: list[int] = []

    def _exec(menu, *_args):
        actions = menu.actions()
        shown.append([a.text() for a in actions])
        if choose:
            actions[choose.pop(0)].trigger()

    monkeypatch.setattr(QMenu, "exec", _exec)
    return shown, choose


def test_no_player_does_nothing(qapp, menu_titles):
    picked = []

    pick_player(QWidget(), 0, "Record", picked.append)

    assert picked == []
    assert menu_titles[0] == []


def test_one_player_is_picked_without_asking(qapp, menu_titles):
    picked = []

    pick_player(QWidget(), 1, "Record", picked.append)

    assert picked == [0]
    assert menu_titles[0] == []


def test_several_players_are_offered_by_number(qapp, menu_titles):
    shown, choose = menu_titles
    choose.append(2)
    picked = []

    pick_player(QWidget(), 3, "Record", picked.append)

    assert shown == [["Record from Player 1", "Record from Player 2", "Record from Player 3"]]
    assert picked == [2]


@pytest.mark.parametrize(("players", "label"), [(0, "Fill"), (1, "Fill"), (2, "Fill ▾"), (4, "Fill ▾")])
def test_the_label_is_marked_only_when_a_click_asks(players, label):
    assert picker_label("Fill", players) == label


# ---------------------------------------------------------------------------
# RecordButton
# ---------------------------------------------------------------------------


def test_the_record_button_is_marked_with_several_players(qapp):
    button = RecordButton(get_clients=lambda: [object(), object()])

    assert button.text() == f"{IDLE_TEXT} ▾"
    assert button.idle


def test_stop_is_never_marked(qapp):
    """Clicking Stop stops; there is nothing to choose."""
    button = RecordButton(get_clients=lambda: [object(), object()])

    button.set_label("Stop")

    assert button.text() == "Stop"
    assert not button.idle


def test_the_mark_follows_players_connecting(qapp):
    clients = [object()]
    button = RecordButton(get_clients=lambda: clients)
    assert button.text() == IDLE_TEXT

    clients.append(object())
    button.refresh_players()

    assert button.text() == f"{IDLE_TEXT} ▾"


# ---------------------------------------------------------------------------
# The node panel
# ---------------------------------------------------------------------------


@pytest.fixture
def players() -> list[int | None]:
    """The 1-based player each recording the panel started was told it records from."""
    return []


@pytest.fixture
def panel(qapp, monkeypatch, players):
    """A canvas showing one Go To XYZ node, with run_recording captured instead of run."""
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    started: list[object] = []

    async def _fake_run(node_type, client, *_args, player=None):
        started.append(client)
        players.append(player)

    monkeypatch.setattr(recording, "run_recording", _fake_run)
    recording._active_task = None
    config = BotConfig(
        id="t",
        name="t",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="teleport_to_coordinates", params={})],
        transitions=[],
    )
    canvas = BotCanvas()
    canvas.setup(config)
    canvas.enter_edit_mode()
    canvas.select_state("a")
    clients = ["p1", "p2", "p3"]
    canvas.set_clients_getter(lambda: clients)
    yield canvas.param_panel, started, canvas
    recording._active_task = None
    canvas.prepare_for_shutdown()


async def _settle():
    import asyncio

    await asyncio.sleep(0)


async def test_start_record_uses_the_client_it_is_given(panel):
    p, started, _canvas = panel

    assert p.start_record("p3")
    await _settle()

    assert started == ["p3"]


async def test_start_record_names_the_player_it_records_from(panel, players):
    """Record fills who performed a step, so it is told which player the client is."""
    p, _started, _canvas = panel

    p.start_record("p3")
    await _settle()

    assert players == [3]


async def test_ctrl_r_records_from_the_preferred_player(panel):
    p, started, canvas = panel
    canvas.set_preferred_player_getter(lambda: 1)

    p.toggle_record()
    await _settle()

    assert started == ["p2"]


async def test_a_preferred_player_that_has_gone_falls_back_to_player_1(panel):
    p, started, canvas = panel
    canvas.set_preferred_player_getter(lambda: 7)

    p.toggle_record()
    await _settle()

    assert started == ["p1"]


async def test_start_record_refuses_while_a_recording_is_live(panel, monkeypatch):
    p, started, _canvas = panel
    monkeypatch.setattr(recording, "is_recording", lambda: True)

    assert not p.start_record("p1")
    await _settle()

    assert started == []


async def test_start_record_refuses_a_node_with_no_recorder(qapp, panel):
    p, started, canvas = panel
    canvas.scene.create_state_at("sleep", canvas.scene.find_empty_grid_cell(canvas.view.rect().center()))
    sleep_id = next(s.id for s in canvas.config.states if s.node_type == "sleep")
    canvas.select_state(sleep_id)

    assert not p.start_record("p1")
