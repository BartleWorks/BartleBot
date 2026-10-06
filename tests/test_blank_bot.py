"""A blank bot: no nodes at all, and what the editor makes of the first one.

The starter node a blank bot used to have was a template of one. Now the canvas is
empty, both roles are empty with it, the bot cannot be run, and the first node put on
it takes both roles however it got there.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPointF

from bartlebot.bot.schema import BotConfig
from bartlebot.gui.canvas.bot_canvas import NO_NODES_ERROR, BotMode
from bartlebot.new_bot import BotDetails, create_bot
from bartlebot.subgraph_store import SUBGRAPH_KEY_PREFIX, SubgraphLibrary
from bartlebot.subgraphs import member_id


def _blank() -> BotConfig:
    return create_bot(None, BotDetails(name="Scratch"))


@pytest.fixture
def canvas(qapp):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    canvas = BotCanvas()
    canvas.set_subgraph_library(SubgraphLibrary())
    canvas.setup(_blank(), mode=BotMode.EDIT)
    yield canvas
    canvas.prepare_for_shutdown()


def test_a_blank_bot_saves_and_loads_as_it_is(tmp_path):
    path = tmp_path / "scratch.json"
    _blank().save(path)
    before = path.read_bytes()

    loaded = BotConfig.load(path)
    loaded.save(path)

    assert loaded.states == [] and (loaded.initial, loaded.recovery_state) == ("", "")
    assert path.read_bytes() == before


def test_only_a_bot_with_no_nodes_may_leave_its_roles_empty():
    data = _blank().model_dump()

    with pytest.raises(ValueError, match="initial state"):
        BotConfig.model_validate({**data, "initial": "state_1", "recovery_state": "state_1"})
    with pytest.raises(ValueError, match="initial state"):
        BotConfig.model_validate(
            {**data, "states": [{"id": "state_1", "node_type": "sleep"}], "initial": "", "recovery_state": ""}
        )


def test_a_blank_bot_opens_on_an_empty_canvas_and_cannot_be_run(canvas):
    assert canvas.scene.node_items == {}
    assert canvas.validation_errors() == [NO_NODES_ERROR]


def test_the_first_node_takes_both_roles(canvas):
    new_id = canvas.scene.create_state_at("sleep", QPointF(0, 0))

    assert new_id is not None
    assert (canvas.config.initial, canvas.config.recovery_state) == (new_id, new_id)
    assert NO_NODES_ERROR not in canvas.validation_errors()


def test_a_second_node_leaves_the_roles_where_they_are(canvas):
    first = canvas.scene.create_state_at("sleep", QPointF(0, 0))
    canvas.scene.create_state_at("sleep", QPointF(440, 0))

    assert (canvas.config.initial, canvas.config.recovery_state) == (first, first)


def test_a_subgraph_node_put_down_first_takes_the_roles_on_an_entry_port(canvas):
    library = canvas.subgraph_library
    sync = next(d for sid in library.ids() if (d := library.get(sid)).name == "Sync players")

    region_id = canvas.scene.create_state_at(f"{SUBGRAPH_KEY_PREFIX}{sync.id}", QPointF(0, 0))

    region = next(r for r in canvas.config.regions if r.id == region_id)
    entries = {member_id(region.id, p.member) for p in region.source.ports.values() if p.member}
    assert canvas.config.initial in entries and canvas.config.recovery_state in entries


def test_deleting_the_last_node_leaves_a_blank_bot_that_still_loads(canvas, tmp_path):
    new_id = canvas.scene.create_state_at("sleep", QPointF(0, 0))

    assert canvas.scene.delete_state(new_id, confirm=False)

    assert (canvas.config.initial, canvas.config.recovery_state) == ("", "")
    path = tmp_path / "emptied.json"
    canvas.config.save(path)
    assert BotConfig.load(path).states == []


def test_a_new_node_is_named_what_the_node_browser_calls_its_type(canvas):
    """#156: a new node used to be unlabeled, so the canvas showed its ``state_N`` id."""
    new_id = canvas.scene.create_state_at("teleport_to_world_hub", QPointF(0, 0))

    state = next(s for s in canvas.config.states if s.id == new_id)
    assert state.label == "Teleport To World Hub"
    assert canvas.scene.node_items[new_id].label == "Teleport To World Hub"
