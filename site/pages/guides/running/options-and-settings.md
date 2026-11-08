# Options and Settings

A bot's options are the choices it leaves to you, such as Safety Delay or what quicksell keeps. This page explains where each value comes from, how to set your own for one bot or for every bot, and which Settings tab holds what.

## Where an option's value comes from

Every option row names where its value comes from, at the right of the row. The first of these that has a value is the one the run uses:

| The row says | The value is | Set it in |
| --- | --- | --- |
| **Your value** | yours, for this bot only | this bot's **Options** tab |
| **Bot default** | what the bot's author chose for this bot | the bot itself, by its author |
| **App settings** | yours, for every bot that leaves it to you | **File › Settings…** |
| **Default** | BartleBot's own, when nothing else answers | nowhere |

So a value you set for one bot wins over everything else, and an author's choice wins over your app setting. Most bots leave Safety Delay, Favorite Realm, Friend Name, Refill Potions and quicksell's switches to your app settings, so one change in Settings reaches all of them.

!!! info "Why your values live apart from the bot"

    BartleBot keeps your values in your settings, on this computer, and the bot file never changes. That's what lets you set options on an Official bot without a copy, and what lets you share a bot without sharing your Friend Name or your realm.

## Set your own value for one bot

1. Open the bot, and select the **Options** tab in the left sidebar.
2. Change the row. It now says **Your value**, and a colored bar marks it.
3. To go back, select the row's revert button, beside the bar. Its tooltip says the value it goes back to, and where that comes from.

The options sit in folding cards, always in this order:

| Card | Holds |
| --- | --- |
| **This dungeon** | the options the bot's author added, and Challenge Mode |
| **Team** | Player Realms, Team-Up and Minimum Team Size, Level Scaling Players, and Friend Name |
| **Quicksell** | quicksell's switch, the sell switches and the keep list. [Quicksell and keep lists](quicksell.md) has them |
| **General** | Safety Delay, Refill Potions and Favorite Realm |

A bot shows only the options its steps use. **Show descriptions** above the cards unfolds what each row does, and **Open app settings** opens Settings › Bot.

Options can't be changed while the bot runs, since a run reads them when it starts. A copy you make of an Official bot keeps the values you set for it.

## Set a value for every bot

Open **File › Settings…** and select **Bot**. Its rows are the app settings the bots fall back to:

- **Default Safety Delay**: how long the game server gets to answer before an action counts as failed. Raise it on a slow connection: 3 to 5 seconds in North America, 5 to 10 elsewhere.
- **Relog interval**: how often every player logs out and back in during a run. **Off** turns it off.
- **Automatically group players**: puts every player in one group when a run starts. Every account must be on the others' friends lists.
- **Friend Name**: the wizard a bot teleports to when it doesn't name one of your players.
- **Refill potions**: buys potions when a player runs out, which costs gold.
- **Favorite Realm**: where Change Realm and Sync in Zone send players.
- **Quicksell**, the sell switches, **Always keep** and **Quicksell reminder**: [Quicksell and keep lists](quicksell.md) has them.
- **Set archmastery with decks**: sets a deck's archmastery school when it's applied.

An app setting changes every bot that leaves the option to it, from the next run. A bot whose author chose a value, or where you set your own, keeps that one.

## Which Settings tab holds what

| Tab | Holds |
| --- | --- |
| **General** | the game folder, updates, the Log tab's lines, the window's layout, streamer mode, auto-save, and game art |
| **Bot** | the defaults every bot falls back to, above |
| **Combat** | the Default Duel Strategies for solo and team fights, and recording combat decisions to the log |
| **Canvas** and **Authoring** | the bot canvas and the tools for making bots |
| **Theme** | the colors |
| **Game** | your accounts and launch profiles, the game's resolution, **Automatically select a character**, and **Disable quick friend teleport** |

Setup asks for the most important of these the first time BartleBot opens. To go through it again, open **File › Set up BartleBot…**.
