# Subgraphs

A subgraph is a group of nodes saved in your library, which any bot can use as one node. When the subgraph changes, every bot that uses it follows. This page covers using one, making one, choosing what a bot can set on it, and what changing one means for the bots that use it.

## What a subgraph is { #what-a-subgraph-is }

On the canvas a subgraph is one **subgraph node**, drawn with a double line down each side. Inside, it's ordinary nodes, as in a bot. It has:

- **Inputs**: the values a bot sets on it, such as a zone. Everything else inside belongs to the subgraph.
- **Entries** and **Exits**: its ports. A bot's edges go into an entry and leave from an exit, as they would on a node.

Official subgraphs come with BartleBot, such as [Sync players](../../reference/subgraphs.md#sync-players), which every Official template uses to bring the team together. Your own are **Custom**. The bot library's **Subgraphs** folder lists both.

## Use a subgraph { #use-a-subgraph }

1. In Edit mode, select **Add State**.
2. Open the **Subgraphs** tab. **Category** and **Source** narrow the list.
3. Select a subgraph to read its inputs, its entries and exits, the nodes inside, and how many bots use it.
4. Select **Add**.

Select the subgraph node to open its page. It has a card for its inputs, a card for each of its input groups, and **Role**, where an entry port can be the bot's initial or recovery state. An input you leave alone uses the subgraph's own value, and the reset button beside a changed one goes back to it. A node the subgraph puts on its page whole keeps its **Record** button there.

Connect edges to its ports as you would to a node's. To look inside in Run mode, double-click it: the canvas shows its nodes, which you can't change from the bot.

**Detach from library** on its page, or **Detach from Library** on its right-click menu, turns it back into ordinary nodes in your bot, exactly as they are. They're yours from then on, and stop following the subgraph.

## When a subgraph changes { #when-a-subgraph-changes }

Each time you open a bot, and before every run, BartleBot builds each subgraph node from the library's current subgraph. When one changed, the bot is marked unsaved, the log says which, and the bot runs the new one. Saving the bot keeps the change in its file.

The bot's file also keeps a copy of what each subgraph node holds, so a bot never breaks when its library disagrees with it. A mark in the subgraph node's corner says when that copy runs instead:

| The mark's dot | What it means | What runs |
|---|---|---|
| none | the node follows the current subgraph | the current subgraph |
| warning color | the subgraph lost a port or a node this bot's edges or roles use | the bot's copy, until you rewire the bot off what went, or detach |
| error color | the subgraph is missing from the library, or its file is broken | the bot's copy |

The bot's validation warnings say which, and why. A subgraph that contains itself is an error, and the bot can't run until it's fixed.

## Make a subgraph { #make-a-subgraph }

1. In Edit mode, select the nodes, then right-click one and select **Create Region from 2 selected states**, or however many you selected.
2. Right-click the region and select **Save Region as Reusable Subgraph…**.
3. Give it a **Name**, a **Description** saying when to use it, and a **Category**. Select **Save**.

The region becomes a subgraph node, saved to your library as version 1. Its entries and exits are the edges that crossed the region's border. It has no inputs yet: everything inside is the subgraph's own until you choose what a bot sets ([Choose what a bot can set](#inputs-and-ports)).

## Edit a subgraph { #edit-a-subgraph }

Double-click a subgraph node in Edit mode, or right-click it and select **Open Definition**, or double-click its card in the library's **Subgraphs** folder. The subgraph opens on the canvas in place of your bot, with a bar at its top left naming where you are. Edit it as you would a bot. On that bar:

- **Save** writes the subgraph to the library. Every bot that uses it follows from its next open or run.
- **Save as…** saves a copy as a new subgraph, and goes on editing the copy.
- **Revert**, on a changed Official subgraph, goes back to the Official one.
- The cross, or Alt+Left, goes back to your bot.

A subgraph with an error isn't saved. Saving an Official subgraph asks first, since it makes a modified copy that every bot that uses it follows in place of the Official one's updates. That copy is marked **Official (modified)**, and **Official updated** once an app update has changed the Official one under it. **Revert** puts the Official one back, for every bot.

A subgraph can't use variables. A subgraph whose nodes read or set one isn't saved.

## Choose what a bot can set { #inputs-and-ports }

With a subgraph open and nothing selected, **Properties** shows its **Description**, its **Ports** and its **Inputs**.

- **An input**: select a node inside, and point at a parameter's row. Select its **Link** button, and link it to a new input or one the subgraph has. The row then shows the input's name, and its value is the input's default. Pointing at the name turns it into **Unlink**.
- **A whole node**: **Promote to input group**, on the node's **Parameters** band, puts every one of its parameters on the subgraph node, under one card, with its **Record** button.
- **Input groups** gather inputs under a card of their own. In the **Inputs** list, drag an input by its grip, or press Alt+Up and Alt+Down, to move it into or out of a group, or to reorder.
- **Entries and exits**: right-click an empty spot and select **Add Entry** or **Add Exit**. Dragging an edge from a port into empty space offers a new exit. An entry needs exactly one edge into the subgraph, and every exit needs one leading into it.

An input linked to nothing is an error, and the subgraph isn't saved until you link it or delete it.

## What a change warns about { #signature-changes }

Saving a subgraph that other bots or subgraphs use asks first, naming each of them, when the change could break one:

| The change | Why a bot that uses it might break |
|---|---|
| an input removed, or a node no longer put on the page whole | a value the bot set stops applying |
| an input renamed: its name, not its label | the bot's value is kept under the old name |
| an input added with no default | a subgraph node placed before has no value for it |
| an entry or exit removed | the bot's edges to it have nowhere to go |
| an entry or exit renamed | an edge drawn to one port now leaves another |

**Save** is the default, and **Cancel** writes nothing. Nothing else asks: a new label, a moved node, a parameter inside, a new input with a default, or a new port. A bot that can't take the change runs its own copy, with the warning mark, until you fix it.

## Rename, delete or share a subgraph { #manage-subgraphs }

Right-click a subgraph's card in the library's **Subgraphs** folder:

- **Rename…** changes your own subgraph's name and description. A bot's subgraph node keeps the label it had.
- **Delete Subgraph…** removes your own, and names the bots that use it first. They keep running their copy. **Revert to Official…**, on a changed Official one, puts the Official one back. An Official subgraph can't be renamed or deleted.
- **Export…** saves it as a share file ([Sharing](sharing.md)).
