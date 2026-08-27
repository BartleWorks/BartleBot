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
Use `poll_each_until_seen()` from `bartlebot/nodes/node_helpers.py` for operations that must complete across all clients (zone changes, prompts, loading screens).

Some world objects only serve one player at a time — a second client interacting while the
first is mid-interaction has its keypress silently swallowed. Use `take_turns()` for those:
it parks every client at `reset_xyz`, then sends them to the object one at a time, handing
each to a `turn_fn` that owns the object until it returns. The release moment is what varies
between nodes (a menu confirmed open, a zone list dismissed, a loading screen begun), which
is why it is a callback rather than a fixed sequence.

This is offered as an optional `sequential` param on `interact`, `interact_menu`,
`enter_zone_via_object`, and `enter_zone_via_list`; it defaults off, and the parallel path
in each of those nodes is untouched by it. `apply_level_scaling` predates the helper and is
unconditionally sequential against its one hardcoded station.

Pass `park_after_turn=False` when the interaction removes the client itself (a zone
transition) — otherwise the helper aims a teleport at a client already on a loading screen.

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

### Popup menus

Build every `QMenu` through `bartlebot/gui/menu_utils.py` — `popup_menu(anchor)` for a
menu built per invocation, `anchor_menu_to_window(QMenu(btn))` for a long-lived one
handed to `QToolButton.setMenu()`.

Qt gives a popup the `QWindow` of its parent's nearest *native* ancestor as transient
parent. Embedding a game client makes `ClientTile` native, and Qt then promotes its
siblings — the left sidebar, the canvas — to native windows of their own. A menu
parented to a button inside one resolves to a `QWidgetWindow` that is not top level,
which Qt refuses outright: `... must be a top level window` (#119), leaving the popup
with no transient parent at all.

The helper swaps the menu onto its top-level window on `aboutToShow` and hands it back
on `aboutToHide`. The swap cannot happen at construction: a menu built in a widget's
`__init__` has no window yet, and `window()` would return the widget itself.

A menu with no parent, or one parented directly to a window or dialog, is already fine.

### qasync + Python 3.13 task re-entrancy rule

**Never call a blocking Qt method (e.g. `QDialog.exec()`, `QMessageBox.exec()`) from inside an `async` function that is running as an asyncio Task.**

Python 3.13 enforces that only one asyncio task may be executing at a time. `QDialog.exec()` re-enters Qt's event loop synchronously, but the calling task's frame remains on the Python call stack. While the dialog is open, any asyncio sleep or future that completes will attempt a `task_wakeup()` callback. Python 3.13 detects the calling task is still "being executed" and raises `RuntimeError: Cannot enter into task while another task is being executed`. qasync catches and logs this error, but the woken task is permanently stuck — its sleep Future is consumed and it never resumes.

**Rule:** If you need to show a blocking dialog before starting an async operation, do it in the synchronous Qt slot *before* calling `asyncio.create_task()`. Example pattern in `AppController._on_run_requested()` — the safety warning dialog runs there, in the slot, so no asyncio task exists yet when `.exec()` blocks.

This bug caused the client section sidebar (zone/XYZ display) to freeze for the entire duration of any bot run that included a node with `safety_warning` set in its `NodeSpec`. The `SafetyWarningDialog.exec()` call inside the async `_start_bot()` task was killing the `_refresh_loop` and `_client_loop` tasks on every run.

### Client section refresh architecture

`ClientSection` (`bartlebot/gui/sidebar/sections/client_section.py`) is a plain `QWidget` filling `RightSidebar`'s Players tab, and runs a long-lived asyncio task (`_refresh_loop`) that polls zone/XYZ data for each connected client. Key design points:

- **Refresh rate is adaptive**, decided by the pure `client_refresh_interval(bot_running, players_visible)`: `CLIENT_REFRESH_INTERVAL_IDLE` (0.1 s) while idle, `CLIENT_REFRESH_INTERVAL_RUN` (0.5 s) during a bot run, and `CLIENT_REFRESH_INTERVAL_HIDDEN` (2.0 s) whenever the Players tab is not the current tab — visibility is checked *first*, so a hidden tab during a run polls at the slowest rate. `AppController._start_bot()` calls `set_bot_running(True/False)` at the bot lifecycle boundary; `RightSidebar.currentChanged` calls `set_players_visible(True/False)`, which restarts the loop on becoming visible rather than waiting out the in-flight 2 s sleep. Polling never stops entirely, because `AppController`'s zone-dropdown getter reads each widget's cached zone via `client_zones()`. These constants live in `bartlebot/nodes/timing.py`.
- **Per-widget timeout**: each `ClientWidget.refresh()` call is wrapped in `asyncio.wait_for(timeout=CLIENT_REFRESH_TIMEOUT)` inside the gather so a single slow client read can't stall the whole cycle.
- **Loading guard**: `ClientWidget._refresh()` checks `is_loading()` first and returns early if True, skipping the more expensive `zone_name()` and `body.position()` reads that produce stale/slow results during zone transitions.
- **WizWalker reads are synchronous**: despite the `async def` signatures, WizWalker memory reads (`zone_name()`, `body.position()`, etc.) call `ReadProcessMemory` synchronously on the event loop thread — they are not truly non-blocking. Keep this in mind when reasoning about latency or contention.
- **The Wizard row costs nothing**: `ClientWidget._refresh()` reads the wizard name from `wizard_names.cached_name()`, a dict lookup. `ClientManager._client_loop` is what fills that cache, once per character. Do not make a sidebar label trigger detection — `resolve_names()` can open the character page, which would flash the game UI every refresh.

### Settings
Use `SettingsManager` singleton. Persisted to `%APPDATA%/BartleBot/settings.json`. Credentials stored via `keyring` (Windows Credential Manager), never on disk.

#### App settings vs. node params

An app setting that a node param also expresses must never be applied by rewriting
the param on load. That is what `AppController._apply_bot_defaults` used to do to
quicksell's `sell_*` params, and it made the node param unreachable: a value stored
in a bot file was overwritten every time the file was opened, then silently written
back on the next save.

Use a three-state param instead — `ParamField(type="tri_bool", app_setting_key=…)`,
stored as `true` / `false` / `null`, defaulting to unset. Unset means "use the app
setting", so a bot with no opinion follows the user, and a bot that pins an answer
keeps it. Resolve at **run time**, in one place, and log which level answered:
`quicksell.resolve_app_defaults()` is the model, and `resolve_combat()`'s
`source_label` is the older precedent for saying where a value came from.

Everything downstream of that one resolution should see plain bools, which is what
keeps the keep-list logic and the picker pure and testable without settings.

Two things that fall out of it:

- Anything showing the user what will happen — the properties panel, the safety
  warning dialog — has to resolve too, and say when a value came from the app
  setting. The bot file does not show that.
- Values the override already wrote have to be dealt with, or they start
  outranking the app setting the moment it stops rewriting them. Every bot file
  is in this repo, so the quicksell params were migrated in place rather than at
  load time: a stored value equal to the shipped default said nothing the app
  setting does not and was dropped, while one that differs was the only trace of
  intent the file carried and stayed as a pin. `test_no_bot_pins_a_quicksell_flag_it_does_not_mean_to`
  keeps the library that way.

`Safety Delay` is the deliberate exception — it describes the machine's connection
rather than the bot, so no stored value should outrank the app setting.

### No real player data

Never commit real, identifiable player data — wizard names, character ids, account
ids, friend codes, or screenshots showing them. This repo is public, and a real
wizard name links the account to whoever wrote the commit.

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

`name_keys.py --verify` reads both the tracked fixture and the local file when
present, so recordings are still useful locally — they just cannot leave the
machine. Follow that pattern for any new tool that records live account data:
write to `*.local.json`, never append to a tracked file.

Bot files under `bots/` are the easy one to miss: `friend_name` and similar
parameters hold real character names and are functional, so redact them
deliberately rather than by find-and-replace.

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

- **WizWalker**: low-level Wizard101 memory hooking — fork `The-World-Tree/wizwalker`, branch `bartlebot`
- **WizSprinter**: safe wrappers + combat DSL — fork `The-World-Tree/wizsprinter`, branch `main`

Both are pinned by exact commit `rev` in `pyproject.toml`, using HTTPS URLs so CI
can fetch them without SSH credentials. They are **forks, not upstream**: they
carry fixes upstream does not have, including the `SpellListControl` /
`DeckListControl` memory offsets that `bartlebot/deck_io.py` depends on. Pointing
either back at its original repo will break deck reads.

### Where updates come from

The two libraries have *different* upstreams, for different reasons. Do not
assume what is true of one holds for the other.

| | Track | Why |
|---|---|---|
| **wizwalker** | `LaurenzLikeThat/wizwalker` (`development`) | `Deimos-Wizard101` is barely maintained and resistant to PRs. Laurenz keeps a fork current, and she is the practical source of memory offset fixes after a game patch. |
| **wizsprinter** | `Deimos-Wizard101/wizsprinter` (`main`) | Upstream here *is* responsive — PRs #7–#14 were merged, several from Laurenz. Her fork is not a parallel line of development; her work reaches us through Deimos. |

**Do not pull wizsprinter from Laurenz.** As of 2026-08 her fork differs from
Deimos only in packaging and docs — the library code is byte-identical — but she
has not taken the poetry→hatchling migration, so merging from her would regress
the build system. Commit counts between the two look wildly divergent (≈35 each
way); that is squash-merged PRs producing new SHAs for identical content, not
real drift. Compare trees, not commit counts.

Our fork carries **no** wizsprinter changes of its own: past work was accepted
upstream, so `The-World-Tree/wizsprinter` should stay a fast-forward of Deimos.
Divergence there is a smell.

### After a game update breaks memory offsets

Offsets change with most game patches. Laurenz usually fixes them first — check
before doing the work by hand.

```bash
cd ../wizwalker
git fetch laurenz
git log --oneline HEAD..laurenz/development     # anything new?
git merge laurenz/development                   # expect conflicts: our commits touch offsets too
poetry run pytest                               # then smoke-test against a live client
git push origin bartlebot
```

Then bump the pin in this repo (see below). If Laurenz has not fixed it yet,
re-derive the offsets with `tools/probe_item_cards.py` and commit them to our
fork — that script exists specifically to make offsets recoverable.

For wizsprinter, the same shape at a much lower cadence:

```bash
cd ../wizsprinter && git fetch deimos && git merge deimos/main
```

## Reading the game's own data

Two sources answer most "what is this offset / where does this value come from"
questions without probing a live client. **Check both before writing a probe** —
several days of `probe_*.py` work has been spent rediscovering things that were
sitting in one of these.

### Type dumps: `tools/r<rev>_WizardDev.json`

A wiztype dump of every game class with the exact byte offset of each property —
about 7,000 classes and 49,000 properties. Query it with `tools/typedump.py`:

```bash
poetry run python tools/typedump.py at ClientObject 448     # what lives at this offset?
poetry run python tools/typedump.py show DeckListControl    # full layout, incl. inherited
poetry run python tools/typedump.py prop "characterId"      # find a property everywhere
poetry run python tools/typedump.py classes "SpellList"     # find classes by regex
poetry run python tools/typedump.py gaps DeckListControl    # holes where raw members hide
poetry run python tools/typedump.py annotate ../wizwalker/wizwalker/memory/memory_objects/client_object.py
poetry run python tools/typedump.py diff <old.json> <new.json>
```

Two things to know before trusting it:

- **It covers reflected PropertyClass properties only.** Raw C++ members — child
  pointers, manager handles, the entry vectors `deck_io.py` reads — never appear.
  `gaps` is the workaround: it brackets those between named neighbours, which is
  how `deck_io`'s `0x300` entry vector shows up as a 40-byte hole between
  `m_treasureFrame` and `m_cardSize`.
- **A class name is not the runtime behavior template name**, and the difference
  is not derivable: `ClientWizPlayerNameBehavior` → `"WizPlayerNameBehavior"`,
  `ClientTreasureBookBehavior` → `"BasicTreasureBookBehavior"`,
  `ClientInventoryBehavior` → `"WizardInventoryBehavior"`. The authoritative list
  is `await client_object.list_behavior_names()` on a live client.

**Dumps are committed, and old ones are never deleted.** A dump can only be
captured from a running client, so once a patch ships there is no way back to the
previous layout. The checked-in dumps are the only record of what the offsets were
when our code last worked; `typedump.py diff` against a fresh dump then says
exactly what moved. `.gitattributes` disables textual diffing for them — use the
tool, not `git diff`.

### Game data: `Root.wad`

WizWalker can read the game's own archives, which hold the lookup tables the
client resolves IDs against. Many fields in memory are keys, not text —
`m_badgeTitle` reads `'Badges_00006184'`, and a wizard's name is three indices,
never characters — so a memory scan for display text will find nothing while the
answer sits in a WAD.

```python
from wizwalker.file_readers.wad import Wad

wad = Wad(r"C:\ProgramData\KingsIsle Entertainment\Wizard101\Data\GameData\Root.wad")
names = await wad.names()                       # ~172,000 entries
data = await wad.get_file("CharacterNames.xml") # bytes
```

Conventions worth knowing:

- `<Thing>.xml` holds the **ordered key list**; `Locale/<loc>/<Thing>.lang` maps
  key → display text. IDs index **position in the XML list**, not the numeric
  suffix of the key (`First_Boy_102` is "Luke" but sits at index 100).
- `.lang` files are UTF-16LE with CRLF, in fixed three-line records: `key`,
  translator description, display text. The description reads as blank in most
  records, which makes the format look like `key / blank / value` — a parser that
  *requires* it blank silently drops every documented key rather than failing. Slice
  at a fixed stride; see `parse_lang` in `tools/dump_dialog_captions.py`.
- Locales are `de el en-US es fr it pl`. Non-English tables can be shorter —
  later additions were not always translated.

Don't read a WAD at runtime: a 172k-entry archive read per lookup, plus a hard
dependency on the game being installed at a guessable path. Extract what you need
into a checked-in snapshot instead, as `tools/dump_name_tables.py` does for
`bartlebot/data/name_tables.json`, and regenerate it when the game adds entries.

### Telling modal dialogs apart

Every in-game modal is the same `MessageBoxModalWindow` tree, so "a modal is up"
does not say which one. Identify it by caption, comparing against the key constants
in `bartlebot/dialog_captions.py`.

Most nodes want `resolve_modal()` rather than `identify_modal()` — it acts on the
dialog and reports only what the caller has to decide about:

```python
outcome = await node_helpers.resolve_modal(client)
if outcome.refusal:                       # the game said no; stop
    machine.log(f"FAILURE: … — {dialog_captions.describe(outcome.refusal)}")
    return "FAILURE"
if outcome.answered:                      # a question was confirmed; skip this tick's retry
    return
```

Pick by the shape of the wait that follows:

| wait | use |
|---|---|
| a retry loop on `is_loading()` | `ModalAwareRetry(action_fn)` as `poll_each_until_seen`'s `action_fn`, then check `.refusal` |
| a *sticky* condition (left the zone, arrived) | `await_instance_prompt()`, or `answer_instance_prompts()` for a whole group |
| one-off, mid-loop | `resolve_modal()` directly |

Never answer a dialog with a blocking wait ahead of an `is_loading()` check: it
costs the full Safety Delay whenever no dialog appears, and three seconds spent
before watching for a loading screen can miss it entirely.

Do **not** infer identity from which buttons are showing. That only works while no
two dialogs a node can hit share a layout, and it already broke once: the dungeon
exit warning and the friend-busy dialog both show a right button, so
`teleport_to_friend` reported every dungeon exit as an unreachable friend (#84).

Captions come from `bartlebot/data/dialog_captions.json`, all seven locales per key
— memory holds the *resolved* text, so an English substring match fails silently on
any other locale. To add a dialog: append to `KNOWN_KEYS`, run
`poetry run python tools/dump_dialog_captions.py`, and commit the regenerated JSON.
`--verify` re-checks the snapshot for keys whose text no longer identifies them
uniquely. An unrecognised caption returns `None`, and callers must fall back rather
than treat it as "no dialog".

### Ambiguous UI paths

Window-tree paths match on **name only**. `_get_window_from_path` walks depth-first and
returns the first branch that completes the path, backtracking out of dead ends — which is
what makes Wizard101's duplicate `ButtonLayout` siblings safe to path through, as long as
only one branch completes.

When *several* branches complete it — a list menu whose rows are structurally identical —
the first wins silently. Narrow it instead:

| need | use |
|---|---|
| every match, to count or iterate | `find_windows_by_path(parent, path)` |
| one match, chosen by label or position | `resolve_path_match(client, path, match_text=…, match_index=…)` |
| click the one you chose | `click_window(client, window)`, not `click_by_path` |

`click_path` exposes this as **Match Text** / **Match Text: Exact** / **Match Index**, and
warns when a path matched more than one element and neither param narrowed it. Match Index
applies *after* Match Text. The text is matched against the element and any element inside
it, case-insensitively, with the game's HTML wrapping stripped (`strip_html`).

Find the text in the **UI Tree Browser's Text column** — that is what distinguishes rows a
path cannot. Select a row to read its text, or press **Load Text** to fill the column for
the selected element and everything inside it; Copy Text puts the full string on the
clipboard. Text is never read for the whole tree: `maybe_text()` is one synchronous
`ReadProcessMemory` each, and doing it for several thousand windows freezes the dialog on
load. Any new per-node read in that browser has the same problem.

Resolve once and act on the window: re-walking the path for the visibility check and again
for the click can land on two different rows.

### Bumping a pin

Local edits do **not** affect the app until they are pushed and the pin moves:

```bash
cd ../wizwalker && git commit -am "..." && git push origin bartlebot
cd ../BartleBot
# update the rev in pyproject.toml to the new commit hash
poetry lock && poetry install
```

While iterating rapidly, reinstall the local copy as editable instead of bumping
the pin each time — undo it before trusting a test run, since it silently
overrides the pinned version:

```bash
poetry run pip install -e ../wizwalker    # live editing
poetry install                            # back to the pinned commit
```

**Tag every commit you pin.** A pinned SHA reachable only from a branch tip can
be orphaned by a force-push, rebase, or branch rename, and `poetry install` then
fails for everyone including CI. Tag it *before* bumping the pin:

```bash
cd ../wizwalker && git tag bartlebot-pin-$(date +%Y%m%d) <sha> && git push origin --tags
```

Tags follow `bartlebot-pin-YYYYMMDD`. Current pins are both tagged
`bartlebot-pin-20260802` — wizwalker `1d1db0f`, wizsprinter `2118aa7`. Never
delete a tag that an older commit of this repo still pins, or you break the
build at that revision.

I have downloaded reference files to assist in planning:

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
