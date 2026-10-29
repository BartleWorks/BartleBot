@../bartleby-core/CLAUDE.md

# BartleBot

Wizard101 bot automation app. Python 3.13+, PyQt6, qasync, transitions FSM.

## Quick Start

```bash
uv run python bartlebot.py
```

To author shipped bots, templates, subgraphs or combat strategies, run with
`BARTLEBOT_DEV=1`. That points every store's user root at the repo, so a save lands in
`bots/`, `templates/`, `subgraphs/` or `strategies/` where git sees it, instead of in an
`%APPDATA%` copy that shadows the repo's or, for a bot or template, is never seen by
anyone else. `bartlebot/content/store_roots.py` has the detail, and `bartlebot/content/bot_roots.py` the
bots' part; logs stay in the repo's `logs/` too. The suite clears it, and
`test_every_linked_bot_resolves_under_the_shipped_root_alone` and
`test_every_shipped_bot_made_from_a_template_resolves_under_the_shipped_root_alone` catch
a definition or template that only exists on your machine. File › Templates… opens a
template's draft for anyone, but only the user's own: dev mode is what makes the shipped
templates under `templates/` editable, since there every template is the user's. Only
Release on the draft's crumb writes a version users are offered
(`bartlebot/content/templates/template_store.py`). New Bot copies the latest released version into a new
bot, records it in the bot's `template`, and saves the file at once
(`bartlebot/content/new_bot.py`).

It also turns on the canvas repaint tracer (`bartlebot/gui/debug/repaint_trace.py`, for
#73). The tracer logs a WARNING, with a screenshot under `logs/repaint/`, whenever an item
change on the canvas is left unrepainted. The window show tracer
(`bartlebot/gui/debug/window_show_trace.py`, for #120) logs a WARNING for every stray top-level
window, ours or the game clients', with the Python stack for ours. The press tracer
(`bartlebot/gui/debug/press_trace.py`, for #126) logs at DEBUG every press over the
canvas that some other widget took instead of the viewport. The stall tracer
(`bartlebot/gui/debug/stall_trace.py`) watches the event loop for the whole session, and
whenever it is held for over a second, writes every thread's stack, taken during the
freeze, to `logs/stalls/`. The game hang tracer (`bartlebot/gui/debug/game_hang_trace.py`)
logs a WARNING whenever a game window goes over a second without answering, saying what its
thread was waiting on, as Windows' wait chain traversal names it. The run trace
(core's `bartleby.runtime.run_trace`, started by `app_controller.start_run_trace`) writes a
timeline of every run to `logs/trace/`: spans for
the pre-run steps, each node and each combat round's phases, every key and click with the
function that sent it, UI-tree walks, memory reads and event-loop stalls.
`uv run python tools/run_trace_report.py` summarizes the newest one.

## Build / Test / Lint

```bash
uv sync                 # all deps including dev
uv run pytest           # pytest-asyncio, asyncio_mode = "auto"
uv run pytest -n auto   # same suite in parallel
uv run pytest --cov     # serial plus a coverage report; what CI runs (reported, not gated)
uv run ruff check       # lint
uv run ruff format      # auto-format
uv run --group site zensical serve -f site/zensical.toml   # preview the download site
```

Python 3.13+ required. `pyproject.toml` enforces `requires-python = ">=3.13"`.

## Architecture

- **FSM engine** (`bartlebot/bot/machine.py`): `transitions.AsyncMachine` drives a node-based state machine
- **Nodes** (`bartlebot/nodes/`): async coroutines, grouped by node-browser category (`combat_and_healing/`, `control_flow/`, `interaction/`, `teleportation/`, `utility/`, `zone_entry/`)
- **GUI** (`bartlebot/gui/`): PyQt6 with qasync bridging async and Qt event loops
- **Bot files**: JSON in `bots/`, validated by Pydantic models in `bartlebot/bot/schema.py`

## Layout

`bartlebot/`'s top level is the app shell only: `app_controller`, `paths` and
`settings_manager`. **A new module goes in a package, never beside them.**

| package | holds |
|---|---|
| `bot/` | the FSM: the machine, the bot file schema, ambient combat, the health monitor, variables |
| `nodes/` | the node library, grouped by kind |
| `game/` | read-only facts about the game: zones, worlds, spells, scales, stat caps, wiki lookups. `game/data/` holds the extracted snapshots, and every reader of them lives here |
| `combat/` | decks, the DSL vocabulary, strategy stores and summaries, Bot Combat Strategies, fight keys, which duel strategy a fight runs (`combat_resolution`) and the pre-run check. The shipped default ids and the last-resort DSL are core's `bartleby.shared.combat_defaults` |
| `content/` | the stores for user and Official content, and how a bot is made: store and bot roots, bot folders, tags, forks, New Bot, share export and import. `content/templates/` and `content/subgraphs/` hold those two |
| `clients/` | game clients at run time: the client manager, recovery and relog, porting, the run's pre-checks, the authoring tools |
| `options/` | bot-option resolution (`docs/BOT_OPTIONS.md`), Player Realms included |
| `packaging/` | the app as a product and a packaged build: BartleBot's `Product` for core (`product.py`), `--self-check`, whether this process is one, and Velopack's install hooks, which write the install key and the file types (`docs/RELEASING.md`, `docs/INSTALLER.md`) |
| `updates/` | the update check: the feed, its signature and the trusted keys, and staging an update for Velopack (`docs/INSTALLER.md`) |
| `gui/` | everything Qt. Dialogs in `dialogs/`, developer tracers in `debug/`, reusable widgets in `widgets/` by kind (`buttons/`, `labels/`, `inputs/`, `pickers/`, `pills/`, `cards/`, `layout/`), and one subpackage per surface (`canvas/`, `sidebar/`, `settings/`, `game_embed/`, `combat_strategies/`) |

What every Bartleby product shares is bartleby-core's, imported, never copied
(`tests/test_family_guards.py` fails on a copy); core's `docs/ARCHITECTURE.md` has what.

Logic a widget needs but that needs no Qt goes outside `gui/`, in the package its
subject belongs to. A module keeps its file name when it moves; a new package gets an
empty `__init__.py`.

**Tests mirror the tree:** the test for `bartlebot/a/b/c.py` is `tests/a/b/test_c.py`
(`tests/CLAUDE.md`).

## Where the rest lives

This file holds what governs code written anywhere in BartleBot, and core's root
`CLAUDE.md`, imported on this file's first line, what governs every Bartleby product. Most
of what follows is the rule alone; the reasoning behind it, and the bug it came from, is in
the file named beside it. The subtree files load on their own when a file in that subtree
is read or edited, so the detail arrives when it applies, and each imports its core
counterpart. The `docs/` ones are read on purpose.

| file | covers |
|---|---|
| `../bartleby-core/` | the family's rules (`CLAUDE.md`, `bartleby/qt/CLAUDE.md`, `bartleby/runtime/CLAUDE.md`, `tests/CLAUDE.md`) and the docs that govern shared code: `ARCHITECTURE`, `WIZARD101`, `UI_TEXT`, `WINDOW_SIZING`, `KEY_BINDINGS`, `WALKING`, `RELOGGING`, `PLAYER_REALMS`, `UPSTREAM`, `GAME_UPDATES` and `PROCESS`, under `docs/`; core's own table says what each covers |
| `bartlebot/gui/CLAUDE.md` | BartleBot's own GUI rules: the canvas, a definition as a document, the node and signature pages, which panel owns what, Source, the crumb bar, Option Values vs. Options |
| `bartlebot/gui/settings/CLAUDE.md` | `SettingsManager`, the settings dialog, the Bot tab |
| `bartlebot/bot/CLAUDE.md` | the machine: callback guards, `machine.log()` |
| `bartlebot/nodes/CLAUDE.md` | adding a node, and why nodes never write variables |
| `tests/CLAUDE.md` | what a change ships with, where a test goes, the doubles |
| `tools/CLAUDE.md` | type dumps and `Root.wad` — read these before writing a probe |
| `docs/BOT_OPTIONS.md` | the four-level resolution, params vs. options, who owns a value, which card an option is drawn in, quicksell's user keep list and when its warning is shown |
| `docs/COMBAT_CONFIG.md` | the five strategy levels and how to name them, the two storage roots, a Bot Combat Strategy's own strategies and the own-then-library lookup, the pick/assemble/author split, the dialog's ways in, and the Save/Cancel boundary |
| `docs/SUBGRAPHS.md` | reusable subgraphs: a definition as a node type (inputs, ports, panels), an instance that sets inputs and nothing else, derived member ids, follow-latest with snapshot fallback, the authoring verbs, the two roots, categories, and what a signature change warns about |
| `docs/EPICS.md` | the index of epic plans, each on a private claude.ai page: Subgraph Calls, Official and Shared Content (#173), Runtime Control Flow (#16), Combat Defaults (#201), Default Strategies (#218), First-Run Setup (#215), Installer and Updater (#27) and Shared Library Extraction (#167), each with its stage-to-issue table, its feature doc and the rules every stage kept; a complete page is frozen |
| `docs/SHARING.md` | share files: the format, the extension and what rides inside for each kind, what never travels (settings, anything that executes), Export: where it is on each item's menu, and the summary, and Import: its ways in, what is refused, matching by id, what Import as copy rewrites, and where things go |
| `docs/TEMPLATES.md` | the template store, template-owned ids and a fork's prefixes, release, user templates and File › Templates…, New Bot, setup steps and fill-ins, and the three-way update diff: units, changes, Apply, Finish review |
| `docs/RETURN_ROUTING.md` | the default failure edge, the explicit stop, why neither is drawn, and the three-mode failure-edge toggle |
| `docs/VARIABLES.md` | bot variables: options vs. variables, condition rows and `set_variables`, why only the run loop writes and a write never waits, fresh at run start and reset by the author, validation's errors and warnings, edge assignments (applied by the run loop when that edge is taken, never on a sibling edge or a recovery jump), authoring them in the editor (the Variables section, what its dialog refuses, the row editors, the labels on the canvas), reading a run (the live values in Run mode and how long a change stays marked, the condition named in the transition log, the final values in the summary), and when a subgraph is the better tool |
| `docs/FIRST_RUN.md` | setup as built: Quick or Custom, the walk, Next records and Back doesn't, closing resumes, first run vs. rerun, and each page with what it writes and when it is answered |
| `docs/AMBIENT_COMBAT.md` | a fight no node started: the game rules it rests on, cancelling and rerunning the node, who helps and how (same realm, or by port), holding the first cast, where everyone goes after, how a session ends, the flee limit |
| `docs/LEVEL_SCALING.md` | the one level scaling node, why it never cancels an elixir and warns at run start instead, the Scales of Knowledge table and how to add an entry, and Challenge Mode's stat caps: why potions read against the scaled max, and which level a dungeon scales to |
| `docs/RESTRICTED_ZONES.md` | the PvP policy and its three enforcement points |
| `docs/ZONE_NAVIGATION.md` | the zone navigation map: link kinds, list networks, doors and side worlds, and routes costed in seconds |
| `docs/RELEASING.md` | the packaged build: what is bundled and why, `--self-check` and each of its checks, the packager and why it won, what Velopack was shown to do, and `package.yml`; the one version number and how to bump it; `release.yml`'s three jobs, from a `v*` tag to a signed draft release, what `vpk pack` is told, and the pinned Velopack version; the release notes and the footer linking the install page; the download site (`site/`), its workflow, and publishing it the first time; testing a draft before it is published, in a Windows account `tools/release_test_user.ps1` recreates, and why `docs/RELEASE_CHECKLIST.md`'s steps are what they are; the signing key, where it is, its backup, and how to replace it |
| `docs/INSTALLER.md` | what an install puts where and what the app writes elsewhere, install, update and uninstall, the hooks and their time limits, why a checkout never calls Velopack, the install key, the file types and what uninstall hands on, one app per user, the update check (when it runs, the feed's signature and the trusted keys, the popup once per version and never over a dialog or a run, its three choices), and trying an install or an update from a local folder |

Landing a branch on `master` is the `merge` skill — the gate, the commit message
template, and the pre-merge read-through. Planning and carrying out a multi-stage epic
is the `epic-plan` skill — the plan page, an issue and a branch per stage, and the As
built record. Both are user-level skills, in `~/.claude/skills/`.

## Key Conventions

The family's conventions are core's, imported above. BartleBot's own:

### Bot files hold no player data

Bot files under `bots/` are the easy one to miss: a parameter holding a real
character name is *functional*, so redacting it by find-and-replace breaks the
bot. Move it out of the file instead, as `friend_name` was — see "When no
bot should ever store a value" in `docs/BOT_OPTIONS.md`.
`test_no_bot_stores_a_friend_name` keeps it out.

### Game art is extracted, never shipped

Art has no checked-in snapshot and never gets one — not the spell card art,
and not the school and archmastery icons beside it. The app extracts all of it on the
user's own machine, from their game install, with `bartlebot/game/art_extraction.py`: on
setup's last page, from Settings › General › Game art, or by `tools/extract_card_art.py`,
the command line over the same function. It writes into
`%APPDATA%/Bartleby/BartleBot/art` — outside the repo entirely, so an accidental `git
add` cannot reach it — and its tests use a made-up install (`tests/art_fixture.py`), never
the game's files. `bartlebot/game/card_art.py` is the read side. `tools/CLAUDE.md` has the
extraction details, core's `bartleby/qt/CLAUDE.md` the fallback rules.

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
BartleBot's install directory is the project root, holding the Official content: bots,
templates, subgraphs and strategies. A new store takes its default path from
`store_roots`, and `test_install_dir_writes` lists every default write path. The vendor's
shared files are written through core's `bartleby.shared`.

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
