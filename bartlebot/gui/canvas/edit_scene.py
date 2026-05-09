"""Interactive QGraphicsScene for the bot canvas editor.

EditScene        — manages interactive nodes, edge dragging, deletion, and positions.
_EditView        — GraphView subclass with interactive=True.
_ColorPickerDialog — six-swatch region palette picker used by EditScene.

Module-level helpers (_node_returns, _validate_node, _dist, _unique_id, etc.) are
also exported here so BotCanvas can use them without circular imports.
"""

import math
from collections import defaultdict

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QCursor, QKeyEvent, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QMessageBox,
    QPushButton,
    QWidget,
)

from bartlebot.bot.schema import BotConfig, BotOptionModel, RegionModel, StateModel, TransitionModel
from bartlebot.gui.canvas.edge_item import (
    EdgeItem,
    bridge_y_above,
    bridge_y_below,
    is_above_routed,
)
from bartlebot.gui.canvas.graph_view import GraphView
from bartlebot.gui.canvas.node_item import GRID_H, GRID_W, NODE_H, NODE_W, NodeItem
from bartlebot.gui.canvas.region_item import RegionItem
from bartlebot.gui.theme import THEME
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS, NODE_REGISTRY, NODE_SPECS

# ---------------------------------------------------------------------------
# Constants used only by the editor
# ---------------------------------------------------------------------------

_PORT_HIT_RADIUS = 16
_AUTO_SPACING = GRID_W  # auto-layout column stride matches grid width

_SCROLL_MARGIN = 40  # px from viewport edge at which auto-scroll activates during drag
_SCROLL_SPEED = 10  # max px to scroll per mouseMoveEvent while in the margin zone


def _spiral_ring(r: int):
    """Yield (dx, dy) offsets at Chebyshev distance r from (0, 0).

    Order: right column top-to-bottom, bottom row right-to-left,
    left column bottom-to-top, top row left-to-right.  This biases
    new nodes toward the right/below the origin, matching natural
    canvas reading order.
    """
    if r == 0:
        yield (0, 0)
        return
    for dy in range(-r, r + 1):
        yield (r, dy)
    for dx in range(r - 1, -r - 1, -1):
        yield (dx, r)
    for dy in range(r - 1, -r - 1, -1):
        yield (-r, dy)
    for dx in range(-r + 1, r):
        yield (dx, -r)


def _auto_scroll_view(view, scene_pos: QPointF) -> None:
    """Scroll the viewport when the drag cursor enters the margin zone near an edge."""
    vp_pos = view.mapFromScene(scene_pos)
    rect = view.viewport().rect()
    dx = dy = 0
    if vp_pos.x() < _SCROLL_MARGIN:
        dx = -((_SCROLL_MARGIN - vp_pos.x()) * _SCROLL_SPEED // _SCROLL_MARGIN + 1)
    elif vp_pos.x() > rect.width() - _SCROLL_MARGIN:
        dx = (vp_pos.x() - (rect.width() - _SCROLL_MARGIN)) * _SCROLL_SPEED // _SCROLL_MARGIN + 1
    if vp_pos.y() < _SCROLL_MARGIN:
        dy = -((_SCROLL_MARGIN - vp_pos.y()) * _SCROLL_SPEED // _SCROLL_MARGIN + 1)
    elif vp_pos.y() > rect.height() - _SCROLL_MARGIN:
        dy = (vp_pos.y() - (rect.height() - _SCROLL_MARGIN)) * _SCROLL_SPEED // _SCROLL_MARGIN + 1
    if dx:
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + dx)
    if dy:
        view.verticalScrollBar().setValue(view.verticalScrollBar().value() + dy)


# ---------------------------------------------------------------------------
# Module-level helpers (also imported by BotCanvas)
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

    graph_changed = pyqtSignal()
    about_to_change = pyqtSignal(str)  # emitted BEFORE config is mutated; carries description
    focus_region_requested = pyqtSignal(str)
    node_type_chosen = pyqtSignal(str, object)  # (node_type, QPointF canvas position)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._node_items: dict[str, NodeItem] = {}
        self._edge_items: list[tuple[str, str | None, str, EdgeItem]] = []
        self._region_items: dict[str, RegionItem] = {}
        self._drag_src: NodeItem | None = None
        self._drag_when: str | None = None
        self._temp_edge: QGraphicsLineItem | None = None
        self._drag_snap_edge: EdgeItem | None = None
        self._auto_scrolling: bool = False
        self._node_drag_scroll_pos: QPointF | None = None
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(16)
        self._scroll_timer.timeout.connect(self._on_scroll_timer)
        self._interactive_mode: bool = True
        self._clipboard: tuple[list[dict], list[dict]] | None = None
        self._in_structural_edit: bool = False  # re-entrancy guard; suppresses nested about_to_change
        self._snap_enabled: bool = False
        self._grid_style: str = "dots"  # "dots" | "lines" | "none"
        self._cycle_pos: QPointF = QPointF()
        self._cycle_edges: list[EdgeItem] = []
        self._cycle_idx: int = -1

    # ------------------------------------------------------------------
    # Grid / snap
    # ------------------------------------------------------------------

    def set_snap_enabled(self, enabled: bool) -> None:
        """Toggle snap-to-grid for all nodes and repaint the background grid."""
        self._snap_enabled = enabled
        for node in self._node_items.values():
            node.set_snap_enabled(enabled)
        self.update()

    def set_grid_style(self, style: str) -> None:
        """Set background grid rendering style ("dots", "lines", or "none")."""
        self._grid_style = style
        self.update()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawBackground(painter, rect)
        if not self._snap_enabled or self._grid_style == "none":
            return
        grid_color = THEME.qcolor("grid_dot")
        x_off = 0.0
        y_off = 0.0
        if self._grid_style == "lines":
            painter.setPen(QPen(grid_color, 1.0))
            x = math.floor((rect.left() - x_off) / GRID_W) * GRID_W + x_off
            while x <= rect.right():
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                x += GRID_W
            y = math.floor((rect.top() - y_off) / GRID_H) * GRID_H + y_off
            while y <= rect.bottom():
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                y += GRID_H
        else:
            # Dots sit at (GRID_W*i, GRID_H*j) — node left edges and vertical centers.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(grid_color))
            dot_r = 2.0
            x = math.floor((rect.left() - x_off) / GRID_W) * GRID_W + x_off
            while x <= rect.right():
                y = math.floor((rect.top() - y_off) / GRID_H) * GRID_H + y_off
                while y <= rect.bottom():
                    painter.drawEllipse(QPointF(x, y), dot_r, dot_r)
                    y += GRID_H
                x += GRID_W

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def teardown(self) -> None:
        """Remove all items and reset internal state without loading a new config."""
        for ri in self._region_items.values():
            try:
                self.changed.disconnect(ri._recompute_rect)
            except (RuntimeError, TypeError):
                pass
        self._region_items.clear()
        self.clear()
        self._node_items.clear()
        self._edge_items.clear()
        self._drag_src = None
        self._drag_when = None
        self._temp_edge = None
        self._drag_snap_edge = None
        self._config = None

    def _pre_edit(self, desc: str) -> None:
        """Emit about_to_change unless we are already inside a compound structural edit."""
        if not self._in_structural_edit:
            self.about_to_change.emit(desc)

    def setup(self, config: BotConfig) -> None:
        """Rebuild the scene from a BotConfig."""
        self.teardown()
        self._config = config

        states = config.states
        transitions = config.transitions

        for i, state in enumerate(states):
            pos = state.position
            if pos and isinstance(pos, dict):
                x = pos.get("x", 0)
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
            )
            node.set_error(is_error, reasons)
            node.set_warning(is_warning, reasons)
            node.set_snap_enabled(self._snap_enabled)
            node.setPos(x, y)
            self.addItem(node)
            self._node_items[state.id] = node

        # Pre-compute shared bridge_y for same-destination backward-edge groups.
        # Backward edges from same-Y nodes that share a (dest, when) pair are snapped
        # to a common corridor so they overlap cleanly instead of forming a spaghetti
        # bundle of nearly-identical slightly-offset paths.
        #
        # Two separate corridors are computed:
        #   below — all backward edges except topmost-port edges (FAILURE, FALSE, etc.)
        #   above — topmost-port (index-0) backward edges on nodes with ≥2 returns
        #           (SUCCESS, TRUE, etc.) — routed above the node row so they occupy a
        #           separate vertical band and do not cross the failure-path edges below.
        #
        # Y-band outliers (source Y differs from destination Y by ≥ one node height)
        # are excluded from corridor grouping and route independently.
        # bridge_y_below / bridge_y_above / is_above_routed are imported from
        # edge_item so the corridor geometry stays in one place.

        # Collect unique (s, w, d) tuples from transitions (deduplicated).
        unique_edges: list[tuple[str, str | None, str]] = []
        _seen: set[tuple[str, str | None, str]] = set()
        for t in transitions:
            key = (t.source, t.when, t.dest)
            if key not in _seen:
                _seen.add(key)
                unique_edges.append(key)

        # Group by (when, y_row) so all backward edges in the same horizontal band
        # with the same when-label share a corridor, regardless of destination.
        # Using destination as the grouping key caused edges to unique destinations
        # to route independently via a port-midpoint heuristic, producing a different
        # bridge Y than the node-bounds formula used for multi-destination groups.
        below_groups: dict[tuple[str | None, int], list[tuple[str, str | None, str]]] = defaultdict(list)
        above_groups: dict[tuple[str | None, int], list[tuple[str, str | None, str]]] = defaultdict(list)
        for s, w, d in unique_edges:
            if s == d or s not in self._node_items or d not in self._node_items:
                continue
            sn, dn = self._node_items[s], self._node_items[d]
            if dn.pos().x() >= sn.pos().x():
                continue  # forward edge
            if abs(sn.pos().y() - dn.pos().y()) >= sn._height:
                continue  # Y-band outlier — routes independently
            y_row = round(sn.pos().y() / GRID_H)
            if is_above_routed(sn, w):
                above_groups[(w, y_row)].append((s, w, d))
            else:
                below_groups[(w, y_row)].append((s, w, d))

        bridge_y_map: dict[tuple[str, str | None, str], float] = {}
        for group_edges in below_groups.values():
            shared_y = max(bridge_y_below(self._node_items[s], self._node_items[d]) for s, w, d in group_edges)
            for key in group_edges:
                bridge_y_map[key] = shared_y
        for group_edges in above_groups.values():
            shared_y = min(bridge_y_above(self._node_items[s], self._node_items[d]) for s, w, d in group_edges)
            for key in group_edges:
                bridge_y_map[key] = shared_y

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
            has_flags = bool(t.flags or t.flag_groups)
            if s == d:
                edge = EdgeItem(sn, sn, when=w, has_flags=has_flags)
            else:
                edge = EdgeItem(sn, dn, when=w, bridge_y_override=bridge_y_map.get(key), has_flags=has_flags)
            self.addItem(edge)
            sn.add_edge(edge)
            if dn is not sn:
                dn.add_edge(edge)
            self._edge_items.append((s, w, d, edge))

        # Compute forward-edge offsets and fan arrival ports now that all edges
        # are registered with their nodes (port_in_for needs the full edge list).
        self._recompute_offsets()

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

    def refresh_edge_flags(self, transition) -> None:
        """Refresh the dotted-pen flag indicator on the edge matching *transition*."""
        has_flags = bool(transition.flags or transition.flag_groups)
        for s, w, d, edge in self._edge_items:
            if s == transition.source and d == transition.dest and w == transition.when:
                edge.set_has_flags(has_flags)
                break

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

    def find_empty_grid_cell(self, viewport_center: QPointF) -> QPointF:
        """Return the top-left scene position of the nearest unoccupied grid cell to viewport_center.

        Searches outward in a spiral so new nodes never land on top of an existing one.
        Falls back to viewport_center (minus half node dims) if the canvas is pathologically full.
        """
        occupied: set[tuple[int, int]] = set()
        for item in self._node_items.values():
            p = item.pos()
            occupied.add((round(p.x() / GRID_W), round((p.y() + NODE_H / 2) / GRID_H)))

        # Grid cell whose center is nearest to viewport_center
        start_cx = round((viewport_center.x() - NODE_W / 2) / GRID_W)
        start_cy = round(viewport_center.y() / GRID_H)

        for radius in range(31):
            for dx, dy in _spiral_ring(radius):
                cell = (start_cx + dx, start_cy + dy)
                if cell not in occupied:
                    return QPointF(cell[0] * GRID_W, cell[1] * GRID_H - NODE_H / 2)

        return QPointF(viewport_center.x() - NODE_W / 2, viewport_center.y() - NODE_H / 2)

    def add_state(self, pos: QPointF) -> None:
        """Open the node browser dialog and emit node_type_chosen on confirmation."""
        if self._config is None:
            return
        from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog

        view = self.views()[0] if self.views() else None
        dlg = NodeBrowserDialog(view, mode="add")
        if dlg.exec() and dlg.selected_node_type:
            self.node_type_chosen.emit(dlg.selected_node_type, pos)

    def create_state_at(self, node_type: str, pos: QPointF) -> None:
        """Instantiate a state of the given node_type at pos. Called after dialog confirmation."""
        if self._config is None:
            return
        self._pre_edit("Create Node")
        state_id = _unique_id([s.id for s in self._config.states])
        default_params = {f.key: f.default for f in NODE_PARAM_SCHEMAS.get(node_type, []) if f.default is not None}
        state = StateModel(
            id=state_id,
            node_type=node_type,
            position={"x": pos.x(), "y": pos.y() + NODE_H / 2},
            params=default_params,
        )
        self._config.states.append(state)
        returns = _node_returns(node_type)
        node = NodeItem(state_id, None, interactive=True, returns=returns, is_terminal=True, node_type=node_type)
        node.set_snap_enabled(self._snap_enabled)
        node.setPos(pos)
        self.addItem(node)
        self._node_items[state_id] = node
        self._update_scene_rect()
        view = self.views()[0] if self.views() else None
        if view is not None:
            view.smooth_pan_if_offscreen(node)
        self.graph_changed.emit()

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

            # Click-cycle: when multiple edges overlap at a point, repeated clicks
            # at the same location cycle through them for individual selection.
            hit = list(self.items(scene_pos))
            hit_nodes = [i for i in hit if isinstance(i, NodeItem)]
            hit_edges = [i for i in hit if isinstance(i, EdgeItem)]
            if not hit_nodes and len(hit_edges) > 1:
                # Prune stale references from previous cycles.
                self._cycle_edges = [e for e in self._cycle_edges if e.scene() is self]
                near = (
                    self._cycle_edges
                    and abs(scene_pos.x() - self._cycle_pos.x()) < 10
                    and abs(scene_pos.y() - self._cycle_pos.y()) < 10
                )
                if near:
                    self._cycle_idx = (self._cycle_idx + 1) % len(self._cycle_edges)
                else:
                    self._cycle_pos = scene_pos
                    self._cycle_edges = hit_edges
                    self._cycle_idx = 0
                self.clearSelection()
                self._cycle_edges[self._cycle_idx].setSelected(True)
                event.accept()
                return

        self._cycle_edges = []
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_src is not None and self._temp_edge is not None:
            src_pt = self._drag_src.port_out_for(self._drag_when)
            cur = event.scenePos()

            snap_edge = self._edge_near(cur)
            if snap_edge is not None and snap_edge._src is self._drag_src and snap_edge._when == self._drag_when:
                snap_edge = None
            if snap_edge is not self._drag_snap_edge:
                if self._drag_snap_edge is not None:
                    self._drag_snap_edge.set_snap_target(False)
                self._drag_snap_edge = snap_edge
                if self._drag_snap_edge is not None:
                    self._drag_snap_edge.set_snap_target(True)

            end_pt = self._drag_snap_edge._dst.port_in() if self._drag_snap_edge else cur
            self._temp_edge.setLine(src_pt.x(), src_pt.y(), end_pt.x(), end_pt.y())

            views = self.views()
            if views and not self._auto_scrolling:
                self._auto_scrolling = True
                _auto_scroll_view(views[0], cur)
                self._auto_scrolling = False
            event.accept()
            return
        if isinstance(self.mouseGrabberItem(), NodeItem):
            self._node_drag_scroll_pos = event.scenePos()
            if not self._scroll_timer.isActive():
                self._scroll_timer.start()
        super().mouseMoveEvent(event)

    def _on_scroll_timer(self) -> None:
        if self._node_drag_scroll_pos is None:
            self._scroll_timer.stop()
            return
        views = self.views()
        if not views:
            self._scroll_timer.stop()
            return
        view = views[0]
        vp_pos = view.mapFromScene(self._node_drag_scroll_pos)
        rect = view.viewport().rect()
        in_margin = (
            vp_pos.x() < _SCROLL_MARGIN
            or vp_pos.x() > rect.width() - _SCROLL_MARGIN
            or vp_pos.y() < _SCROLL_MARGIN
            or vp_pos.y() > rect.height() - _SCROLL_MARGIN
        )
        if in_margin:
            _auto_scroll_view(view, self._node_drag_scroll_pos)
        else:
            self._scroll_timer.stop()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._scroll_timer.stop()
        self._node_drag_scroll_pos = None
        if self._drag_src is not None:
            if self._temp_edge:
                self.removeItem(self._temp_edge)
                self._temp_edge = None
            if event.button() == Qt.MouseButton.LeftButton:
                if self._drag_snap_edge is not None:
                    self._drag_snap_edge.set_snap_target(False)
                snap = self._drag_snap_edge
                self._drag_snap_edge = None

                if snap is not None:
                    self._create_transition(self._drag_src, snap._dst, self._drag_when)
                else:
                    dst = self._node_near_port_in(event.scenePos())
                    if dst is not None:
                        self._create_transition(self._drag_src, dst, self._drag_when)
            else:
                if self._drag_snap_edge is not None:
                    self._drag_snap_edge.set_snap_target(False)
                self._drag_snap_edge = None
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
            sid = item.state_id
            act = QAction("Delete State", menu)
            act.triggered.connect(lambda: self._delete_node(item.state_id))
            menu.addAction(act)
            act_help = QAction("Node Help", menu)
            act_help.triggered.connect(lambda _=None, s=sid: self._open_node_help(s))
            menu.addAction(act_help)
            # Determine target nodes: all selected if the clicked node is part of a multi-selection
            selected_nodes = [i for i in self.selectedItems() if isinstance(i, NodeItem)]
            if len(selected_nodes) >= 2 and item in selected_nodes:
                target_ids = [n.state_id for n in selected_nodes]
            else:
                target_ids = [sid]
            is_multi = len(target_ids) > 1
            # Region membership actions
            if self._config and self._config.regions:
                menu.addSeparator()
                for region in self._config.regions:
                    not_members = [i for i in target_ids if i not in region.members]
                    in_members = [i for i in target_ids if i in region.members]
                    rid = region.id
                    rlabel = region.label
                    if not_members:
                        label = (
                            f"Add {len(not_members)} nodes to Region: {rlabel}"
                            if is_multi
                            else f"Add to Region: {rlabel}"
                        )
                        act_add = QAction(label, menu)
                        act_add.triggered.connect(lambda _=None, ids=not_members, r=rid: self._add_to_region(ids, r))
                        menu.addAction(act_add)
                    if in_members:
                        label = (
                            f"Remove {len(in_members)} nodes from Region: {rlabel}"
                            if is_multi
                            else f"Remove from Region: {rlabel}"
                        )
                        act_rem = QAction(label, menu)
                        act_rem.triggered.connect(
                            lambda _=None, ids=in_members, r=rid: self._remove_from_region(ids, r)
                        )
                        menu.addAction(act_rem)
            # Create region from multi-selection
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

    def _open_node_help(self, state_id: str) -> None:
        """Open the node browser in view mode pre-selected on the given state's node type."""
        from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog

        if self._config is None:
            return
        state = next((s for s in self._config.states if s.id == state_id), None)
        if state is None:
            return
        view = self.views()[0] if self.views() else None
        NodeBrowserDialog(view, mode="view", initial_node_type=state.node_type).exec()

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

    def _edge_near(self, pos: QPointF) -> EdgeItem | None:
        """Return the first EdgeItem whose hit-shape (12 px stroke) contains pos."""
        for item in self.items(pos):
            if isinstance(item, EdgeItem):
                return item
        return None

    def _recompute_offsets(self) -> None:
        """Recompute forward-edge mid_x offsets for all (source, dest) groups.

        Handles bidirectional separation and N-parallel-edge spreading.  Also
        calls _rebuild() on every non-loop edge so fan arrival-port positions
        (option A) and Z-channel positions (option C) are both updated in one pass.
        """
        PARALLEL_STEP = 22.0
        sd_groups: dict[tuple[str, str], list] = defaultdict(list)
        for s, w, d, edge in self._edge_items:
            if s != d:
                sd_groups[(s, d)].append((s, w, d, edge))
        non_loop_pairs = {(s, d) for s, _w, d, _ in self._edge_items if s != d}
        for (s, d), group in sd_groups.items():
            group_sorted = sorted(group, key=lambda x: x[1] or "")
            n = len(group_sorted)
            is_bidi = (d, s) in non_loop_pairs
            bidi_base = (PARALLEL_STEP if s < d else -PARALLEL_STEP) if is_bidi else 0.0
            for i, (_s, _w, _d, edge) in enumerate(group_sorted):
                edge._offset = bidi_base + (i - (n - 1) / 2) * PARALLEL_STEP
                edge._rebuild()

    def _create_transition(self, src_node: NodeItem, dst_node: NodeItem, when: str | None = None) -> None:
        if self._config is None:
            return
        s, d = src_node.state_id, dst_node.state_id
        if any(t.source == s and t.dest == d and t.when == when for t in self._config.transitions):
            return
        self._pre_edit("Create Edge")
        t = TransitionModel(trigger="advance", source=s, dest=d, when=when)
        self._config.transitions.append(t)

        edge = EdgeItem(src_node, dst_node, when=when)
        self.addItem(edge)
        src_node.add_edge(edge)
        if dst_node is not src_node:
            dst_node.add_edge(edge)
        self._edge_items.append((s, when, d, edge))
        self._recompute_offsets()
        self._update_scene_rect()
        self.refresh_terminal()
        self.refresh_errors()
        self.graph_changed.emit()

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

    def _paste(self) -> None:
        if self._config is None or self._clipboard is None:
            return
        self._pre_edit("Paste")
        self._in_structural_edit = True
        states_data, transitions_data = self._clipboard

        # Determine (offset_x, offset_y) to apply to every pasted node's stored position.
        # Clipboard positions: p["x"] = node left edge, p["y"] = node center y (StateModel convention).
        positions = [sd["position"] for sd in states_data if sd.get("position")]

        offset_x = 0.0
        offset_y = 0.0
        if positions:
            view = self.views()[0] if self.views() else None
            cursor_in_vp = False
            if view:
                cursor_local = view.viewport().mapFromGlobal(QCursor.pos())
                cursor_in_vp = view.viewport().rect().contains(cursor_local)
                if cursor_in_vp:
                    cursor_scene = view.mapToScene(view.mapFromGlobal(QCursor.pos()))
                    centroid_x = sum(p["x"] for p in positions) / len(positions)
                    centroid_y = sum(p["y"] for p in positions) / len(positions)
                    offset_x = cursor_scene.x() - centroid_x
                    offset_y = cursor_scene.y() - centroid_y

            if not cursor_in_vp:
                # Fallback: start one grid row below the source nodes, preserving X.
                offset_y = GRID_H

            # Shift offset_y downward until no pasted node collides with an existing one.
            # Grid cell key: (col based on left edge, row based on center y) — same as snap formula.
            occupied = {
                (round(item.pos().x() / GRID_W), round((item.pos().y() + NODE_H / 2) / GRID_H))
                for item in self._node_items.values()
            }
            for _ in range(50):
                if not any(
                    (round((p["x"] + offset_x) / GRID_W), round((p["y"] + offset_y) / GRID_H)) in occupied
                    for p in positions
                ):
                    break
                offset_y += GRID_H

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
                    "x": new_sd["position"]["x"] + offset_x,
                    "y": new_sd["position"]["y"] + offset_y,
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
            )
            node.set_snap_enabled(self._snap_enabled)
            if new_sd.get("position"):
                node.setPos(
                    QPointF(
                        new_sd["position"]["x"],
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
        view = self.views()[0] if self.views() else None
        if view is not None:
            new_nodes = [self._node_items[nid] for nid in id_map.values() if nid in self._node_items]
            if new_nodes:
                view.smooth_pan_if_offscreen(new_nodes[0])
        self._in_structural_edit = False
        self.graph_changed.emit()

    def _delete_selected(self) -> None:
        if self._config is None:
            return
        selected = list(self.selectedItems())
        if not selected:
            return
        nodes = [item for item in selected if isinstance(item, NodeItem)]
        edges = [item for item in selected if isinstance(item, EdgeItem)]

        # Single item — keep the original per-item dialogs (show node label / edge endpoints).
        if len(selected) == 1:
            if nodes:
                self._delete_node(nodes[0].state_id)
            elif edges:
                self._delete_edge_item(edges[0])
            return

        # Multiple items — one bulk confirmation.
        selected_node_ids = {n.state_id for n in nodes}
        implicit_transition_count = sum(
            1 for t in self._config.transitions if t.source in selected_node_ids or t.dest in selected_node_ids
        )
        # Edges whose endpoints are not being deleted need separate confirmation in the count.
        standalone_edges = [
            e
            for e in edges
            if not any(s in selected_node_ids or d in selected_node_ids for s, _w, d, ei in self._edge_items if ei is e)
        ]
        total_transitions = implicit_transition_count + len(standalone_edges)

        parts = []
        if nodes:
            parts.append(f"{len(nodes)} state{'s' if len(nodes) != 1 else ''}")
        if total_transitions:
            parts.append(f"{total_transitions} transition{'s' if total_transitions != 1 else ''}")
        summary = " and ".join(parts) if parts else "selection"

        answer = QMessageBox.question(
            self.views()[0] if self.views() else None,
            "Delete Selection",
            f"Delete {summary}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._pre_edit("Delete Selection")
        self._in_structural_edit = True
        for node in nodes:
            self._delete_node(node.state_id, confirm=False)
        for edge in standalone_edges:
            self._delete_edge_item(edge, confirm=False)
        self._in_structural_edit = False

    def _delete_node(self, state_id: str, *, confirm: bool = True) -> bool:
        """Delete a state and all its edges.  Returns False if the user cancels."""
        if self._config is None:
            return False
        if confirm:
            state = next((s for s in self._config.states if s.id == state_id), None)
            label = (state.label or state_id) if state else state_id
            edges_count = sum(1 for t in self._config.transitions if t.source == state_id or t.dest == state_id)
            detail = f" All {edges_count} connected transition(s) will also be deleted." if edges_count else ""
            answer = QMessageBox.question(
                self.views()[0] if self.views() else None,
                "Delete State",
                f"Delete state '{label}'?{detail}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self._pre_edit("Delete Node")
        prev_in_structural = self._in_structural_edit
        self._in_structural_edit = True
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
        self._recompute_offsets()
        self._config.states = [s for s in self._config.states if s.id != state_id]
        if state_id in self._node_items:
            self.removeItem(self._node_items.pop(state_id))
        self.refresh_terminal()
        self.refresh_errors()
        self._flush_regions()
        self._in_structural_edit = prev_in_structural
        self._update_scene_rect()
        self.graph_changed.emit()
        return True

    def _delete_edge_item(self, edge_item: EdgeItem, *, confirm: bool = True) -> bool:
        """Delete a transition edge.  Returns False if the user cancels."""
        if self._config is None:
            return False
        if confirm:
            src = dst = ""
            for s, _w, d, ei in self._edge_items:
                if ei is edge_item:
                    src, dst = s, d
                    break
            answer = QMessageBox.question(
                self.views()[0] if self.views() else None,
                "Delete Transition",
                f"Delete transition from '{src}' to '{dst}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self._pre_edit("Delete Edge")
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
        self._recompute_offsets()
        self.refresh_terminal()
        self.refresh_errors()
        self.graph_changed.emit()
        return True

    def change_edge_endpoints(
        self,
        old_src: str,
        old_when: object,  # str | None
        old_dst: str,
        new_src: str,
        new_dst: str,
    ) -> None:
        """Rebuild a scene edge after its source/dest was changed via the properties panel.

        The TransitionModel itself is already updated by the param panel before this
        is called.  This method updates the visual EdgeItem and all data structures.
        """
        if self._config is None:
            return
        self._pre_edit("Rewire Edge")
        old_when_str: str | None = old_when  # type: ignore[assignment]
        # Locate the edge record
        record_idx: int | None = None
        for i, (s, w, d, _ei) in enumerate(self._edge_items):
            if s == old_src and w == old_when_str and d == old_dst:
                record_idx = i
                break
        if record_idx is None:
            return
        _s, _w, _d, edge_item = self._edge_items[record_idx]

        old_src_node = self._node_items.get(old_src)
        old_dst_node = self._node_items.get(old_dst)
        new_src_node = self._node_items.get(new_src)
        new_dst_node = self._node_items.get(new_dst)
        if new_src_node is None or new_dst_node is None:
            return

        # Unregister from old nodes
        if old_src_node:
            old_src_node.remove_edge(edge_item)
        if old_dst_node and old_dst_node is not old_src_node:
            old_dst_node.remove_edge(edge_item)

        # Update the EdgeItem in-place
        edge_item._src = new_src_node
        edge_item._dst = new_dst_node
        edge_item._is_self_loop = new_src_node is new_dst_node

        # Register with new nodes
        new_src_node.add_edge(edge_item)
        if new_dst_node is not new_src_node:
            new_dst_node.add_edge(edge_item)

        # Update the _edge_items record
        self._edge_items[record_idx] = (new_src, old_when_str, new_dst, edge_item)

        # Recompute all offsets and fan positions now that the topology has changed.
        self._recompute_offsets()

        self._update_scene_rect()
        self.refresh_terminal()
        self.refresh_errors()
        self.graph_changed.emit()

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
        insert_pos = QPointF(mid.x() - NODE_W / 2, mid.y() - NODE_H / 2)

        # Ask the user which node type to insert
        from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog

        view = self.views()[0] if self.views() else None
        dlg = NodeBrowserDialog(view, mode="add")
        if not (dlg.exec() and dlg.selected_node_type):
            return
        node_type = dlg.selected_node_type

        self._pre_edit("Insert State on Edge")
        self._in_structural_edit = True

        # Create a new state at the midpoint with the chosen type
        new_id = _unique_id([st.id for st in self._config.states])
        default_params = {f.key: f.default for f in NODE_PARAM_SCHEMAS.get(node_type, []) if f.default is not None}
        state = StateModel(
            id=new_id,
            node_type=node_type,
            position={"x": insert_pos.x(), "y": mid.y() + NODE_H / 2},
            params=default_params,
        )
        self._config.states.append(state)
        returns = _node_returns(node_type)
        new_node = NodeItem(
            new_id,
            None,
            interactive=True,
            returns=returns,
            is_terminal=True,
            node_type=node_type,
        )
        new_node.set_snap_enabled(self._snap_enabled)
        new_node.setPos(insert_pos)
        self.addItem(new_node)
        self._node_items[new_id] = new_node

        from bartlebot.settings_manager import SettingsManager

        SettingsManager.instance().add_recently_used_node(node_type)

        # Remove original edge (src --w--> dst), then wire src --w--> new --w'--> dst.
        # Carry the original when value to the outgoing edge only if the new node actually
        # returns that value; otherwise fall back to unconditional so the edge is never dead.
        outgoing_when = w if (w is not None and w in new_node._returns) else None
        self._delete_edge_item(edge_item, confirm=False)
        self._create_transition(src_node, new_node, w)
        self._create_transition(new_node, dst_node, outgoing_when)
        self._in_structural_edit = False
        self._update_scene_rect()

    def _flush_positions(self) -> None:
        if self._config is None:
            return
        # Compute all position changes before mutating so about_to_change fires
        # against the unmodified config (snapshot captures current positions).
        updates: dict[str, dict] = {}
        for state in self._config.states:
            node = self._node_items.get(state.id)
            if node is None:
                continue
            new_pos = {
                "x": node.scenePos().x(),
                "y": node.scenePos().y() + node._height / 2,
            }
            if state.position != new_pos:
                updates[state.id] = new_pos
        if updates:
            self._pre_edit("Move Node")
            for state in self._config.states:
                if state.id in updates:
                    state.position = updates[state.id]
            self._update_scene_rect()
            self.graph_changed.emit()

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
        self._pre_edit("Create Region")
        region = RegionModel(
            id=uuid.uuid4().hex[:8],
            label=label.strip(),
            members=list(state_ids),
            color=1,
        )
        self._config.regions.append(region)
        ri = RegionItem(region, self._node_items)
        ri.set_interactive(self._interactive_mode)
        self.addItem(ri)
        self._region_items[region.id] = ri
        self.changed.connect(ri._recompute_rect)
        ri._recompute_rect()
        self.graph_changed.emit()

    def _delete_region(self, region_id: str) -> None:
        if self._config is None:
            return
        self._pre_edit("Delete Region")
        ri = self._region_items.pop(region_id, None)
        if ri is not None:
            try:
                self.changed.disconnect(ri._recompute_rect)
            except RuntimeError:
                pass
            self.removeItem(ri)
        self._config.regions = [r for r in self._config.regions if r.id != region_id]
        self.graph_changed.emit()

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
        self._pre_edit("Rename Region")
        region.label = label.strip()
        ri = self._region_items.get(region_id)
        if ri is not None:
            ri.set_label(region.label)
        self.graph_changed.emit()

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
        self._pre_edit("Change Region Color")
        region.color = dlg.selected_color()
        ri = self._region_items.get(region_id)
        if ri is not None:
            ri.set_color(region.color)
        self.graph_changed.emit()

    def _add_to_region(self, state_ids: list[str], region_id: str) -> None:
        if self._config is None:
            return
        region = next((r for r in self._config.regions if r.id == region_id), None)
        if region is None:
            return
        to_add = [sid for sid in state_ids if sid not in region.members]
        if not to_add:
            return
        self._pre_edit("Edit Region")
        for sid in to_add:
            region.members.append(sid)
        ri = self._region_items.get(region_id)
        if ri is not None:
            for sid in to_add:
                ri._member_ids.append(sid)
            ri._recompute_rect()
        self.graph_changed.emit()

    def _remove_from_region(self, state_ids: list[str], region_id: str) -> None:
        if self._config is None:
            return
        region = next((r for r in self._config.regions if r.id == region_id), None)
        if region is None:
            return
        to_remove = [sid for sid in state_ids if sid in region.members]
        if not to_remove:
            return
        remaining = [m for m in region.members if m not in to_remove]
        self._pre_edit("Edit Region")
        if len(remaining) < 2:
            self._in_structural_edit = True
            self._delete_region(region_id)
            self._in_structural_edit = False
            self.graph_changed.emit()
            return
        for sid in to_remove:
            region.members.remove(sid)
        ri = self._region_items.get(region_id)
        if ri is not None:
            for sid in to_remove:
                if sid in ri._member_ids:
                    ri._member_ids.remove(sid)
            ri._recompute_rect()
        self.graph_changed.emit()

    def _update_scene_rect(self) -> None:
        bounds = self.itemsBoundingRect().adjusted(-500, -500, 500, 500)
        self.setSceneRect(bounds)

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
# _ColorPickerDialog — six-swatch region palette picker
# ---------------------------------------------------------------------------


class _ColorPickerDialog(QWidget):
    """Minimal six-swatch palette dialog for region color selection."""

    def __init__(self, current_key: int, parent=None) -> None:
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QVBoxLayout

        # Re-parent as QDialog so exec() works
        self._dialog = QDialog(parent)
        self._dialog.setWindowTitle("Choose Color")
        self._selected = current_key
        self._swatch_btns: dict[int, QPushButton] = {}

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

    def _select(self, key: int) -> None:
        self._selected = key
        self._highlight(key)

    def _highlight(self, key: int) -> None:
        for k, btn in self._swatch_btns.items():
            color_hex = THEME.region_color(k).name()
            border = "white" if k == key else "transparent"
            btn.setStyleSheet(
                f"QPushButton {{ background: {color_hex}; border: 2px solid {border}; border-radius: 14px; }}"
                f"QPushButton:hover {{ border-color: white; }}"
            )

    def exec(self):
        return self._dialog.exec()

    def selected_color(self) -> int:
        return self._selected
