"""The #126 diagnostics: what the log says about a canvas that takes no input.

#126 is an Edit-mode canvas whose clicks, drags and edges all do nothing. These lines are
how the next occurrence gets explained: the press log in a definition document, the
Space-pan state, a mouse grab that outlives its release, and the developer-only tracer
for presses some other widget took.
"""

from __future__ import annotations

import logging

import pytest
from PyQt6.QtCore import QPoint, QRectF, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QGraphicsRectItem, QWidget

from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary
from bartlebot.content.subgraphs.subgraphs import definition_to_config
from bartlebot.gui.debug.press_trace import PressTracer
from tests.content.subgraphs.test_subgraphs import SYNC_ID, linked_bot, sync_players_definition


@pytest.fixture
def canvas(qtbot):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    c = BotCanvas()
    qtbot.addWidget(c)
    c.resize(1200, 800)
    c.show()
    yield c
    c.prepare_for_shutdown()


def _definition(canvas):
    canvas.setup(definition_to_config(sync_players_definition()))
    canvas.enter_edit_mode()
    QApplication.processEvents()


def _lines(caplog, text):
    return [r.getMessage() for r in caplog.records if text in r.getMessage()]


def test_a_press_in_a_definition_document_is_logged(canvas, caplog):
    _definition(canvas)
    caplog.set_level(logging.DEBUG)
    QTest.mousePress(canvas.view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    QApplication.processEvents()
    QTest.mouseRelease(canvas.view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    (line,) = _lines(caplog, "Canvas click: button=")
    assert "space_held=False" in line and "widget_grabber=" in line and "modal=None" in line
    assert _lines(caplog, "Canvas click: after press")


def test_a_press_in_a_plain_bot_is_not_logged(canvas, caplog, tmp_path):
    library = SubgraphLibrary(tmp_path / "library")
    library.save(sync_players_definition())
    canvas.setup(linked_bot(library.get(SYNC_ID), library))
    canvas.enter_edit_mode()
    caplog.set_level(logging.DEBUG)
    QTest.mouseClick(canvas.view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    assert not _lines(caplog, "Canvas click")


def test_opening_a_definition_logs_the_canvas_state_after_the_event(canvas, caplog):
    caplog.set_level(logging.DEBUG)
    _definition(canvas)
    (line,) = _lines(caplog, "Canvas input: opened definition")
    assert "mode=EDIT" in line and "scene_interactive=True" in line and "view_interactive=True" in line


def test_space_pan_logs_held_released_and_a_lost_release(canvas, caplog):
    caplog.set_level(logging.DEBUG)
    view = canvas.view
    QTest.keyPress(view, Qt.Key.Key_Space)
    assert view.space_held
    QTest.keyRelease(view, Qt.Key.Key_Space)
    assert not view.space_held
    QTest.keyPress(view, Qt.Key.Key_Space)
    view.focusOutEvent(_focus_out())
    assert view.space_held, "logged, not cleared: the log is there to show the stuck state"
    assert len(_lines(caplog, "Space pan: held")) == 2
    assert len(_lines(caplog, "Space pan: released")) == 1
    assert _lines(caplog, "with Space still held")


def _focus_out():
    from PyQt6.QtGui import QFocusEvent  # noqa: PLC0415

    return QFocusEvent(QFocusEvent.Type.FocusOut, Qt.FocusReason.ActiveWindowFocusReason)


class _Regrabber(QGraphicsRectItem):
    """Takes the mouse grab back as its release ends: a grab that outlives the release."""

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        self.grabMouse()


def test_a_grab_outliving_its_release_is_logged(canvas, caplog):
    _definition(canvas)
    item = _Regrabber(QRectF(-20, -20, 40, 40))
    item.setZValue(1000)
    canvas.scene.addItem(item)
    canvas.view.centerOn(item)
    QApplication.processEvents()
    caplog.set_level(logging.DEBUG)
    pt = canvas.view.mapFromScene(item.sceneBoundingRect().center())
    QTest.mouseClick(canvas.view.viewport(), Qt.MouseButton.LeftButton, pos=pt)
    assert _lines(caplog, "Mouse grab survived release: _Regrabber (in this scene)")
    item.ungrabMouse()


def test_an_ordinary_click_leaves_no_grab_line(canvas, caplog):
    _definition(canvas)
    caplog.set_level(logging.DEBUG)
    node = next(iter(canvas.scene.node_items.values()))
    canvas.view.centerOn(node)
    QApplication.processEvents()
    pt = canvas.view.mapFromScene(node.sceneBoundingRect().center())
    QTest.mouseClick(canvas.view.viewport(), Qt.MouseButton.LeftButton, pos=pt)
    assert node.isSelected()
    assert not _lines(caplog, "Mouse grab survived release")


# ---------------------------------------------------------------------------
# The press tracer
# ---------------------------------------------------------------------------


def test_the_canvas_installs_a_press_tracer_only_in_dev_mode(qapp, monkeypatch):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    plain = BotCanvas()
    assert plain.findChildren(PressTracer) == []
    monkeypatch.setenv("BARTLEBOT_DEV", "1")
    dev = BotCanvas()
    assert len(dev.findChildren(PressTracer)) == 1
    plain.prepare_for_shutdown()
    dev.prepare_for_shutdown()


@pytest.fixture
def traced(qtbot):
    """A shown host with a viewport-like widget, an overlay over half of it, and a tracer on it."""
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(400, 300)
    vp = QWidget(host)
    vp.setGeometry(0, 0, 400, 300)
    overlay = QWidget(host)
    overlay.setGeometry(0, 0, 200, 300)
    host.show()
    tracer = PressTracer(vp)
    yield vp, overlay
    tracer.stop()


def test_a_press_an_overlay_took_is_logged_once(traced, caplog):
    vp, overlay = traced
    caplog.set_level(logging.DEBUG)
    QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=QPoint(50, 50))
    (line,) = _lines(caplog, "Press over the canvas went to")
    assert "QWidget" in line and "not the viewport" in line


def test_a_press_the_viewport_took_is_not_logged(traced, caplog):
    vp, _overlay = traced
    caplog.set_level(logging.DEBUG)
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=QPoint(300, 50))
    assert not _lines(caplog, "Press over the canvas")
