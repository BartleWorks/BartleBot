"""Auto-layout for BotConfig states using BFS layering + barycenter heuristic."""

from __future__ import annotations

from collections import deque

from bartlebot.bot.schema import BotConfig

H_SPACING = 300
V_SPACING = 180


def auto_layout(config: BotConfig) -> None:
    """Assign positions to all states using hierarchical BFS layout.

    Uses a forward BFS from config.initial to assign layers, then a
    barycenter heuristic to order nodes within each layer. Orphaned
    (unreachable) states are collected in a final extra layer.

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

    # BFS layer assignment from initial state
    layer: dict[str, int] = {}
    queue: deque[str] = deque([config.initial])
    layer[config.initial] = 0
    while queue:
        node = queue.popleft()
        for dest in adj.get(node, []):
            if dest not in layer:
                layer[dest] = layer[node] + 1
                queue.append(dest)

    # Unreachable nodes go to a final "orphan" layer
    max_layer = max(layer.values(), default=0)
    orphan_layer = max_layer + 1
    for sid in state_ids:
        if sid not in layer:
            layer[sid] = orphan_layer

    # Group states by layer index
    layers: dict[int, list[str]] = {}
    for sid, lay in layer.items():
        layers.setdefault(lay, []).append(sid)

    # Barycenter column assignment: sort nodes in each layer by average
    # column of their predecessors to reduce edge crossings.
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

    # Write positions back, centering each layer vertically around y=0
    layer_counts = {lay: len(nodes) for lay, nodes in layers.items()}
    for state in config.states:
        sid = state.id
        lay = layer[sid]
        c = col.get(sid, 0)
        n = layer_counts[lay]
        x = lay * H_SPACING + H_SPACING / 2
        y = (c - (n - 1) / 2) * V_SPACING
        state.position = {"x": x, "y": y}
