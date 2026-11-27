# Game data and tools

How to read the game's own data -- type dumps, `Root.wad`, `katsuba` for BINd objects, and
why window names need a live client -- is core's `docs/GAME_DATA.md`. **Read it before writing a
probe.** What stays here is BartleBot's: its snapshots and its art. Measuring a layout bug
before theorising is in core's `bartleby/qt/CLAUDE.md`; `tools/probe_layout.py` is its worked
example.

**The newest dump lives in `bartlebot/game/data/`, the older ones in `tools/`.** Art
extraction reads the newest at run time (`art_extraction.type_dump()`), so it is app data
and ships in the install directory; `tools/` does not ship. When a new dump is captured, it
goes in `bartlebot/game/data/` and the one it replaces moves back to `tools/` (`git mv`, so
history follows it), which keeps one 26 MB dump in each install rather than one per patch.
Every tool that defaults to a dump takes the newest across both folders
(`art_extraction.newest_type_dump`).

## Snapshots of `Root.wad`, and the art

The snapshots, all in `bartlebot/game/data/`, and their tools:

| snapshot | tool | regenerate when |
|---|---|---|
| `data/world_names.json` | `dump_world_names.py` | a world is added or renamed in any locale |
| `data/zone_names.json` | `dump_zone_names.py` | a zone is added or renamed in any locale |
| `data/badge_categories.json` | `dump_badge_categories.py` | a badge category is added, reordered or renamed; the bot library's folders follow its order |
| `data/entity_names.json` | `dump_entity_names.py` | an NPC, mob or world object is added or renamed |
| `data/reagent_nodes.json` | `dump_reagent_nodes.py` | a reagent node is added or renamed; `entity_names.json` cannot hold them, having no display name to join on |

Three more are bartleby-core's, in `bartleby/runtime/game/data/`, with their tools in core's
`tools/`: `name_tables.json` (`dump_name_tables.py`), `dialog_captions.json`
(`dump_dialog_captions.py`) and `realm_names.json` (`dump_realm_names.py`).

Two more are core's but written by tools here, since they read BartleBot's type dumps. Each
writes the core checkout beside this one, `../bartleby-core`, never the copy in `.venv`:

| snapshot | tool | regenerate when |
|---|---|---|
| `spell_names.json` | `dump_spell_names.py` | a spell is added or renamed |
| `spell_schools.json` | `dump_spell_schools.py` | a spell is added, or its school changes |

`entity_names.json` is the one snapshot taken to *avoid* a library call rather than to
replace a probe. WizWalker's `ClientObject.display_name()` answers the same question, but it
resolves the langcode by opening `Root.wad` per lookup, which is the run-time WAD read the
rule above forbids. The langcode is also readable straight out of memory — but the object
name is just as good a key and is already in hand, so the snapshot joins on that instead and
costs no extra read. Its selection rule is worth knowing: `GameObjectTemplate` and its
subclasses *minus* the `ItemTemplate` branch. That branch is 76k of the 105k templates, it
is inventory rather than anything `get_base_entity_list()` can return, and including it
quadruples the file for rows no surface can show.

`WorldNames.lang` is worth knowing about on its own, because its key **is** the world
segment `zone_name()` returns — `WizardCity`, `DragonSpire`, `MooShu` — and its value is
the localized text the spiral door lists. That correspondence is what lets a World Gate step
verify arrival with the glob `"<key>/*"` rather than a per-world table of spiral chamber
zone names.

What it does *not* tell you is which worlds the door actually offers. `Darkmoor` (a world,
on the door) and `G14_DM` (Castle Darkmoor, a dungeon entered from Nightside) both have
WorldNames entries and both have GameData wads; nothing separates them. So
`worlds.SPIRAL_DOOR_WORLDS` is curated by hand, every exclusion is named with its reason,
and `test_worlds.py` checks each entry against the snapshot. Guessing a rule here got
Darkmoor wrong once already.

### Art is extracted per machine, and is never a snapshot

Everything above ends as a checked-in file. **Art cannot**: the repo goes public with
the first beta and a committed game texture is the same exposure a committed wizard name is. So
the extractor writes to `%APPDATA%/Bartleby/BartleBot/art` instead — the user's own
machine, which the repo cannot reach even by accident — and the app treats its absence as
the ordinary case rather than as a missing dependency.

**The extractor is in the app** (#232): `bartlebot/game/art_extraction.py`, run from
setup's Game art page and Settings › General › Game art. `tools/extract_card_art.py` is the
command line over the same `extract_art`, and still the quickest way to rerun it while
developing. It reads the game from the folder the app launches from (`find_install`, so a
Steam install too), and the type list from the newest `bartlebot/game/data/r<rev>_WizardDev.json`,
which ships in the install directory with the rest of `game/data/`. A run writes into `art.partial`
and swaps it in only once it finished, so a failed or cancelled run leaves the art before
it. So the tool's `--out` folder is **replaced whole**, not written into: whatever else is
in it is deleted with the old art. Point it only at a folder of its own. Its tests run on `tests/art_fixture.py`'s made-up install: real KIWAD archives (the
CRC is CRC-32 with no inversion) and spells serialized against a one-class type list, with
tiny PNGs drawn there, so nothing of the game's is committed.

It reads each spell's `m_imageName` and pulls that texture from `GUI/SummonedImages`
(nearly all in `_Shared-WorldData.wad`, a dozen in `Root.wad`), converting DXT3/DXT5 to
PNG because Qt's build here has no `dds` plugin. One PNG per *image*, not per template:
18,007 templates share 1,308 images. `katsuba` and `Pillow` are runtime dependencies for
exactly this, and nothing else at run time reads a WAD or decodes a texture.
Core's `bartleby/runtime/game/card_art.py` is the read side, for BartleBuddy too, which draws
what this extracts; the keys only BartleBot draws are `bartlebot/game/bot_art.py`'s.

The same run also writes 163 UI icons into `art/icons/`, and they are keyed by a
**logical name** — `archmastery`, `universal`, `recent`, `school_fire`, `world_krokotopia`,
`badge_4dungeons`, `tag_team_up`, `drop_treasure_cards`, `pin_shield`, `duel_slot_1` — rather
than by the texture they came from, so neither the extractor's caller nor the GUI ever
spells a KingsIsle filename. `ICON_SOURCES` is the one place the two meet. What they
resolve to, and why each was picked over its neighbors:

| key | texture | why this one |
|---|---|---|
| `archmastery` | `GUI/Icons/Icon_Archmastery.dds` | the rainbow spiral meaning *archmastery*, carrying no school |
| `universal` | `GUI/Icons/Type_Global.dds` | the game's own "any school" mark, as stamped on card art |
| `recent` | `GUI/Buttons/Art_Selfie_Timer.dds` | the camera self-timer's pocket watch, gold-ringed like the badges beside it; the bot library's Recent row |
| `school_<name>` | `GUI/Schools/Art_School_<School>.dds` | the ringed medallion the in-game archmastery picker uses |
| `world_<key>` | `GUI/Buttons/Button_World_<Stem>.dds` | the floating-island emblem the spiral door and the badges page both draw |
| `dungeon` | `GUI/BadgeCategory/Badge_Category_Dungeons.dds` | the bare rock-golem badge, for a nav "world" that is a dungeon group |
| `badge_<key>` | whatever `BadgeFilterDescriptions.xml` names | the icon the badge page draws beside each of its 78 categories; the bot library's folder list |
| `tag_<tag>` | `TAG_TEXTURES`, or built by `TAG_COMPOSITES` | a gold-ringed medallion per Rewards and Activities tag, hand-picked so all twenty match; the Type tags have none |
| `drop_<category>` | `DROP_TEXTURES`, nearly all `GUI/QuestPage/Art_Quest_*` | the quest page's reward medallion for each drop category; see below |
| `pin_<kind>` | `GUI/Buttons/Button_Socketing_Pin_Square_{Pip,Shield,Sword}.dds` | the bank and quicksell filter buttons for Power, Shield and Sword pins |
| `duel_slot_<n>` | `GUI/Art/Art_Target_{Sun,Eye,Star,Moon}.dds` | the gold diamond the game marks each duel position with; see below |

**The drop icons are the game's own pairings, not picks.** Each `Art_Quest_` medallion was
matched to a category by what the game draws it for: the Rate My Stitch outfit slots use
`Hat`, `Robe`, `Shoes` and `Weapon`, every `COLLECT_HOUSE_*` housing item uses `Housing`,
the battleground reward tables use `Seed` and `Treasure_Card`. Three easy "tidy-ups" are
wrong. `Weapon` is the wand, and `Art_Quest_Wand` is the same art at 64px. `Housing` and
`Furniture` are pixel-identical, but `Deed` (the floating house) is a different thing. And
the quest page has no spellement, so Spellements draws `Button_BG_Reward_Spellement02`,
the reward-category icon the Spellements tag also uses.

**The duel slot gems are keyed by position, not by symbol.** A duel puts four wizards
around a circle and turns run clockwise, which puts a team's own players **right to left**:
slot 1 is the rightmost. The game's "Set Sigil Slot Preference" dialog lists them that way
and marks each with a gold diamond — Sun, Eye, Star, Moon, first position to last. The key
is the number (`duel_slot_1`) because a combat order is a list of positions, and
`DUEL_SLOT_TEXTURES` owns the pairing, exactly as `WORLD_ICON_SOURCES` owns world → emblem.

That indirection is load-bearing rather than tidy. **Sun, Star and Moon are also three real
schools**, with their own `school_` medallions that look nothing like these; a caller
reaching for `school_sun` to draw slot 1 gets the archmastery picker's ringed medallion, and
it looks plausible. `test_duel_slot_icons.py` is the guard, and it also checks the run-time
symbol names in `card_art.DUEL_SLOT_SYMBOLS` against the texture table — those names are read
for tooltips on every machine without art, which can decline it or fail to extract it, so they cannot be
derived from a texture that is not there. `Art_Target_Sun` is the one of the four in
`Root.wad`; a probe listing only `_Shared-WorldData.wad` concludes there are three gems.

**A pin's icon comes from its name, not its category.** The game files pins under three
kinds, one filter button each, and `wiki_drops.PIN_KINDS` maps the type word in a pin's name
to its kind: Accurate and Conversion are Power; Blocking, Resist and Mending are Shield;
Punishing, Disabling and Crushing are Sword. The buttons are 2x2 sheets of 64px states, so
they are cropped and come out at 64px. A pin with no recognized type word gets `drop_pins`,
which `DROP_TEXTURES` points at the Jewels medallion. The fallback is the table's to state,
as the dungeon stand-in is for worlds.

**The tag medallions are chosen, not found.** Nothing in the game pairs a medallion with
our tag vocabulary, so `TAG_TEXTURES` is a hand-picked table of full paths, and each pick
was made by eye from the gold-ring family (`GUI/QuestPage/Art_Quest_*`,
`GUI/QuestButtons/`, `GUI/NpcPortraits/` hold most of it). Two tags have no single texture
and are built. **Challenge Mode** is the Dungeon sigil with `Icon_Advanced_Mode` stamped
where the game's own Challenge Mode button puts it. **Gauntlet** is the Housing Gauntlet
badge on a green disc inside `Art_Character_Cover`, a bare gold ring from `Root.wad`.
The constants beside `gauntlet_disc` are the tuned recipe. Two of them are easy to "tidy"
wrongly: the band between the disc and the gold is **solid**, because a fade to black
reads as a second, dark-green ring; and the disc is an orange gradient **recolored** to
the sigil's hue, because a gradient chosen directly in green read pale. The green groups
the four activities entered through a four-player sigil.

**The badge icons are the one set not written out by hand.** The game's own
`BadgeFilterDescriptions.xml` pairs every category with its icon, and several pairings
cannot be guessed — Five B.O.X.E.S. draws `Badge_Category_Maestro_Events`, Special Event
draws `Badge_Category_Deckathalon` — so `dump_badge_categories.py` snapshots the paths and
the extractor reads them by full path, not basename. The badge keys are also not the nav
map's (`Dragonspyre` against `DragonSpire`), which is why they are a `badge_` namespace
and not a second route to `world_`.

**Two badge textures are not state sheets.** Monstrology (`Button_Category_Monster_Magic`)
and Unfiltered (the help button) are single 128px images; every other badge is a 256px
sheet. `SINGLE_IMAGE_BADGES` names them, and cropping either keeps a quarter of the picture.

**The world emblems are 2×2 state sheets, not icons.** A 256×256 `Button_World_` texture
holds four 128px quadrants — normal, two hover/pressed variants, and a desaturated one for
a world the player cannot reach — and so does every `Badge_Category_`. Only the top-left
is wanted, which is what `top_left_quadrant` takes and `STATE_SHEET_ICONS` decides. Saved
whole they render at quarter size with three ghosts around them. The grayed quadrant is a
free "unavailable" affordance if a surface ever wants one; nothing draws it today.

Three traps in the world table, and all three are why `WORLD_TEXTURES` is written out by
hand rather than normalized from the world key:

- **The game spells one world three ways.** `DragonSpire` is the nav key, `Dragonspire`
  the world button, `Dragonspyre` the guild museum door. A case-folding rule is right for
  twenty worlds and silently wrong here.
- **Wysteria's texture is named for its school**, `Button_World_Pigswick_Academy`. Nothing
  derives that.
- **A world can have art in two families that disagree.** `Mirage`, `Karamelle` and
  `Catacombs` appear in both `Buttons/` and `BadgeCategory/` as pixel-identical copies —
  but `Empyrea` does not, so a "resolve by basename, first wad wins" rule would pick a
  different Empyrea depending on iteration order. World emblems come from `Buttons/` only;
  `BadgeCategory/` supplies nothing but the dungeon badge.

`tests/game/test_world_icons.py` is the guard, and the assertion that matters is that every
world in `zone_nav`'s map has an entry — a world added to the map with no emblem loses its
icon silently, behind a header that still reads correctly.

Two traps in that table. `Art_School_` is split across archives — seven schools in
`Root.wad`, and Moon, Star, Sun and Shadow in `_Shared-WorldData.wad` — so a probe that
lists only one wad concludes it covers seven schools and stops looking; it covers all
eleven. And `Type_Global` exists at 32×32 **and nowhere larger**, its `.tga` twin being
smaller still, so 32px is a hard ceiling on how big any surface may draw it rather than
a default to raise later.

Searching for these is its own trap: a name-matching probe keyed on the school names
cannot find a texture called *Universal* or *Global*, and one that caps its per-match
output silently hides whatever sorts last. Both happened here.

Follow the same split for any new asset work: a *name* or an *id* can be snapshotted
into `bartlebot/game/data/`, a *texture, model or sound* goes to the machine-local cache.

### Walks the shipped bots ask for: `check_bot_routes.py`

`uv run --no-sync python tools/check_bot_routes.py [--jobs N] [bot ...]` plans every walk the
shipped bots make, composed with their subgraphs, on the zone maps core ships, and writes a
report to `logs/check_bot_routes/`. A walk is a pair of points: a node's Reset XYZ and its
other points, and the last point of one node and the first of the next along an edge, while
nothing between changed the zone or moved the wizard (`KEEPS_PLACE` lists the nodes that
don't; a port, a zone change or any other node loses track). It lists the pairs with no route,
those whose only way crosses a gate (with the `Crossing` a Teleport to XYZ past it would need),
those over `LONG_WALK` (60 s) at a wizard's own speed, and the gates crossed that have a way
around. A Teleport to XYZ's own start and end are a hop, never planned as a walk (`HOPS`). Run it
after a map release or a change to a shipped bot's points. The first run in a fresh cache builds
the walking copies it needs. It sees nothing of the walk from a zone's arrival point to its first
node, since where the game puts a wizard on arrival isn't in the bot.

**"A gate is the only way" is where to look, not where to hop.** It tries one gate closed at a
time, with every other gate open, and knows nothing of what opens one. Stage 7.2 of #322 found it
wrong both ways:

- **Most of the gates it lists are open by the time the walk reaches them.** A fight won or a
  console used just before opens them (Celestial Pyramid's blockers, Galleries' webs), or a
  farming wizard's quests removed them long ago. The client's entity list says which are there.
- **A zone whose gates all start closed needs more hops than it lists.** Hidden Temple Gauntlet's
  doors stay shut, since the bot skips the rooms that open them. The report named three doors, and
  the way with every door closed crosses eight. Plan such a zone with every gate closed
  (`ZonePlanner.close` on each), and find the fewest doors that join the two points.
- **A Crossing longer than `HOP_SPAN` can't be hopped**, and is often a deep solid rather than a
  long doorway: each Galleries web is a mesh at least 500 deep by its hull. The bot opens such a
  gate the game's way instead (Galleries fights in order).

### Old bots' Teleport to Coordinates: `migrate_teleport_nodes.py`

`uv run --no-sync python tools/migrate_teleport_nodes.py [--check] [path ...]` rewrites every
Teleport to Coordinates node as a Walk to XYZ, which takes the same Zone and XYZ, so only the node
type changes, by text replacement: nothing else in the file moves. With no paths it rewrites the
shipped `bots/` and `templates/`, a released version's sha256 in its `template.json` with it; given
paths, it rewrites a tester's own bots, templates or subgraphs sent in. The app doesn't migrate
them itself (the user's call, 2026-11-26): a file naming the old node refuses to load, naming its
replacements. Each rewritten file must load and save byte-identical, or the tool exits 1. A walk
that used to teleport past a closed gate needs a Teleport to XYZ placed by hand (Stage 7 of #322).

### How far a pickup's prompt reaches: `probe_pickup_reach.py`

`uv run --no-sync python tools/probe_pickup_reach.py --entity "<Entity ID>"` reads, while you walk
toward and away from a reagent, how far from it the pickup prompt still shows. It only reads.
The flag lags the walk both ways, so the summary brackets the edge from where the prompt came on
and went off. `node_helpers.PICKUP_REACH` (275) sits a little inside it, from FrostedFlax_01's
285 to 300: a pickup stands off by that, less a walk's arrival margin, beside an entity named in
Keep Away From, and skips a spawn inside an exit with no floor outside within it. Run it with no
other prompt in range, since the game's prompt flag does not say whose prompt shows.

### How wisps move, and how near a wizard takes one: `probe_wisps.py`

`uv run --no-sync python tools/probe_wisps.py --seconds 120` records the wizard's position and
ratios and every wisp's position, every 0.1 s (it only reads), and `--analyze <csv>` summarizes a
saved run. Each wisp is still, loops, heads one way, follows a path or wanders, with its speed and
lap time. A pickup is timed by the ratio's rise, never by the wisp leaving the list: a taken wisp
flies to the wizard and lingers there 1.3-1.8 s first, so the leaving puts the pickup at 45 units
where the rise puts it at about 400. Stand still for how wisps move, and stand on a moving wisp's
way below full for the pickup. Its findings are in core's `docs/WIZARD101.md`, *How wisps move,
and how near a wizard takes one*.

### Where a walk the game allows leaves the planner's floor: `probe_walk_track.py`

`uv run --no-sync python tools/probe_walk_track.py` records a walk you make by hand (zone and
position, every 0.2 s; it only reads), and `--analyze <csv>` lays the track over the grid that
zone's planner builds: which piece each stretch was on, and where the wizard stood on a spot the
grid refuses, why (no floor at that height, a wall and the solid that makes it, the clearance
from a wall or drop, a step too high), with a map in `logs/walk_route/track/`. Close BartleBot
first, since two programs hooking one client clash. It found what splits Dragonspyre's Arena: a
box tilted into a ramp and a leaning slab, which the grid reads as wall (core's
`docs/WALKING.md`, *What is not done*, says why they stay that way).

### Zone connectivity: `tools/zone_graph/`

Which zones connect, where gates and arrival points sit, and the hub/spiral-door zone
per world all ship in the game files (`zonemap.nav`, `WorldHubZones.xml`, each zone's own
wad). Its `CLAUDE.md` has the layouts, and the open questions. Prefer it over
wizsprinter's hand-built `traversalData/`.
