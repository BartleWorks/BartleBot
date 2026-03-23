"""Unified bot canvas — single QGraphicsScene with Run and Edit modes.

BotCanvas  — top-level widget; owns the scene, toolbars, properties panel,
             and run log. Replaces the former EditTab + RunningTab pair.
EditScene  — interactive QGraphicsScene (same logic as the old edit_tab.py).
"""

import enum
import logging
import math
import time
from collections.abc import Callable

from PyQt6.QtCore import QEvent, QPointF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QColor,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPen,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QComboBox,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.theme import THEME

logger = logging.getLogger(__name__)

from bartlebot.bot.schema import BotConfig, BotOptionModel, RegionModel, StateModel, TransitionModel
from bartlebot.gui.canvas.graph_items import (
    NODE_H,
    NODE_W,
    EdgeItem,
    GraphView,
    NodeItem,
    _MinimapView,
)
from bartlebot.gui.canvas.param_panel import _ParamPanel
from bartlebot.gui.canvas.region_item import RegionItem
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS, NODE_REGISTRY, NODE_SPECS
from bartlebot.settings_manager import SettingsManager

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PORT_HIT_RADIUS = 16
_AUTO_SPACING = 260
_PASTE_OFFSET = 40  # px shift per successive paste of the same clipboard

_TB_RUN = 0  # toolbar stack index for Run mode
_TB_EDIT = 1  # toolbar stack index for Edit mode

_IDLE = "idle"
_RUNNING = "running"
_PAUSED = "paused"

_NAME_TO_LEVEL: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}
_LOG_COLORS: dict[int, str] = {
    logging.DEBUG: THEME.log_debug,
    logging.INFO: THEME.log_info,
    logging.WARNING: THEME.log_warning,
    logging.ERROR: THEME.log_error,
}

_STATUS_DOT_COLORS: dict[str, str] = {
    _IDLE: THEME.status_idle,
    _RUNNING: THEME.status_running,
    _PAUSED: THEME.status_paused,
}


_ICON_SIZE = QSize(20, 20)
_BTN_SIZE = QSize(32, 32)


def _icon_btn(icon_name: str, tooltip: str) -> QPushButton:
    """Create a fixed-size icon-only toolbar button."""
    btn = QPushButton()
    btn.setIcon(svg_icon(icon_name))
    btn.setIconSize(_ICON_SIZE)
    btn.setFixedSize(_BTN_SIZE)
    btn.setToolTip(tooltip)
    return btn


class BotMode(enum.Enum):
    RUN = "run"
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
    config.options = [o for o in config.options if o.name not in all_system_names or o.name in required_now]

    # Add missing required options (preserves user-customised values already present).
    existing = {o.name for o in config.options}
    for state in config.states:
        spec = NODE_SPECS.get(state.node_type)
        if not spec:
            continue
        for req in spec.required_bot_options:
            if req.name not in existing:
                config.options.append(
                    BotOptionModel(
                        name=req.name,
                        type=req.type,
                        default=req.default,
                        description=req.description,
                    )
                )
                existing.add(req.name)


# ---------------------------------------------------------------------------
# EditScene — interactive QGraphicsScene
# ---------------------------------------------------------------------------


class EditScene(QGraphicsScene):
    """Manages interactive nodes, edge dragging, deletion, and positions."""

    config_changed = pyqtSignal()
    focus_region_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._node_items: dict[str, NodeItem] = {}
        self._edge_items: list[tuple[str, str | None, str, EdgeItem]] = []
        self._region_items: dict[str, RegionItem] = {}
        self._drag_src: NodeItem | None = None
        self._drag_when: str | None = None
        self._temp_edge: QGraphicsLineItem | None = None
        self._interactive_mode: bool = True
        self._clipboard: tuple[list[dict], list[dict]] | None = None
        self._paste_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setup(self, config: BotConfig) -> None:
        """Rebuild the scene from a BotConfig."""
        for ri in self._region_items.values():
            try:
                self.changed.disconnect(ri._recompute_rect)
            except RuntimeError:
                pass
        self._region_items.clear()
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
            is_error, is_warning, err_reasons, warn_reasons = _validate_node(state, config)
            reasons = err_reasons + warn_reasons
            node = NodeItem(
                state.id,
                state.label,
                interactive=self._interactive_mode,
                returns=returns,
                is_initial=(state.id == config.initial),
                is_terminal=(state.id not in sources),
                is_error=is_error,
                is_warning=is_warning,
                node_type=state.node_type,
                flipped=getattr(state, "flipped", False),
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

        # Add region items behind nodes (z=-1)
        for region in config.regions:
            ri = RegionItem(region, self._node_items)
            ri.set_interactive(self._interactive_mode)
            self.addItem(ri)
            self._region_items[region.id] = ri
        for ri in self._region_items.values():
            self.changed.connect(ri._recompute_rect)
            ri._recompute_rect()

        bounds = self.itemsBoundingRect().adjusted(-500, -500, 500, 500)
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
            is_error, is_warning, err_reasons, warn_reasons = _validate_node(state, self._config)
            reasons = err_reasons + warn_reasons
            node.set_error(is_error, reasons)
            node.set_warning(is_warning, reasons)

    def set_interactive(self, interactive: bool) -> None:
        """Toggle node and edge interactivity for mode switching."""
        self._interactive_mode = interactive
        for node in self._node_items.values():
            node.set_interactive(interactive)
        for _s, _w, _d, edge in self._edge_items:
            edge.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, interactive)
        for region in self._region_items.values():
            region.set_interactive(interactive)
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
        node = NodeItem(state_id, None, interactive=True, returns=returns, is_terminal=True, node_type=default_type)
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
                line.setPen(QPen(THEME.qcolor("port_color"), 2, Qt.PenStyle.DashLine))
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
                if dst is not None:
                    self._create_transition(self._drag_src, dst, self._drag_when)
            self._drag_src = None
            self._drag_when = None
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._flush_positions()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._interactive_mode:
            super().keyPressEvent(event)
            return
        ctrl = event.modifiers() == Qt.KeyboardModifier.ControlModifier
        if event.key() == Qt.Key.Key_Delete:
            self._delete_selected()
            return
        if ctrl and event.key() == Qt.Key.Key_C:
            self._copy_selected()
            return
        if ctrl and event.key() == Qt.Key.Key_X:
            self._copy_selected()
            self._delete_selected()
            return
        if ctrl and event.key() == Qt.Key.Key_V:
            self._paste()
            return
        if event.key() == Qt.Key.Key_F:
            self._flip_selected_nodes()
            return
        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        from PyQt6.QtGui import QTransform

        item = self.itemAt(event.scenePos(), self.views()[0].transform() if self.views() else QTransform())
        if isinstance(item, RegionItem):
            self.focus_region_requested.emit(item.region_id)
            return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def contextMenuEvent(self, event) -> None:
        if not self._interactive_mode:
            return
        from PyQt6.QtWidgets import QMenu

        item = self.itemAt(
            event.scenePos(),
            self.views()[0].transform()
            if self.views()
            else __import__("PyQt6.QtGui", fromlist=["QTransform"]).QTransform(),
        )
        menu = QMenu()
        if isinstance(item, NodeItem):
            act = QAction("Delete State", menu)
            act.triggered.connect(lambda: self._delete_node(item.state_id))
            menu.addAction(act)
            flip_label = "Unflip Node" if item._flipped else "Flip Node"
            act_flip = QAction(flip_label, menu)
            sid = item.state_id
            act_flip.triggered.connect(lambda _=None, s=sid: self._flip_node(s))
            menu.addAction(act_flip)
            # Region membership actions
            if self._config and self._config.regions:
                menu.addSeparator()
                sid = item.state_id
                for region in self._config.regions:
                    if sid not in region.members:
                        rid = region.id
                        rlabel = region.label
                        act_add = QAction(f"Add to Region: {rlabel}", menu)
                        act_add.triggered.connect(lambda _=None, s=sid, r=rid: self._add_to_region(s, r))
                        menu.addAction(act_add)
                    else:
                        rid = region.id
                        rlabel = region.label
                        act_rem = QAction(f"Remove from Region: {rlabel}", menu)
                        act_rem.triggered.connect(lambda _=None, s=sid, r=rid: self._remove_from_region(s, r))
                        menu.addAction(act_rem)
            # Create region from multi-selection
            selected_nodes = [i for i in self.selectedItems() if isinstance(i, NodeItem)]
            if len(selected_nodes) >= 2:
                menu.addSeparator()
                n = len(selected_nodes)
                act_cr = QAction(f"Create Region from {n} selected states", menu)
                act_cr.triggered.connect(lambda: self._create_region([nd.state_id for nd in selected_nodes]))
                menu.addAction(act_cr)
        elif isinstance(item, RegionItem):
            rid = item.region_id
            act_rn = QAction("Rename Region", menu)
            act_rn.triggered.connect(lambda: self._rename_region(rid))
            menu.addAction(act_rn)
            act_rc = QAction("Change Color", menu)
            act_rc.triggered.connect(lambda: self._recolor_region(rid))
            menu.addAction(act_rc)
            act_del = QAction("Remove Region", menu)
            act_del.triggered.connect(lambda: self._delete_region(rid))
            menu.addAction(act_del)
        elif isinstance(item, EdgeItem):
            act = QAction("Delete Transition", menu)
            act.triggered.connect(lambda: self._delete_edge_item(item))
            menu.addAction(act)
            act_ins = QAction("Insert State Here", menu)
            act_ins.triggered.connect(lambda: self._insert_state_on_edge(item))
            menu.addAction(act_ins)
        else:
            scene_pos = event.scenePos()
            act = QAction("Add State Here", menu)
            act.triggered.connect(
                lambda: self.add_state(QPointF(scene_pos.x() - NODE_W / 2, scene_pos.y() - NODE_H / 2))
            )
            menu.addAction(act)
            selected_nodes = [i for i in self.selectedItems() if isinstance(i, NodeItem)]
            if len(selected_nodes) >= 2:
                menu.addSeparator()
                n = len(selected_nodes)
                act_cr = QAction(f"Create Region from {n} selected states", menu)
                act_cr.triggered.connect(lambda: self._create_region([nd.state_id for nd in selected_nodes]))
                menu.addAction(act_cr)
        menu.exec(event.screenPos())

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

    def _create_transition(self, src_node: NodeItem, dst_node: NodeItem, when: str | None = None) -> None:
        if self._config is None:
            return
        s, d = src_node.state_id, dst_node.state_id
        if any(t.source == s and t.dest == d and t.when == when for t in self._config.transitions):
            return
        t = TransitionModel(trigger="advance", source=s, dest=d, when=when)
        self._config.transitions.append(t)

        reverse_exists = any(es == d and ed == s for (es, _ew, ed, _ei) in self._edge_items)
        if reverse_exists:
            for es, _ew, ed, existing in self._edge_items:
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

    def _copy_selected(self) -> None:
        if self._config is None:
            return
        selected_ids = {item.state_id for item in self.selectedItems() if isinstance(item, NodeItem)}
        if not selected_ids:
            return
        states = [s.model_dump() for s in self._config.states if s.id in selected_ids]
        transitions = [
            t.model_dump() for t in self._config.transitions if t.source in selected_ids and t.dest in selected_ids
        ]
        self._clipboard = (states, transitions)
        self._paste_count = 0

    def _paste(self) -> None:
        if self._config is None or self._clipboard is None:
            return
        states_data, transitions_data = self._clipboard
        self._paste_count += 1
        offset = _PASTE_OFFSET * self._paste_count

        # Build old_id → new_id mapping
        id_map: dict[str, str] = {}
        existing_ids = [s.id for s in self._config.states]
        for sd in states_data:
            new_id = _unique_id(existing_ids)
            id_map[sd["id"]] = new_id
            existing_ids.append(new_id)

        # Create new StateModels and NodeItems
        for sd in states_data:
            new_sd = dict(sd)
            new_sd["id"] = id_map[sd["id"]]
            if new_sd.get("position"):
                new_sd["position"] = {
                    "x": new_sd["position"]["x"] + offset,
                    "y": new_sd["position"]["y"] + offset,
                }
            state = StateModel.model_validate(new_sd)
            self._config.states.append(state)
            returns = _node_returns(state.node_type)
            node = NodeItem(
                state.id,
                state.label,
                interactive=True,
                returns=returns,
                is_terminal=True,
                node_type=state.node_type,
                flipped=state.flipped,
            )
            if new_sd.get("position"):
                node.setPos(
                    QPointF(
                        new_sd["position"]["x"] - NODE_W / 2,
                        new_sd["position"]["y"] - NODE_H / 2,
                    )
                )
            self.addItem(node)
            self._node_items[state.id] = node

        # Re-create internal transitions (_create_transition calls refresh_terminal/errors)
        for td in transitions_data:
            src_node = self._node_items[id_map[td["source"]]]
            dst_node = self._node_items[id_map[td["dest"]]]
            self._create_transition(src_node, dst_node, when=td.get("when"))

        # Ensure terminal/error badges are correct even with no internal transitions
        self.refresh_terminal()
        self.refresh_errors()

        # Select the pasted nodes
        self.clearSelection()
        for new_id in id_map.values():
            if new_id in self._node_items:
                self._node_items[new_id].setSelected(True)

        self._update_scene_rect()
        self.config_changed.emit()

    def _flip_node(self, state_id: str) -> None:
        if self._config is None:
            return
        node = self._node_items.get(state_id)
        if node is None:
            return
        new_flipped = not node._flipped
        node.set_flipped(new_flipped)
        state = next((s for s in self._config.states if s.id == state_id), None)
        if state is not None:
            state.flipped = new_flipped
        self.config_changed.emit()

    def _flip_selected_nodes(self) -> None:
        if self._config is None:
            return
        changed = False
        for item in self.selectedItems():
            if isinstance(item, NodeItem):
                new_flipped = not item._flipped
                item.set_flipped(new_flipped)
                state = next((s for s in self._config.states if s.id == item.state_id), None)
                if state is not None:
                    state.flipped = new_flipped
                changed = True
        if changed:
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
        self._config.transitions = [t for t in self._config.transitions if t.source != state_id and t.dest != state_id]
        remaining = []
        for s, w, d, edge_item in self._edge_items:
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
        self._flush_regions()
        self._update_scene_rect()
        self.config_changed.emit()

    def _delete_edge_item(self, edge_item: EdgeItem) -> None:
        if self._config is None:
            return
        remaining = []
        for s, w, d, ei in self._edge_items:
            if ei is edge_item:
                s_node = self._node_items.get(s)
                d_node = self._node_items.get(d)
                if s_node:
                    s_node.remove_edge(ei)
                if d_node and d_node is not s_node:
                    d_node.remove_edge(ei)
                self._config.transitions = [
                    t for t in self._config.transitions if not (t.source == s and t.dest == d and t.when == w)
                ]
                self.removeItem(ei)
            else:
                remaining.append((s, w, d, ei))
        self._edge_items = remaining
        self.refresh_terminal()
        self.refresh_errors()
        self.config_changed.emit()

    def _insert_state_on_edge(self, edge_item: EdgeItem) -> None:
        """Split an existing edge by inserting a new state at its midpoint."""
        if self._config is None:
            return
        # Locate the edge record
        for s, w, d, ei in list(self._edge_items):  # noqa: B007 — s, w, d used after loop via for-else leak
            if ei is edge_item:
                break
        else:
            return
        src_node = self._node_items.get(s)
        dst_node = self._node_items.get(d)
        if src_node is None or dst_node is None:
            return

        # Midpoint of the edge path in scene coordinates
        mid = edge_item.mapToScene(edge_item.path().pointAtPercent(0.5))

        # Create a new state at the midpoint
        new_id = _unique_id([st.id for st in self._config.states])
        default_type = next(iter(NODE_REGISTRY), "go_to_zone")
        state = StateModel(
            id=new_id,
            node_type=default_type,
            position={"x": mid.x() + NODE_W / 2, "y": mid.y() + NODE_H / 2},
        )
        self._config.states.append(state)
        returns = _node_returns(default_type)
        new_node = NodeItem(
            new_id,
            None,
            interactive=True,
            returns=returns,
            is_terminal=True,
            node_type=default_type,
        )
        new_node.setPos(QPointF(mid.x() - NODE_W / 2, mid.y() - NODE_H / 2))
        self.addItem(new_node)
        self._node_items[new_id] = new_node

        # Remove original edge (src --w--> dst), then wire src --w--> new --∅--> dst
        self._delete_edge_item(edge_item)
        self._create_transition(src_node, new_node, w)
        self._create_transition(new_node, dst_node, None)
        self._update_scene_rect()

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

    def _flush_regions(self) -> None:
        """Remove stale member IDs from regions; auto-delete regions with < 2 members."""
        if self._config is None:
            return
        state_ids = {s.id for s in self._config.states}
        for region in list(self._config.regions):
            cleaned = [m for m in region.members if m in state_ids]
            if len(cleaned) < 2:
                self._delete_region(region.id)
            else:
                region.members[:] = cleaned

    def _create_region(self, state_ids: list[str]) -> None:
        if self._config is None or len(state_ids) < 2:
            return
        import uuid

        from PyQt6.QtWidgets import QInputDialog

        label, ok = QInputDialog.getText(self.views()[0] if self.views() else None, "Create Region", "Region name:")
        if not ok or not label.strip():
            return
        region = RegionModel(
            id=uuid.uuid4().hex[:8],
            label=label.strip(),
            members=list(state_ids),
            color="amber",
        )
        self._config.regions.append(region)
        ri = RegionItem(region, self._node_items)
        ri.set_interactive(self._interactive_mode)
        self.addItem(ri)
        self._region_items[region.id] = ri
        self.changed.connect(ri._recompute_rect)
        ri._recompute_rect()
        self.config_changed.emit()

    def _delete_region(self, region_id: str) -> None:
        if self._config is None:
            return
        ri = self._region_items.pop(region_id, None)
        if ri is not None:
            try:
                self.changed.disconnect(ri._recompute_rect)
            except RuntimeError:
                pass
            self.removeItem(ri)
        self._config.regions = [r for r in self._config.regions if r.id != region_id]
        self.config_changed.emit()

    def _rename_region(self, region_id: str) -> None:
        if self._config is None:
            return
        region = next((r for r in self._config.regions if r.id == region_id), None)
        if region is None:
            return
        from PyQt6.QtWidgets import QInputDialog

        label, ok = QInputDialog.getText(
            self.views()[0] if self.views() else None, "Rename Region", "Region name:", text=region.label
        )
        if not ok or not label.strip():
            return
        region.label = label.strip()
        ri = self._region_items.get(region_id)
        if ri is not None:
            ri.set_label(region.label)
        self.config_changed.emit()

    def _recolor_region(self, region_id: str) -> None:
        if self._config is None:
            return
        region = next((r for r in self._config.regions if r.id == region_id), None)
        if region is None:
            return
        from PyQt6.QtWidgets import QDialog

        dlg = _ColorPickerDialog(current_key=region.color, parent=self.views()[0] if self.views() else None)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        region.color = dlg.selected_color()
        ri = self._region_items.get(region_id)
        if ri is not None:
            ri.set_color(region.color)
        self.config_changed.emit()

    def _add_to_region(self, state_id: str, region_id: str) -> None:
        if self._config is None:
            return
        region = next((r for r in self._config.regions if r.id == region_id), None)
        if region is None or state_id in region.members:
            return
        region.members.append(state_id)
        ri = self._region_items.get(region_id)
        if ri is not None:
            ri._member_ids.append(state_id)
            ri._recompute_rect()
        self.config_changed.emit()

    def _remove_from_region(self, state_id: str, region_id: str) -> None:
        if self._config is None:
            return
        region = next((r for r in self._config.regions if r.id == region_id), None)
        if region is None or state_id not in region.members:
            return
        region.members.remove(state_id)
        if len(region.members) < 2:
            self._delete_region(region_id)
            return
        ri = self._region_items.get(region_id)
        if ri is not None:
            if state_id in ri._member_ids:
                ri._member_ids.remove(state_id)
            ri._recompute_rect()
        self.config_changed.emit()

    def _update_scene_rect(self) -> None:
        bounds = self.itemsBoundingRect().adjusted(-500, -500, 500, 500)
        self.setSceneRect(self.sceneRect().united(bounds))

    def _get_tab_order(self) -> list[str]:
        """State IDs in BFS order from initial state, orphans appended at the end."""
        if self._config is None:
            return list(self._node_items.keys())
        adj: dict[str, list[str]] = {}
        for t in self._config.transitions:
            adj.setdefault(t.source, []).append(t.dest)
        order: list[str] = []
        seen: set[str] = set()
        queue = [self._config.initial]
        while queue:
            sid = queue.pop(0)
            if sid in seen:
                continue
            seen.add(sid)
            if sid in self._node_items:
                order.append(sid)
            for dest in adj.get(sid, []):
                if dest not in seen:
                    queue.append(dest)
        for sid in self._node_items:
            if sid not in seen:
                order.append(sid)
        return order


# ---------------------------------------------------------------------------
# _EditView
# ---------------------------------------------------------------------------


class _EditView(GraphView):
    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, interactive=True, parent=parent)


# ---------------------------------------------------------------------------
# _SearchBar — floating quick-search overlay (Ctrl+F)
# ---------------------------------------------------------------------------


class _SearchBar(QWidget):
    """Floating search bar overlaid on the graph view (Ctrl+F to open)."""

    text_changed = pyqtSignal(str)
    next_match = pyqtSignal()
    prev_match = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QWidget {{ background-color: {THEME.overlay_bg}; border: 1px solid {THEME.overlay_border}; border-radius: 6px; }}"
            f"QLineEdit {{ border: none; background: transparent; color: {THEME.overlay_text}; font-size: 13px; }}"
            f"QPushButton {{ border: none; background: transparent; color: {THEME.overlay_muted}; font-size: 14px; }}"
            f"QPushButton:hover {{ color: {THEME.overlay_text}; }}"
            f"QLabel {{ border: none; color: {THEME.overlay_muted}; font-size: 11px; }}"
        )

        self._input = QLineEdit()
        self._input.setPlaceholderText("Search states…")
        self._input.setFixedWidth(200)
        self._input.installEventFilter(self)
        self._input.textChanged.connect(self.text_changed)

        self._count_lbl = QLabel()

        close_btn = QPushButton("×")
        close_btn.setFixedSize(22, 22)
        close_btn.clicked.connect(self.closed)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 6, 4)
        row.setSpacing(6)
        row.addWidget(self._input)
        row.addWidget(self._count_lbl)
        row.addWidget(close_btn)
        self.adjustSize()
        self.setVisible(False)

    def set_count(self, current: int, total: int) -> None:
        self._count_lbl.setText(
            "" if total == 0 and not self._input.text() else "no matches" if total == 0 else f"{current}/{total}"
        )
        self.adjustSize()

    def focus_input(self) -> None:
        self._input.setFocus()
        self._input.selectAll()

    def current_text(self) -> str:
        return self._input.text()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.closed.emit()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Down):
                self.next_match.emit()
                return True
            if key == Qt.Key.Key_Up:
                self.prev_match.emit()
                return True
        return super().eventFilter(obj, event)


# ---------------------------------------------------------------------------
# BotCanvas — unified widget
# ---------------------------------------------------------------------------


class BotCanvas(QWidget):
    """Single canvas widget covering both Run and Edit modes."""

    config_changed = pyqtSignal()
    mode_changed = pyqtSignal(BotMode)
    run_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    save_done = pyqtSignal()
    # Emitted with "log", "properties", or "bot" to switch the sidebar tab
    tab_switch = pyqtSignal(str)
    # (css_color, status, state_label, elapsed_text) — consumed by MainWindow title bar
    bot_status_changed = pyqtSignal(str, str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._mode = BotMode.RUN
        self._run_state = _IDLE
        self._start_time: float | None = None
        self._active_state: str | None = None
        self._run_externally_enabled: bool = False
        self._n_validation_errors: int = 0

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick)

        self._tab_idx: int = 0
        self._search_matches: list[str] = []
        self._search_match_idx: int = 0
        self._focus_region_id: str | None = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._scene = EditScene()
        self._view = _EditView(self._scene)
        self._param_panel = _ParamPanel()

        # ── Buttons ───────────────────────────────────────────────────
        self._run_pause_btn = _icon_btn("play", "Run bot")
        self._run_pause_btn.setEnabled(False)
        self._run_pause_btn.clicked.connect(self._on_run_pause_clicked)

        self.stop_button = _icon_btn("stop", "Stop bot")
        self.stop_button.setEnabled(False)  # enabled only when running/paused

        self._edit_btn = _icon_btn("pencil", "Enter Edit Mode")
        self._edit_btn.setEnabled(False)
        self._edit_btn.clicked.connect(self._on_edit_clicked)

        self._fit_btn = _icon_btn("fit", "Fit to Canvas (Ctrl+0)")
        self._fit_btn.clicked.connect(self._view.fit_scene)

        self._add_state_btn = _icon_btn("plus", "Add State")
        self._add_state_btn.clicked.connect(self._on_add_state)

        self._layout_btn = _icon_btn("layout", "Auto-Layout states")
        self._layout_btn.clicked.connect(self._on_layout_clicked)

        self._done_btn = _icon_btn("checkmark", "Apply changes and return to Run mode (does not save to disk)")
        self._done_btn.clicked.connect(self._on_done_clicked)

        self._save_done_btn = _icon_btn("save_done", "Save to disk and return to Run mode")
        self._save_done_btn.clicked.connect(self._on_save_done_clicked)

        # ── Swappable center zone ─────────────────────────────────────
        run_center = QWidget()
        run_center_row = QHBoxLayout(run_center)
        run_center_row.setContentsMargins(0, 0, 0, 0)
        run_center_row.addWidget(self._run_pause_btn)
        run_center_row.addSpacing(4)
        run_center_row.addWidget(self.stop_button)

        edit_center = QWidget()
        edit_center_row = QHBoxLayout(edit_center)
        edit_center_row.setContentsMargins(0, 0, 0, 0)
        edit_center_row.addWidget(self._add_state_btn)
        edit_center_row.addSpacing(4)
        edit_center_row.addWidget(self._layout_btn)

        self._center_stack = QStackedWidget()
        self._center_stack.addWidget(run_center)  # _TB_RUN  = 0
        self._center_stack.addWidget(edit_center)  # _TB_EDIT = 1

        # ── Left section (68 px fixed: fit button) ─
        left_section = QWidget()
        left_section.setFixedWidth(68)
        left_row = QHBoxLayout(left_section)
        left_row.setContentsMargins(0, 0, 0, 0)
        left_row.setSpacing(0)
        left_row.addWidget(self._fit_btn)
        left_row.addStretch(1)

        # ── Right mode-toggle zone (68 px fixed) ─────────────────────
        # Run mode page: single icon right-aligned inside 68 px
        run_right = QWidget()
        run_right_row = QHBoxLayout(run_right)
        run_right_row.setContentsMargins(0, 0, 0, 0)
        run_right_row.addStretch(1)
        run_right_row.addWidget(self._edit_btn)

        # Edit mode page: two icons, fills 68 px naturally (32+4+32)
        edit_right = QWidget()
        edit_right_row = QHBoxLayout(edit_right)
        edit_right_row.setContentsMargins(0, 0, 0, 0)
        edit_right_row.addWidget(self._done_btn)
        edit_right_row.addSpacing(4)
        edit_right_row.addWidget(self._save_done_btn)

        self._right_stack = QStackedWidget()
        self._right_stack.setFixedWidth(68)
        self._right_stack.addWidget(run_right)  # _TB_RUN  = 0
        self._right_stack.addWidget(edit_right)  # _TB_EDIT = 1

        # ── Validation badge — shown left of center when errors/warnings exist ──
        # ── Persistent toolbar row ────────────────────────────────────
        toolbar = QWidget()
        toolbar_row = QHBoxLayout(toolbar)
        toolbar_row.setContentsMargins(4, 4, 4, 4)
        toolbar_row.addWidget(left_section)
        toolbar_row.addStretch(1)
        toolbar_row.addWidget(self._center_stack)
        toolbar_row.addStretch(1)
        toolbar_row.addWidget(self._right_stack)

        # ── Validation banner — right-aligned below toolbar ───────────
        # Hidden when valid (takes no space); appears under the edit/save
        # button whose right_stack sits at the 4 px right margin of the toolbar.
        _pill_base = "border-radius: 4px; padding: 1px 7px;"
        self._error_pill = QLabel()
        self._error_pill.setStyleSheet(
            f"QLabel {{ background-color: {THEME.pill_error_bg}; color: {THEME.pill_text}; {_pill_base} }}"
        )
        self._error_pill.setVisible(False)

        self._warning_pill = QPushButton()
        self._warning_pill.setStyleSheet(
            f"QPushButton {{ background-color: {THEME.pill_warning_bg}; color: {THEME.pill_text}; {_pill_base} border: none; }}"
            f"QPushButton:hover {{ background-color: {THEME.pill_warning_bg_hover}; }}"
        )
        self._warning_pill.setVisible(False)
        self._warning_pill.clicked.connect(self._cleanup_orphans)

        self._validation_banner = QWidget(self._view)
        self._validation_banner.setStyleSheet("background: transparent;")
        _banner_row = QHBoxLayout(self._validation_banner)
        _banner_row.setContentsMargins(4, 4, 4, 4)
        _banner_row.setSpacing(4)
        _banner_row.addWidget(self._error_pill)
        _banner_row.addWidget(self._warning_pill)
        self._validation_banner.setVisible(False)

        # ── Minimap overlay — bottom-right corner of the graph view ──────
        self._minimap = _MinimapView(self._scene, self._view, parent=self._view)
        self._minimap.setVisible(SettingsManager.instance().get("canvas", "minimap_visible", default=True))

        # ── Log pane (owned here, displayed in left sidebar Log tab) ──
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(THEME.font_mono())

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

        # ── Main layout: toolbar + validation banner + graph view ─────
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(toolbar)
        outer.addWidget(self._view, 1)

        # ── Search bar overlay — top-center of the graph view ────────
        self._search_bar = _SearchBar(parent=self._view)
        self._search_bar.text_changed.connect(self._on_search_text_changed)
        self._search_bar.next_match.connect(self._on_search_next)
        self._search_bar.prev_match.connect(self._on_search_prev)
        self._search_bar.closed.connect(self._close_search)

        # ── Focus breadcrumb overlay — top-left of the graph view ────
        self._focus_crumb = QWidget(self._view)
        self._focus_crumb.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._focus_crumb.setStyleSheet(
            f"QWidget {{ background-color: {THEME.overlay_bg}; border: 1px solid {THEME.overlay_border}; border-radius: 6px; }}"
            f"QLabel {{ border: none; color: {THEME.overlay_text}; font-size: 12px; padding: 0; }}"
            f"QPushButton {{ border: none; background: transparent; color: {THEME.overlay_muted}; font-size: 14px; }}"
            f"QPushButton:hover {{ color: {THEME.overlay_text}; }}"
        )
        self._focus_crumb_label = QLabel("Viewing: ")
        _close_focus_btn = QPushButton("×")
        _close_focus_btn.setFixedSize(22, 22)
        _close_focus_btn.clicked.connect(self._exit_focus_mode)
        _crumb_row = QHBoxLayout(self._focus_crumb)
        _crumb_row.setContentsMargins(8, 4, 6, 4)
        _crumb_row.setSpacing(4)
        _crumb_row.addWidget(self._focus_crumb_label)
        _crumb_row.addWidget(_close_focus_btn)
        self._focus_crumb.adjustSize()
        self._focus_crumb.setVisible(False)

        # ── Keyboard shortcuts ────────────────────────────────────────
        fit_shortcut = QShortcut(QKeySequence("Ctrl+0"), self)
        fit_shortcut.activated.connect(self._view.fit_scene)

        search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        search_shortcut.activated.connect(self._show_search_bar)

        # ── Reposition banner overlay when the view resizes ───────────
        self._view.installEventFilter(self)

        # ── Wire internal signals ─────────────────────────────────────
        self._scene.config_changed.connect(self._on_scene_changed)
        self._param_panel.config_changed.connect(self._on_scene_changed)
        self._scene.selectionChanged.connect(self._on_selection_changed)
        self._scene.focus_region_requested.connect(self._enter_focus_mode)

    # ------------------------------------------------------------------
    # Public API — bot loading
    # ------------------------------------------------------------------

    def setup(self, config: BotConfig) -> None:
        """Load a BotConfig. Enters RUN mode with the graph ready."""
        self._exit_focus_mode()
        self._close_search()
        self._tab_idx = 0
        _inject_required_options(config)
        self._config = config
        # Auto-layout when no positions are saved and the setting is enabled
        if (
            config.states
            and all(s.position is None for s in config.states)
            and SettingsManager.instance().get("canvas", "auto_layout_on_load", default=True)
        ):
            from bartlebot.gui.canvas.layout import auto_layout

            auto_layout(config)
        self._scene.setup(config)
        self._param_panel.show_idle()
        self._view.fit_scene()
        self._minimap.fit_to_scene()
        self._reposition_banner()
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
            self._run_pause_btn.setToolTip(f"Cannot run: fix {self._n_validation_errors} {noun} first")
        elif self._run_externally_enabled:
            self._run_pause_btn.setEnabled(True)
            self._run_pause_btn.setToolTip("Bot is valid — click to run")
        else:
            self._run_pause_btn.setEnabled(False)
            self._run_pause_btn.setToolTip("")

    def eventFilter(self, obj, event) -> bool:
        if obj is self._view:
            if event.type() == QEvent.Type.Resize:
                self._reposition_banner()
            elif event.type() == QEvent.Type.KeyPress:
                key = event.key()
                mods = event.modifiers()
                if key == Qt.Key.Key_Escape and self._focus_region_id is not None:
                    self._exit_focus_mode()
                    return True
                if key == Qt.Key.Key_Tab and not (mods & Qt.KeyboardModifier.ShiftModifier):
                    self._tab_navigate(1)
                    return True
                if key in (Qt.Key.Key_Backtab,) or (key == Qt.Key.Key_Tab and mods & Qt.KeyboardModifier.ShiftModifier):
                    self._tab_navigate(-1)
                    return True
        return super().eventFilter(obj, event)

    def _reposition_banner(self) -> None:
        """Pin overlays: validation banner top-right, minimap bottom-right, search bar top-center, focus crumb top-left."""
        self._validation_banner.adjustSize()
        x = self._view.width() - self._validation_banner.width() - 4
        self._validation_banner.move(x, 4)
        # Minimap: bottom-right corner
        mx = self._view.width() - self._minimap.width() - 8
        my = self._view.height() - self._minimap.height() - 8
        self._minimap.move(mx, my)
        # Search bar: top-center
        if hasattr(self, "_search_bar"):
            self._search_bar.adjustSize()
            sx = (self._view.width() - self._search_bar.width()) // 2
            self._search_bar.move(sx, 8)
        # Focus breadcrumb: top-left
        if hasattr(self, "_focus_crumb"):
            self._focus_crumb.adjustSize()
            self._focus_crumb.move(8, 8)

    def _apply_canvas_settings(self) -> None:
        """Re-read canvas settings and update live widgets. Called after Settings dialog OK."""
        sm = SettingsManager.instance()
        self._minimap.setVisible(sm.get("canvas", "minimap_visible", default=True))
        self._reposition_banner()

    def _active_state_label(self) -> str:
        """Return the display label for the current active state, falling back to its id."""
        if not self._active_state:
            return ""
        if self._config:
            state = next((s for s in self._config.states if s.id == self._active_state), None)
            if state and state.label:
                return state.label
        return self._active_state

    def _emit_bot_status(self) -> None:
        """Emit bot_status_changed with current run/edit state."""
        if self._mode == BotMode.EDIT:
            self.bot_status_changed.emit("#888888", "Editing", "", "")
            return
        color = _STATUS_DOT_COLORS.get(self._run_state, "#888888")
        if self._run_state == _IDLE:
            self.bot_status_changed.emit(color, "Idle", "", "")
        elif self._run_state == _RUNNING:
            self.bot_status_changed.emit(color, "Running", self._active_state_label(), self._elapsed_str())
        elif self._run_state == _PAUSED:
            self.bot_status_changed.emit(color, "Paused", self._active_state_label(), self._elapsed_str())

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
        self._run_pause_btn.setIcon(svg_icon("pause"))
        self._run_pause_btn.setEnabled(True)
        self.stop_button.setEnabled(True)
        self._edit_btn.setEnabled(False)
        self._start_time = time.monotonic()
        self._tick_timer.start()
        self._emit_bot_status()
        self.tab_switch.emit("log")
        logger.info("=== Bot started ===")

    def set_paused(self) -> None:
        self._run_state = _PAUSED
        self._run_pause_btn.setIcon(svg_icon("play"))
        self._run_pause_btn.setEnabled(True)
        self._emit_bot_status()
        logger.info("=== Paused ===")

    def set_idle(self) -> None:
        self._run_state = _IDLE
        self._run_pause_btn.setIcon(svg_icon("play"))
        self._run_pause_btn.setEnabled(False)  # re-enabled by set_run_enabled
        self.stop_button.setEnabled(False)
        self._edit_btn.setEnabled(self._config is not None)
        self._tick_timer.stop()
        if self._active_state and self._active_state in self._scene._node_items:
            self._scene._node_items[self._active_state].set_active(False)
        self._active_state = None
        self._emit_bot_status()
        logger.info("=== Bot stopped ===")

    def set_state(self, state_id: str) -> None:
        """Highlight the active FSM state node."""
        if self._active_state and self._active_state in self._scene._node_items:
            self._scene._node_items[self._active_state].set_active(False)
        self._active_state = state_id
        if state_id in self._scene._node_items:
            self._scene._node_items[state_id].set_active(True)
        self._emit_bot_status()
        logger.info("Entered: %s", state_id)

    def on_transition(self, prev_state: str, result: str, next_state: str) -> None:
        if prev_state == next_state:
            logger.info("Result: %s → (retry %s)", result, next_state)
        else:
            logger.info("Result: %s → %s", result, next_state)

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
            self._run_pause_btn.setIcon(svg_icon("pause"))
            self._emit_bot_status()
            logger.info("=== Resumed ===")
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

    def _on_layout_clicked(self) -> None:
        if self._config is None or self._focus_region_id is not None:
            return
        from bartlebot.gui.canvas.layout import auto_layout

        self._scene._flush_positions()
        auto_layout(self._config)
        self._scene.setup(self._config)
        self._view.fit_scene()
        self._minimap.fit_to_scene()
        self.config_changed.emit()

    # ------------------------------------------------------------------
    # Focus Mode
    # ------------------------------------------------------------------

    def _enter_focus_mode(self, region_id: str) -> None:
        """Isolate a region's nodes in the view; hide everything else."""
        if self._config is None:
            return
        region = next((r for r in self._config.regions if r.id == region_id), None)
        if region is None:
            return
        self._exit_focus_mode()  # clean up any existing focus first
        self._focus_region_id = region_id
        member_set = set(region.members)

        for sid, node in self._scene._node_items.items():
            node.setVisible(sid in member_set)
        for s, _, d, edge in self._scene._edge_items:
            edge.setVisible(s in member_set and d in member_set)
        for rid, ri in self._scene._region_items.items():
            ri.setVisible(rid == region_id)

        self._focus_crumb_label.setText(f"Viewing: {region.label}")
        self._focus_crumb.setVisible(True)
        self._reposition_banner()

    def _exit_focus_mode(self) -> None:
        """Restore all nodes/edges/regions to visible; hide the breadcrumb."""
        if self._focus_region_id is None:
            return
        self._focus_region_id = None
        for node in self._scene._node_items.values():
            node.setVisible(True)
        for *_, edge in self._scene._edge_items:
            edge.setVisible(True)
        for ri in self._scene._region_items.values():
            ri.setVisible(True)
        if hasattr(self, "_focus_crumb"):
            self._focus_crumb.setVisible(False)

    # ------------------------------------------------------------------
    # Tab navigation
    # ------------------------------------------------------------------

    def _tab_navigate(self, direction: int) -> None:
        """Cycle node selection by BFS order and center the view on the node."""
        order = self._scene._get_tab_order()
        if not order:
            return
        selected = [item for item in self._scene.selectedItems() if isinstance(item, NodeItem)]
        if selected and selected[0].state_id in order:
            cur_idx = order.index(selected[0].state_id)
        else:
            cur_idx = self._tab_idx - direction  # step will land on _tab_idx
        next_idx = (cur_idx + direction) % len(order)
        self._tab_idx = next_idx
        node = self._scene._node_items.get(order[next_idx])
        if node:
            self._scene.clearSelection()
            if self._mode == BotMode.EDIT:
                node.setSelected(True)
            self._view.centerOn(node)

    # ------------------------------------------------------------------
    # Quick search
    # ------------------------------------------------------------------

    def _show_search_bar(self) -> None:
        self._search_bar.setVisible(True)
        self._reposition_banner()
        self._search_bar.focus_input()
        self._on_search_text_changed(self._search_bar.current_text())

    def _close_search(self) -> None:
        if hasattr(self, "_search_bar"):
            self._search_bar.setVisible(False)
        self._search_matches.clear()
        self._search_match_idx = 0
        for node in self._scene._node_items.values():
            node.set_search_match(False)

    def _on_search_text_changed(self, text: str) -> None:
        config = self._scene._config
        if not config:
            self._search_matches.clear()
            self._search_bar.set_count(0, 0)
            return
        q = text.strip().lower()
        matches: list[str] = []
        if q:
            for state in config.states:
                if q in (state.label or "").lower() or q in state.id.lower() or q in state.node_type.lower():
                    matches.append(state.id)
        self._search_matches = matches
        self._search_match_idx = 0
        # Apply highlights
        for sid, node in self._scene._node_items.items():
            node.set_search_match(sid in matches, is_current=(bool(matches) and sid == matches[0]))
        if matches:
            first = self._scene._node_items.get(matches[0])
            if first:
                self._view.centerOn(first)
        self._search_bar.set_count(1 if matches else 0, len(matches))
        self._reposition_banner()

    def _on_search_next(self) -> None:
        if not self._search_matches:
            return
        old = self._search_matches[self._search_match_idx]
        self._search_match_idx = (self._search_match_idx + 1) % len(self._search_matches)
        new = self._search_matches[self._search_match_idx]
        if old != new:
            old_node = self._scene._node_items.get(old)
            if old_node:
                old_node.set_search_match(True, is_current=False)
        new_node = self._scene._node_items.get(new)
        if new_node:
            new_node.set_search_match(True, is_current=True)
            self._view.centerOn(new_node)
        self._search_bar.set_count(self._search_match_idx + 1, len(self._search_matches))
        self._reposition_banner()

    def _on_search_prev(self) -> None:
        if not self._search_matches:
            return
        old = self._search_matches[self._search_match_idx]
        self._search_match_idx = (self._search_match_idx - 1) % len(self._search_matches)
        new = self._search_matches[self._search_match_idx]
        if old != new:
            old_node = self._scene._node_items.get(old)
            if old_node:
                old_node.set_search_match(True, is_current=False)
        new_node = self._scene._node_items.get(new)
        if new_node:
            new_node.set_search_match(True, is_current=True)
            self._view.centerOn(new_node)
        self._search_bar.set_count(self._search_match_idx + 1, len(self._search_matches))
        self._reposition_banner()

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def _apply_mode(self, mode: BotMode) -> None:
        self._exit_focus_mode()
        self._mode = mode
        if mode == BotMode.RUN:
            self._center_stack.setCurrentIndex(_TB_RUN)
            self._right_stack.setCurrentIndex(_TB_RUN)
            self._scene.set_interactive(False)
            self._param_panel.show_idle()
            self._emit_bot_status()
        else:
            self._center_stack.setCurrentIndex(_TB_EDIT)
            self._right_stack.setCurrentIndex(_TB_EDIT)
            self._scene.set_interactive(True)
            self._emit_bot_status()

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
        self._minimap.fit_to_scene()
        if self._search_bar.isVisible():
            self._on_search_text_changed(self._search_bar.current_text())
        self.config_changed.emit()

    def _update_validation_badge(self) -> None:
        config = self._scene._config
        if config is None:
            self._validation_banner.setVisible(False)
            self._n_validation_errors = 0
            self._apply_run_btn_state()
            return

        error_lines: list[str] = []
        warning_lines: list[str] = []
        for state in config.states:
            name = state.label or state.id
            _, _, err_reasons, warn_reasons = _validate_node(state, config)
            for r in err_reasons:
                error_lines.append(f"{name}: {r}")
            for r in warn_reasons:
                warning_lines.append(f"{name}: {r}")

        n_errors = len(error_lines)
        n_warnings = len(warning_lines)

        if error_lines:
            noun = "error" if n_errors == 1 else "errors"
            self._error_pill.setText(f"\u25cf {n_errors} {noun}")
            self._error_pill.setToolTip("\n".join(error_lines))
            self._error_pill.setVisible(True)
        else:
            self._error_pill.setVisible(False)

        if warning_lines:
            noun = "warning" if n_warnings == 1 else "warnings"
            self._warning_pill.setText(f"\u25b2 {n_warnings} {noun}")
            n_orphans = sum(1 for line in warning_lines if "Unreachable from initial state" in line)
            tip = "\n".join(warning_lines)
            if n_orphans:
                noun_o = "state" if n_orphans == 1 else "states"
                tip += f"\n\nClick to delete {n_orphans} orphaned {noun_o}"
            self._warning_pill.setToolTip(tip)
            self._warning_pill.setVisible(True)
        else:
            self._warning_pill.setVisible(False)

        self._validation_banner.setVisible(bool(error_lines or warning_lines))
        if error_lines or warning_lines:
            self._reposition_banner()
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
                    for s, _w, _d, edge in self._scene._edge_items:
                        if s == state.id:
                            edge._rebuild()

    def _cleanup_orphans(self) -> None:
        config = self._scene._config
        if config is None:
            return
        reachable = _reachable_states(config)
        orphan_ids = [s.id for s in config.states if s.id not in reachable]
        for sid in orphan_ids:
            self._scene._delete_node(sid)

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
            state = next((s for s in self._scene._config.states if s.id == item.state_id), None)
            if state:
                self._param_panel.show_state(state)
                self.tab_switch.emit("properties")
        elif isinstance(item, EdgeItem) and self._scene._config:
            for s, w, d, ei in self._scene._edge_items:
                if ei is item:
                    trans = next(
                        (t for t in self._scene._config.transitions if t.source == s and t.dest == d and t.when == w),
                        None,
                    )
                    if trans:
                        src_state = next((st for st in self._scene._config.states if st.id == s), None)
                        src_returns: list[str] = []
                        if src_state:
                            spec = NODE_SPECS.get(src_state.node_type)
                            if spec:
                                src_returns = [r.name for r in spec.returns]
                        option_names = [opt.name for opt in self._scene._config.options]
                        self._param_panel.show_transition(trans, src_returns, option_names)
                        self.tab_switch.emit("properties")
                    break
        else:
            self._param_panel.show_idle()

    # ------------------------------------------------------------------
    # Log helpers
    # ------------------------------------------------------------------

    def append_log(self, msg: str, level: int = logging.INFO) -> None:
        """Public log entry point for node messages. Filtered by the level dropdown."""
        threshold = _NAME_TO_LEVEL.get(self._log_level_combo.currentText(), logging.DEBUG)
        if level < threshold:
            return
        color = _LOG_COLORS.get(level, "#55cc55")

        fmt_plain = QTextCharFormat()
        fmt_colored = QTextCharFormat()
        fmt_colored.setForeground(QColor(color))

        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(f"[{self._elapsed_str()}] ", fmt_plain)
        cursor.insertText(f" {msg}\n", fmt_colored)
        self._log.moveCursor(QTextCursor.MoveOperation.End)

    def append_log_formatted(self, formatted_text: str, level: int) -> None:
        """Append a pre-formatted log line from the Python logging bridge."""
        threshold = _NAME_TO_LEVEL.get(self._log_level_combo.currentText(), logging.DEBUG)
        if level < threshold:
            return
        color = _LOG_COLORS.get(level, "#55cc55")
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(formatted_text + "\n", fmt)
        self._log.moveCursor(QTextCursor.MoveOperation.End)

    def _append_log(self, text: str) -> None:
        self._log.appendPlainText(text)
        self._log.moveCursor(QTextCursor.MoveOperation.End)

    def _tick(self) -> None:
        self._emit_bot_status()

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


def _validate_node(state, config) -> tuple[bool, bool, list[str], list[str]]:
    """Return (is_error, is_warning, error_reasons, warning_reasons) for a state.

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
                    warning_reasons.append(f"Uncovered {label}: {', '.join(repr(r) for r in uncovered)}")

    if config.initial and state.id != config.initial:
        if state.id not in _reachable_states(config):
            warning_reasons.append("Unreachable from initial state")

    return bool(error_reasons), bool(warning_reasons), error_reasons, warning_reasons


def _reachable_states(config: BotConfig) -> set[str]:
    if not config.initial:
        return set()
    reachable: set[str] = set()
    queue = [config.initial]
    while queue:
        sid = queue.pop()
        if sid in reachable:
            continue
        reachable.add(sid)
        for t in config.transitions:
            if t.source == sid:
                queue.append(t.dest)
    return reachable


def _dist(a: QPointF, b: QPointF) -> float:
    return math.hypot(a.x() - b.x(), a.y() - b.y())


def _unique_id(existing: list[str]) -> str:
    i = 1
    while f"state_{i}" in existing:
        i += 1
    return f"state_{i}"


# ---------------------------------------------------------------------------
# _ColorPickerDialog — six-swatch region palette picker
# ---------------------------------------------------------------------------


class _ColorPickerDialog(QWidget):
    """Minimal six-swatch palette dialog for region color selection."""

    def __init__(self, current_key: str, parent=None) -> None:
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QVBoxLayout

        # Re-parent as QDialog so exec() works
        self._dialog = QDialog(parent)
        self._dialog.setWindowTitle("Choose Color")
        self._selected = current_key
        self._swatch_btns: dict[str, QPushButton] = {}

        swatch_row = QHBoxLayout()
        swatch_row.setSpacing(8)
        for key in THEME.REGION_PALETTE:
            btn = QPushButton()
            btn.setFixedSize(28, 28)
            color_hex = THEME.region_color(key).name()
            btn.setStyleSheet(
                f"QPushButton {{ background: {color_hex}; border: 2px solid transparent; border-radius: 14px; }}"
                f"QPushButton:hover {{ border-color: white; }}"
            )
            btn.clicked.connect(lambda _=None, k=key: self._select(k))
            self._swatch_btns[key] = btn
            swatch_row.addWidget(btn)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._dialog.accept)
        buttons.rejected.connect(self._dialog.reject)

        layout = QVBoxLayout(self._dialog)
        layout.addLayout(swatch_row)
        layout.addWidget(buttons)
        self._dialog.setLayout(layout)
        self._highlight(current_key)

    def _select(self, key: str) -> None:
        self._selected = key
        self._highlight(key)

    def _highlight(self, key: str) -> None:
        for k, btn in self._swatch_btns.items():
            color_hex = THEME.region_color(k).name()
            border = "white" if k == key else "transparent"
            btn.setStyleSheet(
                f"QPushButton {{ background: {color_hex}; border: 2px solid {border}; border-radius: 14px; }}"
                f"QPushButton:hover {{ border-color: white; }}"
            )

    def exec(self):
        return self._dialog.exec()

    def selected_color(self) -> str:
        return self._selected
