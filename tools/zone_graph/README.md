# zone_graph

Tools for reading zone connections, gate positions and arrival points straight out of the
Wizard101 game files. `CLAUDE.md` in this folder covers where each piece of data lives,
the file layouts, and the open questions.

Run everything from the repo root. Every script accepts `--game-data <dir>` before the
command if the game isn't installed at
`C:\ProgramData\KingsIsle Entertainment\Wizard101\Data\GameData`.

Sample output below is trimmed.

## zonemap_nav.py

Decodes `Root.wad/zonemap.nav`, the game's graph of which zones connect to which.

**`stats`**: how many zones and links there are, and a check that the file layout still
matches what the script expects.

```bash
uv run python tools/zone_graph/zonemap_nav.py stats
```
```
zones: 3377 (3215 with at least one link)
edge pairs: 10255
node records with non-zero unknown bytes: 0
name-table count: 3377 (matches node count: True)
zones per top-level folder:
    432  WizardCity
    217  Khrysalis
    ...
```

**`find <regex>`**: look up a zone's exact path. The match ignores case.

```bash
uv run python tools/zone_graph/zonemap_nav.py find "WC_Streets/WC_"
```
```
WizardCity/WC_Streets/WC_Catacombs
WizardCity/WC_Streets/WC_Colossus
WizardCity/WC_Streets/WC_Cyclops
...
```

**`neighbours <zone>`**: every zone linked to this one. Links are treated as two-way.

```bash
uv run python tools/zone_graph/zonemap_nav.py neighbours WizardCity/WC_Hub
```
```
WizardCity/Interiors/WC_Library
WizardCity/WC_Golem_Tower
WizardCity/WC_Ravenwood
WizardCity/WC_Shop_Area
WizardCity/WC_Streets/Interiors/WC_PET_Park
WizardCity/WC_Streets/WC_Unicorn
...
```

**`export --out <file>`**: write the whole graph as JSON: the zone names, plus the links as
pairs of positions in that list.

```bash
uv run python tools/zone_graph/zonemap_nav.py export --out zonemap.local.json
```

## zone_data.py

Reads one zone's own wad. Every command except `hubs` takes a zone path like the ones
`zonemap_nav.py find` prints.

**`list <zone>`**: the files inside that zone's wad, with sizes in bytes.

```bash
uv run python tools/zone_graph/zone_data.py list WizardCity/WC_Ravenwood
```
```
      1597  Compass.xml
     41462  gamedata.bin
     16568  triggers.xml
     11931  volumes.xml
     ...
```

**`summary <zone> [--tag regex]`**: the zone's display name, its arrival points, and its
named objects. `--tag` filters the objects, which is how you find the gates.

```bash
uv run python tools/zone_graph/zone_data.py summary WizardCity/WC_Hub --tag "Gate|Teleport"
```
```
WizardCity/WC_Hub  display=WizardZone_TheCommons -> The Commons

arrival points:
  (   -5927.1,     2465.6,    225.6)  Target location (WC_Hub WC_Golem_Tower Exit)
  (     -86.6,     1414.7,    -30.5)  Target location(WC_Hub Ravenwood)
  ...

objects:
  (   -6410.1,     2685.4,    249.9)  WC_GateCommons_ToGolemCourt
  (    6315.1,     5671.2,    -32.7)  WC_GateCommons_ToUnicornWay
  ...
```

**`dump <zone> <entry> [--out file]`**: convert one file from the wad to JSON. This works
for `gamedata.bin`, `Compass.xml` and `spawnData.xml`. Without `--out` it prints to the
terminal.

```bash
uv run python tools/zone_graph/zone_data.py dump WizardCity/WC_Hub Compass.xml
uv run python tools/zone_graph/zone_data.py dump WizardCity/WC_Hub gamedata.bin --out hub.local.json
```
```
{
 "m_markers": [
  {
   "m_textKey": "WizardCompassLocs_00000016",
   "m_location": [-6332.56982421875, 2632.990966796875, 279.3970031738281],
   ...
```

**`volumes <zone> [--grep regex]`**: the name and position of every volume in the zone's
`volumes.xml`. Gate volumes are usually named for where they lead, but not always.

```bash
uv run python tools/zone_graph/zone_data.py volumes WizardCity/WC_Streets/WC_OldeTown --grep Teleport
```
```
  (   -6981.1,     -512.7,  -1400.0)  Conditional Teleporter to Triton Lane
  (    3122.3,      282.5,     30.0)  TeleportVol_Teleport (Street 2 WC_Shop_Area Exit)
  (   -5391.0,     3126.6,   -600.0)  Conditional Teleporter to Cyclops Lane
  ...
```

**`strings <zone> <entry> [--grep regex]`**: the readable text in a file katsuba can't
parse, such as `triggers.xml` or `volumes.xml`.

```bash
uv run python tools/zone_graph/zone_data.py strings WizardCity/WC_Hub triggers.xml --grep Teleport
```
```
rTeleport location (WC_Hub WC_Headmistress_House Entrance)
0TeleportToPetParkTrigger`
.Trigger-TeleportToTower`
4TeleportToShoppingDistrict`
...
```

The stray characters at the start and end of each line are bytes of the binary format
around the text.

**`hubs`**: each world's hub zone and the zone the spiral door drops you in. It takes no
zone argument.

```bash
uv run python tools/zone_graph/zone_data.py hubs
```
```
WizardCity       hub=WizardCity/WC_Hub                        spiral door=WizardCity/WC_Ravenwood_Teleporter
Krokotopia       hub=Krokotopia/KT_Hub                        spiral door=Krokotopia/KT_WorldTeleporter
Marleybone       hub=Marleybone/MB_Hub                        spiral door=Marleybone/Interiors/MB_WolfminsterAbbey
...
```

**`names [regex]`**: every zone in `zonemap.nav` with its English display name. The regex
matches either the path or the name, ignoring case. It reads every zone's wad, so it takes
about 20 seconds. `?` means the zone has no wad, no `gamedata.bin`, or no English name.

```bash
uv run python tools/zone_graph/zone_data.py names "Colossus Boulevard|Caliburn"
```
```
Avalon/AV_Z00_Hub                                             Caliburn
Avalon/Interiors/AV_Z00_FairyCircle_Cave                      Caliburn
WizardCity/WC_Streets/Interiors/WC_Colossus_HideoutEarl       Colossus Boulevard
WizardCity/WC_Streets/WC_Colossus                             Colossus Boulevard
```

Side rooms usually share their street's name, so one name can match several zones.

## main_zones.py

Proposes each world's main zones: the hubs and streets you walk between, as opposed to
the side rooms, boss rooms and gauntlets off them. It's a starting point for choosing
the list by hand, not the list itself. The docstring explains the filter.

A candidate is a zone of the world with no `Interiors/`, `Gauntlets/`, `TreasureTower/`
(and similar) folder in its path, that isn't restricted, and that links to another zone
of the same world. The columns:

| column | meaning |
|---|---|
| `hops` | links from the world's hub, walking through any non-restricted zone of the world |
| `links` | links to other zones of the same world |
| `rooms` | how many of those are side rooms |
| `d` | `*` if Deimos' hand-built `zoneMap.txt` lists the zone |
| `via` | interiors the route from the hub must pass through, e.g. a dojo or an airship flight; used only when no route through streets exists |

After each world's table come the zones Deimos lists that the filter left out, with
the reason.

```bash
uv run python tools/zone_graph/main_zones.py --world Krokotopia
uv run python tools/zone_graph/main_zones.py --out main_zones.local.json
```

`--world` is a world's top-level folder and can be repeated; without it every world
in `WorldHubZones.xml` is proposed. Housing, events, raids and restricted worlds are
skipped.

## compare_traversal_data.py

Checks one world's block of wizsprinter's hand-built `traversalData/` files against the
game data. It has no subcommands.

For that world it reports:

- `zoneMap.txt` links missing from `zonemap.nav`
- whether the `ZONEDOOR` entry matches `WorldHubZones.xml`
- `gates_list.txt` crossings missing from `zonemap.nav`
- for each crossing, the distance from the recorded coordinate to the nearest named object
  in the source zone

`--world` defaults to `WizardCity`. It must match a `WORLD - <name>` header in the Deimos
files.

```bash
uv run python tools/zone_graph/compare_traversal_data.py
```
```
== zoneMap.txt: 14 links, 0 not in zonemap.nav

== ZONEDOOR: Deimos ['WizardCity/WC_Ravenwood_Teleporter'], WorldHubZones.xml 'WizardCity/WC_Ravenwood_Teleporter'

== gates_list.txt: 28 crossings, 2 not in zonemap.nav
   absent: WizardCity/WC_Hub -> WizardCity/WC_Streets/WC_Colossus  at (2240.0, -2000.0, 25.498626708984375)
   absent: WizardCity/WC_Streets/WC_Cyclops -> WizardCity/WC_Streets/WC_Colossus  at (2776.0, -1948.0, 25.498626708984375)

   recorded coordinate vs nearest tagged object in the source zone:
                       WC_Hub -> WC_Golem_Tower               standard           d=   114.4  WC_GateCommons_ToGolemCourt
                       WC_Hub -> WC_PET_Park                  standard           d=    26.7  WC_GateCommons_ToPetPark
                  WC_OldeTown -> WC_Cyclops                   standard           d=   178.9  WC-GateStreet_CyclopsLane instance
   ...
```

```bash
uv run python tools/zone_graph/compare_traversal_data.py --world Krokotopia
```
```
== zoneMap.txt: 28 links, 0 not in zonemap.nav

== ZONEDOOR: Deimos ['Krokotopia/KT_WorldTeleporter'], WorldHubZones.xml 'Krokotopia/KT_WorldTeleporter'

== gates_list.txt: 56 crossings, 0 not in zonemap.nav

   recorded coordinate vs nearest tagged object in the source zone:
              KT_EntranceHall -> KT_Retreat                   dungeon            d=  2711.2  KT_Statuette instance (8)
                       KT_Hub -> KT_Shop01                    standard           d=   416.2  KT-SHOP-PET-RECIPES instance
   ...
```

The distance column only means something where the gate is a named object. That holds for
the Commons, but in Krokotopia the nearest named object is usually an NPC or a prop, not
the gate.

To point it at a different wizsprinter checkout:

```bash
uv run python tools/zone_graph/compare_traversal_data.py --traversal-data D:/src/wizsprinter/wizwalker/extensions/wizsprinter/traversalData
```
