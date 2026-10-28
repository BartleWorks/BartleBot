@../../../bartleby-core/bartleby/runtime/CLAUDE.md

# Node conventions

Conventions for everything under `bartlebot/nodes/`. The rules for driving a client, which
every node follows (pressing keys, cancellation, coordinating clients, dialogs, UI paths,
logging and timing), are core's `bartleby/runtime/CLAUDE.md`, imported above.

## Adding a New Node

1. Create `bartlebot/nodes/action/<name>.py` (or `condition/`)
2. Export `NODE_SPEC` (NodeSpec), `PARAM_SCHEMA` (list[ParamField]), and `async def run(machine, clients, params) -> str`
3. Register in `bartlebot/nodes/registry.py` — add to both `NODE_REGISTRY` and `NODE_SPECS`
4. Action nodes return `"SUCCESS"` / `"FAILURE"`; condition nodes return `"TRUE"` / `"FALSE"`
5. The GUI auto-generates parameter forms and tooltips from `NODE_SPEC`
6. Write the spec's text for the bot author: description, usage notes, param help and
   hints, returns, and the safety warning. `docs/UI_TEXT.md`, *Node help*, has the form
   of each. A param or note another node already has takes its text from
   `bartlebot/nodes/help_text.py` (Zone, Reset XYZ, the note on players who don't arrive), and
   `tests/nodes/test_help_text.py` checks it does
7. Every `BotOptionSpec` in `required_bot_options` names its `group` — the card it is drawn
   in on Option Values and Options (`bartlebot/options/option_groups.py`). Reuse the group an existing
   declaration of the same option uses; `test_every_declaration_of_one_option_agrees_on_its_card`
   fails otherwise

Use those four names and no others unless the node genuinely has a third outcome. The
failure pair is `schema.FALLTHROUGH_RETURNS`: a graph that routes neither of them sends
the run to the bot's recovery state, so a node inventing `"ERROR"` gets no default and
spins to the no-match limit instead. `docs/RETURN_ROUTING.md` has the rest, including how
an author marks a return value fatal.

## Nodes never write variables

A bot's variables (`docs/VARIABLES.md`) are written by the `set_variables` node and by an
edge as it is taken, which the run loop applies, and by nothing else. `BotMachine.set_variable` refuses any task but the running node's and the
run loop's, so a write from a per-client coroutine under `gather`, or from a task a node
spawns, raises `VariableWriteRefused` -- and a node that writes from its own task is still
wrong: the value would change with nothing on the canvas to say so. A node that needs to
remember something for a later branch returns a value and lets an edge route on it.
