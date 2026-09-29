# Combat configuration

Where a combat strategy lives, who owns it, and which of the three things a user might be doing a surface is for. The surfaces themselves — the Combat tab in Run mode, and the dialog behind it — are documented with the rest of the sidebar in `bartlebot/gui/CLAUDE.md`.

Combat is the one part of a bot that *must* vary per user, because it depends on the wizards they actually own. Everything below follows from that: a shared bot has to stay byte-identical however its users tune their combat, and the average user cannot write the WizSprinter DSL and must never be asked to.

## The three tiers, and which surface is for which

| tier | the act | who | writes DSL? | surface |
|---|---|---|---|---|
| **Pick** | choose a configuration that fits my roster | everyone | no | Combat tab, in Run mode |
| **Assemble** | "P1 hits, P2 blades, P3 heals" — choose Player Strategies, set combat order, match assignments to my wizards | most users | **no** | `CombatStrategyDialog`, default state |
| **Author** | write DSL, create new library entries | bot authors | yes | `CombatStrategyDialog`, one level deeper |

The middle tier is the largest, and it is user-owned work. Putting it behind `BotMode.EDIT` would gate a user-owned edit behind an author-owned mode and force a user into graph editing to answer a question about their own wizards. So combat editing has its own edit surface, and the tab that opens it stays a Run-mode surface.

**Assemble must show no DSL.** An assignment picks a Player Strategy from the menu behind its card's search verb, and `(built-in default)` is a real, named choice rather than an empty field. Author is one level deeper, behind `Custom DSL…` — never a monospace box in front of a user who will never type into one. Any change that surfaces DSL by default has collapsed the two tiers back together.

That is also what the assignment card's fold is for. The school, the note and the DSL box live behind the chevron at the right edge of its header, so what an assignment shows at rest is the two things a reader is scanning for — which players, and what they do. The one exception is a DSL that does not parse: the card wears the `⚠` mark and the error colour on its title *while folded*, because everything else the fold hides is optional and that is not. The pencil is a different verb: on a card that links a Player Strategy it switches to the Player tab with that strategy selected, and on any other card it is withdrawn (#112).

## Five levels, and what each one is a fact about

```
BotCombatStrategy      per bot, per user      which fights use which duel strategy, and the per-player decks
  StateAssignment      one entry              these FSM states use this duel strategy
DuelCombatStrategy     library, shareable     one fight: combat order, and who does what
  PlayerAssignment     one row                these player slots run this strategy
PlayerCombatStrategy   library, shareable     one wizard's job, as DSL
DeckPreset             library, personal      a deck to build before the fight
```

`DuelCombatStrategy` **is-a** `DuelConfig`: the same shape, plus an id and a name. A `StateAssignment` and the profile's `default_strategy_id` only ever *link* one, by id, and an unlinked one falls back a level. There is no inline duel config. `StateAssignment.duel_config` and `BotCombatStrategy.default_duel_config` were removed in #118 rather than migrated, because none existed and the editor for them was already gone. The models do not forbid extra fields, so a stray old file still loads, with those fields ignored.

The link/inline pair exists at the player level only: a `PlayerAssignment` carries either `player_combat_strategy_id` or its own `dsl`, which is the assignment card's `Custom DSL…`. **Linked wins, and an inline DSL is not a fallback for a broken link** — a link that cannot be resolved is a warning, not a silent reversion to whatever was typed before the link was made.

`DeckData.tc` is legacy and is never read or applied. Treasure cards are deliberately absent from the deck editor; do not add them back without deciding what applying them would mean.

**A deck's cards are captured, its archmastery answer is chosen.** Everything in a `DeckPreset` comes off the wizard at import time — the cards, the card kinds, the archmastery *school* — and none of it is typeable, so the Deck Presets page shows all of it read-only. The exception is `apply_archmastery`, the three-state "should applying this deck set that school", which is a decision and belongs to whoever owns the deck. It is editable in two places, on purpose: the sidebar deck card's menu, for the deck in a player slot, and the Deck Presets page, for one in the library — a preset whose only route to that answer is *load it into a slot first* is a preset that cannot be configured where it lives. Both spell the three modes from `deck_content.ARCHMASTERY_MODES`, and neither resolves the value on load; `resolve_apply_archmastery` runs at apply time, and at display time for the icons.

## Two roots, and why writes only ever go to one

`strategy_store` resolves against an **ordered pair** of roots:

| root | from | read | written |
|---|---|---|---|
| shipped | `project_root()` — the install directory | yes | **never** |
| user | `%APPDATA%/Bartleby/BartleBot`, via `bartlebot.shared.vendor.product_dir` | yes, and wins | always |

Every read merges the two with the user copy winning; every write and every delete touches only the user root. A user editing a shipped strategy therefore produces an **override**, not a fork: same id, user copy wins, shipped file untouched and still updatable. Deleting the user copy of a shipped id is not a delete at all — it is *revert to default*, and `Origin` / `origin_of()` exist so a surface can tell the two apart and say the honest word on the button.

The dialog asks a second question on top of that one: not *where is the file*, but *what will Save leave behind*. That is `StrategyWorkingSet.status()`, which compares an entry's content with the shipped version the set loaded (`list_shipped`), so it counts unsaved edits (#124):

- **Built-in**: the shipped content, unedited. Its Delete is disabled. Removing only touches the user root, so a "deleted" built-in came back on the next open, while the confirmation had promised otherwise.
- **Built-in (modified)**: differs from the shipped version, saved or not. The button is *Revert to default*. `revert()` puts the shipped version back in the working set and the entry stays listed and selected, with the same id, so everything linking it keeps working. Save then deletes the user copy.
- **Custom**: not shipped. An ordinary Delete.

Save deletes the user copy of any built-in whose content is back to the shipped version, whether through Revert or by hand-editing it back, because an identical copy would still shadow every later update. Every card wears its source as a pill, with labels from `store_roots.SOURCE_LABELS`, the one map the subgraph library uses too. In developer mode the two roots are one directory, so nothing reads as built-in and Delete really deletes, which is what authoring shipped strategies needs.

Shipped strategies are authored with `BARTLEBOT_DEV` set (`bartlebot/store_roots.py`), which makes the project root the user root too, so a save lands in the repo. Without it, editing a shipped strategy produces an override that the app keeps running while the tracked file quietly goes stale.

This is what makes a shared bot shareable. `SettingsManager.get_active_strategy(bot_id)` stores *which* strategy a bot uses, per user, in `settings.json`; the bot file never records it. The pick was always user-owned; #2 made the content user-owned too.

Before this, `project_root()` was the only root, and the Combat tab autosaved into it — so a user whose roster did not match the author's edited tracked repo files that an update would overwrite, and could not share the bot without shipping their roster choices with it.

## Filenames are names, ids are inside

A file is named after its item — `strategies/duel/default-team-boss-duel.json`, not `{uuid}.json` (#99) — and every lookup by id goes through `store_roots.index_dir`, which reads the id each file declares. Everything that refers to a strategy or a subgraph does so by id, so the filename is for people browsing the directory and for git, and nothing else.

What that costs, and where each cost is paid:

- **A rename moves the file.** `save_by_id` writes the new name *before* deleting the old one, so an interrupted save leaves two files with one id rather than none. `index_dir` resolves a duplicate to the newer file and logs a warning; the next save or delete removes the extra one.
- **A hand-copied file keeps its source's id**, and one silently hides the other. `test_no_two_shipped_files_declare_one_id` catches it in the repo; on a user's machine the warning is all there is. Give a copy a fresh uuid4.
- **Shadowing is by id, not filename.** A user copy of a shipped strategy is named after the *user's* name for it, so the two roots can hold one item under different filenames. `origin_in`, load and revert all look it up by id.
- **Collisions are numbered**, `support-2.json`. The slug is lowercase, so names differing only in case are numbered apart rather than overwrite each other on Windows. A file keeps its number when the plain name frees up, so a re-save does not churn git.
- **A file from before #99**, named `{uuid}.json`, still loads, and is renamed on its next save. The shipped ones were renamed in one commit; `test_every_shipped_file_is_named_after_its_item` keeps them that way.

## The Save/Cancel boundary

`StrategyWorkingSet` holds deep copies of the library. Every editor mutates a copy; nothing reaches disk until `commit()`. `CombatStrategyDialog` is a view of one working set, and **nothing below the dialog decides when a file is written**.

Three rules hold this together:

- **Dirtiness is derived, never reported.** An entry is modified when its fields differ from the snapshot taken at load. Nothing calls a `mark_dirty()`, because there isn't one. That means an editor that mutates its model directly participates with no changes at all, and an edit typed and then typed back out leaves the dialog correctly clean — which a flag set from a `changed` signal cannot say. The snapshot uses a full `model_dump`, not the `exclude_defaults=True` form `save()` writes: a field set back to its default is not a change to the *file*, but it is a change the *user* made, and losing it on Cancel without warning would be a lie.
- **One rule at the edge, in both modes.** Cancel discards every edit; Save and Use both commit every edit, and Use additionally returns the selection. The detail pane is editable in both modes, which is what makes one window enough — noticing a strategy needs a tweak while picking it does not mean closing this and opening something else. Two rules would mean explaining which mode you were in.
- **A write that fails keeps the dialog open.** `commit()` logs and skips a strategy it could not write; closing anyway would discard the user's work while showing them a successful Save.

This is what retired the old 500 ms debounce, which made three things impossible at once: Cancel meant nothing, a half-typed DSL was persisted, and a partial flush could commit some fields of an edit and drop others. It also retired `AppController._flush_pending_edits`, which existed only because a quit could beat the debounce. **Do not reintroduce a write on a timer at any level below the dialog** — a better debounce is not the fix; the boundary is.

`tests/test_strategy_editor_identity.py` is gone for the same reason. Its premise was that a debounced save must carry the id of the strategy it belongs to, so a late flush cannot land on whatever the user selected in the meantime. There is no longer a window across which the selection can move.

## One window, and what it does not need

`StrategyLibraryWindow` and `StrategyPickerDialog` used to do largely the same job with different vocabularies. There is now one window in two modes: `MANAGE` (`Cancel` / `Save`, from **File → Combat Strategies…**) and `PICK` (`Use built-in default` / `Cancel` / `Use this`, from a `Change…` button).

It is **master-detail, not a popup, and not both**. `PickerPopup` is 400×420 and right for a one-line payload — a tag, a fight name. A duel strategy's payload is seven to nine mostly-long lines: a description, a combat order, and one row per assignment. Eliding those to fit a popup hides exactly what the user opened it to compare. So the left is the compact `StrategyCard` list with a filter, the right is the full entry unelided, plus what uses it and whether removing it deletes or reverts it. `_AddOptionDialog` is the precedent.

Two things fall out of the tabs being pages of one working set:

- **The assignment menu reads its options from the working set**, so a Player Strategy created on the sibling tab is selectable immediately. That is what removed the need for a picker opened on top of a picker.
- **Promotion is the dialog's job, not the card's.** `Save as Player Strategy…` emits a request; the dialog names it, creates it in the working set, and links the card back to it. A card that wrote its own file would be writing outside the boundary.

**Name, description, usage and origin belong to the page, not to the editor under it.** All three tabs draw them in one shared `AboutSection`. They were drawn three different ways before — label-above on the Player tab, label-beside on the Deck tab, and split across two widgets on the Duel tab, where the name belonged to a wrapper pane and the description to the `DuelConfig` beneath it. They identify the *library entry*, which is a thing all three kinds have; what varies between tabs is what sits below.

`BotStrategyPicker` is the one picker left, and it picks the one kind the library does not own: a `BotCombatStrategy` is bot-scoped and edited on the Combat tab itself. `StrategyWorkingSet` holds bot strategies **read-only**, and only to answer *who references what* for a usage count and a delete confirmation.

Filtering must never disturb the editor beside it. `QListWidget.clear()` emits `currentItemChanged`, and repopulating emits it again — one keystroke in a filter box used to reload the editor and send the DSL caret back to 0. Reselect *inside* the blocked-signals region, and detach the editor only when the entry is actually gone from the working set, never when it is merely filtered out of view.

**Tags, filters and favorites (#125).** A bar across the top of each tab carries facet pickers, the search box and, below them, the selected values as removable chips — the bot library's shape. The vocabulary is `bartlebot/strategy_tags.py`, laid out like `bot_tags.py`: tags OR within a group and AND across groups.

- **Source** (Built-in / Built-in (modified) / Custom) is derived from `StrategyWorkingSet.status`, never stored, and its words are the source pills'. **Role** (Hitter, Support, Healer) is stored, in `tags`, on Player Strategies and Deck Presets only. A duel is a whole team, so it has no role.
- **Works with school X** (Duel and Player tabs): a Player Strategy whose `schools` is empty or contains X, or a duel with a player slot that accepts X. A slot no assignment covers falls back to the built-in default, so it accepts every school. Several schools OR.
- **Team size N** (Duel tab): duels whose `min_players`–`max_players` includes N. It also limits the school check to slots 1..N.
- **Favorites** are per user, in `settings.json` under `favorite_strategies`, and saved on the click. They sit outside the Save/Cancel boundary, because a favorite is a preference about the library, not a change to it. A favorite moves its card under a "★ Favorites" heading. Every entry is in exactly one section, because selection is restored by id. The headings appear only once something is a favorite, and a heading whose section the filter empties hides. The star on a favorite always shows; on any other card it shows only on hover or selection.
- Nothing about the filter persists. Every opening starts unfiltered, since a remembered filter hiding entries is a "where did my strategy go" that a Pick-mode visitor cannot diagnose.

**A school is a set.** `PlayerCombatStrategy.schools` and `PlayerAssignment.schools` are lists, and empty means any school. A file with the old single `school` loads as a one-item list and is rewritten on its next save. An assignment's own non-empty set wins over its linked preset's. Deck Presets carry no school, because most spells can be trained on any wizard.

## Resolution at run time

`resolve_combat()` in `bartlebot/nodes/combat_runner.py` returns `(per_client_dsl, combat_order, source_label)` and falls back to `BARTLEBOT_DEFAULT_COMBAT_CONFIG` at **every** level:

1. `machine.bot_config.id` → `SettingsManager.get_active_strategy(bot_id)` → load the `BotCombatStrategy`.
2. Either the BCS default (`use_default_strategy`) or the `StateAssignment` covering `machine.state`, falling back to the default when no assignment covers it. An unlinked assignment counts as no assignment at all.
3. The `DuelCombatStrategy` that target links, or the built-in default when it links none.
4. Per 1-based player slot, the matching `PlayerAssignment`'s inline DSL, or the linked Player Strategy's DSL.
5. Anything unresolved gets the built-in default.

Assignments key on state ids, so a state whose id changes would lose its assignment
silently. The one place the app renames states — turning a region into a subgraph
instance (`docs/SUBGRAPHS.md`) — re-keys every strategy of that bot through
`strategy_store.rekey_bot_strategy_states` in the same step.

`source_label` says which level answered, in the user's vocabulary — `duel strategy 'Frenzy AoE' (via state assignment for 'fight_1')`. It is the older precedent for the logging `BotMachine.get_option` does; see `docs/BOT_OPTIONS.md`. **Never log the strategy's contents in place of the label**, and never log a wizard name.

## Layers, and what stays Qt-free

| module | holds |
|---|---|
| `bot/strategy_models.py` | the five levels, as Pydantic models |
| `strategy_store.py` | the two roots, load/save/delete per kind, `Origin`, `unique_name` |
| `strategy_workset.py` | the Save/Cancel boundary |
| `strategy_summary.py` | every display string, derived once |
| `combat_grouping.py`, `combat_warnings.py` | how a list is grouped, and what is wrong with a configuration |

All of these are Qt-free and need no `QApplication`. That is deliberate and worth keeping: the boundary, the precedence, the filter rule and the warnings are all testable without a widget, which is why the Qt tests over them are small enough to stay fast.

**Every display string comes from `strategy_summary`.** A surface that formats a player list or a health badge its own way is how the formatting drifted last time; `format_players`, `summarize_duel`, `summarize_player`, `summarize_bot` and `summarize_deck` are the whole vocabulary.

## Two things this is built to survive

- **School is not the organizing axis.** School-agnostic DSL (#6) collapses "Storm AoE" and "Fire AoE" into one strategy, which demotes school from a primary axis to an occasional annotation and promotes player count and role. Anything built on school as its *primary* axis — school colours, school chips, a school filter row, a school requirement as a headline facet — is building on an axis that is going away. A previous GUI pass was discarded largely for this. The "works with school" filter (#125) is on the right side of that line. It answers a *compatibility* question ("what can my Storm wizard play"), where an entry that names no school matches every school, and it is one facet among several. It does not organise the list: nothing is grouped, coloured or sorted by school.
- **The tab keeps the run-time truth.** Which strategy is active, which fights it covers, per-client deck state — that is Run-mode information and must not move behind a dialog. The tab is summary, pick, and one button in.
