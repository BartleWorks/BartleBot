# BartleBot

Wizard101 bot automation app. Python 3.13+, PyQt6, qasync, transitions FSM.

## Quick Start

```bash
uv run python bartlebot.py
```

To author shipped subgraphs or combat strategies, run with `BARTLEBOT_DEV=1`. That
points the store's user root at the repo, so a save lands in `subgraphs/` or
`strategies/` where git sees it, instead of in an `%APPDATA%` copy that silently
shadows the repo's. `bartlebot/store_roots.py` has the detail. The suite clears it,
and `test_every_linked_bot_resolves_under_the_shipped_root_alone` catches a
definition that only exists on your machine.

It also turns on the canvas repaint tracer (`bartlebot/gui/canvas/repaint_trace.py`, for
#73). The tracer logs a WARNING, with a screenshot under `logs/repaint/`, whenever an item
change on the canvas is left unrepainted. The window show tracer
(`bartlebot/gui/window_show_trace.py`, for #120) logs a WARNING for every stray top-level
window, ours or the game clients', with the Python stack for ours.

## Build / Test / Lint

```bash
uv sync                 # all deps including dev
uv run pytest           # pytest-asyncio, asyncio_mode = "auto"
uv run pytest -n auto   # same suite in parallel
uv run pytest -n auto --cov   # plus a coverage report; what CI runs (reported, not gated)
uv run ruff check       # lint
uv run ruff format      # auto-format
```

Python 3.13+ required. `pyproject.toml` enforces `requires-python = ">=3.13"`.

## Architecture

- **FSM engine** (`bartlebot/bot/machine.py`): `transitions.AsyncMachine` drives a node-based state machine
- **Nodes** (`bartlebot/nodes/`): async coroutines in `action/` and `condition/` subdirectories
- **GUI** (`bartlebot/gui/`): PyQt6 with qasync bridging async and Qt event loops
- **Bot files**: JSON in `bots/`, validated by Pydantic models in `bartlebot/bot/schema.py`

## Where the rest lives

This file holds what governs code written anywhere. Most of what follows is the
rule alone; the reasoning behind it, and the bug it came from, is in the file
named beside it. The subtree files load on their own when a file in that subtree
is read or edited, so the detail arrives when it applies. The `docs/` ones are
read on purpose.

| file | covers |
|---|---|
| `bartlebot/gui/CLAUDE.md` | theming, popup menus, widget parenting, qasync, task lifetime, tile slots, which panel owns what, Setup vs. Options |
| `bartlebot/gui/settings/CLAUDE.md` | `SettingsManager`, the settings dialog, the Bot tab |
| `bartlebot/nodes/CLAUDE.md` | adding a node, timing, multi-client coordination, session logs, modal dialogs, UI paths |
| `tests/CLAUDE.md` | what a change ships with, keeping the suite fast, one QApplication per worker |
| `tools/CLAUDE.md` | type dumps and `Root.wad` — read these before writing a probe |
| `docs/WINDOW_SIZING.md` | the (resolution, UI size) ladder, which XML file holds which, and why an unfittable launch is refused |
| `docs/BOT_OPTIONS.md` | the four-level resolution, params vs. options, who owns a value |
| `docs/COMBAT_CONFIG.md` | the five strategy levels, the two storage roots, a combat profile's own strategies and the profile-then-library lookup, the pick/assemble/author split, the dialog's ways in, and the Save/Cancel boundary |
| `docs/SUBGRAPHS.md` | reusable subgraphs: definition vs. instance, derived member ids, follow-latest with snapshot fallback, the three param levels, instance-owned layout, the authoring verbs |
| `docs/SUBGRAPH_CALLS.md` | a pointer to the plan for the successor epic to #37 (subgraph calls, ports, the definition document, the call node, the library rewrite), which lives on a claude.ai page — read that page before any subgraph work; it supersedes several of `SUBGRAPHS.md`'s sections |
| `docs/LIBRARY_MIGRATION.md` | where all 82 bots stand against the four core templates: per-bot tier and divergence, the template input signature, the outlier bots |
| `docs/RETURN_ROUTING.md` | the default failure edge, the explicit stop, why neither is drawn, and the three-mode failure-edge toggle |
| `docs/RELOGGING.md` | why a client slows down, and the two ways to clear it |
| `docs/RESTRICTED_ZONES.md` | the PvP policy and its three enforcement points |
| `docs/ZONE_NAVIGATION.md` | the zone navigation map: link kinds, list networks, doors and side worlds, and routes costed in seconds |
| `docs/UPSTREAM.md` | wizwalker/wizsprinter forks, offset fixes, bumping a pin |
| `docs/PROCESS.md` | issues, labels, milestones, the backlog board |

Landing a branch on `master` is the `merge` skill — the gate, the commit message
template, and the pre-merge read-through.

## Key Conventions

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
uv run python tools/name_keys.py --decode 56900648   # -> "Luke SkullHunter"
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
character name is *functional*, so redacting it by find-and-replace breaks the
bot. Move it out of the file instead, as `friend_name` was — see "When no
bot should ever store a value" in `docs/BOT_OPTIONS.md`.
`test_no_bot_stores_a_friend_name` keeps it out.

### No game assets, either

The same rule, for the same reason, applied to KingsIsle's art: **never commit an
extracted texture, model or sound**, whatever it is of. History is not rewritten before
1.0.0, so a texture committed now ships publicly then, and a repo carrying the game's
own art invites a takedown the way one carrying player data invites a link back to the
account.

Names and ids are the exception, and the line worth remembering is *text versus
asset*: `data/spell_names.json`, `data/zone_names.json` and `data/spell_schools.json`
are all extracted from `Root.wad` and all tracked, because a snapshot of names is what
lets the app talk about the game without shipping any of it.

Art therefore has no checked-in snapshot and never gets one — not the spell card art,
and not the school and archmastery icons beside it. The user extracts all of it on
their own machine with `tools/extract_card_art.py`, into
`%APPDATA%/Bartleby/BartleBot/art` — outside the repo entirely, so an accidental `git
add` cannot reach it. `bartlebot/card_art.py` is the read side, and **every lookup
returning `None` is the ordinary case**: most installs never run the extractor, so any
surface using art needs a form that works without it. `tools/CLAUDE.md` has the
extraction details, `bartlebot/gui/CLAUDE.md` the fallback rules.

### Restricted zones (PvP)

BartleBot does not automate PvP, and there is **no setting, override, or bypass**.
Three enforcement points: `check_bot_config` refuses to start a bot naming a
restricted zone, `ClientHealthMonitor._restricted_zone_watcher` stops the FSM and
unhooks every client if one is entered mid-run, and `check_duel` reads the current
duel's own flags for a PvP zone no rule names.

Raids and every other PvE zone are deliberately allowed — automating PvE monotony
is the point of the tool, and a guard that bans farming content is just a broken
tool. Match on `world` or `exact`, never a substring; read the duel flags only
while `in_battle()`, and never treat either flag as a stop signal on its own.

`docs/RESTRICTED_ZONES.md` has the reasoning and the full preserve list. Those
last two rules have each been re-broken by a well-meaning change already, both
times stopping a legitimate PvE farm mid-fight.

### Settings, and where a value lives

`SettingsManager` singleton, persisted to `%APPDATA%/Bartleby/BartleBot/settings.json`.
Credentials go through `keyring` (Windows Credential Manager), never to disk.

`Bartleby` is the **vendor**, not this product: three products are planned under it, and
the accounts, launch profiles and stored passwords belong to the user rather than to any
one of them. So `%APPDATA%/Bartleby/` is the vendor root, per-product data sits beneath it,
and the shared account records live at the root itself in `accounts.json`
(`bartlebot/shared/`, which imports nothing from `gui/` or `bot/`). The prefix is defined
once, in `bartlebot/shared/vendor.py`, and imported — never spelled out again, on disk or
in Credential Manager. `The-World-Tree` is the GitHub organization and is a separate
string on purpose: it is developer-facing and carries none of the trademark exposure a
name on the user's disk does.

A value that is not a fact about *this node in this graph* is a bot option, not a
node param:

| the value describes | where it goes |
|---|---|
| this node's place in this dungeon (a zone name, an XYZ, which player interacts) | a param |
| the user, or their machine (Safety Delay, whose wizard to port to, what quicksell may sell) | a bot option declared `default=None`, with an entry in `bot_options.APP_SETTING_KEYS` |
| this dungeon, but bot-wide (quicksell's keep list) | a bot option with a real default |

An option resolves through four levels, most local first — user override, then the
declaration if it is non-null, then the app setting, then `BotOptionSpec.default`.
**Never apply a setting by rewriting a param or a declaration on load**: it makes
the level below unreachable and silently rewrites the user's file. Resolve at run
time instead. `docs/BOT_OPTIONS.md` has the rest, including the two surfaces that
edit them.

### Logging

Per-module loggers, never `print()` for diagnostics:

```python
import logging
logger = logging.getLogger(__name__)
```

Configured in `bartlebot/logging_setup.py` — per-session timestamped files in
`logs/`, 10 most recent kept, plus a bridge handler for the in-app log widget.

- Pass `logging.WARNING`, the int constant, not `"WARNING"`.
- `machine.log()` is the node-facing API and files the record under the running
  node; the machine's own bookkeeping uses `self._log_machine()`. The two are not
  interchangeable — the ambient watchers run concurrently with whatever node is
  executing, so a watcher using `log()` files under a node it has nothing to do with.
- Never log the same event twice. A widget restating what another logger already
  wrote passes `extra=GUI_ONLY`, which keeps it out of the session file.
- **Never log a wizard name, or its `m_nameKeys`.** Logs get pasted into bug
  reports, and `tools/name_keys.py --decode` turns the key straight back into the
  name, so it is not a safer stand-in.

### Timing

Import all timing constants from `bartlebot/nodes/timing.py`. Never define a local
timeout or interval constant in a node.

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

### Testing

Every new node, background task, or non-trivial utility class ships with a test
file covering the success path, one failure path, and any timeout or retry
behavior. The exception is pure `QWidget` subclasses that cannot be tested without
a `QApplication`; extract the testable logic into a plain function or class.
`tests/CLAUDE.md` has the two ways this suite goes slow or flaky.

## Reference libraries

Downloaded locally so they can be referenced directly. WizWalker and WizSprinter
are **ours** — libraries we maintain under `The-World-Tree`, not forks staged for
an upstream PR — pinned by exact commit in `pyproject.toml`. See
`docs/UPSTREAM.md` before changing a pin or chasing a memory offset after a game
patch.

| Library | Local Location | Repo URL | Description |
|---|---|---|---|
| WizWalker | `C:\GitHub\wizwalker` | https://github.com/The-World-Tree/wizwalker | Low-level memory hooking. Ours. Still tracks `LaurenzLikeThat/wizwalker` for offset fixes, not the `Deimos-Wizard101` original. |
| WizSprinter | `C:\GitHub\wizsprinter` | https://github.com/The-World-Tree/wizsprinter | Safe wrappers and the combat DSL. Ours. Pull selectively from `Deimos-Wizard101/wizsprinter`; their `main` is no longer our baseline. |
| Deimos | `C:\GitHub\Deimos-Wizard101` | https://github.com/Deimos-Wizard101/Deimos-Wizard101 | An existing Wizard101 botting tool. Examples of interfacing with WizWalker and WizSprinter, plus some custom utilities. |

## Ruff

Line length 120, target py313. Rules: E, W, F, I, UP, B.
Intentional ignores: B905 (zip strict=), E402 (import order), E501 (line length), B008 (default args).
See `[tool.ruff]` in `pyproject.toml` for full config.

## Preferences

- No auto-commits — only commit when explicitly asked
- Prefer library solutions over custom implementations when they fit the need
