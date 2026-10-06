"""Developer-only tracer for #126: presses over the canvas that never reach it.

On only with ``BARTLEBOT_DEV`` set (see ``store_roots.dev_mode``). #126 is a canvas
that takes no clicks, drags, edges or context menu while in Edit mode. The canvas logs
every press its viewport receives in Focus Mode and in a definition document
(``BotCanvas._log_input_click``), so a click that leaves nothing in the log is one the
viewport never saw. This tracer names the widget that took it instead.

It is an application event filter, since a widget covering the viewport, or one holding
the mouse grab, takes the press before the viewport could filter it. It logs, at DEBUG,
each press whose global position lies inside the viewport but which went to some other
widget in the same window. The crumb bar and the other overlays drawn over the viewport
are logged as well, which is expected: they are the known ones. Popups and dialogs are
separate windows and are skipped. A press is logged once, however far it propagates.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)


class PressTracer(QObject):
    """Log presses inside ``viewport``'s area that some other widget received."""

    def __init__(self, viewport: QWidget) -> None:
        super().__init__(viewport)
        self._viewport = viewport
        self._last_stamp: int | None = None
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        logger.info("Canvas press tracer on (BARTLEBOT_DEV); presses that miss the canvas are logged here")

    def stop(self) -> None:
        """Stop watching. Safe to call twice."""
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001
        if event.type() != QEvent.Type.MouseButtonPress or not isinstance(obj, QWidget):
            return False
        vp = self._viewport
        if obj is vp or vp.isAncestorOf(obj):
            # One the viewport ignores propagates on to its parents: the same press.
            self._last_stamp = event.timestamp()
            return False
        if not vp.isVisible() or obj.window() is not vp.window():
            return False
        if event.timestamp() == self._last_stamp:
            return False  # the same press, propagating to a parent
        gp = event.globalPosition().toPoint()
        if not vp.rect().contains(vp.mapFromGlobal(gp)):
            return False
        self._last_stamp = event.timestamp()
        grabber = QWidget.mouseGrabber()
        qt_hit = QApplication.widgetAt(gp)
        logger.debug(
            "Press over the canvas went to %s (%s), not the viewport; mouse grabber=%s; receiver native=%s "
            "WA_NativeWindow=%s transparent=%s; Qt's own hit test there=%s; button=%s buttons=%s; "
            "native ancestors=%s",
            type(obj).__name__,
            obj.objectName() or "unnamed",
            type(grabber).__name__ if grabber is not None else None,
            obj.windowHandle() is not None,
            obj.testAttribute(Qt.WidgetAttribute.WA_NativeWindow),
            obj.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents),
            type(qt_hit).__name__ if qt_hit is not None else None,
            event.button().name,
            int(event.buttons().value),
            _native_chain(obj),
        )
        return False


def _native_chain(widget: QWidget) -> list[str]:
    """The widgets from ``widget`` up to its window that have a native window of their own."""
    out = []
    w: QWidget | None = widget
    while w is not None:
        if w.windowHandle() is not None:
            out.append(type(w).__name__)
        if w.isWindow():
            break
        w = w.parentWidget()
    return out
