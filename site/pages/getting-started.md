# Getting started

This page takes you from installing BartleBot to the end of your first run, in the order you meet each step.

## Install BartleBot

Follow [Installing BartleBot](install.md). When the installer finishes, BartleBot opens and starts its setup.

## Answer setup's questions

Setup asks one question per page, and each answer is a setting you can change later. To go through it again, open **File › Set up BartleBot…**.

The first page asks **How much do you want to set up now?**

- **Quick setup** asks only what your first run needs. Everything else starts at its default.
- **Custom setup** asks the same questions, then lets you choose a few more options yourself.

Quick setup is selected. Choose it unless you already know you want to change the options on the Custom page below.

Setup shows only the pages it needs, so you may not see all of these:

| Page | What it asks | Change it later in |
| --- | --- | --- |
| **Where is Wizard101 installed?** | The game's folder. Shown only when BartleBot can't find it. If you start Wizard101 as usual while this page is open, BartleBot finds it. | Settings › General |
| **Add your Wizard101 accounts** | Your logins, which BartleBot uses to launch the game and to bring it back after a crash or disconnect. Passwords are stored in Windows Credential Manager. Without an account, you start the game yourself, and bots can't recover from a crash. | Launch › Manage Wizard101 Accounts… |
| **Which accounts do you play together?** | Shown with two or more accounts. The checked accounts become a launch profile, in order, so the first is Player 1. | Settings › Game |
| **How should your team fight bosses?** | Shown with two or more accounts. **One hitter, the others support**, or **Everyone attacks**. Solo fights use Default Solo Duel. | Settings › Combat, or one bot's Combat tab |
| **What should bots sell?** | What [quicksell](reference/options.md#quicksell) sells when a backpack fills: [pets](reference/options.md#sell-pets), [Socket Wrenches](reference/options.md#sell-socket-wrenches), [Skeleton Keys](reference/options.md#sell-skeleton-keys), and what to always keep. Quicksell starts off. | Settings › Bot, or one bot's Options tab |
| **Set your favorite realm and Safety Delay** | Your [Favorite Realm](reference/options.md#favorite-realm), where [Change Realm](reference/utility.md#change-realm) moves every player, and the [Safety Delay](reference/options.md#safety-delay), how long the game server gets to answer an action: 3 to 5 seconds in North America, 5 to 10 elsewhere. | Settings › Bot, or one bot's Options tab |
| **Choose a few more options** | Custom setup only. **Extract game art**, **Automatically group players**, which needs every account on the others' friends lists, and [**Refill potions**](reference/options.md#refill-potions), which spends gold. | Settings › General and Settings › Bot |
| **Extracting game art…** | Nothing to answer. BartleBot copies spell card pictures and icons from your game files, about 35 MB. Closing setup stops it. | Settings › General › Game art |

!!! warning "Before you turn quicksell on"

    Quicksell sells every drop your settings don't keep. It never sells an item you've locked in the game, so lock what you want to keep first: turn on **Backpack Item Lock** in the game's **Options › Gameplay Options 2**.

## Find your way around the window

BartleBot's window has four areas. Hide or show each one from the **View** menu, or with the buttons at the top right of the window.

- **The game view**, in the middle, holds your game windows. Before any is open, it shows **Quick Launch**.
- **The left sidebar**, on its left, holds the open bot's **Options** and **Combat** tabs.
- **The right sidebar**, on its right, holds **Players**, **Drops** and **Log**.
- **The bot canvas**, along the bottom, shows the bot library, and then the bot you open.

## Launch the game

Quick Launch lists your accounts on the left and your launch profiles on the right. Select one and select **Launch**, or double-click its card. The **Launch** menu has the same choices.

Each game opens inside BartleBot's window. BartleBot logs in and presses Play on the character you played last. To pick the character yourself each time, turn off **Automatically select a character** in Settings › Game.

The game is still yours to play: click into a window and play as you normally would. While a bot runs, your clicks and keys reach the game too, and can throw the bot off.

## Choose a bot

The bot canvas shows the bot library while no bot is open. **Recent** lists the bots you opened last, and **All** lists every bot. Narrow the list with the tag filters, or by where a bot comes from: **Official**, or your own. The search box finds a bot by its name, its author, or a drop.

Official bots come with BartleBot, are tested in game, and are updated with the app. You can run one and set its options as it is. To change its steps, open it and select the pencil in the bot canvas's toolbar: BartleBot asks to **Make my own copy**, which keeps your options and combat setup for it.

Select a bot's card to open it. To get back to the library, use **File › Close Bot** or **File › Browse Bot Library…**.

Read the bot's description before you run it. If it names a place to start, take your wizards there first.

## Check the bot's options

The **Options** tab in the left sidebar shows every option the bot uses, in folding cards: **This dungeon**, **Team**, **Quicksell** and **General**. [Bot options](reference/options.md) in the Reference describes each option a node adds. Each row shows the value the run will use. When that value comes from your app settings, the row says so.

Change any row to set your own value for this bot. Your values are kept in your settings, and the bot stays as it is. A row you've changed is marked with a colored bar, and its revert button puts it back.

Options can't be changed while the bot runs, since a run reads them when it starts.

## Run the bot

Select **Run bot**, the play button in the bot canvas's toolbar, or press **F5** (**Run › Run / Pause**). It's grayed until the bot is ready and a game is open.

Before the first step, BartleBot may stop to ask about something:

- **Safety Check** lists what this bot will do that could cost you something, such as items or gold. Read it, then select **Run Bot** or cancel.
- **Clients Cannot Be Recovered** names the games BartleBot couldn't bring back after a crash, usually because their account isn't saved. You can run anyway.
- **Players in a Fight** and **Run Bot** refuse to start, and say what to fix first. [Troubleshooting](troubleshooting.md) has the second one.

While it runs, the bot canvas shows the step the bot is on, and the **Log** tab says what each step did.

## Pause or stop

- **Pause**: press **F5** again, or select the same button. The bot waits after the step it's on. Press it again to carry on.
- **Stop**: select **Stop bot** in the toolbar, or press **Shift+F5** (**Run › Stop Bot**).

## Read the run summary

When a run ends, because you stopped it or because the bot did, the **Log** tab ends with a summary of the run:

```text
=== run finished in 1h 02m 15s ===
Passes: 14   States executed: 212
Results: FAILURE=3, SUCCESS=209
Drops: 37 item(s) over 21 entries
```

- **Passes** is how many times the bot went round its loop, and **States executed** how many steps it ran.
- **Results** counts how each step ended. A few FAILURE results are normal, since a bot takes another path when a step fails.
- **Drops** counts what the bot picked up. The **Drops** tab lists them.

To keep the log, select **Export** in the Log tab. If the run didn't go the way it should have, see [Troubleshooting](troubleshooting.md).
