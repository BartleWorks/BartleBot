# New Bot and templates

Every bot you make starts in the **New Bot** window, from a blank canvas or from a template. This page covers both, filling in a template's setup steps, taking a template's later improvements, and making templates of your own.

## Start a new bot { #new-bot }

1. Select **File › New Bot**, or press Ctrl+N.
2. Under **Start from**, pick **Blank bot** or a template. Each template's card says what the bot does, and its tooltip has the rest.
3. Select **Next**.
4. Type a **Display name**. The **File name** follows it as you type, and the line under it says the name the file will get.
5. Add a **Description**, **Authors** and **Tags** if you like, and pick the **Library folder** the bot goes in.
6. Select **Create**.

BartleBot saves the new bot at once and opens it in Edit mode. A bot made from a template starts with the template's nodes and its tags. **Authors** starts blank, since the template's authors didn't write your bot.

A blank bot has no nodes, so it can't run yet: its error says "This bot has no nodes yet: add one to begin". [The canvas](the-canvas.md#build-a-first-bot) walks through building one.

## The Official templates { #official-templates }

| Template | What a bot made from it does |
|---|---|
| **Boss Farmer** | Farms one boss: enters its dungeon, defeats the boss, exits, and repeats. |
| **Dungeon Farmer** | Farms a whole dungeon: marks the entrance, enters, runs the nodes you add, recalls out, and repeats. |
| **Gauntlet Farmer** | Farms a housing gauntlet: ports to an unhooked friend on its sigil, runs the nodes you add, and repeats, going back to The Commons for potions when they run low. |
| **Mob Farmer** | Farms one kind of mob in the open world: fights it, waits somewhere safe, and repeats. |
| **Reagent Collector** | Collects a reagent from every spawn point in a zone, changing realm after each pass so the spawns are fresh. |
| **Spellement Farmer** | Farms a boss for spellements while level-scaled: applies level scaling to selected players when needed, enters the boss's dungeon, defeats the boss, exits, and repeats. |

Every Official template is tested in game. Each one leaves only what is particular to your farm for you to fill in: the zone, the entrance, the boss, its drops.

## Fill in the setup steps { #setup-steps }

A bot made from a template has a **Setup** tab in the right sidebar. It lists what the template asks you to fill in, and says how many steps are done.

1. Select a step's card. The canvas selects the node the step is about, and its page opens in **Properties**.
2. Fill in the row the step names. A row still to fill says "Fill in:" and the step's words. Many steps say **Record**: [Record from the game](the-canvas.md#record) has how.
3. Go on to the next card. A finished step's card says "Done".

A step that imports drops is the one exception: its card lists the bot's fights, each with an **Import…** that reads the fight's drops from its Wizard101 Central Wiki page.

Until a step is done, its node has an error, "Setup:" followed by the step's words, and **Run bot** stays grayed. The bot's card in the library says how many setup steps are left. The **Setup** tab stays until you close the bot. Opened again with every step done, the bot has none, and the template's name in the bar above the canvas brings it back.

## Take a template's update { #template-updates }

When a template releases a newer version than the one your bot was made from, BartleBot offers you a review. The bar above the canvas names the versions, as "Boss Farmer v1 → v2", the bot's card in the library says "Template update available", and the **Setup** tab opens on the review, above the setup steps.

The review lists each newer version with its note, and under it each change that version makes. Select a change's card to see the node it's about. Then choose:

- **Apply** makes the change in your bot.
- **Use template's** or **Keep mine**, on a change to something you changed too. The card says "You changed this too."
- A change that can't be made in your bot, because it needs a node or a connection you removed, says why after "Can't apply:", and offers nothing.

A change you've already made yourself isn't listed, and neither is one to a node you deleted. Each **Apply** is one step you can undo, and nothing is saved until you save the bot.

When you're done, select **Finish review**. Your bot is then reviewed against the newest version: a change you skipped isn't offered again, and the newer version's setup steps take the old ones' place. **Later** puts the review away until you next open the bot.

A review never touches the nodes you added, where nodes sit on the canvas, the bot's name, description, tags or authors, its possible drops, or anything a setup step asked you to fill in.

!!! info "Why a template is copied, not linked"
    A bot made from a template gets its own copy of the template's nodes, so you can change anything in it. That's why an improvement reaches your bot only when you review it: a later version can't know what you changed. A subgraph works the other way round. It stays linked, so every bot that uses one follows its changes ([Subgraphs](subgraphs.md)).

An Official bot gets its updates with BartleBot's own, and has no review. Your copy of one keeps its template, so the template's later versions are offered to your copy as a review.

## Make a template of your own { #your-own-templates }

**File › Templates…** lists every template, Official and your own, as cards, with a **Source** filter. Each card's last line gives its latest version, how many of your bots were made from it, and which template it was copied from.

| To | Select | On |
|---|---|---|
| start a blank template | **New** | |
| change a template's nodes and setup steps | **Edit**, or double-click its card | your own templates |
| make one of your own from another | **Duplicate** | any template |
| rename one | **Rename** | your own templates |
| remove one, every version | **Delete** | your own templates |

An Official template can't be changed. **Duplicate** it to have a copy of your own, made from its latest version. Your copy is a template of its own from then on, and the Official template's later versions don't reach it. No two templates can share a name. Deleting a template leaves the bots made from it running, without setup steps or reviews.

### Edit the draft and release it { #release }

**Edit** opens the template's draft on the canvas, like a bot, with **Save draft** and **Release…** in the bar at the top. A draft is only yours. **New Bot** offers a template's released versions, never its draft.

To give the draft setup steps, open its **Setup** tab:

1. Select **Add step**, and type what the user is asked to do, as an instruction.
2. Choose what the step fills in: select **+ Add target…** on the step's card, to pick a parameter, a subgraph input or the drop list. Or, on a node's page, point at a row and select its **Add to setup step…** button. A row in a step wears a "Setup step" chip, and selecting the chip takes it out again.
3. Leave each of those blank. A bot made from the template fills them in, and a filled one stops the release.

To release, select **Release…**. It releases the draft as saved, so it asks you to save any changes first. Then it asks what changed, in one line. That note heads the version in every review. A draft with an error, a filled-in setup row, or a step with no words or no rows isn't released, and the message lists what to fix. A draft that replaces a node the last version had, rather than changing it, asks first: a bot takes that as a removal and an addition, and loses any edge its user wired to the old node.

A released version never changes. The next release is a new version, which the bots made from your template are offered as a review.

Templates can't be shared in a share file yet. **File › Export…** on a draft says so.
