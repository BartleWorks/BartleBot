# Variables and conditions

An edge can carry a condition, so a port leads one way or another depending on something other than the result. This page covers conditions on a bot option, variables the bot sets during a run, and reading what they held while it ran.

## Options or variables { #options-or-variables }

Both can steer a bot. They answer different questions:

| | A bot option | A variable |
|---|---|---|
| Set by | the user, before the run | the bot, during the run |
| Changes during a run | never | whenever the bot sets it |
| Kept | in the user's settings, and the bot's default | for this run only |
| Use it for | a choice the user makes, such as whether to sell pets | something the run finds out, such as which spot already turned up a key |

A value the user should choose is an option. A variable the run reads but never sets gets a warning that says so: make it an option.

## Branch on an option { #branch-on-an-option }

A Yes / No option can choose between edges. Declare your own on the **Options** tab in Edit mode, with **Add Option**: a name, a type, a description and a default. The options a bot's nodes add are there too, such as [Team-Up](../../reference/options.md#team-up).

1. Draw two edges from the same port, one to each node.
2. Select the first edge. Its page opens in **Properties**.
3. Under **Conditions**, select **Add condition group**, then **Add condition** in the group, and pick the option in the new row. Check **NOT** to take this edge when the option is off.
4. Leave the second edge with no condition. It's taken whenever no conditioned edge is.

A group can hold several conditions, with **Combine with:** AND (all must hold) or OR (any may), and several groups combine the same way, with **Groups combine with:**. Every conditioned edge is tried before the one without, and the canvas draws it dashed, with its condition beside it.

## Declare a variable { #declare-a-variable }

On the **Options** tab in Edit mode, under the options, the **Variables** section lists the bot's variables. Select **Add Variable**:

- **Name**: what the edges and nodes call it. It can't be an option's name, or another variable's.
- **Type**: **Yes / No**, **Whole number**, or **One of a list**. For a list, type the **Values**, one a line.
- **Starts at**: its value when a run starts. Left alone, a Yes / No starts at false, a number at 0, and a list at its first value.
- **Description**: what it remembers and what reads it. The Run-mode **Options** tab shows it under the variable's `?`.

To change one, select the edit button on its row. A rename carries to every edge and node that uses the variable. A delete, a change of type, or removing a list value something still uses is refused, and the message lists what uses it, so a change never quietly sends the bot another way.

## Set a variable { #set-a-variable }

Two places set a variable:

- **[Set Variables](../../reference/control-flow.md#set-variables)**, a node of its own. Select **Add assignment** on its page for each row. A row sets a variable to a value (**=**), adds a whole number to one (**+**), or flips a Yes / No (**toggle**). Rows apply in order.
- **An edge**, under **When taken, set** on its page. The rows are the same, and apply only when the bot takes that edge. Another edge from the same port sets nothing, and neither does going to the recovery state.

An edge's rows save a node per branch: "remember which way I came" is a row on each way in. The canvas shows what an edge sets after an arrow, as `→ swords + 1`.

Every variable starts again from **Starts at** when a run starts, and keeps its value through a recovery. A counter that should start again each time round the loop needs a row that sets it, on the way into the loop. The bot warns about a counter that only ever adds, in a loop, and one that a recovery reaches without passing a reset.

## Read a variable on an edge { #variable-conditions }

A condition row can name a variable instead of an option. It compares the variable with a value: `==` or `!=` for any type, and `<`, `<=`, `>`, `>=` for a whole number. There's no NOT on a variable's row: use `!=`.

To choose among several ways on, give one port several edges, each with a condition, and one more with none, for when nothing matches. [Branch](../../reference/control-flow.md#branch) does nothing but return SUCCESS, so several nodes can lead into one Branch and share its edges.

An edge's condition reads the values from before its own rows are applied. So `swords == 1` with `swords + 1` on the same edge is the edge for the second sword.

!!! info "What the bot checks"
    A bot with a variable that isn't declared, a value of the wrong type, or `+` on something other than a whole number can't run. It warns about a port where every edge has a condition, since a result that matches none has nowhere to go, about two conditions on one port that can both be true, and about a variable set and never read, or read and never set. A few dozen variable-only nodes in a row, with nothing touching the game, stop the run.

## Watch variables during a run { #variables-in-a-run }

In Run mode, the **Options** tab has a **Variables** section under the options. Before a run, each row shows where the variable starts. During one, it shows what it holds now, and a value that just changed is bold, with what it was in its tooltip. When the run ends, the values stay until the next run.

At INFO, the Log tab names the condition on each edge taken, what the edge set, and every change:

```text
Transition: 'spot_3' (Spot 3) --SUCCESS / next_spot = 4, swords + 1--> 'which' (Which rack)
Variable next_spot: 3 → 4
Variable swords: 1 → 2
Transition: 'which' (Which rack) --SUCCESS [swords == 2]--> 'rack_2' (Rack 2)
```

The run summary ends with each variable's last value, as `Variables: next_spot = 9, swords = 4`.

## When a subgraph is the better tool { #when-not-to-use-one }

A variable is for a way on that depends on what the run found, which the graph can't know ahead of time. A path the bot always takes the same way is better drawn out, even when it repeats: a counter and a hub would make the graph smaller and harder to read. Steps a bot repeats in several places are what a [subgraph](subgraphs.md) is for. A subgraph can't use variables itself.
