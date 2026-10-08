"""Reusable zoomable/pannable QGraphicsView and minimap overlay.

GraphView    — base view used by both the editor and the read-only run view.
_MinimapView — small thumbnail overlay driven by the same scene.
"""

import logging

from PyQt6.QtCore import QEasingCurve, QEvent, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QCursor, QKeyEvent, QMouseEvent, QPainter, QPen, QTransform, QWheelEvent
from PyQt6.QtWidgets import QApplication, QGraphicsItem, QGraphicsScene, QGraphicsView

from bartlebot.gui.theme import THEME
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GraphView — reusable zoomable/pannable view
# ---------------------------------------------------------------------------


def prime_last_mouse_event(view: QGraphicsView) -> None:
    """Populate Qt's cached last-mouse-event for *view*, once it is on screen.

    Works around an upstream Qt bug (present in qtbase 6.8 through dev):
    ``QGraphicsViewPrivate::_q_unsetViewportCursor()`` reads
    ``lastMouseEvent->position()`` without the ``useLastMouseEvent`` guard that every
    other reader of that member has.  ``lastMouseEvent`` is absent from the private
    class's initializer list, so on a view that has never received a mouse event it
    holds uninitialized storage and ``QEventPoint::position()`` dereferences garbage.

    ``QGraphicsItem::unsetCursor()`` reaches that function whenever the view is under
    the mouse and the item being unset is the one under the pointer -- which is exactly
    what leaving Edit mode does to every node.  A window shown under a stationary
    pointer gets Enter without a mouse move, and the process dies with an access
    violation inside Qt6Gui.

    One synthetic move fixes it for good: Leave clears the ``useLastMouseEvent`` flag
    but never the storage, and the unguarded reader only looks at the storage.

    The position has to be the real pointer, not the viewport center.  This falls
    through to ``QGraphicsView::mouseMoveEvent``, which dispatches hover into the scene
    and applies the hovered item's cursor, so a center point would leave a node showing
    its hover highlight and grab cursor with the pointer somewhere else entirely.
    """
    vp = view.viewport()
    local = QPointF(vp.mapFromGlobal(QCursor.pos()))
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        local,
        QPointF(vp.mapToGlobal(local.toPoint())),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    # QApplication::notify throws away a button-less move to a viewport that is not
    # tracking the mouse, and a canvas shown before any bot is loaded is exactly that:
    # nothing in the scene has a cursor yet, so Qt has not turned tracking on.  The
    # priming would be silently dropped and never retried.  Qt turns tracking back on
    # itself the moment an item takes a cursor, so borrowing it here changes nothing.
    tracking = vp.hasMouseTracking()
    vp.setMouseTracking(True)
    try:
        QApplication.sendEvent(vp, event)
    finally:
        vp.setMouseTracking(tracking)


class GraphView(QGraphicsView):
    """Zoomable, pannable QGraphicsView.

    interactive=False (default): read-only, left-drag pans.
    interactive=True: items are selectable/movable, middle-mouse pans,
                      left-drag produces a rubber-band selection rect.
    """

    view_changed = pyqtSignal()  # emitted after zoom so overlays can repaint

    def __init__(self, scene: QGraphicsScene, interactive: bool = False, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._user_zoomed = False
        self._pan_origin: QPointF | None = None
        self._space_held: bool = False
        self._fitting: bool = False
        self._primed_mouse: bool = False

        if interactive:
            self.setInteractive(True)
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        else:
            self.setInteractive(False)
            self.setDragMode(QGraphicsView.DragMode.NoDrag)

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        if not self._primed_mouse:
            self._primed_mouse = True
            prime_last_mouse_event(self)

    @staticmethod
    def _scene_fit_bounds(scene: QGraphicsScene) -> "QRectF":
        """Bounding rect of all scene items, excluding items with fit_exclude=True.

        Region items inflate their boundingRect upward for constant-screen-size pill
        labels; including them in the fit calculation adds phantom vertical space.
        """
        bounds = QRectF()
        for item in scene.items():
            if getattr(item, "fit_exclude", False):
                continue
            r = item.mapRectToScene(item.boundingRect())
            bounds = bounds.united(r) if not bounds.isNull() else r
        return bounds

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
        bounds = self._scene_fit_bounds(scene)
        if bounds.isEmpty():
            self._fitting = False
            return
        self.fitInView(bounds.adjusted(-80, -60, 80, 60), Qt.AspectRatioMode.KeepAspectRatio)
        # Keep _fitting True until after any resize events queued by fitInView are
        # delivered — a 0ms timer fires after already-queued events, breaking the loop.
        QTimer.singleShot(0, self._clear_fitting)

    def _clear_fitting(self) -> None:
        self._fitting = False

    def fit_rect(self, rect: QRectF) -> None:
        """Fit an arbitrary scene rect into the viewport, like fit_scene for a subset."""
        if self._fitting or rect.isEmpty():
            return
        self._fitting = True
        self._user_zoomed = False
        self.fitInView(rect.adjusted(-80, -60, 80, 60), Qt.AspectRatioMode.KeepAspectRatio)
        QTimer.singleShot(0, self._clear_fitting)

    def camera_state(self) -> tuple[QTransform, int, int]:
        """Zoom and scroll offsets, enough to put the camera back exactly.

        Scroll positions rather than a scene center point: ``centerOn`` goes through
        integer scrollbars, so round-tripping a scene coordinate loses up to half a
        device pixel -- which at a zoomed-out fit is several scene units, and visibly
        shifts the graph when focus mode exits.
        """
        return (
            QTransform(self.transform()),
            self.horizontalScrollBar().value(),
            self.verticalScrollBar().value(),
        )

    def restore_camera(self, state: tuple[QTransform, int, int]) -> None:
        """Undo a fit_rect, returning to the view saved by camera_state.

        The transform goes back first: it determines the scrollbar ranges the offsets
        are expressed in, so setting them the other way round clamps them.
        """
        transform, h_scroll, v_scroll = state
        self.setTransform(transform)
        self.horizontalScrollBar().setValue(h_scroll)
        self.verticalScrollBar().setValue(v_scroll)

    def smooth_pan_if_offscreen(self, item: QGraphicsItem, duration_ms: int | None = None) -> None:
        """Animate the viewport to center on item only when item is not already fully visible.

        Used after adding or pasting nodes so the user always sees the result, but the camera
        stays put when the node lands inside the current viewport.
        """
        node_scene_rect = item.mapRectToScene(item.boundingRect())
        visible_scene_rect = self.mapToScene(self.viewport().rect()).boundingRect()
        if not visible_scene_rect.contains(node_scene_rect):
            self.smooth_center_on(item, duration_ms)

    def smooth_center_on(self, item: QGraphicsItem, duration_ms: int | None = None) -> None:
        """Animate the viewport to center on item.

        Skipped only when the node is fully visible AND the entire graph fits in the viewport.
        When zoomed in past the point where all nodes fit, always re-centers on the active node.
        """
        if duration_ms is None:
            duration_ms = SettingsManager.instance().get("canvas", "pan_animation_ms", default=300)

        node_scene_rect = item.mapRectToScene(item.boundingRect())
        visible_scene_rect = self.mapToScene(self.viewport().rect()).boundingRect()
        if visible_scene_rect.contains(node_scene_rect):
            scene = self.scene()
            if scene is not None:
                all_bounds = self._scene_fit_bounds(scene)
                if all_bounds.isNull() or visible_scene_rect.contains(all_bounds):
                    return

        center = node_scene_rect.center()
        vp = self.mapFromScene(center)
        dx = int(vp.x() - self.viewport().width() / 2)
        dy = int(vp.y() - self.viewport().height() / 2)

        for anim in (getattr(self, "_pan_anim_h", None), getattr(self, "_pan_anim_v", None)):
            if anim is not None:
                anim.stop()

        self._pan_anim_h = QPropertyAnimation(self.horizontalScrollBar(), b"value", self)
        self._pan_anim_h.setDuration(duration_ms)
        self._pan_anim_h.setStartValue(self.horizontalScrollBar().value())
        self._pan_anim_h.setEndValue(self.horizontalScrollBar().value() + dx)
        self._pan_anim_h.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self._pan_anim_v = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._pan_anim_v.setDuration(duration_ms)
        self._pan_anim_v.setStartValue(self.verticalScrollBar().value())
        self._pan_anim_v.setEndValue(self.verticalScrollBar().value() + dy)
        self._pan_anim_v.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self._pan_anim_h.start()
        self._pan_anim_v.start()

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._user_zoomed = True
        _zoom_step = SettingsManager.instance().get("canvas", "zoom_step", default=1.15)
        factor = _zoom_step if event.angleDelta().y() > 0 else 1 / _zoom_step
        cursor_pos = event.position().toPoint()
        scene_pos = self.mapToScene(cursor_pos)
        self.scale(factor, factor)
        delta = self.mapFromScene(scene_pos) - cursor_pos
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + delta.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + delta.y())
        self.view_changed.emit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._user_zoomed:
            self.fit_scene()

    @property
    def space_held(self) -> bool:
        """Whether Space is down over the view, which turns a left-drag into a pan."""
        return self._space_held

    @property
    def panning(self) -> bool:
        """Whether a Space + left-drag pan is under way."""
        return self._pan_origin is not None

    # Space + left-drag panning
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            # #126: a press with no release leaves every left-click panning.
            logger.debug("Space pan: held")
            self._space_held = True
            if self._pan_origin is None:
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            logger.debug("Space pan: released")
            self._space_held = False
            if self._pan_origin is None:
                self.viewport().unsetCursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: ANN001
        if self._space_held:
            # The release goes wherever focus went, so the view never hears it (#126).
            logger.debug("Space pan: view lost focus (%s) with Space still held", event.reason().name)
        super().focusOutEvent(event)

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
        self._primed_mouse = False

        self.setFixedSize(self._W, self._H)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setInteractive(False)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._apply_stylesheet()

        # Repaint viewport rect whenever main view scrolls or zooms
        main_view.horizontalScrollBar().valueChanged.connect(self.viewport().update)
        main_view.verticalScrollBar().valueChanged.connect(self.viewport().update)
        main_view.view_changed.connect(self.viewport().update)

    def showEvent(self, event) -> None:  # noqa: ANN001
        # Second view on the same scene, so an equally unprimed trigger for the Qt bug
        # prime_last_mouse_event describes -- unsetCursor() walks every attached view.
        super().showEvent(event)
        if not self._primed_mouse:
            self._primed_mouse = True
            prime_last_mouse_event(self)

    def _apply_stylesheet(self) -> None:
        css = (
            f"QGraphicsView {{"
            f"  border: 1px solid {THEME.minimap_border};"
            f"  border-radius: 4px;"
            f"  background: {THEME.minimap_bg};"
            f"}}"
        )
        if self.styleSheet() != css:
            self.setStyleSheet(css)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_stylesheet()

    def fit_to_scene(self) -> None:
        """Fit the minimap to show the full scene bounds."""
        bounds = GraphView._scene_fit_bounds(self.scene())
        if not bounds.isEmpty():
            self.fitInView(
                bounds.adjusted(-60, -60, 60, 60),
                Qt.AspectRatioMode.KeepAspectRatio,
            )

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802 - Qt override
        """The scene's own background, without the editor's grid.

        The minimap shows the whole scene, so it would draw the whole grid -- some
        2,500 dots, one Python call each, on every scroll step of the main view. That
        was two thirds of the cost of a pan, for dots smaller than a minimap pixel.
        """
        scene = self.scene()
        if scene is not None:
            QGraphicsScene.drawBackground(scene, painter, rect)

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
