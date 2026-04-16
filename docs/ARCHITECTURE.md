# BartleBot Architecture Guide

This document is intended for developers who are new to the codebase. It explains the project structure, how the major components work, and how they interact.

## What BartleBot Does

BartleBot automates Wizard101 gameplay by running a user-configured **finite state machine (FSM)** against one or more hooked game clients. Each node in the FSM executes a task (move to a zone, fight an enemy, collect wisps), and the edges between nodes define when to advance — usually based on whether the task succeeded or failed.

Users build bots in BartleBot's visual editor and save them as JSON files. They then hook their Wizard101 clients, load a bot file, and press Start.

---

## Tech Stack

| Technology | Role |
|------------|------|
| [Python 3.13+](https://www.python.org/downloads/) | Language |
| [Poetry](https://python-poetry.org/) | Dependency management |
| [PyQt6](https://pypi.org/project/PyQt6/) | GUI framework |
| [qasync](https://github.com/CabbageDevelopment/qasync) | Bridges asyncio and the Qt event loop |
| [transitions](https://github.com/pytransitions/transitions) | FSM engine (`AsyncMachine`) that builds upon `asyncio`|
| [Pydantic](https://docs.pydantic.dev/) | Bot file validation and serialization |
| [WizWalker](https://github.com/Deimos-Wizard101/wizwalker) | Low-level Wizard101 memory hooking and client API |
| [WizSprinter](https://github.com/Deimos-Wizard101/wizsprinter) | Higher-level utilities on top of WizWalker (entity finding, `SprintyCombat`) |

---

## Directory Structure

```
BartleBot/
├── bartlebot.py              # Entry point: wires together GUI, FSM, and client loop
│
├── bartlebot/
│   ├── bot/
│   │   ├── machine.py        # BotMachine — the FSM engine
│   │   └── schema.py         # Pydantic models for bot config (BotConfig, StateModel, …)
│   │
│   ├── nodes/
│   │   ├── registry.py       # NODE_REGISTRY and NODE_SPECS — all registered node types
│   │   ├── param_schema.py   # ParamField, NodeSpec, ReturnSpec — GUI/doc metadata types
│   │   ├── enter_zone.py          # Node: transition between zones via gateway coords
│   │   └── handle_combat.py  # Node: enter and complete a combat encounter
│   │
│   ├── gui/
│   │   ├── main_window.py    # QMainWindow: top-level layout and menu bar
│   │   ├── bot_canvas.py     # Central canvas: visual FSM editor + run view
│   │   ├── graph_items.py    # QGraphicsItems: NodeItem (state), EdgeItem (transition)
│   │   ├── bot_info_panel.py # Left sidebar: bot metadata and bot-level options
│   │   ├── sidebar.py        # Right panel: per-client utility controls
│   │   ├── client_widget.py  # Single-client widget: zone, XYZ, freecam
│   │   └── drop_panel.py     # Right panel: drop table with CSV export
│   │
│   ├── utils/
│   │   └── drop_detection.py # Chat-based drop detection helpers
│   │
│   ├── defaults/
│   │   └── combat_config.py  # BARTLEBOT_DEFAULT_COMBAT_CONFIG — built-in combat DSL
│   │
│   ├── log_level.py          # LogLevel IntEnum (DEBUG, INFO, WARNING, ERROR)
│   └── recent_files.py       # Persists the recent files list
│
├── tests/
│   ├── test_machine.py       # FSM logic tests (33 tests, no client required)
│   ├── test_schema.py        # Bot config validation and serialization tests
│   └── test_editor_helpers.py
│
├── examples/                 # Sample bot JSON files
│   ├── unicorn_way_test.json
│   └── rattlebones.json
│
└── docs/                     # You are here
```

---

## Core Concepts

### Bot Config (the data model)

A bot is stored as a JSON file. When loaded, it is validated and held in memory as a `BotConfig` Pydantic model (`bartlebot/bot/schema.py`).

```
BotConfig
├── name, description, version, authors
├── initial: str              # ID of the starting state
├── default_combat_config     # WizSprinter DSL string, or None to use built-in default
├── options: [BotOptionModel] # User-exposed bot-level settings (booleans, numbers, …)
├── states: [StateModel]      # The FSM states
│   └── StateModel: id, node_type, label, position, params
└── transitions: [TransitionModel]
    └── TransitionModel: source, dest, when, flag, label
```

The `when` field on a transition means "fire this edge when the previous node returned `<when>`" (e.g., `"SUCCESS"` or `"FAILURE"`). The `flag` field adds an additional guard: a bot option must be truthy. Both fields are optional — a transition with neither fires unconditionally (acts as an else/fallthrough).

`BotConfig.to_machine_config()` converts the high-level config into the flat dict format that `BotMachine` and the underlying `AsyncMachine` expect.

### FSM Engine (BotMachine)

`BotMachine` (`bartlebot/bot/machine.py`) wraps `transitions.extensions.asyncio.AsyncMachine`. It drives the FSM in a loop:

```
while not stopped:
    wait if paused
    look up current state's node_type in NODE_REGISTRY
    call await node_fn(machine, clients, params)
    store result in self.last_result
    fire the "advance" trigger → AsyncMachine evaluates conditions, moves to next state
```

Conditions are generated dynamically via `__getattr__`:
- `_result_is_SUCCESS` returns `True` when `last_result == "SUCCESS"`
- `_flag_repeat` returns `True` when the bot option named `"repeat"` is truthy

This means `BotConfig.to_machine_config()` can write condition names like `"_result_is_SUCCESS"` into the transitions dict, and `BotMachine.__getattr__` synthesizes the corresponding callable at runtime — no boilerplate needed when adding new result values.

### Node Contract

Every node is an async coroutine with this exact signature:

```python
async def run(machine: BotMachine, clients: list, params: dict) -> str:
    ...
    return "SUCCESS"  # or "FAILURE", or any custom string
```

The return value is the string that transitions use for routing. Nodes use `machine.log(msg, level)` to emit log output and `machine.on_drop(item, qty, client_idx)` to report drops. If a node raises an unhandled exception, `BotMachine.run()` catches it, logs the traceback, and treats it as `"FAILURE"`.

### Node Registry

`bartlebot/nodes/registry.py` contains two dicts:

- `NODE_REGISTRY: dict[str, Coroutine]` — maps a `node_type` string to the node's `run` coroutine
- `NODE_SPECS: dict[str, NodeSpec]` — maps the same key to a `NodeSpec` describing the node's parameters and return values, used by the GUI to render the properties panel

To add a new node type, create a `.py` file in `bartlebot/nodes/`, implement `run()` and `NODE_SPEC`, then add both to `registry.py`. Nothing else needs to change.

---

## How a Bot Run Works (End-to-End)

```
bartlebot.py: start_bot()
    ↓
Creates BotMachine(
    clients=_clients,          # list of SprintyClient objects (hooked Wizard101 windows)
    config=config.to_machine_config(),
    node_registry=NODE_REGISTRY,
    bot_config=config,         # kept alive for live option reads
    on_state_change=...,       # GUI callback: highlights the current node
    on_transition=...,         # GUI callback: animates the edge
    on_drop=...,               # GUI callback: updates the drop table
    on_log=...,                # GUI callback: appends to the run log
)
    ↓
asyncio.create_task(machine.run())
    ↓
BotMachine.run() loop:
    1. Reads self.state → looks up state_def in self._state_map
    2. Calls on_state_change(state_id)  →  GUI highlights the node
    3. Calls await node_fn(machine, clients, params)
    4. Stores result in self.last_result
    5. Calls await self.advance()  →  AsyncMachine evaluates _result_is_* conditions
    6. Calls on_transition(prev, result, next)  →  GUI animates the edge
    7. Repeats
```

The `_clients` list is managed by `client_loop()`, a long-running task that polls `WizSprinter` for hooked Wizard101 processes, activates memory hooks on new ones, and removes dead ones.

---

## GUI Overview

The window has three columns:

```
┌─────────────────┬──────────────────────────────┬────────────────────┐
│  BotInfoPanel   │         BotCanvas            │  Right tabs        │
│  (left sidebar) │    (central QGraphicsScene)  │  ┌─────────────┐   │
│                 │                              │  │ClientSidebar│   │
│  Bot name       │  ● state_a ──→ state_b ──┐  │  └─────────────┘   │
│  Description    │       ↑           │       │  │  ┌─────────────┐   │
│  Authors        │       └───────────┘       │  │  │  DropPanel  │   │
│  Options        │                           │  │  └─────────────┘   │
│  Combat config  │  [Properties] [Run log]   │  └────────────────────┘
└─────────────────┴──────────────────────────────┘
```

**BotCanvas** (`gui/bot_canvas.py`) is the largest component. It has two modes:
- `BotMode.EDIT` — nodes and edges are interactive; clicking a node opens its params in the Properties panel below
- `BotMode.RUN` — nodes and edges become read-only; the run log tab becomes active; the current state is highlighted live

The graph is rendered with custom `QGraphicsItem` subclasses: `NodeItem` (rounded rect, draggable, has input/output ports) and `EdgeItem` (bezier curve with an arrowhead and optional label).

**BotInfoPanel** (`gui/bot_info_panel.py`) reads and writes the `BotConfig` object directly. Changes fire `on_config_changed`, which propagates to `bartlebot.py` and marks the file dirty.

All long-running async work (client polling, bot execution) runs as asyncio tasks inside the qasync event loop. GUI updates are dispatched synchronously through the callback functions injected into `BotMachine` — no Qt signals or threads are used.

---

## Bot File Format

Bot files are plain JSON. The full schema is in `docs/BOT_SCHEMA.md`. A minimal example:

```json
{
  "name": "My Bot",
  "version": "1.0",
  "initial": "go_to_dungeon",
  "states": [
    {
      "id": "go_to_dungeon",
      "node_type": "enter_zone",
      "params": {
        "from_zone": "WizardCity/WC_Hub",
        "to_zone": "WizardCity/WC_Interiors/WC_Dungeon01_Interior",
        "xyz": {"x": -4200.0, "y": 1900.0, "z": 200.0}
      }
    },
    {
      "id": "fight_boss",
      "node_type": "handle_combat",
      "params": {"enemy": "Rattlebones", "use_bot_default": true}
    }
  ],
  "transitions": [
    {"trigger": "advance", "source": "go_to_dungeon", "dest": "fight_boss", "when": "SUCCESS"},
    {"trigger": "advance", "source": "go_to_dungeon", "dest": "go_to_dungeon"},
    {"trigger": "advance", "source": "fight_boss",    "dest": "go_to_dungeon", "when": "SUCCESS"},
    {"trigger": "advance", "source": "fight_boss",    "dest": "fight_boss"}
  ]
}
```

A `when`-less transition acts as a fallthrough (like an `else`). States always need at least one fallthrough transition so the FSM can self-loop on unexpected results.

---

## Testing

Tests live in `tests/` and run with pytest:

```bash
poetry run pytest
```

`pytest.ini_options` sets `asyncio_mode = "auto"` so async test functions run automatically.

**FSM tests** (`test_machine.py`) replace real nodes with async lambdas and pass `clients=[]`. No Wizard101 process or WizWalker hooks are required:

```python
def make_machine(node_fn):
    return BotMachine(clients=[], config=TWO_STATE_CONFIG, node_registry={"mock": node_fn})

async def test_advances_on_success():
    visited = []
    async def node(machine, clients, params):
        visited.append(machine.state)
        if len(visited) >= 2: machine.stop()
        return "SUCCESS"
    await make_machine(node).run()
    assert visited == ["state_a", "state_b"]
```

Individual node implementations (`enter_zone`, `handle_combat`, etc.) are not yet unit-tested because they call real WizWalker/WizSprinter client methods. Adding a `FakeClient` with async method stubs in `bartlebot/testing/fake_client.py` would enable this without any architectural changes — nodes receive their clients via injection and do not import client types.

---

## Adding a New Node

1. Create `bartlebot/nodes/<your_node>.py`:

```python
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA = [
    ParamField(key="my_param", type="str", label="My Param", help="...", default=""),
]

NODE_SPEC = NodeSpec(
    description="What this node does.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "When it worked."),
        ReturnSpec("FAILURE", "When it didn't."),
    ],
)

async def run(machine, clients: list, params: dict) -> str:
    value = params.get("my_param", "")
    machine.log(f"Running with my_param={value!r}")
    # ... do async work with clients ...
    return "SUCCESS"
```

2. Register it in `bartlebot/nodes/registry.py`:

```python
from bartlebot.nodes.your_node import run as your_node_run, NODE_SPEC as _your_node_spec

NODE_REGISTRY["your_node"] = your_node_run
NODE_SPECS["your_node"]    = _your_node_spec
```

The GUI will automatically render the correct parameter widgets based on `NODE_SPEC.params`. No other changes needed.

---

## Key Reference Files

| If you want to understand… | Read… |
|---------------------------|-------|
| The full node library spec | `docs/BARTLEBOT_LIBRARY.md` |
| The JSON bot file schema | `docs/BOT_SCHEMA.md` |
| The FSM implementation details | `docs/FSM_ARCHITECTURE.md` |
| The GUI component breakdown | `docs/GUI_ARCHITECTURE.md` |
| WizWalker client API | `docs/WIZWALKER_REFERENCE.md` |
| WizSprinter SprintyClient / combat DSL | `docs/WIZSPRINTER_REFERENCE.md` |
