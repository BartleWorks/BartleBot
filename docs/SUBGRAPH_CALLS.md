# Subgraph calls, the node audit, and the library rewrite

An Epic plan. It supersedes three decisions in `docs/SUBGRAPHS.md` — the composite-node
rejection, the three-level param model, and "no second document type" — and folds in #59
(extract shipped subgraphs and migrate the library) and #67 (collapse regions).

Nothing here relitigates the parts of #37 that are working: the linked-instance model,
composition into ordinary states, follow-latest with snapshot fallback, the two-root store,
derived member ids, instance-owned layout. Those stay exactly as they are. What changes is
how an instance is *drawn*, how its parameters are *named*, and where a definition is
*edited*.

Every count in this document is measured over `bots/`. The per-bot detail, the tier
breakdown and the decisions already taken on individual nodes live in
`docs/LIBRARY_MIGRATION.md`; this file cites it rather than repeating it.

| stage | issue | what lands | status |
|---|---|---|---|
| 0 | — | this document, the Epic decomposition, rewritten #59 and #67 bodies | — |
| 1 | — | one copy of a definition: shipped root, dev redirect, resolution test | done |
| 2 | — | the node audit, the retry-cap fixes, and the nine outlier bots | done |
| 3 | — | ports and the signature — schema, composition, validation; no UI | — |
| 4 | — | the param model collapses to a binary | — |
| 5 | #67 | the call node: collapsed rendering, run highlight, expand in place | — |
| 6 | — | the definition document | — |
| 7 | — | slots | — |
| 8 | — | the two shipped definitions, and the six templates rebuilt on them | — |
| 9 | #59 | the library rewrite: 82 bots re-authored from the templates | — |
| 10 | — | template provenance and the advisory diff | — |

Stage 10 depends on nothing here and can land at any point. **Stage 8 must precede Stage 9**,
and that ordering is the one thing in this plan that is not negotiable — see below.

## The library rewrite is the premise, not a consequence

The whole library is being re-authored from the templates for 1.0.0. That was not in this
plan's first draft, and it changes the economics of every stage.

`docs/LIBRARY_MIGRATION.md` has the measurement: 76 of 82 bots fall into four categories,
**51 of them are a template instantiation plus params**, and the entire surface a template
cannot express is ten nodes. So the templates are the unit of work, and the subgraphs are
whatever the templates are assembled from.

Three consequences, in order of how much work they remove:

- **The templates must be subgraph-based before bot #1 is re-authored.** If the rewrite lands
  first, all 82 bots get a copy-pasted Player Sync and Farming loop, and #59 then has to
  migrate 82 freshly written files. Stage 8 before Stage 9, always.
- **The `node_type` migration script is not needed.** An earlier draft budgeted "one script in
  the shape of `scripts/migrate_quicksell_options.py`, plus a review of the diff" for the
  renames. There is nothing to rename in a file that is being rewritten. The *audit* still
  has to land first, because it fixes the vocabulary the templates are built on — but it
  lands as a node change, not a corpus pass.
- **The `go_to_zone` sweep is subsumed.** 172 navigation nodes across the core four
  categories fold into `go_to_zone`, and the open question of "how far does the cleanup go"
  answers itself: all the way, because every rewritten bot starts with `go_to_zone`.

What the rewrite does *not* subsume is the corpus round-trip guard
(`test_opening_and_saving_a_bot_changes_nothing_in_the_file`). It stays green throughout, and
it is the check that a rewritten bot is a well-formed one.

## The premise that expired

`docs/SUBGRAPHS.md` rejected the composite-node model before it was designed:

> the "Sync players" region in the boss template has five external edges entering at two
> different members, and it holds both `initial` and `recovery_state`. A node with one entry
> port cannot represent what the library already contains.

An earlier draft of this document claimed that had expired, and measured
`bots/templates/boss_farmer_template.json` to prove it. That was the wrong artifact: the
template has no regions at all and never held the region being argued about. Measured against
the only real linked instance in the corpus, `foulgaze`'s, the original objection was still
true and then some — 6 members, two entry ports carrying six incoming edges, two exit ports,
and both role bindings.

What actually retires the objection is not a re-measurement, it is two decisions:

- **`foulgaze` is being rewritten, not migrated.** It exists mainly as the subgraph and
  player-sync experiment. Its shape is not a constraint on anything.
- **Every failure edge relogs.** See `docs/LIBRARY_MIGRATION.md`. The 219 explicit
  second-tier failure edges across 26 bots disappear, because all six templates already bind
  `recovery_state` to `relog` and #11 already routes an unrouted `FAILURE` there. That is
  edge *deletion*, and it takes 1-in/1-out regions from 83/133 to 102/133.

So the composite-node model is viable because the shapes that defeated it are being deleted,
not because they were mis-measured. That distinction matters for Stage 3, which is much
smaller as a result.

## Why an instance does not feel like a function

Because it is not a call. It is an **inlined macro expansion**: the body sits at the call
site, every member is individually clickable, and one subgraph's parameters are set in three
different places — the node page (free params), the region page (inputs), and a padlock
override (locked params). Every complaint about subgraph authoring traces back to that one
fact.

The evidence is in the corpus, in the two instances `foulgaze` carries. "Defeat boss" uses
**2 inputs, zero overrides, for 6 members** — which is what the model is supposed to look
like. "Sync players" uses **zero inputs, 3 param overrides and 4 label overrides** — which is
the scavenger hunt. Same feature, same bot, and the difference is whether the author declared
a signature.

The fix is to make the call a call.

| function concept | subgraph equivalent |
|---|---|
| signature | the definition's declared inputs, shown as the call node's params |
| body | hidden; double-click opens the definition document |
| entry points | declared local states; `initial` / `recovery` are dropdowns over them |
| exits | declared local states whose outgoing edges leave, one output port each |
| local variables | locked params — invisible, because they are the subgraph's business |

One caveat on the vocabulary, because it will otherwise mislead the UI work. A construct with
several *entry* points is not a function; it is a macro, or in statechart terms a submachine
state with entry-point pseudostates. Unreal Blueprint keeps Functions and Macros as separate
things for exactly this reason, and Node-RED refuses multi-input subflows outright. What is
being built here is closer to a **Simulink masked subsystem** — which is the model
`docs/SUBGRAPHS.md` already cites. "Call" is the right user-facing word; "function" is not the
right design word.

**Composition does not change.** Members still compose into real states in the bot file; the
FSM engine, `to_machine_config`, combat assignments, roles and the run highlight all still see
plain states, exactly as `docs/SUBGRAPHS.md` describes. What changes is the *rendering* and
the *edge attachment*: an edge drawn to port `P` on the call node becomes an edge to
`<region>_<P>` underneath. Stages 1 and 2 of #37 survive untouched. Most of Stage 3's work
(locking, group drag, pill state) becomes the expanded view rather than the only view.

## Stage 1 — one copy of a definition

First, because nothing else here is safe without it, and because the failure it describes has
already happened rather than being a risk.

`save_subgraph` writes to the user root only, and `load_subgraph` resolves user-first. So the
moment a definition is copied into the repo's shipped root, the `%APPDATA%` copy **shadows it
permanently** (`Origin.SHIPPED_OVERRIDE`, with no revert surface yet). Authoring continues
against the user copy, the app composes from the user copy, everything looks right, and the
repo's copy silently goes stale. The app cannot report this, because it never reads the
shipped file while a user file of that id exists.

The state today: three `subgraph_id`s are referenced by tracked bot files. **There is no
project-root `subgraphs/` directory at all**, so none resolve under a shipped root.
`foulgaze`'s two (`24556de8`, `201b8a96`) resolve under *no* root on the maintainer's machine
— both instances are `missing` and running from snapshot. `bots/subgraphs/sync_players.json`
references a third that exists only in `%APPDATA%`.

Since `foulgaze` is being rewritten, nothing needs re-extracting; the lost definitions are
not a data-recovery problem. What they are is proof that the shadowing is real.

The need is not a publish feature — users will never write to the shipped library. The need is
**not having two copies while authoring one**. So:

- `subgraphs/` at the project root, which does not exist today.
- A dev-mode root redirect — an env var or dev setting pointing the subgraph user root at the
  repo's `subgraphs/`. One copy, no shadowing, pushes land in git where they can be diffed and
  committed.
- A test asserting every `source.subgraph_id` in `bots/` resolves under the **shipped root
  alone**. That is the guard that catches a stale copy regardless of how it happened, and it is
  what makes a clone or CI reproduce the maintainer's graphs. It fails today, which is the
  point.

Nothing changes for a user: they write to their user root, as now.

**Landed.** The redirect is `BARTLEBOT_DEV`, and it lives in `store_roots.resolve_roots`
rather than at the subgraph call site, so combat strategies get it too — they had the same
bug, with three shipped duel strategies shadowed by newer `%APPDATA%` copies. `subgraphs/`
holds `sync_players`'s definition, moved out of `%APPDATA%`. `foulgaze`'s two regions were
unlinked rather than re-extracted; their members already carried the resolved values, so
the bot runs unchanged. The resolution test is
`test_every_linked_bot_resolves_under_the_shipped_root_alone`, and it passes.

## Stage 2 — the node audit

The audit fixes the vocabulary the templates are built on, so it lands before Stage 8. It is a
node change plus nine small bot edits — not a corpus pass, because Stage 9 rewrites the corpus
anyway.

Measured over `bots/` (82 bots, 31 of 34 registered types in use).

### Merge

| into | from | uses | why |
|---|---|---|---|
| `change_realm(use_favorite_realm, …)` | `change_to_favorite_realm`, `change_to_next_realm` | 13 + 9 | Two node types for one act, and node type is the one thing neither an input nor an override can bridge. It is what lets the 12 reagent bots and the other 67 share **one** Player Sync instead of two near-identical definitions. |
| `check_health_and_mana(health_threshold, mana_threshold)` | `check_health`, `check_mana` | 2 + 3 | Identical shape — one threshold, TRUE/FALSE — and they appear adjacent in the two bots that use them. Planned as `check_stat(stat, threshold)`; it landed with a threshold per stat instead, so a bot that cares about both needs one node rather than a chain, and a threshold of 0 switches that stat off. Its surviving job is narrow and real; see below. |

**`change_realm` needs no schema work.** An earlier draft worried that a three-way "mode"
choice would need `ParamField.enabled_by` extended, since that only gates on a bool. It does
not arise: `change_to_next_realm`'s `start_realm_page` and `end_realm_page` are **one value
across all 12 reagent bots**, have never been changed, and are not something an author should
be changing — so they are deleted outright. With them gone the merged node has no
mode-dependent params left to gate, and `use_favorite_realm` can be a plain bool.

**`check_health_and_mana`'s justification is the wisp detour, not its three former uses.** `collect_wisps`
already finds and teleports to the nearest wisp anywhere in the loaded zone, and returns
instantly when there is none — so "collect wisps before entering" needs no check node and no
coordinate. What `check_health_and_mana` earns its keep on is gating the *teleport* in the opt-in wisp
subgraph (Stage 8): porting ~1,500–4,000 units away and back is worth avoiding while the
players are already healthy. That is a genuine branch rather than a convenience.

### Merges considered and rejected

**`interact` and `interact_menu` stay separate.** An earlier draft merged them, on the grounds
that their params are identical but for the tail and that `interact_menu` *is*
`interact(has_dialogue=False)` plus a menu confirmation. Both halves of that are wrong.

`interact_menu`'s `menu_path` is **load-bearing synchronisation**, not a tail: in
`barkingham_palace_ransacked_throne_room` it waits for
`NPCServicesWin/ControlSprite/optionsLayout` to become visible so the following `click_path`
does not race the menu open. The earlier draft's migration note — that those three states
"become `interact` with `has_dialogue: false`" — would break the bot, merge or no merge.

And with 171 `interact` uses against 3, hanging two more params on the node almost every bot
uses, to serve one bot, is the wrong trade. Most authors touching `interact` should never see
`menu_path`.

**`enter_gauntlet` stays separate from `enter_dungeon`.** Its param list is a subset, but the
entry mechanism is not the same act, and a merged node would be two behaviours behind one
name.

**`teleport_to_player_if_in_zone` was not merged into `teleport_to_friend`**, and the reason is
worth keeping even though the node was removed for a different reason. The two shared an
implementation — both ended in `node_helpers.gather_at_player`, and `ZoneTarget`'s docstring says
it "mirrors teleport_to_friend's TargetResolution — same shape, different resolution rule".
What they do not share is a **contract**: its FAILURE means "nobody is in that zone, try the
next rung", routine and expected, while `teleport_to_friend`'s means something broke. Merging
would put both meanings on one port, and #11's default sends an unrouted failure edge to the
recovery node — so an author using probe mode without routing FAILURE would get a relog
instead of the next rung.

The general rule: **two nodes sharing a helper are not the same node.** What makes them one
node is a shared contract — the same meaning on the same ports — not the same code
underneath. That rule is what rejected the `interact` merge too, and it is the test any future
merge has to pass.

### Retire as an authoring surface, keep as a step

#36 set the precedent with `enter_zone_via_list`: the node stopped appearing in the browser,
the implementation stayed, and `zone_navigation.step_call` still runs it. Its module docstring
is the model for how to record a retirement.

| node | uses | why |
|---|---|---|
| `enter_zone_via_ride` | 0 | Registered as a node, but `zone_navigation` already imports and drives it. Exactly the `enter_zone_via_list` situation, one step behind. |

### Remove

| node | uses | why |
|---|---|---|
| `form_group` | 0 | Obsolete. Nothing calls it, no bot uses it, and the sync chain #59's body claims for it is not the chain the templates use. `group_manager.form_group` stays: relog, pre-run setup and recovery all call it. |
| `teleport_to_player_if_in_zone` | 0 | Superseded by `go_to_zone`. Planned as a retirement while `foulgaze` still used it; once `foulgaze` opened with `go_to_zone` there was no step left for it to be, so it was removed outright. Its target-picking helper, `find_target_in_zone`, moved to `node_helpers`, where zone-arrival recovery uses it. |

`foulgaze` used it as the head of a hand-rolled route plan written before the navigation map
existed:

```
Try teleport to Olde Town  --F-->  Try teleport to Wizard City  --F-->  go_to_world(WizardCity)
                                                                --S-->  teleport_to_world_hub(WC_Hub)
                                                                          --S--> enter_zone(Shopping District)
                                                                          --S--> enter_zone(Olde Town)
```

`go_to_zone` covers every branch: it plans "through walks, teleporters, zone lists, rides,
World Gates, the hub button and HOME" from wherever the players are, another world included;
players already in the zone are left where they are, "so this is cheap at the top of a bot";
and the port-to-a-player short-circuit the ladder is hand-rolling is what it already does
internally. The whole ladder is one `go_to_zone`.

### Keep

`wait_for_health` (5 uses) stays, with **one** of those uses. Four of the five bots using it
have no `handle_potions` node at all, so it is standing in for potion handling they never got;
only `spiral_cup_gauntlet` is the real case, where a housing gauntlet cannot mark a spot to
refill. Its contract is therefore **"for zones where `handle_potions` cannot refill"** —
housing gauntlets today, boss rematches later, both entered through a housing item — and that
belongs in its module docstring, because it is what stops the next author reaching for it as a
general health check. `docs/LIBRARY_MIGRATION.md` has the per-bot verdicts.

Keeping it with one live consumer beats the alternatives: a zero-use node is never exercised
by the suite or the corpus, and a deleted one has to be rebuilt when the gauntlet bots land.

`check_if_dropped` (0 uses) is unadopted rather than dead. Bot files already carry a `drops`
field, so the machinery is half there — but the decision is not abstract: it needs one shipped
template driving a "farm until it drops" loop, or it stays dead.

`mark_location` / `teleport_to_marked_location` share `(zone, xyz)` but are two different acts
against two different game affordances. They stay.

### The retry cap, and two self-loops that spin

Found while checking whether `relog`'s failure needed escalation. It does not — `relog` carries
`max_consecutive_retries=2` and `BotMachine` calls `stop()` on reaching it, the explicit
self-loop in the templates is required because `docs/RETURN_ROUTING.md` exempts the recovery
state's own failure from the default edge, and the node resumes health monitoring in a
`finally` so a genuinely dead client is force-closed and relaunched by the watchers without
anyone asking. Escalating from a node is not even implementable: `ClientManager.relog_clients`
pauses the machine and awaits `wait_until_parked()`, which can never return while a node is
what is running.

What the check did turn up is a real defect. **`AppController` never passes
`max_consecutive_same_state` to `BotMachine`, so the global same-state limit is `None` — off in
production.** Only the 25 nodes declaring their own `max_consecutive_retries` are protected;
the other 9 are not. Across 205 self-loops in the corpus, exactly two land on an uncapped
node:

| bot | state | node | consequence |
|---|---|---|---|
| `spiral_cup_gauntlet` | `state_10` | `wait_for_health` | `FAILURE` self-loop on a 120 s timeout — spins forever in two-minute waits |
| `the_great_spyre` | `state_8` | `apply_level_scaling` | `FAILURE` self-loop, unbounded |

Two ways to close it: give those nodes a cap, or give `BotMachine` a non-`None` default. The
cap is more in keeping with how the other 25 do it, and it keeps the bound a property of the
node rather than a global the app has to remember to pass. Either way the fix belongs here,
because Stage 9 will re-author both bots and the defect would survive the rewrite untouched.

### The nine outlier bots

The only bots this stage edits, and they are edited so the vocabulary is final before the
templates are rebuilt. `docs/LIBRARY_MIGRATION.md` lists them per bot, with what landed: four
`wait_for_health` uses removed, three `check_health`/`check_mana` detours dropped, and the two
self-loops above capped. The self-loops went to per-node caps — `wait_for_health` 5,
`apply_level_scaling` 3 — with `test_every_self_loop_lands_on_a_node_with_a_retry_cap` catching
the next one drawn on an uncapped node, rather than to a `BotMachine` default.

## Stage 3 — ports and the signature

`SubgraphDefinition` gains declared ports:

- **Entry ports** — local state ids an external edge or a role binding may target. `initial`
  and `recovery_state` become dropdowns over the entry ports of the instances that carry them,
  rather than a checkbox on a locked member.
- **Exit ports** — local state ids whose outgoing edges leave the subgraph. A local state with
  an unrouted SUCCESS edge is the seed for one, the way `seed_free_params` seeds ownership.

Composition maps a port to `<region>_<local>` and is otherwise unchanged. Validation gains one
rule: an external edge or role binding may only name a member that is a declared port. A
definition update that removes a port an instance uses is `held`, joining the existing hold
conditions.

**The requirement is much smaller than the current corpus suggests**, and this is the payoff
from the rewrite and the relog decision. Once `foulgaze`'s six-incoming-edge sync region and
every second-tier recovery fan-in are gone, the only shapes left are the templates':

| definition | entry ports | exit ports | notes |
|---|---|---|---|
| **Player Sync** | none by edge | 1 | two members must be role-bindable: `initial` on `go_to_zone`, `recovery_state` on `relog` |
| **Farming loop** | 1 | 1, plus the slot pair | the top-up phase has two shapes; see Stage 8 |

So: one entry port, one exit port, a slot pair, and two role-bindable members. **Roles, not
edges, are the only source of multiple entries.** The general multi-entry machinery — and the
statechart entry-point vocabulary that would come with it — is not needed for 1.0.0. Build the
schema so it does not preclude it; do not build it yet.

No UI in this stage. It is the schema and the composition change, with the editor still drawing
regions.

## Stage 4 — the param model collapses to a binary

With the body hidden, a free param is unreachable, so "free" stops being a third category and
folds into "input". What is left:

| state | meaning | where it is set |
|---|---|---|
| **exposed** | a parameter of the function | the call node's page |
| **internal** | the subgraph's business | nowhere, without expanding |

This lands before the call node, not after: the call node's page *is* the input form, so the
form has to be right first.

The existing seeding logic is the auto-signature: `seed_free_params` / `is_default_param`
already say that a param left at its default is the consumer's. An author writes a subgraph,
and the knobs they left blank *are* its parameters. Overrides do not disappear — a consumer can
still reach into the body — but it becomes a deliberate act rather than a routine one, which is
what it should always have been.

The authoring surface becomes one list. While editing a definition, the region page shows
every member param in a single table with an exposed/internal toggle per row, replacing padlocks
scattered across seven node pages and a separate Expose dialog. One pass, one place.

**What the signature actually has to hold**, measured across the corpus: four inputs. The
target or entrance zone, the `enter_dungeon` quad (`from_zone`, `dungeon_zone`, `sigil_xyz`,
`reset_xyz`), the exit `enter_zone` triple, and `handle_combat.enemy`. Everything else collapses
to a constant or a small enum — `collect_wisps`'s `only_safe`, `timeout` and `if_health_below`
are one value across all 76 core bots, and `handle_potions.refill_potions` is one value across
the whole corpus and belongs in `bot_options.APP_SETTING_KEYS` as an app setting rather than a
param at all. `docs/LIBRARY_MIGRATION.md` has the full table.

## Stage 5 — the call node (closes #67)

An instance collapses to a single node: the definition's name, its input form, and its declared
ports. #67 asks for collapsible regions; this is what satisfies it.

**Ordinary, unlinked regions collapse too.** An earlier draft refused this, on the grounds that
a region with no signature has nowhere to attach its crossing edges, and that "a user who wants
a region collapsed extracts a subgraph first, which is the outcome worth encouraging". The
measurement says otherwise: 40 of 82 bots use regions, there are 133 of them, and **three are
linked instances**. Refusing unlinked regions closes #67 for about 2% of what is on the canvas,
and pushes an author with `mt_olympus`'s 23-member "Collect Legion Tokens" region into
publishing a library definition they will never reuse.

Every graph tool that does this treats folding and reuse as separate features. Unreal Blueprint
ships Collapsed Graph, Macro and Function deliberately, and the Collapsed Graph — pure folding,
ports derived from the crossing links, not reusable — is the most used of the three. Blender
node groups derive their sockets from crossing links for any selection. So: an unlinked region
collapses with **inferred** ports and no signature; a linked one collapses with its declared
ports. After the relog decision, 102 of 133 regions are 1-in/1-out, so the inference is
unambiguous for three quarters of them.

Three requirements that are not optional:

- **The run highlight must survive collapse.** During a run the highlight lands on a state. A
  collapsed call node shows which internal state is active as a sub-label — "Sync players —
  Relog". Without it, debugging a live bot is strictly worse than it is today.
- **Combat assignments need a surface on a collapsed node.** `BotCombatStrategy.state_assignments`
  is keyed by member id and lives in the strategy store outside the bot file. The Farming loop
  contains `handle_combat`, so collapsing it hides the one state a per-state duel strategy
  attaches to. "The engine still sees plain states" is true and beside the point; the authoring
  path has to exist or collapse breaks the per-state level in `docs/COMBAT_CONFIG.md`.
- **Expand in place stays.** Collapse is the default. Expanding one instance to read its body
  without entering an edit is how an author answers "what does this actually do" without
  leaving the canvas. The expanded form is today's rendering, and Stage 3 of #37 is what draws
  it.

## Stage 6 — the definition document

`docs/SUBGRAPHS.md` bet on "there is no second document type… a definition is created from a
region and edited inside a bot that uses it", so a change is tested in a real graph before it
ships. In practice the first definition authored produced `bots/subgraphs/sync_players.json` —
a bot file whose only purpose is to host one linked region. The convention invented itself.

Formalise it: extraction generates a harness bot per definition under `bots/subgraphs/`, listed
in the library's Subgraphs folder (already in `UNBADGED_FOLDERS`), opened like any bot. Edit in
place stays as the contextual path and stops being the only door. The original rationale is
preserved — the harness *is* a real graph — and it costs little, because the file shape is
already proven.

## Stage 7 — slots

The farming loop has a hole: between `enter_dungeon` and `collect_wisps` sits the consumer's
unique content. Splitting the loop into two small definitions was considered and rejected: a
two-node subgraph is not doing enough to justify itself, and it gives up the reuse of the
dungeon-entrance and boss-room zones being one value.

With declared ports, a slot is not new machinery. It is **an exit port and an entry port,
paired**: the "Farming loop" call node exposes `in`, `content →`, `← resume`, and `out`. The
consumer wires their content between the two slot ports. Expanded, it renders as the gap it is.
Composition is untouched — the definition simply carries no internal edge across the slot.

**Design the slot against the heavy bots, not the light ones.** The boss template's median slot
content is 2 nodes; the dungeon template's is 25, and `waterworks` holds 47, `crimson_fields`
44, `mt_olympus` 57. A slot that reads well at two nodes and falls apart at forty has solved
the wrong half of the problem. Those three bots are the acceptance test.

## Stage 8 — the two shipped definitions, and the templates rebuilt on them

The stage the rewrite depends on. Extract, publish shipped, and rebuild all six templates as
consumers.

| definition | members | carried by | notes |
|---|---|---|---|
| **Player Sync** | `relog → go_to_zone → change_realm` | all six templates but gauntlet — **79 of 82 bots** | gauntlet's `relog → teleport_to_friend → handle_potions` is genuinely different and stays unlinked |
| **Farming loop** | `handle_potions → quicksell → enter_dungeon → [slot] → collect_wisps → exit` | boss, dungeon, spellement — **46+ bots** | spellement exits to `check_level_scaling`; the dungeon slot holds many nodes — both absorbed by Stage 7 |

Those reach numbers are the rewrite's doing, and they are the reason this plan is worth the
work at all. Measured against the library *as it stands today*, Player Sync applies to four
bots — only 6 of 82 contain `relog` and a realm change at all. Measured against the library
after Stage 9, it applies to 79. An earlier draft chose these two candidates for the right
reasons but justified them against the corpus that is being replaced.

**A third candidate was rejected.** "Top up health and mana" looked like a shared definition
across `praetor_mako` and `copy_qhat`. It is not: the two share the node sequence but differ in
an internal edge — praetor_mako's `collect_wisps` SUCCESS exits to `enter_dungeon` and its
FAILURE goes to `handle_potions`, copy_qhat's rejoins `check_mana` — and a definition is a fixed
internal edge set. The audit's own rule applies unchanged: two chains sharing a node sequence
are not the same subgraph.

What replaces it is two things that are not a shared definition:

- **A standard `collect_wisps` before entry, in the template.** Zero inputs and effectively
  zero cost: the node scans the client's entity list, teleports to the nearest wisp anywhere in
  the loaded zone, and breaks immediately when there is none, so the no-wisp case costs one
  scan rather than the timeout.
- **An opt-in wisp-detour subgraph** — `check_health_and_mana → teleport_to_coordinates → sleep →
  collect_wisps`, one entry, one exit, an internal bypass — for the minority of bots where the
  wisps are too far to be loaded. Both bots that need it teleport 1,500–4,000 units inside
  their own zone, and both label the following sleep "wait for wisps to load", which is what
  the detour is actually for. Signature: the wisp xyz plus the threshold.

Neither adds a template, which is the constraint that matters: a variant per template would
take six templates to twelve.

The hand-tuned sleep (1.0 s in one bot, 3.0 s in the other) is a smell. If `collect_wisps`
polled the entity list for a short window instead of the caller sleeping, the detour would need
no sleep and the standard pre-entry collect would land more often. Small change, good leverage.

Plus a library-wide test that every shipped bot composes to itself with an empty report — no
`held`, `missing` or `invalid` — so a shipped definition edit that breaks a bot fails the gate.

This is a **smaller #59 than its issue body describes**: it names `relog → form_group →
teleport_to_player_if_in_zone` as the repeated chain, which is not the chain the templates use,
and `form_group` was removed in Stage 2. The body gets rewritten in Stage 0.

`skeleton_key_boss_farmer_template.json` is **deleted** rather than rebuilt. It did not run: 5
of its 10 states were unreachable and `enter_dungeon` had no outgoing transitions, making it a
terminal state that stopped the run after entering the dungeon. It was the only template with
no bot validating it. It comes back with the skeleton-key bots post-1.0.0, alongside the one
new node that work needs — a key-door node whose third return means "out of keys" and carries
an explicit stop, since a consumed finite resource is a routine outcome and #11 would otherwise
route it to a relog loop.

## Stage 9 — the library rewrite (#59)

82 bots re-authored from the six templates. `docs/LIBRARY_MIGRATION.md` is the worksheet: every
bot with its category, its tier, the navigation it folds into `go_to_zone`, and its slot
content.

The shape of the work, by tier: **35 bots are drop-ins** (instantiate, fill four inputs, attach
a combat strategy), 16 are light, 16 are heavy, and 9 are rebuilds. It is not uniform — boss and
reagent are 30 drop-ins out of 43, while dungeon has **zero** and carries 439 slot nodes that no
amount of templating removes.

Two things this stage is not allowed to do. It does not rewrite a bot's `created_from` on load
(Stage 10). And it does not silently change behaviour: folding four navigation nodes into one
`go_to_zone` is a judgement about that bot's opening, and the tier tables exist so those
judgements are visible per bot rather than buried in a script's diff.

The four empty worlds and the thin ones are deliberately queued behind this, not folded into
it. Writing them against the finished system is the intended end-to-end test of it — and
writing them now would only mean migrating them twice.

## Stage 10 — template provenance

Subgraphs carry what must stay updatable. They will not carry everything: a template change
that adds a node or rewires an edge outside a subgraph is arbitrary structural divergence, and
a linked region cannot absorb that without marking every derived bot `held`.

So a bot records `created_from: {template_id, version}`, written once at creation and **never
rewritten on load** — the same rule as bot options and instance snapshots. The library then
reports "Boss Farmer has moved on (v1 → v4)" with a structural diff and a per-change *apply*.
Advisory, human-in-the-loop, no silent adoption. Automatic re-application where the app can
prove the user did not touch that part was considered and deferred: far more machinery, and
the failure mode is a silently rewritten bot.

`version` here is a monotonic integer, as `SubgraphSource.version` already is — not the bot
file's own `version` string, which is a different field meaning a different thing.

A template is **fork** semantics; a subgraph is **link** semantics. Conflating them is what
made problem 2 look unsolvable.

## Open decisions

- **Whether the two uncapped self-loops are fixed with per-node caps or a `BotMachine`
  default.** Per-node matches the existing 25; a default protects the other 7 uncapped nodes
  from a future author drawing the same edge.
- **`check_if_dropped`.** Zero uses, and the decision is not abstract: it needs a shipped
  template driving a "farm until it drops" loop, or it stays dead. Bot files already carry
  `drops`.
- **Port declaration ergonomics.** Whether an author declares ports explicitly or the app
  infers them from unrouted edges and offers a confirmation. The seeding precedent argues for
  infer-then-confirm, Blender and Unreal both infer, and 102 of 133 regions infer
  unambiguously — so infer-then-confirm, unless the confirmation proves noisy.
- **Whether the wisp detour is one subgraph or a flag on the top-up step.** One subgraph keeps
  the template free of a knob most bots do not need; a flag keeps the author from having to
  find and insert anything.
- **What happens to Stage 3 of #37.** Locking, group drag and the region pill become the
  expanded view. Worth an explicit read-through to decide what is kept, what is deleted, and
  what quietly became dead code.

## Out of scope

- Users publishing to the shipped library.
- Automatic template re-application.
- The activity types with no template: raids, boss rematches, lost pages, special events,
  monstrology, gardening, skeleton keys. Post-1.0.0. Of those, monstrology and gardening reuse
  the core four; raids are most likely a dungeon instance with a very large slot rather than a
  template of their own; boss rematches look like the one genuine new template, since the entry
  mechanism replaces `enter_dungeon` rather than filling a slot.
- The general multi-entry port machinery, and the statechart entry-point vocabulary with it.
  Roles cover every multiple entry the templated library contains.
- #12 (client subsets). Composed members are ordinary states, so it applies to them unchanged.
