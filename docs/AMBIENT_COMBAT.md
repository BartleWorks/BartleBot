# Ambient combat

An ambient fight is one no `handle_combat` node started: a mob pulls a player in while the bot is
doing something else. The ambient combat watcher (`BotMachine._ambient_combat_watcher`) takes it
over, brings in whoever can help, fights it, and leaves everyone somewhere a second fight is
unlikely to find them. Issue #116; fleeing is #111.

The session lives in `bartlebot/bot/ambient_combat.py` (`AmbientSession`). The machine's part is
detecting the fight, getting the running node out of the way, and acting on how the session ended.

## How the game behaves

These come from playing the game, not from the code. Nothing in memory says any of them, so do not
design around a guess: ask.

- **Battles start by proximity.** A mob pulls a player in when the player comes within a fixed
  radius. Mobs walk fixed paths per zone and do not chase. Client lag can make the pull happen from
  what looks like ~900 units away (#117).
- **The planning timer.** Each round gives 30s to pick a card, counted from when cards become
  selectable (`DuelPhase.planning`). It **restarts at 30s whenever a player joins**, so helpers
  have time to arrive and a full group can be in by round one.
- **Porting to a player in a fight works** through the friends list, unless the zone blocks
  teleports. Inside a dungeon it depends on the dungeon.
- **Realms are the hazard.** Teleporting to a duel circle's coordinates from another realm starts a
  *separate* fight. Clients in one zone on different realms are normal for reagent bots.
- **The ghost window.** After a fight, a player cannot be pulled into another for 30s if it stays
  completely still. From its first movement the protection lasts only about 4-5s, and rotating and
  teleporting both count as movement.
- **Standing where a fight ended invites another one** (the concern behind #117). Preventing that
  is most of what this document is about.
- **Fleeing** puts a player in its world's hub, as a teleport would, with **empty mana**. The hub
  always has mana wisps close to where fled players land. Flee is never disabled, but can only be
  clicked during planning.
- **A player defeated in a fight the group wins** comes back with 1 health and otherwise behaves as
  if it had won.
- **The world hub is always safe.**
- **Two idle rounds raise "You Are Idle"** (`WorldView/CombatAFKWindow`, lang keys
  `GUI:CombatAFKTitle` and `GUI:CombatAFKMessage`). A player that has not chosen a card or passed
  by the end of two planning phases in a row gets it, and its turn is passed for as long as it is
  up. Flee (the center button) flees with no confirmation; Return (the right one) puts the player
  back to choosing cards.
- **Logging out in battle** works through Esc → Quit, but not while "You Are Idle" is up, and
  not through the spellbook, which will not open in battle (the relog node's route). A player
  that logs back in beside a mob is pulled into a fight on arrival.

## Getting the node out of the way

The watcher polls every client's `in_battle()`. It skips while the FSM is in `handle_combat`, which
is already fighting, and re-checks that once it holds its lock, since the FSM can enter
`handle_combat` while the reads are out. Then it pauses the FSM and **cancels the running node**
(`_preempt_node`), and waits for the loop to park before it moves anyone.

- **Why cancel rather than pause.** Pausing stops the loop only at the next node boundary, which
  leaves the node free to walk a client the watcher is fighting with.
- **The cancelled state runs again from the start** when the watcher resumes: no result, no
  transition, no counters. `_preempted` is how the loop tells this cancel from a stop, and
  `resume()` clears it, because a node that finished just before the cancel landed never consumes
  it. Then the FSM has already moved on, and running the next state is correct.
- **Why wait for the park.** Once parked, nothing the node started is still running, and
  `machine.log()` files under the machine rather than the cancelled node, so the node helpers the
  session reuses log correctly without being told how.
- **Never cancelled:** `handle_combat` and `relog` (`machine.NEVER_PREEMPTED`). Relog is itself the
  recovery, and it waits for every player to be out of battle before logging anyone out, and again
  before re-forming the group, so the session fights alongside it rather than against it. For those, and for a node that does not park within
  `PREEMPT_PARK_TIMEOUT`, the session runs with `may_move=False`: it fights for whoever is in battle
  and moves nobody.

Nodes already had to cope with cancellation, since `stop()` cancels after its grace period.
`bartlebot/nodes/CLAUDE.md` has what that asks of a node, chiefly never swallowing
`CancelledError`.

## The session, in order

Players are named by number in every log line (P1, P2), never by name.

1. **Group the fighters and find helpers.** `read_sightings` for every client. A client not in
   battle is a **helper** when it is proven to share a fighter's realm: it has the fighter loaded
   now, or `machine.are_together` says so. `docs/PLAYER_REALMS.md` has how that is known.
2. **Port the rest (Phase B).** Every other client ports to the first fighter with `port_waves`,
   then joins like a helper, unless the bot is Separate (`player_realms.machine_is_separate`) or
   `machine.port_map` is None. Then they are left where they are. A refused port (a dungeon that
   blocks it, or no route) is logged and dropped; the fight goes ahead without that player.
3. **Record each helper's start before it moves:** its zone, its position, and `mob_clearance`
   there. After the fight everyone stands in the duel circle, which may be too far away to have
   those mobs loaded, so their clearance has to be read now.
4. **Join.** Each helper runs `combat_runner.join_fight` to its fight's center, retrying every
   `COMBAT_TP_RETRY_INTERVAL` and giving up at `ENTER_COMBAT_TIMEOUT`, or once the fight is over.
   A helper a port has already landed in the duel is not teleported.
   A center the game will not let a player stand on (an obstacle the fight cleared leaves its spot
   invalid) puts the helper straight back where it started; that marks the center blocked for the
   whole fight, and every later attempt teleports to a player already in it instead (#157).
5. **Hold the fighter's first cast** until every helper for its fight is in or has given up, or
   `AMBIENT_HELP_HOLD` (20s) has passed since the **anchor**: the later of the fighter's first
   `planning` phase and the most recent join, since each join restarts the game's timer. If the
   phase cannot be read, the anchor is the detection. With no helpers the fight starts at once.
6. **Fight everyone in battle.** Until no client is in battle and every combat task is done, any
   client seen in battle gets its combat run with **its own slot's** strategy
   (`resolve_combat(..., num_clients=len(clients))` gives one per slot): a helper, a client that
   walked into the fight, a bystander pulled into a second one. Drops are polled for every
   participant.
7. **Afterwards**, per fight: a won one as below, a fled one by collecting wisps in the hub where
   the flee left its players.

### After a won fight

**Nobody moves until the move.** Only reads happen before it; the first movement starts the 4-5s
window instead of the 30s one.

1. **Wisps.** Anyone under 50% health or mana (`WISP_BELOW`, `collect_wisps`' own thresholds) makes
   the fight's players collect as a group: to full, `WISP_COLLECT_TIMEOUT`, safe wisps only. A
   player defeated in a won fight is at 1 health, so it always does.
2. **Choose where each player goes.**
   - **A helper still in the zone it started in goes back to its own start.** The bot put it there
     and it stood there through the whole fight without being pulled in, which is the strongest
     evidence of a safe spot there is. A helper ported from another realm of this zone goes back
     to its start *on the fighter's realm*; in a Together bot that is where it belongs anyway.
   - **Everyone else** -- the fighter, and a helper ported in from another zone, whose start is
     coordinates in a zone it has left -- goes to **the nearest anchor in its zone**: a helper's
     start in this zone, or where any client that fought nobody stands now, **on any realm**, each
     at least `SAFE_RADIUS` (1500) clear of mobs. A teleport to coordinates keeps the player on its
     own realm, so another realm's spot is fair.
   - **No anchor: the world hub.**
3. **Check on arrival.** Within `SAFE_RADIUS` of a mob at the arrival point sends that player to
   the hub. It has to be quick, inside the 4-5s window.

A helper that never got into the fight goes through the same choice if it had moved.

**Getting to the hub** is `teleport_to_world_hub.run` with the hub of the player's current world
(`zone_nav.load().world_of(zone).hub`). It waits out the button's cooldown, up to 30s; clicking the
button is not movement, so the ghost window holds while it waits. From inside a dungeon it confirms
the exit warning, which abandons the dungeon; the bot goes to recovery anyway. A world missing from
the navigation map, or a failed teleport, goes straight to recovery: the relog takes the player out.

## How a session ends

- **Every participant and helper is back in the zone it started in:** resume, and the interrupted
  state runs again from the start. A fighter's starting zone is its zone at detection; bystanders
  never moved.
- **Otherwise** -- anyone went to the hub, fled, or came from another zone -- `force_state` to the
  bot's `recovery_state`, then resume.
- **An exception in the session** also goes to recovery. The resume is in a `finally`: without it,
  one unreadable strategy leaves the run paused for good.
- **A bot with no `recovery_state`** runs the interrupted state again, with a WARNING.
- **A fight that did not end after a flee was called for** goes to recovery without moving anyone:
  a player still in battle cannot be moved.

**Solo bots** have no anchor, so every ambient fight one wins ends in the hub and then recovery
(for most bots, a soft relog of a few seconds). That is accepted: it is rare, and cheap.

## Fleeing and the flee limit

A fight is fled by every one of our players in it, never by one alone (`combat_runner.FightWatch`):
when none of them has held a castable damage card and no enemy has carried damage over time for
several planning phases in a row, or once the fight runs past its time limit
(`AMBIENT_COMBAT_MAX_DURATION` here, `max_duration` in `handle_combat`, both 600s).

`AMBIENT_FLEE_LIMIT` (3) flees within `AMBIENT_FLEE_WINDOW` (600s) stops the bot with a reason the
user sees (`BotMachine.note_flee`), counting `handle_combat`'s flees and the watcher's alike.
Without it, fight, flee, recovery, fight never trips the same-state or no-match limits, because the
states keep changing.

## The idle popup

"You Are Idle" means nothing of ours fought two rounds of a fight. It was first seen after a
`handle_combat` timeout fell through to a relog with players still fighting, and after players
logged back in beside a mob and were pulled into a fight while the group survey held the social
window open over the cards (#203). Relog now waits fights out; the popup's watcher
(`BotMachine._combat_idle_watcher`) is the net for whatever else leaves a fight unanswered.

It polls every `COMBAT_AFK_POLL_INTERVAL`, while the bot runs and while the ambient watcher holds
it paused; a pause the user asked for, or one held for client recovery, is left alone. On a
popup it clicks Flee, and then:

- **During an ambient session**, it tells the session (`AmbientSession.note_fled`). The fight ends
  as a fled one: wisps in the hub, counted toward the flee limit, then recovery.
- **During a relog**, it does nothing more. The relog is the recovery already.
- **Otherwise**, it counts the flee and cancels the running node, **handle_combat included**, then
  sends the bot to its recovery state. handle_combat is the one the ambient watcher spares, but a
  fight it was running has just been fled, and left alone it would report that fight as won.

Flee rather than Return, because by the time the popup is up nothing says what the players were
in the middle of, and the hub is always safe.

**Crash recovery** holds the FSM paused while it relaunches a client, which keeps both watchers
off: a fight under way at the crash, or one the relaunched client logs back into, goes unanswered
for the whole recovery. So once every client is back, and before the group is re-formed,
`ClientManager` calls `BotMachine.settle_fights`. It flees every idle popup, then runs a session
for anyone still in battle, with `may_move=True` since no node is running. The bot goes to its
recovery state afterwards either way.

## Pitfalls

- **Keep still until the move.** Anything that turns or moves a participant between the end of the
  fight and the move wastes the 30s ghost window.
- **The mob check only sees loaded mobs** (about 3800 units, per #130's probe). That is why a
  helper's clearance is read before it leaves, and why every move is checked on arrival.
- **A clear reading is a snapshot.** Mobs walk fixed paths, so a patrol can still pass. That is why
  a spot someone has *stood on* ranks above anything measured once.
- **Never aim a helper at a center on another realm.** It starts a second fight.
  `FightCenter(share=...)` enforces it, and `join_fight` with no `find_target` never aims at "the
  closest mob" either; do not bypass either.
- **Read the duel phase and duel center only while `in_battle()` is true.** Outside a fight the duel
  struct holds the last fight's data.
- **Never port a Separate bot's players.** A port moves the porter onto the target's realm, which is
  exactly the split the bot is built on.
