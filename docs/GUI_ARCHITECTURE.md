# BartleBot GUI Architecture

## Top-Level Layout

```
┌─────────────────────────────────────────────────────────────┐
│  BartleBot                                          [─][□][×]│
├─────────────────────────────────────────┬───────────────────┤
│  [Editor] [Running] [Logging]           │  Client Utilities │
│                                         │                   │
│  (active tab content)                   │  Client 1         │
│                                         │  Zone: [........] │
│                                         │  [Copy Zone]      │
│                                         │  XYZ: [.......  ] │
│                                         │  [Copy XYZ]       │
│                                         │  [Freecam ▶/■]    │
│                                         │  [TP to Freecam]  │
│                                         │                   │
│                                         │  Client 2         │
│                                         │  Zone: [........] │
│                                         │  ...              │
└─────────────────────────────────────────┴───────────────────┘
```

The main window is split into:
- **Left/main area:** `QTabWidget` with three tabs
- **Right sidebar:** Fixed-width `QWidget` with per-client utility panels (persistent across tabs)

## Client Utilities Sidebar

One `ClientWidget` section per hooked `Client`, stacked vertically in a `QScrollArea`.

Each `ClientWidget` contains:
- **Zone row:** `QLabel` (zone name, live-updating) + `QPushButton("Copy Zone")`
- **XYZ row:** `QLabel` (xyz, live-updating) + `QPushButton("Copy XYZ")`
- **Freecam row:**
  - `QPushButton("Enter Freecam")` / `QPushButton("Exit Freecam")` — toggles freecam mode. Does **not** teleport the client when exiting.
  - `QPushButton("TP to Freecam")` — enabled only while freecam is active. Teleports the client to the current freecam position, then exits freecam mode and re-labels the button.
- Zone and XYZ update via a repeating `QTimer` (1s interval) that schedules async reads on the
  asyncio event loop via qasync and emits a Qt signal back to update the labels.

## Editor Tab

Visual FSM editor for creating and editing bots.

### Canvas
- `QGraphicsScene` + `QGraphicsView`
- Zoom via scroll wheel (`QGraphicsView.setTransform`)
- Pan via middle-mouse drag or space+drag

### Node Items (`NodeGraphicsItem(QGraphicsItem)`)
- Rounded rectangle with title bar showing `label` (or `node_type`)
- Two connection ports: one output port (bottom/right), one input port (top/left)
- Click: select node, open parameter panel in side panel
- Drag: reposition node (updates `NodeData.position`)
- Right-click context menu: Delete Node, Duplicate Node

### Edge Items (`EdgeGraphicsItem(QGraphicsPathItem)`)
- Cubic bezier from source output port to dest input port
- Drawn with `QPainterPath.cubicTo`
- Click: select edge, show label + conditions in parameter panel
- Right-click: Delete Edge
- Creation: drag from an output port; drop on an input port to create a transition

### Parameter Panel
A `QDockWidget` (or right sub-panel) that shows editable fields for the selected node or edge.

**For a node:**
- `node_type` dropdown (standard library types + registered custom types)
- `label` text field
- Dynamically generated fields for each param defined by the `node_type` schema:
  - `str` → `QLineEdit`
  - `float` / `int` → `QDoubleSpinBox` / `QSpinBox`
  - `XYZ` → three `QDoubleSpinBox` fields (x, y, z) + "Paste from clipboard" button
  - `bool` → `QCheckBox`
  - `path` → `QLineEdit` + file browse button
  - `combat_config` (multi-line DSL) → `QPlainTextEdit`
- For `handle_combat` nodes: checkbox "Override bot combat config" that enables the `combat_config` field. When unchecked, the field is greyed out and the bot-level default is used.

**For an edge:**
- `label` text field
- Conditions list: add/remove condition rows, each with type dropdown + param fields

**Bot Settings panel** (accessible via toolbar or menu, not tied to a selected item):
- `name`, `description`, `version` fields
- `clients` selector
- `default_combat_config` (`QPlainTextEdit`)

### Toolbar
- New Bot, Open Bot, Save Bot, Save As
- Run Bot (switches to Running tab and starts execution)

### Node Data Model

```python
@dataclass
class NodeData:
    id: str
    node_type: str
    label: str
    position: tuple[float, float]
    params: dict

@dataclass
class TransitionData:
    source: str
    dest: str
    label: str
    conditions: list[dict]
```

Each `NodeGraphicsItem` holds a `NodeData`. The scene maintains a `dict[str, NodeGraphicsItem]`
and a `list[EdgeGraphicsItem]`. Serialization walks these to produce the bot JSON.

## Running Tab

Read-only view of the active bot FSM as it executes.

- Same `QGraphicsScene`/`QGraphicsView` layout as Editor, but non-interactive
- Active state node is highlighted (e.g., border color or fill change) via signal from `BotMachine`
- Recently completed transitions flash briefly
- **Runtime timer:** `QLabel` in the tab header or top bar, updated every second
- **Log stream:** `QPlainTextEdit` (append-only) showing timestamped transition events:
  ```
  [00:01:23] Entered: enter_dungeon
  [00:01:31] Result: SUCCESS → fight_cyclops
  [00:02:04] Entered: fight_cyclops
  ```
- **Stop button:** Signals `BotMachine` to cancel after current node completes

## Logging Tab

Tracks drops received during bot execution.

- `QTableWidget` with columns: `Item Name`, `Quantity`, `Client`
- Rows added by bot nodes that detect item drops (e.g., `stop_if_dropped` or a passive drop monitor)
- Per-client split: if multiple clients are running, each drop is attributed to the client that received it
- **Export CSV button:** Writes the current table to a `.csv` file via a file dialog

## Async/Qt Bridge

All WizWalker memory reads are async coroutines. PyQt6's event loop runs via `qasync`.

Pattern for scheduling async work from Qt widgets:

```python
import asyncio

# Schedule an async coroutine from a Qt signal/slot or timer:
asyncio.ensure_future(some_async_func())

# Emit a Qt signal back to the main thread from an async context:
# Define signals on the widget class and emit from the async callback.
# Signals are thread-safe in Qt and can be emitted from any context.
self.zone_updated.emit(zone_name)
```

The client utility sidebar uses a `QTimer` (1s) that calls `asyncio.ensure_future(update_clients())`
on each tick. The async function reads zone/XYZ from all clients in parallel and emits signals to
update the labels.

For `BotMachine` → GUI communication (state changes during bot run), `BotMachine` accepts optional
signal callbacks injected at construction time, keeping the FSM layer decoupled from Qt.
