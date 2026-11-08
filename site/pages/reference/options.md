---
# Written by tools/build_site_reference.py, so an edit here is lost at the next render.
# Fix the text where it comes from (site/CLAUDE.md, Generated pages), then run
# uv run python tools/build_site_reference.py
generated: true
---

# Bot options

Every option a node adds to a bot, by the card it's in on the bot's **Options** tab. A bot's author can add options of their own, which the bot's description explains.

## Where an option's value comes from { #where-a-value-comes-from }

A run takes each option's value from the first of these that has one:

1. **Your value**: what you set for this bot in its **Options** tab.
2. **Bot default**: what the bot's author set for it.
3. **App settings**: what you set in **Settings › Bot**, for an option with a row there.
4. **Default**: the default under each option below.

Your values are kept in your settings, so the bot file stays as its author wrote it.

## This dungeon { #this-dungeon-card }

### Challenge Mode { #challenge-mode }

Enter the dungeon in Challenge Mode.

- **Default:** Off
- **Added by:** [Enter Dungeon](zone-entry.md#enter-dungeon) with **Offers Challenge Mode** on

## Team { #team-card }

### Friend Name { #friend-name }

The wizard to teleport to, when the node doesn't target one of the bot's own players. The wizard must be online and on every player's friends list, and the name is stored on this computer, never in the bot file.

- **Default:** none
- **App setting:** **Settings › Bot › Friend Name**
- **Bot default:** none. A bot's author can't set this option.
- **Added by:** [Teleport to Friend](teleportation.md#teleport-to-friend) with **Teleport to a Hooked Player** off

### Level Scaling Players { #level-scaling-players }

Which players need the level scaling elixir. Every level scaling node uses this list, and an empty list means no players.

- **Default:** none
- **Added by:** [Check Level Scaling](control-flow.md#check-level-scaling), [Handle Level Scaling](utility.md#handle-level-scaling)

### Minimum Team Size { #minimum-team-size }

How many players the Team-Up queue must gather before the dungeon starts. Larger teams take longer to match.

- **Default:** 2
- **Used while** [Team-Up](#team-up) is on
- **Added by:** [Enter Dungeon](zone-entry.md#enter-dungeon) with **Offers Team-Up** on

### Player Realms { #player-realms }

Together keeps every player on one realm. A player found on another realm is brought to the others before a battle or dungeon.

Separate gives each player its own realm and never moves one onto another's. Use it when players on one realm would compete for the same spawns, as reagent collectors do.

- **Default:** Together
- **Added by:** [Enter Dungeon](zone-entry.md#enter-dungeon), [Enter Zone](zone-entry.md#enter-zone), [Enter Zone via Object](zone-entry.md#enter-zone-via-object), [Open Skeleton Key Door](zone-entry.md#open-skeleton-key-door), [Sync in Zone](teleportation.md#sync-in-zone), [Teleport to Marked Location](teleportation.md#teleport-to-marked-location), [Teleport to World Hub](teleportation.md#teleport-to-world-hub), [Handle Combat](combat-and-healing.md#handle-combat), [Change Realm](utility.md#change-realm), [Handle Level Scaling](utility.md#handle-level-scaling)

### Team-Up { #team-up }

Enter the dungeon through the Team-Up queue. Matching can take up to an hour and brings in players who aren't yours.

- **Default:** Off
- **Added by:** [Enter Dungeon](zone-entry.md#enter-dungeon) with **Offers Team-Up** on

## Quicksell { #quicksell-card }

### Quicksell { #quicksell }

Sell drops so backpacks never fill. When off, a full backpack overflows into the character's bank, and once the bank is full too, only Crowns items are kept.

- **Default:** Off
- **App setting:** **Settings › Bot › Quicksell**
- **Bot default:** none. A bot's author can't set this option.
- **Added by:** [Quicksell](utility.md#quicksell)

### Quicksell Keep List { #quicksell-keep-list }

Which items quicksell keeps. A keyword keeps every item containing it, so "giver" keeps every Giver jewel, and a checked item keeps only itself. Your list replaces the author's, and your Always keep list in Settings › Bot applies too.

- **Default:** none
- **Added by:** [Quicksell](utility.md#quicksell)

### Sell Pets { #sell-pets }

Turn off to keep pets. The keep list can't protect a pet.

- **Default:** On
- **App setting:** **Settings › Bot › Sell pets**
- **Used while** [Quicksell](#quicksell) is on
- **Added by:** [Quicksell](utility.md#quicksell)

### Sell Skeleton Keys { #sell-skeleton-keys }

Turn off to keep any item with "Skeleton Key" in its name.

- **Default:** Off
- **App setting:** **Settings › Bot › Sell Skeleton Keys**
- **Used while** [Quicksell](#quicksell) is on
- **Added by:** [Quicksell](utility.md#quicksell)

### Sell Socket Wrenches { #sell-socket-wrenches }

Turn off to keep any item with "Socket Wrench" in its name.

- **Default:** Off
- **App setting:** **Settings › Bot › Sell Socket Wrenches**
- **Used while** [Quicksell](#quicksell) is on
- **Added by:** [Quicksell](utility.md#quicksell)

## General { #general-card }

### Favorite Realm { #favorite-realm }

The realm Change Realm and Sync in Zone send players to. It's stored on this computer, never in the bot file, and set in Settings › Bot.

- **Default:** none
- **App setting:** **Settings › Bot › Favorite Realm**
- **Bot default:** none. A bot's author can't set this option.
- **Added by:** [Sync in Zone](teleportation.md#sync-in-zone) with **Keep Current Realm** off, [Change Realm](utility.md#change-realm) with **Use Favorite Realm** on

### Refill Potions { #refill-potions }

Buy potions from Hilda Brewer when a player runs out. Each refill costs gold and a trip to the Commons, and players come back to where they were.

- **Default:** On
- **App setting:** **Settings › Bot › Refill potions**
- **Added by:** [Handle Potions](combat-and-healing.md#handle-potions)

### Safety Delay { #safety-delay }

Raise the delay on a slow connection: 3 to 5 seconds in North America, 5 to 10 elsewhere. The delay applies to prompts, menus, zone changes and marks.

- **Default:** 3 seconds
- **App setting:** **Settings › Bot › Default Safety Delay**
- **Bot default:** none. A bot's author can't set this option.
- **Added by:** [Enter Dungeon](zone-entry.md#enter-dungeon), [Enter Gauntlet](zone-entry.md#enter-gauntlet), [Enter Zone](zone-entry.md#enter-zone), [Enter Zone via Object](zone-entry.md#enter-zone-via-object), [Open Skeleton Key Door](zone-entry.md#open-skeleton-key-door), [Mark Location](teleportation.md#mark-location), [Sync in Zone](teleportation.md#sync-in-zone), [Teleport to Coordinates](teleportation.md#teleport-to-coordinates), [Collect Wisps](combat-and-healing.md#collect-wisps), [Handle Potions](combat-and-healing.md#handle-potions), [Collect Item](interaction.md#collect-item), [Collect Reagent](interaction.md#collect-reagent), [Interact](interaction.md#interact), [Interact Menu](interaction.md#interact-menu), [Wait for Dialogue](interaction.md#wait-for-dialogue), [Change Realm](utility.md#change-realm), [Handle Level Scaling](utility.md#handle-level-scaling), [Quicksell](utility.md#quicksell), [Sleep](utility.md#sleep) with **Use Safety Delay** on
