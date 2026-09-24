# Zone navigation

How BartleBot gets every client from wherever they are to a named zone, without the bot
author recording each gate along the way. The map lives in `bartlebot/data/zone_nav.json`;
`bartlebot/zone_nav.py` loads it and plans routes over it, and
`bartlebot/nodes/zone_navigation.py` runs a route. The Go To Zone node and the tester both run it.

The research behind the zone list — which zones count as main zones, and how they were
picked from the game files — is in `tools/zone_graph/`.

## The shape of the problem

Each world is a hub with main zones hanging off it. A route between two of them is a chain
of zone changes, and those come in seven kinds:

| kind | what the player does | runs with |
|---|---|---|
| `walk` | touches a transition volume | `enter_zone` |
| `object` | interacts with a teleporter or door | `enter_zone_via_object` |
| `ride` | boards a boat, airship or dragon, presses X to skip the ride | `enter_zone_via_ride` |
| `list` | picks the destination from a zone-list object | `enter_zone_via_list` |
| `hub` | clicks the compass hub button | `teleport_to_world_hub` |
| `gate` | picks another world at a World Gate | `enter_zone_via_list` |
| `home` | presses HOME, landing in the dorm | `wizard_city.teleport_home_and_verify_dorm` |

Only `walk`, `object` and `ride` are stored as links. `list` links are generated from list
networks and `gate` links from each world's gate; `hub` and `home` steps are implied.

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

`enter_zone_via_list` is not a node a bot author places (#36). A list object only ever stands
in a zone the map declares — a network naming an undeclared zone is a load error, and the
tester refuses to record one it cannot place in a network — so there is nowhere it could be
pointed that Go To Zone cannot already plan a route to. It survives as the step runner behind a
planned `list` or `gate` step, and its caller hands it a row it resolved itself:
`zone_lists.resolve` for a zone list, `worlds.display_names` for a gate, whose rows are worlds
rather than zones. That is also why no bot file holds a row's text.

A list entry's text is the destination zone's display name. That is assumed rather than
confirmed for every world; it means the entry can be matched in all seven locales from the
game's own `WizardZone.lang`, the way `go_to_world` searches the spiral door, once a zone-name
snapshot exists.

The assumption is not always right: Empyrea's hub list shows Nimbus Hanger's row as just
"Nimbus", not a locale variant of its real name at all. A member's `list_aliases` covers
that — extra text the list shows for that member, added on top of its locale names rather
than replacing them. It belongs on the member, not `zone_names.json`, because it is a quirk
of this one list, not the zone's actual display name in any language.

Sometimes the zone's own display name is worse than merely absent from the list: it is
*another* zone's real row. Empyrea's hub list has two separate rows that both lead to a
zone called "Chaos Jungle" in the game's own data (`EM_Z12_LandingZone` and
`EM_Z14_ChaosJungle`), but only one of them, the actual Chaos Jungle, is labelled that in
the list — the other's row reads "Mandalla". Adding "Mandalla" as an ordinary `list_aliases`
entry is not enough: the search tries every candidate against every row on the page, so the
locale names for "Chaos Jungle" would still match the *other* zone's row before "Mandalla"
is ever tried, and the wrong list entry gets clicked. `list_aliases_only: true` on the
member drops the zone's own display name from the candidate set entirely, leaving only
`list_aliases` — required and non-empty when this is set.

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

### World Gates, HOME and side worlds

A world's `door` is the zone its World Gate stands in, which is where a gate drops a player
arriving from another world. In most worlds that is the hub. Wizard City, Krokotopia, Marleybone
and MooShu are the exceptions, with one or two zone changes between the door and the hub, and
those are ordinary links.

A world's recorded `gate` generates a `gate` link from its door to every other world's door.
Every gate lists every world the character has unlocked, so a destination needs a door but no
gate of its own. A gate step runs through `enter_zone_via_list`, matching the world's name in
every locale from `world_names.json`, and anywhere in the destination world counts as arriving.
A character who has not unlocked the world fails that step, as `go_to_world` would.

HOME works from any zone at all, a castle or a zone off the map included, so a `home` step to
the dorm (the file's top-level `home`) is implied from every zone but the dorm, the way `hub` is
from every zone of a world. The dorm walks out to Ravenwood, Ravenwood to the World Tree, and the
World Tree has Wizard City's gate: the route `go_to_world` hard-codes is one the planner can pick.
A world whose own gate is recorded beats it — the hub button and the gate are two transitions,
HOME to another world's door is four — and a world whose gate is not recorded yet still routes,
the long way round.

A side world has no door and is on no gate's list. It names the world it is entered from in
`entered_from`, and its entry is an ordinary link whose `from` is a zone of that world: Kembaalung
Village from the Oasis, Zigazag from Krokotopia's Balance School, Castle Darkmoor from Nightside,
Aquila from Cyclops Lane.

## Route cost

A route is costed in transitions: loading screens. That cost is the same on every machine,
where an estimate in seconds is not, and it scales with the group where the game makes it
scale. Every step is one transition, with two exceptions:

- a ride through a cutscene zone is two
- a teleporter that takes players one at a time (`sequential`) is one per client

So with four clients, Azteca's teleporters lose to a three-street walk, while a single client
still takes them. "Use the list or the teleporter when there is one" falls out without a rule:
one transition beats three streets.

`zone_nav.Cost` compares transitions first. Waiting and then effort only break ties.

- **Waiting** is time with no loading screen:
  - whatever is left of the cooldown the hub and HOME buttons share, read from the timer under
    the hub button (`TELEPORT_TIMER_PATH`, shown only while the buttons are disabled; a disabled
    button with no readable timer counts as a full cooldown)
  - the whole cooldown for a second use of either button in the same route
  - a gate's `opens_after`
  - the delay between players at a sequential teleporter
- **Effort** ranks the kinds by how much can go wrong (`EFFORT`): walk, then hub, then object,
  then list, ride and gate, then HOME, which has castles to unequip and a dungeon's exit
  warning to answer.

So a walk wins an exact tie, and a cooling hub button loses to a teleporter it would otherwise
beat, but never to a route with more transitions.

`plan()` and `navigate()` take `avoid`, a set of kinds the route must not use. Avoiding
`object` and `list` gives the route on foot, which is how a street route gets tested when a
teleporter always wins, and how to skip a teleporter whose cooldown makes walking faster;
cooldowns between uses are not modelled yet. Avoiding `hub` does the same for the hub button,
and avoiding `home` keeps a route to the gates it can reach without pressing HOME. Regrouping
(the hub button, or HOME for a group split across worlds) is not affected.

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
| `home` | file | the zone HOME lands in, the dorm: every other zone has a `home` step to it |
| `hub` | world | the zone the hub button lands in; required |
| `door` | world | the zone its World Gate stands in and drops arriving players in, if the world has one |
| `gate` | world | the World Gate in `door`: `{interact_xyz, reset_xyz, verified, source, note}`. Only a world with a `door` may have one; it generates a `gate` link to every other world's door |
| `entered_from` | world | for a side world, the world whose zones its entry links start in |
| `zones` | world | every zone a route may start, pass through or end in. `{"destination": false}` keeps a connector (a dojo, a school) out of the picker |
| `label` | zone | the name to show when the game's is ambiguous: `EM_Z02_Sewers` is "Zanadu" in game, "Zanadu Sewers" here |
| `arrivals` | zone | `{from zone: xyz}`: where a player lands coming from that zone. A walk from here to there with no `reset_xyz` uses it |
| `kind`, `from`, `to` | link | one direction of one zone change |
| `board` | ride | `object` (the default) or `walk`: how the ride is boarded, which decides its coordinates |
| `xyz`, `reset_xyz` | walk, ride boarded on foot | as `enter_zone`; `reset_xyz` optional: without it the zone's `arrivals` entry for the reverse walk, else each player's position when the step starts |
| `unlock` | walk | `{"objects": [{interact_xyz, reset_xyz}, …], "opens_after": seconds}`: a gate those objects open |
| `interact_xyz`, `reset_xyz` | object, ride boarded by interacting | as `enter_zone_via_object` |
| `sequential`, `object_use_delay` | object | as `enter_zone_via_object` |
| `ride_zone` | ride | the cutscene zone the ride passes through; left out for the boats, required on foot |
| `also_arrives_in` | link | other zones that count as arriving — for zones the quest line swaps, like Samsara Village |
| `verified` | link, network | the link has worked in a real client; defaults to **false** |
| `source`, `note` | link, network | where the data came from: `bot`, `code`, `game_data`, `recorded` |
| `volume` | walk | the name of the `volumes.xml` volume nearest the recorded trigger, for reference; routing ignores it |

A list network is `{"zones": {zone: {"interact_xyz", "reset_xyz", "list_aliases",
"list_aliases_only"}}, "verified", "source"}`. A member may be `{}`: it is on the list, but
its own object is not recorded yet, so
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

`zone_navigation.navigate(machine, clients, target)` plans from where the clients are, runs the
first step through its zone-entry node, and plans again from wherever they landed. Another
world is part of the plan, not a separate trip first. A route with no way on is a failure naming
where it stopped. A list step matches the destination's name in every locale from
`bartlebot/data/zone_names.json` (`tools/dump_zone_names.py`), and a gate step matches the
world's name the same way from `world_names.json`.

The Go To Zone node (`nodes/teleportation/go_to_zone.py`) is `navigate()` with the zone as its one
param, chosen in a picker grouped by world (`gui/widgets/zone_picker.py`). Only the zone path is
stored: the map already says which world it is in. The `go_to_world` node is unchanged: bots that
use it still go home and through the World Tree.

### Running a route with several clients

A port is one loading screen for any number of clients, from anywhere. A group taking a transition
together is not: the game loads its players in one at a time. So a group does not always travel
together.

| the clients start | and the route | they |
|---|---|---|
| in one zone | has at most `TRAVEL_TOGETHER_MAX_TRANSITIONS` (2) transitions and no object step | travel together |
| in one zone | is longer, or takes a teleporter | follow one client: it travels, the rest port to it |
| in several zones | — | follow one client |

The client that travels is the one the most others can port to, then the one with the cheapest
route, then the lowest player number; a client already in the target zone has the cheapest route of
all. Nobody else moves until it arrives, so no two clients ever use an object at once. Clients that
cannot port to one another travel together instead, regrouping first if they are split: at the hub
within one world, at home across worlds.

**Who can port to whom** is `bartlebot/port_map.py`, surveyed in pre-run setup: every client's
online friends list, then the Group tab if the friends lists leave a pair unconnected. A port goes
through the friends list where there is one, else through the group (the member's roster line, then
Go To Player). Ports run in waves, so a client with no route to the traveller still gets there
through someone who already has. A route that fails is dropped for the rest of the run, and a client
that cannot port at all travels there itself once the others have arrived.

**A client that misses a step** while travelling together gets one more go at it alone; for a walk
through a gate that has been opened, only the walk is retried. A client that misses it again stays
exactly where it is -- nothing teleports it or runs a step on it -- while the rest carry on, and it
ports to them at the end. The step nodes' own teleport-to-player recovery is turned off for the
steps navigation runs (`node_helpers.without_straggler_recovery()`), since it would port a straggler
into a zone the others are about to leave. A step nobody makes is planned around once
(`plan(avoid_links=...)`): that is usually a teleporter the account has not unlocked.

**An object that serves one player at a time** but is not marked `sequential` is caught by
`enter_zone_via_object` itself: when some clients go through and the rest are still standing there,
the rest take turns, and a warning names the link so it can be marked. The flag still matters,
because the planner only costs a teleporter per client when it is set.

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

A list object is recorded without being used. A list belongs to one zone, and everything a
network member needs is in that zone: where the object is, and a spot clear of its prompt. So
opening a list is enough. Where the player stood when it opened is `interact_xyz`, and the last
spot at least 150 units from the object with no prompt showing is the candidate `reset_xyz`.
The tester holds that on its own card, which a zone change never replaces, and the member joins
the network already holding its zone, else its world's only network, else a new one. A world with
several networks and the zone in none of them is refused, since position cannot say which list
the object belongs to.

That candidate is a sample taken while walking, and an object with a wide interact radius, like
Empyrea's lists, can still show its prompt there once a client teleports straight to it. So the
card's **Verify both points live** does what `enter_zone_via_list` does at runtime, from inside
the same zone: teleport to `interact_xyz` and wait for the prompt, then to the reset point and
wait for it to go, retreating further from the object until it does.

A World Gate opens the same list window as a zone list, so the tester tells them apart by the
window's title, "Spiral Map" in English and in every locale from `world_names.json`. A World Gate
is recorded and verified on the same card but saved as its world's `gate`, not into a list
network: its rows are worlds rather than zones, and every gate lists every world the character has
unlocked, so a gate needs no destinations of its own. The gate must stand in the world's `door`
zone, since that is where a gate drops an arriving player; anywhere else is refused as a sign the
`door` is wrong.

It records the dragon and the airships as rides: a transition into an unmapped zone, by interacting
or through a gate, is held until it is clear whether the player leaves that zone again without
walking. The boats cannot be recorded: the hub button is disabled for the whole ride, so the zone
change at the skip reads as a hub button use and is dropped. Their two links were entered by hand.

## Not built yet

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
