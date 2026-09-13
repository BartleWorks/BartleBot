# Zone graph: how the game connects its zones

Tools for reading zone connectivity and positions straight out of the game files. They
exist because wizsprinter's `traversalData/` files (`zoneMap.txt`, `gates_list.txt`,
`displayZones.txt`, `objectLocations.txt`) turned out to be hand-built: typos, placeholder
coordinates, player-standing positions rather than gate positions, and 14 of Wizard City's
432 zones. The game ships the same information, complete.

Run from the repo root; the scripts import `wad_common.py` from their own folder.

| script | does |
|---|---|
| `zonemap_nav.py` | decodes `Root.wad/zonemap.nav`: `stats`, `find`, `neighbours`, `export` |
| `zone_data.py` | one zone's wad: `list`, `summary` (arrival points, tagged objects), `dump` to JSON, `strings`, `hubs`; `names` for every zone's English name |
| `main_zones.py` | proposes each world's main zones for hand review (folder filter, hops from hub, interiors on the way) |
| `compare_traversal_data.py` | scores a world's Deimos files against the game data |
| `wad_common.py` | paths, the two katsuba serializer configs, JSON shaping, Deimos file parsing |

Write anything these produce to `*.local.json` or the scratchpad. Nothing here is a
snapshot yet; if one is added, it follows the `tools/CLAUDE.md` rule: a checked-in JSON
file, never a WAD read at runtime.

## Where each piece lives

| need | source |
|---|---|
| which zones connect | `Root.wad/zonemap.nav`: 3,377 zones and 10,255 links |
| a world's hub and spiral-door arrival zone | `Root.wad/WorldHubZones.xml` (`m_hubZone`, `m_universeTPZone`) |
| gate and object positions | the zone's `gamedata.bin`, `m_objectList` (`m_zoneTag`, `m_location`) |
| where you arrive from another zone | the zone's `gamedata.bin`, `m_locationList`: `Target location (WC_Hub WC_Golem_Tower Exit)` |
| a zone's display name | `m_zoneDisplayName` -> `Locale/<loc>/WizardZone.lang`; 3,228 of 3,377 zones resolve. Not unique: side rooms carry their street's name, 417 names belong to several zones |
| map-marker names | the zone's `Compass.xml` -> `WizardCompassLocs.lang`; tunnel labels in `WizardPOI.lang` |
| world-qualified names | `ZoneLocName.lang`, e.g. `Wizard City\|Cyclops Lane` |

A zone's wad is its path with `/` replaced by `-`: `WizardCity/WC_Streets/WC_Unicorn` ->
`WizardCity-WC_Streets-WC_Unicorn.wad`.

`Root.wad` ships seven locales (de, el, en-US, es, fr, it, pl). The tools expose English
only, but `zone_display_names` takes the locale as a parameter: the bot will need to show
zone names in the user's language, so never key anything on the English string. Key on
the zone path.

`gamedata.bin` has no `BINd` prefix and only deserializes with flags `0`; the `BINd` files
need `STATEFUL_FLAGS`. `Deserializer` in `wad_common.py` picks by prefix.

## Open questions

- **Link direction in `zonemap.nav`.** Pairs are stored as `(u16, u16)` and every link
  checked so far appears in both orders, so the tools treat the graph as undirected. A
  one-way link (a dungeon exit, a drop-down) would settle which end is the source.
- **The 12 leading bytes of each node record** are zero in every record of the current
  file. `zonemap_nav.py stats` reports it if a patch changes that.
- **Nearest-object matching is weak outside Wizard City.** Commons gates sit 27–340 units
  from Deimos' recorded coordinates, but Krokotopia's crossings are 400–3300 units from
  the nearest tagged object. Their gates are likely untagged objects or trigger volumes,
  so `compare_traversal_data.py`'s distance column only means something where the gate
  has a tag.
- **Gate -> destination.** An object tag names its destination only by convention
  (`WC_GateCommons_ToGolemCourt`, `WC-GateStreet_CyclopsLane`, `WC_Triton_TeleporterA_ToMain`),
  which is not reliable enough to join on. The actual teleport lives in `triggers.xml`,
  which katsuba cannot read with `r800294_WizardDev.json` ("failed to identify type with
  tag"). A newer type dump may fix that; until then `zone_data.py strings` is the only view.
  Others have read both files. In their dump of `KT_Selenopolis/Interiors/KT_Z04_Pyramid_Adv`,
  a trigger maps a name to its event (`Teleport_2_Sele_Lower -> Enter_TeleportVol_Teleport_2_Sele_Lower`),
  and `volumes.xml` places the volume that raises it (`TeleportVol_Teleport_2_Sele_Lower` at
  `(-29, -3033, -5)`). So for zone transitions the two files overlap. What `volumes.xml`
  adds is the trigger position, which untagged gates otherwise lack. The destination is in
  the trigger's action list, which that dump did not show.
- **Two Deimos crossings the graph lacks**, Commons -> Colossus and Cyclops -> Colossus, both
  at round-number coordinates. Probably not real gates, but unconfirmed in a live client.
