"""Auto-layout for BotConfig states using longest-path layering."""

from __future__ import annotations

from bartlebot.bot.schema import BotConfig
from bartlebot.gui.canvas.node_item import GRID_H, GRID_W

H_SPACING = GRID_W  # one grid column width — keeps auto-layout positions on-grid
V_SPACING = 2 * GRID_H  # two grid rows — enough vertical room for edges between stacked nodes


def auto_layout(config: BotConfig) -> None:
    """Assign positions to all states using hierarchical longest-path layout.

    Each node is placed at the layer corresponding to the longest path from
    the initial state to that node. Convergence points (nodes reachable via
    parallel branches of different lengths) appear at the far end of the
    longest incoming branch, keeping parallel paths visually distinct.

    Back-edges (cycle edges such as failure loops back to an earlier state)
    are detected and excluded from layer assignment; they still render as
    backward arrows in the canvas. Vertical positions use barycenter
    propagation: each node's y is the average of its predecessors' y-values,
    with multi-node layers spread evenly around that average. This keeps nodes
    vertically aligned with the branch they belong to rather than centering
    every layer independently at y=0.

    A subgraph instance is laid out as the one node it is drawn as: its call node is
    placed, and the anchor and every member move with it, since members sit at
    ``anchor + definition position``.

    Modifies state.position in-place. Caller must call scene.setup(config)
    to rebuild the canvas.
    """
    if not config.states:
        return

    from bartlebot.subgraphs import nested_regions, outermost_linked_regions  # noqa: PLC0415

    instances = outermost_linked_regions(config.regions)
    call_of = {m: r.id for r in instances for m in r.members}

    def display(sid: str) -> str:
        return call_of.get(sid, sid)

    state_ids = list(dict.fromkeys(display(s.id) for s in config.states))

    # Build adjacency list (de-duplicated)
    adj: dict[str, list[str]] = {sid: [] for sid in state_ids}
    for t in config.transitions:
        src, dst = display(t.source), display(t.dest)
        if src in adj and dst in adj and src != dst:
            if dst not in adj[src]:
                adj[src].append(dst)

    layer, discovery = _longest_path_layers(state_ids, adj, display(config.initial))

    # Build predecessor map from adj for barycenter computation.
    # Back-edges are excluded naturally below: their sources have higher layer
    # numbers and won't be in y_pos yet when the destination is processed.
    pred: dict[str, list[str]] = {sid: [] for sid in state_ids}
    for src, dests in adj.items():
        for dest in dests:
            pred[dest].append(src)

    # Group nodes by layer and compute y-positions via barycenter propagation.
    # Each node's y is the average of its predecessors' y-values (ignoring
    # predecessors not yet assigned, i.e. back-edge sources). Multi-node layers
    # are spread evenly at V_SPACING intervals around that average.
    layers: dict[int, list[str]] = {}
    for sid, lay in layer.items():
        layers.setdefault(lay, []).append(sid)

    y_pos: dict[str, float] = {}
    for lay_idx in sorted(layers.keys()):
        nodes = layers[lay_idx]
        bary: dict[str, float] = {}
        for sid in nodes:
            preds_y = [y_pos[p] for p in pred[sid] if p in y_pos]
            bary[sid] = sum(preds_y) / len(preds_y) if preds_y else 0.0
        nodes_sorted = sorted(nodes, key=lambda s: (bary[s], discovery.get(s, 0)))
        n = len(nodes_sorted)
        mean_bary = sum(bary[s] for s in nodes_sorted) / n
        for i, sid in enumerate(nodes_sorted):
            y_pos[sid] = mean_bary + (i - (n - 1) / 2) * V_SPACING

    for state in config.states:
        sid = state.id
        if sid in call_of:
            continue
        state.position = {"x": layer[sid] * H_SPACING, "y": y_pos.get(sid, 0.0)}
    for region in instances:
        assert region.source is not None
        old = region.source.anchor or {"x": 0.0, "y": 0.0}
        dx = layer[region.id] * H_SPACING - float(old["x"])
        dy = y_pos.get(region.id, 0.0) - float(old["y"])
        for r in [region, *nested_regions(config, region)]:
            a = r.source.anchor or {"x": 0.0, "y": 0.0}
            r.source.anchor = {"x": float(a["x"]) + dx, "y": float(a["y"]) + dy}
        members = set(region.members)
        for state in config.states:
            if state.id in members and state.position is not None:
                state.position = {"x": float(state.position["x"]) + dx, "y": float(state.position["y"]) + dy}


def _longest_path_layers(
    state_ids: list[str],
    adj: dict[str, list[str]],
    initial: str,
) -> tuple[dict[str, int], dict[str, int]]:
    """Return (layer, discovery) dicts using longest-path distance from initial.

    Detects back-edges via DFS and excludes them from layer assignment so
    cycles don't prevent termination. discovery maps each node to its DFS
    visit order, used as a tiebreaker in barycenter sorting.
    """
    back_edges: set[tuple[str, str]] = set()
    visited: set[str] = set()
    on_stack: set[str] = set()
    discovery: dict[str, int] = {}
    topo_order: list[str] = []  # post-order; reversed = topological order
    counter = 0

    # Iterative DFS: detect back-edges and build topological order
    stack: list[tuple[str, bool]] = [(initial, False)]
    while stack:
        node, is_return = stack.pop()
        if is_return:
            on_stack.discard(node)
            topo_order.append(node)
            continue
        if node in visited:
            continue
        visited.add(node)
        on_stack.add(node)
        discovery[node] = counter
        counter += 1
        stack.append((node, True))
        for dest in reversed(adj.get(node, [])):
            if dest in on_stack:
                back_edges.add((node, dest))
            elif dest not in visited:
                stack.append((dest, False))

    # Build reverse adjacency (predecessors), excluding back-edges
    pred: dict[str, list[str]] = {sid: [] for sid in state_ids}
    for src, dests in adj.items():
        for dest in dests:
            if (src, dest) not in back_edges:
                pred[dest].append(src)

    # Assign layers in topological order: each node gets max(predecessor layers) + 1
    layer: dict[str, int] = {}
    for node in reversed(topo_order):
        incoming = [layer[p] for p in pred[node] if p in layer]
        layer[node] = max(incoming) + 1 if incoming else 0

    _assign_orphans(state_ids, layer, discovery_map=discovery, counter_start=counter)

    return layer, discovery


def _assign_orphans(
    state_ids: list[str],
    layer: dict[str, int],
    *,
    discovery_map: dict[str, int] | None = None,
    counter_start: int = 0,
) -> None:
    """Place unreachable states into a final orphan layer."""
    max_layer = max(layer.values(), default=0)
    orphan_layer = max_layer + 1
    counter = counter_start
    for sid in state_ids:
        if sid not in layer:
            layer[sid] = orphan_layer
            if discovery_map is not None:
                discovery_map[sid] = counter
                counter += 1
