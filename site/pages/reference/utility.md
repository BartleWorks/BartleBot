---
# Written by tools/build_site_reference.py, so an edit here is lost at the next render.
# Fix the text where it comes from (site/CLAUDE.md, Generated pages), then run
# uv run python tools/build_site_reference.py
generated: true
---

# Utility

The nodes in the node browser's **Utility** group, with the help it shows for each one.

## Change Realm { #change-realm }

Switches every player to the Favorite Realm, or to the next realm in the list. A player already on its target realm is left alone.

The bot stops after 5 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Use Favorite Realm** | When on, players switch to the Favorite Realm, set in Settings › Bot or this bot's Options.<br><br>When off, players move to the next realm, through the quieter pages of the list. Use this where a fresh realm means fresh spawns, as in reagent farming. | on |

**Returns**

- **SUCCESS**: Every player is on its target realm, and with Player Realms set to Together, beside the others.
- **FAILURE**: The game refused the change, no favorite realm is set, the spellbook or realm list didn't open, the realm wasn't found, the realm-change cooldown didn't end in time, or a player couldn't be brought to the others.

**Bot options** it adds to the bot's **Options** tab:

- [Player Realms](options.md#player-realms)
- [Safety Delay](options.md#safety-delay)
- [Favorite Realm](options.md#favorite-realm), while **Use Favorite Realm** is on

**Usage notes**

Place this node before entering a dungeon, or after leaving it. The game refuses a realm change in a dungeon, gauntlet or house, and the node fails.

## Handle Level Scaling { #handle-level-scaling }

Gives the level scaling elixir to every Level Scaling Player who lacks it. Players travel to the chosen Scales of Knowledge and recall, or take it in place if they're already in the Scales' dungeon.

The bot stops after 3 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Scales of Knowledge** | Which Scales of Knowledge to take the elixir from, usually the one in the world this bot farms. Type part of a dungeon's name, world or level to narrow the list. | the Scales of the world this bot farms |

**Returns**

- **SUCCESS**: Every Level Scaling Player has the elixir, and players are back where they were, or still in the Scales' dungeon if they started there.
- **FAILURE**: The trip to the Scales failed, a player couldn't mark, a player still lacks the elixir after every retry, a player didn't land back on its mark, or this version doesn't know the Scales.

**Bot options** it adds to the bot's **Options** tab:

- [Player Realms](options.md#player-realms)
- [Safety Delay](options.md#safety-delay)
- [Level Scaling Players](options.md#level-scaling-players)

**Usage notes**

In a bot that farms the Scales' own dungeon, place this node after entering the dungeon. Placed before, players make an extra trip in and out.

When every Level Scaling Player already has an elixir, the node succeeds at once, so it's cheap at the top of a loop.

Left unset, the Scales is the one in the world this bot farms. A world with several Scales, or none, has no default.

Every player makes the trip, including those already scaled. The dungeon is entered in normal mode.

In a Challenge Mode dungeon, the node does nothing and succeeds, since the game already scales everyone.

The node never cancels an elixir. Any active elixir reads as level scaled, so a player with a different elixir isn't scaled, and the run warns about it at start.

**See also:** [Sync in Zone](teleportation.md#sync-in-zone), [Enter Dungeon](zone-entry.md#enter-dungeon)

## Quicksell { #quicksell }

Sells drops when the backpack or jewel pouch is full, except items a keep list keeps. Crowns items are sold like any other.

The bot stops after 5 consecutive failures on this node.

This node has no parameters.

**Returns**

- **SUCCESS**: Nothing was full, or the drops were sold.
- **FAILURE**: The quicksell window didn't open, or the sale didn't finish.

**Bot options** it adds to the bot's **Options** tab:

- [Safety Delay](options.md#safety-delay)
- [Quicksell](options.md#quicksell)
- [Sell Pets](options.md#sell-pets)
- [Sell Socket Wrenches](options.md#sell-socket-wrenches)
- [Sell Skeleton Keys](options.md#sell-skeleton-keys)
- [Quicksell Keep List](options.md#quicksell-keep-list)

!!! warning "Shown before a run"

    This bot sells drops when your backpack is full. Lock valuable items in the game, or add them to a keep list in this bot's Options or in Settings › Bot. To lock items, first turn on Backpack Item Lock in the game's Options › Gameplay Options 2. Turn off Sell Pets to keep pets. With quicksell off, nothing is sold. A full backpack overflows into the character's bank, and once the bank is full too, only Crowns items are kept.

## Relog { #relog }

Logs players out to character select and straight back in, which clears the slowdown of a long session in seconds.

The bot stops after 2 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Players to Relog** | Which players log out and back in. Relogging everyone at once is usually best, since the group re-forms once at the end. | every player |

**Returns**

- **SUCCESS**: Every selected player logged out and is back in the world.
- **FAILURE**: A fight didn't end in time, a dialog or the Crowns Shop wouldn't close, the spellbook or its settings page didn't open, or the game refused to log out. The player is still in the world in every case.

**Usage notes**

Place this node just before the bot travels. Players come back where they were before entering their current dungeon or house.

Settings › Bot › Relog interval does the same on a timer. Use this node when the moment matters, such as after a set number of loops, or on a FAILURE edge as a last resort.

The group is lost on logout, and re-formed afterward when Automatically group players is on in Settings › Bot.

Nobody logs out during a fight, and the group re-forms only after any fight the players log back into.

A dialog left open on a player, such as the prompt for leaving a dungeon, is closed first with Cancel or OK, so nothing it asks for happens. So is the Crowns Shop, before logging out and again after logging back in.

A player that can't log back in is closed and relaunched as after a crash, and the bot continues from its recovery state. If that player has no account or saved password, the bot stops.

A player that just logged in, after crash recovery or a scheduled relog, is skipped until it moves.

## Sleep { #sleep }

Waits for Duration, or for Safety Delay.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Duration (s)** | How long to wait. | required |
| **Use Safety Delay** | When on, the node waits for the user's Safety Delay instead of Duration. Use it when waiting on the game server, since that depends on the user's connection. | off |

**Returns**

- **SUCCESS**: Always, once the wait is over.

**Bot options** it adds to the bot's **Options** tab:

- [Safety Delay](options.md#safety-delay), while **Use Safety Delay** is on
