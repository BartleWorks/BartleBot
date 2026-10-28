# Walking

Moving a wizard now that the server resets a teleport: what the server allows, how a route is
planned from a zone's own files, how a live walk follows it, and every measurement taken so far.


Until late October 2026 every node that moved a wizard wrote its position to memory
(`client.teleport`, about 60 calls across 26 files) and the server accepted it. **The server
now resets a wizard to the last position it acknowledged** whenever the client claims one it
could not have walked to, so a written position is undone at once and BartleBot cannot move
at all. This document is the record of the work to move by walking instead. It was all done
as probes, on the `walk-planner` branch (off `ahmarra-test-realm`); nothing in `bartlebot/`
uses it yet. Each probe's docstring has its own OUTCOME section with the run-by-run numbers.

| probe | what it does | needs a client |
|---|---|---|
| `tools/probe_walk_route.py` | plans a route in any zone from its wad, draws it | no |
| `tools/probe_walk_input.py` | walks a planned route on a live client, around mobs, through links | yes |
| `tools/probe_walk_zones.py` | checks every zone's arrival points can reach each other | no |
| `tools/probe_mob_paths.py` | watches a zone's mobs and says whether they walk their paths | yes |

All four need numpy, which is not a project dependency yet: run them as
`uv run --with numpy python tools/<probe>.py`. Their output goes to `logs/walk_route/`.

## What the server allows

Everything below rests on one rule, inferred from every observation and contradicted by none:
**the server checks movement the client claims, never movement the server causes.**

| movement | who causes it | reset? |
|---|---|---|
| a position written to memory | the client | yes, at once |
| walking by the movement keys | the client, at walking pace | no, but see resets below |
| a teleport volume stepped into (Triton's Tele Up) | the server, when the wizard walks in | no |
| an interact teleporter (Triton's hub stones) | the server, when the interact key is pressed | no |
| a zone change, the World Gate, porting to a friend, Mark and Recall | the server | no |
| the run to a duel circle when a fight starts | the server | no |
| the yaw (facing) written to memory | the client | **no**: the camera turns a wizard at any rate, so the server does not check it |

So a bot reaches anything by walking to it, and takes every shortcut the game itself offers.

**Resets happen during ordinary walking too.** On a +40% mount the server threw the wizard back
seven times across the walks recorded here, 221 to 655 units each time, to where it had stood
about 0.7s earlier; players see the same rubber-banding. A walk that reads the position back every
tick absorbs one by re-walking the stretch, at a cost under a second. Nothing about a reset
needs handling beyond that.

**The speed multiplier may be written, up to 2.0x.** The game engine is built to allow a 2.0x
speed multiplier, so a walk may write the client's `speed_multiplier` (a percentage,
`write_speed_multiplier` in wizwalker) up to 100. That field holds the whole bonus, a mount's
included, so 100 is 2.0x in total and never 2.0x on top of a mount. If a wizard at 2.0x is
reset more often than on a mount, the value comes down; it is never raised above 100 to find
where the server's limit is.

**Two things this project does not do, and the reasons stand:** it does not work out the
server's tolerance for a teleport (sub-threshold teleports), and it does not forge or replay
movement. Both are beating a measure the developer added on purpose, a public repository doing
either invites a takedown, and the tolerance is the developer's to tighten whenever it likes.

## Planning a route from a zone's files

`probe_walk_route.build_planner(zone)` builds everything from the zone's wad
(`GameData/<zone with / as ->.wad`), read with katsuba through `tools/zone_graph/wad_common.py`.
No client is needed; a zone takes 0.1 to 2s (Unicorn Way about 4s with everything).

### The floor: `collision.bcd`

The parser follows Deimos' `src/collision.py` (PeechezNCreem, ISC); the rule for when a solid
blocks a foot follows Deimos' `src/collision_math.py` (GPL-3, as BartleBot is).

- **Floor** is every mesh with the WALKABLE flag (bit 1), rotated and placed by its matrix
  (applied transposed, as Deimos does). Faces steeper than a normal z of 0.5 are cliff, not floor.
- **Walls** are the boxes, cylinders and spheres, unless flagged client-object, trigger,
  hitscan or fog. A solid blocks a cell only where its height range meets the floor there
  (Deimos' `_collider_blocks_foot`), which is what lets a ramp pass under a tall cylinder. A
  solid based at or below the floor blocks once it rises 20 above it. Deimos lets a round one
  rise 75, as a big cylinder is often the coarse bounds of a whole structure whose floor lies
  inside it (Celestia's chamber, radius about 834); **only a round one 400 or more across is
  given that.** Mirage's hub has a rock, a cylinder of radius 143 rising 56 above the street,
  that a route went straight through: walked into, the wizard slid around it holding 182 to 183
  units from its center, which fits radius 142.6 (and puts the wizard's own radius nearer 40
  than 49). Counting small rounds as firm closed 0.7% of Mirage's hub and 0.1% of the other
  zones checked, and changed no route walked before.
- **A cylinder stores (length, radius), the reverse of how Deimos reads it.** Found by walking
  into a tree in Unicorn Way stored as (264.9, 85.7): the wizard slid around it holding exactly
  135 units from its center, which is radius 85.7 plus the wizard's own 49. Read Deimos' way
  it is a 265-wide disc the wizard stood inside of, too short to count as a wall, and every
  tree was missing from the grid. Deimos' collision teleport presumably has the same bug.
- **Non-walkable meshes are ignored.** 103 surveyed zones have 250 of them. Triton's three are
  water (flag bit 6). Whether any are real walls is unchecked.
- **Placed objects' collision is not in `collision.bcd`.** An object's template can carry a
  `CollisionBehavior` with its own `m_solidCollisionFilename` (Triton's teleporter stones use
  `MU_TeleportStoneCollision.xml`), and the grid does not read those yet. Every walk up to a
  stone stalled on it once; the walk recovers (below), but the planner should know.

The grid is 40-unit cells, with the player's clearance taken as 50 (49 measured). **Each cell
keeps every floor it has** (floors more than 150 apart are separate; a 3-storey house has
cells with three), and a step goes only to the neighbor's floor at about the same height
(rise up to 1 unit per unit walked, plus 20 for a tread or kerb). That is what lets stairs,
and nothing else, join storeys. Floor is passable only if every cell within the clearance has
floor at about its height, which keeps a route off both walls and drop edges.

### Mobs: `spawnData.xml`, `pathData.xml`, `pathNodeData.bin`

Every mob spawner names a path; the path is a list of node ids, and the nodes carry
positions. A spawned template is a mob if it has `DuelistBehavior`, and its `m_npcProximity`
is the trigger radius (200 for most Wizard City mobs, 275 for dark sprites). Both start node
types walk their paths:

| `m_kStartNodeType` | starts at | checked | spawns surveyed |
|---|---|---|---|
| 0 | the path's first node, then loops it | Unicorn Way: 561 samples a median 23 units off the line, max 370 | 2,759 in 66 zones |
| 1 | one of the path's nodes, then walks it | Triton Avenue: 6 of 6 ghosts walked their path, p90 within 35 units | 3,978 in 219 zones |

Paths loop: samples sit on the segment from the last node back to the first.

**The client lists only the mobs near the wizard** (12 of Triton's 96 spawns). Live positions
cover what is close; the paths cover the rest, which is why the planner avoids every path.

The danger cost is per cell: its **clearance**, how far outside the nearest path's mob's own
trigger radius it is. Beyond 1300 the cost is nil; it rises with the square toward 250; under
250 a step costs 50 times its length. Every route the cost picked in Unicorn Way kept at least
250 units outside every trigger radius by hugging the street edges, for 4 to 20% more
walking, and those routes were checked in game.

Measured trigger distances (`tools/probe_aggro_radius.py`, September 2026): 270 to 390 units
from a still or slow mob, 650 to 886 from one closing at 150 to 400 units a second (client lag).

### In-zone links

Some zones are pieces of floor no walk joins. **A link is anything the game moves a wizard
through**, and the server accepts its move because the move is its own. There are two ways to
take one: **step into a volume**, or **press the interact key at a placed object** (a
fast-travel stone, a zipline, a ladder, a rope). Every one taken so far, of either kind, set
the wizard down within 5 units of one of the zone's named arrival points (`gamedata.bin`'s
`m_locationList`), so a link is planned as *where it is taken* plus *which arrival point it
lands on*. **No client file says where any link lands**: a volume's trigger names only the
event it raises, and every world object's template has `DeletedBehavior` where the behavior
that moves the wizard would be (`TeleportProximityBehaviorTemplate`, whose `m_locationName`
is exactly that landing, survives only in housing and guild templates). So the landing is put
together from what the files do keep, in this order (`probe_walk_route.py`'s `zone_links`):

| source | gives | where it holds |
|---|---|---|
| a volume's name | the arrival point sharing the most words, "to"/"from" breaking a tie, no link where two fit as well | Triton's six volumes (four walked, 1 to 5 off) |
| `zone.nav` | for a volume the name leaves on its own piece, if its name says it moves the wizard (teleport, to), the far end of the quest arrow's edge starting at it | Lemuria's tower lifts, each named for the end it stands at; Krokotopia's Forgotten Tombs |
| a ride's template | a zipline's `InteractableBehavior` holds its two ends as LOCATION records; it lands on the arrival point nearest the second (800 to 920 past it) | all four Floating Mountains ziplines; two pressed, 3 off each |
| a stone's partner | a `TeleporterX` stone lands at the arrival point nearest `TeleporterX_ToMain`, and that one at the arrival point nearest the hub's stones | Triton's four (pressed, 1 to 5 off); Colossus, Cyclops, Firecat, nine Krokotopia zones and Hametsu give names like "StreetB", "Target_Arena", "BackToHub", "HamPorter2" |
| an X / X_B pair | each at the arrival point nearest the other | Krokotopia's Forgotten Tombs teleporters |
| `zone.nav` | for any other object whose prompt is a travel verb, the far end of the edge starting nearest it (within 700) | Mirage's caravan ladders (four climbed, 0 to 4 off; Ladder 04 has no edge), Tyrian Gorge's six ("Ladder 01 top", ...), Novus's ropes, Krokotopia's hub teleporter |

What the sources are:

- **`zone.nav`**, in every zone's wad, is the graph the quest arrow routes through
  (`tools/zone_graph/zone_nav.py`): `zonemap.nav`'s layout without the name table, the 12 bytes
  that are zero there holding each node's x, y, z. Most nodes sit on a 480-unit grid; **the
  links are its edges no walk takes**, longer than 1,500 across the floor or rising more than
  500 more steeply than 1:1 (a ladder). Edges are stored both ways and say nothing of what they
  are, and an end can sit a few hundred units off, so an edge is only trusted next to a volume
  or a travel object. One landing on another piece of floor wins, as an edge whose ends share a
  floor is a walk (Arcanum's lift foot starts one to the other lift's foot); of the rest, where
  two start about as near one, the shorter wins (the top of Mirage's
  Ladder 06 starts its climb down and a 7,004-unit edge across the zone). It lacks some links:
  Triton's stones are not in it, nor Mirage's Ladder 04.
- **A travel object** is a placed object whose template's prompt, from `GUI.lang` or its own
  words in `WizardGameObjects.lang`, says Teleport, Ride, Climb, Descend, Ascend, Travel or
  Exit (613 templates), or, with no prompt, whose name says teleport, rope, ladder, zipline,
  boat, elevator, lift, cart, dinghy or portal.
- **A volume link must join two pieces of floor**: that alone throws out zone exits, whose
  names match an arrival point on their own piece ("Street4 to Cavern"). **An object link is
  kept if it joins two pieces or moves the wizard 1,000 units or more**, as a stone is worth
  pressing even where the far end can be walked to; the route's cost decides. An object within
  300 of a volume is the pad drawn there (Triton's `Telepapad`s), not a link. Cinematic points
  are left out.

None of this is the server's word, so a link that moves nobody is dropped by the walk and the
route replanned. That has already paid off: Floating Mountains has `AZ-ZipLine-3` and a
`-3-Quest` copy at the same spot; the first moved nobody, and the walk took the second.

**A zipline is a teleport, not a ride**, and so is a ladder. Pressed, a zipline plays its ride,
and 2.2s later the server moves the wizard in one tick (5,148 units) onto the arrival point; a
ladder moves it in one tick 0.06 to 0.12s after the press. No reset on any.

**A press counts only once the game shows the prompt.** Mirage's Ladder 02, pressed one tick
after the wizard stopped beside it, did nothing; pressed once the prompt was up, it climbed.
The prompt took 0.2 to 0.4s to appear.

`probe_walk_input.py --interact` still learns an unknown one by pressing it: it walks to a
placed object, presses, and reports how soon, how far, one jump or many steps, and the arrival
point it settled nearest.

`probe_walk_zones.py` across every zone on the zone map or named by a bot (455). Of the 387
with two or more arrival points:

| links used | all on one piece | joined by links | still apart (points) |
|---|---|---|---|
| volumes by name | 295 | 2 | 63 (299) |
| all of the above | 295 | 21 | 44 (135) |

295 links in 56 zones: 189 volumes, 101 objects pressed, 5 rides. 38 zones have a point off
the grid, nearly all cinematic or marker spots. The worst left: Empyrea's Husk (14 points),
Big Ben's counterweights and tower (levers and lifts), MooShu's Waterfall Cave, the Hive,
Forlorn Taig, Dragonspire's Arena. Each has a map in `logs/walk_route/zones/`.

### Routes

A* over the cells (8 neighbors, no cutting a wall's corner), with each link an extra edge from
the floor near where it is taken (100 units of a volume's center, 150 of an object's) to the
landing, costing 4 seconds of walking for a volume, 6 for a press, 20 for a ride. The
heuristic does not know about links, so a route through one is found but not always the
shortest. The route is then straightened: from each kept point, the furthest point still
reachable in a straight line without coming nearer a patrol than the route itself did. That
cuts 100 to 300 grid turns to 20 to 40. A route with links comes back as **legs**, each
straightened alone and each but the last ending at the link's volume, or on the floor beside
the object to press.

## Walking a route

`probe_walk_input.py --to <arrival point, placed object or x,y[,z]>` walks from wherever the
wizard stands.

- **Keys are the account's own.** On `ahmarra-test-realm` they come from the client's memory
  (`clients/game_keys.key_map`), and that reader works on the live game too: MoveForward,
  MoveBackward, MoveLeft and MoveRight (A and D *turn* a wizard; these are not strafes) and
  NPCInteract. Before that branch, `Bin/config.xml`'s `MoveForwardStart` and the rest; failing
  both, W S A D and X. When the branch merges, these five actions belong in its `GameAction`
  list so a run refuses to start without them.
- **Steering writes the yaw.** wizwalker's yaw is 0 facing -y, growing counterclockwise
  (`atan2(-dx, -dy)`). Hundreds of writes, none undone. `--turn-keys` steers with the keys
  instead (A turns the yaw down at about 152 degrees a second), and tracks the route less closely.
- **Speed.** Walking is horizontal only: a stair's height is free. On foot it measured 600 on
  flat ground (wizwalker's constant says 580); a mount multiplies it, most by 1.4 and the
  fastest by 1.55. The client's `speed_multiplier` (a percentage) is read to plan; once
  walking, the measured speed takes over. On the +40% mount: 840 expected, 838 measured. No
  probe writes it yet; writing it up to 100 is allowed (above) and not yet measured.
- **Following** a leg: hold forward, re-aim at the next waypoint each 50ms tick, pass a
  waypoint within 80 units or once past it along its leg, stop within 50 of the goal.
- **Stalls** (forward held 1.5s, under 60 units moved): back off 150 units with the backward
  key, close the floor 80 units ahead within 110, replan from there; up to 3 times. A wizard
  grazing a round trunk slides around it unaided; only one walking straight at an obstacle stalls.
- **Mobs.** A watcher reads every listed mob's position five times a second and estimates its
  velocity. Each tick the walk projects itself 2.5s along its route and every mob along its
  velocity, and wants each mob's trigger radius plus 120, plus 40 for every second looked
  ahead. **A mob walks at about 40 to 180 units a second and a wizard at 600 to 840**, so next
  to a wizard a mob is nearly a standing obstacle: short of room, the walk first plans a
  detour closing each mob out to that same room at three moments over the 2.5s, and only
  without one waits or backs off, for 30s at most. Detours plan with the patrol cost even on
  a shortest-path walk, so after one the rest of the leg follows the patrol-avoiding route.
- **Links.** At the end of a leg the walk stands in the volume, or faces the object and
  presses the account's interact key once the game shows its prompt (wizwalker's
  `is_in_npc_range`, waited on up to 2s), again every 2s, until moved more than 300 units
  (rising counts, for a ladder); after a press it waits for the wizard to be set down (under 20 units in 1s), then
  replans from wherever it landed. A jump near the pending link's landing is that link, taken
  early. Not moved within 6s, the link is dropped and the walk replans: a wrong pairing and a
  quest gate look the same.
- It stops on a battle, a zone change, the goal, or running out of time, and releases every
  key however it stops.

Results, all with no battle unless said:

| walk | result |
|---|---|
| Unicorn Way, gate to Tower 1, keys only | 46 units off, 35.2s against 35.5s planned, median 9 off the route |
| Unicorn Way, Hedge Maze to the gate, patrol-avoiding route | 14 off, 55.2s for 44,880 units, never nearer than 163 beyond a trigger radius and margin, 3 resets absorbed |
| Unicorn Way, gate, Tower 1, Tower 2, Hedge Maze in turn, shortest | all reached; one detour each; waits of 11.7s, 0, 0 |
| Unicorn Way, through the Ghosts 02 loop, waiting only | stopped after 30s held up: a loop of 4 ghosts never clears |
| the same with detours | crossed in 11.2s, 3 detours, 2.1s waiting, least room 11 |
| Housing_FarmHouse, main floor to basement and back | 18 and 16 units off; two flights, z 3100 to 2300 |
| Triton Avenue, two volume links in one route | both taken, landings 5 and 4 units off |
| Triton Avenue, four fast-travel stones | all landed 1 to 5 units off their arrival point |
| Floating Mountains, From3Points to Target-FromTurquoiseCave over ZipLine-3 | ZipLine-3 moved nobody and was dropped; -3-Quest landed 3 off "Target-ToMajorIsland"; 33 off the goal in 29.9s, two detours |
| Floating Mountains, back over ZipLine-4 | landed 3 off "Target-FromMajorIsland", 2.2s after the press; 13 off the goal in 23.7s, one stall recovered |
| Mirage's hub, up Ladder 02, pressing as it stopped | moved nobody: pressed before the prompt showed |
| Mirage's hub, Ladder 02 down, Ladder 05 up, waiting for the prompt | each landed 1 off its arrival point |
| Mirage's hub, Ladder 05's top to Ladder 01's | no ladder: the caravan roofs join, 5,695 units walked |
| Mirage's hub, down Ladder 01 and up Ladder 07 in one route | 4 and 3 off; 0 off the goal in 23.3s, one stall recovered (on a rock the grid let through) |
| Mirage's hub, the same stretch with small rounds firm | no stall; the route kept 333 from the rock's center |

## What is not done

In rough order of what a bot needs first.

1. **Placed objects' collision.** Read each placed object's `CollisionBehavior` solid file into
   the grid, so a walk does not have to stall on a teleporter stone to find it.
2. **Walk the link kinds not yet tried**: a rope (Novus's Stone of Heaven), a Lemuria tower
   lift, a Krokotopia stone pair, an X / X_B pair. How near an object a press must be is
   assumed (150; every Mirage ladder took a press from where its leg ended); whether facing it
   matters is not known (the walk faces it anyway).
3. **The 44 zones still apart**: their maps, starting with the worst. Levers and lifts that
   change state (Big Ben) may need the quest's state, not a link.
4. **A checked-in table of verified links**, as `zone_nav.json` keeps verified gates, so a
   pairing once walked is not re-derived, and one that moved nobody is not tried again.
5. **Zone exits.** A walk's goal across zones is the zone map's route (`docs/ZONE_NAVIGATION.md`)
   with each crossing a link to walk to; today every crossing is reached by `client.teleport`.
6. **Into the app.** A walk node, and every `client.teleport` replaced by walking to the spot
   (`nodes/node_helpers.py`'s interact loops, `teleport_to_coordinates`, `collect_*`,
   `enter_zone*`, `ambient_combat`, `handle_potions`), with the timing constants moved into
   `nodes/timing.py`, numpy added as a dependency, and the pure helpers (`room`, `choose`,
   `along`, `key_code`, `link_candidates`, `zone_links`, `zone_nav.parse`, `_straight_ok`) given tests. Several clients walking
   together, and a grid built per zone in a worker thread, belong here too.
7. **Smaller:** whether any of the 250 non-walkable meshes are walls; decoding a volume's radius
   and shape; whether a quest-gated link can be told from the client before stepping into it.
