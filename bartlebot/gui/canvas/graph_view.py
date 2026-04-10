"""Reusable zoomable/pannable QGraphicsView and minimap overlay.

GraphView    — base view used by both the editor and the read-only run view.
_MinimapView — small thumbnail overlay driven by the same scene.
"""

from PyQt6.QtCore import QPointF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QKeyEvent, QPainter, QPen, QWheelEvent
from PyQt6.QtWidgets import QGraphicsScene, QGraphicsView

from bartlebot.gui.theme import THEME

# ---------------------------------------------------------------------------
# GraphView — reusable zoomable/pannable view
# ---------------------------------------------------------------------------


class GraphView(QGraphicsView):
    """Zoomable, pannable QGraphicsView.

    interactive=False (default): read-only, left-drag pans.
    interactive=True: items are selectable/movable, middle-mouse pans,
                      left-drag produces a rubber-band selection rect.
    """

    view_changed = pyqtSignal()  # emitted after zoom so overlays can repaint
    _ZOOM_FACTOR = 1.15

    def __init__(self, scene: QGraphicsScene, interactive: bool = False, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._user_zoomed = False
        self._pan_origin: QPointF | None = None
        self._space_held: bool = False
        self._fitting: bool = False

        if interactive:
            self.setInteractive(True)
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        else:
            self.setInteractive(False)
            self.setDragMode(QGraphicsView.DragMode.NoDrag)

    def fit_scene(self) -> None:
        """Fit the full scene content into the viewport. Resets user-zoom state."""
        if self._fitting:
            return
        self._fitting = True
        self._user_zoomed = False
        scene = self.scene()
        if scene is None:
            self._fitting = False
            return
        bounds = scene.itemsBoundingRect()
        if bounds.isEmpty():
            self._fitting = False
            return
        self.fitInView(bounds.adjusted(-80, -60, 80, 60), Qt.AspectRatioMode.KeepAspectRatio)
        # Keep _fitting True until after any resize events queued by fitInView are
        # delivered — a 0ms timer fires after already-queued events, breaking the loop.
        QTimer.singleShot(0, self._clear_fitting)

    def _clear_fitting(self) -> None:
        self._fitting = False

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._user_zoomed = True
        factor = self._ZOOM_FACTOR if event.angleDelta().y() > 0 else 1 / self._ZOOM_FACTOR
        self.scale(factor, factor)
        self.view_changed.emit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._user_zoomed:
            self.fit_scene()

    # Space + left-drag panning
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            if self._pan_origin is None:
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = False
            if self._pan_origin is None:
                self.viewport().unsetCursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event) -> None:
        if self._space_held and event.button() == Qt.MouseButton.LeftButton:
            self._pan_origin = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._pan_origin is not None:
            delta = event.position() - self._pan_origin
            self._pan_origin = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._pan_origin is not None:
            self._pan_origin = None
            if self._space_held:
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------------
# _MinimapView — thumbnail overview of the full scene
# ---------------------------------------------------------------------------


class _MinimapView(QGraphicsView):
    """Small read-only thumbnail of the EditScene, shown as an overlay.

    Renders the same QGraphicsScene as the main view.  Clicking or dragging
    inside the minimap pans the main view to that scene position.  A
    translucent rectangle is drawn over the minimap to indicate the portion
    of the scene currently visible in the main view.
    """

    _W = 160
    _H = 110

    def __init__(self, scene: QGraphicsScene, main_view: GraphView, parent=None):
        super().__init__(scene, parent)
        self._main_view = main_view

        self.setFixedSize(self._W, self._H)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setInteractive(False)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setStyleSheet(
            f"QGraphicsView {{"
            f"  border: 1px solid {THEME.minimap_border};"
            f"  border-radius: 4px;"
            f"  background: {THEME.minimap_bg};"
            f"}}"
        )

        # Repaint viewport rect whenever main view scrolls or zooms
        main_view.horizontalScrollBar().valueChanged.connect(self.viewport().update)
        main_view.verticalScrollBar().valueChanged.connect(self.viewport().update)
        main_view.view_changed.connect(self.viewport().update)

    def fit_to_scene(self) -> None:
        """Fit the minimap to show the full scene bounds."""
        bounds = self.scene().itemsBoundingRect()
        if not bounds.isEmpty():
            self.fitInView(
                bounds.adjusted(-60, -60, 60, 60),
                Qt.AspectRatioMode.KeepAspectRatio,
            )

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        # Draw the main view's visible scene rect as a translucent overlay
        visible_scene = self._main_view.mapToScene(self._main_view.viewport().rect()).boundingRect()
        mini_rect = self.mapFromScene(visible_scene).boundingRect()

        vp_color = THEME.qcolor("minimap_viewport")
        vp_fill = QColor(vp_color)
        vp_fill.setAlpha(40)
        vp_color.setAlpha(200)
        painter = QPainter(self.viewport())
        painter.setPen(QPen(vp_color, 1.5))
        painter.setBrush(QBrush(vp_fill))
        painter.drawRect(mini_rect)
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._main_view.centerOn(self.mapToScene(event.pos()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._main_view.centerOn(self.mapToScene(event.pos()))
            event.accept()
            return
        super().mouseMoveEvent(event)
