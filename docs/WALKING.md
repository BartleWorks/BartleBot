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

**Two things this project does not do, and the reasons stand:** it does not work out the
server's tolerance to move faster than walking under it (sub-threshold teleports, or writing
the speed multiplier), and it does not forge or replay movement. Both are beating a measure
the developer added on purpose, a public repository doing either invites a takedown, and the
tolerance is the developer's to tighten whenever it likes. A legitimate mount's speed is read,
never written.

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
  (Deimos' `_collider_blocks_foot`), which is what lets a ramp pass under a tall cylinder.
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
through**, and the server accepts its move because the move is its own. Two kinds are known.

**Volume links** fire when the wizard walks in. `volumes.xml` gives each volume's name,
position and (undecoded, a float near 177) radius, and marks it `STATIC_CLIENT_SERVER`; the
trigger in `triggers.xml` names the event it raises. **Nothing decoded so far names where it
lands**, so the planner pairs it by name with an arrival point (`gamedata.bin`'s
`m_locationList`):

- words shared between the names, "to" and "from" breaking a tie ("From Kraken Isle" lands at
  "From_Kraken_Isle", not "To_Kraken_Isle"), and no link where two landings fit as well;
- **a link must land on another piece of floor than it starts from.** This alone throws out
  zone exits, whose names match an arrival point on their own piece ("Street4 to Cavern",
  "Cavern to Street4");
- cinematic triggers are left out on both sides.

Triton Avenue's, all six found, four walked live:

| volume | lands at | walked |
|---|---|---|
| Tele Up | Target (Tele Up) | 2 units off, 835 jumped |
| Tele Down | Target (Tele Down) | 4 off, 1,489 jumped |
| To TempestNexus | Target Location (TempestNexus) | 1 off, 7,192 jumped |
| High_Ledge | Target location From_High_Ledge | 5 off, 7,597 jumped |
| From Kraken Isle | Target location From_Kraken_Isle | not walked |
| Activator Volume To_Kraken (quest-gated) | Target location To_Kraken_Isle | not walked |

Most fired as the wizard crossed the volume's edge, not its center. The `Telepapad_01` to
`_06` objects are only the pads drawn at these volumes.

**Interact links** fire when the interact key is pressed near a placed object. **Their
destinations are not in the client's files at all**: the object's template has
`DeletedBehavior` where the behavior that moves the wizard would be (the client gets a stub;
the server keeps the real one). So an interact link is learned by pressing it once
(`probe_walk_input.py --interact`). Triton's fast-travel stones, all four pressed:

| pressed X at | lands at | off | jump |
|---|---|---|---|
| `WC_Triton_TeleporterA` (hub) | Target location Triton_A | 1 | 20,479 |
| `WC_Triton_TeleporterB` (hub) | Target location Triton_B | 5 | 17,982 |
| `WC_Triton_TeleporterA_ToMain` (east end) | Target location Hub Teleporter | 2 | 20,295 |
| `WC_Triton_TeleporterB_ToMain` (west end) | Target location Hub Teleporter | 2 | 17,724 |

Each moved the wizard 0.2s after the press, in one jump, settled by 1.2s, pressed from 26 to
32 units off the nearest open floor to the stone. Here the pairing happens to follow the
names (stone X to "Triton_X", `_ToMain` to "Hub Teleporter"); that is not known to hold
elsewhere.

**Ziplines** (Azteca's Floating Mountains: `AZ-ZipLine-1` to `-4`, and a `-3-Quest` copy) are
interact links that carry the wizard over to another island rather than jumping it; their
templates are also all `DeletedBehavior`. **Not tried yet.** `--interact` tells one jump from
a ride and waits for the wizard to settle, so it is ready for one.

`probe_walk_zones.py` across every zone on the zone map or named by a bot (455): of the 387
with two or more arrival points, **295 have them all on one piece, 2 more are joined by volume
links, and 63 are still apart**; 38 have a point off the grid, nearly all cinematic or marker
spots. The names of the stranded points say what joins the rest: ladders ("Ladder01-Up"),
lifts and levels (Big Ben, "Floor_02_to_01"), fast-travel stones, ziplines, gauntlet tents.
**So most of the game's links are interact links, whose destinations have to be learned live.**

### Routes

A* over the cells (8 neighbors, no cutting a wall's corner), with each link an extra edge from
the floor within 100 units of its volume to the landing, costing 4 seconds of walking. The
heuristic does not know about links, so a route through one is found but not always the
shortest. The route is then straightened: from each kept point, the furthest point still
reachable in a straight line without coming nearer a patrol than the route itself did. That
cuts 100 to 300 grid turns to 20 to 40. A route with links comes back as **legs**, each
straightened alone and each but the last ending at the link's volume.

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
  walking, the measured speed takes over. On the +40% mount: 840 expected, 838 measured.
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
- **Links.** At the end of a leg the walk stands in the volume until moved, then replans from
  wherever it landed; a jump near the pending link's landing is that link, taken early. Not
  moved within 6s, the link is dropped and the walk replans: a wrong pairing and a quest gate
  look the same.
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

## What is not done

In rough order of what a bot needs first.

1. **Placed objects' collision.** Read each placed object's `CollisionBehavior` solid file into
   the grid, so a walk does not have to stall on a teleporter stone to find it.
2. **Interact links as data.** Offline, find the interact-link candidates in every zone (placed
   objects whose templates have a stripped behavior, or names like Teleporter, ZipLine,
   Ladder); learn each destination once with `--interact`; keep the verified table checked in,
   as `zone_nav.json` keeps verified gates. Then the planner takes them like volume links,
   pressing the interact key at the end of the leg.
3. **Ziplines and other rides**: try one, confirm `--interact` reads it right, and see whether
   the server resets mid-ride.
4. **The 63 zones still apart**: rerun `probe_walk_zones.py` once interact links exist; whatever
   is still split is worth a look at its map in `logs/walk_route/zones/`.
5. **Zone exits.** A walk's goal across zones is the zone map's route (`docs/ZONE_NAVIGATION.md`)
   with each crossing a link to walk to; today every crossing is reached by `client.teleport`.
6. **Into the app.** A walk node, and every `client.teleport` replaced by walking to the spot
   (`nodes/node_helpers.py`'s interact loops, `teleport_to_coordinates`, `collect_*`,
   `enter_zone*`, `ambient_combat`, `handle_potions`), with the timing constants moved into
   `nodes/timing.py`, numpy added as a dependency, and the pure helpers (`room`, `choose`,
   `along`, `key_code`, `link_candidates`, `_straight_ok`) given tests. Several clients walking
   together, and a grid built per zone in a worker thread, belong here too.
7. **Smaller:** whether any of the 250 non-walkable meshes are walls; decoding a volume's radius
   and shape; whether a quest-gated link can be told from the client before stepping into it.
