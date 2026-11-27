# Epics

Each multi-stage epic was planned on one claude.ai page: the goals, what other tools were
checked, the lessons taken from them, every decision with the alternative it beat, and each
stage's "As built" deviations. This file is the index: one section per epic, with its page,
the stage-to-issue table, the feature doc that says how it works now, and the rules every
stage kept.

The pages are private: open one signed in as the maintainer, or, from a Claude Code
session, read it with the Artifact tool's `read` action. **A complete page is frozen.**
Read it when a design question comes up, but never update it: a later fix goes in the
feature doc. The `epic-plan` skill has how a new one is written.

## Subgraph calls

The successor epic to #37: subgraph calls, ports, the definition document, the call node,
and the library rewrite (#59). https://claude.ai/artifact/66pY2mPVrPYosopv1yaNNe

**Complete on 2026-10-06.**

| stage | what |
|---|---|
| 0–2 | the plan, one copy of a definition (`BARTLEBOT_DEV`), the node audit |
| 3 | param types: a `zone` type, widgets chosen by type |
| 4 | the signature: Entry/Exit port nodes, the port map, typed inputs, input and node panels, the held rule |
| 5 | the definition document: editing a definition as its own document; deletes edit-in-place |
| 6 | the call node: an instance drawn as one node, with its input form and Record |
| 7 | stepping in and out: one crumb bar, double-click in, Esc out |
| 8 | Player Sync, and the templates rebuilt on it |
| 9 | the library rewrite (#59) |
| 10 | template provenance |

`docs/SUBGRAPHS.md` is the feature as built, and `bartlebot/gui/CLAUDE.md` the editor's
part. `docs/TEMPLATES.md`'s "How the shipped templates are cut" keeps the decisions the
library rewrite took.

## Official and shared content

Epic #173: user templates (#164), Official content (#149), and sharing bots, strategies,
decks and subgraphs as files. https://claude.ai/artifact/4A9XUEeoTXQAfohUHP3F1n

**Complete: all seven stages landed.**

| stage | issue | what |
|---|---|---|
| 1 | #168 | storage roots: user bots and templates beside the shipped ones; duel strategies, player strategies and decks move to the vendor root |
| 2 | #149 | Official: the label, read-only Official bots, and Make my own copy |
| 3 | #169 | content hashes, histories, "Official updated", and the subgraph signature warning |
| 4 | #170 | share files: one format, an extension per kind, and Export |
| 5 | #171 | share files: Import, matched by id, defaulting to Import as copy |
| 6 | #164 | the Templates dialog, and user templates as forks |
| 7 | #172 | subgraph categories |

`docs/SHARING.md` is the share file's format, Export and Import (Stages 4 and 5).
`docs/TEMPLATES.md`'s "User templates" is File › Templates… and forks (Stage 6).
`docs/SUBGRAPHS.md`'s "Categories, and finding a subgraph" is Stage 7.

After it: templates in share files (#165), the release check that enforces "nothing
released is removed" (#166), and extracting `bartlebot/shared/` to a shared library repo
once BartleBuddy development starts (#167). The installer's part, including the file type
registrations, is in #27.

The rules every stage kept:

- **Official comes from where a file is, never from what it says.** It means tested and
  proven in game, and shipping is the gate.
- **Linked things are overridden; copied things are forked.** Subgraphs and strategies
  keep their same-id override; an Official template or bot is customized by forking it.
- **Official content arrives only with app updates, and nothing released is ever removed.**
- **A share file carries everything Custom and names everything Official.** Combat picks
  and option values live in settings and never travel.
- **Duel strategies, player strategies and decks are shared with BartleBuddy.** Their
  code is staged in `bartlebot/shared/`, which imports nothing from `bartlebot` outside
  itself.
- **Shared files are data only, permanently.**

## Runtime control flow

Epic #16: bot variables, named values the bot sets as it runs and reads back to choose
its next node. https://claude.ai/artifact/VnoJzdBGUjJw4a8gZG9U5M

**Complete: all five stages landed by 2026-10-08.**

| stage | issue | what |
|---|---|---|
| 1 | #186 | the engine: declarations, variable conditions on edges, `set_variables` and `branch`, validation, the template diff |
| 2 | #187 | authoring: the Variables section, condition and assignment rows, condition labels on the canvas, the badge |
| 3 | #188 | watching a run: live values in Run mode, the condition in the transition log, final values in the summary |
| 4 | #189 | edge assignments: rows applied when an edge is taken |
| 5 | #190 | Heretical Academy's sword puzzle rebuilt on variables, run in game |

`docs/VARIABLES.md` is the feature as built.

After it, each needing a plan of its own: subgraph-local variables with by-reference
ports (#194), and variables that feed node params (#195). The stages' leftovers are
#196 to #200.

The rules every stage kept:

- **Options are the user's; variables are the bot's.** An option is read-only during a
  run, and a variable never becomes a setting or a param.
- **Only the run loop writes, and a write never waits.** Watchers and a node's game code
  are refused.
- **Fresh when the bot starts; after that, only the bot resets a value.** Nothing a
  variable holds is ever written to disk.
- **Rows, never expressions.** Share files stay data only.
- **No zone ever comes from a variable**, so the restricted-zone check stays complete.

## Combat defaults

Epic #201: every fight runs the most tailored duel strategy available for the team on the
field, and a run stops to ask only when something the user chose is broken.
https://claude.ai/artifact/X6DUPQ7YrZwkjosGJnSbo4

**Complete: all seven stages landed by 2026-10-13, and the close-out (#211) on
2026-10-15.**

| stage | issue | what |
|---|---|---|
| 1 | #204 | one vocabulary: Bot Combat Strategy everywhere, "profile" left to launch profiles |
| 2 | #205 | shared preferences: `preferences.json`, the re-read-and-replace write, `AccountStore` on it |
| 3 | #206 | default duels: Default Support's fallback, a Default player strategy, three shipped duels |
| 4 | #207 | levels and tiers: the four-level resolution, faults and notices, the dialog for faults only |
| 5 | #208 | showing the source: the Combat tab banner, Settings › Combat |
| 6 | #209 | first run: one question about the team, answered once for both products |
| 7 | #210 | after a lost fight: a mark where the fight is drawn when it was fled on a default |

`docs/COMBAT_CONFIG.md` is the feature as built: "Naming" (Stage 1), "The shipped
defaults" (3), "Resolution at run time" and "Before a run: faults and notices" (4),
"Showing the source, and choosing the defaults" (5), "First run: one question about the
team" (6), and "After a lost fight" (7).

After it: Official Bot Combat Strategies (#213), and more first-run pages, which became
epic #215 (*First-run setup*, below).

The rules every stage kept:

- **Nothing writes to a bot or a Bot Combat Strategy file at load or at run time.** Every
  level is resolved when the fight starts, and every bot file round-trips byte-identical.
- **A fallback is shown, never asked about.** Only a broken choice the user made stops a
  run, and going back is the default.
- **The checks that tell rather than ask stay first and blocking**: restricted zones,
  Player Realms, variables, and players already in a fight.
- **Share files carry no preference and no pick**, and keep their formats.
- **`bartlebot/shared/` imports nothing from the rest of `bartlebot`.**
- **The dialog's Save/Cancel boundary is untouched**, and the Assemble tier shows no DSL.
- **"Profile" means a launch profile, and only that.**

## Default strategies

Epic #218: the shipped default strategies fight the way Wizard101 players do. One hitter
and the rest support on a boss, everyone hits in round 1 of a team mob fight, a solo
wizard shields, buffs and then hits, and a support never spends a buff meant for the
hitter. https://claude.ai/artifact/JNffqBsHsanEtdhhNXP3RK

**Complete: all six stages landed by 2026-10-15.**

| stage | issue | what |
|---|---|---|
| 1 | #219 | stacking check: the `stacks` card-template keyword in wizsprinter, and the pin that brings it |
| 2 | #220 | fight harness: a test helper that walks a DSL through one round of a described fight |
| 3 | #221 | supports: Default Support and the two cheat-boss supports rewritten to the support ladder |
| 4 | #222 | hitters: Default rewritten to the hitter ladder, and Default Team Hitter for the team duels |
| 5 | #223 | two duels go: Default Team Boss Duel and Default Turn 1 AoE Mob Duel leave the Official set |
| 6 | #224 | guardian and Attenuate: Default Support only buffs, Default Guardian Support shields and heals P1 first, every support casts a negative aura on the boss through wizsprinter's `harmful_aura` and `stronger`, and every hitter shields first |

`docs/COMBAT_CONFIG.md` is the feature as built: "The shipped defaults", "What a support
does", "What the guardian does" and "What the hitter does". wizsprinter documents the DSL
beside its parser, in `combat_backends/DSL.md` and `CONDITIONS.md`.

After it: playing the defaults in game (#212); smart combat, hitting when a hit kills
(#30, 2.0.0); and a Willcast that reads its card (#225, 2.0.0). Each of the last two needs
a plan of its own.

The rules every stage kept:

- **The duels keep their ids**, and BartleBuddy reads them from
  core's `bartleby/shared/combat_defaults.py`. Everything that changes is in the player
  strategies they link.
- **`LAST_RESORT_DSL` is Default's text, word for word.**
- **Every shipped strategy that buffs enchants ahead first**, and the cheat-boss supports
  never fuse an enchant into the card their boss's cheat watches.
- **The fight kind is read by one rule**: `?(any(enemies).is_boss == 1)` for a boss fight,
  `?(all(enemies).is_boss == 0)` for a mob fight; never `boss.health` or `boss.hp`. P1's
  death is `ally(last).health == 0`, never `is_dead`.
- **The shared set's version rises with every change to a shared Official item.** It is
  `strategies/official_set.json`'s since #237; it was `SHARED_SET_VERSION` then.
- **Nothing writes to a bot or a Bot Combat Strategy file at load or at run time.**
- **`bartlebot/shared/` imports nothing from the rest of `bartlebot`.**

## First-run setup

Epic #215: the first time BartleBot opens, it walks the user through everything a first
run needs, Quick or Custom, and the whole walk can be run again from the File menu.
https://claude.ai/artifact/DMYNo3NfYVLYPogsHACncx

**Complete: all seven stages landed by 2026-10-15.**

| stage | issue | what |
|---|---|---|
| 1 | #226 | the setup frame: Quick or Custom, no Skip, saved as you go, File › Set up BartleBot… |
| 2 | #227 | game folder: install detection with Steam, the page, a Settings row |
| 3 | #228 | accounts: the page, on the existing account editor |
| 4 | #229 | launch profile: one profile from a checklist |
| 5 | #230 | quicksell: Settings › Bot's rows, the keep list, the per-run explanation off |
| 6 | #231 | realm and Safety Delay: one page |
| 7 | #232 | game art and Custom: the extractor in the app, the Options page, the art page |

`docs/FIRST_RUN.md` is the feature as built: the pages, what each writes, and when each
counts as answered. `bartlebot/gui/CLAUDE.md` has the rules for writing a page.

BartleBuddy's setup, its one Later card, was done by #298 in #167, which moved the
shared pages to core. The stages' leftovers are #303, #320 and #321.

The rules every stage kept:

- **Every page writes a choice, and there is no Skip.** A page that only explains gets
  skipped; closing keeps what was answered and asks the rest next launch.
- **A page writes through the function its Settings row uses.** Setup and Settings never
  disagree about what is stored.
- **No page writes a bot file, a bot option's declaration or a user override.** Every
  page writes an app setting or a shared preference, the level a Settings row writes.
- **What both products need is written where both read it.** The game folder, accounts,
  launch profiles and the combat answer go in `preferences.json` and `accounts.json`,
  with their Qt-free code in `bartlebot/shared/` (core's `bartleby/shared/` since #167).
- **An answered page is never shown on a first run.** A user who answered the combat
  question in #209, or in BartleBuddy, is not asked it again.

## Installer and updater

Epic #27: a packaged build, a per-user Velopack install, a signed update feed, and the
first release. https://claude.ai/artifact/BNNKxZLiNZ4Pd9kcV8fdGd

**Complete: all seven stages landed by 2026-10-18.**

| stage | issue | what |
|---|---|---|
| 1 | #235 | packaged build: the spike, `--self-check`, the packager chosen, Velopack's questions answered |
| 2 | #236 | installer and release build: one version, Velopack in the app, `release.yml` making a draft release |
| 3 | #237 | install key, file types and single instance |
| 4 | #238 | updater: the signed feed, the startup check, one popup per version |
| 5 | #239 | art appears at once: one `art_changed` signal, every art surface redraws |
| 6 | #240 | first release: the release notes, the checklist, the repo public, the `release` environment, and the `v0.1.0` tag; in two parts, 6.1 and 6.2 |
| 7 | #248 | download site: a Zensical site with a landing page, the download button and the install page; landed between 6.1 and 6.2 |

`docs/RELEASING.md` is the build and the release as built: the self-check, `release.yml`,
the release notes, the download site, testing a draft and the signing key.
`docs/INSTALLER.md` is what an install does on the user's machine: the hooks, the install
key, the file types, single instance and the update check. `docs/RELEASE_CHECKLIST.md`
is the procedure each release follows.

After it, each needing a plan of its own: refusing a release that removes shipped content
(#166) and `.bbtemplate` share files (#165). The stages' leftover is #250, publishing
0.1.0 and the first update fetched from GitHub. Filed before the plan and left out of
it: #233 (one process per game client, now BartleWorks/bartleby-core#7) and #234 (wizwalker's cache under `Bartleby`).

The rules every stage kept:

- **A checkout run never changes.** No Velopack, no registry writes and no update check
  outside an installed build.
- **A release is built by CI from a tag, never on a maintainer's machine,** and a
  maintainer publishes the draft CI makes.
- **Everything the app reads at run time is bundled, and `--self-check` proves it** on
  every build, before anything else is done with it.
- **Install, update and uninstall never touch `%APPDATA%\Bartleby`.**
- **No update is applied while a bot runs,** and one whose signature does not verify is
  never offered.
- **The signing key is read by one job, in an environment only `v*` tags reach.** Whoever
  owns the org owns the key, and a branch or a pull request never sees it.

## Shared library extraction

Epic #167: BartleBot and BartleBuddy as two apps of one family, built from one shared
library, bartleby-core, which holds the shell, the widgets, the client runtime and the
rules learned building them. BartleBot moves onto core in the same stages that build it.
https://claude.ai/artifact/3K34iJqy6UV73EKuadNuky

**Complete: all 12 stages landed by 2026-10-30.**

| stage | issue | what |
|---|---|---|
| 1 | #288 | merge `walk-planner` into `master`, with r811339's game data held back until Ahmarra goes live |
| 2 | #289 | family knowledge: core's CLAUDE.md tree and docs, the user-level preferences and skills, the guards as core functions, render sheets |
| 3 | #290 | BartleBot onto core as it stands: core public on GitHub, the import codemod, BartleBot's copies deleted, the pin |
| 4 | #291 | the threading probe: one qasync loop, or the runtime on its own thread |
| 5 | #292 | the app skeleton and product identity: `Product`, logging and the excepthook, single instance, window state |
| 6 | #293 | cards, reordering, pills and the Log tab |
| 7 | #294 | the client manager's Qt-free half; `ClientPool` deleted |
| 8 | #295 | player cards on view models |
| 9 | #296 | the game view, Quick Launch and window sizing for two tile sizes |
| 10 | #297 | Bar A from a list of actions |
| 11 | #298 | settings and first run |
| 12 | #299 | the family check: both apps side by side, and BartleBuddy in game |

The feature docs are core's, from Stage 2: `bartleby-core/docs/ARCHITECTURE.md`, and the
docs that move there (`WINDOW_SIZING`, `KEY_BINDINGS`, `WALKING`, `GAME_UPDATES` and the
rest the page lists).

After it, each needing a plan of its own: BartleBot on the arbiter and core's helpers
(#301), live resize of a running client (BartleBuddy #3) and packaging BartleBuddy
(BartleBuddy #4). The stages' leftovers are #300 and #302 (now BartleWorks/bartleby-core#10 and #13), #303 and #304, and BartleBuddy #1 and
#2 from Stage 8.

The rules every stage kept:

- **Move, never rewrite.** A surface moves into core whole, with its docstring, tests and
  rules, and keeps its file name. What it needs from BartleBot becomes one named cut: a
  view model, a settings protocol, or the `Product`.
- **BartleBot's copy is deleted in the stage that moves it**, and a codemod rewrites the
  imports. No shim keeps an old path alive.
- **Each surface reaches both apps in its own stage**, and BartleBot's render sheet is
  pixel-identical before and after, unless the card names the change.
- **Core imports neither product**, and its tiers run one way: `bartleby.shared` ←
  `bartleby.runtime` ← `bartleby.qt`.
- **BartleBot pins core by commit, never by path**, and every merge leaves all three repos
  green. No stage migrates a bot file, a setting or a preference.
- **No real player data and no game assets** in any of the three repos; core is public
  from Stage 3.

## Live client resize

Epic BartleBuddy #3: a wizard who becomes Main gets a crisp, full-size game in seconds, and the
wizards the user only watches run small, each whole in its tile, with no client restart. Filed in
BartleBuddy, and listed here with the others.
https://claude.ai/artifact/7xXKV8yEcnjyEVFUQMo497

**Complete: all 5 stages landed by 2026-11-01.**

| stage | issue | what |
|---|---|---|
| 1 | BartleBuddy #5 | measure in game what the plan still assumes (`tools/probe_runtime_resize.py`) |
| 2 | BartleBuddy #6 | the forcer in wizwalker, the pending byte read from code, and the pin in all three |
| 3 | BartleBuddy #7 | tiles by rung: whole Side tiles, Main in what is left, and each tile's target |
| 4 | BartleBuddy #8 | the resizer: `ClientResizer`, the `UISize` node, the lease, and giving back |
| 5 | BartleBuddy #9 | Main changes hands: the window half and the wiring, in game |

Core's `docs/WINDOW_SIZING.md`, "Resizing a running client" and "Tiles by rung", is the feature as
built. BartleBot has no part of it yet, though it carries the code since its pin moved to core's
`v0.2.1`.

After it, each needing a plan of its own: resizing across aspects (BartleBuddy #10), and BartleBot
adopting it (#308). The stages' leftovers are BartleBuddy #11 to #13.

The rules every stage kept:

- **The 1:1 contract.** The backbuffer equals the client area after every resize, because the
  game maps clicks against it, and a window is never stretched to fit a tile.
- **The ladder.** Every size a client takes is in `VALID_RESOLUTIONS`, with the UI size
  `ui_size_for` gives it.
- **The arbiter and the crossing.** A memory write on a client needs a lease, and the GUI holds no
  wizwalker object.
- **BartleBot's answers are identical at every stage.** Its grid, window floor and launch fit do
  not change, and `tests/game/test_pane_sizing.py` holds them.
- **The user's own settings** are captured once and given back, as for a launch.
- **A client that can't be resized behaves as it does today**, and a session with no Side tile
  never resizes, unless a game is too large for its tile beside another, which the user chose in
  Stage 5. One account never does.

## Multibox roles and views

Epic BartleBuddy #14: a player swaps wizards with one click or one key, and the large tile, the
keyboard and the team all follow, from a Role per slot and the Active wizard. Filed in
BartleBuddy, and listed here with the others. https://claude.ai/artifact/MBsMDvJPgGM2aLcRV2PSQt

**Complete: all 8 stages landed by 2026-11-06.**

| stage | issue | what |
|---|---|---|
| 1 | BartleBuddy #15 | measure in game what the design assumes (`tools/probe_multibox.py`) |
| 2 | BartleBuddy #16 | Roles, the Active wizard and the leader |
| 3 | BartleBuddy #17 | the team's behaviors: Speed, Stop following, ports from far away |
| 4 | BartleBuddy #18 | two views, One large and All equal |
| 5 | BartleBuddy #19 | swapping and focus: fast swaps, the first click, the pause and detached windows |
| 6 | BartleBuddy #20 | hotkeys: keys, defaults and clashes, the game's chat and detached windows |
| 7 | BartleBuddy #23 | a smooth swap: the ring in each resized tile until its game is in |
| 8 | BartleBuddy #21 | in game on both setups, and the last of the old words (`tests/test_terms.py`) |

BartleBuddy's `docs/MULTIBOX.md` is the feature as built, and core's `docs/WINDOW_SIZING.md` the
resizes under it. BartleBot has no part of it, though it carries the code since its pin moved to
core's `v0.2.1`.

After it, each needing a plan of its own: copying interactions (BartleBuddy #1, #2), combat
(BartleBot #276), separate instances, scouting and auto-questing (BartleBuddy #26), resizing across aspects
(BartleBuddy #10), and BartleBot adopting resize (BartleBot #308). The stages' leftovers are BartleBuddy #13
(flaky tests), #22 (a resize taken back behind a secure screen), #24 (a white frame as a swap's
rings come off) and #25 (a swallowed cancel in six more places).

The rules every stage kept:

- **The 1:1 contract.** The backbuffer equals the client area after every resize, because the
  game maps clicks against it, and a window is never stretched to fit a tile.
- **The ladder.** Every size a client takes is in `VALID_RESOLUTIONS`, with the UI size
  `ui_size_for` gives it.
- **The arbiter and the crossing.** Every press and memory write goes through a lease, and the
  GUI holds no wizwalker object.
- **BartleBot's answers.** Its equal grid, window floor and launch fit don't change, and its suite
  stays green against core's merged tree.
- **One account** never resizes, never scans and shows no team controls.
- **The 2.0x cap** on the speed field, and the user's own game settings, captured once and given
  back.
- **Core merges first**, and every in-game check runs the core case, one or two accounts on 1080p,
  before four on 4K. A quit is checked from the terminal, never under the debugger.

## User documentation

Epic #25: the site at `bartleworks.github.io/BartleBot` grows from a download page into
the manual, with Getting started, guides for running and making bots, a reference generated
from the node specs, Troubleshooting, and F1 and a `?` in the app that open the right page.
https://claude.ai/artifact/MztJCi7kb275XQwDChrQaP

**In progress. The page is kept current while the stages land.**

| stage | issue | what | status |
|---|---|---|---|
| 1 | #312 | structure: the four-section nav, edit links, `site/CLAUDE.md`, the same-branch rule, Getting started and Troubleshooting | landed |
| 2 | #313 | the reference, rendered from `NODE_SPECS`, the node-declared options and the Official subgraphs, committed and checked current | landed |
| 3 | #314 | guides for running bots, Safety and PvP among them, and Troubleshooting's entries for them | landed |
| 4 | #315 | guides for making bots, and the first bot built across them | landed |
| 5 | #316 | help from the app: `HELP_TOPICS`, F1, a `?` on each dialog, and the Help menu | landed |
| 6 | #317 | the combat guide, after #213 | planned |

`site/CLAUDE.md` holds the site's rules as built: what goes on the site, in the app and in
`docs/`, the four kinds of page, the voice on top of core's `docs/UI_TEXT.md`, links,
screenshots and generated pages. `docs/RELEASING.md`'s "The download site" covers the build
and the publish. BartleBuddy's docs are left out until it ships, in a plan of their own.

The rules every stage keeps:

- **A page changes in the same branch as the behavior it describes**, the root `CLAUDE.md`'s
  Key Conventions. A docs pass before each release lets the drift pile up.
- **Every page is checked against the app**, at the stage's branch point, and the As built
  names which flows were walked in the live app and which were read from code only.
- **The reference is the spec's own words.** A gap is fixed in the `NodeSpec`, so the app
  gains it too, and no page adds text of its own per node.
- **`install.md` stays plain markdown under its name**, since it is every release's notes.
- **A visit sends nothing to anyone but GitHub**, and no page or screenshot carries player
  data or game art.

## Walking routes

Epic #322: every wizard moved by walking, along routes planned from each zone's own files, so
no node writes a long teleport: every move is walked, taken through the game's own links, or
an author's Teleport to XYZ across a gate, never longer than `HOP_MAX`.
https://claude.ai/artifact/Ged4jK6Q1erSRjQ4uearvQ

**In progress. The page is kept current while the stages land.** Shelved on 2026-11-16, when
long written teleports held again on Ahmarra's live build, and taken up again on 2026-11-24 by
choice. Core's stages land in bartleby-core, and BartleBot pins each core release Stages 1 and 2 cut, and 0.12.0, the fixes from
reviewing them and walking the walker in game. 0.12.1 carries Stage 3.1's gates, pinned with
the r815378 update's wizwalker, 0.13.0 Stage 3.2's gate read, pinned by Stage 4, 0.14.0 a walk
into a fight and the floor outside an exit, pinned by Stage 5, and 0.14.1 the maps of every Scales of
Knowledge, pinned by Stage 6. **No BartleBot
release is cut until Stages 7 and 8 land** (the user's call, 2026-11-25): Stage 4's walks can fail
in the bots `tools/check_bot_routes.py` lists, where a gate or a placed floor is in the way.
**Since Stage 7.1, `walking.hop` is the one position write**, an author's Teleport to XYZ past a
closed gate, never farther than `HOP_MAX` (350, the user's call of 2026-11-26) from where the
wizard stands; Teleport to Coordinates is gone, and a file naming it refuses to load. **No hop
runs in game before #327** (the user's call, the same day).

| stage | issue | what | status |
|---|---|---|---|
| 0 | #336 | speed boosts off in both apps, behind `SPEED_TOGGLE_ENABLED` | landed |
| 1 | core #14 | route planning (1.1), and the shipped zone maps (1.2) | landed |
| 2 | core #15 | walking a route in core | landed |
| 3 | core #16 | doors in the maps: gates open until found closed (3.1), and gates read from the client (3.2) | landed |
| 4 | #323 | the run's helpers walk | landed |
| 5 | #324 | fights and pickups by walking | landed |
| 6 | #325 | the other movers walk | landed |
| 7 | #326 | Walk to XYZ and Teleport to XYZ, `walking.hop` and `HOP_MAX` (7.1), and hops where a gate is found closed (7.2) | Landed |
| 8 | core #17 | verified links | |
| 9 | #327 | the bot library on live | |

Core's `docs/WALKING.md` is the feature as built; the game's rules of movement are core's
`docs/WIZARD101.md`, *Moving, and what the server allows*. The probes (`probe_walk_route.py`,
`probe_walk_input.py`, `probe_walk_zones.py`, `probe_mob_paths.py`) plan with core's planner, on
zones read by core's wad reader (its `tools/zone_files.py`, from the checkout beside this one), and
`probe_walk_input.py` walks on core's walker (`movement/walk.py`'s `RouteWalker`).
`probe_gates.py` reads a zone's gates with the same reader and matches each against the
client's entity list, for Stage 3.2's session in game. `check_bot_routes.py` plans every walk the
shipped bots make, and lists the ones with no route, a gate as the only way, or over 60 s: Stage 7's
list of where a Teleport to XYZ goes. A run plans on the zone maps core ships, built by its `tools/build_zone_maps.py` from
`zone_nav.json`, `bots/` and `subgraphs/`; `test_every_zone_on_the_map_has_a_walking_map_core_ships`
fails on a `zone_nav.json` zone without one.

The rules every stage keeps:

- **No teleport is ever longer than `HOP_MAX`**, measured from where the wizard stands, and
  nothing but `walking.hop` writes a position. `HOP_MAX` has no setting, option or override.
- **A failed move says where, and never teleports instead.**
- **Legitimate means only**: the movement keys, yaw writes, the game's own teleporters and
  links, the World Gate, porting to a friend, Mark and Recall. Nothing boosts speed until the
  user's go-ahead.
- **Every bot file round-trips byte-identical**, but for Stage 7's one rewrite of
  `teleport_to_coordinates`.
- **The restricted-zone check stays complete**, and core imports neither product.
- **No game file is committed**: tests build their zones in code, and what ships is derived.
- **Every input goes through an arbiter lease**, and no log names a wizard or an account.

## Auto-combat

Epic BartleBuddy #31: the team fights its own fights, with a duel for mob fights and one for
boss fights, and a deck set that each wizard equips by name before a boss. BartleBot's fight
runner, strategy layer and Combat Strategies window move to core, so both apps fight with one
system. Filed in BartleBuddy, with the family stages' issues here and in core.
https://claude.ai/artifact/DiNH2NhDqp2jHS5GWv2atw

**In progress. The page is kept current while the stages land.**

| stage | issue | what | status |
|---|---|---|---|
| 1 | BartleBuddy #32 | measure in game what the plan assumes (BartleBuddy's `tools/probe_combat_facts.py`) | landed |
| 2 | #330 | the fight runner moves to core | landed |
| 3 | BartleBuddy #33 | Auto-combat in BartleBuddy | landed |
| 4 | #331 | the team's order told to wizsprinter, and the followers joining in it | landed |
| 5 | #332 | the strategy layer moves to core | landed |
| 6 | #333 | the Combat Strategies window moves to core | landed |
| 7 | #334 | mob and boss duels, and the duel card in core | landed |
| 8 | core #22 | equipping a deck by its name | landed |
| 9 | BartleBuddy #34 | deck sets, and Boss decks | landed |

BartleBuddy's `docs/COMBAT.md`, from Stage 3, is the feature as built. The game's rules it rests
on are core's `docs/WIZARD101.md`, *Decks and the backpack* and *Seats and casting order*.

The rules every stage keeps:

- **Every shared strategy file round-trips byte-identical.** A new field is left out while
  empty, so either app loads what the other wrote.
- **BartleBot resolves each fight exactly as now**, and its tests pass with only their imports
  changed by a move.
- **Stop all ends every combat lease at once**, and a wizard the player is playing is never cast
  for.
- **Nothing opens a backpack or the deck page in a fight**, and nothing equips a deck the player
  didn't ask for.
- **Logs name players by number**, and BartleBuddy's settings take no migration while it is
  unreleased.

## Wisps and ports

Epic #340: a group moves by whichever is quicker, walking or porting to one of its own, and
Collect Wisps spends its time on the player who needs a wisp most, by the seconds each wisp's
route takes rather than its straight-line distance. Drafted from the Captain Nemo bot's runs
of 2026-11-27 (#339).
https://claude.ai/artifact/TZD7vHp168TRUbY8UTwrgy

**In progress. The page is kept current while the stages land.** Core's stages land in
bartleby-core, and the BartleBot stage that uses each pins that release's tag. No stage runs
a port or a walk in game before the user's go-ahead for that stage.

| stage | issue | what | status |
|---|---|---|---|
| 1 | core #38 | costing routes: a walk that stops before its first step when its plan is too long, the plan kept, and one search for several goals (1.1); room from walls where there is room (1.2) | landed, core v0.15.5 |
| 2 | #341 | Collect Wisps by route time, neediest first | landed |
| 3 | #342 | a long walk to one of ours becomes a port | landed |
| 4 | #343 | how wisps move: a probe, in game | |
| 5 | core #39 | walking to a moving wisp | |
| 6 | #344 | full, by the game's word, and a timeout that waits for progress | |

Core's `docs/WALKING.md` is the feature as built: *Routes* for `seconds_to`, *Walking a route*
for a plan too long and the plan kept, and *A bot run* for what BartleBot does with them. The
game's rules it rests on are core's `docs/WIZARD101.md`, *Moving, and what the server allows*:
a port within a zone, and the zones that refuse one.

The rules every stage keeps:

- **Old bots load and run unchanged**, and every bot file round-trips byte-identical. No param
  is added, renamed or removed; a saved timeout keeps working.
- **No teleport is ever longer than `HOP_MAX`**, and nothing but `walking.hop` writes a
  position. A port is the game's own move, made by clicking, and writes nothing.
- **A walk into a fight, or into a zone exit, is always walked.**
- **Every input goes through an arbiter lease**; a port is made under the mover's movement
  lease, as a walk is.
- **Logs name players by number**, never by wizard name.
- **The restricted-zone check stays complete**, and core imports neither product.
- **No game file is committed**: tests build their zones in code.
