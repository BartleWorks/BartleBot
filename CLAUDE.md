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
poetry run pytest -n auto   # same suite in parallel (~10s vs ~33s); what CI runs
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

## Where the rest lives

The subtree files load automatically when a file in that subtree is read or
edited; the `docs/` ones are read on purpose.

| file | covers |
|---|---|
| `bartlebot/gui/CLAUDE.md` | theming, popup menus, widget parenting, qasync, task lifetime, tile slots, which panel owns what, Setup vs. Options |
| `bartlebot/gui/settings/CLAUDE.md` | `SettingsManager`, the settings dialog, the Bot tab |
| `bartlebot/nodes/CLAUDE.md` | adding a node, timing, multi-client coordination, session logs, modal dialogs, UI paths |
| `tests/CLAUDE.md` | what a change ships with, keeping the suite fast, one QApplication per worker |
| `tools/CLAUDE.md` | type dumps and `Root.wad` — read these before writing a probe |
| `docs/BOT_OPTIONS.md` | the four-level resolution, params vs. options, who owns a value |
| `docs/RELOGGING.md` | why a client slows down, and the two ways to clear it |
| `docs/RESTRICTED_ZONES.md` | the PvP policy and its three enforcement points |
| `docs/UPSTREAM.md` | wizwalker/wizsprinter forks, offset fixes, bumping a pin |
| `docs/PROCESS.md` | issues, labels, milestones, the backlog board |

## Key Conventions

### Logging
Use Python `logging` module with per-module loggers:
```python
import logging
logger = logging.getLogger(__name__)
```
Never use `print()` for diagnostics. Logging is configured in `bartlebot/logging_setup.py` — per-session timestamped files in `logs/` (10 most recent kept), plus a GUI bridge handler for the in-app log widget.

Pass `logging.WARNING` (int constant), not `"WARNING"` (string), to `machine.log()` and any other logging call. The logging module expects integer level values; a string may coerce silently today but is semantically wrong.

### No real player data

Never commit real, identifiable player data — wizard names, character ids, account
ids, friend codes, or screenshots showing them. This repo is private today but goes
public at 1.0.0, and history is not rewritten on the way — anything committed now
ships then. A real wizard name links the account to whoever wrote the commit.

This applies to test fixtures, sample data, docstrings, comments, log excerpts
pasted into issues, and bot files. It applies to *any* player, not just your own.

Use synthetic values instead. For anything name-shaped, build it from the shipped
tables so it stays realistic and verifiable rather than arbitrary:

```bash
poetry run python tools/name_keys.py --decode 56900648   # -> "Luke SkullHunter"
```

`tools/name_key_samples.json` is generated this way: real `m_nameKeys` values for
characters that do not exist, which still decode correctly and still exercise the
real tables. It is tracked, and safe to share.

Tools that capture real values write to gitignored paths, so an accidental commit
is not possible by mistake:

| written by | path | tracked |
|---|---|---|
| `probe_name_behavior.py --record` | `tools/name_key_samples.local.json` | no (`*.local.json`) |
| `probe_wizard_name.py` | `probe_*_report.txt` | no |
| `probe_chat_log_size.py --csv` | `*.local.csv` (refuses any other suffix) | no |

`name_keys.py --verify` reads both the tracked fixture and the local file when
present, so recordings are still useful locally — they just cannot leave the
machine. Follow that pattern for any new tool that records live account data:
write to `*.local.json`, never append to a tracked file.

Bot files under `bots/` are the easy one to miss: a parameter holding a real
character name is *functional*, so redacting it by find-and-replace breaks the bot.
Move it out of the file instead — see "When no bot should ever store a value" in
`docs/BOT_OPTIONS.md`,
which is how `friend_name` was dealt with (#102). `test_no_bot_stores_a_friend_name`
keeps it out.

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

## Reference libraries

| Library     | Local Location               | Repo URL                                             | Description |
|-------------|------------------------------|------------------------------------------------------|-------------|
| WizWalker   | `C:\GitHub\wizwalker`        | https://github.com/The-World-Tree/wizwalker            | Core library for hooking Wizard101 clients and interacting with them. Our fork; tracks `LaurenzLikeThat/wizwalker` for offset fixes, not the `Deimos-Wizard101` original.
| WizSprinter | `C:\GitHub\wizsprinter`      | https://github.com/The-World-Tree/wizsprinter          | Extension for WizWalker that adds slightly more advanced functionality. Our fork, kept as a fast-forward of `Deimos-Wizard101/wizsprinter` — that upstream is responsive and merges PRs, so our changes go there rather than diverging.
| Deimos      | `C:\GitHub\Deimos-Wizard101` | https://github.com/Deimos-Wizard101/Deimos-Wizard101 | An existing Wizard101 botting tool. Includes examples of how to interface with WizWalker and WizSprinter, as well as some custom utilities 

## Ruff

Line length 120, target py313. Rules: E, W, F, I, UP, B.
Intentional ignores: B905 (zip strict=), E402 (import order), E501 (line length), B008 (default args).
See `[tool.ruff]` in `pyproject.toml` for full config.

## Preferences

- No auto-commits — only commit when explicitly asked
- Prefer library solutions over custom implementations when they fit the need
