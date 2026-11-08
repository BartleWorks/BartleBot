# When something goes wrong in a run

A long run meets crashes, disconnects and fights nobody planned. This page says what BartleBot does about each one by itself, what it needs from you beforehand, and when it gives up and stops.

## When a game crashes or disconnects

While a bot runs, BartleBot watches every game. It treats one as lost when:

- the game closes,
- the game stops answering, or its network icon shows a bad connection, for about 15 seconds,
- the game shows a disconnect message,
- or a loading screen lasts over a minute.

Then it pauses the bot, closes that game, and launches the account again. It logs in, presses Play, and puts the group back together. Then the bot carries on from its recovery state: the step its author chose to start again from. In most Official bots, that's a quick log out and back in for anyone who needs it.

Several games lost at once come back together, in one batch. If a player was in a fight, the relaunched player tries to rejoin it. If it can't, the others log out and back in too, so the team starts again together, at the dungeon's entrance when they were inside one.

## What recovery needs { #what-recovery-needs }

BartleBot can launch a game again only when it knows the account and has its password:

- **The account is saved** in **Launch › Manage Wizard101 Accounts…**, with its password.
- **The account was launched from BartleBot** at least once. After that, BartleBot recognizes its game however it was started.
- **Automatically select a character** is on, in Settings › Game. When off, a relaunched game waits at character select until you pick, and BartleBot says so.

Before a run, **Clients Cannot Be Recovered** names any game that falls short, and asks whether to run anyway. If one of those games is lost, the run can't go on without it: the bot stops, or waits paused until you're back, and says why. [Troubleshooting](../../troubleshooting.md#clients-cannot-be-recovered) has what to do.

## Relog to clear the slowdown { #relogging }

A game gets slower the longer it stays logged in, and logging out is the only thing that clears it. So during a run, BartleBot logs every player out and back in every few hours. The run pauses at the end of a step, the games close and come back the way they do after a crash, and the bot carries on from its recovery state.

- **Relog interval**, in Settings › Bot, sets how often. It's 4 hours at first. **Off** turns it off.
- A relog for four players takes about three minutes.
- BartleBot waits for a fight to end before it relogs, and skips the relog while any game couldn't be brought back.
- A single game that nears a limit Windows puts on what it can draw is relogged early, before it starts to fail.

A bot can also hold a Relog step of its own, which logs out through the game's menu and back in. It takes seconds and closes nothing.

## Fights no step started

A player who walks too close to a monster is pulled into a fight, even in the middle of another step. BartleBot takes the fight over:

1. It cancels the step that was running.
2. The players who share the fighter's realm join the fight. Other players teleport to the fighter through the friends list, unless the bot keeps players on separate realms. A dungeon that refuses the teleport leaves that player out.
3. The fighter waits up to 20 seconds for the others before its first card, then everyone fights with the bot's combat setup.
4. After a won fight, players under half health or mana collect wisps. Then each moves to a spot nearby that no monster is close to, usually where a helper stood before the fight, or to the world's hub when there's none.
5. When everyone is back where they started, the cancelled step runs again from its start. Otherwise the bot carries on from its recovery state.

A fight the players can't win is fled: when none of them can deal damage for several rounds, or after 10 minutes. Fled players land in the world's hub, collect wisps, and the bot goes to its recovery state. If the game shows **You Are Idle** in any fight, BartleBot flees it the same way.

Three fled fights within 10 minutes stop the bot, since something is wrong with the decks, the mana or the combat setup. [Troubleshooting](../../troubleshooting.md#the-players-fled-fights) has what to check.

## Level scaling elixirs at the start of a run

A bot that level scales its players gives each one the elixir from the Scales of Knowledge, unless the player already has an elixir. The game draws every elixir the same way, so BartleBot can't tell a level scaling elixir from another kind, and it never cancels one.

So before a run, the **Safety Check** names any level scaling player who already has an elixir. If it's another kind, that player runs unscaled. If it's a level scaling elixir taken earlier, it runs out before the others' and costs an extra trip. To start everyone together, cancel the elixir in the game, then run.

Which players are level scaled is the **Level Scaling Players** option, in the bot's **Options** tab.

## When BartleBot gives up

BartleBot stops the run, with a message saying why, when putting things right isn't working:

- a lost game that can't be relaunched,
- the bot going back to its recovery state again and again for the same reason, after the same steps,
- or three fled fights within 10 minutes.

A step that fails too many times in a row stops the run too, and the log says which.

[Troubleshooting](../../troubleshooting.md#the-bot-stopped-by-itself) has each message and what to try.
