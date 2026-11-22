"""Recording from any connected player (#85), and the player menu Fill and Record share.

Record used to take Player 1 whatever the author was doing. The button now asks, as
the Fill buttons do; Ctrl+R, with no menu to open, takes the preferred player (the
game window last in focus); and ``start_record`` takes the client outright, which is
what speed-authoring passes.
"""

from __future__ import annotations

import pytest

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.gui.canvas import recording
from bartlebot.gui.widgets.buttons.record_button import IDLE_TEXT, RecordButton

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

    async def _fake_run(node_type, client, *_args, player=None, safety_delay=None):
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
