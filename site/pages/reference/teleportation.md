---
# Written by tools/build_site_reference.py, so an edit here is lost at the next render.
# Fix the text where it comes from (site/CLAUDE.md, Generated pages), then run
# uv run python tools/build_site_reference.py
generated: true
---

# Teleportation

The nodes in the node browser's **Teleportation** group, with the help it shows for each one.

## Mark Location { #mark-location }

Marks every player's location. A player who already marked this spot in this run is left alone, so a loop can pass through the node every time.

The bot stops after 5 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Zone** | The zone every player must be in when the node starts. | required |
| **Mark XYZ** | Where to mark. Players elsewhere in Zone teleport here first. | required |

**Returns**

- **SUCCESS**: Every player has a mark here, set now or earlier in this run.
- **FAILURE**: A player wasn't in Zone, had no mana, was in a fight or a zone that refuses marks, or the game didn't answer within Safety Delay.

**Bot options** it adds to the bot's **Options** tab:

- [Safety Delay](options.md#safety-delay)

**Usage notes**

Marking costs mana, so a player with no mana can't mark. If recall is on cooldown, the node waits for it first.

Each player waits Safety Delay before marking, so the game knows where the player now stands. A mark set sooner can land where the player stood before the last teleport. Raise Safety Delay on a slow connection if marks fail or a recall lands somewhere else.

## Sync in Zone { #sync-in-zone }

Brings every player to the chosen zone, together and on one realm, from wherever they are. One player leads, and the others teleport to it.

The bot stops after 3 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Zone** | The zone to bring every player to. Type part of a zone's name, world or ID to narrow the list. Players can start anywhere, even in another world. | required |
| **Keep Current Realm** | When on, nobody changes realm, and everyone joins the leader on the realm it's on. Use it partway through a bot that left the Favorite Realm on purpose.<br><br>When off, the leader changes to your Favorite Realm on arrival. | off |

**Returns**

- **SUCCESS**: Every player is in Zone, on one realm, beside the leader.
- **FAILURE**: There's no route from where players are, usually because a world or teleporter isn't unlocked yet. Or a step failed with no other route left, Zone isn't on the navigation map, a player is in a restricted zone, the leader couldn't change realm, or a player couldn't reach the leader.

**Bot options** it adds to the bot's **Options** tab:

- [Player Realms](options.md#player-realms)
- [Safety Delay](options.md#safety-delay)
- [Favorite Realm](options.md#favorite-realm), while **Keep Current Realm** is off

**Usage notes**

Follow this node with Teleport to Coordinates when the exact spot matters. Players arrive wherever the last zone change puts them.

Players already together in the zone stay where they are, so the node is cheap at the top of a bot.

On a long route, or one through a teleporter, the leader travels and the others teleport to it on arrival. A player who can't teleport travels alone, then changes to the leader's realm.

With Player Realms set to Separate, every player travels on its own and nobody changes realm.

**See also:** [Teleport to World Hub](#teleport-to-world-hub), [Change Realm](utility.md#change-realm)

## Teleport to Coordinates { #teleport-to-coordinates }

Teleports every player to a spot in the current zone.

The bot stops after 20 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Zone** | The zone every player must be in when the node starts. | required |
| **Target XYZ** | Where in Zone to teleport players to. | required |

**Returns**

- **SUCCESS**: Every player is at Target XYZ.
- **FAILURE**: A player wasn't in Zone at the start, or didn't reach Target XYZ after several tries.

**Bot options** it adds to the bot's **Options** tab:

- [Safety Delay](options.md#safety-delay)

## Teleport to Friend { #teleport-to-friend }

Teleports players through the friends list to one of the bot's own players, or to the wizard in the Friend Name option. Checks that everyone arrived.

The bot stops after 5 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Teleport to a Hooked Player** | When on, players teleport to one of the bot's own players. When off, they teleport to the wizard in the Friend Name option. | on |
| **Target Player** | Which of the bot's players the others teleport to. That player must already be at the destination. | none |
| **Destination Zone** | Where the target should be. With a hooked player, the node fails before anyone teleports if the target is elsewhere. Leave it empty when you can't know, such as a friend's house. | anywhere, as long as everyone ends up together |

**Returns**

- **SUCCESS**: Every player is with the target, and in Destination Zone if it's set.
- **FAILURE**: The friend isn't online or has teleports off, no target is set or detected yet, a hooked target isn't in Destination Zone, a player didn't teleport, or not every player reached the target.

**Bot options** it adds to the bot's **Options** tab:

- [Friend Name](options.md#friend-name), while **Teleport to a Hooked Player** is off

!!! warning "Shown before a run"

    This bot teleports your players to another wizard, who must be online and on every player's friends list. If the name is wrong, change it here, in this bot's Options, or in Settings › Bot.

**Usage notes**

Every account must be on the target's friends list, even when the target is one of the bot's own players. The node teleports through the friends list.

A teleport within the same zone and realm shows no loading screen, and one onto a player already beside you shows nothing. Both count as arrived.

## Teleport to Marked Location { #teleport-to-marked-location }

Recalls every player to their mark and checks that they arrived.

The bot stops after 5 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Zone** | The zone every player must be in when the node ends. | each player's mark zone |
| **Mark XYZ** | Where players should be after recalling. | each player's mark |

**Returns**

- **SUCCESS**: Every player recalled to Zone and Mark XYZ.
- **FAILURE**: A player had no mana or no mark, the game refused the recall (usually because the marked dungeon has reset), a player never started loading, or not every player arrived.

**Bot options** it adds to the bot's **Options** tab:

- [Player Realms](options.md#player-realms)

**Usage notes**

Leave Zone and Mark XYZ empty to check each player against the mark it set earlier in this run, with Mark Location or Handle Level Scaling. A player with no mark from this run then fails the node before anyone recalls.

If only some players arrive, the others teleport to one who did before the node fails. This needs every player on the others' friends lists.

## Teleport to World Hub { #teleport-to-world-hub }

Teleports every player to their world's hub with the compass's hub button.

The bot stops after 10 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Hub Zone** | The hub zone every player must be in when the node ends. If every player is already there, the node succeeds at once. | required |

**Returns**

- **SUCCESS**: Every player is in Hub Zone.
- **FAILURE**: The hub button stayed grayed, or not every player reached Hub Zone.

**Bot options** it adds to the bot's **Options** tab:

- [Player Realms](options.md#player-realms)

**Usage notes**

If only some players arrive, the others teleport to one who did before the node fails. This needs every player on the others' friends lists.
