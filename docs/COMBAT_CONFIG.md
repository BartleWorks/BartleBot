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

That is also what the assignment card's fold is for. The school, the note and the DSL box live behind the chevron at the right edge of its header, so what an assignment shows at rest is the two things a reader is scanning for — which players, and what they do. The one exception is a DSL that does not parse: the card wears the `⚠` mark and the error color on its title *while folded*, because everything else the fold hides is optional and that is not. The pencil is a different verb: on a card that links a Player Strategy it switches to the Player tab with that strategy selected, and on any other card it is withdrawn (#112).

## Five levels, and what each one is a fact about

```
BotCombatStrategy      per bot, per user      which fights use which duel strategy,
                                              the per-player decks, and strategies of its own
  StateAssignment      one entry              these fights use this duel strategy
    FightRef           one fight              the state it was on, and the enemy it named
DuelCombatStrategy     library or this bot    one fight: combat order, and who does what
  PlayerAssignment     one row                these player slots run this strategy
PlayerCombatStrategy   library or this bot    one wizard's job, as DSL
DeckPreset             library, personal      a deck to build before the fight
```

### Naming (#204)

The three levels a user sees are **Bot Combat Strategy**, **Duel Combat Strategy** and **Player Combat Strategy**, and "profile" means a launch profile, and only that. Calling a Bot Combat Strategy a "profile" gave one noun two meanings and left the code speaking a second vocabulary to the UI's; both are gone, and `tests/test_vocabulary.py` keeps the word out of the combat modules and docs.

The writing rule, for every surface:

- **The full name appears once per surface**: in a banner, a menu item or a section heading.
- **After that, a surface uses the level's word alone** (Duel, Player) or the item's name. "In 'Solo' only", under a heading that already says which Bot Combat Strategy, not "In the Bot Combat Strategy 'Solo' only".
- **Two full names never share a sentence**, and there is no third form, such as "bot strategy".
- **Strategies owned by one Bot Combat Strategy are This bot's.** Their pill and their Source filter value both say **This bot**, from one constant, `strategy_tags.THIS_BOT_SOURCE`.

In code, a `BotCombatStrategy` is a `bot_strategy` (or `bcs`), the strategies inside it are its *own* (`own_duel`, `own_items`, `is_own_entry`, `OWN_KINDS`), and the modules are `combat/bot_strategy_models.py` and `combat/bot_combat_strategy.py`. No file format changed: no field of any combat model carries the word.

`DuelCombatStrategy` **is-a** `DuelConfig`: the same shape, plus an id and a name. A `StateAssignment` and the Bot Combat Strategy's `default_strategy_id` only ever *link* one, by id, and an unlinked one falls back a level. There is no inline duel config. `StateAssignment.duel_config` and `BotCombatStrategy.default_duel_config` were removed in #118 rather than migrated, because none existed and the editor for them was already gone. The models do not forbid extra fields, so a stray old file still loads, with those fields ignored.

The link/inline pair exists at the player level only: a `PlayerAssignment` carries either `player_combat_strategy_id` or its own `dsl`, which is the assignment card's `Custom DSL…`. **Linked wins, and an inline DSL is not a fallback for a broken link** — a link that cannot be resolved is a fault before the run, not a silent reversion to whatever was typed before the link was made.

## A bot's own strategies (#118)

A fight sometimes needs a strategy no other fight needs, and a library-only store made every one-off clutter the list every bot sees. So a Bot Combat Strategy holds duel and player strategies of its own, in `BotCombatStrategy.duel_strategies` and `player_strategies`, inside its file. Deck presets stay library-only; its own decks are already its `player_decks`, one per slot.

Its own strategies have ids like any other entry, and are linked with the existing fields: `StateAssignment.duel_combat_strategy_id`, `BotCombatStrategy.default_strategy_id` and `PlayerAssignment.player_combat_strategy_id`. That is what lets one move into the library with nothing rewritten.

- **Lookup order: the active Bot Combat Strategy's own first, then the library.** It is one helper, `StrategyLookup` in `bartlebot/combat/bot_combat_strategy.py`, and `combat_resolution`, `combat_warnings`, `find_references` and the Combat tab's cards and fights popup all go through it. A surface that resolves ids its own way is how two of them come to disagree about what a fight runs.
- **Library content never links a bot's own.** A library duel is shared, so it may use only library player strategies, or it would depend on one user's setup for one bot. The dialog enforces it: the assignment menu in a library duel offers library player strategies only, and in one of the bot's own duels it offers the bot's own first, then the library's (`StrategyWorkingSet.players_for_duel`). `combat_warnings` flags a violation that got in some other way, such as a hand-edited file.
- **Only the active Bot Combat Strategy is ever shown or edited.** Others of the same bot never appear in the dialog; showing them read-only next to an editable one was judged more confusing than useful. Duplicating it is how its work is reused. Importing one entry from another is #134.
- **Add to library moves an entry, keeping its id.** A duel takes the bot's own player strategies it uses along with it, after a confirmation that names them in numerals. It is an edit like any other, so it happens at Save and Cancel undoes it. Save writes the library files *before* rewriting the Bot Combat Strategy without them, and skips it if a library write failed, so an interrupted save leaves two copies, never none. That is `save_by_id`'s rule for a rename.
- **Duplicating a Bot Combat Strategy gives everything it owns a fresh id** and rewrites every link inside the copy (`bot_combat_strategy.duplicate_bot_strategy`). Otherwise the two share ids, and once one copy moved into the library, the other's copy would silently hide it.
- **`find_references` counts links from a bot's own duels.** Without that, a library player strategy used only by one of them read as unused, and deleting it gave no warning. The usage line names what uses an entry — "Used by the Bot Combat Strategy 'Solo' and 'Boss — P2'" — rather than only counting, and names the level once, before its names.

`DeckData.tc` is legacy and is never read or applied. Treasure cards are deliberately absent from the deck editor; do not add them back without deciding what applying them would mean.

**A deck's cards are captured, its archmastery answer is chosen.** Everything in a `DeckPreset` comes off the wizard at import time — the cards, the card kinds, the archmastery *school* — and none of it is typeable, so the Deck Presets page shows all of it read-only. What it offers is the way to capture again: the same **Import from client** and **Apply to client** as a deck card on the Combat tab, with the player picker Fill current and Record use (`DeckPresetEditor`): one client acts at once, two or more open a "from Player N" menu. Import replaces the preset's cards in the dialog's working copy, so Save decides whether it lasts, and it keeps the deck's archmastery answer. The dialog knows nothing about clients, so the controller hands it a `DeckClientLink` and runs the job under the same one-at-a-time rule as the tab's. The exception is `apply_archmastery`, the three-state "should applying this deck set that school", which is a decision and belongs to whoever owns the deck. It is editable in two places, on purpose: the sidebar deck card's menu, for the deck in a player slot, and the Deck Presets page, for one in the library — a preset whose only route to that answer is *load it into a slot first* is a preset that cannot be configured where it lives. Both spell the three modes from `deck_content.ARCHMASTERY_MODES`, and neither resolves the value on load; `resolve_apply_archmastery` runs at apply time, and at display time for the icons.

## Two roots, and why writes only ever go to one

`strategy_store` resolves against an **ordered pair** of roots, and which pair depends on the kind:

| kind | shipped root (read, **never** written) | user root (read and wins, always written) |
|---|---|---|
| duel strategies, player strategies, deck presets | the install directory with the newest shared Official set (`shared.official_set`) | `%APPDATA%/Bartleby`, via `shared.vendor.vendor_dir` |
| Bot Combat Strategies | `project_root()`, the install directory | `%APPDATA%/Bartleby/BartleBot`, via `shared.vendor.product_dir` |

**The first three are one library for every product by the vendor** (#168): BartleBuddy reads and writes the same `%APPDATA%/Bartleby/strategies/`, beside `accounts.json`. Their models and their store live in `bartlebot/shared/` (`strategy_models`, `strategy_library`), which imports nothing from `bartlebot` outside itself, and `strategy_store` is BartleBot's facade over them. Bot Combat Strategies stay BartleBot's own, since only BartleBot has bots. `store_roots.resolve_shared_roots` gives the library's pair; in developer mode both are the repo, whatever else is installed.

**Which shipped copy.** Each product ships the shared Official set in its own install directory, pinned from one source. With two installed, `official_set.shipped_root` reads both products' install keys under `HKCU\Software\Bartleby\<product>` (`InstallDir`, `SharedSetVersion`, written by #27's installer) and uses the higher set version, whole, never mixing items from two sets; on a tie, or once the other product is uninstalled, our own. `SHARED_SET_VERSION` is raised whenever a shared Official item changes. The registry is read through `InstallRegistry`, and the suite swaps in an empty one.

**Two products, one file.** Every write to the shared library checks first (`shared/strategy_library.py`):

- A load hands back each entry's **fingerprint**, the SHA-256 of its user-root file as read, or `None` when it has none, and a save or delete passes it as `expected`. If the file changed since, nothing is written and `ChangedOnDisk` is raised. A save with no fingerprint expects no user file, which is what creating one means. `StrategyWorkingSet` holds each entry's fingerprint and lists a refused write in `conflicts`; the dialog then asks per entry, **Save mine as a copy** (the default; `keep_as_copy`) or **Use theirs** (`reload`). A refused delete can only be reloaded.
- Each library file carries a **`format`**, left out while it is 1, so no file written before it existed changes. A file in a format newer than `LIBRARY_FORMAT` loads read-only, since the models drop fields they don't know; its editors are locked, Delete and Duplicate are disabled, and `NewerFormat` refuses any write.

Every read merges the two with the user copy winning; every write and every delete touches only the user root. A user editing a shipped strategy therefore produces an **override**, not a fork: same id, user copy wins, shipped file untouched and still updatable. Deleting the user copy of a shipped id is not a delete at all — it is *revert to default*, and `Origin` / `origin_of()` exist so a surface can tell the two apart and say the honest word on the button.

The dialog asks a second question on top of that one: not *where is the file*, but *what will Save leave behind*. That is `StrategyWorkingSet.status()`, which compares an entry's content with the shipped version the set loaded (`list_shipped`), so it counts unsaved edits (#124):

- **Official**: the shipped content, unedited. Its Delete is disabled. Removing only touches the user root, so a "deleted" Official one came back on the next open, while the confirmation had promised otherwise.
- **Official (modified)**: differs from the shipped version, saved or not. The button is *Revert to default*. `revert()` puts the shipped version back in the working set and the entry stays listed and selected, with the same id, so everything linking it keeps working. Save then deletes the user copy.
- **Custom**: not shipped. An ordinary Delete.

Save deletes the user copy of any Official one whose content is back to the shipped version, whether through Revert or by hand-editing it back, because an identical copy would still shadow every later update. Every card wears its source as a pill, with labels from `store_roots.SOURCE_LABELS`: **Official**, **Official (modified)** and **Custom**, the one map every list of bots, templates and subgraphs uses too (#149). Official means shipped, and so tested and proven in game; it is worked out from the root a file is in and never read from the file, so nothing a user receives can claim it. In developer mode the two roots are one directory, so nothing reads as Official and Delete really deletes, which is what authoring shipped strategies needs.

**An override says when the Official one has moved on** (#169). An override silently hides every later Official fix, the problem Debian's `ucf --three-way` exists for, so the save that first makes one records `official_sha256`: the content hash of the Official strategy it was made from (`shared/content_hash.py`). Later saves keep it. When the Official strategy later hashes to something else, after an app update or because the other product's newer shared set took over, `StrategyWorkingSet.official_updated()` says so: the card's pill reads **Official (modified) · Official updated**, and the About pane says so beside *Revert to default*, which takes the new one. Nothing compares the two side by side yet. An override made before the field existed has none, and is never called out of date: there is no record of what it was made from. Copies (Duplicate, Save mine as a copy) start with no base and no history, since they override nothing.

**Every strategy keeps a history** (#169): duel and player strategies, decks and Bot Combat Strategies each carry `history`, the content hashes of what earlier saves replaced, newest first, each once, at most 64, and left out of the file while empty. The store records it before writing, from the file the save replaces (or, for the first save of an override, the Official one), so loading never writes one and an unchanged save adds nothing. Import (#171) reads it to tell a newer copy of an item from a separate edit of it. The hash is of content, the model dumped as its file would be with the keys sorted and `history` and `official_sha256` left out, so a reformatted file is not an edit. Because of those two fields, `status()` compares an entry with the shipped one by that hash, not field by field: an override edited back to the Official content still reads as Official, and Save still removes it.

Shipped strategies are authored with `BARTLEBOT_DEV` set (`bartlebot/content/store_roots.py`), which makes the project root the user root too, so a save lands in the repo. Without it, editing a shipped strategy produces an override that the app keeps running while the tracked file quietly goes stale.

**Each strategy kind exports to a share file of its own** (#170): a duel strategy as `.bbduel`, a player strategy as `.bbplayer`, a deck as `.bbdeck` and a Bot Combat Strategy as `.bbcombat`, with every Custom strategy it links inside and every Official one named. Export… is on a card's right-click in the dialog and on the Combat tab banner's menu, and the pick and every option value stay behind. `docs/SHARING.md` has the format and what each kind carries.

This is what makes a shared bot shareable. `SettingsManager.get_active_strategy(bot_id)` stores *which* strategy a bot uses, per user, in `settings.json`; the bot file never records it. The pick was always user-owned; #2 made the content user-owned too.

Before this, `project_root()` was the only root, and the Combat tab autosaved into it — so a user whose roster did not match the author's edited tracked repo files that an update would overwrite, and could not share the bot without shipping their roster choices with it.

## Filenames are names, ids are inside

A file is named after its item — `strategies/duel/default-team-duel.json`, not `{uuid}.json` (#99) — and every lookup by id goes through `store_roots.index_dir`, which reads the id each file declares. Everything that refers to a strategy or a subgraph does so by id, so the filename is for people browsing the directory and for git, and nothing else.

What that costs, and where each cost is paid:

- **A rename moves the file.** `save_by_id` writes the new name *before* deleting the old one, so an interrupted save leaves two files with one id rather than none. `index_dir` resolves a duplicate to the newer file and logs a warning; the next save or delete removes the extra one.
- **A hand-copied file keeps its source's id**, and one silently hides the other. `test_no_two_shipped_files_declare_one_id` catches it in the repo; on a user's machine the warning is all there is. Give a copy a fresh uuid4.
- **Shadowing is by id, not filename.** A user copy of a shipped strategy is named after the *user's* name for it, so the two roots can hold one item under different filenames. `origin_in`, load and revert all look it up by id.
- **Collisions are numbered**, `support-2.json`. The slug is lowercase, so names differing only in case are numbered apart rather than overwrite each other on Windows. A file keeps its number when the plain name frees up, so a re-save does not churn git.
- **A file from before #99**, named `{uuid}.json`, still loads, and is renamed on its next save. The shipped ones were renamed in one commit; `test_every_shipped_file_is_named_after_its_item` keeps them that way.

## The Save/Cancel boundary

`StrategyWorkingSet` holds deep copies of the library, and of the active Bot Combat Strategy when the dialog was opened from the Combat tab. Every editor mutates a copy; nothing reaches disk until `commit()`, which writes the library, then the Bot Combat Strategy, then the library deletes. `CombatStrategyDialog` is a view of one working set, and **nothing below the dialog decides when a file is written**.

Three rules hold this together:

- **Dirtiness is derived, never reported.** An entry is modified when its fields differ from the snapshot taken at load. Nothing calls a `mark_dirty()`, because there isn't one. That means an editor that mutates its model directly participates with no changes at all, and an edit typed and then typed back out leaves the dialog correctly clean — which a flag set from a `changed` signal cannot say. The snapshot uses a full `model_dump`, not the `exclude_defaults=True` form `save()` writes: a field set back to its default is not a change to the *file*, but it is a change the *user* made, and losing it on Cancel without warning would be a lie.
- **One rule at the edge, in every mode.** Cancel discards every edit; Save and Use both commit every edit, and Use additionally returns the selection. Pick edits nothing (#123), so there Use only ever returns the selection.
- **A write that fails keeps the dialog open.** `commit()` logs and skips a strategy it could not write; closing anyway would discard the user's work while showing them a successful Save.
- **An edit that reaches further than the dialog shows asks first, at Save.** If a changed library entry is used by another Bot Combat Strategy of any bot, or a changed player strategy by a library duel, Save asks once and lists those uses — "'Boss Burn' is also used by 2 other Bot Combat Strategies: 'Solo' (Atlantea) and 'Team' (Darkmoor)." — with **Save anyway** and **Go back** (`StrategyWorkingSet.shared_uses`). Uses inside the active one don't count. The library view asks too, and there every one counts. At Save rather than on the first keystroke, because typing is not yet a decision. It is what makes leaving library entries editable in the Bot Combat Strategy editor safe; locking them would make the library harder to use.

The Combat tab keeps its own copy of the active Bot Combat Strategy and writes it on almost every action, so an accepted dialog is followed by a reload of it from disk and a re-bind of every fight **before** the tab acts on the result (`CombatTab.open_combat_dialog`). Without it, a strategy made in the dialog and then picked was lost when the tab saved its stale copy over the file, and the fight was left linked to nothing without a word. A caller that held a binding from before the dialog looks it up again by key (`_link`).

This is what retired the old 500 ms debounce, which made three things impossible at once: Cancel meant nothing, a half-typed DSL was persisted, and a partial flush could commit some fields of an edit and drop others. It also retired `AppController._flush_pending_edits`, which existed only because a quit could beat the debounce. **Do not reintroduce a write on a timer at any level below the dialog** — a better debounce is not the fix; the boundary is.

`tests/test_strategy_editor_identity.py` is gone for the same reason. Its premise was that a debounced save must carry the id of the strategy it belongs to, so a late flush cannot land on whatever the user selected in the meantime. There is no longer a window across which the selection can move.

## One window, and what it does not need

`StrategyLibraryWindow` and `StrategyPickerDialog` used to do largely the same job with different vocabularies. There is now one window, in one mode per way in, and each shows only the tabs its row lists (`dialog.tabs_for`):

| way in | mode | tabs | This bot section | can edit |
|---|---|---|---|---|
| **File → Combat Strategies** | `LIBRARY` (`Cancel` / `Save`) | Duel, Player, Decks | no | library entries |
| **✏** on a fight card or the default card | `BOT_STRATEGY` (`Cancel` / `Save`) | Duel, Player | yes | the bot's own and library entries |
| **🔍** on a card, a fight's pill, the add-card's "Select existing duel strategy…" | `PICK` (`Use Default Duel Strategies` or `Use (bot default)` / `Cancel` / `Use this`) | Duel | yes | nothing |
| **Load from library** on a deck slot | `PICK` | Decks | no | nothing |
| **Change…** on a Settings › Combat card | `PICK` (`Cancel` / `Use this`), filtered to a team of one or two | Duel | no | nothing |

- **Pick is select-only (#123)**: no New, Duplicate or Delete, and the detail pane is read-only. Before, Pick edited everything, which made 🔍 and ✏ the same button with different wording.
- **In the Bot Combat Strategy editor, New and Duplicate create in it**, a library entry's duplicate included — that is how "this fight needs something slightly different" is done. Creating in the library happens in the library view, or by creating here and then using **Add to library**. The extra step is deliberate: it makes users face the difference between the two places.
- **The This bot section** comes first, headed with the Bot Combat Strategy's name, then ★ Favorites, then the library. Its cards wear a **This bot** pill in `pill_this_bot_bg`, a teal of its own, never the error red that marks a DSL that does not parse; the pill's tooltip names the Bot Combat Strategy. They carry no star, because favorites are library-only. An empty section says "Strategies made here belong to this Bot Combat Strategy. Use Add to library to share one." A loaded bot with none yet is offered one there ("This bot has no Bot Combat Strategy yet."), and Cancel discards it. The About pane says "In 'Solo' only", with **Add to library** beside it. The library view never shows a This bot section, even with a bot loaded.
- **Pick's first button names the level below the caller's**: `Use Default Duel Strategies` on the default card, `Use (bot default)` on a fight, with a tooltip saying what that means (`none_label`, `none_tooltip`). A way in with no level below, such as Settings › Combat, has no such button.
- **From the Combat tab**, ✏ is on every card, one running a default included, and a strategy made on that visit is linked to the card that opened the editor on Save (`selected_was_created`). The add-card ("Add another duel strategy") is a menu of two: "Select existing duel strategy…" opens Pick, and "Create new duel strategy…" opens the Bot Combat Strategy editor with nothing selected. Either way the flow continues into choosing the card's fights, and if that popup closes empty the card is dropped but a strategy made for it stays in the Bot Combat Strategy.
- **Save as Player Strategy…** on an assignment card saves where its duel lives: among the bot's own for one of its own, in the library for a library duel.

It is **master-detail, not a popup, and not both**. `PickerPopup` is 400×420 and right for a one-line payload — a tag, a fight name. A duel strategy's payload is seven to nine mostly-long lines: a description, a combat order, and one row per assignment. Eliding those to fit a popup hides exactly what the user opened it to compare. So the left is the compact `StrategyCard` list with a filter, the right is the full entry unelided, plus what uses it and whether removing it deletes or reverts it. `_AddOptionDialog` is the precedent.

Two things fall out of the tabs being pages of one working set:

- **The assignment menu reads its options from the working set**, so a Player Strategy created on the sibling tab is selectable immediately. That is what removed the need for a picker opened on top of a picker.
- **Promotion is the dialog's job, not the card's.** `Save as Player Strategy…` emits a request; the dialog names it, creates it in the working set, and links the card back to it. A card that wrote its own file would be writing outside the boundary.

**Name, description, usage and origin belong to the page, not to the editor under it.** All three tabs draw them in one shared `AboutSection`. They were drawn three different ways before — label-above on the Player tab, label-beside on the Deck tab, and split across two widgets on the Duel tab, where the name belonged to a wrapper pane and the description to the `DuelConfig` beneath it. They identify the *library entry*, which is a thing all three kinds have; what varies between tabs is what sits below.

`BotStrategyPicker` is the one picker left, and it picks the one kind the library does not own: a `BotCombatStrategy` is bot-scoped and edited on the Combat tab itself. `StrategyWorkingSet` holds every Bot Combat Strategy but the active one **read-only**, only to answer *who references what* for a usage count, a delete confirmation and the Save confirmation. The active one it holds editable, for its own strategies.

Filtering must never disturb the editor beside it. `QListWidget.clear()` emits `currentItemChanged`, and repopulating emits it again — one keystroke in a filter box used to reload the editor and send the DSL caret back to 0. Reselect *inside* the blocked-signals region, and detach the editor only when the entry is actually gone from the working set, never when it is merely filtered out of view.

**Tags, filters and favorites (#125).** A bar across the top of each tab carries facet pickers, the search box and, below them, the selected values as removable chips — the bot library's shape. The vocabulary is `bartlebot/combat/strategy_tags.py`, laid out like `bot_tags.py`: tags OR within a group and AND across groups.

- **Source** (Built-in / Built-in (modified) / Custom) is derived from `StrategyWorkingSet.status`, never stored, and its words are the source pills'. A tab with a This bot section adds **This bot**, for the bot's own entries, the same word their pill says, from the same constant. Which Bot Combat Strategy is the section heading's job, not the pill's: a pill of its name repeated the heading and was the one pill wide enough to push a card past its column. **Role** (Hitter, Support, Healer, Solo) is stored, in `tags`, on Player Strategies and Deck Presets only. A duel is a whole team, so it has no role.
- **Works with school X** (Duel and Player tabs): a Player Strategy whose `schools` is empty or contains X, or a duel with a player slot that accepts X. A slot no assignment covers falls back to the built-in default, so it accepts every school. Several schools OR.
- **Team size N** (Duel tab): duels whose `min_players`–`max_players` includes N. It also limits the school check to slots 1..N.
- **Favorites** are per user, in `settings.json` under `favorite_strategies`, and saved on the click. They sit outside the Save/Cancel boundary, because a favorite is a preference about the library, not a change to it, and an entry of the bot's own has none. A favorite moves its card under a "★ Favorites" heading. Every entry is in exactly one section, because selection is restored by id. The headings appear only once there is more than one section, and a heading whose section the filter empties hides. The star on a favorite always shows; on any other card it shows only on hover or selection.
- Nothing about the filter persists. Every opening starts unfiltered, since a remembered filter hiding entries is a "where did my strategy go" that a Pick-mode visitor cannot diagnose.

**A school is a set.** `PlayerCombatStrategy.schools` and `PlayerAssignment.schools` are lists, and empty means any school. A file with the old single `school` loads as a one-item list and is rewritten on its next save. An assignment's own non-empty set wins over its linked preset's. Deck Presets carry one too (#174), authored the same way and never inferred from the cards, since most spells can be trained on any wizard; it is the school the deck is built for, not the archmastery school it captured, and the Deck tab's "works with school" filter reads it.

## The shipped defaults (#206)

Six Official items are what a fight with nothing tailored runs, and a seventh, Default
Guardian Support, ships beside them for a user to pick. They are ordinary files
under `strategies/`, found by id, and their ids are named in
`bartlebot/shared/combat_defaults.py`, which imports nothing from the rest of `bartlebot`.

| item | kind | what it does |
|---|---|---|
| Default | player | the hitter ladder (below): heals itself below 30% health, shields itself while it holds no shield, buffs while a buff adds to its next hit, then hits; its DSL is `LAST_RESORT_DSL` |
| Default Team Hitter (`SHIPPED_TEAM_HITTER_ID`) | player | Default's lines, plus a round-1 line that opens a mob fight with AoE |
| Default Support | player | the support ladder (below): attacks in round 1 of a mob fight or once P1 is dead, and otherwise buffs P1 and traps the boss, casts a global and then a negative aura on the boss, and passes. It never heals or shields |
| Default Guardian Support (`SHIPPED_GUARDIAN_SUPPORT_ID`) | player | heals, shields and keeps a survival aura up, P1 first, then plays the support ladder (below). No duel links it |
| Default Solo Duel (`SHIPPED_SOLO_DUEL_ID`) | duel | one player: P1 runs Default |
| Default Team Duel (`SHIPPED_TEAM_DUEL_ID`) | duel | two to four players: P1 runs Default Team Hitter, P2 to P4 run Default Support, combat order 4, 3, 2, 1 |
| Default Team Duel (Everyone Attacks) (`SHIPPED_EVERYONE_ATTACKS_DUEL_ID`) | duel | two to four players: P1 to P4 run Default Team Hitter |

`LAST_RESORT_DSL` is also what a fight runs when no file can be read at all. The two
duels named Solo and Team are what level 4 of the resolution answers with (below).

The Official set ships six duels. Besides the three above, each made to be assigned to
the fights that need it:

| duel | for |
|---|---|
| Default Team Boss Duel (No Blades) | a boss that cheats on a blade: P2 to P4 run Default No-Blades Support |
| Default Team Boss Duel (No Feints) | a boss that cheats on a Feint: P2 to P4 run Default No-Feints Support |
| Default Turn 1 Scald-Incindiate | a team of four: P1 and P2 buff, P3 casts Scald and P4 detonates it |

Default Team Boss Duel and Default Turn 1 AoE Mob Duel left the set at version 6 (#218,
Stage 5): Default Team Duel reads the fight kind in its strategies, and a default can't be
chosen by fight kind. The player strategy the second one linked, Default Turn 1 AoE mob
fight, followed at version 8 (#212): every shipped team strategy already AoEs in round 1 of
a mob fight, and despite its name it cast an AoE every round, boss or not. Nothing leaves the set after 1.0.0 (`official_set.SHARED_SET_VERSION`),
since a user's link to it would then point at nothing. Before then a Bot Combat Strategy
still linking one gets that fault before a run, and its card reads "(missing strategy)";
nothing relinks it for the user.

**An ambient fight's Team default is Everyone Attacks.** In a fight no node started
(`docs/AMBIENT_COMBAT.md`), players take their seats in whatever order they arrive: the
one pulled in first, then helpers as their teleports and ports land. Default Team Duel's
combat order is what puts P1 in the last seat, so there Default Support's blade on
`ally(last)` would land on whoever joined last. Most ambient fights are a stray mob, which
every player hitting ends quickly. So `default_duel(..., ambient=True)` answers level 4
from `SHIPPED_AMBIENT_DUEL_IDS`, and the label says "the shipped Team default for an
ambient fight". Levels 1 to 3 are unchanged: a duel the user chose still runs, `ally(last)`
and all.

**What a support does** (#218, Stages 3 and 6). A support never spends a buff meant for
the hitter, and Default Support does nothing else: it never heals or shields. Combat
order 4, 3, 2, 1 seats the supports to cast before P1, so a support that hits a trapped
boss spends the Feint P1 was meant to hit through. Default Support's lines, first that
can cast wins:

1. Enchant ahead (below).
2. Round 1 of a mob fight only: AoE, then single-target damage.
3. `?(ally(last).health == 0)`: AoE, then single-target damage. `ally(last)` is P1
   under combat order 4, 3, 2, 1, and the team list keeps the dead, so it stays P1. The
   gate reads `health`, the participant's own, and never `is_dead`, which reads the game
   stats' hit points: wizsprinter's targeting has found those still reading alive for a
   killed enemy.
4. The buffs that stack: `"Feint Mass - Amulet"`; `any<trap & !aoe & stacks>[potent] @ boss`,
   which ranking points at Feint; `any<blade & stacks>[sharp] @ ally(last)`; and a mass
   trap, `any<trap & aoe & stacks>`.
5. The same buffs without `stacks`, in the same order. A buff that won't add to the next
   hit still stays up for the one after, which a two-hit Challenge Mode fight wants.
6. `any<global>`.
7. `any<harmful_aura & inc_damage & stronger> @ boss`: a negative aura such as Attenuate
   (+20% incoming damage, four rounds), the last buff before a pass.
8. `pass`, which banks pips.

So a support attacks only where no buff for P1 can be standing: round 1 of a team mob
fight, and once P1 is dead. When P1 dies with the boss far from dead, the fight is lost
and the team should flee, reset and recover; the supports attack at once rather than
buff themselves into a second hitter. A blade comes before a mass trap other than Mass
Feint: a mass trap is about 30% from an item card that can't be enchanted, a blade 35%,
or 45 to 55% enchanted. Two supports can pick the same buff in round 1, since each plans
from the state at the start of the round; the duplicate stays up for the next hit.

- **The round-1 line carries the whole ladder.** wizsprinter walks only the `{1}` line in
  round 1 and never falls through to the repeating one, so the `{1}` line is the
  repeating line with the mob-fight AoE added after the enchant-ahead slot.
- **Mass Feint is named, Feint is found by rank.** `"Feint Mass - Amulet"` is the only
  Mass Feint players get, and the quoted name also matches the pet version. An unquoted
  `feint` would match Mass Feint too. `stacks` can't be put on a name, so a held Mass
  Feint is cast even onto one already up.
- **The Feint line takes the best single trap**, `!aoe` keeping Mass Hex below the blade.
  With no Feint in hand it takes a school trap, ahead of the blade.
- **A support's own positive aura never helps P1.** Amplify or Frenzy lands on its caster
  and raises only the caster's hits, so no buff support casts one. A negative aura on the
  boss does help: unlike a trap, a hit doesn't use it up. A participant holds one aura,
  and a new one replaces it, so Attenuate replaces a boss's own Frenzy. `stronger` (in
  wizsprinter's `CONDITIONS.md`) keeps a weaker one from replacing a stronger: Attenuate
  never goes over Typhoon Attenuate. It ranks by size and doesn't read schools, so
  Typhoon Attenuate's Death and Storm halves rank first for a Fire hitter too.

The No-Blades and No-Feints supports take the same ladder minus the card their boss's
cheat watches. Their duels assign only boss fights, so they have no round-1 line, but
they keep the P1-dead line. `tests/strategies/test_supports.py` walks all three through
the fights above.

**What the guardian does** (#218, Stage 6). Teams rarely carry shields or need a healer,
since killing faster wins, so Default Team Duel keeps the buff support. A team whose
decks do carry them carries them out of necessity, and casts them before any buff.
Default Guardian Support is for that team: a user picks it for a seat. It protects P1
first, since losing the hitter loses the fight, then itself. Its lines, first that can
cast wins:

1. Enchant ahead.
2. `?(ally(last).health < 50% && ally(last).health > 0) any<heal> @ ally(last)`, then
   `?(self.health < 30%) any<heal> @ self`. Healing is its job, and a hitter at 50% can
   die to one boss hit.
3. `?(ally(last).beneficial_wards == 0 && ally(last).health > 0) any<ward> @ ally(last)`,
   then the same for itself. One shield at a time, so a second never stacks; Feint's
   drawback on P1 is a harmful ward and doesn't count.
4. A survival aura while none is up: `any<aura & inc_damage>` (Stabilize), then
   `any<aura & out_heal>` (Mend). Its own aura protects it or strengthens its heals, so
   unlike a damage aura it is worth casting.
5. Default Support's ladder from its round-1 AoE on: the P1-dead attack, the buffs, the
   global, the negative aura and `pass`.

Protecting whichever teammate is lowest instead would need a health condition per seat,
which makes the DSL unreadable. `tests/strategies/test_guardian.py` walks it through its
fights, and checks that with nothing to protect it casts what Default Support does.

**What the hitter does** (#218, Stage 4). The hitter keeps buffing while a buff adds to
its next hit, and hits once none can cast and the pips allow. Players buff until a hit
will kill; a buff already up is no reason to stop. Default's lines, first that can cast
wins:

1. Enchant ahead (below).
2. `?(self.health < 30%) any<heal> @ self`.
3. `?(self.beneficial_wards == 0) any<ward> @ self`: a deck that carries a shield carries
   it out of necessity, so it goes up before any buff, one at a time. A user who wants no
   shields leaves them out of the deck.
4. The buffs that stack: `"Feint Mass - Amulet"`; `any<trap & !aoe & stacks>[potent] @ boss`,
   the support's Feint line; `any<blade & stacks>[sharp] @ self`; and
   `?(self.beneficial_auras == 0) any<aura & out_damage>`, an aura only while none is up.
5. The hit: `any<damage & aoe>[any<mod_damage>]`, then `any<damage>[any<mod_damage>] @ boss`.
6. Fillers: `any<global>`, then `any<shadow>`.
7. The buffs that don't stack: the Feint line and the blade line without `stacks`.
8. `pass`.

A hit it can't afford doesn't cast, so with buffs up and too few pips it falls to a
filler, a buff that won't stack, or a pass, and keeps its buffs. There is no
single-target line at a mob: short of pips for AoE in a mob fight, the hitter waits
rather than spend its buffs on one mob. Until smart combat (#30) can tell when a hit
kills, "no buff left that stacks" stands in for it. No trap aims at a mob, which could be
a minion, and a mass trap other than Mass Feint is the supports' to cast.

- **Default Team Hitter is Default plus a `{1}` line**, which is the same ladder with
  `?(all(enemies).is_boss == 0) any<damage & aoe>[any<mod_damage>]` after the shield: in a
  team mob fight everyone opens with AoE, and the mobs that survive it are the strong
  ones. A solo wizard usually can't clear mobs in round 1, so Default has no such line
  and buffs first. Both team duels link it, so an ambient fight's stray mob gets the
  round-1 AoE too.
- **Default AoE Hitter and Default Single-target Hitter** take the heal, the shield, the
  Mass Feint line and the stacking buffs (the single-target one the Feint line too), keep their own
  hit line, then a buff that won't stack, then `pass`.

`tests/strategies/test_hitters.py` walks all four through the fights above, and finds a
line for every card kind across the shipped player strategies.

**The fight kind is read by one rule.** A boss fight is `?(any(enemies).is_boss == 1)` and
a mob fight `?(all(enemies).is_boss == 0)`: `any` and `all` count every living enemy,
targetable or not, so they describe the fight. A line aimed `@ boss` needs no condition:
it skips when there is no boss. Write the condition this way and no other:

- **Never `?(boss.health > 0)`.** It goes through resolving `boss`, which may come up
  empty while the boss can't be targeted.
- **Never `boss.hp`.** It is no attribute, and since #214 it does not parse; before
  that it was silently false on every turn.

**Every shipped player strategy enchants ahead, first.** Its first line fuses enchants
into the cards they fit (damage enchants into damage, AoEs first; Sharpened Blade into
blades; Potent Trap into traps) before the round's card is cast. Fusing never ends the
turn, and each fuse frees a hand slot that the next refill draws into, which is how a big
deck cycles. wizsprinter holds such a line until it knows the round's card, and drops it
when that card attacks, leaving the hand alone; wizsprinter's `CONDITIONS.md` has the rule.
Two rules hold for the line:

- **It is gated `?(deck.remaining >= 2)`.** A deck the hand already holds gains nothing
  from a freed slot. `deck.remaining` counts treasure cards until the main pile first runs
  short, so a small deck carrying them enchants ahead for one round it need not.
- **A strategy enchants only the kinds it casts.** A fused card it never casts holds a
  slot for the rest of the fight. So the No-Blades support fuses no Sharpened Blade, and
  the No-Feints support's Potent Traps go only into traps that are not feints: those two
  exist for bosses that cheat when a blade or a feint is cast.

`LAST_RESORT_DSL` carries the line too, since it is Default's text.

**`SHARED_SET_VERSION` rises whenever a shared Official item changes**, so a product
shipping an older set never mixes items with this one's.

## Resolution at run time (#207)

Each fight's duel strategy comes from the most tailored of four levels that has one and
fits the team, resolved when the fight starts. `combat/combat_resolution.py` holds them,
and `resolve_combat()` in `bartlebot/nodes/combat_runner.py` wraps it, returning
`(per_client_dsl, combat_order, source_label)`.

| level | `Level` | where it lives | what answers |
|---|---|---|---|
| 1 | `PICK` | the user's Bot Combat Strategy for this bot (`SettingsManager.get_active_strategy`) | the fight's assignment, else its own default |
| 2 | `OFFICIAL` | the bot's shipped Bot Combat Strategies | the one whose duels fit the team for the most of the bot's fights (`official_strategy_for`) |
| 3 | `YOUR_DEFAULT` | the user's Solo or Team default, in `preferences.json` | the duel stored under `combat.default_duel.solo` or `.team` |
| 4 | `SHIPPED_DEFAULT` | the shipped defaults | Default Solo Duel for one client, Default Team Duel for two or more (Everyone Attacks in an ambient fight) |

- **A duel fits *n* clients when `min_players` ≤ *n* ≤ `max_players`** (`DuelConfig.accepts`).
  At levels 2 to 4 a duel that doesn't fit falls to the next level, and the reason is
  kept for the log. At level 1 it is used anyway: the user chose it, the pre-run check
  is what tells them it doesn't fit, and Run anyway means running what they chose.
- **A Bot Combat Strategy covers a fight with its assignment, else its own
  `default_strategy_id`.** Only one with neither falls through for that fight, and the
  user's falls to level 3, past the Official one: a run never fills the user's gaps
  from the author's, so each run has one owner to explain it. Level 2 answers only when
  the user has no Bot Combat Strategy for the bot, or its file is missing. An
  unlinked assignment counts as none. The assignment is found by `fight_keys.bind`,
  below, and the duel and its player strategies by `StrategyLookup`, the bot's own first.
- **Level 2 reads the shipped files as they ship** (`strategy_store.list_shipped`). A
  user's copy of an Official Bot Combat Strategy is the user's, and only ever their
  pick. Its score counts the bot's default fight as one fight, and a strategy that
  fits no fight isn't a candidate. `test_no_two_shipped_bot_combat_strategies_for_one_bot_tie`
  refuses two that tie at any *n* from 1 to 4, so a run never needs a tie-break. No
  Official Bot Combat Strategy ships yet, so level 2 is empty until one does.
- **Levels 3 and 4 are shared** (`shared/combat_defaults.default_duel`), so BartleBuddy
  resolves a default the same way. "Recommended" is stored as no key at all, so a later
  fix to the shipped default reaches everyone who kept it. A stored id the library
  lacks, or a duel that doesn't fit, falls to the shipped one.
- **Last resort.** When even the shipped defaults can't be read, every slot runs
  `LAST_RESORT_DSL` and the log says so at ERROR. A slot with no assignment, or whose
  player strategy can't be read, runs it too, as before.
- **Nothing writes.** No level is filled in by writing a file, on load or at run time,
  so every bot and Bot Combat Strategy file round-trips byte-identical and every level
  stays reachable. `docs/BOT_OPTIONS.md` has the same rule for options, for the same
  reason.

`source_label` names the duel and the level in the user's words, plus what was passed
over on the way: `duel strategy 'Default Team Duel' (your Team default; this bot has no
Bot Combat Strategy)`, or `duel strategy 'Boss' (via state assignment for 'fight_1';
your Bot Combat Strategy 'Farm')`. `AppController._start_bot` logs one line before the
first node, naming the run's sources fight by fight with fights that share one grouped
(`describe_run`). **Never log a strategy's contents in place of the label**, and never
log a wizard name. The label is the older precedent for the logging `BotMachine.get_option`
does; see `docs/BOT_OPTIONS.md`.

## Before a run: faults and notices (#207)

`combat_warnings.check_combat` sorts what it finds into two tiers, `CombatCheck(faults,
unassigned, fallbacks, seating)`, the last three being its `notices`:

| tier | what it is | where it goes |
|---|---|---|
| **fault** | a choice the user made that cannot work | the pre-run dialog, "Combat Strategy Problems", with **Go back** as its default and **Run anyway** |
| **notice** | a fallback in use: a valid setup | the log, once, at INFO, and the Combat tab, which names the source all the time; never a dialog |

The faults:

- **A DSL that doesn't parse, anywhere this run could reach**: every duel the pick links,
  whatever the team size, and every duel a fight falls to. That includes a condition that
  could never read anything (#214): an attribute wizsprinter doesn't know (`boss.hp`), a
  `%` on one with no maximum (`self.power_pips < 50%`, where only `health` and `mana`
  have one), and anything on `deck` but `remaining`. Each was false on every turn, which
  reads exactly like a condition that doesn't hold. Wizsprinter's parser refuses them,
  naming the attribute and the close name when there is one, so the dialog, the strategy
  summaries and the DSL editor all report them as they report a syntax error, and the
  editor underlines the attribute. The accepted names are `MEMBER_ATTRIBUTES`,
  `DECK_ATTRIBUTES` and `hanging_attributes()` in wizsprinter's `combat_api`, which
  `dsl_syntax`'s completion lists are tested against.
- **A link the user made that points at nothing**: the picked Bot Combat Strategy's
  file, or a duel or player strategy it links.
- **The user's own pick needing more players than are connected** (`min_players`).
- **A library duel linking a strategy that belongs to one Bot Combat Strategy.**

Every fault in the pick is checked over its default and the assignments `fight_keys.bind`
places on a fight the bot has. One left over from a fight the bot no longer has, after a
template update, can't run, so it never stops this run.

The notices, grouped so that a setup says each thing once rather than once per fight:

- `unassigned`: exposed fights with no assignment, which the pick's own default covers;
- `fallbacks`: fights that fall past level 1, grouped by the duel they run and the level
  that answered: "Every fight runs duel strategy 'Default Team Duel' (the shipped Team
  default; this bot has no Bot Combat Strategy).";
- `seating`: each duel a fight reaches with no combat order whose player strategy aims at
  a seat, `ally(last)` or `ally(0)` (#212). The team lists players in the order they sat
  down, and with Simultaneous entry that is whatever order they arrived in, so a support's
  blade on `ally(last)` lands on a different player each fight. It runs, so it is no fault,
  and it may be meant, but it is logged at WARNING rather than INFO. A duel seating one
  player, or with any combat order, never gets it. `seat_order` finds the seats, and the
  same sentence is the duel's health (`HealthStatus.SEAT_ORDER`): a ⚠ in the warning color
  on its card and the Combat tab, and a line under Combat Order in the duel editor.

Each is logged once. `AppController._confirm_combat` logs `unassigned` and `seating`: the
fallbacks are in the line `_log_combat_sources` writes before the first node
(`describe_run`), which names every fight's source, the pick's included, and logging
both would say each fallback twice.

So a fresh install starts every shipped bot with no dialog, and a Bot Combat Strategy
that sets only its default starts Atlantea with no dialog instead of eight lines. The
dialog is rare, and that is what makes it worth reading: the press a frequent dialog
trains is the one that keeps a broken setup from running. **Never add "Don't show
again" to it**, which would hide the faults with the noise, and never send a notice to
a dialog. The dialog's text and buttons are `gui/dialogs/combat_faults_dialog.py`.

## Showing the source, and choosing the defaults (#208)

Lasting state belongs in a status indicator, not a notification, so where a bot's fights
get their strategies is said on screen all the time, not at run start:

- **The Combat tab's banner** names the source: the user's Bot Combat Strategy, an
  Official one with its pill, or the Default Duel Strategies with the Solo or Team duel
  in use, by the clients connected. Its words come from the Qt-free
  `combat_resolution.describe_source`, which resolves as a run would. With nothing
  picked, its verbs are **Create Bot Combat Strategy** and **Change defaults…**. A pick
  whose file is gone is a fault, and the banner says so, *File missing*, with **Clear
  pick**, which forgets it. `bartlebot/gui/CLAUDE.md` has the five states.
- **The default card**, while its Bot Combat Strategy has no default, shows the default
  that answers with an **Official** or **Your default** pill.
- **Settings › Combat** is where the Solo and Team defaults are chosen: two cards, each
  the duel in use, with **Change…** (Pick, filtered to a team of one or two) and **Use
  recommended**. It writes `preferences.json` on OK, one key each, and "recommended" is
  no key. `bartlebot/gui/settings/CLAUDE.md` has the card and the apply rule.

## First run: one question about the team (#209)

A user who never opens the Combat tab never learns that strategies exist or that the
team default can be changed. Pages that only explain get skipped, but a question that
writes a real choice gets answered, so the first time the app opens it asks one: how the
team takes on bosses.

| answer | stores |
|---|---|
| **One hitter, the others support** (recommended, first) | no Team default: Default Team Duel |
| **Everyone attacks** | `SHIPPED_EVERYONE_ATTACKS_DUEL_ID` as the Team default |
| **Skip** (or Escape, or closing it) | nothing but the record that it was asked |

Every answer sets `setup.combat_defaults_answered` in `preferences.json`, so whichever
product asks first, the other doesn't ask again. The logic is
`bartlebot/shared/first_run.py`, which has no Qt, so BartleBuddy writes the same keys.
Four rules:

- **"Solo or team?" is never asked.** Both defaults exist, and the clients connected
  decide which one a fight uses. The question is about the team, and a line under it
  says solo fights use Default Solo Duel, that Settings › Combat changes either default
  later, and that any bot's combat is tailored on its Combat tab.
- **It writes only what the user answered.** One hitter is stored as no key, like every
  "recommended". The Solo default is never touched, and Skip changes no default.
- **A Team default the user already chose is the answer.** While the Team default is a
  duel neither answer names, chosen in Settings › Combat or in the other product,
  `combat_question_answered` is true and nothing is asked or written. When it is one of
  the two, the dialog opens with that card selected.
- **The record follows the write.** `record_answer` marks the question answered only
  once its default is on disk, so a failed write asks again next time rather than losing
  the choice.

Upgrading users see it once too, which is how they learn the defaults changed.
`AppController._after_window_shown` opens it after the main window shows, before a file
named at launch is imported, so the two dialogs never stack. `bartlebot/gui/CLAUDE.md`
has the dialog and its page list.

## After a lost fight: the mark on a fight fled on the default (#210)

Tutorials get skipped and forgotten, but help at the point of need works, and a user is
most ready to learn about combat strategies right after a fight goes badly. So a fight
fled while it ran a default is marked on the Combat tab, where it can be tailored. There
is never a dialog: a dialog at the end of a run is an interruption by another name.

- **What counts.** A flee in `handle_combat` on level 3 or 4 (`Level.is_default`). A
  flee on the user's pick, an Official Bot Combat Strategy or the last resort counts for
  nothing, and neither does an ambient fight or an idle-popup flee: an ambient fight is no
  fight of the bot's, so the tab has nowhere to put the mark, and an idle-popup flee means
  nothing was fighting.
- **The path.** `resolve_combat` returns a `ResolvedCombat` with the `Level` that
  answered. On a flee, `handle_combat` calls `machine.note_fight_fled(state_id, level)`
  before `note_flee`, which may stop the bot. The machine's `on_fight_fled` callback is
  guarded, so the tab raising never stops a run. `AppController` counts the flee in its
  one `FleeMarks` (`bartlebot/combat/flee_marks.py`, Qt-free), by bot and state, and the
  tab redraws.
- **The log.** One WARNING per flee on a default, through `machine.log`: *Fled 'Lost
  Soul' on a default: duel strategy 'Default Team Duel' (the shipped Team default; …).
  The Combat tab marks it, with a way to give it a strategy of its own.*
- **Where the mark goes.** Where the fight is drawn. An exposed fight's own pill on the
  default card, *Lost Soul · fled 2×*. The default card itself for fights the sidebar
  doesn't list, *Fled 1× on the default*. The banner when the bot has no Bot Combat
  Strategy, since then it is all the tab shows. The verb that fixes it is already first
  in each place: the pill's click, **Change…**, **Create Bot Combat Strategy**.
- **Only while it still applies.** `fled_on_default` resolves each marked fight as a run
  would, and a mark shows only while its fight would still run a default. A fight given a
  strategy of its own, or covered by a Bot Combat Strategy's default, loses its mark, and
  gets it back with its count if that strategy goes.
- **For the session.** The marks are in memory and last until the app closes. Switching
  bots keeps them, and nothing is written.

`bartlebot/gui/CLAUDE.md` has the pills.

## Fight keys: which stored fight is which state

A state id alone loses its strategy whenever the id changes: a node replaced, a bot
re-authored, a fight deleted and made again. So an assignment stores each fight as a
pair, the state it was on and the enemy it named:

```
{ "fights": [{ "state": "state_14", "enemy": "Lamia" }], "duel_combat_strategy_id": "…" }
```

A file written before this holds `"state_ids": ["state_14"]`. It still loads, as fights
with no enemy, and is **never rewritten on load**; the next save writes the new shape.
`StateAssignment.state_ids` is a read-only view of each fight's state.

- **An enemy key** is the fight node's `enemy` param, stripped, exactly as typed,
  wildcards included. It is a key only while exactly one exposed fight in the bot names
  it. A fight that takes the closest mob has none.
- **One resolver, `fight_keys.bind(bcs, config)`,** places every stored fight on a
  current state: a fight whose enemy is a key goes to the node naming it; each
  remaining fight goes to its `state`, if that is still an exposed fight and was not
  taken by an enemy match; anything left is orphaned. Enemy matches win, so a renamed
  enemy and a replaced node both keep their strategy, and only changing both at once
  loses it. With no config it falls back to the raw state ids.
- **Every reader goes through it:** `combat_resolution`, `combat_warnings`, and
  `combat_grouping.build_bindings`, which the Combat tab seeds from. A surface that
  looks a fight up by state id on its own will disagree with the run about a fight
  that moved.
- **The keys heal on save, and only there.** When the Combat tab saves, it writes each
  fight as its current state, plus its enemy if that enemy is a key right now
  (`fight_keys.heal`), and drops a fight whose state is no longer an exposed fight,
  along with any assignment left with none. Nothing writes to the Bot Combat Strategy
  when a node is edited: it saves at once, while the bot waits for Save.
- **In the tab, the state is the truth.** Loading places the fights and rewrites each
  assignment's fights in memory to the states they landed on (`fight_keys.settle`).
  From then on the tab edits membership by state id, so its save heals by state and
  never re-places by a stored enemy that may have gone stale.

The one place the app renames states — turning a region into a subgraph instance
(`docs/SUBGRAPHS.md`) — still re-keys every strategy of that bot through
`strategy_store.rekey_bot_strategy_states` in the same step. It renames each fight's
`state` and leaves its `enemy` alone. A fight with no enemy key has nothing else to
follow it by.

## Layers, and what stays Qt-free

| module | holds |
|---|---|
| `shared/strategy_models.py` | player strategies, duel strategies and decks, as Pydantic models |
| `combat/bot_strategy_models.py` | the Bot Combat Strategy (`BotCombatStrategy`) and its fight assignments |
| `combat/strategy_store.py` | the two roots, load/save/delete per kind, `Origin`, `unique_name` |
| `combat/strategy_workset.py` | the Save/Cancel boundary |
| `combat/strategy_summary.py` | every display string, derived once |
| `combat/combat_grouping.py`, `combat/combat_warnings.py` | how a list is grouped, and what is wrong with a configuration |
| `combat/combat_resolution.py` | the four levels a fight's duel strategy comes from |
| `combat/flee_marks.py` | the session's fights fled on a default, which of them still run one, and the marks' words |
| `shared/combat_defaults.py`, `shared/first_run.py` | the shipped default ids, levels 3 and 4, and what the first-run question stores |
| `combat/fight_keys.py` | which stored fight belongs to which combat state |
| `combat/bot_combat_strategy.py` | the own-then-library lookup, what one of a bot's own duels takes with it, and duplicating a Bot Combat Strategy |

All of these are Qt-free and need no `QApplication`. That is deliberate and worth keeping: the boundary, the precedence, the filter rule and the warnings are all testable without a widget, which is why the Qt tests over them are small enough to stay fast.

**Every display string comes from `strategy_summary`.** A surface that formats a player list or a health badge its own way is how the formatting drifted last time; `format_players`, `summarize_duel`, `summarize_player`, `summarize_bot` and `summarize_deck` are the whole vocabulary.

## Two things this is built to survive

- **School is not the organizing axis.** School-agnostic DSL (#6) collapses "Storm AoE" and "Fire AoE" into one strategy, which demotes school from a primary axis to an occasional annotation and promotes player count and role. Anything built on school as its *primary* axis — school colors, school chips, a school filter row, a school requirement as a headline facet — is building on an axis that is going away. A previous GUI pass was discarded largely for this. The "works with school" filter (#125) is on the right side of that line. It answers a *compatibility* question ("what can my Storm wizard play"), where an entry that names no school matches every school, and it is one facet among several. It does not organize the list: nothing is grouped, colored or sorted by school.
- **The tab keeps the run-time truth.** Which strategy is active, which fights it covers, per-client deck state — that is Run-mode information and must not move behind a dialog. The tab is summary, pick, and one button in.
