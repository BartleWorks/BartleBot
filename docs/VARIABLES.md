# Variables

What a bot learns as it runs and reads back to choose its next node: the model, the
rules the engine and validation hold it to, and when to reach for a subgraph instead.
The plan, epic #16, lives on a claude.ai page (*Runtime control flow*); this file is the
part of it that governs code. Stage 1 (#186) is the engine, Stage 2 (#187) the editors,
Stage 3 (#188) reading a run: the live values and the log, and Stage 4 (#189) assignments
on edges.

A bot already branches on the value its last node returned. A variable is how it
branches on something that happened earlier in the run, without copying every node in
between onto two branches.

## Options and variables

**Options are what the user chose before the run. Variables are what the bot learned
during it.** The two never cross: separate declarations, separate sections, and a
variable may not take an option's name.

| | bot option | variable |
|---|---|---|
| set by | the user, before the run | the bot, during the run |
| kept in | `settings.json` and the bot's declaration | memory, for this run only |
| changes during a run | never | yes |
| types | bool, int, float, str, lists, the node-declared kinds | bool, int, enum |
| declared in | `BotConfig.options`, or a node's spec | `BotConfig.variables` |

`docs/BOT_OPTIONS.md` is the other half of this table.

## The model

- **Declared and typed** (`schema.VariableModel`): a name, a type of `bool`, `int` or
  `enum`, an enum's `values`, an `initial` value and a description. Left unset, `initial`
  starts a bool at false, an int at 0 and an enum at its first value. `BotConfig.variables`
  is left out of the file while empty, so a bot without any is written as before. There is
  no variable created by its first write: a typo would make a new one that reads as zero.
- **Read by edge conditions, and nowhere else.** A condition row is either an option row,
  `{option, negate}`, or a variable row, `{variable, compare, value}`, and the two mix in
  the same AND/OR groups. Comparisons are `== != < <= > >=` against a literal; only an int
  takes the four that order. Equality is by type as well as value, so `true` never equals
  `1` (`variables.compare`). Every reader of `FlagCondition.option` must pass over a
  variable row, whose `option` is None.
- **Written by a `set_variables` node, or by an edge as it is taken, and nowhere else.**
  A node's `assignments` param is rows of variable, `op` and `value`: `set` to a value,
  `add` a whole number to an int, `toggle` a bool. They apply in order, so a later row
  sees an earlier one's write. An edge holds the same rows in `TransitionModel.assign`,
  left out of the file while empty, and applies them only when it is the edge taken (see
  [Edge assignments](#edge-assignments)).
- **A switch** is several conditioned edges on one port plus one without a condition: the
  otherwise. `sort_transitions` already tries conditioned edges first. A `branch` node
  does nothing and returns SUCCESS, so several nodes can lead into one switch.
- **Rows, never expressions.** Nothing is parsed, so validation knows every read and
  write, and a share file stays data.

## The rules the engine keeps

**Only the run loop writes.** `BotMachine.set_variable` is the one write, and it refuses
any task but the running node's and the run loop's own (`VariableWriteRefused`). The
ambient watchers run beside a node, and a node's game code runs once per client in
parallel under `gather`, which wraps each coroutine in a task of its own; letting either
write would put two writers on one value. So **a node's game code never writes a
variable**, and `bartlebot/nodes/CLAUDE.md` says so.

**A write never waits.** `set_variables` has no `await`: it works out every row's new
value first (`variables.apply_assignments`, which raises before returning anything), then
writes them one after another. An ambient fight's cancel lands before the node starts or
after it has written, never between two rows, so the rerun cannot apply `swords + 1`
twice.

**Fresh when the run starts, and changed only by a write after that.** `run()` sets every
value from its declaration. A recovery jump (`force_state`), an unrouted failure falling
through to recovery, and a rerun after an ambient fight leave values alone. The author
resets a value where the loop begins, usually with an edge assignment on the way into the
dungeon, and a recovery goes back in through that same edge. There is no reset on
entering the first node: no shipped bot loops back to its first node, so it would only
ever fire at startup (#191).

## Edge assignments

"Remember which way I came" with only `set_variables` costs a node per branch: Heretical
Academy had eight, one per spot, before they moved onto the spots' edges. A statechart's answer is an assignment on the transition
itself, and our graph is a state machine, so an edge can write too (#189).

**Applied when that edge is taken, and only then.** `to_machine_config` carries an edge's
rows under `assign`, and the machine gives each assigning transition an after-callback,
`_assign_<idx>`, resolved by `BotMachine.__getattr__` as the conditions are. It runs only
when its transition fires, and hands the rows to the run loop. The run loop logs the
transition and then applies them (`_apply_taken_assignments`), so the log reads in the
order things happened:

```
Transition: 'spot_3' (Spot 3) --SUCCESS / next_spot = 4, swords + 1--> 'which' (Which rack)
Variable next_spot: 3 → 4
Variable swords: 1 → 2
```

That is a statechart's `event [guard] / action`; a conditioned edge reads
`--SUCCESS [swords == 2] / found toggled-->`. So:

- **An edge's condition reads the values from before its own writes.** Every condition on
  the port is checked first, and only the edge chosen has its rows applied, so
  `[swords == 1] / swords + 1` is the edge for the second sword. A condition that read
  after the writes would depend on an edge that had not yet been chosen, and sibling
  edges with different rows would each see a different value. Moving a `set_variables`
  node's rows onto the edge before it therefore lowers by one every comparison after it
  that reads a counter it adds to, as Heretical Academy's racks did.

- **Another edge from the same port writes nothing**, and neither does an edge on another
  port: only the edge taken noted its rows.
- **A jump to recovery writes nothing.** `force_state` and an unrouted failure falling
  through take no edge.
- **A rerun after an ambient fight cannot write twice.** The rows are applied once, by the
  run loop, after the source node has returned; the node a rerun repeats is the one the
  edge led into.
- **A write never waits** here either: every row's value is worked out first
  (`apply_assignments`), then written, with no `await` between the transition and the
  last write. A row validation should have refused stops the run with a reason instead
  of half-applying.

A call node's edge stands for one transition per member that feeds its exit port. The
edge panel writes the rows to all of them, as it does a condition, and only the one whose
member ran is ever taken.

**Each write is logged once**, by the machine, as `Variable swords: 1 → 2`; a write that
changes nothing is DEBUG. `on_variable_change(name, old, new)` follows the callback guard
in `CLAUDE.md`. Nothing else logs a write, the Run-mode section included: the machine's
line already reaches the in-app log, so a restatement would only be a second copy there.

**A loop of variable-only nodes stops the run.** A wrong condition between two
`set_variables` or `branch` nodes would loop without touching the game, and neither the
no-match count nor the same-state count sees it. More than
`variables.MAX_VARIABLE_ONLY_STEPS` of them in a row stops the run with a reason naming
them (`_note_variable_only_step`).

## Validation

`variables.check(config)` returns errors and warnings, with no Qt. An error refuses the
run (`AppController._check_variables`, beside the restricted-zone and Player Realms
checks); a warning is the author's. Both are on the canvas badge, each line headed
`Variables:`, and an error disables Run as any other does. A problem's `states` mark those
nodes too, so the node a condition or a write is on turns red or amber like one with a
param missing (`edit_scene.variable_report`, `variable_reasons`). A bot that declares,
reads and writes no variable skips the check.

Errors:

- a condition or a write naming an undeclared variable, on a node or an edge (an edge's
  problem marks its source node);
- a value of the wrong type, or an enum value not in the list;
- an ordering comparison on a bool or an enum;
- `add` on a non-int, or `toggle` on a non-bool;
- two variables with one name, or a variable named like one of the bot's options;
- a declaration that breaks its own type: no enum values, a repeated or blank one, values
  on a non-enum, an initial value the type cannot hold.

Warnings:

- **a port where every edge has a condition**, among ports with at least one variable
  condition: a result that matches none goes to the no-match count or to recovery. Ports
  conditioned on options alone are left alone, since an option and its negation are the
  ordinary way to fork on a setting;
- **two single-variable conditions on one port that can both be true**: which one is taken
  is file order, which the canvas does not show;
- a variable **set and never read**, or **read and never set** ("make it an option");
- a `set_variables` node with no rows;
- **a counter that carries over**: a variable an `add` or `toggle` changes, in a loop with
  no `set` of it anywhere. A loop is the strongly connected set of nodes the counter sits
  in, so a counter counting inside one pass of a loop that resets it on the way in is
  fine — that is Heretical Academy's shape. Every node counts as able to reach recovery,
  since an unrouted failure and an ambient fight both go there;
- **a recovery that skips the reset**: a counter the recovery state reaches without
  passing a `set` of it, so a value from before the recovery carries into the run after.

For both loop warnings an assigning edge is a step of its own between its two ends, so a
reset on Enter's SUCCESS edge does not cover Enter's FAILURE edge into the same node.

## Authoring

Everything above can be done in the editor, without touching the file (#187). Heretical
Academy's puzzle, built through the three editors alone, is a test
(`test_heretical_academys_puzzle_can_be_built_without_touching_the_file`).

**Declaring: the Variables section**, under Options on the Edit-mode Options tab
(`sidebar/sections/variables_section.py`). A row is the name, then the type and where it
starts (`Whole number · starts at 0`), behind the option rows' gutter. Add and Edit open
one dialog: name, type, an enum's values one a line (the order is kept, and the first is
where it starts unless another is picked), Starts at, and a description. A start that is
the type's own is left out of the file. The dialog refuses three things, each because it
would change routing without saying so:

- **Delete, while anything uses the variable.** It names each edge and node that does
  (`variables.uses`), and the author takes them off first. Deleting an option strips its
  conditions in silence; a variable's conditions are the routing.
- **A change of type, while anything uses it.** Every row reading it would compare with a
  value the new type refuses.
- **Removing an enum value a row compares with or sets** (`uses(..., values=...)`).

A name is refused when it is blank or another variable's or an option's
(`variables.name_problem`). **A rename is the one change that carries its references**:
`variables.rename` rewrites both condition forms, every `set_variables` row and every
edge's assignments. Every
change goes out on `edit_starting` first, so it is one undo step like a panel edit.

**Reading: the edge panel's Conditions** (`canvas/variable_rows.ConditionRow`, inside
`param_panel._FlagConditionsWidget`). A row picks an option or a variable from one grouped
list. A variable row offers only the comparisons its type takes
(`variables.comparisons_for`) and its value in **`VariableValueField`**
(`widgets/inputs/variable_widgets.py`): a checkbox for a bool, a spin box for an int, the enum's
own values for an enum. So **no control ever offers a value the type refuses**. A
variable cannot be negated, so the NOT box is hidden on its row, keeping its slot.

**Writing: the `assignments` param type** (`variable_rows.AssignmentsField`), a
`set_variables` node's rows: variable, the writes its type allows
(`variables.operations_for`: set and add for an int, set and toggle for a bool, set for an
enum), and a value unless it toggles. An add is a whole number. The edge panel has the same
editor under Conditions, as **When taken, set**, read against the declarations the page
was shown with; a change is one undo step.

**A row the editor only read is never rewritten.** One naming a variable the bot does not
declare is listed and handed back as it came, and so is a value its type refuses: shown
blank, never as a value it is not, until the author picks one. Validation is what says it
is wrong. Assignments that are not rows at all are held untouched (`_HeldParam`).

**A page follows a declaration edit.** The Options tab replaces the Properties page while
the author declares, so an option or variable added, renamed or deleted there re-shows
the node or edge page from the config (`BotCanvas.reshow_properties`, on
`LeftSidebar.declarations_changed`). Left as it was, a renamed variable's old name would
be written back on the page's next edit.

**On the canvas, every conditioned edge says its condition**, options as well as
variables: a label, `swords == 2`, with the whole expression in its tooltip
(`variables.describe_guard`). It hangs where the edge turns toward its destination, not at
its source port: every edge from one port shares its first stretch, so a switch's labels
would sit on each other there. It is cut to `variables.EDGE_LABEL_CHARS`, then to the room
its segment has, so it never runs into the destination node; the tooltip is never cut. A
click on it selects the edge. It is drawn over every edge and under every node, so no
other edge strikes through its text. That is why it is an item of its own
(`edge_item.EdgeLabelItem`) rather than a child of its edge: Qt paints a child straight
after its parent, under every edge stacked later. The edge adds it to the scene, removes
it, and shows and hides it along with itself. The repaint tracer (#73) watches it like
any other item, and every change of its size goes through `prepareGeometryChange`.

**Every edge that writes says what it writes** on the same label, after its condition:
`swords == 4 → found toggled`, or `→ swords + 1` for an edge with no condition
(`variables.edge_label`). The port the edge leaves already names its outcome, and the
arrow is what tells a write from a condition: `swords = 4` beside `swords == 4` would be
too easy to misread. Each part is cut to `EDGE_LABEL_CHARS` on its own. Short of room, the
condition is cut first, since the dashed pen already says there is one and nothing else
on the canvas says what the edge sets; with no room for any of it, `… → found to…`. An
assignment is not a condition, so it leaves the pen solid.

## Reading a run

Once a branch depends on a variable, the highlighted node no longer says why the bot is
there. Three things say it instead (#188).

**The Variables section in Run mode** (`sidebar/sections/variable_values_section.py`), under
Option Values on the Run-mode Options tab, as the declarations sit under Options in Edit
mode. A row is the variable's name and its value. Before a run that is where it starts.
During one it is what it holds now: `AppController` passes the machine's
`on_variable_change` to the section, so every write shows as it is made. After the run the
values stay, so the run can be read, until the next run or another bot starts them again.
An equal config swapped in during a run, by an undo snapshot or the pre-run recompose,
keeps them. A bot with no variables hides the section.

**A value that just changed is marked**, bold in the accent, with what it was in its
tooltip. The mark lasts through the first node after the write that touches the game, and
is cleared when the one after that starts (`bartlebot/bot/variable_watch.LiveVariables`, fed
`on_state_change` with `BotMachine.node_type_of`). Clearing it when the next node starts
would mark nothing anyone could see: a `set_variables` write is followed by the next node
within milliseconds, and so is the `branch` that usually reads it. For the same reason
`set_variables` and `branch` never count toward clearing it. Two writes before any game
node has shown the first are one change, and the tooltip names the value from before both.

**A conditioned transition names its condition in the log**, in the same words as the
edge's label:

```
Transition: 'which' (Which rack) --SUCCESS [swords == 2]--> 'rack_2' (Rack 2)
```

An edge without a condition, the otherwise of a switch included, is logged as before.
`to_machine_config` carries each edge's condition as text under `guard`, and the machine
gives each guarded transition a before-callback, `_took_guard_<idx>`, that notes it was
the one taken. The destination alone cannot say which: two edges from one port can reach
the same node on different conditions. An edge that writes names what it set after the
condition, `/ swords + 1`, and each write follows on its own line, once. The machine takes
`guard`, `assign` and `label` off before it hands the transitions to the `transitions`
library, which refuses any argument it does not know.

**The run summary lists each variable's final value**, after the drops:
`Variables: next_spot = 9, swords = 4`.

## Subgraphs, templates and share files

**A subgraph definition cannot use a variable.** A variable row, an edge assignment or a
`set_variables` node in a definition is a structure error (`definition_structure_errors`), and a definition
document that declares one cannot be saved. Two instances reading the bot's variables by
name would share one counter without saying so. Variables of a subgraph's own, renamed
per instance the way member ids are, are a later stage of the plan.

**The template diff** has a `("variable", name)` unit per declaration, applied in place
like an option, and `_canon_flags` includes variable rows, so a changed comparison moves
the edge to another slot and shows as a change to it. A slot's value is a `SlotValue`:
where the edge lands and what it sets, in order. So an added, removed or changed
assignment is a change to the edge ("When taken, sets nothing → sets swords + 1"), and a
conflict when the user changed it too.

**A variable the template declares or drops is one change with what uses it**: every edge
and `set_variables` node of that release that reads or writes it. Applied alone, either
side would leave a row naming a variable the bot does not declare, which refuses the run.
The change is titled for the variable, or, when it comes with an added node, names it
after the node ("Add 'Mark found'; declare 'found'"). A declaration that only changes is
not grouped: it leaves no row dangling.

**Share files** need nothing new: a bot carries its variables inside it. If this lands
after a release, `SHARE_FORMAT` rises with it, since an older app would drop the field it
does not know on its first save. Before the first public release there is no older release.

## When not to use one

Mount Olympus visits Athena four times, and each visit leads to a different stage. A
`stage` counter and one hub would make that graph smaller and harder to read: the canvas
would no longer show the dungeon's order. **Repeated authoring is what subgraphs are
for.** A variable is for a path that depends on what the run found, which the graph
cannot know ahead of time: which rack is free, which spot already turned up a sword.
That is also why a variable set and never read is a warning.

Variables never feed a node's params in this plan, and a zone param never will:
`check_bot_config` refuses restricted zones by reading params before the run
(`docs/RESTRICTED_ZONES.md`).
