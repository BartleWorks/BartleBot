# Troubleshooting

Find what you see below, and try what it says. If that doesn't fix it, [open an issue](#opening-an-issue) with your logs.

## Windows won't let BartleBot install or start

**"Windows protected your PC"** when you run the installer is SmartScreen. Select **More info**, then **Run anyway**. [Installing BartleBot](install.md#smartscreen-windows-protected-your-pc) shows each step.

If Windows blocks BartleBot every time it starts, **Smart App Control** is on. BartleBot can only run with it turned off. [Installing BartleBot](install.md#smart-app-control) shows where.

If your antivirus removes BartleBot, [open an issue](https://github.com/BartleWorks/BartleBot/issues) that names the antivirus. Don't turn the antivirus off for it.

## BartleBot can't press every action this run needs

A bot won't start, and the **Run Bot** message names a player and an action, such as "Player 2: Interact is on Ctrl+X."

BartleBot presses each action by the key your account has it on, and it can't press a key that needs Ctrl, Shift or Alt.

1. In the game, open the **Keybinding** tab for the player the message names.
2. Give each action it lists a key without Ctrl, Shift or Alt that nothing else uses. Either of an action's two keys works.
3. Run the bot again.

## A game window stops answering

The game freezes, or Windows says it is **Not Responding**.

1. Wait a minute. The game can hold still while it loads a zone or ends a long fight, and it comes back by itself.
2. If it doesn't, close that game window. While a bot runs and the account is saved in BartleBot, BartleBot launches it again and the bot carries on.
3. If no bot is running, launch the account again from Quick Launch.

A game slows down the longer it stays logged in, and logging out clears it. If a long run keeps freezing, stop the bot, close its game windows, and launch them again.

If BartleBot itself freezes with the game, [open an issue](#opening-an-issue).

## "This bot cannot be run" { #restricted-zone }

**Restricted Zone** says "This bot cannot be run. It sends a client to a zone BartleBot does not automate", and lists each step that names a PvP zone or Beastmoon.

BartleBot doesn't automate PvP, and nothing turns that off. To run the bot, make your own copy and take out the steps the message lists. [PvP](guides/running/pvp.md) has which zones are off limits.

## "The bot was stopped: a client entered…"

A player reached a PvP zone, or a PvP fight, during a run. BartleBot stopped the bot and unhooked every game.

1. Move every player out of the zone in the game.
2. Select **Rehook All** in the **Players** tab.
3. Start the bot again. If the bot itself took a player there, [open an issue](#opening-an-issue) with the log.

## "This bot cannot be Separate"

The **Player Realms** message says a step in the bot needs every player on one realm, and names it. The bot's **Player Realms** option is Separate.

Set **Player Realms** back to **Together** in the bot's **Options** tab, then run it again.

## "Player 1 is in a fight"

**Players in a Fight** names each player in a fight when you start the bot. Before the first step, BartleBot applies decks and forms the group, and neither works mid-fight.

Finish the fight, or flee it, then start the bot again.

## "…clients cannot be recovered automatically if they crash" { #clients-cannot-be-recovered }

**Clients Cannot Be Recovered** names each game BartleBot couldn't launch again after a crash, with why:

- **"not linked to a configured account"**: the game was started outside BartleBot, and its account has never been launched from BartleBot. Save the account in **Launch › Manage Wizard101 Accounts…**, close the game, and launch it from Quick Launch once.
- **"no password saved"**: edit the account in **Launch › Manage Wizard101 Accounts…** and save its password.

Select **Yes** to run anyway. A crash in one of those games then stops the run, or leaves the bot paused until you're back.

During a run, the same gap shows as **Client Recovery Failed**, "No stored password for account", and the bot stops. Save the password, then start the bot again.

## "…waiting at character select"

A game BartleBot launched again after a crash waits at character select, because **Automatically select a character** is off. Log in to that character, and the run carries on. To have BartleBot do it, turn the setting on in Settings › Game.

## The bot stopped by itself { #the-bot-stopped-by-itself }

A **Bot Stopped** message says why. The most common ones:

- **"The bot stopped at", a step, and "which returned"**: the bot's author marked that result as a stop. The rest of the message says why, such as a player in the wrong world. Do what it says, then run the bot again.
- **"cannot be closed and relaunched automatically"**: a game froze or crashed, and BartleBot couldn't launch it again. See [Clients Cannot Be Recovered](#clients-cannot-be-recovered).
- **"The bot went back to its recovery state", a number, "times in a row for the same reason"**: the bot keeps failing at the same place, and starting again doesn't help. The message names the steps and the reason. Look at those steps in the game: a moved NPC, a closed door, or a player in the wrong place. A higher Safety Delay helps on a slow connection. If an Official bot does this, [open an issue](#opening-an-issue).
- **"The players fled", a number, "fights within 10 minutes"**: see [the next entry](#the-players-fled-fights).

With no message, the log names the last step. A step that fails too many times in a row stops the bot with "consecutive ticks without advancing", and a step with nowhere to go next with "Reached terminal state".

## "The players fled 3 fights within 10 minutes" { #the-players-fled-fights }

BartleBot flees a fight when none of your players can still deal damage, and three fled fights stop the bot.

1. Check each player's deck holds damage cards they can cast at their level.
2. Check they have mana. In a bot that uses potions, **Refill potions**, in Settings › Bot, buys more when they run out.
3. Check the bot's **Combat** tab, and the strategy each player fights with.

## Quicksell didn't sell { #quicksell-didnt-sell }

The Quicksell step ends in FAILURE, with a log line such as "FAILURE: quicksell open button did not appear", and the backpack stays full.

1. Close whatever is open in that player's game window, such as a dialog or a shop, so the backpack can open.
2. Raise **Safety Delay** if the connection is slow.
3. If it keeps happening, [open an issue](#opening-an-issue) with the log.

If quicksell sold nothing because nothing was full, that's expected: it sells only when a backpack or the jewel pouch is full. [Quicksell and keep lists](guides/running/quicksell.md) has the rest.

## Grouping or teleporting to a friend fails

The log says a player couldn't find a friend, or the group didn't form.

1. Check every account is on the others' friends lists, and the wizard a bot teleports to is online.
2. Turn off **Dblclick Go To Friend** in each game's **Options › Gameplay Options 4**. BartleBot turns it off only in the games it launches.
3. Check the wizard's name in **Friend Name**, in Settings › Bot or the bot's **Options** tab.

## A launch profile is grayed out

Quick Launch grays a profile whose games wouldn't fit in the game view, and says the size it needs and the space there is.

- Lower **Default game resolution** in Settings › Game, or the profile's own resolution.
- Drag a sidebar's edge, or hide one from the **View** menu, to give the game view more room.
- Launch fewer games at once.

## Where the logs are

BartleBot writes a log of each session to:

```text
%APPDATA%\Bartleby\BartleBot\logs
```

Paste that into the address bar of File Explorer to open it. Each file is named for the date and time BartleBot started, such as `bartlebot_2026-11-07_21-49-03.log`. BartleBot keeps the 10 most recent.

The **Log** tab in the right sidebar shows the same session as it happens. Its **Export** button saves what it shows to a file.

## Opening an issue

[Open an issue](https://github.com/BartleWorks/BartleBot/issues/new) on GitHub. Issues are public, so anyone can read what you attach.

Include:

- **What you did**, what you expected, and what happened instead.
- **The bot**, by its name in the library, and whether it's Official or your own.
- **The log** of the session where it happened, from [the logs folder](#where-the-logs-are). Drag the file into the issue. The log calls your players P1, P2 and so on, and names no wizard or account.
- **The version**, shown under BartleBot's name on Quick Launch.
- **A screenshot**, if it shows the problem. Turn on **Streamer mode** in Settings › General first, which hides your wizards' names, and crop out chat and anything else that identifies your account.
