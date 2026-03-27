"""Interactive QGraphicsScene for the bot canvas editor.

EditScene        — manages interactive nodes, edge dragging, deletion, and positions.
_EditView        — GraphView subclass with interactive=True.
_ColorPickerDialog — six-swatch region palette picker used by EditScene.

Module-level helpers (_node_returns, _validate_node, _dist, _unique_id, etc.) are
also exported here so BotCanvas can use them without circular imports.
"""

import math

from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QKeyEvent, QMouseEvent, QPen
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QMessageBox,
    QPushButton,
    QWidget,
)

from bartlebot.bot.schema import BotConfig, BotOptionModel, RegionModel, StateModel, TransitionModel
from bartlebot.gui.canvas.edge_item import EdgeItem
from bartlebot.gui.canvas.graph_view import GraphView
from bartlebot.gui.canvas.node_item import NODE_H, NODE_W, NodeItem
from bartlebot.gui.canvas.region_item import RegionItem
from bartlebot.gui.theme import THEME
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS, NODE_REGISTRY, NODE_SPECS

# ---------------------------------------------------------------------------
# Constants used only by the editor
# ---------------------------------------------------------------------------

_PORT_HIT_RADIUS = 16
_AUTO_SPACING = 260
_PASTE_OFFSET = 40  # px shift per successive paste of the same clipboard

_SCROLL_MARGIN = 40  # px from viewport edge at which auto-scroll activates during drag
_SCROLL_SPEED = 10  # max px to scroll per mouseMoveEvent while in the margin zone


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
        self._auto_scrolling: bool = False
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
            views = self.views()
            if views and not self._auto_scrolling:
                self._auto_scrolling = True
                _auto_scroll_view(views[0], cur)
                self._auto_scrolling = False
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
        for item in list(self.selectedItems()):
            if isinstance(item, NodeItem):
                self._delete_node(item.state_id)
            elif isinstance(item, EdgeItem):
                self._delete_edge_item(item)

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

        # Recalculate offset — check if a reverse edge already exists on the NEW pair
        reverse_exists = any(
            es == new_dst and ed == new_src for (es, _ew, ed, _ei2) in self._edge_items if _ei2 is not edge_item
        )
        if reverse_exists:
            for _i2, (es, _ew, ed, ei2) in enumerate(self._edge_items):
                if ei2 is not edge_item and es == new_dst and ed == new_src:
                    ei2._offset = -22.0
                    ei2._rebuild()
            new_offset = 22.0
        else:
            new_offset = 0.0

        # Update the EdgeItem in-place
        edge_item._src = new_src_node
        edge_item._dst = new_dst_node
        edge_item._is_self_loop = new_src_node is new_dst_node
        edge_item._offset = new_offset
        edge_item._rebuild()

        # Register with new nodes
        new_src_node.add_edge(edge_item)
        if new_dst_node is not new_src_node:
            new_dst_node.add_edge(edge_item)

        # Update the _edge_items record
        self._edge_items[record_idx] = (new_src, old_when_str, new_dst, edge_item)

        # Clean up offsets on the OLD pair if the reverse was offset for it
        old_reverse = any(
            es == old_dst and ed == old_src for (es, _ew, ed, ei2) in self._edge_items if ei2 is not edge_item
        )
        if not old_reverse:
            for _i2, (es, _ew, ed, ei2) in enumerate(self._edge_items):
                if ei2 is not edge_item and es == old_dst and ed == old_src:
                    ei2._offset = 0.0
                    ei2._rebuild()

        self._update_scene_rect()
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
        self._delete_edge_item(edge_item, confirm=False)
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
