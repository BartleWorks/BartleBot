# Library migration worksheet

Where all 85 shipped bots stand against the six released templates, what each one costs to
migrate, and what has to be settled before the rewrite starts. This is the worksheet for
Stage 9 of the subgraph-calls plan (linked from `docs/SUBGRAPH_CALLS.md`), issue #59.

Measured over `bots/` at `3109d5c` on 2026-10-03, against v1 of every template, with one
exception: `do_daga` is measured at its version before `00b8049` (see *Fix before the
rewrite*). The first measurement, on 2026-09-22 over 82 bots, predates Stage 2, Stage 8 and the
templates' rebuild on Sync players; nothing below carries over from it except where it says so.

Scope is all six templates. Boss, dungeon, spellement and reagent are the core four for
1.0.0; mob and gauntlet are listed with them now, since both templates were released with the
rest. The activity types with no template (raids, boss rematches, lost pages, monstrology,
gardening, skeleton keys) are post-1.0.0 and are not costed here.

## Method

Each bot is classified by node signature, in this order: any `collect_reagent` → reagent;
any `enter_gauntlet` → gauntlet; any level scaling node (`check_level_scaling`,
`apply_level_scaling` or `handle_level_scaling`) → spellement; no `enter_dungeon` but combat →
mob; `enter_dungeon` with at most one combat → boss; otherwise dungeon.

Every node a bot has beyond its template's own node histogram falls in one of four buckets.
The template's histogram now includes Sync players' two members (`relog`, `sync_in_zone`), so a
bot that already opens that way folds nothing for it.

| bucket | meaning | what it costs |
|---|---|---|
| **nav to fold** | `teleport_to_world_hub`, `go_to_world` (since removed), `enter_zone_via_object`, `check_zone`, `teleport_to_friend` (not in gauntlet, whose template has one), and any `relog`, `sync_in_zone` or `change_realm` beyond the template's | mechanical: the Sync players node and its zone input replace all of it |
| **scaling to fold** | `check_level_scaling`, `apply_level_scaling` | mechanical: one `handle_level_scaling`, which checks, travels, applies and returns (`docs/LEVEL_SCALING.md`) |
| **slot** | the bot's own content: extra combats, interacts, coordinate teleports, room-to-room `enter_zone`, extra reagent spawns, marks and recalls | irreducible; it is what the bot adds to the template |
| **outlier** | a node no template can express | a decision, per node |

Two caveats on the buckets. The scaling count is the two old nodes only; the travel a bot drew
between them (an `enter_dungeon` to the Scales' dungeon, the walk to them) is counted as slot or
nav, though `handle_level_scaling` folds it too, so spellement slot counts read slightly high.
And `teleport_to_coordinates` is deliberately not nav: `sync_in_zone` leaves players wherever
the last zone change put them, so a bot that needs a position keeps it.

The tier numbers come from a scratch measurement, not a tool. Progress is
`tools/rebuild_from_template.py status`.

## Tiers

| tier | rule | meaning | count |
|---|---|---|---|
| **A** | slot ≤ 3, no outliers | drop-in: start from the template, fill its fill-ins | **42** |
| **B** | slot ≤ 9, at most one outlier | light: template plus a handful of the bot's own nodes | **19** |
| **C** | slot 10–25 | heavy: the template is the frame, the content is hand-authored | **15** |
| **D** | slot > 25 | rebuild: the template is a small fraction of the file | **9** |

Tier measures node divergence, not effort. A tier A bot with an unnamed enemy still needs a
session in game, which the notes column flags.

## Totals

| category | bots | nodes | nav to fold | scaling to fold | slot | outliers | A | B | C | D |
|---|---|---|---|---|---|---|---|---|---|---|
| boss | 32 | 299 | 55 | 0 | 47 | 0 | 30 | 2 | 0 | 0 |
| dungeon | 19 | 576 | 43 | 0 | 442 | 0 | 0 | 3 | 9 | 7 |
| spellement | 16 | 355 | 46 | 33 | 182 | 3 | 4 | 8 | 2 | 2 |
| reagent | 12 | 119 | 18 | 0 | 55 | 0 | 5 | 5 | 2 | 0 |
| **core 4** | **79** | **1349** | **162** | **33** | **726** | **3** | **39** | **18** | **13** | **9** |
| mob | 3 | 29 | 3 | 0 | 6 | 0 | 3 | 0 | 0 | 0 |
| gauntlet | 3 | 62 | 3 | 0 | 49 | 1 | 0 | 1 | 2 | 0 |

Three facts. **57 of 79 core bots are tier A or B**, up from 51 of 76, because the templates
now carry more of what the bots already did (Sync players, the second Collect wisps, the mob
template's wait spot). **Dungeon's 442 slot nodes are 31% of every node in the library**, and
no template removes them. And the whole "a template cannot express this" surface is now **four
nodes in two bots**: `interact_menu` ×3 in `barkingham_palace_ransacked_throne_room`, and
`wait_for_health` in `spiral_cup_gauntlet`.

What moved since the first measurement:

- **Three new bots**: `atlantea` (Aquila, spellement D), `taking_tree` (Selenopolis, boss A),
  `spiral_cup_portal_of_peril` (Special Events, dungeon C). All three already link Sync players.
- **Tiers fell across the board.** `praetor_mako` went from C to A once its wisp detour was
  dropped, and all three mob bots are tier A now that Mob Farmer has its wait spot.
- **Seven bots already link Sync players** (`atlantea`, `sisyphus`, `taking_tree`,
  `spiral_cup_portal_of_peril`, and three reagent bots), and twenty more open with a hand-drawn
  `sync_in_zone`. None records a `template`.

## What the rewrite carries over, and what it changes

A rewritten bot is a new bot from the template's latest version, with these carried over from
the old file. Everything here is from the plan's Stage 9 card and its deviations; it is
collected so it can be checked per bot.

**Kept from the old file:** `id` (or the bot loses its combat profile and option overrides),
`name`, `description`, `tags`, `authors` (nothing comes from the template), `drops`, the bot's
own option declarations, and its path under `bots/`. `template` is set to the version the bot
was built from, exactly as `create_bot` sets it, and never written any other way. The bot
composes before it is saved, as `_new_bot` does.

**Filled in:** every fill-in the template's setup steps target, from the old file's values.
A Sync players input stays unset wherever it equals the definition's default, so a later
default change still reaches the bot. A fight a template owns names its enemy, or the bot
opens with a setup error.

**Deleted:** the folded navigation and level scaling, and every second-tier failure edge.
Measured as a `FAILURE` or `FALSE` edge into a navigation node other than the recovery state,
there are **189 across 26 bots**. The first measurement's 219 used a definition it did not
record, so the two counts are not comparable.

**Changed, on purpose, and recorded per bot:**

- **Recovery becomes a relog.** The recovery state is Sync players' Recover port in every
  template. Today it is `teleport_to_world_hub` in 49 bots, `relog` in 19, `check_zone` in 6,
  `sync_in_zone` in 4, `go_to_world` in 3 (each now a `sync_in_zone`), and something else in 4.
- **32 bots gain a quicksell** they did not have, and **13 gain `handle_potions`**. Both are
  template structure, and quicksell runs under the user's own Enable Quicksell setting.
- **Every boss, dungeon and spellement bot runs a Collect wisps before entry**, whose failure
  goes on to the dungeon rather than relogging.
- **Fights keep their strategies only if they stay exposed.** There are 42 exposed fights; the
  35 with an enemy follow by enemy (Stage 10.5). The other 7 (`spiral_cup_portal_of_peril` ×6,
  `waterworks` ×1) are bound by state id, so the rewrite names their enemy or leaves their
  strategies to be reassigned.

The notes column marks the bots each change touches. The tier tables exist so these are
visible per bot, not buried in a diff.

## Rebuild status

**59 of 84 bots are rebuilt** (2026-10-04), each by `tools/rebuild_from_template.py rebuild
--write`, which writes a bot only when every old node is carried or replaced, every node
fill-in is filled, and the result is sound. The other 25 are unchanged and still run as they
did; `status` lists them. `mirkholm_keep_ore` was deleted (`485528e`), so the library is 84.

| template | rebuilt |
|---|---|
| Boss Farmer (26) | `achaelos`, `advisor_fancy_fries`, `alicane_swiftarrow`, `bellows`, `crash_the_bandit_coot`, `foulgaze`, `graveyard`, `guillermo_shatterer`, `harvest_lord`, `hierophant_of_destruction`, `jabberwock`, `lord_bramble`, `lord_graustark`, `lord_nightshade`, `mongu_refuge`, `mr_cane`, `mr_stone`, `oakheart`, `praetor_mako`, `prince_gobblestone`, `scion_of_the_old_one`, `sergeant_skullsplitter`, `sister_sanderson`, `sprockets`, `taking_tree`, `vigilant_jarilo` |
| Dungeon Farmer (19) | `atlantea`, `barkingham_palace_main_hall`, `barkingham_palace_rooftop`, `barkingham_palace_state_wing`, `castle_darkmoor`, `crimson_fields`, `five_boxes_avalon`, `heretical_academy`, `katzensteins_lab`, `king_detritus`, `mirror_lake`, `mt_olympus`, `secret_tunnel`, `spike_the_crusher`, `spiral_cup_portal_of_peril`, `throne_room_of_fire`, `tree_of_life`, `trial_of_the_spheres`, `waterworks` |
| Spellement Farmer (5) | `copy_qhat`, `do_daga`, `eerkala`, `hall_of_heroes`, `harlequin_knight` |
| Reagent Collector (6) | `mirkholm_keep_reagents`, `nightside_flax`, `selenopolis_parchment`, `the_necropolis_deep_mushroom`, `the_wild_deep_mushroom`, `the_zocalo_red_mandrake` |
| Mob Farmer (3) | `fortee_thieves`, `monkey_spiders`, `valerik_brightsword` |

`do_daga` was rebuilt from its version before `00b8049`, as decided, not from the stub.
`eerkala` lost its own "Quicksell" option and its guards. `nightside_flax`, which
declared no Player Realms, now ships Separate, Reagent Collector's own declaration; the other
rebuilt reagent bots already declared it.
`crash_the_bandit_coot`, `eerkala` and `heretical_academy` have no drops, so their drops step
stays open; that blocks nothing.

The six bots first rebuilt on Dungeon Farmer were rebuilt again from their files before
`3842b9f` once it recalled out (see *Dungeon Farmer leaves by recall*); each differs from the
first rebuild only at its exit. `mt_olympus` lost its mark inside Mount Olympus, which would
have sent the recall back in, and keeps the quicksell it ran just before leaving, so it sells
twice; the second finds nothing. `atlantea` and `mt_olympus` no longer offer Team-Up, as
Dungeon Farmer does not.

### Why 25 are not rebuilt

A bot can be in several rows; each row is a reason the tool refuses it.

| reason | bots | what it needs |
|---|---|---|
| **No exit, in a single-boss farm.** Boss and Spellement Farmer walk out; these leave by the hub button (their entrance is the hub), by a teleporter back to the entrance, or have no door recorded. | `big_salgio`, `cronus`, `gladiator_dimachaerus`, `sand_squid_tentacles` (hub); `krokopatra`, `pyramid_of_mother_moon_tartarus_workaround` (teleporter); `prince_viggor`, `youkai` (no door) | a decision, deferred: see *Dungeon Farmer leaves by recall* |
| **The fight restarts in place**: the boss is fought again without leaving its zone | `jade_oni`, `queen_calypso`, `oyotomi_the_defiler` (open world, not a dungeon) | handled as exceptions, outside the templates |
| **A blank fill-in**, which would stop the bot running | `gladiator_dimachaerus`, `sand_squid_tentacles` (enemy) | recording in game |
| **Content only navigation reached**: a puzzle or teleport on the trip that Sync players would skip | `barkingham_palace_ransacked_throne_room`, `biti_nirini`, `crab_alley_stone_block`, `house_of_scales`, `the_great_spyre`, and four of the no-exit bots | a decision per bot: the trip may need its own steps after Sync players |
| **Content that leaves through navigation**: a branch that ends with a hub teleport or a ride | `aeriel_shores_aethyr_burst`, `aeriel_shores_silver_chests`, `grand_tourney_arena_gauntlet`, `hidden_temple_gauntlet` | a decision per bot |
| **A recall of the bot's own inside the dungeon**: Dungeon Farmer's exit recalls to the latest mark | `ironworks` (two recalls to a mark in the station) | its in-dungeon trips without a mark, or by hand |
| **A template node no run reaches** | `spiral_cup_gauntlet` | rebuilding by hand |
| **A reagent dungeon entered on the way in**: Sync players would gather in the entrance hall and collect before going in | `scrap_iron_collector`, `the_portico_pearl` | Reagent Collector with an entry, or these two by hand |
| **Challenge Mode potions logic**: a flag-guarded skip that runs backwards through the template's potions, quicksell and wisps | `sisyphus`, `barkingham_palace_ransacked_throne_room` | a decision on Challenge Mode potions |

`foulgaze`, `hierophant_of_destruction` and `jabberwock` were rebuilt once their enemies were
recorded in game (`485528e`).

**The no-exit row corrected this worksheet, and the recall answered most of it.** The first
measurement took the walk back to a dungeon's entrance for its exit, so it put most of the 26
bots with no exit in tier A or B. Dungeon Farmer now recalls out, so no dungeon bot needs an
exit recorded, and the eight left are single-boss farms.

## Settled for the rewrite (2026-10-03)

- **`tools/rebuild_from_template.py` rebuilds a bot from a template.** New Bot mints a fresh
  id, and nothing else builds a bot from a template. The tool builds from the latest released
  version through `create_bot`, keeps the old bot's id, name, description, tags, authors, drops,
  clients and its own options, and fills the fill-ins from the old file. It folds the
  navigation Sync players replaces and the level scaling `handle_level_scaling` replaces,
  carries the bot's own content into the template's slot, and keeps the bot's own thresholds on
  the potions and wisps the template replaces. Its module docstring has the rules and what it
  refuses. `rebuild` is a dry run until `--write`, which writes only a clean bot; `status`
  reports progress, and a bot is done when it records `template`.
- **Every Portal of Peril bot is built from Dungeon Farmer**, `spiral_cup_portal_of_peril` and
  any that follow it. Special Events is otherwise still out of scope.
- **`big_salgio` is built from Spellement Farmer** (#23), so it scales on the Celestia default,
  Trial of the Spheres, and moves from boss to spellement. Not yet: it has no exit (see
  *Rebuild status*), and the tool needs `--template "Spellement Farmer"` for it. The other half of #23 is done:
  `valerik_brightsword` is tagged Seeds and Boss.
- **One entrance is off the zone map.** Sync players' zone is a `nav_zone`, so it has to be a
  destination. `advisor_fancy_fries` enters its dungeon from House Calixco, an interior of Rubal
  Wastes, so it syncs in Rubal Wastes and keeps one `enter_zone` of its own into the house.
  Every other bot's farm entrance is a destination. The plan's second exception,
  `KM_Z00_CavityDining` in `copy_qhat`, is the entrance to its Scales detour, which
  `handle_level_scaling` replaces; its farm entrance is Gutenstadt.
- **Two reagent bots collect inside a dungeon.** `the_portico_pearl` (The Portico, from
  Celestia's Survey Camp) and `scrap_iron_collector` (Counterweight West, from the Royal Museum)
  keep their `enter_dungeon` and exit as their own nodes. Reagent Collector's zone step targets
  the Sync players zone and every spawn's zone together, so in these two the Sync players zone
  is the entrance and the spawns' zones are the dungeon.

### Dungeon Farmer leaves by recall (2026-10-04)

Dungeon Farmer marks the entrance right after Sync players ("Mark the entrance", at the
sigil's Reset XYZ) and leaves the dungeon by recalling to it ("Recall to the entrance"), in
place of the walk-out exit it had. A dungeon's way out is often no door at all: 26 bots left
by the hub button, a teleporter that appears after the last boss, or a Sync in zone back, and
none of those is a zone change the template's exit could record. A recall covers all of
them in one loading screen, lands just outside the sigil, and can skip the closing cutscene,
which is usually welcome: drops are granted the moment the last boss falls.

- **The mark runs once per Sync players**, at the start and after each recovery, not every
  loop. A recall does not use a mark up, and marking has no cooldown of its own: the timer
  under the Recall button is the cooldown the hub button, HOME and recall share.
- **Every mark is remembered for the run** (`mark_location.MARKS_KEY`), written by
  `mark_client` so `handle_level_scaling`'s marks count. `mark_location` leaves a player
  already marked on its spot alone, and the recall names no spot of its own: with its zone
  and XYZ blank it checks each player against the mark it set. `handle_potions` marks where
  the players stand, the entrance, so its refill trip recalls back there too.
- **A mark of the bot's own inside the dungeon is dropped** by the rebuild, since the recall
  goes to the latest mark; a recall of its own partway through is refused (`ironworks`).
- **Boss and Spellement Farmer keep their walk-out exits.** A single-room farm loops often,
  and a recall there spends mana every loop where the door costs nothing; recalling past a
  teleporter out of a whole dungeon is a much smaller missed optimization. Their eight bots
  with no door wait for a decision of their own.
- **`atlantea`, `mt_olympus` and `the_great_spyre` are dungeons first.** Each drops
  spellements but is farmed as a dungeon, never as a spellement farm (`atlantea` is about
  five times slower for spellements than `sisyphus`, a single boss). They are rebuilt from
  Dungeon Farmer (`DUNGEONS_FIRST` in the tool), each with a `handle_level_scaling` of its
  own straight after the entry, where its Scales are.
- **`jade_oni`, `queen_calypso` and `oyotomi_the_defiler` are exceptions.** Their boss is
  fought again without leaving the zone, which no template's loop does; they are handled as
  the edge cases they are.

Dungeon Farmer v1 was re-cut from the draft, as in `f6fc0f8`; no user had a bot from it.

Rejected, in the order they were weighed: a recall in every template (it spends mana every
loop in the single-room farms that make up most of the library); an Exit dungeon node that
takes a door, a teleporter, the hub button or a recall (a new param type and Record flow for
what a recall already covers); and no exit at all, with the loop returning through a Sync in
zone (slower, and a missing exit goes unnoticed).

### Level scaling goes after entry where the Scales are inside

Spellement Farmer is unchanged: it scales before `enter_dungeon`. A bot that farms the Scales'
own dungeon places `handle_level_scaling` after the entry instead, as `docs/LEVEL_SCALING.md`
says, so its players scale in place rather than marking outside, going in, scaling, recalling
out and entering a second time. That is 10 of the 16 spellement bots (`krokopatra`, `jade_oni`,
`oyotomi_the_defiler`, `the_great_spyre`, `atlantea`, `gladiator_dimachaerus`, `mt_olympus`,
`sand_squid_tentacles`, `hall_of_heroes`, `barkingham_palace_ransacked_throne_room`), and most of
a full library. The rebuild tool decides it from the Scales table: the bot's farm dungeon is the
Scales' own dungeon. Three of the ten, `the_great_spyre`, `atlantea` and `mt_olympus`, are now
dungeons first on Dungeon Farmer, which has no level scaling node; each scales in place all the
same, through a `handle_level_scaling` of its own after the entry.

It costs little against template updates. The move changes four edge slots of template-owned
nodes: Sync in zone and the exit each go on to Handle potions, Enter dungeon goes to Handle level
scaling, and Handle level scaling goes to the fight. The review is a three-way diff, so a release
that leaves those four slots alone shows nothing about them and every other change applies as
usual; only a release that changes one of the four reaches these bots, as a conflict.

Rejected: a second spellement template for Scales-inside dungeons, which doubles every
spellement release; and moving the node after entry in the template itself, which works for the
other 6 bots only if players can mark inside their farm dungeon.

### `wait_for_health` is removed

`spiral_cup_gauntlet` waits for health after porting to its friend, before entering. Gauntlet
Farmer does that job differently: Check health and mana sends players back to Sync players in
The Commons, where `handle_potions` can refill. Rebuilt on the template, the bot no longer needs
the wait, and nothing else uses the node, so it goes when that bot is rebuilt. Boss rematches
get their own answer after 1.0.0.

## What needs the game

None of these can be done from the files.

- **Two template fights have no enemy**: `gladiator_dimachaerus` and `sand_squid_tentacles`.
  Boss, Spellement and Mob Farmer make the enemy a fill-in, so each needs its enemy ID recorded
  before the bot can run.
- **Eight single-boss farms have no exit** Boss or Spellement Farmer can record (see
  *Rebuild status*); two of them, `prince_viggor` and `youkai`, may have a door to record.
- **`sisyphus` needs level scaling before 1.0.0.** It is farmed for spellements in Challenge
  Mode, which scales its players itself, so it has no level scaling node; a user who does not
  play Challenge Mode needs one.
- **Seven exposed fights have no enemy** (see above).
- **Every rewritten bot needs a run** before it counts as done.

## Decisions taken

### `wait_for_health` — reduced to one use, then none

The first measurement found that four of its five users had no `handle_potions` at all: the
wait was standing in for potion handling the bot never got. Stage 2 dropped those four and kept
`spiral_cup_gauntlet`, capped at 5 retries. The node's contract is recorded in its module
docstring: **for zones where `handle_potions` cannot refill**, housing gauntlets and later boss
rematches. Gauntlet Farmer made it redundant there too, so it is removed with the rewrite.

`queen_calypso` lost its wait in Stage 2 and did not gain `handle_potions`; Boss Farmer gives
it one.

### `check_health_and_mana` — merged, and used by Gauntlet Farmer

`check_health` and `check_mana` merged into `check_health_and_mana(health_threshold,
mana_threshold)`, where a threshold of 0 switches that stat off. Stage 2 dropped its three bot
uses (the wisp detours in `praetor_mako`, `copy_qhat` and
`pyramid_of_mother_moon_tartarus_workaround`). The opt-in wisp detour that was to bring it back
was not built (decided 2026-10-03): the Collect wisps before entry covers it, and a bot whose
wisps are out of range adds its own nodes. Its one use today is Gauntlet Farmer's health check.

### `interact` and `interact_menu` — do not merge

`interact_menu`'s `menu_path` is load-bearing synchronization: it waits for
`NPCServicesWin/ControlSprite/optionsLayout` to show before the following `click_path`, so the
clicks do not race the menu. Plain `interact` would not wait. With 190 `interact` uses against 3,
two more params on the common node for one bot is the wrong trade, so
`barkingham_palace_ransacked_throne_room` keeps both nodes as its own content.

### Params

- **`collect_wisps`**: `only_safe`, `timeout` and `if_health_below` were one value across the
  corpus, and the templates set them. With templates copied rather than linked there is no lock;
  a bot that changes them is simply different.
- **`handle_potions.refill_potions` is the Refill Potions bot option**, declared `None` so the
  app setting (Settings → Bot, on by default) answers: spending gold describes the user, not the
  node. Landed 2026-10-03. Every stored value was `true`, so the param was dropped from every
  bot and draft, the six templates were re-cut as v1 without it, and
  `test_no_bot_carries_a_dead_refill_potions_param` keeps it out.
- **`enter_dungeon.offers_team_up` / `offers_challenge_mode`**: template defaults, not locks.
  Team-Up is on in Boss and Spellement Farmer and off in Dungeon Farmer (settled 2026-10-03);
  Challenge Mode is off in all three.
- **`change_realm`**: the realm range is the constants 3–5, and every realm state in the library
  is `change_realm`. Landed.
- **`handle_combat`**: strategies live in the combat profile, keyed by fight (state and enemy
  since Stage 10.5), not in the template.

### Every failure edge relogs

Adopted in place of a second-tier recovery hub. A soft `relog` is the only step that clears
both dangerous stuck states, stuck in combat and stuck with the UI open, and
`teleport_to_world_hub` clears neither. Under #11 an unrouted `FAILURE` already goes to the
recovery state, and every template's recovery state is Sync players' Recover port, which is
`relog`. So the second-tier edges are deleted, not replaced.

**A relog that fails stops the bot after two attempts.** The plan had the templates draw a
`relog --FAILURE--> relog` self-loop. Sync players has not had one since v3, and does not need
it: the recovery state's own failure is exempt from the default edge (`docs/RETURN_ROUTING.md`),
so an unrouted failure there is a no-match that leaves the machine on `relog`, and `relog`'s
`max_consecutive_retries=2` stops the run on the second tick, as the self-loop would have. The
canvas warns about the uncovered port, which is accurate. A client that genuinely died is
caught by `_client_status_watcher` and relaunched by the recovery queue on its own.

Escalating to a hard relog from inside a node is not implementable:
`ClientManager.relog_clients` pauses the machine and waits for it to park, which a running node
never lets it do. The hard path is reachable only from the watch loop.

### The retry cap is per node

`AppController` still passes no `max_consecutive_same_state` to `BotMachine`, so the global
same-state limit is off and only a node's own `max_consecutive_retries` stops a spinning state.
Stage 2 capped the two uncapped nodes that had self-loops (`wait_for_health` 5,
`apply_level_scaling` 3), and `test_every_self_loop_lands_on_a_node_with_a_retry_cap` fails the
suite the next time a shipped self-loop lands on an uncapped node. A rewritten bot that draws a
self-loop is checked by the same test.

### Deleted and deferred

- **Removed in Stage 2:** `teleport_to_player_if_in_zone` (its helper `find_target_in_zone`
  moved to `node_helpers`), `form_group`, `check_health`, `check_mana`,
  `change_to_favorite_realm` and `change_to_next_realm`. `enter_zone_via_ride` is retired from
  the browser and kept as a navigation step. `go_to_zone` was renamed `sync_in_zone` (#133).
- **`skeleton_key_boss_farmer_template.json` is deleted.** It did not run, and it comes back
  with the skeleton-key bots after 1.0.0, alongside a key-door node whose "out of keys" outcome
  carries an explicit stop.
- **`foulgaze` is rewritten from the template**, not migrated. It was the subgraph and player
  sync experiment. Its lost definitions no longer matter, and `subgraphs/sync-players.json` is
  the one shipped definition.
- **Missing drops are tracked separately.** Each template has a drops setup step, so
  `crash_the_bandit_coot`, `heretical_academy` and `eerkala`, which have none, open with that
  step undone.
- **The empty worlds wait for the rewrite.** Writing them against the finished system is the
  intended end-to-end test of it.

## Nodes the rewrite leaves unused

Measured by walking each bot from its initial and recovery states, and from its old level
scaling nodes, up to its first farming node (a fight, an interact, a reagent, or an
`enter_dungeon` that is not a Scales detour). A navigation node reached that way is what Sync
players or `handle_level_scaling` replaces; one reached only further in is the bot's own
content and survives.

| node | uses today | after the rewrite | verdict |
|---|---|---|---|
| `wait_for_health` | 1 | 0 | **remove** (decided) |
| `check_level_scaling` | 16 | 0 | **remove**: `handle_level_scaling` checks first |
| `apply_level_scaling` | 15 | 0 | **remove from the library**, but see below |
| `go_to_world` | 6 | 0 | **removed**: each use is now a `sync_in_zone` to the zone its World Gate lands in |
| `check_zone` | 42 | 0 | **kept for now**: every use is an opening or recovery hub that Sync players replaces |
| `check_if_dropped` | 0 | 0 | **removed**: never adopted; no template has a "farm until it drops" loop |
| `interact_menu`, `click_path` | 3 each, one bot | same | keep (see *Decisions taken*) |
| `mark_location`, `teleport_to_marked_location` | 6 and 5 bots | every Dungeon Farmer bot | keep: Dungeon Farmer marks the entrance and recalls out |
| `teleport_to_friend` | 4 | Gauntlet Farmer | keep |
| `teleport_to_world_hub` | 63 bots | 2 bots | keep: a mid-route hop in `house_of_scales` and `aeriel_shores_aethyr_burst`, and a step zone navigation runs. `spiral_cup_portal_of_peril`'s was its way out, and is the recall now |
| `enter_zone_via_object` | 19 bots | 9 bots | keep: in-dungeon teleporters and skips |

**`apply_level_scaling` no longer holds anything else up.** `tools/scales_table.py add` used to
build a new entry from a scratch bot with an `apply_level_scaling` recorded on the Scales; it now
reads an `interact`, which records the same zone, point and reset. `apply_elixir` and the two
option specs moved into `handle_level_scaling`, which `apply_level_scaling` now imports them from.
So once the last bot is rebuilt, both old nodes are deleted outright: no bot uses them, so they
need no hidden-but-registered state in between.

`go_to_world` went because a Sync in zone to the zone a world's gate lands in does the same
thing, through a route the planner picks. Its six uses were converted in place, keeping their
state ids and edges. `check_zone` stays for now: Sync players has superseded its uses, but it is
the case explicit stops were built for (`docs/RETURN_ROUTING.md`). The `check_zone` the
restricted-zone guard calls is a different function, in `restricted_zones`, and is unaffected.

## Coverage

Core-four and mob bots per world. Reagent bots are filed by activity under `25_crafting`, not
by world, and gauntlets under `27_housing_gauntlets`.

| world | boss | dungeon | spellement | mob |
|---|---|---|---|---|
| Wizard City | 6 | 1 | 0 | 0 |
| Krokotopia | 0 | 1 | 2 | 0 |
| Marleybone | 2 | 3 | 0 | 0 |
| MooShu | 2 | 2 | 3 | 0 |
| Dragonspyre | 0 | 0 | 1 | 1 |
| **Grizzleheim** | **0** | **0** | **0** | **0** |
| Celestia | 4 | 1 | 0 | 0 |
| Zafaria | 0 | 1 | 0 | 0 |
| Avalon | 1 | 0 | 1 | 0 |
| Azteca | 1 | 0 | 1 | 0 |
| **Khrysalis** | **0** | **0** | **0** | **0** |
| Wysteria | 2 | 0 | 0 | 0 |
| Aquila | 2 | 0 | 4 | 0 |
| Polaris | 1 | 0 | 0 | 0 |
| Mirage | 2 | 0 | 1 | 1 |
| Empyrea | 0 | 1 | 0 | 1 |
| Arcanum | 1 | 0 | 0 | 0 |
| Catacombs | 1 | 1 | 0 | 0 |
| Karamelle | 0 | 0 | 1 | 0 |
| Lemuria | 1 | 0 | 1 | 0 |
| **Novus** | **0** | **0** | **0** | **0** |
| Wallaru | 2 | 0 | 0 | 0 |
| Selenopolis | 1 | 0 | 0 | 0 |
| Darkmoor | 2 | 1 | 0 | 0 |
| Four Dungeons | 1 | 5 | 1 | 0 |
| Five Boxes | 0 | 1 | 0 | 0 |
| Special Events | 0 | 1 | 0 | 0 |

Three worlds have no bots at all: Grizzleheim, Khrysalis and Novus. Selenopolis has one since
`taking_tree`. Eight worlds have no boss bot and fifteen have no spellement bot. All of it reuses
the existing templates, so it is authoring, not design, and it is queued behind the rewrite.

## Per-bot worksheet

Columns are the buckets above. Notes flag what the rewrite changes or needs for that bot:
whether it already opens with Sync players, its second-tier edges, plain regions, fights,
unnamed enemies, an entrance needing its own step, a Scales the node must name (no world
default applies), and the quicksell or potion handling it gains.

### Boss — 32 bots

| tier | bot | world | nodes | nav | scaling | slot | outliers | notes |
|---|---|---|---|---|---|---|---|---|
| A | `crash_the_bandit_coot` | Wallaru | 9 | 2 | 0 | 0 | — | has `sync_in_zone`; no drops |
| A | `cronus` | Aquila | 6 | 1 | 0 | 0 | — | gains quicksell; gains potions |
| A | `foulgaze` | Wizard City | 9 | 1 | 0 | 0 | — | has `sync_in_zone`; 1 region; **enemy unnamed** |
| A | `lord_bramble` | Wysteria | 11 | 3 | 0 | 0 | — | has `sync_in_zone`; 2nd-tier recovery (3 edges) |
| A | `lord_graustark` | Wysteria | 11 | 3 | 0 | 0 | — | has `sync_in_zone`; 2nd-tier recovery (3 edges) |
| A | `lord_nightshade` | Wizard City | 9 | 1 | 0 | 0 | — | has `sync_in_zone`; 2nd-tier recovery (3 edges); 1 region |
| A | `mr_cane` | Wallaru | 9 | 1 | 0 | 0 | — | has `sync_in_zone` |
| A | `praetor_mako` | Celestia | 9 | 1 | 0 | 0 | — | has `sync_in_zone` |
| A | `prince_gobblestone` | Wizard City | 8 | 0 | 0 | 0 | — | has `sync_in_zone`; 2nd-tier recovery (2 edges); 1 region |
| A | `sister_sanderson` | Darkmoor | 9 | 0 | 0 | 0 | — | has `sync_in_zone`; 2nd-tier recovery (2 edges) |
| A | `taking_tree` | Selenopolis | 9 | 1 | 0 | 0 | — | links Sync players |
| A | `achaelos` | Celestia | 7 | 1 | 0 | 1 | — | gains quicksell |
| A | `advisor_fancy_fries` | Mirage | 10 | 2 | 0 | 1 | — | syncs in Rubal Wastes, plus an `enter_zone` |
| A | `hierophant_of_destruction` | Darkmoor | 12 | 3 | 0 | 1 | — | has `sync_in_zone`; **enemy unnamed** |
| A | `jabberwock` | Avalon | 10 | 3 | 0 | 1 | — | **enemy unnamed** |
| A | `mr_stone` | Arcanum | 9 | 3 | 0 | 1 | — | gains quicksell |
| A | `scion_of_the_old_one` | Lemuria | 9 | 3 | 0 | 1 | — | 2nd-tier recovery (7 edges) |
| A | `sisyphus` | Aquila | 10 | 1 | 0 | 1 | — | links Sync players; 1 region |
| A | `bellows` | Marleybone | 6 | 1 | 0 | 2 | — | gains quicksell; gains potions |
| A | `big_salgio` | Celestia | 7 | 1 | 0 | 2 | — | gains quicksell |
| A | `guillermo_shatterer` | Azteca | 7 | 1 | 0 | 2 | — | gains quicksell; gains potions |
| A | `prince_viggor` | Catacombs | 10 | 3 | 0 | 2 | — | 2nd-tier recovery (1 edge); gains quicksell |
| A | `sprockets` | Marleybone | 6 | 1 | 0 | 2 | — | gains quicksell; gains potions |
| A | `vigilant_jarilo` | Polaris | 8 | 0 | 0 | 2 | — | has `sync_in_zone`; gains quicksell |
| A | `alicane_swiftarrow` | Wizard City | 10 | 2 | 0 | 3 | — | 2 regions; gains quicksell |
| A | `graveyard` | Four Dungeons | 15 | 5 | 0 | 3 | — | 2nd-tier recovery (3 edges); 1 region |
| A | `harvest_lord` | Wizard City | 10 | 2 | 0 | 3 | — | 2 regions; gains quicksell |
| A | `mongu_refuge` | Mirage | 14 | 4 | 0 | 3 | — | — |
| A | `oakheart` | MooShu | 8 | 1 | 0 | 3 | — | gains quicksell; gains potions |
| A | `sergeant_skullsplitter` | Wizard City | 10 | 2 | 0 | 3 | — | 2 regions; gains quicksell |
| B | `queen_calypso` | Celestia | 10 | 1 | 0 | 4 | — | has `sync_in_zone`; 2 regions; gains quicksell; gains potions |
| B | `youkai` | MooShu | 12 | 1 | 0 | 6 | — | 2 regions; gains quicksell |

### Dungeon — 19 bots

| tier | bot | world | nodes | nav | scaling | slot | outliers | notes |
|---|---|---|---|---|---|---|---|---|
| B | `throne_room_of_fire` | Krokotopia | 12 | 1 | 0 | 7 | — | 2 combats; gains quicksell |
| B | `secret_tunnel` | Empyrea | 15 | 1 | 0 | 9 | — | has `sync_in_zone`; 3 regions; 3 combats; gains quicksell |
| B | `spike_the_crusher` | Marleybone | 17 | 3 | 0 | 9 | — | 2nd-tier recovery (14 edges); 2 combats; 1 unnamed enemy |
| C | `barkingham_palace_main_hall` | Four Dungeons | 19 | 3 | 0 | 11 | — | 3 combats |
| C | `king_detritus` | Catacombs | 20 | 3 | 0 | 12 | — | 2nd-tier recovery (1 edge); 3 regions; 3 combats; 2 unnamed enemies |
| C | `barkingham_palace_state_wing` | Four Dungeons | 19 | 1 | 0 | 14 | — | 3 combats; 2 unnamed enemies; gains quicksell |
| C | `tree_of_life` | MooShu | 23 | 4 | 0 | 15 | — | 3 combats; gains potions |
| C | `barkingham_palace_rooftop` | Four Dungeons | 21 | 1 | 0 | 16 | — | 5 regions; 3 combats; 1 unnamed enemy; gains quicksell |
| C | `mirror_lake` | Zafaria | 23 | 0 | 0 | 16 | — | has `sync_in_zone`; 2nd-tier recovery (19 edges); 5 regions; 4 combats |
| C | `spiral_cup_portal_of_peril` | Special Events | 32 | 3 | 0 | 22 | — | links Sync players; 5 regions; 6 combats; 6 unnamed enemies |
| C | `katzensteins_lab` | Marleybone | 29 | 1 | 0 | 24 | — | 6 regions; 4 combats; gains quicksell |
| C | `castle_darkmoor` | Four Dungeons | 34 | 4 | 0 | 25 | — | 2nd-tier recovery (3 edges); 5 regions; 2 combats |
| D | `heretical_academy` | Darkmoor | 37 | 3 | 0 | 28 | — | has `sync_in_zone`; 3 combats; no drops |
| D | `house_of_scales` | Four Dungeons | 44 | 7 | 0 | 33 | — | 2nd-tier recovery (4 edges); 5 regions; 6 combats; gains quicksell |
| D | `ironworks` | Marleybone | 42 | 2 | 0 | 36 | — | 8 regions; 5 combats; 1 unnamed enemy; gains quicksell |
| D | `trial_of_the_spheres` | Celestia | 42 | 1 | 0 | 37 | — | 4 regions; 3 combats; gains potions |
| D | `five_boxes_avalon` | Five Boxes | 46 | 3 | 0 | 39 | — | 2nd-tier recovery (44 edges); 3 regions; 4 combats |
| D | `crimson_fields` | MooShu | 48 | 1 | 0 | 43 | — | 8 regions; 6 combats; 2 unnamed enemies; gains quicksell |
| D | `waterworks` | Wizard City | 53 | 1 | 0 | 46 | — | has `sync_in_zone`; 7 regions; 8 combats; 7 unnamed enemies |

### Spellement — 16 bots

| tier | bot | world | nodes | nav | scaling | slot | outliers | notes |
|---|---|---|---|---|---|---|---|---|
| A | `gladiator_dimachaerus` | Aquila | 7 | 1 | 2 | 0 | — | **enemy unnamed**; names its Scales; gains quicksell; gains potions |
| A | `hall_of_heroes` | Lemuria | 12 | 3 | 2 | 1 | — | 2nd-tier recovery (8 edges) |
| A | `sand_squid_tentacles` | Aquila | 9 | 1 | 2 | 1 | — | **enemy unnamed**; names its Scales; gains quicksell; gains potions |
| A | `do_daga` | MooShu | 12 | 4 | 2 | 2 | — | 2nd-tier recovery (8 edges); 2 regions; gains quicksell; gains potions |
| B | `copy_qhat` | Karamelle | 15 | 2 | 2 | 4 | — | has `sync_in_zone`; 2nd-tier recovery (1 edge) |
| B | `eerkala` | Mirage | 21 | 7 | 3 | 4 | — | 2nd-tier recovery (16 edges); no drops |
| B | `harlequin_knight` | Avalon | 12 | 1 | 2 | 4 | — | 2 regions; gains quicksell |
| B | `jade_oni` | MooShu | 12 | 1 | 2 | 4 | — | 1 region; gains quicksell |
| B | `pyramid_of_mother_moon_tartarus_workaround` | Azteca | 15 | 3 | 2 | 5 | — | has `sync_in_zone`; 2nd-tier recovery (2 edges); 2 regions; names its Scales; gains quicksell; gains potions |
| B | `barkingham_palace_ransacked_throne_room` | Four Dungeons | 20 | 3 | 2 | 6 | interact_menu×3 | 3 regions; names its Scales |
| B | `krokopatra` | Krokotopia | 15 | 3 | 2 | 6 | — | 2 regions; gains quicksell |
| B | `oyotomi_the_defiler` | MooShu | 21 | 7 | 2 | 6 | — | 2nd-tier recovery (13 edges); 4 regions |
| C | `biti_nirini` | Krokotopia | 23 | 3 | 3 | 11 | — | 2nd-tier recovery (14 edges); 3 regions; gains quicksell |
| C | `the_great_spyre` | Dragonspyre | 30 | 4 | 2 | 19 | — | 5 regions; 4 combats; gains quicksell |
| D | `atlantea` | Aquila | 64 | 1 | 1 | 53 | — | links Sync players; 8 regions; 8 combats; names its Scales |
| D | `mt_olympus` | Aquila | 67 | 2 | 2 | 56 | — | 5 regions; 7 combats; 4 unnamed enemies; names its Scales |

### Reagent — 12 bots

| tier | bot | world | nodes | nav | scaling | slot | outliers | notes |
|---|---|---|---|---|---|---|---|---|
| A | `selenopolis_parchment` | Crafting | 5 | 0 | 0 | 0 | — | links Sync players |
| A | `the_portico_pearl` | Crafting | 7 | 2 | 0 | 2 | — | 2nd-tier recovery (5 edges); collects inside a dungeon |
| A | `the_zocalo_red_mandrake` | Crafting | 7 | 1 | 0 | 2 | — | — |
| A | `nightside_flax` | Crafting | 7 | 3 | 0 | 3 | — | 2nd-tier recovery (4 edges) |
| A | `the_wild_deep_mushroom` | Crafting | 7 | 2 | 0 | 3 | — | — |
| B | `mirkholm_keep_reagents` | Crafting | 10 | 0 | 0 | 4 | — | has `sync_in_zone`; 2nd-tier recovery (2 edges); 2 regions |
| B | `scrap_iron_collector` | Crafting | 8 | 3 | 0 | 4 | — | 2nd-tier recovery (6 edges); collects inside a dungeon |
| B | `the_necropolis_deep_mushroom` | Crafting | 10 | 2 | 0 | 4 | — | — |
| B | `crab_alley_stone_block` | Crafting | 12 | 3 | 0 | 5 | — | 2 regions |
| B | `mirkholm_keep_ore` | Crafting | 12 | 0 | 0 | 6 | — | links Sync players; deleted in `485528e` |
| C | `aeriel_shores_aethyr_burst` | Crafting | 18 | 1 | 0 | 11 | — | links Sync players; 3 regions |
| C | `aeriel_shores_silver_chests` | Crafting | 16 | 1 | 0 | 11 | — | 3 regions |

### Mob — 3 bots

| tier | bot | world | nodes | nav | scaling | slot | outliers | notes |
|---|---|---|---|---|---|---|---|---|
| A | `fortee_thieves` | Mirage | 7 | 0 | 0 | 1 | — | — |
| A | `valerik_brightsword` | Dragonspyre | 11 | 1 | 0 | 2 | — | has `sync_in_zone` |
| A | `monkey_spiders` | Empyrea | 11 | 2 | 0 | 3 | — | 2nd-tier recovery (1 edge); 1 region |

### Gauntlet — 3 bots

| tier | bot | world | nodes | nav | scaling | slot | outliers | notes |
|---|---|---|---|---|---|---|---|---|
| B | `hidden_temple_gauntlet` | Housing Gauntlets | 10 | 1 | 0 | 6 | — | 1 unnamed enemy |
| C | `grand_tourney_arena_gauntlet` | Housing Gauntlets | 23 | 1 | 0 | 19 | — | 6 regions; 5 combats |
| C | `spiral_cup_gauntlet` | Housing Gauntlets | 29 | 1 | 0 | 24 | wait_for_health | 6 regions; 6 combats; 6 unnamed enemies; gains potions |
