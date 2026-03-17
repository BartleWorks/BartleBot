"""Unified bot canvas — single QGraphicsScene with Run and Edit modes.

BotCanvas  — top-level widget; owns the scene, toolbars, properties panel,
             and run log. Replaces the former EditTab + RunningTab pair.
EditScene  — interactive QGraphicsScene (same logic as the old edit_tab.py).
"""

import enum
import math
import time
from typing import Callable

from PyQt6.QtCore import Qt, QPointF, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QPen, QKeyEvent, QMouseEvent, QTextCharFormat, QTextCursor
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox, QGraphicsItem, QGraphicsLineItem, QGraphicsScene,
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from bartlebot.bot.schema import BotConfig, BotOptionModel, StateModel, TransitionModel
from bartlebot.log_level import LogLevel
from bartlebot.gui.canvas.graph_items import (
    EdgeItem, GraphView, NodeItem, NODE_H, NODE_W, PORT_R,
)
from bartlebot.nodes.registry import NODE_REGISTRY, NODE_PARAM_SCHEMAS, NODE_SPECS
from bartlebot.gui.canvas.param_panel import _ParamPanel


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PORT_HIT_RADIUS = 16
_AUTO_SPACING = 260

_TB_RUN  = 0  # toolbar stack index for Run mode
_TB_EDIT = 1  # toolbar stack index for Edit mode

_IDLE    = "idle"
_RUNNING = "running"
_PAUSED  = "paused"

_LOG_PREFIXES: dict[LogLevel, str] = {
    LogLevel.DEBUG:   "[DBG]",
    LogLevel.INFO:    "[INF]",
    LogLevel.WARNING: "[WRN]",
    LogLevel.ERROR:   "[ERR]",
}
_LOG_COLORS: dict[LogLevel, str] = {
    LogLevel.DEBUG:   "#5599ff",
    LogLevel.INFO:    "#55cc55",
    LogLevel.WARNING: "#ddcc00",
    LogLevel.ERROR:   "#ff5555",
}


class BotMode(enum.Enum):
    RUN  = "run"
    EDIT = "edit"


# ---------------------------------------------------------------------------
# Bot-option injection
# ---------------------------------------------------------------------------

def _inject_required_options(config: BotConfig) -> None:
    """Sync bot options required by node specs with config.options.

    - Adds options for any NodeSpec requirement not already present.
    - Removes options that are system-managed (known to any NodeSpec) but are no
      longer required by any state currently in the config.
    - Existing user-set values are preserved when options are re-added.
    """
    # Collect option names required by the current set of states.
    required_now: set[str] = set()
    for state in config.states:
        spec = NODE_SPECS.get(state.node_type)
        if spec:
            for req in spec.required_bot_options:
                required_now.add(req.name)

    # Collect all option names that are system-managed (declared by any NodeSpec).
    all_system_names: set[str] = set()
    for spec in NODE_SPECS.values():
        for req in spec.required_bot_options:
            all_system_names.add(req.name)

    # Prune system-managed options that are no longer required by any state.
    config.options = [
        o for o in config.options
        if o.name not in all_system_names or o.name in required_now
    ]

    # Add missing required options (preserves user-customised values already present).
    existing = {o.name for o in config.options}
    for state in config.states:
        spec = NODE_SPECS.get(state.node_type)
        if not spec:
            continue
        for req in spec.required_bot_options:
            if req.name not in existing:
                config.options.append(BotOptionModel(
                    name=req.name,
                    type=req.type,
                    default=req.default,
                    description=req.description,
                ))
                existing.add(req.name)


# ---------------------------------------------------------------------------
# EditScene — interactive QGraphicsScene
# ---------------------------------------------------------------------------

class EditScene(QGraphicsScene):
    """Manages interactive nodes, edge dragging, deletion, and positions."""

    config_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._node_items: dict[str, NodeItem] = {}
        self._edge_items: list[tuple[str, str | None, str, EdgeItem]] = []
        self._drag_src: NodeItem | None = None
        self._drag_when: str | None = None
        self._temp_edge: QGraphicsLineItem | None = None
        self._interactive_mode: bool = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setup(self, config: BotConfig) -> None:
        """Rebuild the scene from a BotConfig."""
        self.clear()
        self._node_items.clear()
        self._edge_items.clear()
        self._drag_src = None
        self._drag_when = None
        self._temp_edge = None
        self._config = config

        states = config.states
        transitions = config.transitions

        for i, state in enumerate(states):
            pos = state.position
            if pos and isinstance(pos, dict):
                x = pos.get("x", 0) - NODE_W / 2
                y = pos.get("y", 0) - NODE_H / 2
            else:
                total_w = (len(states) - 1) * _AUTO_SPACING
                x = i * _AUTO_SPACING - total_w / 2 - NODE_W / 2
                y = -NODE_H / 2
            returns = _node_returns(state.node_type)
            sources = {t.source for t in transitions}
            is_error, is_warning, reasons = _validate_node(state, config)
            node = NodeItem(
                state.id, state.label,
                interactive=self._interactive_mode,
                returns=returns,
                is_initial=(state.id == config.initial),
                is_terminal=(state.id not in sources),
                is_error=is_error,
                is_warning=is_warning,
                node_type=state.node_type,
            )
            node.set_error(is_error, reasons)
            node.set_warning(is_warning, reasons)
            node.setPos(x, y)
            self.addItem(node)
            self._node_items[state.id] = node

        non_loop = {(t.source, t.dest) for t in transitions if t.source != t.dest}
        bidi = {(s, d) for (s, d) in non_loop if (d, s) in non_loop}

        drawn: set[tuple[str, str | None, str]] = set()
        for t in transitions:
            s, d, w = t.source, t.dest, t.when
            key = (s, w, d)
            if key in drawn:
                continue
            drawn.add(key)
            if s not in self._node_items or d not in self._node_items:
                continue
            sn, dn = self._node_items[s], self._node_items[d]
            if s == d:
                edge = EdgeItem(sn, sn, when=w)
            else:
                is_bidi = (s, d) in bidi
                offset = 22.0 if (is_bidi and s < d) else (-22.0 if is_bidi else 0.0)
                edge = EdgeItem(sn, dn, offset=offset, when=w)
            self.addItem(edge)
            sn.add_edge(edge)
            if dn is not sn:
                dn.add_edge(edge)
            self._edge_items.append((s, w, d, edge))

        bounds = self.itemsBoundingRect().adjusted(-80, -100, 80, 60)
        self.setSceneRect(bounds)

    def refresh_initial(self, initial_id: str) -> None:
        """Update the initial-state purple-border indicator."""
        for node_id, node in self._node_items.items():
            node.set_initial(node_id == initial_id)

    def refresh_terminal(self) -> None:
        """Update the terminal-state badge based on current transitions."""
        if self._config is None:
            return
        sources = {t.source for t in self._config.transitions}
        for node_id, node in self._node_items.items():
            node.set_terminal(node_id not in sources)

    def refresh_errors(self) -> None:
        """Recompute and apply error/warning indicators for all nodes."""
        if self._config is None:
            return
        for state in self._config.states:
            node = self._node_items.get(state.id)
            if node is None:
                continue
            is_error, is_warning, reasons = _validate_node(state, self._config)
            node.set_error(is_error, reasons)
            node.set_warning(is_warning, reasons)

    def set_interactive(self, interactive: bool) -> None:
        """Toggle node and edge interactivity for mode switching."""
        self._interactive_mode = interactive
        for node in self._node_items.values():
            node.set_interactive(interactive)
        for _s, _w, _d, edge in self._edge_items:
            edge.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, interactive)
        if not interactive:
            self.clearSelection()

    def add_state(self, pos: QPointF) -> None:
        if self._config is None:
            return
        state_id = _unique_id([s.id for s in self._config.states])
        default_type = next(iter(NODE_REGISTRY), "go_to_zone")
        state = StateModel(
            id=state_id,
            node_type=default_type,
            position={"x": pos.x() + NODE_W / 2, "y": pos.y() + NODE_H / 2},
        )
        self._config.states.append(state)
        returns = _node_returns(default_type)
        node = NodeItem(state_id, None, interactive=True, returns=returns, is_terminal=True,
                        node_type=default_type)
        node.setPos(pos)
        self.addItem(node)
        self._node_items[state_id] = node
        self._update_scene_rect()
        self.config_changed.emit()

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._interactive_mode:
            scene_pos = event.scenePos()
            result = self._node_near_port_out(scene_pos)
            if result is not None:
                src, when = result
                self._drag_src = src
                self._drag_when = when
                src_pt = src.port_out_for(when)
                line = QGraphicsLineItem(src_pt.x(), src_pt.y(), src_pt.x(), src_pt.y())
                line.setPen(QPen(QColor("#3366bb"), 2, Qt.PenStyle.DashLine))
                line.setZValue(10)
                self._temp_edge = line
                self.addItem(line)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_src is not None and self._temp_edge is not None:
            src_pt = self._drag_src.port_out_for(self._drag_when)
            cur = event.scenePos()
            self._temp_edge.setLine(src_pt.x(), src_pt.y(), cur.x(), cur.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_src is not None:
            if self._temp_edge:
                self.removeItem(self._temp_edge)
                self._temp_edge = None
            if event.button() == Qt.MouseButton.LeftButton:
                dst = self._node_near_port_in(event.scenePos())
                if dst is not None and dst is not self._drag_src:
                    self._create_transition(self._drag_src, dst, self._drag_when)
            self._drag_src = None
            self._drag_when = None
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._flush_positions()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Delete and self._interactive_mode:
            self._delete_selected()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def contextMenuEvent(self, event) -> None:
        if not self._interactive_mode:
            return
        from PyQt6.QtWidgets import QMenu
        item = self.itemAt(
            event.scenePos(),
            self.views()[0].transform() if self.views()
            else __import__("PyQt6.QtGui", fromlist=["QTransform"]).QTransform()
        )
        menu = QMenu()
        if isinstance(item, NodeItem):
            act = QAction("Delete State", menu)
            act.triggered.connect(lambda: self._delete_node(item.state_id))
            menu.addAction(act)
        elif isinstance(item, EdgeItem):
            act = QAction("Delete Transition", menu)
            act.triggered.connect(lambda: self._delete_edge_item(item))
            menu.addAction(act)
        else:
            scene_pos = event.scenePos()
            act = QAction("Add State Here", menu)
            act.triggered.connect(
                lambda: self.add_state(QPointF(scene_pos.x() - NODE_W / 2, scene_pos.y() - NODE_H / 2))
            )
            menu.addAction(act)
        menu.exec(event.screenPos().toPoint())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _node_near_port_out(self, pos: QPointF) -> tuple[NodeItem, str | None] | None:
        for node in self._node_items.values():
            if node._returns:
                for name in node._returns:
                    if _dist(pos, node.port_out_for(name)) <= _PORT_HIT_RADIUS:
                        return (node, name)
            else:
                if _dist(pos, node.port_out()) <= _PORT_HIT_RADIUS:
                    return (node, None)
        return None

    def _node_near_port_in(self, pos: QPointF) -> NodeItem | None:
        for node in self._node_items.values():
            if _dist(pos, node.port_in()) <= _PORT_HIT_RADIUS:
                return node
        return None

    def _create_transition(
        self, src_node: NodeItem, dst_node: NodeItem, when: str | None = None
    ) -> None:
        if self._config is None:
            return
        s, d = src_node.state_id, dst_node.state_id
        if any(t.source == s and t.dest == d and t.when == when for t in self._config.transitions):
            return
        t = TransitionModel(trigger="advance", source=s, dest=d, when=when)
        self._config.transitions.append(t)

        reverse_exists = any(es == d and ed == s for (es, _ew, ed, _ei) in self._edge_items)
        if reverse_exists:
            for (es, ew, ed, existing) in self._edge_items:
                if es == d and ed == s:
                    existing._offset = -22.0
                    existing._rebuild()
            offset = 22.0
        else:
            offset = 0.0

        edge = EdgeItem(src_node, dst_node, offset=offset, when=when)
        self.addItem(edge)
        src_node.add_edge(edge)
        if dst_node is not src_node:
            dst_node.add_edge(edge)
        self._edge_items.append((s, when, d, edge))
        self._update_scene_rect()
        self.refresh_terminal()
        self.refresh_errors()
        self.config_changed.emit()

    def _delete_selected(self) -> None:
        changed = False
        for item in list(self.selectedItems()):
            if isinstance(item, NodeItem):
                self._delete_node(item.state_id)
                changed = True
            elif isinstance(item, EdgeItem):
                self._delete_edge_item(item)
                changed = True
        if changed:
            self.config_changed.emit()

    def _delete_node(self, state_id: str) -> None:
        if self._config is None:
            return
        self._config.transitions = [
            t for t in self._config.transitions
            if t.source != state_id and t.dest != state_id
        ]
        remaining = []
        for (s, w, d, edge_item) in self._edge_items:
            if s == state_id or d == state_id:
                s_node = self._node_items.get(s)
                d_node = self._node_items.get(d)
                if s_node:
                    s_node.remove_edge(edge_item)
                if d_node and d_node is not s_node:
                    d_node.remove_edge(edge_item)
                self.removeItem(edge_item)
            else:
                remaining.append((s, w, d, edge_item))
        self._edge_items = remaining
        self._config.states = [s for s in self._config.states if s.id != state_id]
        if state_id in self._node_items:
            self.removeItem(self._node_items.pop(state_id))
        self.refresh_terminal()
        self.refresh_errors()
        self._update_scene_rect()
        self.config_changed.emit()

    def _delete_edge_item(self, edge_item: EdgeItem) -> None:
        if self._config is None:
            return
        remaining = []
        for (s, w, d, ei) in self._edge_items:
            if ei is edge_item:
                s_node = self._node_items.get(s)
                d_node = self._node_items.get(d)
                if s_node:
                    s_node.remove_edge(ei)
                if d_node and d_node is not s_node:
                    d_node.remove_edge(ei)
                self._config.transitions = [
                    t for t in self._config.transitions
                    if not (t.source == s and t.dest == d and t.when == w)
                ]
                self.removeItem(ei)
            else:
                remaining.append((s, w, d, ei))
        self._edge_items = remaining
        self.refresh_terminal()
        self.refresh_errors()
        self.config_changed.emit()

    def _flush_positions(self) -> None:
        if self._config is None:
            return
        changed = False
        for state in self._config.states:
            node = self._node_items.get(state.id)
            if node is None:
                continue
            new_pos = {
                "x": node.scenePos().x() + NODE_W / 2,
                "y": node.scenePos().y() + node._height / 2,
            }
            if state.position != new_pos:
                state.position = new_pos
                changed = True
        if changed:
            self.config_changed.emit()

    def _update_scene_rect(self) -> None:
        bounds = self.itemsBoundingRect().adjusted(-80, -100, 80, 60)
        self.setSceneRect(bounds)


# ---------------------------------------------------------------------------
# _EditView
# ---------------------------------------------------------------------------

class _EditView(GraphView):
    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, interactive=True, parent=parent)


# ---------------------------------------------------------------------------
# BotCanvas — unified widget
# ---------------------------------------------------------------------------

class BotCanvas(QWidget):
    """Single canvas widget covering both Run and Edit modes."""

    config_changed = pyqtSignal()
    mode_changed   = pyqtSignal(BotMode)
    run_requested  = pyqtSignal()
    pause_requested  = pyqtSignal()
    resume_requested = pyqtSignal()
    save_done = pyqtSignal()
    # Emitted with "log", "properties", or "bot" to switch the sidebar tab
    tab_switch = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._mode = BotMode.RUN
        self._run_state = _IDLE
        self._start_time: float | None = None
        self._active_state: str | None = None
        self._tick_count: int = 0
        self._run_externally_enabled: bool = False
        self._n_validation_errors: int = 0

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._scene = EditScene()
        self._view = _EditView(self._scene)
        self._param_panel = _ParamPanel()

        # ── Run-mode toolbar ─────────────────────────────────────────
        self._run_bar = QWidget()
        run_row = QHBoxLayout(self._run_bar)
        run_row.setContentsMargins(4, 4, 4, 4)

        self._run_pause_btn = QPushButton("▶  Run")
        self._run_pause_btn.setFixedWidth(110)
        self._run_pause_btn.setEnabled(False)
        self._run_pause_btn.clicked.connect(self._on_run_pause_clicked)

        self.stop_button = QPushButton("■  Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.setFixedWidth(90)

        self._state_label = QLabel("State: Idle")
        self._elapsed_label = QLabel("00:00:00")
        self._elapsed_label.setFont(QFont("Consolas", 10))
        self._elapsed_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._edit_btn = QPushButton("Edit")
        self._edit_btn.setFixedWidth(80)
        self._edit_btn.setEnabled(False)
        self._edit_btn.clicked.connect(self._on_edit_clicked)

        self._fit_btn_run = QPushButton("Fit to Canvas")
        self._fit_btn_run.setFixedWidth(100)
        self._fit_btn_run.clicked.connect(self._view.fit_scene)

        self._validation_badge = QLabel()
        self._validation_badge.setTextFormat(Qt.TextFormat.RichText)
        self._validation_badge.setVisible(False)

        run_row.addWidget(self._run_pause_btn)
        run_row.addSpacing(4)
        run_row.addWidget(self.stop_button)
        run_row.addSpacing(6)
        run_row.addWidget(self._validation_badge)
        run_row.addWidget(self._state_label, 1)
        run_row.addWidget(self._elapsed_label)
        run_row.addSpacing(8)
        run_row.addWidget(self._fit_btn_run)
        run_row.addSpacing(12)
        run_row.addWidget(self._edit_btn)

        # ── Edit-mode toolbar ─────────────────────────────────────────
        self._edit_bar = QWidget()
        edit_row = QHBoxLayout(self._edit_bar)
        edit_row.setContentsMargins(4, 4, 4, 4)

        self._add_state_btn = QPushButton("+ Add State")
        self._add_state_btn.setFixedWidth(110)
        self._add_state_btn.clicked.connect(self._on_add_state)

        self._fit_btn_edit = QPushButton("Fit to Canvas")
        self._fit_btn_edit.setFixedWidth(100)
        self._fit_btn_edit.clicked.connect(self._view.fit_scene)

        editing_lbl = QLabel("Editing — run disabled")
        editing_lbl.setStyleSheet("color: #996600;")

        self._done_btn = QPushButton("Done")
        self._done_btn.setFixedWidth(90)
        self._done_btn.setToolTip("Apply changes and return to Run mode (does not save to disk)")
        self._done_btn.clicked.connect(self._on_done_clicked)

        self._save_done_btn = QPushButton("Save and Done")
        self._save_done_btn.setFixedWidth(130)
        self._save_done_btn.setToolTip("Save to disk and return to Run mode")
        self._save_done_btn.clicked.connect(self._on_save_done_clicked)

        edit_row.addWidget(self._add_state_btn)
        edit_row.addSpacing(4)
        edit_row.addWidget(self._fit_btn_edit)
        edit_row.addStretch(1)
        edit_row.addWidget(editing_lbl)
        edit_row.addSpacing(12)
        edit_row.addWidget(self._done_btn)
        edit_row.addSpacing(4)
        edit_row.addWidget(self._save_done_btn)

        # ── Toolbar stack ─────────────────────────────────────────────
        self._toolbar_stack = QStackedWidget()
        self._toolbar_stack.addWidget(self._run_bar)   # _TB_RUN  = 0
        self._toolbar_stack.addWidget(self._edit_bar)  # _TB_EDIT = 1
        self._toolbar_stack.setCurrentIndex(_TB_RUN)

        # ── Log pane (owned here, displayed in left sidebar Log tab) ──
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 9))

        log_header = QWidget()
        log_header_row = QHBoxLayout(log_header)
        log_header_row.setContentsMargins(4, 2, 4, 2)
        log_header_row.addWidget(QLabel("Min level:"))
        self._log_level_combo = QComboBox()
        self._log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self._log_level_combo.setCurrentText("INFO")
        self._log_level_combo.setFixedWidth(90)
        log_header_row.addWidget(self._log_level_combo)
        log_header_row.addStretch()

        self._log_widget = QWidget()
        log_vbox = QVBoxLayout(self._log_widget)
        log_vbox.setContentsMargins(0, 0, 0, 0)
        log_vbox.setSpacing(0)
        log_vbox.addWidget(log_header)
        log_vbox.addWidget(self._log, 1)

        # ── Main layout: toolbar + graph view only ────────────────────
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._toolbar_stack)
        outer.addWidget(self._view, 1)

        # ── Keyboard shortcuts ────────────────────────────────────────
        fit_shortcut = QShortcut(QKeySequence("Ctrl+0"), self)
        fit_shortcut.activated.connect(self._view.fit_scene)

        # ── Wire internal signals ─────────────────────────────────────
        self._scene.config_changed.connect(self._on_scene_changed)
        self._param_panel.config_changed.connect(self._on_scene_changed)
        self._scene.selectionChanged.connect(self._on_selection_changed)

    # ------------------------------------------------------------------
    # Public API — bot loading
    # ------------------------------------------------------------------

    def setup(self, config: BotConfig) -> None:
        """Load a BotConfig. Enters RUN mode with the graph ready."""
        _inject_required_options(config)
        self._config = config
        self._scene.setup(config)
        self._param_panel.show_idle()
        self._view.fit_scene()
        self._apply_mode(BotMode.RUN)
        self._edit_btn.setEnabled(True)
        self._update_validation_badge()
        self.mode_changed.emit(BotMode.RUN)

    # ------------------------------------------------------------------
    # Public API — run state management
    # ------------------------------------------------------------------

    def set_run_enabled(self, enabled: bool) -> None:
        """Enable/disable the Run button (only when idle and in RUN mode)."""
        self._run_externally_enabled = enabled
        self._apply_run_btn_state()

    def _apply_run_btn_state(self) -> None:
        """Sync Run button enabled/tooltip from external readiness + validation errors."""
        if self._run_state != _IDLE or self._mode != BotMode.RUN:
            return
        if self._n_validation_errors > 0:
            self._run_pause_btn.setEnabled(False)
            noun = "error" if self._n_validation_errors == 1 else "errors"
            self._run_pause_btn.setToolTip(
                f"Cannot run: fix {self._n_validation_errors} {noun} first"
            )
        elif self._run_externally_enabled:
            self._run_pause_btn.setEnabled(True)
            self._run_pause_btn.setToolTip("Bot is valid — click to run")
        else:
            self._run_pause_btn.setEnabled(False)
            self._run_pause_btn.setToolTip("")

    @property
    def param_panel(self) -> QWidget:
        """The _ParamPanel widget — placed in the left sidebar Properties tab."""
        return self._param_panel

    @property
    def log_widget(self) -> QWidget:
        """The log container widget — placed in the left sidebar Log tab."""
        return self._log_widget

    def set_clients_getter(self, fn: Callable[[], list]) -> None:
        """Pass a callable that returns the current list of hooked clients."""
        self._param_panel._get_clients = fn

    def set_running(self) -> None:
        self._run_state = _RUNNING
        self._tick_count = 0
        self._run_pause_btn.setText("⏸  Pause")
        self._run_pause_btn.setEnabled(True)
        self.stop_button.setEnabled(True)
        self._edit_btn.setEnabled(False)
        self._start_time = time.monotonic()
        self._elapsed_label.setText("00:00:00")
        self._tick_timer.start()
        self.tab_switch.emit("log")
        self.append_log("=== Bot started ===")

    def set_paused(self) -> None:
        self._run_state = _PAUSED
        self._run_pause_btn.setText("▶  Resume")
        self._run_pause_btn.setEnabled(True)
        self.append_log("=== Paused ===")

    def set_idle(self) -> None:
        self._run_state = _IDLE
        self._run_pause_btn.setText("▶  Run")
        self._run_pause_btn.setEnabled(False)  # re-enabled by set_run_enabled
        self.stop_button.setEnabled(False)
        self._edit_btn.setEnabled(self._config is not None)
        self._tick_timer.stop()
        self._state_label.setText("State: Idle")
        if self._active_state and self._active_state in self._scene._node_items:
            self._scene._node_items[self._active_state].set_active(False)
        self._active_state = None
        self.append_log("=== Bot stopped ===")

    def set_state(self, state_id: str) -> None:
        """Highlight the active FSM state node."""
        if self._active_state and self._active_state in self._scene._node_items:
            self._scene._node_items[self._active_state].set_active(False)
        self._active_state = state_id
        self._tick_count = 0  # restart pulse phase on state change
        if state_id in self._scene._node_items:
            self._scene._node_items[state_id].set_active(True)
        self._state_label.setText(f"State: {state_id}")
        self.append_log(f"Entered: {state_id}")

    def on_transition(self, prev_state: str, result: str, next_state: str) -> None:
        if prev_state == next_state:
            self.append_log(f"Result: {result} → (retry {next_state})")
        else:
            self.append_log(f"Result: {result} → {next_state}")

    # ------------------------------------------------------------------
    # Toolbar button handlers
    # ------------------------------------------------------------------

    def _on_run_pause_clicked(self) -> None:
        if self._run_state == _IDLE:
            self.run_requested.emit()
        elif self._run_state == _RUNNING:
            self.set_paused()
            self.pause_requested.emit()
        elif self._run_state == _PAUSED:
            self._run_state = _RUNNING
            self._run_pause_btn.setText("⏸  Pause")
            self.append_log("=== Resumed ===")
            self.resume_requested.emit()

    def enter_edit_mode(self) -> None:
        """Switch to Edit mode (no-op if the bot is currently running)."""
        if self._run_state != _IDLE:
            return
        self._apply_mode(BotMode.EDIT)
        self.mode_changed.emit(BotMode.EDIT)

    def _on_edit_clicked(self) -> None:
        self.enter_edit_mode()

    def _on_done_clicked(self) -> None:
        self._apply_mode(BotMode.RUN)
        self.mode_changed.emit(BotMode.RUN)
        self.config_changed.emit()

    def _on_save_done_clicked(self) -> None:
        self._apply_mode(BotMode.RUN)
        self.mode_changed.emit(BotMode.RUN)
        self.save_done.emit()

    def _on_add_state(self) -> None:
        center = self._view.mapToScene(self._view.viewport().rect().center())
        self._scene.add_state(QPointF(center.x() - NODE_W / 2, center.y() - NODE_H / 2))

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def _apply_mode(self, mode: BotMode) -> None:
        self._mode = mode
        if mode == BotMode.RUN:
            self._toolbar_stack.setCurrentIndex(_TB_RUN)
            self._scene.set_interactive(False)
            self._param_panel.show_idle()
            self.tab_switch.emit("bot")
        else:
            self._toolbar_stack.setCurrentIndex(_TB_EDIT)
            self._scene.set_interactive(True)
            self.tab_switch.emit("properties")

    # ------------------------------------------------------------------
    # Scene change callbacks
    # ------------------------------------------------------------------

    def refresh_initial(self, initial_id: str) -> None:
        """Propagate initial state change to the canvas node indicator."""
        self._scene.refresh_initial(initial_id)

    def _on_scene_changed(self) -> None:
        if self._scene._config is not None:
            _inject_required_options(self._scene._config)
        self._refresh_node_labels()
        self._refresh_node_returns()
        self._refresh_initial()
        self._refresh_terminal()
        self._refresh_errors()
        self._update_validation_badge()
        self.config_changed.emit()

    def _update_validation_badge(self) -> None:
        config = self._scene._config
        if config is None:
            self._validation_badge.setVisible(False)
            return
        n_errors = 0
        n_warnings = 0
        for state in config.states:
            is_error, is_warning, _ = _validate_node(state, config)
            if is_error:
                n_errors += 1
            if is_warning:
                n_warnings += 1
        parts = []
        if n_errors:
            noun = "error" if n_errors == 1 else "errors"
            parts.append(f'<span style="color: #CC2222;">&#9679; {n_errors} {noun}</span>')
        if n_warnings:
            noun = "warning" if n_warnings == 1 else "warnings"
            parts.append(f'<span style="color: #BB7700;">&#9650; {n_warnings} {noun}</span>')
        if parts:
            self._validation_badge.setText("&nbsp;&nbsp;".join(parts))
            self._validation_badge.setVisible(True)
        else:
            self._validation_badge.setVisible(False)
        self._n_validation_errors = n_errors
        self._apply_run_btn_state()

    def _refresh_initial(self) -> None:
        if self._scene._config is None:
            return
        self._scene.refresh_initial(self._scene._config.initial)

    def _refresh_terminal(self) -> None:
        self._scene.refresh_terminal()

    def _refresh_errors(self) -> None:
        self._scene.refresh_errors()

    def _refresh_node_labels(self) -> None:
        if self._scene._config is None:
            return
        for state in self._scene._config.states:
            node = self._scene._node_items.get(state.id)
            if node:
                node.set_label(state.label)
                node.set_node_type(state.node_type)

    def _refresh_node_returns(self) -> None:
        if self._scene._config is None:
            return
        for state in self._scene._config.states:
            node = self._scene._node_items.get(state.id)
            if node:
                returns = _node_returns(state.node_type)
                if node._returns != returns:
                    node.set_returns(returns)
                    for (s, w, d, edge) in self._scene._edge_items:
                        if s == state.id:
                            edge._rebuild()

    def _on_selection_changed(self) -> None:
        try:
            selected = self._scene.selectedItems()
        except RuntimeError:
            return
        if not selected:
            self._param_panel.show_idle()
            return
        item = selected[0]
        if isinstance(item, NodeItem) and self._scene._config:
            state = next(
                (s for s in self._scene._config.states if s.id == item.state_id), None
            )
            if state:
                self._param_panel.show_state(state)
                self.tab_switch.emit("properties")
        elif isinstance(item, EdgeItem) and self._scene._config:
            for (s, w, d, ei) in self._scene._edge_items:
                if ei is item:
                    trans = next(
                        (t for t in self._scene._config.transitions
                         if t.source == s and t.dest == d and t.when == w),
                        None,
                    )
                    if trans:
                        src_state = next(
                            (st for st in self._scene._config.states if st.id == s), None
                        )
                        src_returns: list[str] = []
                        if src_state:
                            spec = NODE_SPECS.get(src_state.node_type)
                            if spec:
                                src_returns = [r.name for r in spec.returns]
                        option_names = [
                            opt.name for opt in self._scene._config.options
                        ]
                        self._param_panel.show_transition(trans, src_returns, option_names)
                        self.tab_switch.emit("properties")
                    break
        else:
            self._param_panel.show_idle()

    # ------------------------------------------------------------------
    # Log helpers
    # ------------------------------------------------------------------

    def append_log(self, msg: str, level: LogLevel = LogLevel.INFO) -> None:
        """Public log entry point for node messages. Filtered by the level dropdown."""
        threshold = LogLevel[self._log_level_combo.currentText()]
        if level < threshold:
            return
        prefix = _LOG_PREFIXES.get(level, "[INF]")
        color = _LOG_COLORS.get(level, "#55cc55")

        fmt_plain = QTextCharFormat()
        fmt_colored = QTextCharFormat()
        fmt_colored.setForeground(QColor(color))

        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(f"[{self._elapsed_str()}] ", fmt_plain)
        cursor.insertText(prefix, fmt_colored)
        cursor.insertText(f" {msg}\n", fmt_plain)
        self._log.moveCursor(QTextCursor.MoveOperation.End)

    def _append_log(self, text: str) -> None:
        self._log.appendPlainText(text)
        self._log.moveCursor(QTextCursor.MoveOperation.End)

    def _tick(self) -> None:
        self._elapsed_label.setText(self._elapsed_str())
        self._tick_count += 1
        if self._run_state == _RUNNING and self._active_state:
            node = self._scene._node_items.get(self._active_state)
            if node:
                node.set_pulse(self._tick_count % 2 == 0)

    def _elapsed_str(self) -> str:
        if self._start_time is None:
            return "00:00:00"
        s = int(time.monotonic() - self._start_time)
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{sec:02d}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node_returns(node_type: str) -> list[str]:
    spec = NODE_SPECS.get(node_type)
    return [r.name for r in spec.returns] if spec else []


def _required_field_empty(field, params: dict) -> bool:
    """Return True if a required ParamField has no meaningful value in params."""
    if field.key not in params:
        return True
    val = params[field.key]
    if field.type in ("str", "text"):
        return not str(val).strip()
    if field.type in ("int_list", "str_list"):
        return not val  # empty list
    return False  # int, float, bool, xyz always have a value


def _validate_node(state, config) -> tuple[bool, bool, list[str]]:
    """Return (is_error, is_warning, reasons) for a state given the full config.

    Error conditions:
      - node_type not in NODE_REGISTRY (unknown type)
      - any required param key absent from state.params
      - any outgoing transition points to a non-existent destination state

    Warning conditions:
      - node has outgoing transitions but some return values are not covered
        by any t.when (and no unconditional when=None transition exists)
    """
    error_reasons: list[str] = []
    warning_reasons: list[str] = []

    if state.node_type not in NODE_REGISTRY:
        error_reasons.append(f"Unknown node type: '{state.node_type}'")

    schema = NODE_PARAM_SCHEMAS.get(state.node_type, [])
    for field in schema:
        if field.required and _required_field_empty(field, state.params):
            error_reasons.append(f"Required param '{field.key}' is empty")

    state_ids = {s.id for s in config.states}
    outgoing = [t for t in config.transitions if t.source == state.id]
    for t in outgoing:
        if t.dest not in state_ids:
            error_reasons.append(f"Transition to unknown state '{t.dest}'")

    if outgoing:
        spec = NODE_SPECS.get(state.node_type)
        if spec and spec.returns:
            outgoing_whens = {t.when for t in outgoing}
            if None not in outgoing_whens:
                uncovered = [r.name for r in spec.returns if r.name not in outgoing_whens]
                if uncovered:
                    label = "return value" if len(uncovered) == 1 else "return values"
                    warning_reasons.append(
                        f"Uncovered {label}: {', '.join(repr(r) for r in uncovered)}"
                    )

    return bool(error_reasons), bool(warning_reasons), error_reasons + warning_reasons


def _dist(a: QPointF, b: QPointF) -> float:
    return math.hypot(a.x() - b.x(), a.y() - b.y())


def _unique_id(existing: list[str]) -> str:
    i = 1
    while f"state_{i}" in existing:
        i += 1
    return f"state_{i}"
