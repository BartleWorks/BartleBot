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

#### Keeping a session log readable

A five-hour four-client run writes tens of thousands of lines, and the difference
between a log you can debug from and one you scroll past is entirely in what does
*not* get written twice. Three rules keep it that way (#156):

- **`machine.log()` is the node-facing API, and files the record under the running
  node** — `bartlebot.node.quicksell`, not `bartlebot.bot.machine`. That is what makes
  "everything quicksell did" a grep. The machine's own bookkeeping uses
  `self._log_machine()` instead, which always lands on `bartlebot.bot.machine`. The
  split is not cosmetic: the ambient dialog and combat watchers run *concurrently*
  with whatever node is executing, so a watcher using `log()` would file its message
  under a node that had nothing to do with it.
- **Never log the same event twice.** A GUI widget that restates something another
  logger already wrote passes `extra=GUI_ONLY`, which keeps it in the in-app log
  widget and out of the session file. `bot_canvas`'s `Entered:` / `Result:` lines are
  the case this exists for — they were a fifth of the file.
- **Never log a wizard name, or its `m_nameKeys`.** Logs get pasted into bug reports,
  and `tools/name_keys.py --decode` turns the key straight back into the name, so it
  is not a safer stand-in. See *No real player data*.

`RepeatCollapsingFilter` keeps the first of a run of identical messages and reports a
count when the streak ends. It only collapses *consecutive* repeats, deliberately: two
failures alternating are the case where a count would hide something. That also limits
what it can do — a warning that fires once per loop pass is separated by everything else
that pass logged, so it does not collapse. `=== pass N ===` is what makes that shape
legible instead.

### GUI theming
Use `THEME` singleton from `bartlebot/gui/theme.py`. No hardcoded colors in widget code.

Anything a widget builds from `THEME` is a snapshot of whichever theme was active at
the time, and Qt never recomputes it: `QApplication.setStyleSheet()` delivers
`StyleChange` to every widget, but a stylesheet the widget set on *itself* always
wins and is left exactly as it was. The widget has to re-derive it.

**Inherit `ThemedMixin` (`bartlebot/gui/themed.py`) and put the work in
`apply_theme_styles`.** It is enforced: `tests/test_theme_refresh_guard.py` fails any
class that builds a `THEME`-derived value without either the mixin or a
`changeEvent` that handles `StyleChange`.

```python
class MyPanel(ThemedMixin, QWidget):          # mixin FIRST — see below
    def __init__(self) -> None:
        super().__init__()
        self._label = QLabel("hello")
        self.refresh_theme_styles()           # call this; implement the other

    def apply_theme_styles(self) -> None:
        self._label.setStyleSheet(f"color: {THEME.text};")
```

Four things that are not obvious:

- **`refresh_theme_styles()` is what you call; `apply_theme_styles()` is what you
  write.** The first carries a re-entrancy guard, and `setStyleSheet` on `self`
  delivers `StyleChange` synchronously back into `changeEvent`. Calling the hook
  directly applies twice, or recurses until the app dies in its own constructor.
- **The mixin goes first in the base list.** `class P(QWidget, ThemedMixin)` does not
  merely skip the mixin — sending `StyleChange` to such a widget aborts the process
  inside `sendEvent`, with no exception. The guard test checks base order statically
  because nothing can catch it at runtime.
- **A bare `svg_icon(name)` is themed.** Its `tint` defaults to `THEME.icon_tint`, so
  an icon set once and kept goes stale with no stylesheet anywhere to grep for. Pass
  `tint=""` for an icon that should keep its own colours.
- **A factory cannot restyle what it returns.** `field_label(text)` hands a label to a
  caller that has nothing to re-apply, so the staleness lands on the *caller* — which
  is why the guard blames a themed helper's callers. Return a
  `ThemedTextLabel` (`bartlebot/gui/widgets/themed_label.py`) instead; it owns its own
  colour, named by token so it is re-read rather than snapshotted.

Widgets that read `THEME` at paint time (e.g. `QGraphicsItem.paint()`, `_RowWidget`)
hold no baked value and need none of this.

**Never destroy a widget from inside `apply_theme_styles`.** A theme switch arrives as
`QApplication.setStyleSheet()`, which is iterating over every widget when it does, and
freeing a subtree mid-iteration aborts the process with no traceback. `_DetailPanel`
(`node_browser_dialog.py`) is the one place that rebuilds rather than restyles — its
`QScrollArea.setWidget()` deletes the old container outright — so it defers the rebuild
to the next event-loop turn via a `QTimer` parented to itself, which dies with the panel
instead of firing into a destroyed widget.

Two failure modes the guard cannot see, so check them by hand:

- **A colour inside rich text.** The required-field marker in `param_panel` is a
  `<span>` carrying `THEME.error` in the label's own *text*, so restyling means
  rewriting the text. The guard looks for `setStyleSheet`.
- **A widget styled only on the next switch.** The guard checks that
  `refresh_theme_styles()` is called *somewhere* outside `changeEvent`, not that
  construction reaches it. `WikiImportDialog` passed while its only caller was
  `_set_status`, and opened unstyled.
- **A hand-written `changeEvent` that only restyles some of the widget.** The guard
  asks whether a class handles `StyleChange`, not whether it handles all of it, so a
  partial handler passes and reads as done. `_ParamPanel` restyled three of its ten
  themed widgets that way, leaving the node help button in the old palette. Prefer
  the mixin — one hook that restyles everything is easier to keep complete than a
  handler someone extended once.

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

#### When no bot should ever store a value

`tri_bool` says "no bot *needs* to pin this". Some values are stronger than that:
storing one in a bot file is always wrong, because the value describes the user and
the file is shared. `teleport_to_friend`'s friend name is the case — a character name
in a committed bot is both a privacy leak and a bot that only its author can run.

Those use `ParamField(type="app_setting_str", app_setting_key=…)`. The widget edits
the app setting directly, and `get_params()` **skips the key entirely**, so the
properties panel cannot write it back to the bot no matter what the user types. The
guard is `test_no_bot_stores_a_friend_name`; the run-time resolution, with its
`source` label, is `teleport_to_friend.resolve_target`.

A literal left in an older file still wins at run time, logged as deprecated, so
upgrading does not break a bot — but the editor drops it on the next save, and the
safety dialog stops offering the app-setting field while it is there, because
editing a value the run will ignore is worse than not offering it.

### Restricted zones (PvP)

BartleBot does not automate PvP. `bartlebot/restricted_zones.py` and its data file
`bartlebot/data/restricted_zones.json` hold the whole policy: the `Battlegrounds`
world (all of Beastmoon) and the twelve PvP match arenas. **Raids and every other
PvE zone are deliberately allowed** — automating PvE monotony is the point of the
tool, and a guard that bans farming content is just a broken tool.

Three enforcement points, and none of them is optional:

| where | what |
|---|---|
| `check_bot_config` → `AppController._check_restricted_zones` | refuses to *start* a bot naming a restricted zone; runs first in `_on_run_requested`, and unlike the other pre-run dialogs it has no "continue" |
| `ClientHealthMonitor._restricted_zone_watcher` | polls every 0.25 s during a run; stops the FSM and unhooks every client |
| `check_duel` | reads the current duel's own flags and team make-up, catching a PvP zone no rule names |

Things to preserve when touching this:

- **Match on `world` or `exact`, never a substring.** "Arena" appears in Darkmoor,
  DragonSpire, Krokosphinx, Wysteria and a dozen housing zones, all PvE and several
  of them prime farming targets. `WizardCity/WC_Duel_Arena` is the *lobby* and is
  allowed; `WC_Duel_Arena_New` is a match zone despite the name and is not.
- **The duel flags are only read while `in_battle()`.** Out of combat the duel struct
  holds whatever the last fight left in it, and a stale `pvp` would stop a bot standing
  in a questing zone.
- **Neither duel flag is a stop signal on its own.** `m_bPVP` and `m_bBattleground`
  select a *ruleset* — match timers, pass penalty — and Challenge Mode borrows it
  wholesale for ordinary PvE dungeons, setting *both* on a dungeon boss with no other
  player in the fight. Each must be corroborated by players on two different teams
  (`_players_on_both_teams`), which no PvE fight has; the flag then only decides how
  the stop is named. Do not re-promote either to a standalone signal — that bug shipped
  twice, once per flag, and both times it stopped a Marleybone farm mid-fight.
- **An unknown zone is allowed.** A client on a loading screen has no zone; failing
  closed there would fire on every transition in every bot.
- **Stopping the FSM is not enough — the clients get unhooked.** A stopped machine
  still leaves WizWalker's memory hooks in a process sitting in a PvP arena, which is
  the state the guard exists to prevent.
- **No setting, no override, no bypass.** Anyone can edit the JSON — this is
  open source — but the shipped build must have no supported path into PvP.

Zone paths come from the game's own `GameData` WAD filenames, where `-` separates the
segments `zone_name()` returns joined by `/`. That listing is how the set was
enumerated, and how to re-check it after a patch adds a world.

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

`name_keys.py --verify` reads both the tracked fixture and the local file when
present, so recordings are still useful locally — they just cannot leave the
machine. Follow that pattern for any new tool that records live account data:
write to `*.local.json`, never append to a tracked file.

Bot files under `bots/` are the easy one to miss: a parameter holding a real
character name is *functional*, so redacting it by find-and-replace breaks the bot.
Move it out of the file instead — see "When no bot should ever store a value" above,
which is how `friend_name` was dealt with (#102). `test_no_bot_stores_a_friend_name`
keeps it out.

### Testing
- Test doubles in `tests/conftest.py`: `FakeClient`, `FakeMachine`, `FakeEntity`
- No Wizard101 process needed — all node tests use `FakeClient`
- `FakeMachine.get_option()` returns 0 for unknown options to avoid real delays

Every new node, background task, or non-trivial utility class must ship with a corresponding test file. Cover at minimum: the success path, one failure path, and any timeout/retry behavior. The exception is pure `QWidget` subclasses that can't be tested without a `QApplication`; extract any testable logic into a plain function or class.

#### Keeping the suite fast

A node test that sleeps through a production cadence costs that time on every run, so
compress the constants the code under test reads — `monkeypatch.setattr` on the *module
that imported them*, not on `timing.py`. `_fast_settle` in `tests/test_deck_io.py` and
`fast` in `tests/test_auto_select.py` are the pattern.

Two things make this less obvious than it looks:

- **Windows schedules waits on a ~15.6 ms tick.** Every `asyncio.sleep` shorter than that
  costs a full tick anyway, so dropping an interval from 10 ms to 2 ms buys *nothing*. The
  session fixture `_fine_grained_timers` in `conftest.py` calls `timeBeginPeriod(1)` to
  drop the tick to ~1 ms, which is what makes short intervals worth compressing at all.
  Don't remove it: it was worth ~7 s across the suite on its own.
- **Compress cadences, never ceilings.** A poll interval can go as low as the tick allows,
  because the fakes it drives count iterations rather than seconds. A *ceiling* — a
  `timeout=`, a `wait_for`, a Safety Delay — is what the code races against, and cutting
  one does not make a test faster so much as change which branch it takes. Undershooting
  `AUTO_SELECT_TREE_TIMEOUT` sent the drive down its blind fallback path on CI, where the
  assertions about the sighted path no longer meant anything, and the test failed with no
  hint that it had stopped testing what it names.
- **A ceiling that something is read after is a race, too.** `timeout=0.05` on a call whose
  result the test then inspects passes alone and fails in parallel, when the cancel lands
  before the object exists — see `test_apply_deck_closes_the_builder_on_timeout`. Same for
  a window several polls have to fit inside: keep it an order of magnitude wider than the
  polls nominally cost, as `test_dungeon_warning_dialog_appears_late` now does.

These bite only under load, so they pass locally and fail on a 2-core CI runner. CI runs
`-n auto` rather than a fixed worker count for the same reason — a count picked on a
16-core machine oversubscribes a runner 4:1 and stretches every wall-clock test until it
breaks.

Tests whose *subject* is elapsed time are the exception and should stay: the `#114` guards
in `test_group_manager.py` assert a regroup pays one settle rather than three. Scale
`_SETTLE` and `_BUDGET` together if they get slow — only their ratio carries the meaning.

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

The three snapshots and their tools:

| snapshot | tool | regenerate when |
|---|---|---|
| `data/name_tables.json` | `dump_name_tables.py` | the game adds name entries |
| `data/dialog_captions.json` | `dump_dialog_captions.py` | a modal is reworded, or a key is added to `KNOWN_KEYS` |
| `data/world_names.json` | `dump_world_names.py` | a world is added or renamed in any locale |

`WorldNames.lang` is worth knowing about on its own, because its key **is** the world
segment `zone_name()` returns — `WizardCity`, `DragonSpire`, `MooShu` — and its value is
the localized text the spiral door lists. That correspondence is what lets `go_to_world`
verify arrival with the glob `"<key>/*"` rather than a per-world table of spiral chamber
zone names.

What it does *not* tell you is which worlds the door actually offers. `Darkmoor` (a world,
on the door) and `G14_DM` (Castle Darkmoor, a dungeon entered from Nightside) both have
WorldNames entries and both have GameData wads; nothing separates them. So
`worlds.SPIRAL_DOOR_WORLDS` is curated by hand, every exclusion is named with its reason,
and `test_worlds.py` checks each entry against the snapshot. Guessing a rule here got
Darkmoor wrong once already.

### What neither source answers: window layout

**UI window names are not in the type dump and not in any WAD.** The dump covers
reflected `PropertyClass` properties, and the archives hold game data; the window
layout is compiled into the client binary. Searching every UI-ish XML across
`Root.wad`, `GUI-WorldData.wad` and `_Shared-WorldData.wad` for names the app
already relies on — `DeckConfiguration`, `CardsInDeck`, `ItemSpells` — returns
zero content hits, and `GUI/` in `Root.wad` is textures.

So the rule above inverts for anything shaped like "what is this control called":
go straight to a live client. Either the in-app **UI Tree Browser**, or a probe
that dumps the subtree and diffs it across a change the user makes by hand —
`tools/probe_archmastery_ui.py` is the worked example of the second.

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

Tags follow `bartlebot-pin-YYYYMMDD`. Current pins are wizwalker `ba5c32c`
(`bartlebot-pin-20260828`) and wizsprinter `2118aa7` (`bartlebot-pin-20260802`).
Never delete a tag that an older commit of this repo still pins, or you break the
build at that revision.

**Commit in the fork with the fork's identity.** `../wizwalker` has a repo-local
`user.name` / `user.email` set in the checkout. Check `git config --local user.email`
before committing there — fixing an author afterwards means rewriting, and rewriting
is what orphans a pinned SHA.

I have downloaded reference files to assist in planning:

| Library     | Local Location               | Repo URL                                             | Description |
|-------------|------------------------------|------------------------------------------------------|-------------|
| WizWalker   | `C:\GitHub\wizwalker`        | https://github.com/The-World-Tree/wizwalker            | Core library for hooking Wizard101 clients and interacting with them. Our fork; tracks `LaurenzLikeThat/wizwalker` for offset fixes, not the `Deimos-Wizard101` original.
| WizSprinter | `C:\GitHub\wizsprinter`      | https://github.com/The-World-Tree/wizsprinter          | Extension for WizWalker that adds slightly more advanced functionality. Our fork, kept as a fast-forward of `Deimos-Wizard101/wizsprinter` — that upstream is responsive and merges PRs, so our changes go there rather than diverging.
| Deimos      | `C:\GitHub\Deimos-Wizard101` | https://github.com/Deimos-Wizard101/Deimos-Wizard101 | An existing Wizard101 botting tool. Includes examples of how to interface with WizWalker and WizSprinter, as well as some custom utilities 

## Issue and project tracking

Issues live in the GitHub repo; the backlog board is the user-level project
**BartleBot Backlog** (`gh project … --owner BartleBot`, number 3), linked to the repo.

Three mechanisms, three questions. Do not make one do another's job:

| | answers | cardinality |
|---|---|---|
| **Labels** | what is this about | many per issue, permanent |
| **Milestone** | when does it ship | one per issue, closes |
| **Project board** | what am I doing next | one board, ongoing |

Most of the board is automated by the project's built-in workflows: an issue is
auto-added at `Backlog`, moves to `In progress` when a pull request links to it,
and moves to `Done` when it closes or its PR merges. Do not hand-set a Status a
workflow owns. `Ready` and `Blocked` are the only ones set by hand, and both are
judgment calls.

Closing an issue by dragging its card to `Done` is deliberately *not* enabled —
the **Auto-close issue** workflow is off, so a mis-drag cannot close an issue with
no PR trail behind it.

### Filing

- **Two labels, always, at creation**: one type (`bug` / `enhancement`) and exactly
  one `area/*`. If two areas fit equally well, it is probably two issues.
- **A milestone is a commitment.** Add `1.0.0` only if it blocks going public. No
  milestone is the default and carries no stigma.
- **Priority and Size are set at triage, not at creation.** Filing is cheap;
  estimating is not. Leave them empty and sweep them periodically.
- **Never put work items in a markdown checklist.** A checklist line cannot be
  labeled, scheduled, assigned, or closed. More than about three sub-tasks means
  the issue is an `epic`: decompose it into real sub-issues and link them
  (`POST /repos/:owner/:repo/issues/:n/sub_issues`). #110 is the worked example.

### Bodies

- **Bug**: what happened, what you expected, how to reproduce, and the relevant log
  excerpt — redacted per *No real player data*, which applies to issue bodies and
  pasted logs just as much as to committed files.
- **Enhancement**: the problem first, then a sketch of the approach. #129 and #102
  are the models — they explain *why* before *what*.

### Closing

- **Issues close via PR, not by hand.** Every PR body carries one `Closes #N` line
  per issue it resolves. Multi-issue PRs are normal here; each issue still needs
  its own line.
- Closing keywords fire only on merges into the **default branch** (`master`).
- **Close by hand only for won't-do or duplicate**, and set the reason to
  `not planned` so the issue does not read as completed.

### Board

- `Ready` is a pull queue, not a wish list — it means scoped enough to start
  without further thinking. Keep it short.
- `In progress` is set for you the moment a PR links to the issue. Set it by hand
  only when work genuinely starts before a PR exists — otherwise leave it alone.
- `Blocked` needs a comment naming the blocker, or it is just a stale `Ready`.

### Areas

One `area/*` label per issue, from this set:

| label | scope |
|---|---|
| `area/editor` | bot canvas and node editing UX |
| `area/app-ui` | app shell, sidebar, settings, windowing |
| `area/engine` | FSM, run semantics, control flow |
| `area/nodes` | node library |
| `area/bot-library` | shipped bot files under `bots/` |
| `area/clients` | client launching, accounts, characters |
| `area/combat` | combat DSL, decks, fight tables |
| `area/meta` | project, process, release |

## Ruff

Line length 120, target py313. Rules: E, W, F, I, UP, B.
Intentional ignores: B905 (zip strict=), E402 (import order), E501 (line length), B008 (default args).
See `[tool.ruff]` in `pyproject.toml` for full config.

## Preferences

- No auto-commits — only commit when explicitly asked
- Prefer library solutions over custom implementations when they fit the need
