"""Auto-layout for BotConfig states using BFS or DFS layering + barycenter heuristic."""

from __future__ import annotations

from collections import deque

from bartlebot.bot.schema import BotConfig

H_SPACING = 300
V_SPACING = 180


def auto_layout(config: BotConfig, method: str = "dfs") -> None:
    """Assign positions to all states using hierarchical layout.

    ``method`` selects the traversal used to assign layers:

    - ``"bfs"`` (default): layers are shortest-path distance from the initial
      state.  Works best for wide, shallow graphs.
    - ``"dfs"``: layers are DFS depth from the initial state.  Works best for
      deep, linear chains — nodes along the main execution path stay in the
      same column rather than being compressed into early layers.

    In both cases a barycenter heuristic reduces edge crossings within each
    layer, and unreachable (orphaned) states are collected in a final extra
    layer.

    Modifies state.position in-place. Caller must call scene.setup(config)
    to rebuild the canvas.
    """
    if not config.states:
        return

    state_ids = [s.id for s in config.states]

    # Build adjacency list (de-duplicated)
    adj: dict[str, list[str]] = {sid: [] for sid in state_ids}
    for t in config.transitions:
        if t.source in adj and t.dest in adj:
            if t.dest not in adj[t.source]:
                adj[t.source].append(t.dest)

    if method == "dfs":
        layer, col = _dfs_layers(state_ids, adj, config.initial)
    else:
        layer, col = _bfs_layers(state_ids, adj, config.initial)

    # Write positions back, centering each layer vertically around y=0
    layers: dict[int, list[str]] = {}
    for sid, lay in layer.items():
        layers.setdefault(lay, []).append(sid)
    layer_counts = {lay: len(nodes) for lay, nodes in layers.items()}

    for state in config.states:
        sid = state.id
        lay = layer[sid]
        c = col.get(sid, 0)
        n = layer_counts[lay]
        x = lay * H_SPACING + H_SPACING / 2
        y = (c - (n - 1) / 2) * V_SPACING
        state.position = {"x": x, "y": y}


# ---------------------------------------------------------------------------
# Layer / column assignment strategies
# ---------------------------------------------------------------------------

def _bfs_layers(
    state_ids: list[str],
    adj: dict[str, list[str]],
    initial: str,
) -> tuple[dict[str, int], dict[str, int]]:
    """Return (layer, col) dicts using BFS shortest-path distance."""
    layer: dict[str, int] = {}
    queue: deque[str] = deque([initial])
    layer[initial] = 0
    while queue:
        node = queue.popleft()
        for dest in adj.get(node, []):
            if dest not in layer:
                layer[dest] = layer[node] + 1
                queue.append(dest)

    _assign_orphans(state_ids, layer)

    # Group and sort layers by barycenter heuristic
    layers: dict[int, list[str]] = {}
    for sid, lay in layer.items():
        layers.setdefault(lay, []).append(sid)

    col: dict[str, int] = {}
    for lay_idx in sorted(layers.keys()):
        nodes_in_layer = layers[lay_idx]
        if lay_idx == 0:
            for i, sid in enumerate(nodes_in_layer):
                col[sid] = i
        else:
            def _bary(sid: str) -> float:
                preds = [s for s in state_ids if sid in adj.get(s, []) and s in col]
                return sum(col[p] for p in preds) / len(preds) if preds else float("inf")
            nodes_in_layer.sort(key=_bary)
            for i, sid in enumerate(nodes_in_layer):
                col[sid] = i

    return layer, col


def _dfs_layers(
    state_ids: list[str],
    adj: dict[str, list[str]],
    initial: str,
) -> tuple[dict[str, int], dict[str, int]]:
    """Return (layer, col) dicts using iterative DFS depth.

    Nodes are placed at their depth in the DFS spanning tree, so a long linear
    chain stays spread across many layers rather than all piling up at depth 1.
    Within each layer, nodes are ordered by DFS discovery time.
    """
    layer: dict[str, int] = {}
    discovery: dict[str, int] = {}
    counter = 0

    # Iterative DFS — avoids recursion limits on large graphs
    stack: list[tuple[str, int]] = [(initial, 0)]
    while stack:
        node, depth = stack.pop()
        if node in layer:
            continue
        layer[node] = depth
        discovery[node] = counter
        counter += 1
        # Reverse so the first child is processed first
        for dest in reversed(adj.get(node, [])):
            if dest not in layer:
                stack.append((dest, depth + 1))

    _assign_orphans(state_ids, layer, discovery_map=discovery, counter_start=counter)

    # Group layers; within each layer sort by DFS discovery order
    layers: dict[int, list[str]] = {}
    for sid, lay in layer.items():
        layers.setdefault(lay, []).append(sid)

    col: dict[str, int] = {}
    for lay_idx in sorted(layers.keys()):
        nodes_in_layer = sorted(layers[lay_idx], key=lambda s: discovery.get(s, 0))
        for i, sid in enumerate(nodes_in_layer):
            col[sid] = i

    return layer, col


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
