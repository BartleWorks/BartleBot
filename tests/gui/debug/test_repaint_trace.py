"""The #73 repaint tracer: item changes Qt never repainted are found, repainted ones aren't.

The ledger is pure bookkeeping, so most of this drives it with plain rects and a
made-up clock. The last tests put a tracer on a real view and give it one change
Qt repaints properly and one it can't: an item whose bounds change without
``prepareGeometryChange``, which leaves exactly the stale pixels #73 is about.
"""

import logging
import time

from PyQt6.QtCore import QRect, QRectF
from PyQt6.QtGui import QPainterPath, QRegion
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QGraphicsScene, QGraphicsView

from bartlebot.gui.debug.repaint_trace import RepaintLedger, RepaintTracer, describe_item, update_path

VISIBLE = QRect(0, 0, 800, 600)
NAMES = {1: "EdgeItem(a -> b)", 2: "NodeItem(c)"}


def _moved(ledger, old: QRect, new: QRect, now: float = 1.0) -> None:
    ledger.observe(0.0, {1: old}, NAMES, VISIBLE)
    ledger.observe(now, {1: new}, NAMES, VISIBLE)


def test_the_first_observation_is_a_baseline():
    ledger = RepaintLedger(deadline=0.5)
    ledger.observe(0.0, {1: QRect(10, 10, 50, 20)}, NAMES, VISIBLE)
    assert ledger.owed_count == 0


def test_a_move_painted_over_both_ends_owes_nothing():
    ledger = RepaintLedger(deadline=0.5)
    _moved(ledger, QRect(10, 10, 50, 20), QRect(10, 100, 50, 20))
    ledger.painted(QRegion(QRect(0, 0, 100, 200)))
    assert ledger.expired(99.0) == []


def test_old_pixels_never_erased_are_reported_once_past_the_deadline():
    ledger = RepaintLedger(deadline=0.5)
    _moved(ledger, QRect(10, 10, 50, 20), QRect(10, 100, 50, 20), now=1.0)
    ledger.painted(QRegion(QRect(10, 100, 50, 20)) + QRegion(QRect(10, 10, 20, 20)))

    assert ledger.expired(1.4) == [], "not stale until the deadline has passed"
    (stale,) = ledger.expired(1.6)
    assert stale.kind == "was"
    assert stale.what == "EdgeItem(a -> b)"
    assert stale.missing.boundingRect() == QRect(30, 10, 30, 20)
    assert ledger.expired(9.0) == [], "a finding is reported once"


def test_new_pixels_never_drawn_are_reported_too():
    ledger = RepaintLedger(deadline=0.5)
    _moved(ledger, QRect(10, 10, 50, 20), QRect(10, 100, 50, 20))
    ledger.painted(QRegion(QRect(10, 10, 50, 20)))
    (stale,) = ledger.expired(9.0)
    assert stale.kind == "now"


def test_removal_and_addition_each_owe_their_area():
    ledger = RepaintLedger(deadline=0.5)
    ledger.observe(0.0, {1: QRect(10, 10, 50, 20)}, NAMES, VISIBLE)
    ledger.observe(1.0, {2: QRect(200, 10, 50, 20)}, NAMES, VISIBLE)
    found = {(s.what, s.kind) for s in ledger.expired(9.0)}
    assert found == {("EdgeItem(a -> b)", "was"), ("NodeItem(c)", "now")}


def test_changes_off_screen_owe_nothing():
    ledger = RepaintLedger(deadline=0.5)
    _moved(ledger, QRect(2000, 10, 50, 20), QRect(2000, 100, 50, 20))
    assert ledger.owed_count == 0


def test_reset_forgets_what_was_owed_and_re_baselines():
    ledger = RepaintLedger(deadline=0.5)
    _moved(ledger, QRect(10, 10, 50, 20), QRect(10, 100, 50, 20))
    ledger.reset()
    ledger.observe(2.0, {1: QRect(10, 300, 50, 20)}, NAMES, VISIBLE)
    assert ledger.expired(9.0) == []


def test_describe_item_names_what_the_log_needs_and_nothing_more():
    class Node(QGraphicsPathItem):
        state_id = "state_3"

    class Edge(QGraphicsPathItem):
        src_node, dst_node, when = Node(), Node(), "SUCCESS"

    assert describe_item(Node()) == "Node(state_3)"
    assert describe_item(Edge()) == "Edge(state_3 -SUCCESS-> state_3)"
    assert describe_item(QGraphicsPathItem()) == "QGraphicsPathItem"


# ---------------------------------------------------------------------------
# A tracer on a live view
# ---------------------------------------------------------------------------


class _Sneaky(QGraphicsItem):
    """Changes its bounds without telling Qt, so Qt can't erase where it was."""

    def __init__(self):
        super().__init__()
        self.rect = QRectF(100, 100, 80, 40)

    def boundingRect(self):
        return self.rect

    def paint(self, painter, option, widget=None):
        painter.drawRect(self.rect)


def _live(qtbot, item, shots=None):
    scene = QGraphicsScene()
    scene.setSceneRect(QRectF(0, 0, 600, 400))
    scene.addItem(item)
    view = QGraphicsView(scene)
    view.resize(640, 440)
    view.show()
    qtbot.waitExposed(view)
    tracer = RepaintTracer(view, screenshot_dir=shots)
    qtbot.wait(50)
    tracer.sample()
    return view, tracer


def _settle(qtbot, tracer) -> list:
    """Let the change paint, then look far enough ahead that anything owed has expired."""
    qtbot.wait(100)
    tracer.sample()
    return tracer.report_expired(time.monotonic() + 60)


def test_a_change_qt_repaints_is_not_reported(qtbot, caplog, tmp_path):
    path = QPainterPath()
    path.addRect(QRectF(100, 100, 80, 40))
    item = QGraphicsPathItem(path)
    _view, tracer = _live(qtbot, item, tmp_path)

    moved = QPainterPath()
    moved.addRect(QRectF(100, 250, 80, 40))
    item.setPath(moved)

    with caplog.at_level(logging.WARNING, logger="bartlebot.gui.debug.repaint_trace"):
        assert _settle(qtbot, tracer) == []
    assert not caplog.records


def test_bounds_changed_behind_qts_back_are_reported(qtbot, caplog, tmp_path):
    item = _Sneaky()
    _view, tracer = _live(qtbot, item, tmp_path)

    item.rect = QRectF(100, 250, 80, 40)
    item.update()  # repaints the new bounds only: the old ones are left on screen

    with caplog.at_level(logging.WARNING, logger="bartlebot.gui.debug.repaint_trace"):
        stale = _settle(qtbot, tracer)
    assert [(s.what, s.kind) for s in stale] == [("_Sneaky", "was")]
    assert "Lost canvas repaint" in caplog.text
    assert len(list(tmp_path.glob("stale_*.png"))) == 1, "the real screen is captured as evidence"


def test_a_finding_carries_what_tests_the_compat_path_theory(qtbot, caplog, tmp_path):
    item = _Sneaky()
    _view, tracer = _live(qtbot, item, tmp_path)

    item.rect = QRectF(100, 250, 80, 40)
    item.update()  # paints the new bounds, so a paint lands after the change

    with caplog.at_level(logging.WARNING, logger="bartlebot.gui.debug.repaint_trace"):
        _settle(qtbot, tracer)
    text = caplog.text
    assert "update path: direct" in text
    assert "inside the scene rect" in text and "NOT inside" not in text
    paints = text.split("paints since the change:")[1].split("recent canvas events:")[0]
    assert "rect(s)" in paints, "the paint that covered the new bounds is listed"


def test_update_path_names_the_compat_path_once_anything_listens_to_changed(qapp):
    scene = QGraphicsScene()
    assert update_path(scene) == "direct"
    scene.changed.connect(lambda _rects: None)
    assert update_path(scene).startswith("compat (1 ")


def test_the_tracer_never_puts_the_canvas_on_the_compat_path(qtbot):
    _view, tracer = _live(qtbot, _Sneaky())
    assert update_path(_view.scene()) == "direct"


def test_the_canvas_installs_a_tracer_only_in_dev_mode(qapp, monkeypatch):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    assert BotCanvas().findChildren(RepaintTracer) == []
    monkeypatch.setenv("BARTLEBOT_DEV", "1")
    assert len(BotCanvas().findChildren(RepaintTracer)) == 1
