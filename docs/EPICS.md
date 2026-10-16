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
  `bartlebot/shared/combat_defaults.py`. Everything that changes is in the player
  strategies they link.
- **`LAST_RESORT_DSL` is Default's text, word for word.**
- **Every shipped strategy that buffs enchants ahead first**, and the cheat-boss supports
  never fuse an enchant into the card their boss's cheat watches.
- **The fight kind is read by one rule**: `?(any(enemies).is_boss == 1)` for a boss fight,
  `?(all(enemies).is_boss == 0)` for a mob fight; never `boss.health` or `boss.hp`. P1's
  death is `ally(last).health == 0`, never `is_dead`.
- **`SHARED_SET_VERSION` rises with every change to a shared Official item.**
- **Nothing writes to a bot or a Bot Combat Strategy file at load or at run time.**
- **`bartlebot/shared/` imports nothing from the rest of `bartlebot`.**

## First-run setup

Epic #215: the first time BartleBot opens, it walks the user through everything a first
run needs, Quick or Custom, and the whole walk can be run again from the File menu.
https://claude.ai/artifact/DMYNo3NfYVLYPogsHACncx

**All seven stages landed by 2026-10-15; the page is kept current until the close-out.**

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

The rules every stage kept:

- **Every page writes a choice, and there is no Skip.** A page that only explains gets
  skipped; closing keeps what was answered and asks the rest next launch.
- **A page writes through the function its Settings row uses.** Setup and Settings never
  disagree about what is stored.
- **No page writes a bot file, a bot option's declaration or a user override.** Every
  page writes an app setting or a shared preference, the level a Settings row writes.
- **What both products need is written where both read it.** The game folder, accounts,
  launch profiles and the combat answer go in `preferences.json` and `accounts.json`,
  with their Qt-free code in `bartlebot/shared/`.
- **An answered page is never shown on a first run.** A user who answered the combat
  question in #209, or in BartleBuddy, is not asked it again.

## Installer and updater

Epic #27: a packaged build, a per-user Velopack install, a signed update feed, and the
first release. https://claude.ai/artifact/BNNKxZLiNZ4Pd9kcV8fdGd

**In progress; the page is kept current while stages land.**

| stage | issue | what |
|---|---|---|
| 1 | #235 | packaged build: the spike, `--self-check`, the packager chosen, Velopack's questions answered |
| 2 | #236 | installer and release build: one version, Velopack in the app, `release.yml` making a draft release |
| 3 | #237 | install key, file types and single instance |
| 4 | #238 | updater: the signed feed, the startup check, one popup per version |
| 5 | #239 | art appears at once: one `art_changed` signal, every art surface redraws |
| 6 | #240 | first release: the checklist, the install page, an update through GitHub |

`docs/RELEASING.md` is the build and the release as built.

The rules every stage keeps:

- **A checkout run never changes.** No Velopack, no registry writes and no update check
  outside an installed build.
- **A release is built by CI from a tag, never on a maintainer's machine,** and a
  maintainer publishes the draft CI makes.
- **Everything the app reads at run time is bundled, and `--self-check` proves it** on
  every build, before anything else is done with it.
- **Install, update and uninstall never touch `%APPDATA%\Bartleby`.**
- **No update is applied while a bot runs,** and one whose signature does not verify is
  never offered.
