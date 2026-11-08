---
# Written by tools/build_site_reference.py, so an edit here is lost at the next render.
# Fix the text where it comes from (site/CLAUDE.md, Generated pages), then run
# uv run python tools/build_site_reference.py
generated: true
---

# Zone Entry

The nodes in the node browser's **Zone Entry** group, with the help it shows for each one.

## Enter Dungeon { #enter-dungeon }

Enters a dungeon through its sigil and checks that every player arrived. Fails if a player outside the team entered with them.

The bot stops after 10 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **From Zone** | The zone every player must be in when the node starts. | required |
| **Dungeon Zone** | The zone every player must be in when the node ends. | required |
| **Dungeon Name** | The dungeon's name as its entrance prompt shows it. Record fills it in, and it's used only to name the dungeon in the bot's description. | named by its zone, if at all |
| **Sigil XYZ** | Where players stand to get the dungeon's entrance prompt. | required |
| **Reset XYZ** | A nearby spot where the prompt doesn't appear. Players step here to clear the prompt before trying again. | required |
| **Offers Challenge Mode** | Turn on if this sigil offers Challenge Mode. The user then gets a Challenge Mode bot option. | normal mode only |
| **Offers Team-Up** | Turn on if this sigil offers Team-Up and a stranger joining the run is acceptable. The user then gets the Team-Up and Minimum Team Size bot options. | no Team-Up |

**Returns**

- **SUCCESS**: Every player is in Dungeon Zone.
- **FAILURE**: A player wasn't in From Zone, the entrance prompt never appeared, not every player reached Dungeon Zone, a player outside the team entered with them, the sigil couldn't switch to Challenge Mode, or Team-Up didn't open, a player couldn't join its queue, or it didn't match in time. Team-Up runs skip the check for outsiders.

**Bot options** it adds to the bot's **Options** tab:

- [Player Realms](options.md#player-realms)
- [Safety Delay](options.md#safety-delay)
- [Challenge Mode](options.md#challenge-mode), while **Offers Challenge Mode** is on
- [Team-Up](options.md#team-up), while **Offers Team-Up** is on
- [Minimum Team Size](options.md#minimum-team-size), while **Offers Team-Up** is on

!!! warning "Shown before a run"

    Team-Up is on with more than one player. Each player queues alone and may land in a different copy of the dungeon, so the bot brings them together on arrival and stops if the game refuses.

**Usage notes**

If only some players arrive, the others teleport to one who did before the node fails. This needs every player on the others' friends lists.

## Enter Gauntlet { #enter-gauntlet }

Enters a gauntlet placed in a player's house. Players must already be at its sigil, since this node doesn't teleport them.

The bot stops after 10 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Dungeon Zone** | The zone every player must be in when the node ends. | required |
| **Gauntlet Name** | The gauntlet's name as its entrance prompt shows it. Record fills it in, and it's used only to name the gauntlet in the bot's description. | named by its zone, if at all |

**Returns**

- **SUCCESS**: Every player is in Dungeon Zone.
- **FAILURE**: The entrance prompt never appeared, or not every player reached Dungeon Zone.

**Bot options** it adds to the bot's **Options** tab:

- [Safety Delay](options.md#safety-delay)

## Enter Zone { #enter-zone }

Moves every player to another zone by teleporting them into the spot that triggers the zone change. Can send four players one at a time to keep them on one realm.

The bot stops after 10 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **From Zone** | The zone every player must be in when the node starts. | required |
| **To Zone** | The zone every player must be in when the node ends. | required |
| **Transition XYZ** | A spot inside the zone change's trigger. Teleporting here starts the zone change. | required |
| **Reset XYZ** | A spot clear of the trigger, where players step to re-arm it. A player whose zone change doesn't fire is moved here and back, up to three times. | required |
| **Stagger Players** | When on, the first player goes through alone, and the rest follow once it has loaded, so all four end up on one realm. Applies only with four players. | off |

**Returns**

- **SUCCESS**: Every player is in To Zone.
- **FAILURE**: A player wasn't in From Zone at the start, or not every player reached To Zone.

**Bot options** it adds to the bot's **Options** tab:

- [Player Realms](options.md#player-realms)
- [Safety Delay](options.md#safety-delay)

**Usage notes**

Keep Reset XYZ clear of the trigger. Record sets it to where players stood when recording started. Left empty, each player resets to where it stood when the node started.

If only some players arrive, the others teleport to one who did before the node fails. This needs every player on the others' friends lists.

## Enter Zone via Object { #enter-zone-via-object }

Interacts with an object that changes zone, such as a snow globe or portal, until every player is in To Zone.

The bot stops after 10 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **From Zone** | The zone every player must be in when the node starts. | required |
| **To Zone** | The zone every player must be in when the node ends. | required |
| **Interact XYZ** | Where players stand to get the interact prompt. | required |
| **Reset XYZ** | A nearby spot where the prompt doesn't appear. Players step here to clear the prompt before trying again. | required |
| **Sequential Interaction** | When on, players use the object one at a time. Turn it on only for an object that serves one player at a time, since it's slower. | all players at once |
| **Delay Between Object Uses (s)** | How long to wait between one player finishing and the next starting, with Sequential Interaction on. Raise it for an object that takes several seconds to reset, such as a teleporter. | 1 second |

**Returns**

- **SUCCESS**: Every player is in To Zone.
- **FAILURE**: A player wasn't in From Zone, no player got the interact prompt, or not every player reached To Zone.

**Bot options** it adds to the bot's **Options** tab:

- [Player Realms](options.md#player-realms)
- [Safety Delay](options.md#safety-delay)

**Usage notes**

Turn on Sequential Interaction when the object serves one player at a time. The sign is that one player interacts and the others' X presses sometimes do nothing. It adds about one interaction's time per extra player. Zone teleporters are the usual case.

If only some players arrive, the others teleport to one who did before the node fails. This needs every player on the others' friends lists.

## Open Skeleton Key Door { #open-skeleton-key-door }

Unlocks a skeleton key door with one player's key and takes every player through it.

The bot stops after 5 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **From Zone** | The zone every player must be in when the node starts. | required |
| **To Zone** | The zone every player must be in when the node ends. | required |
| **Interact XYZ** | Where players stand to get the interact prompt. | required |
| **Transition XYZ** | A spot inside the zone change's trigger. Teleporting here starts the zone change. | required |
| **Reset XYZ** | A nearby spot where the prompt doesn't appear. Players step here to clear the prompt before trying again. | required |

**Returns**

- **SUCCESS**: Every player is in To Zone.
- **NO\_KEY**: No player got the interact prompt, and the door isn't open.
- **FAILURE**: A player was in neither zone, the prompt stayed after X, or not every player reached To Zone.

**Bot options** it adds to the bot's **Options** tab:

- [Player Realms](options.md#player-realms)
- [Safety Delay](options.md#safety-delay)

**Usage notes**

Use it for a skeleton key door, in a skeleton key boss dungeon or a side room of an ordinary one. No Enter Zone follows it: it takes every player through.

The lowest-numbered player with a key presses X, so one player's keys run out before the next player's are used. A player without a key gets no prompt and is skipped. One key opens the door for everyone in the dungeon, so the players must be on one realm.

With four players, the one who used the key goes through first and the rest follow once it has loaded, so all four end up on one realm. With fewer, they all go together.

Give NO\_KEY a route or a stop: it's the one failure that trying again won't fix, and it doesn't go to the recovery state by itself. If the door is already open, the node spends no key and takes everyone through. Keep Reset XYZ clear of the door's trigger too.

If only some players arrive, the others teleport to one who did before the node fails. This needs every player on the others' friends lists.

**See also:** [Interact](interaction.md#interact), [Enter Zone](#enter-zone)
