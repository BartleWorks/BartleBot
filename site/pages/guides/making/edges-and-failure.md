# Edges and failure

Every node ends by returning a result, and the bot's edges decide where each result goes. This page covers what happens to a result with no edge, the recovery state, making a result stop the bot on purpose, and how to show or hide the failure edges on the canvas.

## What a node returns { #results }

Most nodes return **SUCCESS** or **FAILURE**. A node that checks something, such as [Check Zone](../../reference/control-flow.md#check-zone), returns **TRUE** or **FALSE** instead. Each node's page in the [Reference](../../reference/index.md) says when it returns each one, and so does its **Connections** card.

Each result has its own output port. An edge from a port is taken when the node returns that result. A port can have several edges with conditions on them ([Variables and conditions](variables-and-conditions.md#branch-on-an-option)).

## A failure with no edge goes to the recovery state { #the-default-failure-edge }

**A FAILURE or FALSE with no edge sends the bot to its recovery state.** You don't draw that edge. Instead the port wears the recovery state's amber badge, and the node's **Connections** card says "recovery · not drawn".

So you draw a failure edge only where a failure should go somewhere else, such as on to a potion node when [Collect Wisps](../../reference/combat-and-healing.md#collect-wisps) leaves a player short. Any edge you draw wins over the recovery state.

The other cases:

| The node | What happens |
|---|---|
| returns FAILURE or FALSE, with no edge on that port | the bot goes to its recovery state |
| is the recovery state, and returns FAILURE or FALSE with no edge | the bot runs the node again, as in the next row. Draw an edge or a stop. Until you do, the node has a warning |
| returns SUCCESS or TRUE, with no edge on that port | the bot runs the node again, and stops after several times in a row. The node has a warning, "Uncovered return value" |
| has no edges at all | the run ends there, cleanly. The node wears a square stop badge, and the log says "Reached terminal state" |

!!! info "Why the edge to recovery isn't drawn"
    Nearly every failure in a real bot goes to the recovery state. Before BartleBot did that by itself, more than half the failure edges in the Official bots were long edges back to one recovery node, and they buried the rest of the graph. A port that wears the badge says the same thing, without the line across the canvas.

## Finish the first bot { #finish-the-first-bot }

The bot from [Build a first bot](the-canvas.md#build-a-first-bot) has one warning: Collect Wisps is the recovery state, so its FAILURE has nowhere to go. When a player stays short of health or mana, the bot should wait and try again, as it does after a success.

1. Drag from Collect Wisps' **FAILURE** port to [Sleep](../../reference/utility.md#sleep)'s input. The warning goes.
2. Select **Save and finish editing**, the checkmark. When it asks for a Rewards tag, select **Save Without**.
3. Select **Run bot**, or press F5.

The bot collects wisps, sleeps 30 seconds, and starts again, until you stop it with Shift+F5. Set the Log tab's **Min level** to INFO to watch each step ([Running a bot](../running/running-a-bot.md#watch-the-run)).

## Pick the recovery state { #recovery-state }

The recovery state is where the bot starts again when something goes wrong: a failure no edge routes, a game brought back after a crash, or a fight no step started ([When something goes wrong in a run](../running/when-something-goes-wrong.md)). It should be a node that works from wherever the players are, and leads back into the loop. In the Official templates it's a [Relog](../../reference/utility.md#relog), which clears a player stuck in a fight or a window.

To move it, select the node that should take it and check **Recovery state** on its **Role** card. The first node of a new bot takes it, and the initial state too.

**A run that keeps landing in recovery stops.** When the bot goes back to its recovery state again and again for the same reason, after running the same steps each time, the run stops and says why ([Troubleshooting](../../troubleshooting.md#the-bot-stopped-by-itself)).

## Stop the bot on a result { #explicit-stop }

Some results mean the run can't go on, such as Check Zone finding a player in the wrong world. Make that result stop the bot, and say why:

1. Right-click the port, on a node in Edit mode. It must have no edge.
2. Select **Stop Bot on FALSE…**, or on whichever result the port is, such as **Stop Bot on FAILURE…**.
3. Type the reason. BartleBot shows it to whoever runs the bot.

The port wears a red stop badge. When the run reaches it, the bot stops and a **Bot Stopped** message says "The bot stopped at", the step, and what it returned, as "which returned FAILURE.", then your reason. A stop wins over everything, the recovery state included. To undo it, right-click the port and select **Clear Stop on FALSE Port**, or on whichever result it is.

## Show or hide failure edges { #failure-edge-toggle }

The eye button at the right-hand end of the bar above the canvas, or F, cycles through three views:

| The button's tooltip | Shows |
|---|---|
| "Failure/False edges: all shown (F)" | every failure edge |
| "Failure/False edges: recovery edges hidden (F)" | only failure edges that go somewhere other than the recovery state |
| "Failure/False edges: all hidden (F)" | no failure edges |

The middle one helps most on an older bot of your own, drawn before BartleBot sent failures to the recovery state by itself: an edge drawn into the recovery state says what a port with no edge already says. Your choice is kept for every bot.
