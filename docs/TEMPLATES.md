# Templates

A template is where a new bot starts: Boss Farmer, Dungeon Farmer and the rest. This file
covers where templates live, how a version is released, what a bot made from one is asked
to fill in, and how a later release reaches that bot. The surfaces — the New Bot dialog,
the Setup tab, Bar B's template chip — are documented with the rest of the GUI in
`bartlebot/gui/CLAUDE.md`.

## Fork, not link

**A template is copied into the bot; a subgraph stays linked to its definition.** That is
the test for what goes where. A change that would be a bug fix belongs in a subgraph
definition, because every bot follows it. A template change — a node added, an edge
rewired outside a subgraph — is structural, a link cannot absorb it, and it reaches a bot
only when a person reviews it. A review reaches only the users who act on it.

## Terms

| term | meaning |
|---|---|
| template | a starting point for a new bot. One id, one draft, any number of released versions. |
| draft | its author's working copy, `draft.json`. Never offered by New Bot. |
| version | an immutable copy of the draft, written only by Release, numbered 1, 2, 3… |
| base | the version a bot was made from, or last finished reviewing against: `template.version` in the bot file. |
| latest | the highest released version. |
| template-owned node | a node whose id starts with one of the template's prefixes: `t_`, or a fork's (below). Only a draft mints these. |
| fork | a template made by Duplicate from another, recording `forked_from`. |
| setup step | one instruction a template gives the user, with the fields it asks them to fill. |
| fill-in | a field a setup step targets. Blank in every released version. |
| unit, change | a unit is the smallest thing the update diff compares; a change is the group of units applied or skipped together. |

## The store

```
templates/                 beside subgraphs/ and strategies/
  boss-farmer/             named by store_roots.slugify; cosmetic
    template.json          the index: the template's id, every released version, and a
                           name a rename gave it since the last release
    draft.json             a BotConfig with template_meta
    v1.json                a released copy of the draft, never written again
```

`bartlebot/template_store.py` is the whole of it, Qt-free. A template is found by the id
in its `template.json`, which the draft and every version carry as their own `id`, so a
rename never moves a folder.

`templates/` is read under both roots (`store_roots.resolve_roots`, #168): the Official
templates shipped in the install directory, and the user's own under
`%APPDATA%/Bartleby/BartleBot`. Each `Template` carries its `origin`.

- **`save_draft` and `release` write only to the user root.** An Official template's draft
  is never saved (`PermissionError`) and Release refuses it: only an app release changes
  one.
- **A template is copied, so it never overrides.** A user-root folder declaring an Official
  template's id is ignored with a warning, and the Official one is used. Customizing an
  Official template is a fork with a new id: see "User templates" below.
- **Everything that reads templates goes through the store**, so `setup_steps.steps_for`,
  `template_diff.load_update`, `template_diff.latest_versions` and
  `new_bot.starting_points` see both roots. Their caches key on both folders.
- With `BARTLEBOT_DEV` set the two roots are one directory, the repo's, and every template
  reads as the user's; that is how the maintainer authors the shipped ones.

`test_templates_shipped` guards what ships: every index entry's file exists and still has
its recorded hash, and every shipped bot made from a template resolves against the shipped
templates alone.

Two fields on `BotConfig` carry the rest, both `None` by default so they stay out of every
file that does not use them:

- `template: {id, version}` is set only on a bot made from a template. **It is written in
  exactly two places: New Bot and Finish review.** Never on load, never by a migration.
  Save As copies it unchanged, since a copy of a bot has the same base, and so does Make
  my own copy.
- `template_meta: {blurb, setup, id_prefixes, forked_from}` is set only on a draft or a
  version, never on a bot. The last two are a fork's (below), and stay out of the file
  for a template that has neither, the six made before forks among them.

`templates/**` is `-text` in `.gitattributes`, and a template's files are written with LF
on every platform: a released file is hashed, so its bytes must not move.

### Template-owned ids, and a fork's prefixes

A draft mints ids under its template's own prefix (`t_4`, `t_sync_players_1`); a bot
mints `state_N` and unprefixed instance ids. So a node a later version adds can never take
an id a bot already has. The prefix comes from the open document: the scene mints the
template's own prefix (`template_store.own_id_prefix`) exactly when the config has
`template_meta`. An id a version used and a later one retired is never minted again
(`template_store.used_ids`), because a bot based on the earlier version still has a node
under it.

**Which prefix** (#164):

- The six templates made before forks, the shipped ones, record none and mint `t_`.
- A template made in the app, blank or by Duplicate, mints `t<first 6 hex of its id>_`
  (`template_store.fork_prefix`, eight characters, never `t_`).
- A fork also keeps every prefix it inherited, because its draft starts as a copy of its
  source's nodes. `template_meta.id_prefixes` lists them, its own first: a fork of a fork
  of Boss Farmer has three. `template_store.id_prefixes` reads them, and `t_` alone when
  there are none.

So nothing a fork adds can take an id its source has or could add later, and a fork of a
fork none of its parent's. That is what keeps pulling a later Official release into a fork
possible after 1.0.0. The prefixes are stored, not worked out from where the template is,
because developer mode makes every template read as the user's.

"Template-owned" means *under any prefix in the lineage*, everywhere: Release accepts any
of them, and the update diff (below) takes its versions' prefixes, since the bot carries no
`template_meta` to say.

## Authoring and release

**File › Templates…**'s Edit opens a draft as a top-level document, in Edit mode, the way
a bot opens: the user's own templates always, and an Official one only with
`BARTLEBOT_DEV` set, where every template is the user's anyway. Open Bot refuses an
Official draft for the same reason, pointing at Duplicate. A draft saves to `draft.json`;
autosave and crash recovery work unchanged.

**Release is the only way a version is written.** No save produces one, so a draft cannot
reach users by accident. `template_store.release` copies the saved draft's bytes to
`v(N+1).json` and appends the version, its SHA-256 and a one-line note to the index, or
writes nothing and raises `ReleaseRefused`. It refuses a draft with any canvas error, an id
under no prefix of its lineage, a fork's id under an inherited prefix that the version it
was duplicated from lacks, a retired id reused, a filled fill-in, a step with no text or no
targets, or a blank note. It also refuses while a `vN.json` for the next number is already on disk and
the index does not list it, which is what a release that failed between its two writes
leaves: nothing under `templates/` is ever overwritten, so the stray file is removed by
hand first. Release a version with the app's button or with `release`, never by copying a
file.

## User templates

Every user makes templates of their own (#164) in **File › Templates…**
(`gui/templates_dialog.py`, logic in `bartlebot/template_gallery.py`). It is a gallery of
New Bot's cards, one per template, each with its Source pill and a last line giving its
latest version, how many of the user's bots were made from it, and the template it was
copied from. A Source filter sits beside the heading. The verbs are a footer row and a
card's right-click menu:

| verb | Official | Custom |
|---|---|---|
| Edit (opens the draft; the commit, and a double-click) | dev mode only | yes |
| Duplicate | yes | yes |
| Rename | no | yes |
| Delete | no | yes, naming the bots made from it |
| New | a blank template | |

**A user template has the whole lifecycle** of an Official one: a draft, Release with its
refusals and a required note, and versions that update the bots made from them through
the same review. It lives under the user root.

- **New** starts a blank draft with no nodes, as a blank bot has none, minting under its
  own prefix. Nothing is offered by New Bot until its first release.
- **Duplicate makes a fork**: a new id, a draft seeded from the source's latest released
  version, and `template_meta.forked_from: {id, version}` saying which. The fork's
  `id_prefixes` are its own and then the source's. A copy of the user's own template
  before its first release seeds from its draft and records no `forked_from`, having no
  released base. A same-id override was rejected: a bot's `{id, version: 2}` could then
  mean the user's v2 or the Official v2.
- **Rename** writes the draft's name, and the index's `name` while the latest release
  still carries the old one; Release clears it. A name typed into the draft itself is not
  public until released, so a draft save never writes the index, and a shipped
  `template.json` stays byte-identical. A rename of the open draft renames the document
  too, so its next save keeps it. With one root, as in developer mode, a template with a
  release renames its draft alone, since a name in that index would ship with no release
  behind it (`template_store.rename_waits_for_release`). Its card keeps the released name
  and adds "renamed to … in draft" until the next release carries it.
- **No two templates share a name**, compared without case, Official and Custom alike,
  since New Bot lists both; a name a draft carries counts as well as the released one.
  New, Duplicate and Rename ask again with what was typed, and saving a draft renamed in
  its header to a taken name is refused (`template_store.name_clash`). The check runs only
  when the name changes, so two templates that shared one before it still save.
- **Delete** removes the folder: draft and every version. The bots made from it keep
  running, without setup steps or update reviews, as a bot shared without its template
  does. An open draft of it is closed first. An Official template is never deleted, and
  with one root, as in developer mode, neither is one with a release, since that folder
  is what ships (`template_store.deletion_refused`).

## New Bot

The New Bot dialog (`gui/new_bot_dialog.py`, logic in `bartlebot/new_bot.py`) offers a
blank bot and one card per template, built from its latest released version. Create copies
that version, drops `template_meta`, mints a fresh id, records `template`, composes from
the subgraph library, and saves the file at once. The setup steps are not copied into the
bot: it reads them from its base version. Nor are the template's `authors`: the dialog has
an optional Authors field, blank whatever the starting point, and the bot gets what was
typed there or nothing.

A blank bot has no nodes at all, and so no `initial` or `recovery_state`: it is the one
config that loads with both empty. It cannot be run, and the first node put on its canvas
takes both roles (`EditScene.settle_roles`), on an entry port when that node is a subgraph
node.

## Setup steps

`template_meta.setup` is a list of steps, each with text and targets. A target is one of
three kinds: a node param, a subgraph node's input, or the bot's `drops`.

- **Every target is a fill-in, and nothing else marks one.**
- **A fill-in is blank in every released version.** So "done" means "valid", and a
  placeholder can never run as a real value. Blank is the type's own emptiness
  (`setup_steps.field_empty`).
- **In a bot, a blank fill-in is an error on its node**, "Setup: <step text>", whether or
  not the param is required on its own. Errors block Run. In a draft the same blank raises
  nothing, and a blank required param no step targets is an error.
- **Done is worked out, never stored.** A step is done when every target is filled and
  valid. A target whose node the user deleted is ignored, and a step with none left is not
  listed.
- **A subgraph input is judged by the value it resolves to, everywhere.** The editor, the
  library row and Release all read the composed member the input drives, through the
  subgraph library, never the value the subgraph node stores. An input left unset still
  runs with its default, so a *named* input whose default is not blank cannot be a
  fill-in, and Release says so. A promoted param (`state_2.zone`) names its member itself.
- **Save As keeps the steps.** The copy has a new id and the same base, so the controller
  reads its template context again (`_refresh_template_context`).

## Updates

When a template releases a version newer than a bot's base, the bot is offered a review.
**Except an Official bot**, which is read-only and gets its updates from the app (#149):
`_refresh_template_context` hands the canvas no update for one, and its library row never
says one is available. Developer mode never marks a bot Official, so the maintainer still
reviews the shipped bots against a new release.
`bartlebot/template_diff.py` is the Qt-free half.

The diff compares three configs: **base**, **theirs** (the latest version) and **mine**
(the open bot). Each becomes a set of units:

| unit | key | value |
|---|---|---|
| a template-owned node | its id, under any prefix in the template's lineage | its `node_type` |
| a param, label or stop list of one | (id, key) | the value |
| an edge slot from one | (source, `when`, canonical flags) | `dest` |
| a template-owned subgraph node | its region id | `subgraph_id` |
| an input on one | (region id, input name) | the value |
| an edge into or out of one | normalized to (region id, port id) | as an edge slot |
| an option declaration | option name | the declaration |
| a role, and `clients` | `initial`, `recovery_state`, `clients` | the value |

**Never units:** positions; name, description, tags and authors; plain regions; `drops`;
the members inside a subgraph node, and its `version` and snapshot, which follow their
definition; every fill-in the base or the latest version names; and anything on a node the
bot itself added.

| base → theirs | mine | outcome |
|---|---|---|
| unchanged | anything | not shown |
| changed | equals theirs | hidden: the bot already matches |
| changed | equals base | **offered** |
| changed | differs from both | **conflict**: use the template's, or keep mine |
| changed | its node was deleted by the user | hidden |
| changed | needs a node or port the bot lacks | **can't apply**, with the reason |
| removed | the bot wired edges of its own to it | **conflict**: applying drops them, and the change names the nodes left to rewire |

The last row covers what no unit holds. An edge out of a node the bot added is never a
unit, and an edge the bot wired into a slot the template left empty is not one either,
because that slot is empty in both versions. A removal drops both kinds all the same.
Without the row, a release that replaces a node rather than keeping it strips the user's
own nodes out of the loop and calls the change clean. "The bot's own" means an edge base
does not have, compared the way slots are, ports and all.

- **Grouping.** A node added together with its params, its label, its own slots and every
  slot or role that now points at it is one change, and so is a removal. Added and removed
  nodes joined by an edge are one change. Every other unit is a change of its own.
- **Applying** is `template_diff.apply(config, change)`, pure, all of a change's units or
  none. Every change is trial-applied when the diff is built, so one that would not leave a
  valid bot is reported as can't-apply instead of failing later. An added node is placed
  beside a node it is wired to, as far from it as in the template. When that spot is taken
  by a node the added one leads on to, it is an insert between two nodes: the added node
  takes the spot, and that column and every column right of it move over by one, all of
  each (`template_diff._make_room`), which is what the editor's own insert on an edge does
  (`EditScene.insert_state_on_edge`). Taken by anything else, the editor moves the added
  node to a free grid cell (`EditScene.free_position`).
- **A change is found again by its id**, which spells out its unit's whole key, flag guard
  included: two guarded edges from one node on one outcome are two changes.
- **In the editor, each Apply is one undo step** (`BotCanvas.apply_template_change`). A
  change that takes a fight's duel strategy with it asks first, naming every combat
  profile of the bot that loses one, not only the active one; the check goes through
  `fight_keys`, so a fight replaced against the same enemy is not asked about.
- **Finish review advances the base** to the latest version (`finish_template_review`,
  through `template_diff.finish`), also one undo step. That is what remembers a skip: against the new base the template has
  not changed it. There is no ignore list. Nothing reaches the file until the bot is saved.
  **Later** leaves the base alone and puts the review away until the bot is next opened.
- **The newer version's setup steps come with the new base**, since a bot reads its steps
  from its base version.
- **Automatic re-application is out of scope.** Offering the untouched parts ready to apply
  gets most of the benefit; applying them unasked silently rewrites a bot when it guesses
  wrong.

Where it shows: the review is a section of the Setup tab, above the bot's steps; Bar B's
template chip reads "Boss Farmer v1 → v2"; and a library row says "Template update
available", read from the bot file and the store's index without opening the bot.

## Fight keys

A combat profile finds a fight by its enemy as well as by its state id, so a strategy
follows a fight a template update replaced. `docs/COMBAT_CONFIG.md` has it, under "Fight
keys".

## What must stay true

- Every bot file round-trips byte-identical, with `template` or without.
- Nothing writes `template`, rewrites a profile, or changes a bot file on load.
- A released version file never changes after release.
- A bot never mints a template-owned id, and a draft mints only under its own prefix.
- A fork never adds an id under a prefix it inherited.
- A user is only ever offered released versions.
