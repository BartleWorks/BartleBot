# Variables

What a bot learns as it runs and reads back to choose its next node: the model, the
rules the engine and validation hold it to, and when to reach for a subgraph instead.
The plan, epic #16, lives on a claude.ai page (*Runtime flow control*); this file is the
part of it that governs code. Stage 1 (#186) is the engine. The editors, the live values
in Run mode and assignments on edges are Stages 2 to 4 (#187, #188, #189).

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
- **Written by a `set_variables` node, and nowhere else** (until #189 adds the same rows
  on an edge). Its `assignments` param is rows of variable, `op` and `value`: `set` to a
  value, `add` a whole number to an int, `toggle` a bool. They apply in order, so a later
  row sees an earlier one's write.
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
resets a value where the loop begins, usually on the way into the dungeon, and a recovery
goes back in through that same reset. There is no reset on entering the first node: no
shipped bot loops back to its first node, so it would only ever fire at startup (#191).

**Each write is logged once**, by the machine, as `Variable swords: 1 → 2`; a write that
changes nothing is DEBUG. `on_variable_change(name, old, new)` follows the callback guard
in `CLAUDE.md`.

**A loop of variable-only nodes stops the run.** A wrong condition between two
`set_variables` or `branch` nodes would loop without touching the game, and neither the
no-match count nor the same-state count sees it. More than
`variables.MAX_VARIABLE_ONLY_STEPS` of them in a row stops the run with a reason naming
them (`_note_variable_only_step`).

## Validation

`variables.check(config)` returns errors and warnings, with no Qt. An error refuses the
run (`AppController._check_variables`, beside the restricted-zone and Player Realms
checks); a warning is the author's. Stage 2 draws both on the canvas badge.

Errors:

- a condition or a write naming an undeclared variable;
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

## Subgraphs, templates and share files

**A subgraph definition cannot use a variable.** A variable row or a `set_variables` node
in a definition is a structure error (`definition_structure_errors`), and a definition
document that declares one cannot be saved. Two instances reading the bot's variables by
name would share one counter without saying so. Variables of a subgraph's own, renamed
per instance the way member ids are, are a later stage of the plan.

**The template diff** has a `("variable", name)` unit per declaration, applied in place
like an option, and `_canon_flags` includes variable rows, so a changed comparison moves
the edge to another slot and shows as a change to it.

**Share files** need nothing new: a bot carries its variables inside it. If this lands
after a release, `SHARE_FORMAT` rises with it, since an older app would drop the field it
does not know on its first save. Before 1.0.0 there is no older release.

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
