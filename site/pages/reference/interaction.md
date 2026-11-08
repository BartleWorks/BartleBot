---
# Written by tools/build_site_reference.py, so an edit here is lost at the next render.
# Fix the text where it comes from (site/CLAUDE.md, Generated pages), then run
# uv run python tools/build_site_reference.py
generated: true
---

# Interaction

The nodes in the node browser's **Interaction** group, with the help it shows for each one.

## Click Path { #click-path }

Clicks the game's UI element at a UI path, once it's visible and enabled. Can confirm the click worked by waiting for a second path.

The bot stops after 5 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **UI Path** | The UI path of the element to click, such as WorldView › windowHUD › btnSpellbook. | required |
| **Click Players** | Which players click. Choose some when the element is open only for some players, such as a menu that Interact Menu opened for fewer players. | all players |
| **Match Text** | When several elements share the path, click only one whose text, or the text of something inside it, matches this. The UI Tree Browser's Text column shows each element's text. | no text filter |
| **Match Whole Text** | When on, the text must match Match Text exactly. When off, it only has to contain it. Case never matters. | off |
| **Match Index** | Which matching element to click, counting from 0 in tree order, after Match Text. A negative number counts from the end, so -1 is the last. | first match |
| **Availability Timeout (s)** | How long to wait for the element to be visible and enabled. At 0, the node checks once. | 10 seconds |
| **Confirm Path** | A UI path that shows the click worked. After clicking, the node waits for its first match. | no check |
| **Confirm When Visible** | When on, the node waits for Confirm Path to be visible. When off, it waits for it to be enabled. | on |
| **Confirm Timeout (s)** | How long to wait for Confirm Path after clicking. | 10 seconds |

**Returns**

- **SUCCESS**: The element was clicked, and Confirm Path, if set, was met.
- **FAILURE**: No element matched in time, the click didn't register, or Confirm Path wasn't met in time.

**Usage notes**

Copy UI paths from the UI Tree Browser. A path matches window names only, so several elements can share one, as the rows of a list menu do. Match Text and Match Index pick among them. Without them, the first match is clicked, and the log warns that there were more.

Every player clicks unless Click Players narrows it. For a menu only some players opened, choose the same players here.

## Collect Item { #collect-item }

Teleports one player to the nearest listed quest item and picks it up.

The bot stops after 5 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Zone** | The zone every player must be in when the node starts. | required |
| **Starting XYZ** | Where the Collecting Player teleports before searching. An item only shows to a player nearby, so choose a spot within range of where it spawns. | where the player stands |
| **Entity IDs** | The IDs of the item, as the Entity Browser shows them, not its in-game name. Each must be a whole ID, in any case, so Stone never collects Sandstone. Use \* and ? to match a pattern, such as \*Stone\*. | required |
| **Collecting Player** | Which player picks the item up. In an instance one pickup counts for the whole group, so one player is enough. | Player 1 |

**Returns**

- **SUCCESS**: The Collecting Player picked up an item.
- **FAILURE**: No item was in range of Starting XYZ, picking it up failed, a player wasn't in Zone, the Collecting Player isn't connected, or Entity IDs is empty.

**Bot options** it adds to the bot's **Options** tab:

- [Safety Delay](options.md#safety-delay)

!!! warning "Shown before a run"

    This bot teleports to collect items. Run it on a quiet realm.

**Usage notes**

Use it for a quest item that spawns in one of several places, such as a piece of a puzzle. For reagents, use Collect Reagent, which collects every one it finds and records the drops.

Route FAILURE to the next place to search. An item shows only to a player nearby, so FAILURE means none was in range of Starting XYZ, and spawns far apart need a node for each area. The node waits up to Safety Delay for an item to show.

Only the Collecting Player moves. The others stay where they are.

**See also:** [Collect Reagent](#collect-reagent), [Interact](#interact)

## Collect Reagent { #collect-reagent }

Collects every listed reagent in the zone, each player on its own. Finding none succeeds, since spawns are random.

The bot stops after 5 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Zone** | The zone every player must be in when the node starts. | required |
| **Starting XYZ** | Where to teleport players before searching. | where players stand |
| **Entity IDs** | The IDs of the reagents to collect, as the Entity Browser shows them, not their in-game names. Each must be a whole ID, in any case, so Stone never collects Sandstone. Use \* and ? to match a pattern, such as \*Stone\*. | required |

**Returns**

- **SUCCESS**: The zone was searched, whether or not anything was found.
- **FAILURE**: A player wasn't in Zone, or Entity IDs is empty.

**Bot options** it adds to the bot's **Options** tab:

- [Safety Delay](options.md#safety-delay)

!!! warning "Shown before a run"

    This bot teleports to collect reagents. Run it on a quiet realm.

**See also:** [Collect Item](#collect-item)

## Interact { #interact }

Interacts with an NPC or object and clicks through any dialogue that follows.

The bot stops after 10 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Zone** | The zone every player must be in when the node starts. | required |
| **Interact XYZ** | Where players stand to get the interact prompt. | required |
| **Reset XYZ** | A nearby spot where the prompt doesn't appear. Players step here to clear the prompt before trying again. | required |
| **Interact Players** | Which players interact. | required |
| **Has Dialogue** | Turn off for an interaction without dialogue, such as a lever, so the node doesn't wait for any. Unexpected dialogue is still clicked through. | on |
| **Animation Delay (s)** | Extra seconds to wait for an animation or cutscene before dialogue opens, on top of Safety Delay. | none |
| **Sequential Interaction** | When on, players use the object one at a time. Turn it on only for an object that serves one player at a time, since it's slower. | all players at once |
| **Delay Between Object Uses (s)** | How long to wait between one player finishing and the next starting, with Sequential Interaction on. Raise it for an object that takes several seconds to reset, such as a teleporter. | 1 second |

**Returns**

- **SUCCESS**: Every player interacted, and all dialogue is closed.
- **FAILURE**: The interact prompt never appeared, or dialogue didn't close in time.

**Bot options** it adds to the bot's **Options** tab:

- [Safety Delay](options.md#safety-delay)

**Usage notes**

Turn on Sequential Interaction when the object serves one player at a time. The sign is that one player interacts and the others' X presses sometimes do nothing. It adds about one interaction's time per extra player.

## Interact Menu { #interact-menu }

Interacts with an NPC or object that opens a menu, such as a shop, and checks that the menu opened. For an interaction that starts dialogue, use Interact.

The bot stops after 10 consecutive failures on this node.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Zone** | The zone every player must be in when the node starts. | required |
| **Interact XYZ** | Where players stand to get the interact prompt. | required |
| **Reset XYZ** | A nearby spot where the prompt doesn't appear. Players step here to clear the prompt before trying again. | required |
| **Interact Players** | Which players interact. | required |
| **Menu Path** | The UI path of an element that appears when the menu opens. The node waits for it after pressing X. | required |
| **Menu Timeout (s)** | How long to wait for Menu Path after pressing X. | 10 seconds |
| **Sequential Interaction** | When on, players use the object one at a time. Turn it on only for an object that serves one player at a time, since it's slower. | all players at once |
| **Delay Between Object Uses (s)** | How long to wait between one player finishing and the next starting, with Sequential Interaction on. Raise it for an object that takes several seconds to reset, such as a teleporter. | 1 second |

**Returns**

- **SUCCESS**: Menu Path is visible for every player who interacted.
- **FAILURE**: A player wasn't in Zone, the interact prompt never appeared, or Menu Path didn't appear within Menu Timeout.

**Bot options** it adds to the bot's **Options** tab:

- [Safety Delay](options.md#safety-delay)

**Usage notes**

Set Menu Path to an element that's visible only while the menu is open. The UI Tree Browser shows its path.

Sequential Interaction checks each player's menu in turn. On an object that serves one player at a time, the previous player's menu may close as the next one opens.

## Wait for Dialogue { #wait-for-dialogue }

Clicks through dialogue on every player until none has appeared for Safety Delay plus Animation Delay.

**Parameters**

| Parameter | What it does | Default |
|---|---|---|
| **Timeout (s)** | The longest to wait before failing. 0 means no limit. | 60 seconds |
| **Animation Delay (s)** | Extra seconds of quiet to wait for, on top of Safety Delay, so a pause between two dialogue windows isn't taken for the end. | none |

**Returns**

- **SUCCESS**: No player has had dialogue for the whole quiet period.
- **FAILURE**: Timeout ran out while a player still had dialogue.

**Bot options** it adds to the bot's **Options** tab:

- [Safety Delay](options.md#safety-delay)
