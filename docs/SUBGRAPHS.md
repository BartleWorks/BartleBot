# Reusable subgraphs

What a subgraph definition and an instance are, how an instance follows its definition,
what an instance may set, and how a definition is authored. The editor surfaces — the
call node, the definition document, the signature page, the crumb bar — are documented
with the rest of the canvas in `bartlebot/gui/CLAUDE.md`. The plan behind the current
model is *Subgraph calls* in `docs/EPICS.md`; the first design (#37) let an instance be
edited in place, lock its members and override their params, and all of that is gone.

Code: `bartlebot/content/subgraphs/subgraphs.py` (composition and extraction, pure),
`subgraph_store.py` (the two roots), `subgraph_signature.py` (what a save warns about),
`subgraph_tags.py` (categories), `signature_tree.py` (the signature page's model). The
models are `SubgraphDefinition`, `SubgraphSource`, `SubgraphInput`, `SubgraphPanel` and
`SubgraphPort` in `bartlebot/bot/schema.py`.

## A definition is a node type the user made

A **definition** is one file in the subgraph library: states, internal transitions and
regions, every id local to the file and every position relative to `(0, 0)`. Its
**inputs** are its params. Its **ports** are reserved nodes in its body,
`subgraph_entry` and `subgraph_exit`, because an FSM edge lands on a state and not on a
pin; they are never composed into a bot and never offered by the node browser. Its
**panels** are headings on the call node's page: a group of inputs, or one member exposed
whole, so a recordable node keeps its Record button inside a subgraph.

An **instance** is an ordinary `RegionModel` in a bot whose `source` names a definition,
and whose members are ordinary `StateModel`s composed from it. The editor draws the whole
instance as one call node. The FSM engine, `to_machine_config`, combat assignments and
the run highlight all see plain states, wired straight to the members: which port each
edge crosses is recorded in `source.ports`, so a later definition change moves the bot's
edges with the port.

- **Roles are bot-owned.** A definition has no `initial` or `recovery_state`. A role on
  an instance sits on one of its entry ports.
- **A region without `source` is unchanged.** Every field added for subgraphs defaults
  to `None` or empty and is left out of the file, so a bot that uses none is written
  exactly as before. `test_opening_and_saving_a_bot_changes_nothing_in_the_file` is the
  guard.

## An instance sets inputs and nothing else

`source.inputs` maps an input's name to its value, and an exposed member's params as
`<member>.<key>`. A member param is the bound input's value when the instance set one,
and the definition's value otherwise. To change anything else about an instance, detach
it: its members become the bot's own.

- **An unset input leaves the definition's value alone.** Absence or `None` is unset; a
  stored empty value is a choice, the same rule as bot option overrides. An input's
  `default` prefills a fresh instance and is not applied during composition.
- **An input's type is a `ParamField` type name**, the narrowest of its bound params'
  types, so its value is valid for every param it feeds. What the type owns (range,
  placeholder, widget) comes from the bound fields when the form is drawn.
- **A binding names a member's param, or an input of a nested instance**, so a value
  goes into a nested subgraph through its signature and never past it.
- **Layout is the definition's.** `source.anchor` is the call node's position, and the
  members sit at `anchor + definition position`.

## Member ids are derived, deterministic, and never recycled

A member's id is `<region id>_<local id>`, charset `[A-Za-z0-9_]`, where the local id is
the state's id inside the definition. A linked region's id is minted from the
definition's name: `<slug>_<n>`, where the slug is the name lowercased with runs of
anything else collapsed to `_` ("Sync players" → `sync_players`) and `n` is one above
every number that slug already carries in the bot, as a region id or at the head of a
state id (`mint_instance_id`). So the third state of the first "Sync players" instance
is `sync_players_1_state_3`, and the fourth state of the second is
`sync_players_2_state_4`. Extracting a region renumbers its local ids `state_1..N` in
file order, whatever numbers the bot had reached, so the same subgraph reads the same
in every bot it lands in. Composing the same instance twice yields the same ids; a
nested instance prefixes again (`<outer>_<inner>_<local>`).

Ids are never *parsed* to find structure. Whether one instance is nested in another
is a question of membership (every member of the inner is a member of the outer,
`is_nested_in`), so two definitions whose slugs share a prefix ("Farm" and "Farm 2")
cannot be mistaken for each other. `local_id` is only meaningful for a state that is a
member of the region it is asked about.

State ids are the key for everything that outlives an edit: a Bot Combat Strategy's
fight assignments, `initial`, `recovery_state`, the run highlight, and the reason
`EditScene._unique_id` never hands out an id it has handed out before. A definition
update has to land on the *same* ids or every one of those would silently detach. So
ids come from the definition, never from `_unique_id`, and pasting an instance mints a
new region id rather than new member ids.

Converting an existing region into an instance (*Save Region as Reusable Subgraph*)
rewrites its member ids to the derived form everywhere they appear — states,
transitions, roles, every region's `members` — and re-keys this bot's combat
assignments in the same step (`rekey_bot_strategy_states`). The app owns that store and
the bot's identity has not changed, so there is nothing for the user to redo.

## An instance follows its definition, and is never unloadable

The bot file stores a **snapshot**: the composed members, exactly as they would be
written for an ordinary region. On open and again before every run, the app reloads the
library and recomposes each instance from the current definition. If the result
differs, the in-memory config is swapped, the bot is marked unsaved, and one log line
per changed region says so. The file is **not** rewritten on load — the same rule as
bot options — and the refreshed snapshot reaches disk the next time the user saves.

The snapshot is what keeps a bot loadable when the library disagrees with it. Each
linked region composes to one of five states, reported per region and shown in the
validation banner:

| status | meaning | what runs |
|---|---|---|
| `linked` | composed from the current definition | the current definition |
| `held` | the update removes a port or member this instance's edges or roles use, or leaves an exit with nothing leading into it | the snapshot, with a warning naming each reason |
| `missing` | no definition of that id under either root | the snapshot, with a warning |
| `invalid` | the definition file exists but fails validation | the snapshot, with a warning |
| `cycle` | the definition contains itself, transitively | nothing — an error that disables Run |

A `held` region is the user's to resolve: rewire the bot off the vanishing port, or
detach. The app never guesses. `missing` and `invalid` are warnings rather than errors
because the snapshot is a complete, valid graph and the library is the user's to fix; a
definition removed from the user root while an Official one of the same id exists simply
reverts to the Official one.

A `cycle` is an error rather than a warning because there is no snapshot to fall back
on that is not itself the product of the cycle. Nesting is depth-limited (`MAX_DEPTH`,
8) as a second guard, and saving a definition that would contain its own id is refused,
so a cycle in the library means a hand-edited file.

The pre-run recompose is silent — one log line per changed region and a banner entry,
never a dialog — because a dialog on every run of every bot is noise the user learns to
click through. The run itself is what tests the update.

Where this happens: `AppController._compose` reloads the session's `SubgraphLibrary`
and composes (`compose_bot`); `_open_bot` and crash recovery call it after
`BotConfig.load`, and `_recompose_before_run` calls it first in the run slot, ahead of
the restricted-zone check. A changed result goes in through `BotCanvas.replace_config`,
the same rebind path undo takes, and the report through `BotCanvas.set_compose_report`,
which feeds the validation banner. Composition raising is a bug, not a library problem,
so `_compose` logs it and opens the file's snapshot rather than refusing the bot.

## Authoring: a definition is its own document

A definition is edited as a document of its own, the way a bot is, never inside a bot
that uses it (`bartlebot/gui/CLAUDE.md`, *A definition is a document of its own*). The
verbs:

| verb | on | what it does |
|---|---|---|
| **Save Region as Reusable Subgraph…** | a plain region | extracts a definition (version 1) to the user root, its ports inferred from the edges crossing the region (`boundary_ports`), and converts the region into an instance of it |
| **Open Definition** (or double-click the call node, or its card in the library's Subgraphs folder) | an instance | sets the bot aside and opens the definition; going back recomposes the bot from the library |
| **Save**, **Save as…**, **Revert** (the definition's crumb) | the open definition | Save writes `version + 1` and warns about a signature change (below); Save as makes a copy at a new id and version 1; Revert, on a modified Official one only, deletes the user copy |
| **Detach from Library** | an instance | drops `source`; members and ids stay exactly as they are, and become the bot's own |
| **Insert** (node browser, Subgraphs tab) | the canvas | mints a region id, places the instance at the click as `anchor`, prefills inputs from their defaults |

The signature — inputs, panels, ports — is edited on the definition's page with nothing
selected (`bartlebot/gui/CLAUDE.md`, *The signature page is a list with a detail
editor*). An input bound to nothing is an error that blocks saving
(`definition_problems`), but never one that stops a definition *file* loading.

**Saving an Official definition makes a user override**, not a fork, keyed by the same
id (`SHIPPED_OVERRIDE`), like an Official strategy edited in the dialog. Every bot that
links the id follows the override, and Revert is the delete that undoes it.

## Two roots, one `Origin`

Definitions live under `subgraphs/` in the same ordered pair of roots as combat
strategies (`docs/COMBAT_CONFIG.md`): the install directory ships them and is never
written; the product directory under `%APPDATA%/Bartleby/BartleBot` takes every
app-initiated write and shadows a shipped file of the same id.
`bartlebot/content/subgraphs/subgraph_store.py` mirrors `strategy_store.py` function for
function, and the `Origin` enum (`SHIPPED`, `SHIPPED_OVERRIDE`, `USER`, `MISSING`) is the
one from the strategy store, not a copy, so the app can say honestly whether removing a
definition deletes or reverts it. A definition's file is named after it
(`subgraphs/sync-players.json`) and found by the id inside, like a strategy's —
"Filenames are names, ids are inside" in `COMBAT_CONFIG.md`.

A definition leaves the library from its card in the Bot Library's Subgraphs folder
(right-click, #161), and the menu says which of the three it is: *Delete Subgraph…* on a
custom one, *Revert to Official…* on a modified Official one, and a grayed-out Delete on an
unedited Official one, which has no user file to remove. `AppController._delete_subgraph`
asks first, naming the bots and the other definitions that link it — they are not
broken, since each keeps its snapshot and composes as `missing`, but none gets another
update — and refuses while the definition is open as a document. With `BARTLEBOT_DEV`
set the roots are one directory, so every definition is custom and Delete removes the
repo's file, which git then shows.

A definition is renamed two ways (#160): *Rename…* on the same card menu, which edits the
name and description in a dialog (`AppController._rename_subgraph`, refused while the
definition is open), or the open document's header name in Edit mode, then Save.
Either writes at the version it has: `definition_change` calls a changed name or
description `"meta"`, which, like `"layout"`, reaches no bot's run — a bot links the
id. The file moves to the new name's slug, since the filename is a name. Nothing that
links it follows the rename: an instance's label is the bot's own, set from the name
when it was placed, and its call node page shows the new name beside it; its region id
keeps the old slug too, which is never parsed. Rename is the user's own definitions
only. An Official one's name is the shipped file's, so the card's item is grayed out and the
header's name stays a label in Edit mode, modified or not; it is renamed with
`BARTLEBOT_DEV` set, where every definition is the user's.

A shipped definition is authored with `BARTLEBOT_DEV` set, which makes the project root
the user root as well: one copy, written where git sees it. Authoring without it is how
the first shipped definitions were lost — saved to `%APPDATA%`, where they shadowed the
repo and never reached it. `test_every_linked_bot_resolves_under_the_shipped_root_alone`
is the guard.

`SubgraphLibrary` is the session's view of the two roots: one per app session, reloaded
on open and before run, cached between. Pure functions receive it; tests build one on a
`tmp_path`. An unloadable file is logged and treated as missing rather than raising,
because one broken definition must not make every bot that links a different one
unopenable.

### A modified Official definition says when the Official one changes

An override hides every later change to the Official definition under it, so the save
that first makes one records `official_sha256`, the content hash of the Official
definition it was made from (`bartlebot/shared/content_hash.py`, #169), and every later
save keeps it. When the Official one stops hashing to it, after an app update,
`SubgraphLibrary.official_updated()` says so, and the pill reads **Official (modified) ·
Official updated** on the Subgraphs folder card, which puts a *Revert…* button beside it,
on the node browser's page, and in the open definition's header, which offers Revert in
Edit mode. Revert is the same ask-then-delete as the card's menu: the user's changes go,
and every linking bot follows the new Official one. Comparing the two first can come
later. An override saved before this has no base, and is never said to be out of date.

Every definition also keeps a `history`: the content hashes of what earlier saves
replaced, newest first, each once, at most 64, left out of the file while empty. The
store records it from the file the save replaces, or, on an override's first save, from
the Official one, so an unchanged save adds nothing and the document never has to carry
it. The hash is of content, the definition dumped as its file would be with keys sorted
and `history` and `official_sha256` left out; `definition_change` ignores the two fields
too, so a recorded history never reads as an edit. Import (#171) reads the history to
tell a newer copy of a definition from a separate edit of it.

## Categories, and finding a subgraph

Once users make and share subgraphs, a flat list stops working, so a definition says what
it does with a **category** (#172), and every list of subgraphs filters by it. It has the
shape `bartlebot/combat/strategy_tags.py` gave combat strategies: a stored group with a fixed
vocabulary, normalized on load, beside a **Source** group (Official, Official (modified),
Custom) worked out from where the file is and never stored. `bartlebot/content/subgraphs/subgraph_tags.py`
holds the vocabulary and the rule.

| category | for a subgraph that |
|---|---|
| Zone Entry | gets into a dungeon, a gauntlet or a zone, by its door or an object |
| Teleportation | moves across a world: to coordinates, a marked location, a friend, a hub |
| Team | brings several players to one zone, realm and area |
| Combat and Healing | runs a fight and recovers after it: potions, waiting for health and mana, wisps |
| Interaction | talks to an NPC, clicks through a menu, collects reagents |
| Control Flow | chooses the next step: checks on health, zone or scaling, variables, branches |
| Utility | does anything else: applying level scaling, selling, relogging, changing realm, waiting |

The words are the node browser's own tabs wherever one fits, since a subgraph is a node the
user made and is added from that dialog. **Team** is the one it lacks: a node never brings
players together alone, and the one shipped definition, Sync players, does exactly that. It
was chosen with one subgraph shipped and none shared yet, so expect it to grow.

A definition may carry several. They are stored as `tags` in its file, after `version`,
and left out while empty, so a definition never given one is written exactly as before. A
word outside the vocabulary, a Source word included, is dropped on load with a warning, as
an unknown bot tag is. In the open document they ride on `DefinitionMeta.tags`, because the
document's own `tags` are bot tags and validation would drop any other word; Bar B's tag
row shows them in place of a bot's, with one *Category* picker in Edit mode, beside where
the definition comes from. *Save Region as Reusable Subgraph* and the crumb's *Save as…*
ask for them too, beside the name and description, the second starting from the
original's (`SubgraphMetaDialog`'s Category row); the library's Rename asks only for the
name and description. A category change is `"meta"` to `definition_change`, like a
rename: it keeps the version and reaches no bot's run. It does change the content hash, so
it is recorded in the history and travels in a share file.

Two lists filter by them, with the strategy dialog's rule: **OR within a group, AND across
groups**, so "Team or Combat, and Custom".

- **The Bot Library's Subgraphs folder.** Its bar shows a *Category* picker in place of
  *Rewards* and *Activities*, which a subgraph never carries, and keeps *Source*, which
  both lists share. A bot tag left selected stays selected, out of sight, and its chip
  comes back with the bots; it is not applied to subgraphs, which would always come up
  empty. Each card wears its categories, and the search box matches them.
- **The node browser's Subgraphs tab.** *Category* and *Source* sit beside the search box
  on that tab only, and a search's subgraphs are filtered by them too. The detail page
  shows the categories beside the Source pill.

**Adding a category** is a code change, for the reason it is for bot tags: a library shared
between users that drifts between spellings cannot be filtered.

1. Add the word to `SUBGRAPH_CATEGORIES` in `bartlebot/content/subgraphs/subgraph_tags.py`, where its place
   in the tuple is its place in every picker and in every file. Give it a one-line comment
   saying what belongs in it.
2. Keep it apart from the bot tags and the Source words:
   `test_no_category_is_also_a_bot_tag_or_a_source_word` fails otherwise, because a shared
   word would put a bot tag's medallion on a subgraph card.
3. If it is not one of the node browser's tabs, add it to the set
   `test_the_vocabulary_is_the_node_browsers_words_and_team` expects, and say why here.
4. Add a row to the table above. Nothing is migrated: removing or renaming a word drops it
   from every file that carries it on the next load, so never do either to a released one.

## Changing a signature warns

A definition's signature is what a bot linking it depends on: its inputs, whose values an
instance stores by name, and its ports, which the bot's edges attach to. Saving a
definition (`AppController._save_definition`) warns, naming every bot and definition that
links it, when the save:

| change | why it can break a bot |
|---|---|
| removes an input, or stops exposing a node | a value the bot set for it stops applying |
| renames an input: its `name`, not its label | the bot's value is stored under the old name |
| adds an input with no default | an instance placed before it has no value for it |
| removes an Entry or Exit | the bot's edges to it have nowhere to go |
| renames an Entry or Exit: its label | an edge drawn to "Done" now leaves "Failed" |

`bartlebot/content/subgraphs/subgraph_signature.py` finds them. A rename is told from a removal by an added
input driving the same params. Nothing else warns: a new label on an input, a moved node,
a member's param, a new input with a default, a new port, a name or description. Save is
the default, and Cancel writes nothing. It is a warning, not a refusal, the rule Home
Assistant's blueprint guidance gives for the same reason: a bot that cannot absorb the
change is `held` on its snapshot (see *An instance follows its definition*), so nothing
stops running. Nothing linking the definition means nothing to warn about, so a
definition being authored saves without the question.

## What the rest of the app does with an instance

| surface | behavior |
|---|---|
| **Undo** | every mutation goes through `_pre_edit`; `source`, `anchor`, inputs and ports round-trip `model_dump()` / `model_validate()` like every other field |
| **Autosave and crash recovery** | write and read the in-memory config, which is the composed one; recovery composes again on load like open does |
| **Save As** | mints a new bot id only; member ids and every `source` are untouched, so the copy still follows the same definitions |
| **Copy and paste** | a whole instance pastes as a new instance: a fresh region id, the same inputs, members composed from the library, or from the clipboard's states flagged `missing` when the library has no such definition. Part of an instance pastes as plain states |
| **Validation banner** | `cycle` is an error line and disables Run; `held`, `missing` and `invalid` are warning lines |
| **Link badge** | a two-squares glyph in the call node's corner marks an instance; its dot is the state — none for linked, warning for outdated (`held`), error for missing (`missing`, `invalid`, `cycle`), accent while its definition is open. `EditScene.refresh_region_states(report)` sets it from the last compose report; a linked region the report does not mention reads as linked |
| **Combat assignments** | keyed by member id and by enemy, so they survive updates; re-keyed once on conversion |
| **`flush_regions`** | never auto-deletes a region with `source`, even with one member |
| **Nesting** | an instance inside a definition is stored the way a bot stores one, its members as a snapshot and its region carrying `source`; compose recurses |
| **Node browser** | a *Subgraphs* tab whenever the dialog is given the library and no port filter; items are `subgraph:<id>` keys that `EditScene.create_state_at` routes to `insert_subgraph_instance`; the detail pane shows inputs, origin and *Used by N bots* from a raw-JSON scan of the bot library, once per dialog |
| **Export** (#170) | a bot's share file packs every Custom definition its instances link, nested ones included, and names every Official one; a definition exports alone as `.bbsubgraph` from its Subgraphs card or File › Export…. A modified Official definition is packed as a copy, and one missing from the library is left out, since the bot carries its snapshot. `docs/SHARING.md` |
