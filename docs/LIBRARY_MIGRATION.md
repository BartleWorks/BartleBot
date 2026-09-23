# Library migration worksheet

Where all 82 shipped bots stand against the four core templates, what each one costs to
migrate, and what the templates and subgraphs have to cover to absorb them. Measured over
`bots/` on 2026-09-22; regenerate rather than hand-edit if the corpus moves.

Scope is the four core categories for 1.0.0 — boss, dungeon, spellement, reagent. Mob and
gauntlet have templates and are listed at the end; the activity types with no template
(raids, boss rematches, lost pages, special events, monstrology, gardening, skeleton keys)
are post-1.0.0 and are not costed here.

## Method

Each bot is classified by node signature, in this order: any `collect_reagent` → reagent;
any `enter_gauntlet` → gauntlet; any level-scaling node → spellement; no `enter_dungeon`
but combat → mob; `enter_dungeon` with at most one combat → boss; otherwise dungeon.

Every node a bot has beyond its template's own histogram falls in one of three buckets, and
the split is the whole point — only the third is a blocker:

| bucket | meaning | what it costs |
|---|---|---|
| **nav to fold** | `teleport_to_world_hub`, `go_to_world`, `enter_zone_via_object`, `check_zone`, `teleport_to_friend`, `teleport_to_player_if_in_zone`, `change_to_next_realm` | mechanical — `go_to_zone` plans all of it |
| **slot** | the bot's own content: combats, interacts, coordinate teleports, room-to-room `enter_zone`, extra reagent spawns | irreducible; it is what the template's slot is for |
| **outlier** | a node no template can express | a design decision, per node |

`teleport_to_coordinates` is deliberately *not* nav: `go_to_zone`'s own usage note says
players "arrive wherever the last zone change puts them", so a bot that needs a position
keeps it.

## Tiers

| tier | rule | meaning | count |
|---|---|---|---|
| **A** | slot ≤ 3, no outliers | drop-in: instantiate the template, fill params | **35** |
| **B** | slot ≤ 9, at most one outlier | light: template plus a handful of slot nodes | **16** |
| **C** | slot 10–25 | heavy: template contributes the frame, content is hand-authored | **16** |
| **D** | slot > 25 | rebuild: the template is a small fraction of the file | **9** |

Tier measures *node* divergence, not conceptual divergence. `foulgaze` is tier A on two slot
nodes and is simultaneously the messiest file in the library; read tier A as "small diff",
not "no thought required".

## Totals

| category | bots | nodes | nav to fold | slot | outliers | A | B | C | D |
|---|---|---|---|---|---|---|---|---|---|
| boss | 31 | 310 | 59 | 70 | 3 | 25 | 5 | 1 | 0 |
| dungeon | 18 | 545 | 38 | 439 | 1 | 0 | 1 | 9 | 8 |
| spellement | 15 | 315 | 53 | 143 | 6 | 5 | 5 | 4 | 1 |
| reagent | 12 | 117 | 22 | 56 | 0 | 5 | 5 | 2 | 0 |
| **core 4** | **76** | **1287** | **172** | **708** | **10** | **35** | **16** | **16** | **9** |
| mob | 3 | 31 | 4 | 8 | 2 | 1 | 2 | 0 | 0 |
| gauntlet | 3 | 62 | 3 | 49 | 1 | 0 | 1 | 2 | 0 |

Read that table as three facts. **51 of 76 core bots are tier A or B** — two thirds of the
library is a template instantiation plus params. **The 439 slot nodes in 18 dungeon bots are
34% of every node in the library**, and no amount of templating removes them. And the entire
"a template cannot express this" surface is **10 nodes**.

## Decisions taken

### `wait_for_health` — keep the node, keep one use, drop four

The measurement that decided it: **four of the five bots using it have no `handle_potions`
node at all.** The node is not competing with potions in those bots; it is standing in for
potion handling the bot never got.

| bot | potion handling | verdict |
|---|---|---|
| `spiral_cup_gauntlet` | none — housing gauntlet, cannot mark to refill | **legitimate, keep** |
| `queen_calypso` | none | add `handle_potions`, drop the wait |
| `secret_tunnel` | none | add `handle_potions`, drop the wait |
| `valerik_brightsword` | `mark_zone` empty, so refill cannot work | fix `mark_zone`, drop the wait |
| `monkey_spiders` | valid interior `mark_zone` | potions already work; the wait is redundant |

So the node's real contract is **"for zones where `handle_potions` cannot refill"** — housing
gauntlets today, boss rematches later, both entered through a housing item. That is worth
recording in its module docstring, because it is the fact that stops the next author reaching
for it as a general-purpose health check.

Keeping it with one live consumer is better than either alternative: a zero-use node is never
exercised by the suite or the corpus, and a deleted node has to be rebuilt from scratch when
the gauntlet bots land.

The one use that stays has a `FAILURE` self-loop on an uncapped node, which spins forever. That
is tracked with the other self-loop in *Stage 2 work* below.

### `check_stat(stat, threshold)` — merge and keep

`check_health` and `check_mana` stay, merged. The case is narrow but real: a boss that drops no
wisps, with wisps nearby, where collecting is free and instant and potions cost gold. Three
consumers: `praetor_mako`, `copy_qhat`, `pyramid_of_mother_moon_tartarus_workaround`.

The useful finding is *where* they sit. In both `praetor_mako` and `copy_qhat` the detour is in
the identical position — after the loop's exit or quicksell, before re-entry — which is the
farming loop's own top-up phase. It is therefore **a variant of the top-up step, not a second
slot**: the loop's "top up" can be one small definition with two shapes (potions only, or
wisps-then-potions) rather than a hole the consumer fills.

### `interact` and `interact_menu` — do not merge

Confirmed against the one bot that uses `interact_menu`. Its `menu_path` is **load-bearing
synchronisation**, not a tail: it waits for `NPCServicesWin/ControlSprite/optionsLayout` to
become visible so the following `click_path` does not race the menu open. Plain `interact`
would not wait, and the clicks would fire into nothing.

This also corrects the migration note in `docs/SUBGRAPH_CALLS.md`, which claims
`barkingham_palace_ransacked_throne_room` "becomes `interact` with `has_dialogue: false`". It
does not — that would break the bot, merge or no merge.

With 171 `interact` uses against 3, putting two more params on the common node to serve one bot
is the wrong trade. Both nodes stay as they are.

### Params: what gets locked, defaulted, or promoted

- **`collect_wisps` — locked in every template.** `only_safe`, `timeout` and `if_health_below`
  are one value across all 76 bots; the rest are user-or-machine facts.
- **`handle_potions.refill_potions` — promote to an app setting.** One value across the whole
  corpus, and it describes the user rather than the node, which is exactly the middle row of
  `CLAUDE.md`'s ownership table: a bot option declared `default=None` with an entry in
  `bot_options.APP_SETTING_KEYS`, resolved at run time and never written back into a param.
- **`enter_dungeon.offers_team_up` / `offers_challenge_mode` — template defaults, not locks.**
  The corpus reads lopsided only because the flags are recent and mostly unaudited, so the
  measurement is not a signal. The intent is team-up on for nearly every boss bot, challenge
  mode off for all but a few dungeon bots. Set the template defaults that way and leave both
  exposed.
- **`change_to_next_realm.start_realm_page` / `end_realm_page` — delete.** One value across all
  12 reagent bots, never changed, and not something an author should be changing. Removing them
  is what makes the `change_realm` merge cheap: the merged node has no mode-dependent params
  left to gate, so it needs no `enabled_by` / `disabled_by` work at all.
- **`handle_combat`** combat fields live in the strategy store keyed by state id, not in the
  template signature.

### Every failure edge relogs

Adopted in place of the second-tier recovery hub. A soft `relog` is the only step that clears
both of the dangerous stuck states — stuck in combat and stuck with the UI open — and
`teleport_to_world_hub` clears neither.

Two consequences, both good:

- **The 219 explicit second-tier failure edges across 26 bots simply disappear.** Under #11 an
  unrouted `FAILURE` already goes to `recovery_state`, and all six templates already bind
  `recovery_state` to `relog`. So this is edge *deletion*, not new machinery — and the rewrite
  does it for free.
- **It removes the need for a region-level failure binding.** Dropping those edges takes
  1-in/1-out regions from 83/133 to 102/133 and regions with more than two exits from 22 to 6.
  The port model no longer has to carry a second recovery tier.

**The templates' `relog --FAILURE--> relog` self-loop is correct as it stands**, and needs no
escalation. Three facts make it so:

- `relog`'s spec carries `max_consecutive_retries=2`, and `BotMachine` calls `stop()` on
  reaching it, so the loop is two attempts and then a clean stop — not an unbounded retry.
- The self-loop has to be explicit, because `docs/RETURN_ROUTING.md` deliberately exempts the
  recovery state's own failure from the default edge. Writing it is the author saying "retry
  the relog", and the retry cap is what bounds it.
- The node resumes health monitoring in a `finally`, on the failure path included. So a client
  that genuinely died gets caught by `_client_status_watcher` → `remove_dead_clients` →
  `_run_recovery_queue`, which force-closes and relaunches it. The hard path arrives on its
  own; nothing has to ask for it.

Escalating from the node is also not implementable: `ClientManager.relog_clients` calls
`machine.pause()` and then awaits `machine.wait_until_parked()`. Called from inside a node, the
run loop can never reach a boundary, so it would burn `RELOG_PARK_TIMEOUT` and refuse. The hard
path is reachable only from the watch loop, by design — its two callers are the scheduled timer
and the GDI ceiling.

### Deleted and deferred

- **`skeleton_key_boss_farmer_template.json` is deleted.** It did not run: 5 of its 10 states
  were unreachable and `enter_dungeon` had no outgoing transitions, making it a terminal state
  that stopped the run after entering the dungeon. Nothing referenced it — templates are
  folder-scanned with no manifest — and it will be rebuilt with the skeleton-key bots
  post-1.0.0, alongside the one new node that work needs (a key-door node whose third return
  means "out of keys" and carries an explicit stop).
- **`foulgaze` will be rewritten from the template**, not migrated. It exists mainly as the
  subgraph and player-sync experiment. That also retires the worry about its two lost subgraph
  definitions (`24556de8`, `201b8a96`): nothing needs re-extracting. What remains is that no
  project-root `subgraphs/` directory exists and `bots/subgraphs/sync_players.json` points at
  an id that lives only in `%APPDATA%`.
- **Missing `drops` are tracked separately** and are not a template concern.
- **The empty worlds wait for a stable template and subgraph baseline**, deliberately — new
  bots written now would only need migrating again, and writing them against the finished
  system is the intended end-to-end test of it.

## Stage 2 work: the nine outlier bots and two spinning self-loops

Everything here lands in `docs/SUBGRAPH_CALLS.md` Stage 2, before the templates are rebuilt,
because Stage 9 re-authors these bots and an unfixed defect would survive the rewrite
untouched. Each row is independent of the others and of the subgraph work.

### The retry cap is off

**`AppController` never passes `max_consecutive_same_state` to `BotMachine`** (see the
constructor call in `app_controller.py`), so it defaults to `None` and the global same-state
limit is disabled in production. `BotMachine` only stops a spinning state when the node's own
`NodeSpec.max_consecutive_retries` is set — true for 25 of 34 nodes, not for the other 9.

Across 205 self-loop transitions in the corpus, exactly two land on an uncapped node:

| bot | state | node | `when` | consequence |
|---|---|---|---|---|
| `spiral_cup_gauntlet` | `state_10` | `wait_for_health` | `FAILURE` | 120 s timeout, retried forever — an unbounded sequence of two-minute waits |
| `the_great_spyre` | `state_8` | `apply_level_scaling` | `FAILURE` | unbounded retry |

Two ways to close it, and the choice is open: give those two nodes a
`max_consecutive_retries`, matching how the other 25 do it and keeping the bound a property of
the node; or give `BotMachine` a non-`None` default, which also protects the remaining 7
uncapped nodes from a future author drawing the same edge.

For contrast, `relog`'s `--FAILURE-->` self-loop in all six templates is **correct and needs no
change**: the node carries `max_consecutive_retries=2`, the machine calls `stop()` on reaching
it, the explicit edge is required because `docs/RETURN_ROUTING.md` exempts the recovery state's
own failure from the default, and the node resumes health monitoring in a `finally` so a
genuinely dead client is force-closed and relaunched by the watchers on its own.

### The nine outlier bots

| bot | change |
|---|---|
| `queen_calypso` | add `handle_potions`; drop `wait_for_health` |
| `secret_tunnel` | add `handle_potions`; drop `wait_for_health` |
| `valerik_brightsword` | fix the empty `handle_potions.mark_zone`; drop `wait_for_health` |
| `monkey_spiders` | drop `wait_for_health` — the existing interior `mark_zone` already works |
| `spiral_cup_gauntlet` | **keep** `wait_for_health`; cap the retry so the self-loop terminates |
| `praetor_mako` | drop the `check_health`/`check_mana` wisp detour |
| `copy_qhat` | drop the `check_health`/`check_mana` wisp detour |
| `pyramid_of_mother_moon_tartarus_workaround` | drop the `check_mana` detour |
| `the_great_spyre` | cap the `apply_level_scaling` retry |

The three detours are not simply deleted at the bot level — they are what the opt-in wisp
subgraph re-expresses, so a bot that genuinely needs one gets it back by inserting that
subgraph. `docs/SUBGRAPH_CALLS.md` Stage 8 has its shape.

While editing `wait_for_health`, record its real contract in the module docstring: **for zones
where `handle_potions` cannot refill** — housing gauntlets today, boss rematches later, both
entered through a housing item. That sentence is what stops the next author reaching for it as
a general health check, which is how four of its five current uses happened.

## What this does to the subgraph port requirement

Worth stating, because it is much smaller than the current corpus suggests. After the rewrite,
`foulgaze`'s 6-incoming-edge sync region and every second-tier recovery fan-in are gone, and
the only shapes left are the templates'. What the two shipped definitions then need:

| definition | entry | exits | notes |
|---|---|---|---|
| **Player Sync** (`relog → go_to_zone → change_realm`) | none by edge | 1 | two members must be role-bindable: `initial` on `go_to_zone`, `recovery_state` on `relog` |
| **Farming loop** (`handle_potions → quicksell → enter_dungeon → [slot] → collect_wisps → exit`) | 1 | 1 plus the slot pair | top-up phase has two shapes (potions, or wisps-then-potions) |

So the requirement is one entry port, one exit port, a slot pair, and two role-bindable
members. **Roles, not edges, are the only source of multiple entries** — which is what the
plan already says, and it means the general multi-entry port machinery is not needed for
1.0.0.

## Coverage: what is missing

Core-4 bots per world today. Reagent bots are filed by activity under `25_crafting` rather
than by world, which is why they appear in no world row.

| world | boss | dungeon | spellement |
|---|---|---|---|
| Wizard City | 6 | 1 | 0 |
| Krokotopia | 0 | 1 | 2 |
| Marleybone | 2 | 3 | 0 |
| MooShu | 2 | 2 | 3 |
| Dragonspyre | 0 | 0 | 1 |
| **Grizzleheim** | **0** | **0** | **0** |
| Celestia | 4 | 1 | 0 |
| Zafaria | 0 | 1 | 0 |
| Avalon | 1 | 0 | 1 |
| Azteca | 1 | 0 | 1 |
| **Khrysalis** | **0** | **0** | **0** |
| Wysteria | 2 | 0 | 0 |
| Aquila | 2 | 0 | 3 |
| Polaris | 1 | 0 | 0 |
| Mirage | 2 | 0 | 1 |
| Empyrea | 0 | 1 | 0 |
| Arcanum | 1 | 0 | 0 |
| Catacombs | 1 | 1 | 0 |
| Karamelle | 0 | 0 | 1 |
| Lemuria | 1 | 0 | 1 |
| **Novus** | **0** | **0** | **0** |
| Wallaru | 2 | 0 | 0 |
| **Selenopolis** | **0** | **0** | **0** |
| Darkmoor | 2 | 1 | 0 |
| Four Dungeons | 1 | 5 | 1 |
| Five Boxes | 0 | 1 | 0 |

Four worlds have no bots at all, and eight more have a single one. Nine worlds have no boss
bot; sixteen have no spellement bot. All of it reuses the existing four templates, so the
net-new work is authoring rather than design, and it is deliberately queued behind the
baseline.

## Per-bot worksheet

### Boss — 31 bots

| tier | bot | world | nodes | nav to fold | slot | outliers | notes |
|---|---|---|---|---|---|---|---|
| A | `crash_the_bandit_coot` | Wallaru | 9 | 2 | 0 | — | uses `go_to_zone` |
| A | `lord_nightshade` | Wizard City | 9 | 0 | 0 | — | 2nd-tier recovery (3 edges); 1 region; uses `go_to_zone` |
| A | `prince_gobblestone` | Wizard City | 8 | 0 | 0 | — | 1 region; uses `go_to_zone` |
| A | `achaelos` | Celestia | 7 | 1 | 1 | — | — |
| A | `cronus` | Aquila | 6 | 1 | 1 | — | — |
| A | `jabberwock` | Avalon | 10 | 3 | 1 | — | — |
| A | `lord_bramble` | Wysteria | 11 | 1 | 1 | — | 2nd-tier recovery (3 edges); uses `go_to_zone` |
| A | `lord_graustark` | Wysteria | 11 | 1 | 1 | — | 2nd-tier recovery (3 edges); uses `go_to_zone` |
| A | `mr_cane` | Wallaru | 9 | 1 | 1 | — | uses `go_to_zone` |
| A | `mr_stone` | Arcanum | 9 | 3 | 1 | — | — |
| A | `scion_of_the_old_one` | Lemuria | 9 | 3 | 1 | — | 2nd-tier recovery (7 edges) |
| A | `sister_sanderson` | Darkmoor | 9 | 0 | 1 | — | uses `go_to_zone` |
| A | `advisor_fancy_fries` | Mirage | 10 | 2 | 2 | — | — |
| A | `bellows` | Marleybone | 6 | 1 | 2 | — | — |
| A | `big_salgio` | Celestia | 7 | 1 | 2 | — | — |
| A | `foulgaze` | Wizard City | 14 | 4 | 2 | — | 2nd-tier recovery (4 edges); 2 regions; **2 linked instances, both definitions lost** |
| A | `guillermo_shatterer` | Azteca | 7 | 1 | 2 | — | — |
| A | `hierophant_of_destruction` | Darkmoor | 12 | 3 | 2 | — | uses `go_to_zone` |
| A | `prince_viggor` | Catacombs | 10 | 3 | 2 | — | — |
| A | `sprockets` | Marleybone | 6 | 1 | 2 | — | — |
| A | `vigilant_jarilo` | Polaris | 9 | 2 | 2 | — | — |
| A | `alicane_swiftarrow` | Wizard City | 10 | 2 | 3 | — | 2 regions |
| A | `harvest_lord` | Wizard City | 10 | 2 | 3 | — | 2 regions |
| A | `oakheart` | MooShu | 8 | 1 | 3 | — | — |
| A | `sergeant_skullsplitter` | Wizard City | 10 | 2 | 3 | — | 2 regions |
| B | `graveyard` | Four Dungeons | 15 | 5 | 4 | — | 1 region |
| B | `mongu_refuge` | Mirage | 14 | 4 | 4 | — | — |
| B | `queen_calypso` | Celestia | 12 | 1 | 6 | wait_for_health | 2 regions |
| B | `sisyphus` | Aquila | 16 | 5 | 6 | — | 2nd-tier recovery (6 edges); 2 regions |
| B | `youkai` | MooShu | 12 | 1 | 6 | — | 2nd-tier recovery (3 edges); 2 regions |
| C | `praetor_mako` | Celestia | 15 | 2 | 5 | check_health, check_mana | — |

### Dungeon — 18 bots

| tier | bot | world | nodes | nav to fold | slot | outliers | notes |
|---|---|---|---|---|---|---|---|
| B | `throne_room_of_fire` | Krokotopia | 12 | 1 | 8 | — | 2 combats |
| C | `spike_the_crusher` | Marleybone | 17 | 3 | 10 | — | 2nd-tier recovery (14 edges); 2 combats |
| C | `barkingham_palace_main_hall` | Four Dungeons | 19 | 3 | 12 | — | 3 combats |
| C | `secret_tunnel` | Empyrea | 16 | 1 | 12 | wait_for_health | 3 regions; 3 combats |
| C | `king_detritus` | Catacombs | 20 | 3 | 13 | — | 3 regions; 3 combats |
| C | `barkingham_palace_state_wing` | Four Dungeons | 19 | 1 | 15 | — | 3 combats |
| C | `tree_of_life` | MooShu | 23 | 4 | 16 | — | 3 combats |
| C | `barkingham_palace_rooftop` | Four Dungeons | 21 | 1 | 17 | — | 5 regions; 3 combats |
| C | `mirror_lake` | Zafaria | 23 | 0 | 17 | — | 2nd-tier recovery (19 edges); 5 regions; uses `go_to_zone`; 4 combats |
| C | `katzensteins_lab` | Marleybone | 29 | 1 | 25 | — | 6 regions; 4 combats |
| D | `castle_darkmoor` | Four Dungeons | 34 | 4 | 26 | — | 5 regions; 2 combats |
| D | `heretical_academy` | Darkmoor | 37 | 3 | 29 | — | uses `go_to_zone`; 3 combats |
| D | `house_of_scales` | Four Dungeons | 44 | 7 | 34 | — | 2nd-tier recovery (4 edges); 5 regions; 6 combats |
| D | `ironworks` | Marleybone | 42 | 2 | 37 | — | 8 regions; 5 combats |
| D | `trial_of_the_spheres` | Celestia | 42 | 1 | 38 | — | 2nd-tier recovery (6 edges); 4 regions; 3 combats |
| D | `five_boxes_avalon` | Five Boxes | 46 | 2 | 39 | — | 2nd-tier recovery (44 edges); 3 regions; 4 combats |
| D | `crimson_fields` | MooShu | 48 | 1 | 44 | — | 2nd-tier recovery (4 edges); 8 regions; 6 combats |
| D | `waterworks` | Wizard City | 53 | 0 | 47 | — | 7 regions; uses `go_to_zone`; 8 combats |

### Spellement — 15 bots

| tier | bot | world | nodes | nav to fold | slot | outliers | notes |
|---|---|---|---|---|---|---|---|
| A | `gladiator_dimachaerus` | Aquila | 7 | 1 | 0 | — | — |
| A | `do_daga` | MooShu | 12 | 4 | 1 | — | 2nd-tier recovery (5 edges); 2 regions |
| A | `hall_of_heroes` | Lemuria | 12 | 3 | 1 | — | 2nd-tier recovery (8 edges) |
| A | `sand_squid_tentacles` | Aquila | 9 | 1 | 2 | — | — |
| A | `harlequin_knight` | Avalon | 12 | 1 | 3 | — | 2 regions |
| B | `eerkala` | Mirage | 21 | 7 | 5 | — | 2nd-tier recovery (10 edges) |
| B | `jade_oni` | MooShu | 12 | 1 | 5 | — | 1 region |
| B | `barkingham_palace_ransacked_throne_room` | Four Dungeons | 20 | 3 | 6 | interact_menux3 | 2nd-tier recovery (3 edges); 3 regions |
| B | `krokopatra` | Krokotopia | 15 | 3 | 6 | — | 2 regions |
| B | `oyotomi_the_defiler` | MooShu | 21 | 7 | 6 | — | 2nd-tier recovery (7 edges); 4 regions |
| C | `copy_qhat` | Karamelle | 21 | 4 | 6 | check_health, check_mana | 2nd-tier recovery (10 edges); uses `go_to_zone` |
| C | `biti_nirini` | Krokotopia | 23 | 3 | 12 | — | 2nd-tier recovery (9 edges); 3 regions |
| C | `pyramid_of_mother_moon_tartarus_workaround` | Azteca | 33 | 9 | 14 | check_mana | 2nd-tier recovery (12 edges); 3 regions; uses `go_to_zone` |
| C | `the_great_spyre` | Dragonspyre | 30 | 4 | 19 | — | 2nd-tier recovery (8 edges); 5 regions; 4 combats |
| D | `mt_olympus` | Aquila | 67 | 2 | 57 | — | 5 regions; 7 combats |

### Reagent — 12 bots

| tier | bot | world | nodes | nav to fold | slot | outliers | notes |
|---|---|---|---|---|---|---|---|
| A | `selenopolis_parchment` | Crafting | 4 | 2 | 0 | — | — |
| A | `the_portico_pearl` | Crafting | 7 | 2 | 2 | — | 2nd-tier recovery (5 edges) |
| A | `the_zocalo_red_mandrake` | Crafting | 7 | 1 | 2 | — | — |
| A | `nightside_flax` | Crafting | 7 | 3 | 3 | — | 2nd-tier recovery (3 edges) |
| A | `the_wild_deep_mushroom` | Crafting | 7 | 2 | 3 | — | — |
| B | `mirkholm_keep_reagents` | Crafting | 10 | 0 | 4 | — | 2 regions; uses `go_to_zone` |
| B | `scrap_iron_collector` | Crafting | 8 | 3 | 4 | — | 2nd-tier recovery (5 edges) |
| B | `the_necropolis_deep_mushroom` | Crafting | 10 | 2 | 4 | — | — |
| B | `crab_alley_stone_block` | Crafting | 12 | 3 | 5 | — | 2 regions |
| B | `mirkholm_keep_ore` | Crafting | 12 | 1 | 7 | — | — |
| C | `aeriel_shores_aethyr_burst` | Crafting | 17 | 2 | 11 | — | 2nd-tier recovery (14 edges); 3 regions |
| C | `aeriel_shores_silver_chests` | Crafting | 16 | 1 | 11 | — | 3 regions |

## Deferred: mob and gauntlet

Both have templates and neither is in the core four. Listed so they are not lost.

### Mob — 3 bots

| tier | bot | world | nodes | nav to fold | slot | outliers | notes |
|---|---|---|---|---|---|---|---|
| A | `fortee_thieves` | Mirage | 7 | 0 | 1 | — | — |
| B | `monkey_spiders` | Empyrea | 12 | 2 | 3 | wait_for_health | 1 region |
| B | `valerik_brightsword` | Dragonspyre | 12 | 2 | 4 | wait_for_health | — |

### Gauntlet — 3 bots

| tier | bot | world | nodes | nav to fold | slot | outliers | notes |
|---|---|---|---|---|---|---|---|
| B | `hidden_temple_gauntlet` | Housing Gauntlets | 10 | 1 | 6 | — | — |
| C | `grand_tourney_arena_gauntlet` | Housing Gauntlets | 23 | 1 | 19 | — | 6 regions; 5 combats |
| C | `spiral_cup_gauntlet` | Housing Gauntlets | 29 | 1 | 24 | wait_for_health | 6 regions; 6 combats |
