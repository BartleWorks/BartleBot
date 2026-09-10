# Reusable subgraphs

What a subgraph definition and an instance are, how an instance follows its definition,
where the three levels of a member's params come from, and what each authoring verb does.
The editor surfaces are documented with the rest of the canvas in `bartlebot/gui/CLAUDE.md`.

Designed for #37. Each stage is its own sub-issue and lands on `master` alone; a section
below says which stage implements it, and the table is updated as they merge.

| stage | issue | what lands | status |
|---|---|---|---|
| 0 | #54 | this document, the Epic decomposition | done |
| 1 | #55 | schema, two-root store, pure composition — nothing calls it | done |
| 2 | #56 | compose on open and before run; snapshot on save; validation flags | done |
| 3 | #57 | editor read side: locking, group drag, pill state, region page, detach, copy/paste | done |
| 4 | #58 | authoring: save-region-as, edit/push/discard, inputs, node-browser tab, nesting | done |

Extracting the library's repeated regions ("Sync players" and its kin) into shipped
definitions and migrating `bots/` is #59, an `area/bot-library` task that depends on
Stage 4. It is deliberately not part of #37.

## An instance is a region, and the engine does not know it exists

A subgraph **definition** is a file holding states, internal transitions and (for
nesting) regions, with every id local to the file and every position relative to `(0, 0)`.
An **instance** is an ordinary `RegionModel` in a bot whose `source` field names a
definition, and whose members are ordinary `StateModel`s composed from it. The FSM
engine, `to_machine_config`, combat assignments, roles and the run highlight all see
plain states, exactly as they see them today.

Why a region and not a composite node: the "Sync players" region in the boss template
has five external edges entering at two different members, and it holds both `initial`
and `recovery_state`. A node with one entry port cannot represent what the library
already contains, so the composite-node model was rejected before it was designed. This
is the linked-instance model of Figma components and Simulink library links: the copy
is real, the link says where it came from, and updates flow along the link.

Two consequences that are easy to forget:

- **Roles are bot-owned.** A definition has no `initial` or `recovery_state`. Which
  member is the initial state is a fact about this bot, so the checkbox stays enabled
  on a locked member (Stage 3) and a definition update that would remove the member
  holding a role is held rather than applied (see *An instance is never unloadable*).
- **A region without `source` is unchanged.** `source` defaults to `None`, and
  `exclude_none=True` in `BotConfig.save` writes nothing for it, so every file in
  `bots/` stays byte-identical.
  `test_opening_and_saving_a_bot_changes_nothing_in_the_file` is the guard, and every
  field added for this feature has to default to `None` or empty for the same reason.

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

State ids are the key for everything that outlives an edit: `BotCombatStrategy.
state_assignments`, `initial`, `recovery_state`, the run highlight, and the reason
`EditScene._unique_id` never hands out an id it has handed out before. A definition
update has to land on the *same* ids or every one of those would silently detach. So
ids come from the definition, never from `_unique_id`, and pasting an instance mints a
new region id rather than new member ids. `_unique_id` in turn counts the `state_N`
tail of a member id: a state added while editing an instance becomes local `state_N`
on push, so it must not take a number the definition already uses.

Converting an existing region into an instance rewrites its member ids to the derived
form everywhere they appear — states, transitions, roles, every region's `members` —
and re-keys this bot's combat assignments through `strategy_store` in the same step
(`rekey_bot_strategy_states`). The app owns that store and the bot's identity has not
changed, so there is nothing for the user to redo.

## An instance follows its definition, and is never unloadable

The bot file stores a **snapshot**: the composed members, exactly as they would be
written for an ordinary region. On open and again before every run, the app reloads the
library and recomposes each instance from the current definition. If the result
differs, the in-memory config is swapped, the bot is marked unsaved, and one log line
per changed region says so. The file is **not** rewritten on load — the same rule as
bot options — and the refreshed snapshot reaches disk the next time the user saves.

The snapshot is what keeps a bot loadable when the library disagrees with it. Each
linked region composes to one of five states, reported per region and shown in the
validation banner (Stage 2):

| status | meaning | what runs |
|---|---|---|
| `linked` | composed from the current definition | the current definition |
| `held` | the definition changed in a way this bot cannot absorb: it would remove the member holding `initial` or `recovery_state`, or the endpoint of an external edge | the snapshot, with a warning naming each offender |
| `missing` | no definition of that id under either root | the snapshot, with a warning |
| `invalid` | the definition file exists but fails validation | the snapshot, with a warning |
| `cycle` | the definition contains itself, transitively | nothing — an error that disables Run |

A `held` region is the user's to resolve: edit the bot so the role or edge moves off the
vanishing member, or detach. The app never guesses. `missing` and `invalid` are
warnings rather than errors because the snapshot is a complete, valid graph and the
library is the user's to fix; a definition removed from the user root while a shipped
one of the same id exists simply reverts to the shipped one.

A `cycle` is an error rather than a warning because there is no snapshot to fall back
on that is not itself the product of the cycle. Nesting is depth-limited (`MAX_DEPTH`,
8) as a second guard, and pushing a definition that would contain its own id is refused
at the push (`contains_subgraph`), so a cycle in the library means a hand-edited file.

The pre-run recompose is silent — one log line per changed region and a banner entry,
never a dialog — because a dialog on every run of every bot is noise the user learns to
click through. The run itself is what tests the update.

Where this happens: `AppController._compose` reloads the session's `SubgraphLibrary`
and composes; `_open_bot` and crash recovery call it after `BotConfig.load`, and
`_recompose_before_run` calls it first in the run slot, ahead of the restricted-zone
check. A changed result goes in through `BotCanvas.replace_config`, the same rebind
path undo takes, and the report through `BotCanvas.set_compose_report`, which feeds the
validation banner. Composition raising is a bug, not a library problem, so `_compose`
logs it and opens the file's snapshot rather than refusing the bot.

Since Stage 3 the editor locks an instance's members (see *Authoring*), so the only
edits that reach a member outside an override are the ones the next compose would
make anyway. `flush_regions` leaves a linked region alone when a definition update
takes it below two members, so the region itself survives the round trip.

## A member's params come from three levels

A definition ships full param values, so a situation-specific subgraph ("apply level
scaling in this world") works out of the box. A generic subgraph ("sync players in a
world hub") **declares inputs**: named, typed values bound to one or more member
params, set per instance. Any instance may also **override** one member param outright,
tracked as a diff with a reset. Resolution for one param on one member:

```
instance param override  ->  bound input, if the instance set it  ->  definition value
```

Why three levels and not one: inputs alone would make the author name every knob up
front, and every user of a shipped subgraph would be blocked on the author to expose
one more. Overrides alone would make the common case ("set the world") a scavenger
hunt across seven members. Shipped values alone would make a generic subgraph
impossible. Each level answers a different person's question — the author's default,
the author's intended knobs, and this user's exception.

Three things that are load-bearing:

- **An unset input leaves the definition value alone.** An input whose value is
  `None` in `source.inputs` has not been set, and the member keeps what the definition
  shipped. Absence is unset; a stored empty value is a choice — the same rule as bot
  option overrides.
- **A stale override is kept and warned, never dropped.** A definition update may
  remove the state or the param an override names. There is no prune sweep, for the
  reason there is none for bot options: the value is the user's, the next update may
  bring the param back, and a dead row cannot affect a resolution. It is reported as a
  reason on a `linked` status.
- **Editing a subgraph shows definition values.** While an instance is being edited,
  its members carry the definition's params, not this instance's resolved ones,
  because what is on screen is what gets pushed (`definition_values`). The instance
  keeps its inputs and overrides untouched in `source`; the region page hides the form
  and the override list for the duration, and the compose after Save or Discard
  re-applies them.

An input's type is a `ParamField` type name, so the region page builds its form with the
same `_DynamicParamForm` that builds a node's. The schema does not validate a binding's
param key against the node registry, for the same reason `_validate_node` lives in the
scene and not in `schema.py`: the models stay registry-free.

## Layout is instance-owned

A definition stores positions relative to `(0, 0)`. An instance stores an `anchor` in
`source`, and each member keeps its own `position` in the bot file as any state does.
On compose, a member the instance already has keeps its stored position; a member new
to the instance is placed at `anchor + definition position`. Dragging the whole group
translates `anchor` by the same delta (Stage 3).

Following the definition's layout was rejected: the members in a bot sit among external
nodes that did not move, and reflowing every instance in eighty bots because one
author tidied a definition would undo eighty layouts at once. Layout is cosmetic and
local; behavior is shared. The same split as Figma's instance position versus the
component's internals.

## Two roots, one `Origin`

Definitions live under `subgraphs/` in the same ordered pair of roots as combat
strategies (`docs/COMBAT_CONFIG.md`): the project root ships them and is never written;
the product directory under `%APPDATA%/Bartleby/BartleBot` takes every app-initiated
write and shadows a shipped file of the same id. `bartlebot/subgraph_store.py` mirrors
`strategy_store.py` function for function, and the `Origin` enum (`SHIPPED`,
`SHIPPED_OVERRIDE`, `USER`, `MISSING`) is the one from the strategy store, not a copy,
so the region page can say honestly whether removing a definition deletes or reverts it.

`SubgraphLibrary` is the session's view of the two roots: one per app session, reloaded
on open and before run, cached between. Pure functions receive it; tests build one on a
`tmp_path`. An unloadable file is logged and treated as missing rather than raising,
because one broken definition must not make every bot that links a different one
unopenable.

## Authoring is edit in place, then push

There is no second document type. A definition is created from a region and edited
inside a bot that uses it, so a change is tested in a real graph before it ships to
the others. Simulink's "edit linked block, then push" is the model. The verbs:

| verb | on | what it does |
|---|---|---|
| **Save region as reusable subgraph…** | an ordinary region | extracts a definition (`version` 1) to the user root, converts the region into an instance of it, re-keys combat assignments |
| **Edit subgraph** | an instance | unlocks that one region's members, shows definition values, allows adding states and internal edges inside it |
| **Save to library** | the region being edited | pushes members, internal edges, nested instances and carried inputs back as `version + 1`, refuses a cycle, then recomposes every other instance of that id in this bot |
| **Discard** | the region being edited | recomposes the region from the library and clears the editing state |
| **Expose as input…** | a member param, while editing | binds it to an existing input of a compatible type or a new one; the current value becomes the input's default |
| **Detach from Library** | an instance | drops `source`; members and ids stay exactly as they are |
| **Remove Subgraph Instance…** | an instance | deletes every member and the region, after a confirm; orphaned roles are reassigned as on any delete |
| **Insert** (node browser, Subgraphs tab) | the canvas | mints a region id, places the instance at the click as `anchor`, prefills inputs from their defaults |

Three rules that fall out of it:

- **Outside an edit, an instance is locked as one unit.** Its members cannot be
  deleted or have their params changed except through an override, internal edges
  cannot be created or removed, and states cannot be added to or removed from the
  region. External edges to and from a member are always allowed — that is how an
  instance is wired in. The one lock query is `EditScene.is_locked(state_id)`; nothing
  keeps a second flag, so undo needs no lock bookkeeping and turning the query off
  restores today's editor exactly.

  What reads it (Stage 3): pressing a locked member selects the whole instance, so a
  drag moves the group and a same-place release puts the group back (Qt narrows a
  click inside a multi-selection to the clicked item, which is right for ordinary
  nodes and wrong for a unit). `Delete` skips locked members, and a selection that is
  exactly one instance offers *Remove Subgraph Instance…* instead. The node menu
  drops *Delete State*, the port inserts that would split an internal edge, and the
  add/remove entries for a locked region; the region menu drops *Add State Here* and
  *Remove Region* in favour of *Detach from Library* and *Remove Subgraph Instance…*.
  `RegionManager.add_to_region` / `remove_from_region` are no-ops on a locked region.
  A locked node draws a small padlock by its type sub-label; the tooltip names the
  subgraph.
- **Pushing a shipped definition makes a user override**, not a fork, keyed by the same
  id — `SHIPPED_OVERRIDE`, like a shipped strategy edited in the dialog. Every bot that
  links the id follows the override. "Revert to default" for one is the same delete
  the strategy store does.
- **The editing state is not in the bot config**, so it is not undoable. Done, Save,
  close and open all resolve an open edit first with a synchronous Save / Discard /
  Cancel prompt.

How the verbs are built (Stage 4): every one is a `BotCanvas` method, because the
library and the editing state live there, and the scene's menus and the region page
only *ask* (`save_region_as_subgraph_requested`, `edit_subgraph_requested`,
`expose_input_requested`). Each works on a deep copy of the config and lands through
`replace_config`, so it is one undo step. `begin_subgraph_edit` rewrites the members to
definition values under its own undo push and sets `EditScene.set_editing_region`, which
is what unlocks them — the lock query, not a second flag. `save_subgraph_to_library`
renames members added while editing to the derived form *before* composing
(`added_member_ids` + `rename_states`), since an external edge to a new member would
otherwise read as an update removing it and hold the region; then `push_back`, the
cycle check, `library.save`, and one `compose_bot` that also updates every sibling
instance in the bot. `discard_subgraph_edit` drops the added members (`drop_states`,
roles reassigned) and recomposes; members edited or deleted come back from the
library. The working definition's inputs are the only thing Expose and the region
page's inputs editor write to, and they reach the library with the push.

Two limits, deliberate: the region page's inputs editor removes inputs and bindings but
does not rename or re-describe one (Expose declares them, and a definition file is
plain JSON for anything else); and a pushed shipped definition becomes a
`SHIPPED_OVERRIDE` with no *Revert to default* surface yet — the strategy store has the
same delete-is-revert and the button belongs with a library dialog, which is #59's.

## The properties panel on an instance

Clicking one **member** shows that member's page with the definition's fields — label,
type, notes and every param — disabled, and the roles enabled, because the roles are the
bot's. Each param row carries an **Override** toggle: on records the current value in
`source.param_overrides` and unlocks that one field, so the next edit lands in both
`state.params` and the override; off drops the override and re-resolves the member from
the library, so the definition's value shows again. Recording the *current* value first,
rather than opening an empty field, means nothing visible changes until the user types —
an override is a diff from the definition, and the diff starts at zero.

Clicking the **pill** selects the whole region and shows the **region page**. Every
region gets it (label and colour, and the same goes for an ordinary region — one surface,
not two); a linked one adds the definition's name and description, `Version N` with the
library's version beside it when the update is held, where the definition comes from,
the **inputs** form, the **override list** with a Reset per entry, and **Detach from
library**. The inputs form is a `_DynamicParamForm` built from the definition's
`SubgraphInput`s; a change writes only the inputs the user touched into `source.inputs`
— a widget always holds *some* value, and writing every key would turn each untouched
input into an empty override of the definition — and then re-resolves every direct
member's params in place through `reresolve_member_params`, under the same debounced
undo step a param edit takes. A colour change is one immediate undo step, as a node-type
switch is.

Which page a whole-instance selection shows is decided by where the press landed:
`EditScene.pressed_member_id` is the locked member under the last press, or `None` for
a pill click. Both leave the same selection behind, and the selection alone cannot say
which the user meant.

## Copying an instance makes a new instance

Copying a whole instance and pasting it, in the same bot or another, mints a fresh
region id and composes the members from the library with the same inputs, overrides and
a translated `anchor`. The ids are derived from the new region id, never from
`_unique_id`. If the library has no definition of that id, the clipboard's states stand
in as the definition so the paste still works and the new region is flagged `missing`
like any other. Copying only some members of an instance pastes ordinary, unlinked
states — the same thing copy does with any partial selection today.

## What the rest of the app does with an instance

| surface | behavior |
|---|---|
| **Undo** | every mutation goes through `_pre_edit`; `source`, `anchor`, inputs and overrides round-trip `model_dump()` / `model_validate()` like every other field |
| **Autosave and crash recovery** | write and read the in-memory config, which is the composed one; recovery composes again on load like open does |
| **Save As** | mints a new bot id only; member ids and every `source` are untouched, so the fork still follows the same definitions |
| **Validation banner** | `cycle` is an error line and disables Run; `held`, `missing`, `invalid` and a stale override are warning lines |
| **Region pill** | a two-squares glyph marks an instance; its dot is the state — none for linked, warning for outdated (`held`), error for missing (`missing`, `invalid`, `cycle`), accent while editing. `EditScene.refresh_region_states(report)` sets it from the last compose report; a linked region the report does not mention reads as linked |
| **Combat assignments** | keyed by member id, so they survive updates; re-keyed once on conversion |
| **`flush_regions`** | never auto-deletes a region with `source`, even with one member |
| **Nesting** | an instance inserted while editing another becomes an inner linked region; compose recurses; push carries it in the definition's `regions` |
| **Node browser** | a *Subgraphs* tab whenever the dialog is given the library and no port filter; items are `subgraph:<id>` keys that `EditScene.create_state_at` routes to `insert_subgraph_instance`; the detail pane shows inputs, origin and *Used by N bots* from a raw-JSON scan of the bot library, once per dialog; Recent stays node types only |

## Out of scope

- Migrating the shipped library onto definitions (#59).
- A collapsed rendering of an instance. Members are drawn as they are today.
- #11 (default failure edge) and #12 (client subsets). Neither is a prerequisite:
  composed members are ordinary states, so both apply to them unchanged.
