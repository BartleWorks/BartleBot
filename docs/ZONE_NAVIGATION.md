# Zone navigation

How BartleBot gets every client from wherever they are to a named zone, without the bot
author recording each gate along the way. The map lives in `bartlebot/data/zone_nav.json`;
`bartlebot/zone_nav.py` loads it and plans routes over it, and
`bartlebot/nodes/zone_navigation.py` runs a route. No node uses it yet; the tester does.

The research behind the zone list — which zones count as main zones, and how they were
picked from the game files — is in `tools/zone_graph/`.

## The shape of the problem

Each world is a hub with main zones hanging off it. A route between two of them is a chain
of zone changes, and those come in five kinds:

| kind | what the player does | runs with |
|---|---|---|
| `walk` | touches a transition volume | `enter_zone` |
| `object` | interacts with a teleporter or door | `enter_zone_via_object` |
| `ride` | boards a boat, airship or dragon, presses X to skip the ride | `enter_zone_via_ride` |
| `list` | picks the destination from a zone-list object | `enter_zone_via_list` |
| `hub` | clicks the compass hub button | `teleport_to_world_hub` |

Only `walk`, `object` and `ride` are stored as links. `list` links are generated from list
networks, and `hub` steps are implied.

### The hub button is one step from anywhere

The compass hub button works in every zone of a world, so the planner treats every zone as
one step from its world's hub. That is what makes a route from a boss room, or any other
zone the map does not declare, possible at all: its only step is the button.

The button sends a side world's players to the side world's own hub — Castle Darkmoor,
Zigazag, Kembaalung Village and Aquila each have one — even though they are entered from
another world. Wintertusk is the exception: it shares Grizzleheim's hub, Northguard. A
world's `hub` may therefore name a zone another world declares.

### Teleporters are objects

A hub teleporter is an `object` link from the hub to its zone; the teleporter in that zone
is another `object` link back. Azteca's Three Points and Khrysalis' Silent Market have their
own sets, and they are object links too. Teleporters often serve one player at a time, and
how long each needs varies — up to 8–10 s in Azteca, much less elsewhere — so an object link
carries `sequential` and `object_use_delay`, the same params `enter_zone_via_object` takes.

### Lists are networks, not pairs

A zone-list object stands in the world's hub and in every zone on its list, and any of them
can send you to any other. Storing that as pairs would be n² links for a handful of object
positions, so the file stores one network — each member zone and where its object is — and
the loader generates a `list` link for every ordered pair. List objects serve players in
parallel.

A list entry's text is the destination zone's display name. That is assumed rather than
confirmed for every world; it means the entry can be matched in all seven locales from the
game's own `WizardZone.lang`, the way `go_to_world` searches the spiral door, once a zone-name
snapshot exists.

The assumption is not always right: Empyrea's hub list shows Nimbus Hanger's row as just
"Nimbus", not a locale variant of its real name at all. A member's `list_aliases` covers
that — extra text the list shows for that member, added on top of its locale names rather
than replacing them. It belongs on the member, not `zone_names.json`, because it is a quirk
of this one list, not the zone's actual display name in any language.

### Rides

These are the only rides in the game, and they differ only in how they start:

| ride | boarded by | cutscene zone |
|---|---|---|
| the Oasis ↔ Krokosphinx boats | interacting | none: the boat moves through the zone it started in |
| Digmoore Station ↔ Hyde Park, Chelsea Court airships | walking into a gate | one per direction (`MB_FlightToCourt`, `MB_FlightFromCourt`) |
| Dragonspyre Academy → Dragon's Roost dragon | interacting | `DS_A3_FlightPath` |

The Ironworks airship is a ride too, but it is behind a dungeon entrance, so it is not on the map.
The dragon has no ride back: Dragon's Roost to the Academy is an ordinary teleporter.

A ride link's `board` says how it starts, `walk` or `object` (the default), and it takes that
kind's coordinates. `ride_zone` names the cutscene zone when there is one; a ride boarded through
a gate always has one, since a gate is a zone change.

All of them end the same way: press X on the skip prompt, and the player goes straight to the
destination. The prompt shows anywhere on the ride, so nothing is reset for it, and each client
skips for itself. `enter_zone_via_ride` teleports only while boarding: once a client is on the
ride it is being carried, and a teleport would pull it off. A boat loads nothing when it is
boarded, so a client counts as aboard once, within the Safety Delay of pressing X, it is somewhere
other than the interact point without having been teleported: the boat is moving it. The next
prompt it sees is the skip. A client whose skip never lands still arrives when the ride ends by itself.

A client can be left in a cutscene zone — a run stopped on the dragon. The planner gives every
`ride_zone` a step that finishes its ride, which is why a cutscene zone may belong to only one
ride and may not also be a declared zone.

### A gate opened by objects

The Oasis to Tomb of Storms is a plain walk through a gate that is not there until three objects
have been used, in any order and without dialogue. The gate appears 20 seconds after the last one,
stays 20 seconds, and is shared: one player opening it opens it for everyone. So it is a walk link
with an `unlock` — the objects and `opens_after` — and `step_call` runs it as `interact` at each
object with player 1 alone, `sleep` for `opens_after`, then the walk for the whole group. Using an
object again does not close an open gate, so it is always opened fresh rather than tried first.
The walk has 20 seconds to fire, which Safety Delay's three attempts fit inside unless it is set
above about six seconds.

The objects are added to the link by hand: no zone change happens at them for the tester to record.

### Doors and side worlds

`go_to_world` leaves players at a world's spiral door. In most worlds the door is in the
hub. Wizard City, Krokotopia, Marleybone and MooShu are the exceptions, with one or two zone
changes between the door and the hub, and those are ordinary links — the planner weighs
them against the hub button like any other route. (`go_to_world` stops Wizard City at
Ravenwood rather than the World Tree.)

A side world has no door. It names the world it is entered from in `entered_from`, and its
entry is an ordinary link whose `from` is a zone of that world: Kembaalung Village from the
Oasis, Zigazag from Krokotopia's Balance School, Castle Darkmoor from Nightside, Aquila from
Cyclops Lane. `NavMap.entry_world()` follows `entered_from` to the world `go_to_world` should
be asked for.

## Route cost

Routes are costed in estimated seconds, not hops, because the question a user cares about is
which way is faster. A loading screen is the unit (`LOAD_SECONDS`); a list costs a little
more for opening and paging it, an object a little more for the prompt. A ride costs a load plus
boarding and the skip prompt, and a second load when it has a cutscene zone.
The hub button costs a load plus whatever is left of its cooldown — the caller passes that
in, since it can read whether the button is enabled — and a second use in the same route
pays the whole cooldown.

That is enough for "use the list or the teleporter when there is one" to fall out without a
rule: one load beats three streets. A sequential teleporter adds `object_use_delay` for every
client after the first, so with a full group of four, Azteca's teleporters can lose to a
short walk. On an exact tie a walk is cheapest by construction, and a walk has the fewest
ways to fail.

`plan()` and `navigate()` take `avoid`, a set of kinds the route must not use. Avoiding
`object` and `list` gives the route on foot, which is how a street route gets tested when a
teleporter always wins, and how to skip a teleporter whose cooldown makes walking faster;
cooldowns between uses are not modelled yet. Avoiding `hub` does the same for the hub button.
Getting to the world (the spiral door) and regrouping (the hub button) are not affected.

The numbers are estimates and are meant to be tuned; they live in `zone_nav.py`, not
`nodes/timing.py`, because nothing waits on them.

## The data file

```json
{
  "worlds": {
    "WizardCity": {
      "hub": "WizardCity/WC_Hub",
      "door": "WizardCity/WC_Ravenwood_Teleporter",
      "zones": { "WizardCity/WC_Hub": {}, "WizardCity/WC_Shop_Area": {} },
      "links": [
        {"kind": "walk", "from": "WizardCity/WC_Hub", "to": "WizardCity/WC_Shop_Area",
         "xyz": {"x": 1384.81, "y": -3644.26, "z": -27.99},
         "reset_xyz": {"x": 1349.43, "y": -3125.51, "z": -27.99},
         "verified": true, "source": "bot", "note": "prince_gobblestone.json"}
      ],
      "list_networks": []
    }
  }
}
```

| field | on | meaning |
|---|---|---|
| `hub` | world | the zone the hub button lands in; required |
| `door` | world | the zone `go_to_world` lands in, if the world has a door |
| `entered_from` | world | for a side world, the world whose zones its entry links start in |
| `zones` | world | every zone a route may start, pass through or end in. `{"destination": false}` keeps a connector (a dojo, a school) out of the picker |
| `label` | zone | the name to show when the game's is ambiguous: `EM_Z02_Sewers` is "Zanadu" in game, "Zanadu Sewers" here |
| `arrivals` | zone | `{from zone: xyz}`: where a player lands coming from that zone. A walk from here to there with no `reset_xyz` uses it |
| `kind`, `from`, `to` | link | one direction of one zone change |
| `board` | ride | `object` (the default) or `walk`: how the ride is boarded, which decides its coordinates |
| `xyz`, `reset_xyz` | walk, ride boarded on foot | as `enter_zone`; `reset_xyz` optional |
| `unlock` | walk | `{"objects": [{interact_xyz, reset_xyz}, …], "opens_after": seconds}`: a gate those objects open |
| `interact_xyz`, `reset_xyz` | object, ride boarded by interacting | as `enter_zone_via_object` |
| `sequential`, `object_use_delay` | object | as `enter_zone_via_object` |
| `ride_zone` | ride | the cutscene zone the ride passes through; left out for the boats, required on foot |
| `also_arrives_in` | link | other zones that count as arriving — for zones the quest line swaps, like Samsara Village |
| `verified` | link, network | the link has worked in a real client; defaults to **false** |
| `source`, `note` | link, network | where the data came from: `bot`, `code`, `game_data`, `recorded` |
| `volume` | walk | the name of the `volumes.xml` volume nearest the recorded trigger, for reference; routing ignores it |

A list network is `{"zones": {zone: {"interact_xyz", "reset_xyz", "list_aliases"}}, "verified",
"source"}`. A member may be `{}`: it is on the list, but its own object is not recorded yet, so
the list routes into it and not out of it — `list_aliases` still applies if given, since it
describes how the member is *shown*, not how to reach it. That is how the tester builds a
network — a list use adds the origin's object and the destination as a member — and networks
found to share a zone are merged, since they are one list.

The file has one layout, `zone_nav.dumps()`: two-space indent, small objects on one line. The
tester writes it that way, and a test fails if a hand edit leaves it in any other.

### Arrival points are resets

The game places an arriving player clear of the gate they came through, so the spot you land
in the Shopping District coming from Colossus Boulevard is a safe reset for the walk back to
Colossus. Each zone's `gamedata.bin` lists these spots (`Target location (…)`), but not which
gate each belongs to. The tester supplies the pairing: it matches where you landed to the
nearest listed spot and stores it under `arrivals`.

Every zone is declared by exactly one world, and a link may only name declared zones — a
typo is a load error, not a route that silently never exists. Restricted zones are refused
outright, so no route can pass through one. `parse()` reports every problem at once;
`load()` logs and returns an empty map if the shipped file is unreadable, and
`tests/test_zone_nav.py` parses the shipped file strictly so that never reaches a user.

### Verified, and where unverified data comes from

`verified` defaults to false, and the planner leaves unverified links out unless asked. A link
is verified once it has worked in a real client: copied from a working bot, or recorded by the
tester, which only records what someone just did in the game. Tentative links are the other
kind — coordinates guessed from the game files (a gate's volume as `xyz`, an arrival point as
`reset_xyz`) that no client has used yet. The tester's Test tab runs them and marks the ones
that worked.

## Running a route

`zone_navigation.navigate(machine, clients, target)` goes to the target's entry world through
the spiral door when a client is out of reach, regroups a split group with the hub button,
then plans, runs the first step through its zone-entry node, and plans again from wherever
the clients landed. It uses the door at most once, and a route that still has no way on is a
failure naming where it stopped. A list step matches the destination's name in every locale
from `bartlebot/data/zone_names.json` (`tools/dump_zone_names.py`).

## Mapping it: the tester

`uv run python -m tools.zone_nav_tester` hooks one client and shows a tree of the current world
rooted at its hub, coloured by whether each zone has a verified route, a tentative one, or
none. Its Map tab records zone changes as you play, detects the kind, fills in coordinates,
matches a walk to its volume and the landing to an arrival point, and saves the result into
`zone_nav.json` as a verified link — without replacing a verified link that already exists
unless told to. Its Test tab previews or runs a route, lists the hops
still to record when there is none, and marks the tentative links a successful run used as
verified. `tools/zone_nav_tester/app.py` has the details; the detection rules are in
`recorder.py`.

It records the dragon and the airships as rides: a transition into an unmapped zone, by interacting
or through a gate, is held until it is clear whether the player leaves that zone again without
walking. The boats cannot be recorded: the hub button is disabled for the whole ride, so the zone
change at the skip reads as a hub button use and is dropped. Their two links were entered by hand.

## Not built yet

- the navigation node, a wrapper around `navigate()`
- links for every world but Wizard City: the zones are declared, the links are what the tester
  is for
- Wintertusk, which is not declared at all

## Known gap: a teleporter shortcut can be locked

Some `object` links are not doors or teleporters but a boat, dock, or similar one-off
interaction to a zone that also has an ordinary teleporter once unlocked -- in the data
they're indistinguishable, both stored as plain `object` links. A handful of Zafaria's hub
teleporters (to Stone Town, Waterfront, Drum Jungle, Elephant Graveyard) are the known case:
each is also reachable by the zone's own first-visit method, and the hub teleporter is
usually unlocked automatically on that first visit, but not guaranteed to be for every
account/quest state. The planner has no notion of "unlocked for this account" and always
takes the cheapest `object` link, so a client that has not yet unlocked a given teleporter
could be routed onto a link it cannot actually use. Considered low priority: teleporters are
unlocked on first entry in the overwhelming majority of cases, and `avoid={OBJECT}` (the
Test tab's "Walk only") is the workaround when it does come up.
