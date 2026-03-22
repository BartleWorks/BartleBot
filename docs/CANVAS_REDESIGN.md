# Bot Canvas Scale — Combined Implementation Plan

## Context

The current `BotCanvas` + `EditScene` stack works well for small bots (5–15 states) but degrades
as bots grow. This plan adds four complementary capabilities to address legibility, navigation,
insertion friction, and structural complexity:

1. **Minimap + LOD** — see the whole graph and read nodes at any zoom level
2. **Auto-layout + Edge-Insert** — eliminate manual positioning
3. **Keyboard navigation + Quick-Search** — jump anywhere fast
4. **Node Groups** — collapse logical sections into single units (pre-comp style)

All features work in Edit mode. Minimap + search work in Run mode too.

---

## Settings

Two new app-level settings (stored in existing settings system, not per-bot):

| Key | Type | Default | Description |
|---|---|---|---|
| `canvas.minimap_visible` | bool | true | Whether the minimap overlay is shown |
| `canvas.auto_layout_on_load` | bool | true | Run auto-layout when a bot has no saved positions |

No per-bot settings. Groups/positions are stored in the bot JSON.

---

## Feature 1 — Level-of-Detail (LOD) Rendering

**File:** `bartlebot/gui/canvas/graph_items.py` — `NodeItem.paint()`

Two rendering tiers based on `option.levelOfDetailFromTransform(painter)`:

| Scale | Content |
|---|---|
| ≥ 0.45 | Full: header, sub-label, named ports, badges, divider line |
| < 0.45 | Compact: main label only, centered, larger font (~18 px), no ports, no badges |

Implementation: wrap the existing paint logic in `if lod >= 0.45: ... else: ...` block. The compact
path is a simple filled roundrect + one centered text draw. No structural changes to `NodeItem`.

---

## Feature 2 — Minimap Overlay

**Files:**
- `bartlebot/gui/canvas/graph_items.py` — new `_MinimapView` class
- `bartlebot/gui/canvas/bot_canvas.py` — instantiate, position, and toggle minimap

### _MinimapView

A read-only `QGraphicsView` subclass that renders the *same* `EditScene`:
- Fixed size (160 × 110 px), no scrollbars, no interaction (drag mode = NoDrag)
- Semi-transparent rounded border via stylesheet
- Overrides `mousePressEvent` + `mouseMoveEvent`: maps click position to scene coordinates and
  calls `self._main_view.centerOn(scene_pos)`
- Draws a translucent viewport rectangle showing where the main view is currently looking:
  computed from `main_view.mapToScene(main_view.viewport().rect())`; updated whenever the main
  view scrolls or scales (connect to `main_view.horizontalScrollBar().valueChanged` and
  `verticalScrollBar().valueChanged` and scale changes via eventFilter)

### Positioning in BotCanvas

The minimap is a child widget of `_EditView` (same overlay technique as `_validation_banner`):
```python
self._minimap = _MinimapView(self._scene, main_view=self._view, parent=self._view)
self._view.installEventFilter(self)  # already done; reposition on resize
```
Pinned to bottom-right: `x = view.width() - minimap.width() - 8; y = view.height() - minimap.height() - 8`

Minimap toggle button added to the left section of the toolbar (next to the fit button).
State persisted to `canvas.minimap_visible` setting.

---

## Feature 3 — Auto-Layout Engine + Edge-Insert

### Auto-Layout

**New file:** `bartlebot/gui/canvas/layout.py`

```
auto_layout(config: BotConfig) -> None
```

Algorithm (~90 lines):
1. **Layer assignment (BFS):** Start from `config.initial`, assign `layer[node] = BFS depth`.
   Back-edges (transitions to a node whose layer is already ≤ current) are recorded but not
   used to deepen layers. Unreachable nodes (orphans) are placed in a final "orphan" layer.
2. **Column assignment (barycenter):** Within each layer, sort nodes by the average column
   of their predecessors (barycenter heuristic). One pass forward is sufficient for typical FSMs.
3. **Position mapping:** `x = layer * H_SPACING`, `y = column * V_SPACING`.
   Default constants: `H_SPACING = 300`, `V_SPACING = 180`.
4. **Write back:** Update `state.position` for all states in config. The caller calls
   `scene.setup(config)` to rebuild.

Trigger points:
- "Layout" toolbar button in Edit mode (new icon in left section of toolbar)
- Auto-runs in `BotCanvas.setup()` when all `state.position` are `None` and
  `canvas.auto_layout_on_load` setting is true

### Edge-Insert

**File:** `bartlebot/gui/canvas/graph_items.py` — `EdgeItem`

Add `contextMenuEvent` to `EdgeItem`:
```
Right-click edge → "Insert State Here"
```
Implementation (calls back into the owning scene via a signal or direct reference):
1. Compute midpoint of the edge's current path
2. Call `scene.add_state(midpoint_pos)` — creates a new state at that position
3. Record the original source `s`, destination `d`, and `when` value `w`
4. Call `scene._create_transition(new_node, dst_node, w)` to wire `new → dst`
5. Call `scene._delete_edge_item(this_edge)` and create `src → new` transition
6. All existing nodes to the right of the midpoint x-coordinate are shifted right by `H_SPACING`
   (update their `state.position` and call `node.setPos(...)`)

The edge-insert gives `EdgeItem` a reference back to the `EditScene`. Currently `EdgeItem` has no
back-reference to the scene; add one at construction time (or pass it via signal to `EditScene`
which handles the operation).

---

## Feature 4 — Keyboard Navigation + Quick-Search

### Tab Navigation

**File:** `bartlebot/gui/canvas/bot_canvas.py` or `graph_items.py` — `EditScene.keyPressEvent`

- **Tab**: select the next node in BFS order from `config.initial` (same order as auto-layout
  layer assignment). After the last node, wraps to the first.
- **Shift+Tab**: reverse direction.
- Each step: `scene.clearSelection()`, `node.setSelected(True)`,
  `self._view.centerOn(node)` (or `ensureVisible`).
- Works in both modes (Run mode: Tab highlights without Edit-mode interaction).

BFS order list is computed lazily from `scene._config` whenever the config changes (cache it in
`EditScene` as `_tab_order: list[str]`).

### Quick-Search

**File:** `bartlebot/gui/canvas/bot_canvas.py`

A floating overlay widget (child of `self._view`, same as minimap and validation banner):

```
[🔍 __________________ ] × (Esc to close)
     1 of 3 matches
```

- Shortcut: **Ctrl+F** (registered in `BotCanvas._build_ui()`)
- Widget: `QLineEdit` with a close button, positioned top-center of `self._view`
- On text change: search `config.states` for `state.label`, `state.id`, `state.node_type`
  containing the query (case-insensitive). Highlight all matches (call `node.setSelected(True)`
  for matches, `False` for non-matches). Center on first match.
- **Enter / Down arrow**: cycle to next match (`view.centerOn(match_node)`)
- **Up arrow**: cycle to previous match
- **Escape**: close search bar, clear selection

Highlight style for search matches: use a distinct border color (not the normal selected color)
so multiple matches can be visible simultaneously while the "current" match is centered.

---

## Feature 5 — Node Groups

### Schema

**File:** `bartlebot/bot/schema.py`

```python
class GroupModel(BaseModel):
    id: str
    label: str
    members: list[str]          # state IDs belonging to this group
    collapsed: bool = True
    position: dict | None = None  # {x, y} of the GroupItem when collapsed

class BotConfig(BaseModel):
    ...
    groups: list[GroupModel] = []  # new optional field; default empty
```

Existing bot JSON files load without change (Pydantic default = `[]`).

---

### GroupItem (collapsed representation)

**New file:** `bartlebot/gui/canvas/group_item.py`

A `QGraphicsItem` that visually matches `NodeItem` conventions and exposes the same port interface:
- **Appearance:** wider rect (`NODE_W * 1.6`), dark fill, group icon + label in header,
  "⧉ N states" badge, left input port, right output ports (one per unique external destination)
- **Ports:**
  - `port_in() -> QPointF`: left-center
  - `port_out_for(dest_id: str) -> QPointF`: right side, evenly spaced by dest index
  - `port_out() -> QPointF`: right-center fallback (for unconditional transitions)
- **Height:** `max(NODE_H, (n_outputs + 1) * PORT_SPACING)`
- **Double-click:** emits `expand_requested` signal (connected in `EditScene.setup()`)
- **Interactive:** supports move (updates `group.position`) and selection

`GroupItem` is duck-type compatible with `NodeItem` for use as an `EdgeItem` endpoint.

---

### GroupBoundaryItem (expanded representation)

A `QGraphicsRectItem` subclass drawn *behind* member nodes (z-value < 0):
- **Appearance:** dashed rounded border, very faint fill, group label in top-left corner
- **Geometry:** bounding rect of all member nodes + 20 px padding on all sides; auto-updates
  when members move (same `itemChange` pattern as `NodeItem` notifying edges)
- **Non-interactive to clicks:** `setFlag(ItemIsSelectable, False)`, `setFlag(ItemIsMovable, False)`
- **Double-click:** triggers collapse (emits `collapse_requested` signal)
- **Right-click context menu:** "Rename Group", "Ungroup", "Collapse"

---

### EditScene changes

**File:** `bartlebot/gui/canvas/graph_items.py` — `EditScene`

New state:
```python
self._group_items: dict[str, GroupItem] = {}         # group_id → GroupItem
self._boundary_items: dict[str, GroupBoundaryItem] = {} # group_id → boundary rect
```

#### `setup()` additions

After building normal nodes and edges, iterate `config.groups`:

**If `group.collapsed`:**
1. Hide member NodeItems: `node_items[member_id].setVisible(False)`
2. Hide edges where both src and dst are in the group (internal edges)
3. For external edges (src in group, dst outside; or dst in group, src outside):
   - These are already drawn correctly between real NodeItems; we need to *reroute* their visual
     endpoint to the GroupItem.
   - Simplest approach: call `edge._rebuild()` after swapping `edge._src` or `edge._dst` to the
     GroupItem. This requires GroupItem to implement the same interface as NodeItem for `port_in`,
     `port_out_for`, and position tracking.
4. Compute output ports: for each unique destination outside the group among all boundary-crossing
   outgoing transitions, add one port to the GroupItem (keyed by destination state ID)
5. Create `GroupItem`, connect `expand_requested` signal to `_on_group_expand`
6. Add `GroupItem` to scene

**If `not group.collapsed` (expanded):**
1. All member NodeItems are visible (default)
2. All edges drawn normally
3. Create `GroupBoundaryItem` using union of member bounding rects + padding
4. Connect `collapse_requested` signal to `_on_group_collapse`

#### Expand/Collapse

```python
def _on_group_expand(self, group_id: str) -> None:
    """User double-clicked a collapsed GroupItem."""
    if self._config is None:
        return
    self._flush_positions()
    group = next((g for g in self._config.groups if g.id == group_id), None)
    if group:
        group.collapsed = False
        self.setup(self._config)
        self.config_changed.emit()

def _on_group_collapse(self, group_id: str) -> None:
    """User double-clicked an expanded GroupBoundaryItem."""
    if self._config is None:
        return
    self._flush_positions()
    group = next((g for g in self._config.groups if g.id == group_id), None)
    if group:
        # Compute and save the group's collapsed position (centroid of members)
        members = [self._node_items[m] for m in group.members if m in self._node_items]
        if members:
            cx = sum(n.scenePos().x() + NODE_W / 2 for n in members) / len(members)
            cy = sum(n.scenePos().y() + n._height / 2 for n in members) / len(members)
            group.position = {"x": cx, "y": cy}
        group.collapsed = True
        self.setup(self._config)
        self.config_changed.emit()
```

#### Create Group (context menu on multi-selection)

`contextMenuEvent` addition when 2+ `NodeItem`s are selected:
```
"Group Selected States" → _create_group(selected_ids)
```

```python
def _create_group(self, state_ids: list[str]) -> None:
    label, ok = QInputDialog.getText(None, "Group Name", "Group name:", text="Group")
    if not ok or not label.strip():
        return
    gid = _unique_id([g.id for g in self._config.groups], prefix="group_")
    # Centroid position for collapsed GroupItem
    members = [self._node_items[s] for s in state_ids if s in self._node_items]
    cx = sum(n.scenePos().x() + NODE_W / 2 for n in members) / len(members)
    cy = sum(n.scenePos().y() + n._height / 2 for n in members) / len(members)
    group = GroupModel(id=gid, label=label.strip(), members=state_ids,
                       collapsed=True, position={"x": cx, "y": cy})
    self._config.groups.append(group)
    self._flush_positions()
    self.setup(self._config)
    self.config_changed.emit()
```

#### Ungroup

Right-click collapsed `GroupItem` or expanded `GroupBoundaryItem` → "Ungroup":
```python
def _ungroup(self, group_id: str) -> None:
    self._config.groups = [g for g in self._config.groups if g.id != group_id]
    self.setup(self._config)
    self.config_changed.emit()
```
Members are restored at their stored `state.position` values (unchanged).

#### Remove Node from Group

Right-click a member `NodeItem` while its group is expanded → "Remove from Group":
```python
def _remove_from_group(self, state_id: str) -> None:
    for group in self._config.groups:
        if state_id in group.members:
            group.members.remove(state_id)
            if len(group.members) <= 1:
                # Auto-ungroup if only 0 or 1 members remain
                self._config.groups.remove(group)
            break
    self.setup(self._config)
    self.config_changed.emit()
```

The ejected node appears at its `state.position` on the main canvas, keeping all its existing
transitions (no transitions are modified).

#### Add Node to Group

Right-click any non-member `NodeItem` while a group is expanded → "Add to Group [label]"
(one menu item per expanded group if multiple groups are expanded):
```python
def _add_to_group(self, state_id: str, group_id: str) -> None:
    group = next(g for g in self._config.groups if g.id == group_id)
    group.members.append(state_id)
    self.setup(self._config)
    self.config_changed.emit()
```

---

### Cross-boundary connections while expanded

Since groups expand *in-place* (all nodes remain in the same `EditScene`), the user draws edges
between any nodes exactly as normal — no special handling needed. When the group is later
collapsed, `setup()` recomputes which transitions cross the boundary and derives the GroupItem's
output ports automatically. No "group output port" placeholder nodes exist; ports are always
derived from actual transitions in `config.transitions`.

---

### Tab Navigation with Groups

When a group is collapsed, Tab skips all member states and instead stops at the `GroupItem`.
`_tab_order` list in `EditScene`:
- Non-grouped nodes: appear in BFS layer order
- Collapsed groups: appear as a unit at the layer of their shallowest member
- Expanded group members: appear in BFS order within the group sequence

`centerOn` targets the `GroupItem` when collapsed, or the individual `NodeItem` when expanded.

---

## Files Summary

| File | Change |
|---|---|
| `bartlebot/bot/schema.py` | Add `GroupModel`, `groups` field to `BotConfig` |
| `bartlebot/gui/canvas/graph_items.py` | LOD tiers in `NodeItem.paint()`; `_MinimapView`; Tab/edge-insert in `EditScene`; group expand/collapse/create/ungroup in `EditScene` |
| `bartlebot/gui/canvas/bot_canvas.py` | Minimap toggle; Layout button; Ctrl+F search overlay; settings load/save |
| `bartlebot/gui/canvas/group_item.py` | New: `GroupItem`, `GroupBoundaryItem` |
| `bartlebot/gui/canvas/layout.py` | New: `auto_layout()` |

---

## Verification

1. **LOD:** Open any bot, zoom to ≤0.45 scale — labels should show, sub-labels/ports hidden
2. **Minimap:** Toggle minimap button; drag inside minimap to pan main view; viewport rect tracks
3. **Auto-layout:** Open bot with no positions saved; verify hierarchical arrangement. Click Layout
   button on an existing bot; verify nodes rearrange cleanly
4. **Edge-insert:** Right-click any edge → Insert State Here; verify new state wired correctly
5. **Tab nav:** Press Tab repeatedly; verify each node is selected and centered in sequence
6. **Search:** Ctrl+F, type partial label; verify highlights and centering; Esc clears
7. **Groups — full UX flow:**
   - Select 3 nodes → Group → name it → verify collapsed GroupItem appears
   - Verify external edges connect to GroupItem with correct output ports (by destination)
   - Double-click GroupItem → verify expansion (dashed bounding rect, members visible)
   - Draw a new edge between an internal node and an external node while expanded
   - Collapse again → verify new boundary transition appears as an output port
   - Right-click member while expanded → Remove from Group → verify node ejected
   - Right-click GroupItem → Ungroup → verify all nodes restored
8. **Persistence:** Save bot, reload → verify group state preserved in JSON
9. **Run `pytest`** — existing unit tests should all pass (no runtime changes)
