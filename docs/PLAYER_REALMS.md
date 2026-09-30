# Player realms

Players on different realms enter separate battles and separate dungeons, and a friends-list or
group port moves the porter onto the target's realm. Nothing could tell whether two clients shared
a realm without opening a spellbook, so every arrival check compared zones, and one zone is not one
realm. This is how the app knows now, what a bot says it wants, and where each node acts on it.
Epic #136: #130 (knowing), #132 (wanting), #131 and #133 (acting).

## Knowing: sightings

A client loads only the players in its own zone, realm and instance, and only nearby
(`tools/probe_same_place.py` measured it). So:

- **Seen** -- one client holds another's `global_id_full()` in its `get_base_entity_list()` --
  proves the two share a zone, realm and instance. One direction is enough: the probe's two
  directions disagreed only in the unload band, thousands of units out.
- **Not seen, both ways, within `SIGHT_RANGE`** (3000; ~3800 measured), with nobody loading,
  proves they do not. Different zones are apart at any distance.
- **Anything else is Unknown**: too far apart, someone loading, a read that failed. A loading
  client is Unknown even when seen, since a leaver stays in the others' lists for about a second
  into its loading screen.

A check costs ~5 ms a client. `bartlebot/sightings.py` holds the rules, pure;
`node_helpers.read_sightings(clients, machine)` takes the readings and never raises.

Sharing a zone, realm and instance is transitive, so a check yields **groups**: if A sees B and B
sees C, all three share a realm even out of each other's sight. `sightings.choose_leader` picks from
the largest group, ties to player order.

### The record

`BotMachine` keeps the groups it has seen (`note_sightings`, `are_together`, `together_with`,
`forget_sightings`), keyed by global id, since a relog hands back a new client object for the same
character. A group stays confirmed until a member loads, which is the only way a realm can change.
`ClientHealthMonitor`'s loading watcher forgets a client on every poll that shows it loading or
unreadable -- **ahead of its flagged check**, because relog flags its clients and a relog is exactly
when a realm can change. Outside `run()` nothing would say when a sighting went stale, so the record
keeps only the latest check. A fresh Apart overrides the record.

The record cannot answer across a transition: everyone who went through the door loaded. But a door
keeps each player's realm, so the zone-entry nodes call `note_realm_peers` just before theirs, and
straggler recovery reads that (a context variable, like `straggler_recovery`). The hub button and a
recall to a mark are assumed to keep realms too; neither is verified.

## Wanting: the Player Realms option

**A node param says where the players go; this option says how they are arranged.** No node param
decides the arrangement, so the two cannot contradict each other. Two values
(`bartlebot/player_realms.py`):

- **Together** (the default): every player on one realm and in one area.
- **Separate**: never move a player onto another player's realm. An existing split is left as it
  is. The reagent collectors and Scrap Iron ship Separate.

Every node that moves players declares the one shared `player_realms.SPEC`, so it shows once in
Options. The author sets the default; the user may overrule it.

**A node that merges players by nature** declares `NodeSpec.merges_players` -- `teleport_to_friend`
is the one -- and a bot holding one cannot be Separate. `separate_blocked_by` is the rule, and three
places enforce it, as with restricted zones:

1. the Setup and Options rows grey out Separate, naming the nodes;
2. the canvas marks the node an error while the bot's file declares Separate;
3. `AppController._check_player_realms` refuses the run, which is what a hand-edited file or an
   override from before the node was added reaches.

## Acting

| where | Together | Separate |
|---|---|---|
| `port_clients` (a hooked target) | a quiet client is presumed there only once seen with it | -- |
| `gather_at_player`, `teleport_to_friend`, `port_map._port_batch` | arrival includes being seen | the same: these merge on purpose |
| straggler recovery | any arrived player | only one that shared the straggler's realm before the transition |
| `enter_dungeon` | on the sigil before X, and at the entrance after | nothing |
| `handle_combat` | together before anyone engages; one realm and one duel circle once in battle; a split fight is finished, then repaired | a fight centre is shared only within a realm |
| `change_realm` | favorite: all to it; next: the leader, then the rest to its realm; checked after | favorite: P1 to it, the rest the rows after; next: player N, N rows along; a collision is logged |
| `sync_in_zone` | leader travels, hops, everyone it does not see ports to it, confirmed by sight | everyone travels; nobody ports or hops |

**Repair** is `node_helpers.ensure_together`: the record first, then one check; everyone the leader
does not see -- Apart and Unknown alike -- ports to it through `gather_at_player`, which confirms by
sight. The node fails if that port fails. It is a no-op for a Separate bot.

Names and ids are never logged. Players are named by number.
