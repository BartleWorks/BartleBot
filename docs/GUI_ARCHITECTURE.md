# BartleBot GUI Architecture

## Top-Level Layout

### Edit View
```
┌──────────────┬──────────────────────────────────┬───────────────────┐
│  BartleBot                                                    [─][□][×]│
├──────────────┼──────────────────────────────────┼───────────────────┤
│ Bot Info     │  [Edit] [Run]                    │ Client Utilities  │
│              │                                  │                   │
│ Name         │  (FSM canvas — interactive)      │ Client 1          │
│ Description  │                                  │ Zone: [........]  │
│ Start loc.   │                                  │ [Copy Zone]       │
│ Drops        ├──────────────────────────────────┤ XYZ: [.........]  │
│ Authors      │ Node / Edge Parameters           │ [Copy XYZ]        │
│              │ (selected item fields)            │ [Enter Freecam]   │
│ ─────────── │                                  │ [TP to Freecam]   │
│ Options      │                                  │                   │
│              │                                  │ Client 2          │
│ [✓] Option A │                                  │ Zone: [........]  │
│ [ ] Option B │                                  │ ...               │
└──────────────┴──────────────────────────────────┴───────────────────┘
```

### Run View
```
┌──────────────┬──────────────────────────────────┬───────────────────┐
│  BartleBot                                                    [─][□][×]│
├──────────────┼──────────────────────────────────┼───────────────────┤
│ Bot Info     │  [Edit] [Run]                    │ Drops             │
│              │                                  │                   │
│ Name         │ State: fight_cyclops  00:01:23 [■]│ Item | Qty | Who  │
│ Description  │                                  │ ──────────────── │
│ Start loc.   │  (FSM graph — read-only)         │ Pixie Hat  | 1 |p1│
│ Drops        │                                  │ ...               │
│ Authors      ├──────────────────────────────────┤                   │
│              │ [00:01:23] → enter_dungeon        │                   │
│ ─────────── │ [00:01:31] SUCCESS → fight_cyclops│ [Export CSV]      │
│ Options      │ [00:02:04] → fight_cyclops        │                   │
│              │                                  │                   │
│ [✓] Option A │                                  │                   │
│ [ ] Option B │                                  │                   │
└──────────────┴──────────────────────────────────┴───────────────────┘
```

The main window has three zones:
- **Left sidebar:** Fixed-width panel, permanent in both tabs — bot metadata and bot-wide options
- **Center:** `QTabWidget` with two tabs (Edit, Run). Each tab owns its bottom sub-panel.
- **Right sidebar:** Swaps content per active tab — Client Utilities in Edit, Drop table in Run

---

## Left Sidebar — Bot Info & Options

Always visible. Populated from the loaded `BotConfig`. Two sections separated by a divider.

### Bot Info section

Read-only display of bot metadata. Fields map to `BotConfig` top-level fields:

| Field | Source |
|---|---|
| Name | `config.name` |
| Description | `config.description` |
| Start location | `config.start_location` (new field, free-text) |
| Expected drops | `config.drops` (new field, list of strings) |
| Authors / Author | `config.authors` (new field, free-text) |
| Version | `config.version` |

Editing is done via the Bot Settings panel in the Edit tab (accessible from the toolbar or menu).

### Options section

Bot-wide toggles/values defined in `config.options`. Analogous to the `setvar` block at the top of Deimos scripts — e.g. `ChallengeMode`, `PuzzleFight`, `RestartAfterPuzzleFight` from the MT Olympus bot.

Each option renders as:
- `bool` → `QCheckBox` with label (and optional tooltip from `description`)
- `int` / `float` → `QSpinBox` / `QDoubleSpinBox` with label
- `str` → `QLineEdit` with label

Options are editable in both Edit and Run views. Changes are written back to the in-memory config immediately and persisted when the bot file is saved.

Bot nodes read options at runtime via `machine.config["options"]`.

### Schema additions (BotConfig)

```python
class BotOptionModel(BaseModel):
    name: str                                      # e.g. "RestartAfterPuzzleFight"
    type: Literal["bool", "int", "float", "str"]
    default: bool | int | float | str
    description: str | None = None                 # shown as tooltip

class BotConfig(BaseModel):
    ...
    start_location: str | None = None
    drops: list[str] = []
    authors: str | None = None
    options: list[BotOptionModel] = []
```

---

## Right Sidebar — Edit View: Client Utilities

Visible only when the Edit tab is active.

One `ClientWidget` section per hooked `Client`, stacked vertically in a `QScrollArea`.

Each `ClientWidget` contains:
- **Zone row:** `QLabel` (zone name, live-updating) + `QPushButton("Copy Zone")`
- **XYZ row:** `QLabel` (xyz, live-updating) + `QPushButton("Copy XYZ")`
- **Freecam row:**
  - `QPushButton("Enter Freecam")` / `QPushButton("Exit Freecam")` — toggles freecam mode. Does **not** teleport the client when exiting.
  - `QPushButton("TP to Freecam")` — enabled only while freecam is active. Teleports the client to the current freecam position, then exits freecam mode and re-labels the button.
- Zone and XYZ update via a repeating `QTimer` (1s interval) that schedules async reads via qasync and emits a Qt signal back to update the labels.

---

## Right Sidebar — Run View: Drop Table

Visible only when the Run tab is active.

`QTableWidget` with columns: `Item Name`, `Quantity`, `Client`. Rows added by bot nodes that detect item drops. Each drop is attributed to the client that received it.

**Export CSV button** at the bottom — writes the current table to a `.csv` file via a file dialog.

---

## Edit Tab

Visual FSM editor for creating and editing bots.

### Canvas
- `QGraphicsScene` + `QGraphicsView`
- Zoom via scroll wheel (`QGraphicsView.setTransform`)
- Pan via middle-mouse drag or space+drag

### Node Items (`NodeGraphicsItem(QGraphicsItem)`)
- Rounded rectangle with title bar showing `label` (or `node_type`)
- Two connection ports: one output port (bottom/right), one input port (top/left)
- Click: select node, populate the bottom parameter panel
- Drag: reposition node (updates `NodeData.position`)
- Right-click context menu: Delete Node, Duplicate Node

### Edge Items (`EdgeGraphicsItem(QGraphicsPathItem)`)
- Cubic bezier from source output port to dest input port
- Drawn with `QPainterPath.cubicTo`
- Click: select edge, populate the bottom parameter panel
- Right-click: Delete Edge
- Creation: drag from an output port; drop on an input port to create a transition

### Bottom Panel — Node / Edge Parameters

A collapsible sub-panel at the bottom of the Edit tab canvas area. Shows fields for the currently selected node or edge. Hidden when nothing is selected.

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
- `name`, `description`, `version`, `start_location`, `drops`, `authors` fields
- `clients` selector
- `default_combat_config` (`QPlainTextEdit`)
- Options list: add/remove/edit `BotOptionModel` entries

### Toolbar
- New Bot, Open Bot, Save Bot, Save As
- Run Bot (switches to Run tab and starts execution)

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

---

## Run Tab

Read-only graphical view of the active bot FSM as it executes.

### Canvas
- `QGraphicsScene` + `QGraphicsView` — same node/edge rendering as the Edit tab, but non-interactive (`setInteractive(False)`)
- Zoom (scroll wheel) and pan (middle-mouse or space+drag) still work in read-only mode
- Active state node is highlighted (fill color change) via `on_state_change` callback from `BotMachine`
- Recently completed transitions flash briefly via `on_transition` callback

### Info bar (top of tab)
- Current state label (left)
- Elapsed timer, updated every second (center)
- Stop button (right) — calls `machine.stop()`; bot halts after the current node completes

### Bottom Panel — Log Stream

`QPlainTextEdit` (append-only, read-only). Timestamped entries written by `BotMachine` callbacks:

```
[00:01:23] → enter_dungeon
[00:01:31] SUCCESS → fight_cyclops
[00:02:04] → fight_cyclops
```

Entry types:
- **State entered:** `[HH:MM:SS] → <state_id>`
- **Transition taken:** `[HH:MM:SS] <RESULT> → <next_state_id>`
- **Bot started/stopped:** `=== Bot started ===` / `=== Bot stopped ===`

---

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
