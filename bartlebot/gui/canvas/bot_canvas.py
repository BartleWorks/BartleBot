"""Unified bot canvas — single QGraphicsScene with Run and Edit modes.

BotCanvas  — top-level widget; owns the scene, toolbars, properties panel,
             and run log. Replaces the former EditTab + RunningTab pair.
EditScene  — interactive QGraphicsScene (same logic as the old edit_tab.py).
"""

import asyncio
import enum
import json
import math
import time
from typing import Any, Callable

from PyQt6.QtCore import Qt, QPointF, QTimer
from PyQt6.QtGui import QAction, QColor, QFont, QPen, QKeyEvent, QMouseEvent, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QGraphicsItem, QGraphicsLineItem, QGraphicsScene, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QSplitter, QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)

from bartlebot.bot.schema import BotConfig, BotOptionModel, StateModel, TransitionModel
from bartlebot.log_level import LogLevel
from bartlebot.gui.canvas.graph_items import (
    EdgeItem, GraphView, NodeItem, NODE_H, NODE_W, PORT_R,
)
from bartlebot.nodes.registry import NODE_REGISTRY, NODE_PARAM_SCHEMAS, NODE_SPECS


class _IntListLineEdit(QLineEdit):
    """QLineEdit variant used for 'int_list' param fields.

    Stores values as a comma-separated string (e.g. "1, 2, 3") and is
    identified by get_params() to deserialise back to list[int].
    """


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PORT_HIT_RADIUS = 16
_AUTO_SPACING = 260

_TB_RUN  = 0  # toolbar stack index for Run mode
_TB_EDIT = 1  # toolbar stack index for Edit mode
_TAB_PROPS = 0  # bottom tab index for Properties
_TAB_LOG   = 1  # bottom tab index for Log

_IDLE    = "idle"
_RUNNING = "running"
_PAUSED  = "paused"

_RECORDABLE_NODES: set[str] = {"go_to_zone", "enter_dungeon"}

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
    """Ensure all bot options required by node specs are present in config.options.

    Scans every state in the config, reads required_bot_options from the matching
    NodeSpec, and appends any missing options. Existing options (by name) are never
    overwritten so user-configured values are preserved.
    """
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._node_items: dict[str, NodeItem] = {}
        self._edge_items: list[tuple[str, str | None, str, EdgeItem]] = []
        self._drag_src: NodeItem | None = None
        self._drag_when: str | None = None
        self._temp_edge: QGraphicsLineItem | None = None
        self._interactive_mode: bool = True
        self.on_config_changed: Callable[[], None] = lambda: None
        self.on_selection_changed_cb: Callable[[], None] = lambda: None
        self.selectionChanged.connect(self.on_selection_changed_cb)

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
            is_error, is_warning = _validate_node(state, config)
            node = NodeItem(
                state.id, state.label,
                interactive=self._interactive_mode,
                returns=returns,
                is_initial=(state.id == config.initial),
                is_terminal=(state.id not in sources),
                is_error=is_error,
                is_warning=is_warning,
            )
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
            is_error, is_warning = _validate_node(state, self._config)
            node.set_error(is_error)
            node.set_warning(is_warning)

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
        node = NodeItem(state_id, None, interactive=True, returns=returns, is_terminal=True)
        node.setPos(pos)
        self.addItem(node)
        self._node_items[state_id] = node
        self._update_scene_rect()
        self.on_config_changed()

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
        self.on_config_changed()

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
            self.on_config_changed()

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
        self.on_config_changed()

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
        self.on_config_changed()

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
            self.on_config_changed()

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
# _DynamicParamForm
# ---------------------------------------------------------------------------

class _DynamicParamForm(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._form = QFormLayout(self)
        self._form.setContentsMargins(6, 6, 6, 6)
        self._form.setSpacing(6)
        self._widgets: dict[str, Any] = {}
        self._required_widgets: dict[str, Any] = {}
        self._suppress = False
        self.on_changed: Callable[[], None] = lambda: None

    def load_schema(self, schema: list, params: dict) -> None:
        self._suppress = True
        while self._form.rowCount() > 0:
            self._form.removeRow(0)
        self._widgets.clear()
        self._required_widgets.clear()
        for field in schema:
            label_text = field.label
            if field.required:
                label_text += " <span style='color:#CC2222'>*</span>"
            label_w = QLabel(label_text)
            label_w.setTextFormat(Qt.TextFormat.RichText)
            if field.help:
                label_w.setToolTip(field.help)
            val = params.get(field.key, field.default)
            widget = self._make_widget(field.type, val, field.help, field.key)
            self._widgets[field.key] = widget
            if field.required:
                self._required_widgets[field.key] = widget
            display_w = widget if not isinstance(widget, tuple) else widget[3]
            if field.default_hint and not field.required:
                wrapper = QWidget()
                vbox = QVBoxLayout(wrapper)
                vbox.setContentsMargins(0, 0, 0, 0)
                vbox.setSpacing(2)
                vbox.addWidget(display_w)
                hint_lbl = QLabel(field.default_hint)
                hint_lbl.setStyleSheet("color: #888;")
                hint_lbl.setWordWrap(True)
                vbox.addWidget(hint_lbl)
                self._form.addRow(label_w, wrapper)
            else:
                self._form.addRow(label_w, display_w)
        if not schema:
            placeholder = QLabel("This node type has no parameters.")
            placeholder.setStyleSheet("color: #888;")
            self._form.addRow(placeholder)
        # Wire disabled_by: when controller checkbox is checked, disable the target widget
        for field in schema:
            if field.disabled_by and field.disabled_by in self._widgets:
                controller = self._widgets[field.disabled_by]
                controlled = self._widgets[field.key]
                display_w = controlled if not isinstance(controlled, tuple) else controlled[3]
                if isinstance(controller, QCheckBox):
                    display_w.setEnabled(not controller.isChecked())
                    controller.checkStateChanged.connect(
                        lambda state, w=display_w: w.setEnabled(
                            state != Qt.CheckState.Checked
                        )
                    )
        self._suppress = False
        self._validate_required_fields()

    def _validate_required_fields(self) -> None:
        """Apply or clear a red border on required input widgets based on whether they are empty."""
        for widget in self._required_widgets.values():
            if isinstance(widget, tuple):
                # xyz — cannot be meaningfully empty
                display_w = widget[3]
                empty = False
            elif isinstance(widget, QPlainTextEdit):
                display_w = widget
                empty = not widget.toPlainText().strip()
            elif isinstance(widget, QLineEdit):
                display_w = widget
                empty = not widget.text().strip()
            else:
                # QSpinBox, QDoubleSpinBox, QCheckBox — always have a value
                display_w = widget
                empty = False
            display_w.setStyleSheet("border: 1px solid #CC2222;" if empty else "")

    def get_params(self) -> dict:
        result: dict[str, Any] = {}
        for key, w in self._widgets.items():
            if isinstance(w, tuple):
                x_s, y_s, z_s, _ = w
                result[key] = {"x": x_s.value(), "y": y_s.value(), "z": z_s.value()}
            elif isinstance(w, QPlainTextEdit):
                result[key] = w.toPlainText()
            elif isinstance(w, _IntListLineEdit):
                result[key] = [
                    int(x.strip()) for x in w.text().split(",") if x.strip().lstrip("-").isdigit()
                ]
            elif isinstance(w, QLineEdit):
                result[key] = w.text()
            elif isinstance(w, QSpinBox):
                result[key] = w.value()
            elif isinstance(w, QDoubleSpinBox):
                result[key] = w.value()
            elif isinstance(w, QCheckBox):
                result[key] = w.isChecked()
        return result

    def _make_widget(self, field_type: str, val: Any, help_text: str, key: str = "") -> Any:
        if field_type == "text":
            w = QPlainTextEdit()
            w.setPlainText(str(val) if val is not None else "")
            w.setFixedHeight(120)
            w.setPlaceholderText("Enter combat DSL…")
            if help_text:
                w.setToolTip(help_text)
            w.textChanged.connect(self._emit_changed)
            return w
        if field_type == "str":
            w = QLineEdit()
            w.setText(str(val) if val is not None else "")
            if help_text:
                w.setToolTip(help_text)
            w.textChanged.connect(self._emit_changed)
            return w
        if field_type == "int_list":
            w = _IntListLineEdit()
            if isinstance(val, list):
                w.setText(", ".join(str(v) for v in val))
            else:
                w.setText(str(val) if val else "")
            w.setPlaceholderText("e.g. 1, 2, 3")
            if help_text:
                w.setToolTip(help_text)
            w.textChanged.connect(self._emit_changed)
            return w
        if field_type == "int":
            w = QSpinBox()
            w.setRange(-999_999, 999_999)
            try:
                w.setValue(int(val) if val is not None else 0)
            except (TypeError, ValueError):
                w.setValue(0)
            if help_text:
                w.setToolTip(help_text)
            w.valueChanged.connect(self._emit_changed)
            return w
        if field_type == "float":
            w = QDoubleSpinBox()
            w.setRange(-999_999.0, 999_999.0)
            w.setDecimals(3)
            w.setSingleStep(1.0)
            try:
                w.setValue(float(val) if val is not None else 0.0)
            except (TypeError, ValueError):
                w.setValue(0.0)
            if help_text:
                w.setToolTip(help_text)
            w.valueChanged.connect(self._emit_changed)
            return w
        if field_type == "bool":
            w = QCheckBox()
            w.setChecked(bool(val) if val is not None else False)
            if help_text:
                w.setToolTip(help_text)
            w.checkStateChanged.connect(self._emit_changed)
            return w
        if field_type == "xyz":
            xyz = val if isinstance(val, dict) else {}
            container = QWidget()
            row = QHBoxLayout(container)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            spins = []
            for axis, axis_key in [("X", "x"), ("Y", "y"), ("Z", "z")]:
                lbl = QLabel(axis)
                lbl.setFixedWidth(12)
                spin = QDoubleSpinBox()
                spin.setRange(-999_999.0, 999_999.0)
                spin.setDecimals(2)
                spin.setSingleStep(1.0)
                try:
                    spin.setValue(float(xyz.get(axis_key, 0.0)))
                except (TypeError, ValueError):
                    spin.setValue(0.0)
                spin.valueChanged.connect(self._emit_changed)
                row.addWidget(lbl)
                row.addWidget(spin, 1)
                spins.append(spin)
            if help_text:
                container.setToolTip(help_text)
            paste_btn = QPushButton("Paste")
            paste_btn.setFixedWidth(48)
            paste_btn.setToolTip("Fill from clipboard (Copy XYZ format)")
            paste_btn.clicked.connect(lambda _checked, k=key: self._paste_xyz(k))
            row.addWidget(paste_btn)
            return (spins[0], spins[1], spins[2], container)
        w = QLabel(f"(unsupported type: {field_type})")
        w.setStyleSheet("color: #888;")
        return w

    def _emit_changed(self, *_) -> None:
        if not self._suppress:
            self._validate_required_fields()
            self.on_changed()

    def _paste_xyz(self, key: str) -> None:
        try:
            data = json.loads(QApplication.clipboard().text().strip())
        except (json.JSONDecodeError, AttributeError):
            return
        if not isinstance(data, dict):
            return
        widgets = self._widgets.get(key)
        if not isinstance(widgets, tuple):
            return
        x_s, y_s, z_s, _ = widgets
        self._suppress = True
        try:
            x_s.setValue(float(data["x"]))
        except (KeyError, TypeError, ValueError):
            pass
        try:
            y_s.setValue(float(data["y"]))
        except (KeyError, TypeError, ValueError):
            pass
        try:
            z_s.setValue(float(data["z"]))
        except (KeyError, TypeError, ValueError):
            pass
        self._suppress = False
        self._emit_changed()

    def set_str(self, key: str, value: str) -> None:
        """Programmatically set a 'str' field by key."""
        widget = self._widgets.get(key)
        if not isinstance(widget, QLineEdit):
            return
        self._suppress = True
        widget.setText(value)
        self._suppress = False
        self._emit_changed()

    def set_xyz(self, key: str, x: float, y: float, z: float) -> None:
        """Programmatically set an 'xyz' field by key."""
        widgets = self._widgets.get(key)
        if not isinstance(widgets, tuple):
            return
        x_s, y_s, z_s, _ = widgets
        self._suppress = True
        x_s.setValue(x)
        y_s.setValue(y)
        z_s.setValue(z)
        self._suppress = False
        self._emit_changed()


# ---------------------------------------------------------------------------
# Per-node option-override helpers
# ---------------------------------------------------------------------------

def _make_override_widget(type_str: str, val: Any, help_text: str = "") -> QWidget:
    """Create a value input widget for a bot-option override row."""
    w: QWidget
    if type_str == "bool":
        wb = QCheckBox()
        wb.setChecked(bool(val) if val is not None else False)
        w = wb
    elif type_str == "int":
        wi = QSpinBox()
        wi.setRange(-999_999, 999_999)
        try:
            wi.setValue(int(val) if val is not None else 0)
        except (TypeError, ValueError):
            wi.setValue(0)
        w = wi
    elif type_str == "float":
        wf = QDoubleSpinBox()
        wf.setRange(-999_999.0, 999_999.0)
        wf.setDecimals(3)
        wf.setSingleStep(0.1)
        try:
            wf.setValue(float(val) if val is not None else 0.0)
        except (TypeError, ValueError):
            wf.setValue(0.0)
        w = wf
    else:  # str
        ws = QLineEdit()
        ws.setText(str(val) if val is not None else "")
        w = ws
    if help_text:
        w.setToolTip(help_text)
    return w


def _read_override_widget(w: QWidget) -> Any:
    if isinstance(w, QCheckBox):
        return w.isChecked()
    if isinstance(w, QSpinBox):
        return w.value()
    if isinstance(w, QDoubleSpinBox):
        return w.value()
    if isinstance(w, QLineEdit):
        return w.text()
    return None


def _connect_override_widget(w: QWidget, callback: Callable[[], None]) -> None:
    if isinstance(w, QCheckBox):
        w.checkStateChanged.connect(lambda _: callback())
    elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
        w.valueChanged.connect(lambda _: callback())
    elif isinstance(w, QLineEdit):
        w.textChanged.connect(lambda _: callback())


# ---------------------------------------------------------------------------
# _ParamPanel
# ---------------------------------------------------------------------------

_PAGE_IDLE = 0
_PAGE_NODE = 1
_PAGE_EDGE = 2


class _ParamPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: StateModel | None = None
        self._transition: TransitionModel | None = None
        self._suppress: bool = False
        self.on_config_changed: Callable[[], None] = lambda: None
        self._get_clients: Callable[[], list] = lambda: []
        self._record_task: asyncio.Task | None = None

        self._stack = QStackedWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._stack)

        # Page 0 — idle
        idle = QLabel("Select a node or edge to edit its properties.")
        idle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        idle.setStyleSheet("color: #888;")
        self._stack.addWidget(idle)

        # Page 1 — node
        node_w = QWidget()
        nf = QFormLayout(node_w)
        nf.setContentsMargins(6, 6, 6, 6)
        self._node_id_lbl = QLabel()
        self._node_label_edit = QLineEdit()
        self._node_type_combo = QComboBox()
        self._node_type_combo.addItems(sorted(NODE_REGISTRY.keys()))
        nf.addRow("ID", self._node_id_lbl)
        nf.addRow("Label", self._node_label_edit)
        nf.addRow("Node type", self._node_type_combo)

        self._param_form = _DynamicParamForm()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setWidget(self._param_form)

        self._returns_section = QWidget()
        returns_layout = QVBoxLayout(self._returns_section)
        returns_layout.setContentsMargins(6, 4, 6, 4)
        returns_layout.setSpacing(2)
        returns_title = QLabel("Returns:")
        returns_title.setStyleSheet("font-weight: bold; color: #555;")
        returns_layout.addWidget(returns_title)
        self._returns_body_layout = QVBoxLayout()
        self._returns_body_layout.setSpacing(1)
        returns_layout.addLayout(self._returns_body_layout)
        self._returns_section.setVisible(False)

        # Option overrides section (per-node override of bot-wide options)
        self._overrides_section = QWidget()
        _ov_vbox = QVBoxLayout(self._overrides_section)
        _ov_vbox.setContentsMargins(6, 4, 6, 4)
        _ov_vbox.setSpacing(4)
        _ov_title = QLabel("Option Overrides:")
        _ov_title.setStyleSheet("font-weight: bold; color: #555;")
        _ov_vbox.addWidget(_ov_title)
        self._overrides_body = QVBoxLayout()
        self._overrides_body.setSpacing(4)
        _ov_vbox.addLayout(self._overrides_body)
        self._overrides_section.setVisible(False)
        self._override_rows: list[QWidget] = []
        self._override_widgets: dict[str, tuple[QCheckBox, QWidget]] = {}

        # Record row — shown only for recordable node types
        self._record_row = QWidget()
        _rr_layout = QHBoxLayout(self._record_row)
        _rr_layout.setContentsMargins(6, 0, 6, 4)
        _rr_layout.setSpacing(6)
        self._record_btn = QPushButton("Record")
        self._record_btn.setFixedWidth(70)
        self._record_status_lbl = QLabel("")
        self._record_status_lbl.setStyleSheet("color: #888; font-size: 11px;")
        _rr_layout.addWidget(self._record_btn)
        _rr_layout.addWidget(self._record_status_lbl, 1)
        self._record_row.setVisible(False)
        self._record_btn.clicked.connect(self._on_record_clicked)

        node_outer = QWidget()
        node_vbox = QVBoxLayout(node_outer)
        node_vbox.setContentsMargins(0, 0, 0, 0)
        node_vbox.setSpacing(0)
        node_vbox.addWidget(node_w)
        node_vbox.addWidget(self._record_row)
        node_vbox.addWidget(scroll, 1)
        node_vbox.addWidget(self._overrides_section)
        node_vbox.addWidget(self._returns_section)
        self._stack.addWidget(node_outer)

        # Page 2 — edge
        edge_w = QWidget()
        ef = QFormLayout(edge_w)
        ef.setContentsMargins(6, 6, 6, 6)
        self._edge_src_lbl = QLabel()
        self._edge_dst_lbl = QLabel()
        self._edge_when_combo = QComboBox()
        self._edge_flag_combo = QComboBox()
        ef.addRow("Source", self._edge_src_lbl)
        ef.addRow("Dest", self._edge_dst_lbl)
        ef.addRow("When", self._edge_when_combo)

        flag_lbl = QLabel("Flag")
        flag_lbl.setToolTip(
            "Bot option that must be truthy for this transition to fire.\n"
            "Both When and Flag conditions must be satisfied."
        )
        ef.addRow(flag_lbl, self._edge_flag_combo)
        self._stack.addWidget(edge_w)

        self._node_label_edit.textChanged.connect(self._on_node_label_changed)
        self._node_type_combo.currentTextChanged.connect(self._on_node_type_changed)
        self._param_form.on_changed = self._on_param_form_changed
        self._edge_when_combo.currentTextChanged.connect(self._on_edge_when_changed)
        self._edge_flag_combo.currentTextChanged.connect(self._on_edge_flag_changed)

    def show_idle(self) -> None:
        self._cancel_record()
        self._state = None
        self._transition = None
        self._stack.setCurrentIndex(_PAGE_IDLE)

    def show_state(self, state: StateModel) -> None:
        self._cancel_record()
        self._state = state
        self._transition = None
        self._suppress = True
        self._node_id_lbl.setText(state.id)
        self._node_label_edit.setText(state.label or "")
        idx = self._node_type_combo.findText(state.node_type)
        if idx >= 0:
            self._node_type_combo.setCurrentIndex(idx)
        schema = NODE_PARAM_SCHEMAS.get(state.node_type, [])
        self._param_form.load_schema(schema, state.params or {})
        self._rebuild_overrides(state)
        self._rebuild_returns(state.node_type)
        self._suppress = False
        self._record_row.setVisible(state.node_type in _RECORDABLE_NODES)
        self._stack.setCurrentIndex(_PAGE_NODE)

    def show_transition(
        self,
        transition: TransitionModel,
        src_returns: list[str] | None = None,
        option_names: list[str] | None = None,
    ) -> None:
        self._transition = transition
        self._state = None
        self._suppress = True
        self._edge_src_lbl.setText(transition.source)
        self._edge_dst_lbl.setText(transition.dest)

        # Populate "When" combo from source node's possible return values
        self._edge_when_combo.clear()
        self._edge_when_combo.addItem("(none)")
        for name in (src_returns or []):
            self._edge_when_combo.addItem(name)
        if transition.when and self._edge_when_combo.findText(transition.when) < 0:
            self._edge_when_combo.addItem(transition.when)
        if transition.when:
            idx = self._edge_when_combo.findText(transition.when)
            if idx >= 0:
                self._edge_when_combo.setCurrentIndex(idx)
        else:
            self._edge_when_combo.setCurrentIndex(0)

        # Populate "Flag" combo from bot-wide options
        self._edge_flag_combo.clear()
        self._edge_flag_combo.addItem("(none)")
        for name in (option_names or []):
            self._edge_flag_combo.addItem(name)
        if transition.flag and self._edge_flag_combo.findText(transition.flag) < 0:
            # Flag references an option that no longer exists — show it anyway
            self._edge_flag_combo.addItem(transition.flag)
        if transition.flag:
            idx = self._edge_flag_combo.findText(transition.flag)
            if idx >= 0:
                self._edge_flag_combo.setCurrentIndex(idx)
        else:
            self._edge_flag_combo.setCurrentIndex(0)

        self._suppress = False
        self._stack.setCurrentIndex(_PAGE_EDGE)

    def _on_node_label_changed(self, text: str) -> None:
        if self._suppress or self._state is None:
            return
        self._state.label = text or None
        self.on_config_changed()

    def _on_node_type_changed(self, text: str) -> None:
        if self._suppress or self._state is None:
            return
        self._cancel_record()
        self._state.node_type = text
        self._state.params = {}
        self._state.option_overrides = {}
        schema = NODE_PARAM_SCHEMAS.get(text, [])
        self._param_form.load_schema(schema, {})
        self._state.params = self._param_form.get_params()
        self._rebuild_overrides(self._state)
        self._rebuild_returns(text)
        self._record_row.setVisible(text in _RECORDABLE_NODES)
        self.on_config_changed()

    def _on_param_form_changed(self) -> None:
        if self._state is None:
            return
        self._state.params = self._param_form.get_params()
        self.on_config_changed()

    def _on_edge_when_changed(self, text: str) -> None:
        if self._suppress or self._transition is None:
            return
        self._transition.when = None if text == "(none)" else text
        self.on_config_changed()

    def _on_edge_flag_changed(self, text: str) -> None:
        if self._suppress or self._transition is None:
            return
        self._transition.flag = None if text == "(none)" else text
        self.on_config_changed()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _cancel_record(self) -> None:
        if self._record_task is not None and not self._record_task.done():
            self._record_task.cancel()
        self._record_task = None
        self._record_btn.setText("Record")
        self._record_status_lbl.setText("")

    def _on_record_clicked(self) -> None:
        if self._record_task is not None and not self._record_task.done():
            self._cancel_record()
            return
        if self._state is None:
            return
        clients = self._get_clients()
        if not clients:
            self._record_status_lbl.setText("No client connected.")
            return
        if self._state.node_type == "go_to_zone":
            self._record_btn.setText("Stop")
            self._record_status_lbl.setText("Starting…")
            self._record_task = asyncio.ensure_future(
                self._record_go_to_zone(clients[0])
            )
        elif self._state.node_type == "enter_dungeon":
            self._record_btn.setText("Stop")
            self._record_status_lbl.setText("Starting…")
            self._record_task = asyncio.ensure_future(
                self._record_enter_dungeon(clients[0])
            )

    async def _record_go_to_zone(self, client) -> None:
        POLL = 0.15

        try:
            # Phase 1: from_zone
            from_zone = await client.zone_name()
            self._param_form.set_str("from_zone", from_zone)
            self._record_status_lbl.setText("Walk to transition…")

            last_pos = await client.body.position()

            # Phase 2: live-update xyz; stop when loading
            while True:
                await asyncio.sleep(POLL)
                if await client.is_loading():
                    self._param_form.set_xyz("xyz", last_pos.x, last_pos.y, last_pos.z)
                    self._record_status_lbl.setText("Loading…")
                    break
                last_pos = await client.body.position()
                self._param_form.set_xyz("xyz", last_pos.x, last_pos.y, last_pos.z)

            # Phase 3: wait for load to finish
            while await client.is_loading():
                await asyncio.sleep(POLL)

            # Phase 4: to_zone
            to_zone = await client.zone_name()
            self._param_form.set_str("to_zone", to_zone)

            # Phase 5: auto-populate label if empty
            if self._state is not None and not (self._state.label or "").strip():
                label = f"{_zone_display(from_zone)} → {_zone_display(to_zone)}"
                self._state.label = label
                self._suppress = True
                self._node_label_edit.setText(label)
                self._suppress = False
                self.on_config_changed()

            self._record_status_lbl.setText(f"Done — {to_zone.split('/')[-1]}")
            self._record_btn.setText("Record")

        except asyncio.CancelledError:
            self._record_status_lbl.setText("")
            self._record_btn.setText("Record")
        except Exception as e:
            self._record_status_lbl.setText(f"Error: {e}")
            self._record_btn.setText("Record")

    async def _record_enter_dungeon(self, client) -> None:
        POLL = 0.15

        try:
            # Phase 1: from_zone and reset_xyz (starting position)
            from_zone = await client.zone_name()
            self._param_form.set_str("from_zone", from_zone)
            start_pos = await client.body.position()
            self._param_form.set_xyz("reset_xyz", start_pos.x, start_pos.y, start_pos.z)
            self._record_status_lbl.setText("Walk to sigil…")

            last_pos = start_pos

            # Phase 2: live-update sigil_xyz; stop when loading
            while True:
                await asyncio.sleep(POLL)
                if await client.is_loading():
                    self._param_form.set_xyz("sigil_xyz", last_pos.x, last_pos.y, last_pos.z)
                    self._record_status_lbl.setText("Loading…")
                    break
                last_pos = await client.body.position()
                self._param_form.set_xyz("sigil_xyz", last_pos.x, last_pos.y, last_pos.z)

            # Phase 3: wait for load to finish
            while await client.is_loading():
                await asyncio.sleep(POLL)

            # Phase 4: dungeon_zone
            dungeon_zone = await client.zone_name()
            self._param_form.set_str("dungeon_zone", dungeon_zone)

            # Phase 5: auto-populate label if empty
            if self._state is not None and not (self._state.label or "").strip():
                label = f"{_zone_display(from_zone)} → {_zone_display(dungeon_zone)}"
                self._state.label = label
                self._suppress = True
                self._node_label_edit.setText(label)
                self._suppress = False
                self.on_config_changed()

            self._record_status_lbl.setText(f"Done — {dungeon_zone.split('/')[-1]}")
            self._record_btn.setText("Record")

        except asyncio.CancelledError:
            self._record_status_lbl.setText("")
            self._record_btn.setText("Record")
        except Exception as e:
            self._record_status_lbl.setText(f"Error: {e}")
            self._record_btn.setText("Record")

    def _rebuild_overrides(self, state: StateModel) -> None:
        for row in self._override_rows:
            self._overrides_body.removeWidget(row)
            row.deleteLater()
        self._override_rows.clear()
        self._override_widgets.clear()

        spec = NODE_SPECS.get(state.node_type)
        bot_options = spec.required_bot_options if spec else []
        if not bot_options:
            self._overrides_section.setVisible(False)
            return

        overrides = state.option_overrides
        for bot_opt in bot_options:
            name = bot_opt.name
            is_overriding = name in overrides
            current_val = overrides.get(name, bot_opt.default)

            val_widget = _make_override_widget(bot_opt.type, current_val, bot_opt.description or "")
            val_widget.setEnabled(is_overriding)

            cb = QCheckBox(f"Override {name}:")
            cb.setChecked(is_overriding)
            if bot_opt.description:
                cb.setToolTip(bot_opt.description)

            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            row_layout.addWidget(cb)
            row_layout.addWidget(val_widget, 1)

            def _on_cb_toggled(checked: bool, n=name, w=val_widget, s=state) -> None:
                if self._suppress:
                    return
                w.setEnabled(checked)
                if checked:
                    s.option_overrides[n] = _read_override_widget(w)
                else:
                    s.option_overrides.pop(n, None)
                self.on_config_changed()

            def _on_val_changed(n=name, cb_ref=cb, w=val_widget, s=state) -> None:
                if self._suppress or not cb_ref.isChecked():
                    return
                s.option_overrides[n] = _read_override_widget(w)
                self.on_config_changed()

            cb.toggled.connect(_on_cb_toggled)
            _connect_override_widget(val_widget, _on_val_changed)

            self._overrides_body.addWidget(row)
            self._override_rows.append(row)
            self._override_widgets[name] = (cb, val_widget)

        self._overrides_section.setVisible(True)

    def _rebuild_returns(self, node_type: str) -> None:
        while self._returns_body_layout.count():
            item = self._returns_body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        spec = NODE_SPECS.get(node_type)
        if spec and spec.returns:
            for ret in spec.returns:
                if ret.description:
                    lbl = QLabel(f"<b>{ret.name}</b> — {ret.description}")
                else:
                    lbl = QLabel(f"<b>{ret.name}</b>")
                lbl.setWordWrap(True)
                lbl.setStyleSheet("font-size: 11px;")
                self._returns_body_layout.addWidget(lbl)
            self._returns_section.setVisible(True)
        else:
            self._returns_section.setVisible(False)


# ---------------------------------------------------------------------------
# BotCanvas — unified widget
# ---------------------------------------------------------------------------

class BotCanvas(QWidget):
    """Single canvas widget covering both Run and Edit modes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._mode = BotMode.RUN
        self._run_state = _IDLE
        self._start_time: float | None = None
        self._active_state: str | None = None

        # Callbacks wired from bartlebot.py
        self.on_config_changed: Callable[[], None] = lambda: None
        self.on_mode_changed: Callable[[BotMode], None] = lambda _m: None
        self.on_run: Callable[[], None] = lambda: None
        self.on_pause: Callable[[], None] = lambda: None
        self.on_resume: Callable[[], None] = lambda: None
        self.on_save_and_done: Callable[[], None] = lambda: None

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

        run_row.addWidget(self._run_pause_btn)
        run_row.addSpacing(4)
        run_row.addWidget(self.stop_button)
        run_row.addWidget(self._state_label, 1)
        run_row.addWidget(self._elapsed_label)
        run_row.addSpacing(12)
        run_row.addWidget(self._edit_btn)

        # ── Edit-mode toolbar ─────────────────────────────────────────
        self._edit_bar = QWidget()
        edit_row = QHBoxLayout(self._edit_bar)
        edit_row.setContentsMargins(4, 4, 4, 4)

        self._add_state_btn = QPushButton("+ Add State")
        self._add_state_btn.setFixedWidth(110)
        self._add_state_btn.clicked.connect(self._on_add_state)

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

        # ── Log pane ──────────────────────────────────────────────────
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

        log_container = QWidget()
        log_vbox = QVBoxLayout(log_container)
        log_vbox.setContentsMargins(0, 0, 0, 0)
        log_vbox.setSpacing(0)
        log_vbox.addWidget(log_header)
        log_vbox.addWidget(self._log, 1)

        # ── Bottom tab widget ─────────────────────────────────────────
        self._bottom_tabs = QTabWidget()
        self._bottom_tabs.addTab(self._param_panel, "Properties")  # _TAB_PROPS = 0
        self._bottom_tabs.addTab(log_container, "Log")             # _TAB_LOG   = 1

        # ── Canvas vertical splitter ──────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._view)
        splitter.addWidget(self._bottom_tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([500, 160])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._toolbar_stack)
        outer.addWidget(splitter, 1)

        # ── Wire internal callbacks ───────────────────────────────────
        self._scene.on_config_changed = self._on_scene_changed
        self._param_panel.on_config_changed = self._on_scene_changed
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

    # ------------------------------------------------------------------
    # Public API — run state management
    # ------------------------------------------------------------------

    def set_run_enabled(self, enabled: bool) -> None:
        """Enable/disable the Run button (only when idle and in RUN mode)."""
        if self._run_state == _IDLE and self._mode == BotMode.RUN:
            self._run_pause_btn.setEnabled(enabled)

    def set_clients_getter(self, fn: Callable[[], list]) -> None:
        """Pass a callable that returns the current list of hooked clients."""
        self._param_panel._get_clients = fn

    def set_running(self) -> None:
        self._run_state = _RUNNING
        self._run_pause_btn.setText("⏸  Pause")
        self._run_pause_btn.setEnabled(True)
        self.stop_button.setEnabled(True)
        self._edit_btn.setEnabled(False)
        self._start_time = time.monotonic()
        self._elapsed_label.setText("00:00:00")
        self._tick_timer.start()
        self._bottom_tabs.setCurrentIndex(_TAB_LOG)
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
            self.on_run()
        elif self._run_state == _RUNNING:
            self.set_paused()
            self.on_pause()
        elif self._run_state == _PAUSED:
            self._run_state = _RUNNING
            self._run_pause_btn.setText("⏸  Pause")
            self.append_log("=== Resumed ===")
            self.on_resume()

    def _on_edit_clicked(self) -> None:
        if self._run_state != _IDLE:
            return
        self._apply_mode(BotMode.EDIT)
        self.on_mode_changed(BotMode.EDIT)

    def _on_done_clicked(self) -> None:
        self._apply_mode(BotMode.RUN)
        self.on_mode_changed(BotMode.RUN)
        self.on_config_changed()

    def _on_save_done_clicked(self) -> None:
        self._apply_mode(BotMode.RUN)
        self.on_mode_changed(BotMode.RUN)
        self.on_save_and_done()

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
        else:
            self._toolbar_stack.setCurrentIndex(_TB_EDIT)
            self._scene.set_interactive(True)
            self._bottom_tabs.setCurrentIndex(_TAB_PROPS)

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
        self.on_config_changed()

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
    if field.type == "int_list":
        return not val  # empty list
    return False  # int, float, bool, xyz always have a value


def _validate_node(state, config) -> tuple[bool, bool]:
    """Return (is_error, is_warning) for a state given the full config.

    Error conditions:
      - node_type not in NODE_REGISTRY (unknown type)
      - any required param key absent from state.params
      - any outgoing transition points to a non-existent destination state

    Warning conditions:
      - node has outgoing transitions but some return values are not covered
        by any t.when (and no unconditional when=None transition exists)
    """
    from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS
    is_error = False
    is_warning = False

    if state.node_type not in NODE_REGISTRY:
        is_error = True

    schema = NODE_PARAM_SCHEMAS.get(state.node_type, [])
    for field in schema:
        if field.required and _required_field_empty(field, state.params):
            is_error = True
            break

    state_ids = {s.id for s in config.states}
    outgoing = [t for t in config.transitions if t.source == state.id]
    for t in outgoing:
        if t.dest not in state_ids:
            is_error = True
            break

    if outgoing:
        spec = NODE_SPECS.get(state.node_type)
        if spec and spec.returns:
            outgoing_whens = {t.when for t in outgoing}
            if None not in outgoing_whens:
                for ret in spec.returns:
                    if ret.name not in outgoing_whens:
                        is_warning = True
                        break

    return is_error, is_warning


def _zone_display(zone: str) -> str:
    """Return a human-friendly display name for a zone path like 'WizardCity/WC_Hub'."""
    try:
        from wizsprinter import toZone, toZoneDisplayName  # noqa: PLC0415
        return toZoneDisplayName(toZone(zone))
    except Exception:
        return zone.split("/")[-1] if "/" in zone else zone


def _dist(a: QPointF, b: QPointF) -> float:
    return math.hypot(a.x() - b.x(), a.y() - b.y())


def _unique_id(existing: list[str]) -> str:
    i = 1
    while f"state_{i}" in existing:
        i += 1
    return f"state_{i}"
