# The canvas

The bot canvas is where a bot's steps are laid out: each node is one step, and each edge says where the bot goes next. This page covers editing it: Edit mode, adding nodes, a node's page, recording its values from the game, and the two browsers that look inside the game for you.

## Switch to Edit mode { #edit-mode }

Select the pencil, **Enter Edit Mode**, at the right-hand end of the bar above the canvas. A bot made with **New Bot** opens in Edit mode already. An Official bot can't be changed, so the pencil offers to make your own copy instead ([The bot library](../running/library.md#make-your-own-copy-of-an-official-bot)).

In Edit mode:

- The left sidebar has **Options**, where the bot's options and variables are declared, and **Properties**, the page of whatever is selected.
- The bar above the canvas gains **Add State** and **Auto-Layout states**, which tidies the graph.
- A favorites bar shows above the canvas. Selecting a favorite adds that node.
- **Edit › Undo** (Ctrl+Z) and **Edit › Redo** (Ctrl+Shift+Z) step through your changes.

Save with **Save** (Ctrl+S), the button beside the checkmark. A dot on it means unsaved changes. The checkmark, **Save and finish editing**, saves and goes back to Run mode, where the bot can run. When a bot has no Rewards tag, saving asks what a run of it yields, so the library's filters can find it. Pick one, or select **Save Without**.

## Add a node { #add-a-node }

Select **Add State**, or right-click an empty spot on the canvas and select **Add State Here**. The **Add Node** window opens.

- Type in the search box, or pick a tab: **All**, **Recent**, **Favorites**, a group of nodes, or **Subgraphs**.
- Select a node to read its page on the right: what it does, its parameters, what it returns, and the bot options it adds. The [Reference](../../reference/index.md) has the same pages.
- The star on a node's page adds it to your favorites.
- Select **Add**, or double-click the node.

Other ways in:

- Right-click an edge and select **Insert State Here**, to put a node between two others.
- Right-click a node's port and select **Insert State at SUCCESS Port**, or at whichever port you clicked. The new node takes over every edge on that port.
- Right-click a node and select **Node Help** to open its page in the **Node Browser** without adding anything.

The first node on a blank bot is its initial state, where a run starts, and its recovery state, where the bot goes when something fails ([Edges and failure](edges-and-failure.md#recovery-state)). It wears both badges: the play badge for initial, the amber arrow for recovery.

## Connect two nodes { #connect-nodes }

A node has one input, on its left edge, and an output port on its right edge for each thing it can return, labeled SUCCESS, FAILURE, TRUE or FALSE. Drag from an output port and drop on another node's input to draw an edge. The edge says: when this node returns that, go there next.

Select an edge to open its page in **Properties**, or right-click it and select **Delete Transition**. Where several edges cross, clicking again in the same spot selects the next one.

## Build a first bot { #build-a-first-bot }

A bot that tops up your wizard's health and mana from wisps, over and over. Stand your wizard near health or mana wisps and hook the game ([Accounts, clients and realms](../running/accounts-clients-and-realms.md)). Then:

1. Make a blank bot ([New Bot](new-bot-and-templates.md#new-bot)). It opens in Edit mode.
2. Select **Add State**, search for [Collect Wisps](../../reference/combat-and-healing.md#collect-wisps), and select **Add**. As the first node, it's the bot's initial and recovery state.
3. Select **Add State** again, and add [Sleep](../../reference/utility.md#sleep).
4. With Sleep selected, type 30 in **Duration (s)** on its page, so the bot waits 30 seconds between tries.
5. Drag from Collect Wisps' **SUCCESS** port to Sleep's input.
6. Drag from Sleep's **SUCCESS** port back to Collect Wisps' input. That's the loop.

Now the bot has one warning, on Collect Wisps: "Uncovered return value: 'FAILURE'". [Edges and failure](edges-and-failure.md#finish-the-first-bot) says why, and finishes the bot.

## Fill in a node's page { #node-page }

Select a node to open its page in **Properties**. At the top is the node's label, which you can type over, and a gray line saying what kind of node it is. **Show descriptions** and **Collapse all** act on the whole page. Under them are four cards:

- **Parameters**: the node's settings, one row each. The `?` beside a row shows what it does, and the reset button beside that puts the default back. A required row that's still empty shows a red asterisk, and the card's band says how many are left to fill. Some rows have buttons at the end: **Fill current** reads a connected player's zone or position, the play button takes every player to a zone, and the teleport button to a position. Right-click a position's boxes to copy or paste it.
- **Connections**: where each thing the node returns goes. A FAILURE or FALSE with no edge says "recovery · not drawn".
- **Notes**: anything you want to remember about this step. The bot doesn't read it.
- **Role**: **Initial state** and **Recovery state**. Checking one moves that role here from the node that held it.

A node with a problem wears a red or amber mark, and the badge under the bar above the canvas counts the bot's errors and warnings. Point at the badge to read them. An error grays out **Run bot** until it's fixed. A warning doesn't, but says something is probably wrong. **Run › Validate Bot** checks the bot without running it.

## Record from the game { #record }

Many nodes can fill in their own parameters while you play the step yourself. Their **Parameters** card has **Record** on its band, and a line under it saying what Record fills, such as "Not recorded. Record fills From Zone, Dungeon Zone, Dungeon Name, Sigil XYZ and Reset XYZ." on Enter Dungeon.

1. Select the node.
2. Select **Record**, or press Ctrl+R to record from the game window you were last in. With several players connected, choose which one.
3. Do what the line at the top of the card says, in that player's game: "Walk to sigil…", then through the loading screen.
4. When the card says "Done", check the rows it filled.

While it records, the button reads **Stop**. Select it to stop early, and nothing is kept. A node you haven't named yet is named from what you recorded, such as the dungeon's name. A label you typed is never replaced.

| Node | What you do while it records |
|---|---|
| [Enter Zone](../../reference/zone-entry.md#enter-zone) | Walk into the zone change. |
| [Enter Dungeon](../../reference/zone-entry.md#enter-dungeon) | Walk to the sigil and go in. |
| [Enter Gauntlet](../../reference/zone-entry.md#enter-gauntlet) | Walk to the gauntlet's sigil and go in. |
| [Enter Zone via Object](../../reference/zone-entry.md#enter-zone-via-object) | Walk to the object and use it. |
| [Open Skeleton Key Door](../../reference/zone-entry.md#open-skeleton-key-door) | Walk to the door, use your key, and walk through. |
| [Interact](../../reference/interaction.md#interact) | Walk up to the NPC or object and use it. |
| [Interact Menu](../../reference/interaction.md#interact-menu) | Walk up and use it. Set **Menu Path** yourself after. |
| [Teleport to Coordinates](../../reference/teleportation.md#teleport-to-coordinates) | Stand where the bot should go. Record takes the spot at once. |
| [Mark Location](../../reference/teleportation.md#mark-location) | Stand where the mark should be. |
| [Teleport to Marked Location](../../reference/teleportation.md#teleport-to-marked-location) | Stand at the mark. |
| [Handle Combat](../../reference/combat-and-healing.md#handle-combat) | Stand near the enemy. Record takes the closest one's name. |

!!! info "Speed-authoring"
    With speed-authoring on, keys 1 to 0 in a game window add that favorites-bar slot's node and start recording it, so you can record a dungeon without leaving the game. **Settings › Authoring** turns it on, and sets the other keys that help while you record: moving every player onto the one you're playing, turning on speed, auto-combat and auto-dialogue.

## Look inside the game { #look-inside-the-game }

Some parameters name something you can't read off the screen: an entity's ID, or a UI path. Two browsers find them. Each player's card in the right sidebar's **Players** tab opens them, for that player's game.

### Entity Browser { #entity-browser }

**Entities** opens the **Entity Browser**: everything near the player, closest first, with its **ID**, **Display Name**, distance and location. The filter matches either name.

- **Copy ID** copies the ID a node's parameter takes, such as a reagent's in [Collect Reagent](../../reference/interaction.md#collect-reagent).
- **Copy XYZ** copies its position, in the form a position row pastes.
- **Teleport** takes the player to it, to look at it.
- **Refresh** reads the list again. It doesn't update by itself.

The player's card has two shortcuts of its own: selecting its **XYZ** row copies the player's position, and its **Enemy** row the closest enemy's name.

### UI Tree Browser { #ui-tree-browser }

**UI Tree** opens the **UI Tree Browser**: every window and button in the player's game, as a tree, with whether each is **Visible**, **Enabled** or **Grayed**. Selecting an element draws a red box around it in the game.

- **Copy Path** copies its UI path, for [Click Path](../../reference/interaction.md#click-path).
- **Load Text** reads the **Text** of the selected element and everything under it, and **Copy Text** copies it, for Click Path's **Match Text**, where several elements share one path.
- **Hide invisible**, **Expand All** and **Collapse All** narrow the tree, and **Copy All** copies it as text.

## Find your way around a big bot { #big-bots }

- Ctrl+F searches the nodes by label or kind. Ctrl+0, or **Fit to Canvas**, shows the whole graph. The map in the bottom right corner shows where you are.
- **Regions** group nodes under a name and a color. Select two or more nodes, right-click and select **Create Region from 2 selected states**. A region can be collapsed into one node, from its page or with **Collapse all regions** in the bar above the canvas. Double-click a region to work on it alone, in Focus Mode. Alt+Left, or the cross on the bar at the canvas's top left, steps back out.
- F shows or hides failure edges ([Edges and failure](edges-and-failure.md#failure-edge-toggle)).
