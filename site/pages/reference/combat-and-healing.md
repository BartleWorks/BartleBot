---
# Written by tools/build_site_reference.py, so an edit here is lost at the next render.
# Fix the text where it comes from (site/CLAUDE.md, Generated pages), then run
# uv run python tools/build_site_reference.py
generated: true
---

# Combat and Healing

The nodes in the node browser's **Combat and Healing** group, with the help it shows for each one.

## Collect Wisps { #collect-wisps }

Collects health and mana wisps after a fight, until every player meets its thresholds or no wisps are left. Fails if a player is still below the fail thresholds, so you can route to a potion node.

The bot stops after 15 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Collect Health Wisps If Below** | Players below this share of their health collect health wisps. At 0%, health wisps are skipped. | below full health |
| **Collect Mana Wisps If Below** | Players below this share of their mana collect mana wisps. At 0%, mana wisps are skipped. | below full mana |
| **Timeout (s)** | The longest to spend collecting. 0 means no limit. | 10 seconds |
| **Fail If Health Still Below** | After collecting, the node fails if any player's health is still below this. At 0%, it never fails on health. | 50% |
| **Fail If Mana Still Below** | After collecting, the node fails if any player's mana is still below this. At 0%, it never fails on mana. | 50% |
| **Only Collect Safe Wisps** | When on, skips wisps within 1500 units of an enemy, so collecting doesn't start a fight. Turn off only where enemies are always nearby. | skip wisps near enemies |

**Returns**

- **SUCCESS**: Every player is at or above both fail thresholds.
- **FAILURE**: A player is still below a fail threshold.

**Bot options** it adds to the bot's **Options** tab:

- [Safety Delay](options.md#safety-delay)

**Usage notes**

Players move together, teleporting to the same wisp, so a player who's already full passes through without using it up.

Place it right after a fight or a teleport. Wisps take a moment to appear, so when none is in sight and a player is below a fail threshold, the node waits up to Safety Delay for one before it gives up. If nobody is below a fail threshold, it doesn't wait.

With Challenge Mode on, a player outside the dungeon is measured against the lower maximum its next entry scales it to, as in Handle Potions.

## Handle Combat { #handle-combat }

Teleports every player to an enemy and fights until the battle ends. Drops are recorded as chat announces them.

The bot stops after 5 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Enemy ID** | The enemy's ID, as Record and the Entity Browser show it, not its in-game name. An exact match wins, then any ID containing this, so an advanced-mode copy is still found. Use \* and ? to match a pattern, such as \*Bandit\*. | the closest enemy |
| **Wiki Page** | The Wizard101 Central Wiki page for this enemy, such as Creature:Lost Soul. Paste the URL or the title. It records where the drop table came from, and isn't used while the bot runs. | none |
| **Use Default Strategy** | When on, this fight uses the bot's default Duel Strategy. When off, the fight is listed on the bot's Combat tab, where you can give it its own. | on |
| **Max Duration (s)** | How long the whole node may take, finding the enemy included. A fight still going then is fled, and the node fails. 0 means no limit. | 600 seconds |

**Returns**

- **SUCCESS**: Every player finished the battle.
- **FAILURE**: No enemy was found, a player had no mana, the battle didn't start or end in time, or the fight was fled.

**Bot options** it adds to the bot's **Options** tab:

- [Player Realms](options.md#player-realms)

## Handle Potions { #handle-potions }

Has each player below a health or mana threshold use a potion. With Refill Potions on, players who run out mark their spot, buy more from Hilda Brewer in the Commons, use one if they still need it, and recall.

The bot stops after 5 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Use Potion If Health Below** | Players below this share of their health use a potion. At 0%, health is never checked. | required |
| **Use Potion If Mana Below** | Players below this share of their mana use a potion. At 0%, mana is never checked. | required |
| **Mark Zone** | The zone players must be in before leaving to buy potions, so the mark lands somewhere predictable, such as a dungeon entrance. The node fails if they're elsewhere. | any zone |

**Returns**

- **SUCCESS**: Every player who needed a potion used one, and players who ran out refilled and are back.
- **FAILURE**: A player who needed to buy wasn't in Mark Zone, couldn't mark for a reason other than mana, couldn't reach the potion shop, or couldn't mark or recall and then couldn't teleport back to another player. Players can be left in the Commons.

**Bot options** it adds to the bot's **Options** tab:

- [Refill Potions](options.md#refill-potions)
- [Safety Delay](options.md#safety-delay)

**Usage notes**

With Challenge Mode on, thresholds are measured against the lower maximums the next entry scales players to, so the same thresholds work in both modes.

A player with too little mana to mark goes to buy anyway. After buying, it teleports to a player who stayed behind or recalled, through the friends list or the group. So does a player whose recall fails. A teleported player lands beside that player, away from its own starting spot. With Player Realms set to Separate, a player only teleports to one that shared its realm before the trip.

A purchase that buys nothing, usually for lack of gold, is logged as a warning, and the node goes on.
