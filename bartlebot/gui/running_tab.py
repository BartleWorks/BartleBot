"""Running tab — graphical read-only view of the active bot execution."""

import time

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QPushButton, QPlainTextEdit,
    QGraphicsScene, QGraphicsView,
)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QPainter, QFont, QTextCursor, QWheelEvent

from bartlebot.gui.graph_items import NodeItem, EdgeItem, NODE_W, NODE_H


# ---------------------------------------------------------------------------
# Zoomable / pannable QGraphicsView
# ---------------------------------------------------------------------------

class _GraphView(QGraphicsView):
    """Read-only graphics view with scroll-wheel zoom and drag-to-pan."""

    _ZOOM_FACTOR = 1.15

    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setInteractive(False)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._user_zoomed = False

    def fit_scene(self) -> None:
        """Fit the full scene into the viewport. Resets user-zoom state."""
        self._user_zoomed = False
        if self.scene() and not self.scene().sceneRect().isEmpty():
            self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._user_zoomed = True
        factor = self._ZOOM_FACTOR if event.angleDelta().y() > 0 else 1 / self._ZOOM_FACTOR
        self.scale(factor, factor)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._user_zoomed:
            self.fit_scene()


# ---------------------------------------------------------------------------
# RunningTab
# ---------------------------------------------------------------------------

_IDLE = "idle"
_RUNNING = "running"
_PAUSED = "paused"


class RunningTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._start_time: float | None = None
        self._node_items: dict[str, NodeItem] = {}
        self._active_state: str | None = None
        self._run_state = _IDLE

        # Callbacks wired from bartlebot.py
        self.on_run = lambda: None
        self.on_pause = lambda: None
        self.on_resume = lambda: None

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Top info bar: state label | elapsed | Run/Pause | Stop
        info_bar = QWidget()
        info_row = QHBoxLayout(info_bar)
        info_row.setContentsMargins(10, 6, 10, 6)

        self._state_label = QLabel("State: Idle")
        self._elapsed_label = QLabel("00:00:00")
        self._elapsed_label.setFont(QFont("Consolas", 10))
        self._elapsed_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self._run_pause_btn = QPushButton("▶  Run")
        self._run_pause_btn.setFixedWidth(110)
        self._run_pause_btn.setEnabled(False)
        self._run_pause_btn.clicked.connect(self._on_run_pause_clicked)

        self.stop_button = QPushButton("■  Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.setFixedWidth(90)

        info_row.addWidget(self._state_label, 1)
        info_row.addWidget(self._elapsed_label)
        info_row.addSpacing(12)
        info_row.addWidget(self._run_pause_btn)
        info_row.addSpacing(4)
        info_row.addWidget(self.stop_button)
        outer.addWidget(info_bar)

        # Splitter: graph view (top, 2/3) | log (bottom, 1/3)
        splitter = QSplitter(Qt.Orientation.Vertical)
        outer.addWidget(splitter, 1)

        self._scene = QGraphicsScene()
        self._view = _GraphView(self._scene)
        splitter.addWidget(self._view)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 9))
        splitter.addWidget(self._log)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

    # ------------------------------------------------------------------
    # Public API (called from bartlebot.py)
    # ------------------------------------------------------------------

    def setup_graph(self, config: dict) -> None:
        """Build the FSM graph from a machine config dict."""
        self._scene.clear()
        self._node_items.clear()
        self._active_state = None

        states = config["states"]
        transitions = config["transitions"]

        # Place nodes
        for i, state in enumerate(states):
            pos = state.get("position")
            if pos and isinstance(pos, dict) and "x" in pos and "y" in pos:
                x = pos["x"] - NODE_W / 2
                y = pos["y"] - NODE_H / 2
            else:
                spacing = NODE_W + 80
                total_w = (len(states) - 1) * spacing
                x = i * spacing - total_w / 2 - NODE_W / 2
                y = -NODE_H / 2

            node = NodeItem(state["id"], state.get("label"))
            node.setPos(x, y)
            self._scene.addItem(node)
            self._node_items[state["id"]] = node

        # Identify bidirectional pairs
        non_loops = {
            (t["source"], t["dest"])
            for t in transitions
            if t["source"] != t["dest"]
        }
        bidi: set[tuple[str, str]] = {
            (s, d) for (s, d) in non_loops if (d, s) in non_loops
        }

        # Draw one EdgeItem per unique (source, dest)
        drawn: set[tuple[str, str]] = set()
        for t in transitions:
            s, d = t["source"], t["dest"]
            if (s, d) in drawn:
                continue
            drawn.add((s, d))

            if s not in self._node_items or d not in self._node_items:
                continue

            src_node = self._node_items[s]
            dst_node = self._node_items[d]

            if s == d:
                edge = EdgeItem(src_node, src_node)
            else:
                is_bidi = (s, d) in bidi
                offset = 22.0 if (is_bidi and s < d) else (-22.0 if is_bidi else 0.0)
                edge = EdgeItem(src_node, dst_node, offset=offset)

            self._scene.addItem(edge)

        # Set scene rect and fit view (re-fits on each resize until user zooms)
        bounds = self._scene.itemsBoundingRect().adjusted(-80, -100, 80, 60)
        self._scene.setSceneRect(bounds)
        self._view.fit_scene()

    def set_run_enabled(self, enabled: bool) -> None:
        """Enable/disable the Run button (only takes effect in idle state)."""
        if self._run_state == _IDLE:
            self._run_pause_btn.setEnabled(enabled)

    def set_running(self) -> None:
        """Transition to running state: reset log/timer, update buttons."""
        self._run_state = _RUNNING
        self._run_pause_btn.setText("⏸  Pause")
        self._run_pause_btn.setEnabled(True)
        self.stop_button.setEnabled(True)
        self._log.clear()
        self._start_time = time.monotonic()
        self._elapsed_label.setText("00:00:00")
        self._tick_timer.start()
        self._append_log("=== Bot started ===")

    def set_paused(self) -> None:
        """Transition to paused state: update button label."""
        self._run_state = _PAUSED
        self._run_pause_btn.setText("▶  Resume")
        self._run_pause_btn.setEnabled(True)
        self._append_log(f"[{self._elapsed_str()}] === Paused ===")

    def set_idle(self) -> None:
        """Transition to idle state: stop timer, deactivate nodes, update buttons."""
        self._run_state = _IDLE
        self._run_pause_btn.setText("▶  Run")
        self._run_pause_btn.setEnabled(False)  # re-enabled by set_run_enabled
        self.stop_button.setEnabled(False)
        self._tick_timer.stop()
        self._state_label.setText("State: Idle")
        if self._active_state and self._active_state in self._node_items:
            self._node_items[self._active_state].set_active(False)
        self._active_state = None
        self._append_log("=== Bot stopped ===")

    def set_state(self, state_id: str) -> None:
        """Highlight the newly-entered state node and log the entry."""
        if self._active_state and self._active_state in self._node_items:
            self._node_items[self._active_state].set_active(False)
        self._active_state = state_id
        if state_id in self._node_items:
            self._node_items[state_id].set_active(True)
        self._state_label.setText(f"State: {state_id}")
        self._append_log(f"[{self._elapsed_str()}] Entered: {state_id}")

    def on_transition(self, prev_state: str, result: str, next_state: str) -> None:
        """Log the result and transition taken after each node execution."""
        if prev_state == next_state:
            self._append_log(
                f"[{self._elapsed_str()}] Result: {result} → (retry {next_state})"
            )
        else:
            self._append_log(
                f"[{self._elapsed_str()}] Result: {result} → {next_state}"
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _on_run_pause_clicked(self) -> None:
        if self._run_state == _IDLE:
            self.on_run()  # on_run is responsible for calling set_running()
        elif self._run_state == _RUNNING:
            self.set_paused()   # update UI immediately
            self.on_pause()     # pause the machine
        elif self._run_state == _PAUSED:
            # Resume: restore running UI without resetting log/timer
            self._run_state = _RUNNING
            self._run_pause_btn.setText("⏸  Pause")
            self._append_log(f"[{self._elapsed_str()}] === Resumed ===")
            self.on_resume()    # resume the machine

    def _tick(self) -> None:
        self._elapsed_label.setText(self._elapsed_str())

    def _elapsed_str(self) -> str:
        if self._start_time is None:
            return "00:00:00"
        s = int(time.monotonic() - self._start_time)
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{sec:02d}"

    def _append_log(self, text: str) -> None:
        self._log.appendPlainText(text)
        self._log.moveCursor(QTextCursor.MoveOperation.End)
