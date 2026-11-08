# Accounts, clients and realms

BartleBot can run one game or several at once, each in its own tile of the game view. This page covers saving your accounts, launching and connecting games, running a team of players, and keeping them on one realm or apart.

## Add your accounts

An account is one Wizard101 login. BartleBot uses it to launch the game, log in, and launch it again after a crash.

1. Open **Launch › Manage Wizard101 Accounts…**, or **File › Settings…** and the **Game** tab.
2. Select **Add Wizard101 account**, and enter the username and password. An **Alias** and a **Color** are optional. The color marks the account's card, and the frame around its game window while it has focus.
3. Select **OK**.

Passwords are stored in Windows Credential Manager. To change one, edit the account and type the new password. Leave it blank to keep the one saved.

## Launch the game

Before any game is open, the game view shows Quick Launch, with your accounts on the left and your launch profiles on the right. Select one and select **Launch**, or double-click its card. The **Launch** menu has the same choices, and launches more games once some are open.

BartleBot logs in and presses Play on the character you played last. To pick the character yourself, turn off **Automatically select a character** in Settings › Game. Recovery after a crash uses the same setting, so with it off a relaunched game waits at character select for you.

A game you start from Wizard101's own launcher connects too: BartleBot finds every open game window and hooks it. To be brought back after a crash, its account has to be launched from BartleBot at least once.

## Run several players together

A launch profile starts several accounts at once, in the order you give them, so the first is Player 1. Add one with **Add launch profile** in Settings › Game. Setup's **Which accounts do you play together?** page makes the first one.

- **Player numbers** follow the order of the cards in the **Players** tab. Drag a card to reorder the players while no bot runs.
- **Grouping**: with **Automatically group players** on, in Settings › Bot, a run puts every player in one group before its first step, and again after a crash. Every account must be on the others' friends lists.
- **Teleporting to each other** goes through the friends list. BartleBot turns off the game's quick friend teleport in the games it launches, since it closes the list on a click. A game you started yourself keeps your own setting, and the **Safety Check** says so before a run. Turn **Dblclick Go To Friend** off in the game's **Options › Gameplay Options 4**.
- **Unhook** and **Rehook**, on a player's card, take a game out of BartleBot's control and give it back, leaving the window open.

## Fit the game windows

Each game sits in a tile of the game view, and the tiles share its space: one fills it, two sit side by side, and three or four share a grid. The size each game launches at is **Default game resolution**, in Settings › Game.

BartleBot doesn't launch a game that wouldn't fit. A profile that doesn't fit is grayed, with the size it needs and the space there is. Lower the resolution, launch fewer games, or drag a sidebar's edge to give the game view more room.

## Choose whether players share a realm

Players on different realms are in different fights and different dungeons. The **Player Realms** option, in the bot's **Options** tab, says how a bot arranges them:

- **Together**, the usual choice: every player on one realm. A player found on another realm is brought to the others before a fight or a dungeon.
- **Separate**: each player keeps its own realm, and BartleBot never moves one onto another's. Reagent collectors use it, so players don't compete for the same spawns.

A bot with a step that brings everyone onto one realm, such as teleporting to a friend or opening a Skeleton Key door, can't be Separate, and the row says why. With one player, there's nothing to arrange and both choices are grayed.

**Favorite Realm**, in Settings › Bot, is where Change Realm and Sync in Zone send players. A game BartleBot launches also gets the game's Remember Realm option, so a relogged player comes back to the realm it left.

## Players who aren't yours

A dungeon with a player in it who isn't on your team fails to enter, and the bot takes its recovery path. Another player in a fight breaks the bot's combat.

Team-Up is the exception. With **Team-Up** on, a bot joins the game's Team-Up queue, which can take up to an hour and brings in players who aren't yours. **Minimum Team Size** is how many the queue waits for. With more than one of your players, each queues alone and may land in a different copy of the dungeon. The bot brings them together when they arrive, and stops if the game refuses. The **Safety Check** says so before the run.
