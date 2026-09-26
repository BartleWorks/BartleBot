"""What Record writes into the node panel beyond the params: the label, and the player list.

Record names a node after what it captured. A label it wrote before — this session or
another — is replaced when the node is recorded again; one the author typed never is.
"""

from __future__ import annotations

import pytest

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.gui.canvas import recording


@pytest.fixture
def interact_panel(qapp, monkeypatch):
    """A canvas showing one interact node, whose recording is whatever a test sets ``run`` to."""
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    canvases = []

    def _make(label: str = "", run=None):
        async def _fake_run(node_type, client, param_form, on_status, on_btn, try_set_label, *, player=None):
            await run(param_form, try_set_label, player)

        monkeypatch.setattr(recording, "run_recording", _fake_run)
        recording._active_task = None
        config = BotConfig(
            id="t",
            name="t",
            initial="a",
            recovery_state="a",
            states=[StateModel(id="a", node_type="interact", label=label, params={})],
            transitions=[],
        )
        canvas = BotCanvas()
        canvas.setup(config)
        canvas.enter_edit_mode()
        canvas.select_state("a")
        canvas.set_clients_getter(lambda: ["p1", "p2"])
        canvases.append(canvas)
        return canvas

    yield _make
    recording._active_task = None
    for canvas in canvases:
        canvas.prepare_for_shutdown()


async def _settle():
    import asyncio

    await asyncio.sleep(0)
    await asyncio.sleep(0)


def _labels_it(label: str):
    async def _run(_form, try_set_label, _player):
        try_set_label(label)

    return _run


@pytest.mark.parametrize("before", ["", "WC_Hub", "Interact with Someone Else", "(unknown zone)"])
async def test_a_blank_or_recorded_label_takes_the_new_recording(interact_panel, before):
    canvas = interact_panel(before, _labels_it("Interact with Cyrus Drake"))

    canvas.param_panel.start_record("p1")
    await _settle()

    assert canvas.config.states[0].label == "Interact with Cyrus Drake"


async def test_a_recorded_label_is_drawn_on_the_canvas_at_once(interact_panel):
    """The label field never has focus during a recording, so no focus-out draws it."""
    canvas = interact_panel("", _labels_it("Interact with Cyrus Drake"))

    canvas.param_panel.start_record("p1")
    await _settle()

    assert canvas.scene.node_items["a"].label == "Interact with Cyrus Drake"


async def test_a_label_the_author_typed_is_kept(interact_panel):
    canvas = interact_panel("Hand in the quest", _labels_it("Interact with Cyrus Drake"))

    canvas.param_panel.start_record("p1")
    await _settle()

    assert canvas.config.states[0].label == "Hand in the quest"
    assert canvas.scene.node_items["a"].label == "Hand in the quest"


async def test_the_player_list_is_written_and_read_through_the_form(interact_panel):
    async def _run(form, _try_set_label, player):
        assert form.players("interact_clients") == []
        form.set_players("interact_clients", [player])

    canvas = interact_panel("", _run)

    canvas.param_panel.start_record("p2")
    await _settle()

    assert canvas.param_panel.param_form.players("interact_clients") == [2]
    assert canvas.config.states[0].params["interact_clients"] == [2], "saved to the node, not just shown"


async def test_a_snapshot_puts_the_player_list_back(interact_panel):
    canvas = interact_panel("", _labels_it(""))
    form = canvas.param_panel.param_form
    form.set_players("interact_clients", [1, 2])
    saved = form.snapshot(["interact_clients"])

    form.set_players("interact_clients", [])
    form.restore(saved)

    assert form.players("interact_clients") == [1, 2]
