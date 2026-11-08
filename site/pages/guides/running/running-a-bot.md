# Running a bot

This page covers a run from start to finish: what BartleBot checks before the first step, what you can watch while it runs, how to pause or stop it, and what makes a run end by itself.

## Get ready

1. Launch your games and log in. [Accounts, clients and realms](accounts-clients-and-realms.md) has how.
2. Open the bot from [the library](library.md) and read its description. Take your wizards to the place it names.
3. Check the bot's **Options** tab. A run reads every option when it starts, so changes made during a run wait for the next one. [Options and Settings](options-and-settings.md) has what each row means.

## Start the run

Select **Run bot**, the play button in the bot canvas's toolbar, or press F5 (**Run › Run / Pause**). It's grayed while no game is open, while the bot is in Edit mode, or while the bot has errors to fix. Its tooltip says which.

Before the first step, BartleBot checks the run. Some checks refuse to start and say what to fix. [Troubleshooting](../../troubleshooting.md) has each one:

- **Restricted Zone**: the bot names a PvP zone or Beastmoon. [PvP](pvp.md) has why.
- **Player Realms**: the bot's [Player Realms](../../reference/options.md#player-realms) option keeps players on separate realms, but the bot holds a step that brings them together.
- **Players in a Fight**: a player is in a fight. Finish or flee it first.
- **Run Bot**: a player has an action on a key BartleBot can't press.

Others ask, and you choose:

- **Safety Check** lists what the run could cost you. [Safety](safety.md) has what each warning means.
- **Combat Strategy Problems** names a combat setup that can't work as chosen. **Go back** is the default.
- **Clients Cannot Be Recovered** names the games BartleBot couldn't bring back after a crash. [When something goes wrong in a run](when-something-goes-wrong.md#what-recovery-needs) has why.

Then BartleBot gets the players ready. It takes every camera out of freecam, applies the decks the combat setup names, and, with **Automatically group players** on, puts every player in one group. Then the first step runs.

## Watch the run

- **The bot canvas** highlights the step the bot is on.
- **The Log tab**, in the right sidebar, shows what happened. It shows warnings and errors only, at first. Set **Min level** to INFO to see each step, each edge the bot takes, and the run summary at the end. The log file keeps every line, whatever the tab shows.
- **The Drops tab** lists what each player picked up. **Keep list only** shows just the drops the bot's quicksell keep list keeps, and **Export CSV** saves a player's table.
- **The Players tab** shows each player's zone and position.

While a run goes on, the **Options** and **Combat** tabs are locked. Your clicks and keys still reach the game, and can throw the bot off, so leave the game windows alone unless you mean to step in. BartleBot keeps Windows from going to sleep until the run ends.

## Pause or stop

- **Pause**: press F5 again, or select the same button. The bot finishes the step it's on, then waits. Press it again to carry on.
- **Stop**: select **Stop bot**, or press Shift+F5 (**Run › Stop Bot**). The step in progress gets about a second to finish, then the run ends.

BartleBot pauses the bot by itself while it brings a crashed game back, while it relogs your players, and while it fights a fight no step started. The bot carries on when that's done. [When something goes wrong in a run](when-something-goes-wrong.md) has what happens in each.

## How long a run goes on

A bot runs until you stop it. Most bots loop: they run their steps, then start again from the top. A run also ends by itself when:

- **The bot reaches a step with no way on.** It stops there, and the log says so.
- **A step its author marked as a stop is reached.** A **Bot Stopped** message names the step and why.
- **The same step keeps failing.** A step that fails too many times in a row stops the bot, and the node browser says how many times each kind of step may.
- **BartleBot can't put things right.** A game it can't bring back, the same problem sending the bot back to its recovery state again and again, or the players fleeing fight after fight. Each one shows a **Bot Stopped** message. [Troubleshooting](../../troubleshooting.md#the-bot-stopped-by-itself) has each one.
- **A player reaches a PvP zone.** [PvP](pvp.md) has what happens then.

A long run slows the games down. BartleBot logs every player out and back in every few hours to clear it, which **Relog interval** in Settings › Bot sets. [When something goes wrong in a run](when-something-goes-wrong.md#relogging) has more.

## Read the run summary

When a run ends, the log ends with a summary. Set the Log tab's **Min level** to INFO to see it:

```text
=== run finished in 1h 02m 15s ===
Passes: 14   States executed: 212
Results: FAILURE=3, SUCCESS=209
Drops: 37 item(s) over 21 entries
```

**Passes** is how many times the bot went round its loop, and **States executed** how many steps it ran. **Results** counts how each step ended. A few FAILURE results are normal, since a bot takes another path when a step fails. **Drops** counts what the bot picked up.

To keep the log, select **Export** in the Log tab. It saves the lines the tab shows. The session's whole log is in [the logs folder](../../troubleshooting.md#where-the-logs-are).
