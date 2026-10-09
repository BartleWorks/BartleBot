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

`handle_level_scaling` has one param: which Scales to use, and it is usually left unset
(see "The default" below). Who gets scaled is the `Level Scaling Players` bot option. `check_level_scaling` still
exists as a condition node. `apply_level_scaling` is gone: the library moved off it (#59) and it was
deleted outright, so no bot file can name it. Its elixir code, `apply_elixir`, lives in
`handle_level_scaling`.

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

### Challenge Mode

Entering a dungeon in Challenge Mode level scales its players automatically. It shows no
elixir for that, and the Challenge Mode copy of the dungeon has no Scales of Knowledge.
That copy is a separate zone whose id is the ordinary one plus `_Challenge`, `_Hard` or
`_Adv` (`zone_names.challenge_variant_of`).

So when every client is standing in a Challenge Mode zone, the node returns `SUCCESS` and
does nothing. Without that, the missing elixir would send the players out of the instance
for one they do not need.

For the same reason the table never names a Challenge Mode zone, or a pattern that would
take one in. The node always enters through the sigil in normal mode, so it lands in the
ordinary zone. `tools/scales_table.py` writes the Scales' own zone even when the recorded
`enter_dungeon` holds a pattern, and `check` refuses a pattern or a Challenge Mode zone.

A bot needs no pattern for this any more: `node_helpers.zone_matches` treats a zone and its
Challenge Mode copy as one (#143), so a recorded ordinary zone matches a client in either.

### Challenge Mode and potions: the stat caps

Challenge Mode scales by the **stat caps**, the game's own table in `Root.wad`'s
`LevelScaledData.xml` (`tools/dump_stat_caps.py` writes it to `data/stat_caps.json`). A
stat is scaled by the cap at the dungeon's level over the cap at the player's own, and a
player is held to the caps of the lowest ten-level step it has not passed: 90 to the level
90 caps, 91 to 100 to the level 100 caps. Caps apply only in advanced combat, which here
means only Challenge Mode, and potions cannot be used inside it.

A player leaves with the health and mana it had inside, under its full caps again, so
leaving a level 40 dungeon at full health reads about 20% outside. Entering clamps current
health to the scaled max rather than scaling it, so the share that matters before the
next entry is the share of the *scaled* max. With the Challenge Mode option on,
`handle_potions` and `collect_wisps` read their ratios that way
(`nodes/combat/scaled_ratios.py`), and the same thresholds serve both modes. Before this a
Challenge Mode bot branched to a second `handle_potions` with lower thresholds, guessed by
hand.

The level is the bot's own: the `dungeon_zone` of an `enter_dungeon` offering Challenge
Mode, looked up by `stat_caps.dungeon_level`:

1. the Scales whose own zone is that dungeon. A secret boss's Scales one gateway further in
   (Pit of the Noxii inside Mount Olympus) do not count: in Challenge Mode they are gone and
   the boss is fought at the larger dungeon's level;
2. Wallaru (170) and Darkmoor (180), which have Challenge Mode and no Scales, from
   `stat_caps.WORLD_LEVELS_WITHOUT_SCALES`;
3. the world's default Scales, or the one level every Scales in the world shares (Azteca).

A player inside a Challenge Mode zone is read plainly, since the game has scaled its max
already. With no level found the ratios are plain too, which errs toward a potion. Boss
rematches have Challenge Mode but enter through a housing item, so nothing gives their
level; they will need it entered by hand once they are built.

`stats.school_id()` and `stats.reference_level()` give the school and level. The school id
is not yet checked in game against wizwalker's `school_to_str`; a school the table cannot
name takes the mean over the seven, which is within a few hundredths of any one.

### A rerun after a cancel

The ambient combat watcher can cancel the node mid-trip and run it again from the start
(`bartlebot/nodes/CLAUDE.md`). A rerun that marked again would mark somewhere on the way to
the Scales and return there with `SUCCESS`. So the marks are kept in
`machine.node_scratch` from the moment every client has marked, the rerun reuses them
instead of marking, and the record is dropped whenever the node returns. Marks kept for
different client objects are discarded.

## The table

`bartlebot/game/data/scales_of_knowledge.json`, read by `bartlebot/game/scales.py`. It holds names,
zone paths and coordinates, so it is text and safe to track.

| field | holds |
|---|---|
| `key` | what a bot file stores. Never renamed once shipped |
| `name`, `world`, `level` | what the picker shows. `level` may be null |
| `world_default` | present and true on the Scales of a world's final dungeon. At most one per world |
| `entrance_zone` | a destination on the zone navigation map, where navigation hands over |
| `steps` | the zone changes from `entrance_zone` to `zone`, in order |
| `zone`, `interact_xyz`, `reset_xyz` | the Scales' zone, the interact point, and a spot clear of its prompt |

A step is `sigil` (a dungeon entrance, run by `enter_dungeon` in normal mode, never Team-Up
or Challenge Mode) or `gateway` (a zone transition, run by `enter_zone`). Most entries are
one sigil. The Scales of a secret boss nested in another dungeon (Pit of the Noxii, The
Poseidon Adventure) add a gateway after it. A sigil that is indoors (Cavity Chamber) has
gateways before it, because its room is not a navigation destination. Every entry has at
least one sigil; the first one is where "inside the dungeon" begins.

`tests/game/test_scales.py` holds the shipped table to its rules: every entrance is a navigation
destination, every entry's steps chain from its entrance to its Scales, and every entry
loads.

### Adding a Scales

The file is never edited by hand. `tools/scales_table.py` builds an entry from a bot
recorded in the app, checks it, and writes the whole file back in one fixed format.

1. In the app, make a scratch bot and Record, standing in game:
   - an `enter_dungeon` on the dungeon's sigil;
   - an `enter_zone` for each zone change between the sigil and the Scales, or between a
     zone the zone picker offers and the sigil, when there are any;
   - an `interact` on the Scales: the point where its prompt shows, and a reset point where it
     does not. It records the same three fields the removed `apply_level_scaling` did.

   The nodes need no edges and no order. Save the bot anywhere.
2. Look at what the tool makes of it, then write it:

   ```bash
   uv run python tools/scales_table.py add path/to/scratch.json --level 100
   uv run python tools/scales_table.py add path/to/scratch.json --level 100 --write
   ```

The tool finds the steps by following zones back from the Scales to the first zone the
navigation map can reach. The name comes from the sigil's prompt title and the key from the
name; `--name` and `--key` override them, which a nested Scales needs, since its own zone
has no prompt. `--world-default` marks the Scales of a world's final dungeon.

`set KEY --level N`, `set KEY --world-default`, `remove KEY`, `list`, `check` and `format`
cover the rest. Every command that changes the file prints the result and writes nothing
without `--write`, and refuses to write a table that fails `check`. A key is what bot
files store, so `set` cannot change one.

`test_the_shipped_table_passes_the_tools_checks_and_is_in_its_format` fails on a file
that was edited by hand and reflowed.

The first thirteen entries were copied from the shipped bots' recorded params and have not
been re-checked in game as a set. Every entry's `level` was checked in game on 2026-10-04,
and every Scales in Aquila is recorded. Challenge Mode reads the levels too (see
*Challenge Mode and potions*).

## The default

An unset `scales` param means the default Scales of the bot's world, resolved when the node
runs (`scales.resolve`), not written into the bot. So a bot made from a template needs no
setup step for it, and the answer follows the bot as its zones are filled in.

- The bot's world is the one most of its zone params are in (`scales.bot_world`). A tie, or
  a bot with no zones yet, has none.
- A world's default is the entry marked `world_default`. A world with several Scales and no
  final dungeon (Aquila) marks none, and so does a world whose only entry is a side dungeon.

An unset param in a bot whose world has no default is an error on the node, and `FAILURE`
if it runs. A world not known yet waits for the zones that will decide it: in a template
draft, and in a bot whose setup steps still have blank fill-ins (which already block Run),
there is no error until a zone names the world.

Azteca is the one world whose final-dungeon Scales (`xibalba`) are in the table but not
marked. They are bugged: the Scales disappear after the first player uses them, so a group
cannot all scale there. An Azteca bot chooses `tartarus` by hand instead, which is also
level 90. Sending each player into Xibalba on a separate realm would give each their own
Scales, but that was judged too fragile to build.

## The picker

The `scales` param type (`bartlebot/gui/widgets/pickers/scales_picker.py`) lists the table by world,
with each row's level as its meta. Only the key is stored. A key the table lacks is an
error on the node, as a `nav_zone` that is not a destination is; the button keeps it and
marks it rather than dropping it. An unset button reads "Bot's world: …" with the Scales
the default resolves to.

## Not done

- **Split travel.** Sending only the players who need scaling, with no mark, would cover the
  zones where a mark fails. #97 is the same idea for `handle_potions`.
- **Migrating the library** and retiring the two old nodes: under #59.
