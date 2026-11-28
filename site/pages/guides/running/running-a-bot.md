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
- **No Walking Map**: the bot names a zone BartleBot can't walk in yet.
- **Player Realms**: the bot's [Player Realms](../../reference/options.md#player-realms) option keeps players on separate realms, but the bot holds a step that brings them together.
- **Players in a Fight**: a player is in a fight. Finish or flee it first.
- **Run Bot**: a player has an action on a key BartleBot can't press.

Others ask, and you choose:

- **Safety Check** lists what the run could cost you. [Safety](safety.md) has what each warning means.
- **Combat Strategy Problems** names a combat setup that can't work as chosen. **Go back** is the default.
- **Clients Cannot Be Recovered** names the games BartleBot couldn't bring back after a crash. [When something goes wrong in a run](when-something-goes-wrong.md#what-recovery-needs) has why.

Then BartleBot gets the players ready. It takes every camera out of freecam, applies the decks the combat setup names, and, with **Automatically group players** on, puts every player in one group. Then the first step runs.

A player inside a Challenge Mode dungeon keeps the deck it has, since the game won't open the deck there. The log says which player.

## Watch the run

- **The bot canvas** highlights the step the bot is on.
- **The Log tab**, in the right sidebar, shows what happened. It shows warnings and errors only, at first. Set **Min level** to INFO to see each step, each edge the bot takes, and the run summary at the end. The log file keeps every line, whatever the tab shows.
- **The Drops tab** lists what each player picked up. **Keep list only** shows just the drops the bot's quicksell keep list keeps, and **Export CSV** saves a player's table.
- **The Players tab** shows each player's zone and position.

While a run goes on, the **Options** and **Combat** tabs are locked. Your clicks and keys still reach the game, and can throw the bot off, so leave the game windows alone unless you mean to step in. BartleBot keeps Windows from going to sleep until the run ends.

## How players move

Inside a zone, players walk. A step that sends them somewhere walks every player there at once, each from where it stands, at its own speed, around walls, and with [Avoid Mobs](../../reference/options.md#avoid-mobs) on, around the mobs too. A walk that can't get there fails the step, and the log says where it stopped ([A walk failed](../../troubleshooting.md#a-walk-failed)). A walk that runs into a fight anyway is handled like any [fight no step started](when-something-goes-wrong.md#fights-no-step-started).

A long walk can be a teleport instead. When another of your players stands at the goal, or near enough that teleporting to it and walking the rest is quicker, a player with a long way to go teleports to that one through the friends list or the group. A teleport within a zone takes about 5 seconds, so a walk shorter than about 7 seconds is always walked, and so is a walk into a fight or a zone exit. Players that arrive first become the ones the rest teleport to, and a player waits at most about 7 seconds for another to arrive before it walks instead. With Player Realms set to Separate, a player teleports only to one it has been seen with on its own realm. Some zones, mostly dungeons, refuse a teleport. The first refusal is in the log, and players walk in that zone for the rest of the run.

To fight, players walk up to the enemy, and once the fight has started, to its duel circle. With Avoid Mobs on, that walk keeps clear of every other enemy, so it starts no other fight. After a fight, only the players low on health or mana walk to the wisps, those after health and those after mana at once, each group to the wisp its walk reaches soonest. Wisps drift, so each walk follows its wisp right up to it, or until someone picks it up. The rest stay still, which keeps them safe from enemies for a while.

A player walks to a reagent or a quest item to pick it up. One inside a zone exit, such as a cave mouth, is picked up from just outside, or skipped when that's too far for its prompt to show, so no player leaves the zone by accident.

Between zones, players take the game's own way: a door, a teleporter, a list, or a teleport to a friend or the group leader.

Leave the keyboard and mouse off a game window while its player walks. A key you press there steers it.

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
