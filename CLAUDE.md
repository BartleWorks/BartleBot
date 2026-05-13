# BartleBot

Wizard101 bot automation app. Python 3.13+, PyQt6, qasync, transitions FSM.

## Quick Start

```bash
poetry install
poetry run python bartlebot.py
```

## Build / Test / Lint

```bash
poetry install              # all deps including dev
poetry run pytest           # pytest-asyncio, asyncio_mode = "auto"
poetry run ruff check       # lint
poetry run ruff format      # auto-format
```

Python 3.13+ required. `pyproject.toml` enforces `^3.13`.

## Architecture

- **FSM engine** (`bartlebot/bot/machine.py`): `transitions.AsyncMachine` drives a node-based state machine
- **Nodes** (`bartlebot/nodes/`): async coroutines in `action/` and `condition/` subdirectories
- **GUI** (`bartlebot/gui/`): PyQt6 with qasync bridging async and Qt event loops
- **Bot files**: JSON in `bots/`, validated by Pydantic models in `bartlebot/bot/schema.py`

Several related repos have been downloaded locally so they can be easily referenced:
- WizWalker: C:\GitHub\wizwalker
- WizSprinter: C:\GitHub\wizsprinter
- Deimos: C:\GitHub\Deimos-Wizard101

## Adding a New Node

1. Create `bartlebot/nodes/action/<name>.py` (or `condition/`)
2. Export `NODE_SPEC` (NodeSpec), `PARAM_SCHEMA` (list[ParamField]), and `async def run(machine, clients, params) -> str`
3. Register in `bartlebot/nodes/registry.py` — add to both `NODE_REGISTRY` and `NODE_SPECS`
4. Action nodes return `"SUCCESS"` / `"FAILURE"`; condition nodes return `"TRUE"` / `"FALSE"`
5. The GUI auto-generates parameter forms and tooltips from `NODE_SPEC`

## Key Conventions

### Timing
Import all timing constants from `bartlebot/nodes/timing.py`. Never define local timeout or interval constants in nodes.

### Multi-client coordination
Use `poll_until_each_seen()` from `bartlebot/nodes/node_helpers.py` for operations that must complete across all clients (zone changes, prompts, loading screens).

### Logging
Use Python `logging` module with per-module loggers:
```python
import logging
logger = logging.getLogger(__name__)
```
Never use `print()` for diagnostics. Logging is configured in `bartlebot/logging_setup.py` — per-session timestamped files in `logs/` (10 most recent kept), plus a GUI bridge handler for the in-app log widget.

Pass `logging.WARNING` (int constant), not `"WARNING"` (string), to `machine.log()` and any other logging call. The logging module expects integer level values; a string may coerce silently today but is semantically wrong.

### GUI theming
Use `THEME` singleton from `bartlebot/gui/theme.py`. No hardcoded colors in widget code.

Any widget that sets a stylesheet using `THEME` tokens must also override `changeEvent` to re-apply that stylesheet when the theme changes. Qt delivers `QEvent.Type.StyleChange` to all widgets when `QApplication.setStyleSheet()` is called (which happens on every theme switch via `AppController._apply_theme()`). Stylesheets applied at `__init__` time are NOT automatically refreshed — without a `changeEvent` override the widget will show stale colors after a theme change.

Pattern:
```python
from PyQt6.QtCore import QEvent
from bartlebot.gui.theme import THEME

def changeEvent(self, event: QEvent) -> None:
    super().changeEvent(event)
    if event.type() == QEvent.Type.StyleChange:
        self._my_widget.setStyleSheet(f"color: {THEME.text}; ...")
```

Widgets that read THEME at paint time (e.g. `QGraphicsItem.paint()`) update automatically and don't need this.

### qasync + Python 3.13 task re-entrancy rule

**Never call a blocking Qt method (e.g. `QDialog.exec()`, `QMessageBox.exec()`) from inside an `async` function that is running as an asyncio Task.**

Python 3.13 enforces that only one asyncio task may be executing at a time. `QDialog.exec()` re-enters Qt's event loop synchronously, but the calling task's frame remains on the Python call stack. While the dialog is open, any asyncio sleep or future that completes will attempt a `task_wakeup()` callback. Python 3.13 detects the calling task is still "being executed" and raises `RuntimeError: Cannot enter into task while another task is being executed`. qasync catches and logs this error, but the woken task is permanently stuck — its sleep Future is consumed and it never resumes.

**Rule:** If you need to show a blocking dialog before starting an async operation, do it in the synchronous Qt slot *before* calling `asyncio.create_task()`. Example pattern in `AppController._on_run_requested()` — the safety warning dialog runs there, in the slot, so no asyncio task exists yet when `.exec()` blocks.

This bug caused the client section sidebar (zone/XYZ display) to freeze for the entire duration of any bot run that included a node with `safety_warning` set in its `NodeSpec`. The `SafetyWarningDialog.exec()` call inside the async `_start_bot()` task was killing the `_refresh_loop` and `_client_loop` tasks on every run.

### Client section refresh architecture

`ClientSection` (`bartlebot/gui/sidebar/sections/client_section.py`) runs a long-lived asyncio task (`_refresh_loop`) that polls zone/XYZ data for each connected client. Key design points:

- **Refresh rate is adaptive**: `CLIENT_REFRESH_INTERVAL_IDLE` (0.1 s) while idle, `CLIENT_REFRESH_INTERVAL_RUN` (0.5 s) during a bot run. `AppController._start_bot()` calls `set_bot_running(True/False)` at the bot lifecycle boundary. These constants live in `bartlebot/nodes/timing.py`.
- **Per-widget timeout**: each `ClientWidget.refresh()` call is wrapped in `asyncio.wait_for(timeout=CLIENT_REFRESH_TIMEOUT)` inside the gather so a single slow client read can't stall the whole cycle.
- **Loading guard**: `ClientWidget._refresh()` checks `is_loading()` first and returns early if True, skipping the more expensive `zone_name()` and `body.position()` reads that produce stale/slow results during zone transitions.
- **WizWalker reads are synchronous**: despite the `async def` signatures, WizWalker memory reads (`zone_name()`, `body.position()`, etc.) call `ReadProcessMemory` synchronously on the event loop thread — they are not truly non-blocking. Keep this in mind when reasoning about latency or contention.

### Settings
Use `SettingsManager` singleton. Persisted to `%APPDATA%/BartleBot/settings.json`. Credentials stored via `keyring` (Windows Credential Manager), never on disk.

### Testing
- Test doubles in `tests/conftest.py`: `FakeClient`, `FakeMachine`, `FakeEntity`
- No Wizard101 process needed — all node tests use `FakeClient`
- `FakeMachine.get_option()` returns 0 for unknown options to avoid real delays

Every new node, background task, or non-trivial utility class must ship with a corresponding test file. Cover at minimum: the success path, one failure path, and any timeout/retry behavior. The exception is pure `QWidget` subclasses that can't be tested without a `QApplication`; extract any testable logic into a plain function or class.

### Use public API across class boundaries

Never access private attributes (names starting with `_`) of `BotMachine`, `AppController`, `ClientManager`, `EditScene`, `BotCanvas`, or any other BartleBot class from outside that class. If you need access to something, add a property or public method to the owning class. Private attributes are implementation details; reaching into them from outside creates hidden coupling that breaks on any internal refactor.

Examples of what this prevents: `machine._stop_flag.is_set()` → use `machine.is_stopped`; `scene._region_items` → use `scene.region_items`; `canvas._find_empty_grid_cell()` → use `canvas.find_empty_grid_cell()`.

### Callback guards in BotMachine

Every callback stored on `BotMachine` and invoked from the `run()` loop must be wrapped in try/except. A bare callback call will crash the FSM on any GUI exception. Follow the existing pattern for `on_state_change` and `on_transition`:

```python
if self._on_my_callback:
    try:
        self._on_my_callback(arg1, arg2)
    except Exception:
        logger.exception("on_my_callback raised")
```

When adding a new callback to `BotMachine`, check that its invocation site has this guard.

### Background task lifetime in GUI widgets

Any `asyncio.Task` or `QThread` started inside a `QWidget` or `QDialog` must be explicitly cleaned up in `closeEvent` to prevent callbacks from firing on a destroyed widget.

Pattern for asyncio tasks:
```python
def __init__(self):
    self._my_task: asyncio.Task | None = None
    self._closed = False

def closeEvent(self, event):
    self._closed = True
    if self._my_task:
        self._my_task.cancel()
    super().closeEvent(event)

async def _some_async_method(self):
    if self._closed:
        return
    # ... do work ...
```

For `QThread`, call `thread.quit()` and `thread.wait()` in `closeEvent` before letting the dialog be destroyed.

## External Dependencies

- **WizWalker**: low-level Wizard101 memory hooking (Git branch `development` — exact commit pinned via `poetry.lock`)
- **WizSprinter**: safe wrappers + combat DSL (pinned to commit hash in `pyproject.toml`)
- Both are fragile to game updates. After a game patch: update the `rev` in `pyproject.toml`, run `poetry lock`, test locally.

I have downloaded reference files to assist in planning:

| Library     | Local Location               | Repo URL                                             | Description |
|-------------|------------------------------|------------------------------------------------------|-------------|
| WizWalker   | `C:\GitHub\wizwalker`        | https://github.com/Deimos-Wizard101/wizwalker        | Core library for hooking Wizard101 clients and interacting with them. Provides main basic functionality.
| WizSprinter | `C:\GitHub\wizsprinter`      | https://github.com/Deimos-Wizard101/wizsprinter      | Extension for WizWalker that adds slightly more advanced functionality. Not very actively maintained, so consult Deimos (below) for examples on how to use. Deimos does have some custom WizSprinter implementation.
| Deimos      | `C:\GitHub\Deimos-Wizard101` | https://github.com/Deimos-Wizard101/Deimos-Wizard101 | An existing Wizard101 botting tool. Includes examples of how to interface with WizWalker and WizSprinter, as well as some custom utilities 

## Ruff

Line length 120, target py313. Rules: E, W, F, I, UP, B.
Intentional ignores: B905 (zip strict=), E402 (import order), E501 (line length), B008 (default args).
See `[tool.ruff]` in `pyproject.toml` for full config.

## Preferences

- No auto-commits — only commit when explicitly asked
- Prefer library solutions over custom implementations when they fit the need
