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
