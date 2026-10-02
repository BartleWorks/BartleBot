# Level scaling

How `handle_level_scaling` works, the table it reads, and the warning at run start. The
decisions came from #141.

## Game background

- Every world except Wizard City has a **Scales of Knowledge** in its final dungeon, and
  some level-gated dungeons have one too. There are roughly 25.
- Interacting with a Scales gives the level scaling elixir, free. It scales the player's
  stats down to that dungeon's level, and a scaled player can receive more loot. It lasts
  two hours.
- The game draws every elixir with the same HUD indicator (`LEVEL_SCALING_ELIXIR_PATH`).
  Telling the level scaling elixir from any other means opening it, which is too slow to do
  on every check.

## One node

`handle_level_scaling` has one param: which Scales to use. Who gets scaled is the
`Level Scaling Players` bot option. `check_level_scaling` and `apply_level_scaling` still
exist until the library moves off them (#59).

| step | what happens | fails when |
|---|---|---|
| Check | Every Level Scaling Player shows an elixir: `SUCCESS` at once | never |
| Mark | Away from the Scales' dungeon, every client marks where it stands | no mana, or a zone that refuses marks |
| Travel | Zone navigation to the entrance zone, then the entry's steps | any step fails |
| Apply | The players who lack the elixir take it, one at a time, with retries | one still lacks it after `MAX_RETRIES` |
| Recall | If the clients marked, each recalls and is checked against its own mark | a recall fails or lands elsewhere |

`SUCCESS` always means every selected player is scaled and the players are where the bot
expects them.

### Mark and recall are decided by where the clients are

There is no checkbox. Clients away from the Scales' dungeon mark, travel, scale and recall.
Clients already inside it scale in place and stay. "Inside" is read from the zone: the
clients share one zone, and it is the destination of the entry's sigil step or of a step
after it (`plan_trip`).

A checkbox would add one case, away with recall off, where `SUCCESS` leaves the players at
the farm when nobody needed scaling and in the Scales' dungeon when somebody did. An author
could not route from that.

In a bot that farms the Scales' own dungeon, the node goes after the bot's dungeon entry.
Placed before it, the players mark outside, go in, and recall back out. That costs a trip
and breaks nothing.

### No elixir is ever cancelled

#141 first planned to cancel every selected player's elixir at the Scales so that all the
timers started together. That was dropped as too risky: a cancel followed by a failed apply
leaves a player worse off than before. So a player who has an elixir keeps it, and only the
players who lack one visit the Scales.

Two things follow, and the bot can fix neither during a run:

- A player with a different elixir active reads as level scaled and runs unscaled.
- A level scaling elixir taken earlier runs out before the others', which costs another trip.

So the run request warns instead. `AppController._on_run_requested` reads the indicator on
every Level Scaling Player when the bot holds a level scaling node, and
`level_scaling_warning` adds a safety warning naming the players who already have one. The
read is async and the dialog blocks, so the read runs as a task and the dialogs run from its
done callback, outside any task (see "qasync + Python 3.13" in `bartlebot/gui/CLAUDE.md`).
A read that fails warns about nobody and the run starts.

### A rerun after a cancel

The ambient combat watcher can cancel the node mid-trip and run it again from the start
(`bartlebot/nodes/CLAUDE.md`). A rerun that marked again would mark somewhere on the way to
the Scales and return there with `SUCCESS`. So the marks are kept in
`machine.node_scratch` from the moment every client has marked, the rerun reuses them
instead of marking, and the record is dropped whenever the node returns. Marks kept for
different client objects are discarded.

## The table

`bartlebot/data/scales_of_knowledge.json`, read by `bartlebot/scales.py`. It holds names,
zone paths and coordinates, so it is text and safe to track.

| field | holds |
|---|---|
| `key` | what a bot file stores. Never renamed once shipped |
| `name`, `world`, `level` | what the picker shows. `level` may be null |
| `entrance_zone` | a destination on the zone navigation map, where navigation hands over |
| `steps` | the zone changes from `entrance_zone` to `zone`, in order |
| `zone`, `interact_xyz`, `reset_xyz` | the Scales' zone, the interact point, and a spot clear of its prompt |

A step is `sigil` (a dungeon entrance, run by `enter_dungeon` in normal mode, never Team-Up
or Challenge Mode) or `gateway` (a zone transition, run by `enter_zone`). Most entries are
one sigil. The Scales of a secret boss nested in another dungeon (Pit of the Noxii, The
Poseidon Adventure) add a gateway after it. A sigil that is indoors (Cavity Chamber) has
gateways before it, because its room is not a navigation destination. Every entry has at
least one sigil; the first one is where "inside the dungeon" begins.

`tests/test_scales.py` holds the shipped table to its rules: every entrance is a navigation
destination, every entry's steps chain from its entrance to its Scales, and every entry
loads.

### Adding a Scales

Record the sigil with `enter_dungeon`'s Record and the Scales with `apply_level_scaling`'s,
then copy the zones and points into a new entry. Pick an `entrance_zone` that the zone
picker offers, and add gateway steps from it if the sigil is further in.

The first thirteen entries were copied from the shipped bots' recorded params and have not
been re-checked in game as a set. The `level` values other than the three level 90 Aquila
ones are world level caps, not read from the game.

## The picker

The `scales` param type (`bartlebot/gui/widgets/scales_picker.py`) lists the table by world,
with each row's level as its meta. Only the key is stored. A key the table lacks is an
error on the node, as a `nav_zone` that is not a destination is; the button keeps it and
marks it rather than dropping it.

## Not done

- **Split travel.** Sending only the players who need scaling, with no mark, would cover the
  zones where a mark fails. #97 is the same idea for `handle_potions`.
- **Defaulting the picker** to the Scales of the bot's own world.
- **Migrating the library** and retiring the two old nodes: under #59.
