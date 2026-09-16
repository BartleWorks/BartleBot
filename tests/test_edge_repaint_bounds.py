"""Regression tests for #73 — an edge drop leaving stale pixels on the canvas.

Dropping an edge left the drag preview on screen, left a snap highlight uncleared,
and drew the new edge only where the node boxes happened to repaint over it.  The
lost updates were all the ones issued *before* the scene rect was corrected;
everything after it landed.

The cause is ordering.  Every caller mutates the scene first — drop the drag
preview, clear the snap highlight, reroute paths, move a node — and only then
corrects the scene rect.  Changing the rect moves the scene->viewport mapping for
the whole view, so the updates already queued against the old mapping land on the
wrong pixels while the ones that needed repainting stay stale.  The view runs on
Qt's default ``MinimalViewportUpdate``, which invalidates precisely those rects
and nothing more, so nothing else covers for them.

Confirmed live: adding a node far past the existing content pinned the content
bounds, which made the rect stop changing, and the artifacts stopped with it.

``set_scene_rect_and_repaint`` is the single place that now owns this, for both
the scene-wide recompute and the cheap union on a node drag.  Nothing here reads
pixels; these test the invariants the repaint path relies on.
"""

from PyQt6.QtCore import QPointF, QRectF

from bartlebot.bot.schema import BotConfig, StateModel, TransitionModel


def _config():
    return BotConfig(
        id="test-bot",
        name="t",
        initial="a",
        recovery_state="a",
        states=[StateModel(id=sid, node_type="change_to_next_realm", params={}) for sid in ("a", "b", "c")],
        transitions=[
            TransitionModel(trigger="advance", source="a", dest="b", when="SUCCESS"),
            TransitionModel(trigger="advance", source="b", dest="c", when="SUCCESS"),
            # A second edge arriving at "c" — dropping another one on that port
            # re-fans the arrival positions and moves every incoming edge at once.
            TransitionModel(trigger="advance", source="a", dest="c", when="FAILURE"),
        ],
    )


def _canvas(config):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    canvas = BotCanvas()
    canvas.setup(config)
    canvas.enter_edit_mode()
    return canvas


def _watch_viewports(scene) -> list:
    """Record every ``viewport().update()`` the scene's views receive."""
    seen: list = []
    for view in scene.views():
        vp = view.viewport()
        vp.update = lambda *a, _vp=vp, **k: seen.append(_vp)
    return seen


def test_scene_rect_update_is_a_noop_when_the_bounds_are_unchanged(qapp):
    """The common case must not touch the scene rect at all.

    Every ``_create_transition`` calls this.  Rewriting the rect to the value it
    already holds is what used to strand the edit's own updates, so an unchanged
    rect has to return before ``setSceneRect``.
    """
    canvas = _canvas(_config())
    scene = canvas.scene
    scene._update_scene_rect()  # settle

    calls = []
    original = scene.setSceneRect
    scene.setSceneRect = lambda *a, **k: (calls.append(a), original(*a, **k))[1]
    try:
        scene._update_scene_rect()
    finally:
        del scene.setSceneRect

    assert calls == [], "scene rect was rewritten despite unchanged bounds"


def test_changing_the_scene_rect_repaints_every_viewport(qapp):
    """When the mapping does move, every view has to be repainted.

    The updates queued before this point were recorded against the old mapping, so
    they land on the wrong pixels; repainting is what makes the new mapping whole
    again.  Both views count — the canvas and the minimap share the scene.
    """
    canvas = _canvas(_config())
    scene = canvas.scene

    repainted = _watch_viewports(scene)
    scene.setSceneRect(QRectF(0.0, 0.0, 10.0, 10.0))  # force the next call to differ
    repainted.clear()
    scene._update_scene_rect()

    assert repainted, "scene rect changed but no viewport was repainted"
    assert len(repainted) == len(scene.views())


def test_a_node_dragged_past_the_edge_repaints_the_viewport(qapp):
    """The node-drag path grows the rect too, and strands updates the same way.

    ``NodeItem.itemChange`` reroutes every attached edge and *then* grows the rect,
    so it carries the same ordering hazard and goes through the same helper.
    """
    canvas = _canvas(_config())
    scene = canvas.scene
    node = scene.node_items["c"]

    repainted = _watch_viewports(scene)
    node.setPos(node.pos() + QPointF(9000.0, 7000.0))

    assert repainted, "node moved past the scene rect but no viewport was repainted"
