# Reading the game's own data

Two sources answer most "what is this offset / where does this value come from"
questions without probing a live client. **Check both before writing a probe** —
several days of `probe_*.py` work has been spent rediscovering things that were
sitting in one of these.

## Type dumps: `r<rev>_WizardDev.json`

A wiztype dump of every game class with the exact byte offset of each property —
about 7,000 classes and 49,000 properties. Query it with `tools/typedump.py`:

```bash
uv run python tools/typedump.py at ClientObject 448     # what lives at this offset?
uv run python tools/typedump.py show DeckListControl    # full layout, incl. inherited
uv run python tools/typedump.py prop "characterId"      # find a property everywhere
uv run python tools/typedump.py classes "SpellList"     # find classes by regex
uv run python tools/typedump.py gaps DeckListControl    # holes where raw members hide
uv run python tools/typedump.py annotate ../wizwalker/wizwalker/memory/memory_objects/client_object.py
uv run python tools/typedump.py diff <old.json> <new.json>
```

Two things to know before trusting it:

- **It covers reflected PropertyClass properties only.** Raw C++ members — child
  pointers, manager handles, the entry vectors `deck_io.py` reads — never appear.
  `gaps` is the workaround: it brackets those between named neighbors, which is
  how `deck_io`'s `0x300` entry vector shows up as a 40-byte hole between
  `m_treasureFrame` and `m_cardSize`.
- **A class name is not the runtime behavior template name**, and the difference
  is not derivable: `ClientWizPlayerNameBehavior` → `"WizPlayerNameBehavior"`,
  `ClientTreasureBookBehavior` → `"BasicTreasureBookBehavior"`,
  `ClientInventoryBehavior` → `"WizardInventoryBehavior"`. The authoritative list
  is `await client_object.list_behavior_names()` on a live client.

**A dump is captured with wiztype**, from one running client:
`uvx --from wiztype==0.1.13 python tools/game_update/capture_type_dump.py`. katsuba only
reads one. The tool finds the type registry itself when a patch breaks wiztype's own scan,
as r811339 did; `bartleby-core/docs/GAME_UPDATES.md` has when to run it and where the file goes.

**Dumps are committed, and old ones are never deleted.** A dump can only be
captured from a running client, so once a patch ships there is no way back to the
previous layout. The checked-in dumps are the only record of what the offsets were
when our code last worked; `typedump.py diff` against a fresh dump then says
exactly what moved. `.gitattributes` disables textual diffing for them — use the
tool, not `git diff`.

**The newest dump lives in `bartlebot/game/data/`, the older ones in `tools/`.** Art
extraction reads the newest at run time (`art_extraction.type_dump()`), so it is app data
and ships in the install directory; `tools/` does not ship. When a new dump is captured, it
goes in `bartlebot/game/data/` and the one it replaces moves back to `tools/` (`git mv`, so
history follows it), which keeps one 26 MB dump in each install rather than one per patch.
Every tool that defaults to a dump takes the newest across both folders
(`art_extraction.newest_type_dump`).

## Game data: `Root.wad`

WizWalker can read the game's own archives, which hold the lookup tables the
client resolves IDs against. Many fields in memory are keys, not text —
`m_badgeTitle` reads `'Badges_00006184'`, and a wizard's name is three indices,
never characters — so a memory scan for display text will find nothing while the
answer sits in a WAD.

```python
from wizwalker.file_readers.wad import Wad

wad = Wad(r"C:\ProgramData\KingsIsle Entertainment\Wizard101\Data\GameData\Root.wad")
names = await wad.names()                       # ~172,000 entries
data = await wad.get_file("CharacterNames.xml") # bytes
```

Conventions worth knowing:

- `<Thing>.xml` holds the **ordered key list**; `Locale/<loc>/<Thing>.lang` maps
  key → display text. IDs index **position in the XML list**, not the numeric
  suffix of the key (`First_Boy_102` is "Luke" but sits at index 100).
- `.lang` files are UTF-16LE with CRLF, in fixed three-line records: `key`,
  translator description, display text. The description reads as blank in most
  records, which makes the format look like `key / blank / value` — a parser that
  *requires* it blank silently drops every documented key rather than failing. Slice
  at a fixed stride; see `parse_lang` in `tools/dump_dialog_captions.py`.
- Locales are `de el en-US es fr it pl`. Non-English tables can be shorter —
  later additions were not always translated.

Don't read a WAD at runtime: a 172k-entry archive read per lookup, plus a hard
dependency on the game being installed at a guessable path. Extract what you need
into a checked-in snapshot instead, as `tools/dump_name_tables.py` does for
`bartlebot/game/data/name_tables.json`, and regenerate it when the game adds entries.

The snapshots, all in `bartlebot/game/data/`, and their tools:

| snapshot | tool | regenerate when |
|---|---|---|
| `data/name_tables.json` | `dump_name_tables.py` | the game adds name entries |
| `data/dialog_captions.json` | `dump_dialog_captions.py` | a modal is reworded, or a key is added to `KNOWN_KEYS` |
| `data/world_names.json` | `dump_world_names.py` | a world is added or renamed in any locale |
| `data/spell_names.json` | `dump_spell_names.py` | a spell is added or renamed |
| `data/realm_names.json` | `dump_realm_names.py` | a realm is added, retired or renamed; also carries the localized Yes/No the Remember Realm option shows |
| `data/zone_names.json` | `dump_zone_names.py` | a zone is added or renamed in any locale |
| `data/spell_schools.json` | `dump_spell_schools.py` | a spell is added, or its school changes |
| `data/badge_categories.json` | `dump_badge_categories.py` | a badge category is added, reordered or renamed; the bot library's folders follow its order |
| `data/entity_names.json` | `dump_entity_names.py` | an NPC, mob or world object is added or renamed |
| `data/reagent_nodes.json` | `dump_reagent_nodes.py` | a reagent node is added or renamed; `entity_names.json` cannot hold them, having no display name to join on |

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
`bartlebot/game/card_art.py` is the read side.

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

`tests/tools/test_world_icons.py` is the guard, and the assertion that matters is that every
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

### Zone connectivity: `tools/zone_graph/`

Which zones connect, where gates and arrival points sit, and the hub/spiral-door zone
per world all ship in the game files (`zonemap.nav`, `WorldHubZones.xml`, each zone's own
wad). Its `CLAUDE.md` has the layouts, and the open questions. Prefer it over
wizsprinter's hand-built `traversalData/`.

## Reading a field out of a BINd file: use `katsuba`, not a byte scan

Every game object under `Root.wad` (`Spells/`, `ObjectData/`, etc.) is a `BINd`-
prefixed binary blob — a serialized `PropertyClass`, not text. Pulling one named
field out of it without a real deserializer means guessing at a marker in the raw
bytes, and that guess can silently read the wrong field: `dump_spell_names.py`
originally found a spell's display-name langcode by searching for the first
literal `b"Spells_"` and reading the length byte before it, which assumes
`m_displayName` is always the first localized string in the blob. It isn't —
field order in the serialized bytes isn't fixed, so for some templates
`m_description`'s langcode came first and got misread as the name (1457 spells
got their combat-log template text — `"$eA$ $dT_image$$Damage_image$..."` — as
their "display name" before this was caught).

[`katsuba`](https://pypi.org/project/katsuba/) is a real BINd deserializer — the
same one the wizdb/Kimerith-WindHammer Discord-bot toolchain uses — and it reads
straight off the newest wiztype dump, the one in `bartlebot/game/data/` that `typedump.py`
reads too, so no separate type dump is needed:

```python
from katsuba.op import STATEFUL_FLAGS, Serializer, SerializerOptions, TypeList
from katsuba.wad import Archive

from bartlebot.game.art_extraction import type_dump

archive = Archive.mmap(str(wad_path))
types = TypeList.open(str(type_dump()))
opts = SerializerOptions()
opts.flags = STATEFUL_FLAGS
opts.shallow = False
opts.skip_unknown_types = True
ser = Serializer(opts, types)

data = archive["Spells/Tiered Spells/Novus Storm - T05 - A.xml"]
if data.startswith(b"BINd"):
    data = data[4:]
obj = ser.deserialize(data)
obj.get("m_displayName")  # -> b"Spells_00003940", by name, not position
```

`tools/dump_spell_names.py` is the worked example. It's a dev-only dependency
(`uv add --group dev katsuba`) — nothing at runtime reads a WAD, per the rule
above — so reach for it whenever a new dump tool needs a specific field off a
`BINd` object rather than an ordered `<Thing>.xml` / `.lang` pair; save the
marker-scanning approach for cases `katsuba` genuinely can't cover.

**A spell's WAD path stem is its template name, but a template name is not unique.**
41 of them exist twice — once under `Spells/CastleMagic/` and once as the real spell,
so `Banshee` is both `CastleMagic` and `Death`. Both files name the same art, so it is
one card filed twice rather than two cards. A tool keyed by stem therefore has a
collision rule whether it writes one down or not, and "last one the archive handed
over" is not a rule. `dump_spell_schools.py` keeps whichever copy claims a real school,
because a card in a combat deck is never the castle-magic copy.

## What neither source answers: window layout

**UI window names are not in the type dump and not in any WAD.** The dump covers
reflected `PropertyClass` properties, and the archives hold game data; the window
layout is compiled into the client binary. Searching every UI-ish XML across
`Root.wad`, `GUI-WorldData.wad` and `_Shared-WorldData.wad` for names the app
already relies on — `DeckConfiguration`, `CardsInDeck`, `ItemSpells` — returns
zero content hits, and `GUI/` in `Root.wad` is textures.

So the rule above inverts for anything shaped like "what is this control called":
go straight to a live client. Either the in-app **UI Tree Browser**, or a probe
that dumps the subtree and diffs it across a change the user makes by hand —
`tools/probe_archmastery_ui.py` is the worked example of the second.

# Our own widgets: measure before theorising

`tools/probe_layout.py` is the equivalent of the rule above for BartleBot's *own* GUI.
It builds a real `BotCanvas` offscreen, drives a scripted interaction, and prints what
Qt did: each widget's height at every step, the event stream, and each paint's damage
rect. No game client, so it is cheap to re-run.

```bash
uv run python tools/probe_layout.py mode            # Run/Edit switches
uv run python tools/probe_layout.py mode --tags 6   # a heavily tagged bot, whose tags wrap
uv run python tools/probe_layout.py paints --show   # paint damage; --show to really paint
```

`bartlebot/gui/debug/qt_layout_debug.py` is the reusable half — `LayoutTracer` and
`size_snapshot` — so the same instrumentation can be attached to the live app when a bug
only reproduces there. It is installed nowhere by default and hooks nothing.

**Widths measured offscreen are not the app's widths.** The offscreen platform resolves
`fusion`, and the app resolves `windows11`; fusion's controls are much wider at the same
font metrics — a "Gear" tag pill is 80px there and 59px in the app, a ~35% error that
compounds across a row. Heights are close enough to reason about, widths are not. So
**anything about wrapping, eliding or how much room a row needs must be measured with
`--show`**, which uses the real platform and therefore the real style. This is the same
trap `tests/CLAUDE.md` describes for CI, arriving through a different door: there the
runner has the wrong style, here the probe does.

**Reach for it before forming a theory about a layout or repaint bug.** #83's toolbar
flicker took four wrong guesses — deferred `deleteLater`, height-for-width propagation,
`setUpdatesEnabled`, a forced re-layout — each of which sounded right and measured as
having no effect. One run of this printed the answer: Bar B was given six different
heights per Run/Edit round trip, four of them heights no mode had asked for, and it ended
18px taller than its own size hint said it should be.

Two readings carry most of the diagnostic weight:

- **Geometry that disagrees with the widget's own `sizeHint` after the event loop has
  turned** means a parent laid the widget out from a hint that has since changed, and
  nothing is coming to correct it. Qt delivers `LayoutRequest` as a *posted* event, so a
  parent whose request is served before its child's measures the state the child is
  leaving. Fix it by removing the dependence — a height that never changes cannot be read
  at the wrong moment — not by forcing a re-measure afterwards, which in #83 landed on the
  right pixel for two cycles and then stopped.
- **A height the widget passed through that no resting state asked for** is a frame that
  was painted. That is what a flicker *is*, and counting them is the difference between a
  bar that is legitimately taller in Edit mode and one that overshoots on the way there.

One Qt trap the tool was built around, worth knowing before reading a trace:
**`QWidgetItem` answers `heightForWidth` from the widget's *layout*, never from the
widget's own override.** So a wrapping widget — anything holding a `FlowLayout` — cannot
floor its height by overriding `sizeHint` or `heightForWidth`; the parent reads straight
past both. `setMinimumHeight` is the floor a parent honors. `_TagsField` in
`bartlebot/gui/canvas/bot_header.py` sets both, and the comment there says why.
