"""A region follows its members without anything listening to ``scene.changed``.

One listener on that signal switches every view on the scene onto Qt's compatibility
update path, and both lost repaints the #73 tracer caught were on a graph with a
region, which was the listener. So the scene tells the regions when a node moves.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPointF

from bartlebot.bot.schema import BotConfig, RegionModel, StateModel, TransitionModel
from bartlebot.gui.debug.repaint_trace import update_path


def _bot() -> BotConfig:
    ids = ["a", "b", "c", "d"]
    return BotConfig(
        id="bot",
        name="Regions",
        initial="a",
        recovery_state="a",
        states=[
            StateModel(id=sid, node_type="sleep", position={"x": 440.0 * i, "y": 0.0}, params={"duration": 1.0})
            for i, sid in enumerate(ids)
        ],
        transitions=[
            TransitionModel(trigger="advance", source=a, dest=b, when="SUCCESS") for a, b in zip(ids, ids[1:])
        ],
        regions=[RegionModel(id="r1", label="Middle", members=["b", "c"], color=2)],
    )


@pytest.fixture
def canvas(qapp):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    c = BotCanvas()
    c.setup(_bot())
    c.enter_edit_mode()
    yield c
    c.prepare_for_shutdown()


def test_nothing_listens_to_scene_changed(canvas):
    scene = canvas.scene
    assert scene.region_items, "the guard means nothing on a graph with no region"
    assert scene.receivers(scene.changed) == 0
    assert update_path(scene) == "direct"


def test_a_rebuilt_scene_does_not_listen_either(canvas):
    scene = canvas.scene
    assert scene.set_region_collapsed("r1", True)
    assert scene.set_region_collapsed("r1", False)
    assert scene.region_items
    assert scene.receivers(scene.changed) == 0


def test_dragging_a_member_refits_the_region_at_once(canvas):
    scene = canvas.scene
    region = scene.region_items["r1"]
    before = region.scene_bounds
    node = scene.node_items["c"]
    node.setPos(node.pos() + QPointF(0, 300))  # what a drag does, one mouse move at a time
    after = region.scene_bounds
    assert after.bottom() > before.bottom() + 250, "no event-loop turn needed: the move itself refits it"
    assert after.contains(node.body_scene_rect())


def test_a_node_outside_the_region_leaves_it_alone(canvas):
    scene = canvas.scene
    region = scene.region_items["r1"]
    before = region.scene_bounds
    node = scene.node_items["a"]
    node.setPos(node.pos() + QPointF(0, 300))
    assert region.scene_bounds == before
