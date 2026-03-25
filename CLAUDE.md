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

## Ruff

Line length 120, target py313. Rules: E, W, F, I, UP, B.
Intentional ignores: B905 (zip strict=), E402 (import order), E501 (line length), B008 (default args).
See `[tool.ruff]` in `pyproject.toml` for full config.

## Preferences

- No auto-commits — only commit when explicitly asked
- Prefer library solutions over custom implementations when they fit the need
