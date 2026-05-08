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

Reference docs:
- @docs/ARCHITECTURE.md — full system overview
- @docs/FSM_ARCHITECTURE.md — FSM design and transitions library usage
- @docs/BOT_SCHEMA.md — JSON bot file schema
- @docs/GUI_ARCHITECTURE.md — widget hierarchy and signal flow
- @docs/DESIGN_REVIEW.md — quality review with open issues and roadmap

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

### Settings
Use `SettingsManager` singleton. Persisted to `%APPDATA%/BartleBot/settings.json`. Credentials stored via `keyring` (Windows Credential Manager), never on disk.

### Testing
- Test doubles in `tests/conftest.py`: `FakeClient`, `FakeMachine`, `FakeEntity`
- No Wizard101 process needed — all node tests use `FakeClient`
- `FakeMachine.get_option()` returns 0 for unknown options to avoid real delays

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
