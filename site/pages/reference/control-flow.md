---
# Written by tools/build_site_reference.py, so an edit here is lost at the next render.
# Fix the text where it comes from (site/CLAUDE.md, Generated pages), then run
# uv run python tools/build_site_reference.py
generated: true
---

# Control Flow

The nodes in the node browser's **Control Flow** group, with the help it shows for each one.

## Branch { #branch }

Does nothing and returns SUCCESS. Its edges choose where to go next.

This node has no parameters.

**Returns**

- **SUCCESS**: Always, at once.

**Usage notes**

Give its SUCCESS port several edges with conditions, plus one without for when none is true. Several nodes can lead into one Branch and share its edges.

**See also:** [Set Variables](#set-variables)

## Check Health and Mana { #check-health-and-mana }

Checks whether every player's health and mana are at or above the thresholds. Use it to skip a detour, such as collecting wisps, when players are already healthy.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Health Threshold** | The node returns FALSE if any player's health is below this. At 0%, health isn't checked. | full health |
| **Mana Threshold** | The node returns FALSE if any player's mana is below this. At 0%, mana isn't checked. At 1%, only a player with no mana fails, as after fleeing a battle. | full mana |

**Returns**

- **TRUE**: Every player is at or above both thresholds.
- **FALSE**: At least one player is below at least one threshold.

## Check Level Scaling { #check-level-scaling }

Checks whether every Level Scaling Player has the level scaling elixir.

This node has no parameters.

**Returns**

- **TRUE**: Every Level Scaling Player has the elixir.
- **FALSE**: A Level Scaling Player doesn't have the elixir.

**Bot options** it adds to the bot's **Options** tab:

- [Level Scaling Players](options.md#level-scaling-players)

## Check Zone { #check-zone }

Checks whether the chosen players are all in a zone.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Zone** | The zone to check, such as WizardCity/WC\_Ravenwood. | required |
| **Players to Check** | Which players to check. | required |

**Returns**

- **TRUE**: Every checked player is in Zone.
- **FALSE**: A checked player isn't in Zone.

## Set Variables { #set-variables }

Sets the bot's variables, one row at a time.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Assignments** | Each row sets one variable: to a value, by adding a whole number to an int, or by toggling a bool. Rows apply in order, so a later row sees an earlier one's change. | none |

**Returns**

- **SUCCESS**: Always, once every row is written.

**Usage notes**

Use this node to remember something for a later edge condition, such as which rack is next. It doesn't touch the game.

All rows are written together. More than a few dozen variable nodes in a row stop the run.

**See also:** [Branch](#branch)
