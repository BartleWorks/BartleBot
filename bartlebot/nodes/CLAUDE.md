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
   hints, returns, and the safety warning. `bartleby-core/docs/UI_TEXT.md`, *Node help*, has the form
   of each. A param or note another node already has takes its text from
   `bartlebot/nodes/help_text.py` (Zone, Reset XYZ, the note on players who don't arrive), and
   `tests/nodes/test_help_text.py` checks it does
7. Every `BotOptionSpec` in `required_bot_options` names its `group` — the card it is drawn
   in on Option Values and Options (`bartlebot/options/option_groups.py`). Reuse the group an existing
   declaration of the same option uses; `test_every_declaration_of_one_option_agrees_on_its_card`
   fails otherwise
8. Rerun `uv run python tools/build_site_reference.py` and commit the pages it writes under
   `site/pages/reference/`. The site's Reference is rendered from every spec, so a new node,
   or any change to a spec's text, fails `test_the_committed_reference_is_current` until it
   is rerun (`site/CLAUDE.md`, *Generated pages*)

Use those four names and no others unless the node genuinely has a third outcome, as
`open_skeleton_key_door`'s `NO_KEY` is: trying again cannot fix it, so it must not read as
a failure. The failure pair is `schema.FALLTHROUGH_RETURNS`: a graph that routes neither of
them sends the run to the bot's recovery state, so a node inventing `"ERROR"` gets no default
and spins to the no-match limit instead, which the canvas warns about as an uncovered return. `docs/RETURN_ROUTING.md` has the rest, including how
an author marks a return value fatal.

## Nodes never write variables

A bot's variables (`docs/VARIABLES.md`) are written by the `set_variables` node and by an
edge as it is taken, which the run loop applies, and by nothing else. `BotMachine.set_variable` refuses any task but the running node's and the
run loop's, so a write from a per-client coroutine under `gather`, or from a task a node
spawns, raises `VariableWriteRefused` -- and a node that writes from its own task is still
wrong: the value would change with nothing on the canvas to say so. A node that needs to
remember something for a later branch returns a value and lets an edge route on it.

## Moving a client

**A node moves a client inside a zone by walking it, through `walking.walk_clients`**
(`bartlebot/nodes/walking.py`), never by writing its position. The server undoes a long
written teleport, and the rule every stage of #322 keeps is that no teleport is longer than
`HOP_MAX` and nothing but `walking.hop` writes a position (Stage 7). Until then
`teleport_to_coordinates` is the one node that still writes a position, beside the authoring
tools outside `nodes/`, and nothing new may join them:
`test_teleport_to_coordinates_is_the_one_node_left_that_writes_a_position` fails on another.
**Every one goes through `walking.teleport`, which refuses while `walking.TELEPORTS_ENABLED` is
off**, as it ships until Stage 7 confirms the short radius (the user's call, 2026-11-25): the
node fails with the move named. The suite turns it on
(`teleports_enabled` in `tests/conftest.py`); `test_every_position_write_goes_through_the_guard`
fails on a `.teleport(` or `.tp_to(` call anywhere else.

- **Through the shared helpers where one fits**: `clear_stale_prompt`, `reach_prompt`,
  `take_turns` and `enter_zone.fire_trigger` walk. A node that calls one of them, or
  `walk_clients` itself, declares `walking.AVOID_MOBS_OPTION` in its `required_bot_options`.
- **A walk that fails ends the node there**, logged with where it ended (the zone, both
  points, and the closed gate in the way). It is never retried the same way, and never
  replaced by a teleport: a wall a walk met is still there. The one exception is a walk that
  only hurries the game's own move: the Skeleton Key unlocker's walk into the trigger, beside
  the carry-in the game makes anyway, is logged when it fails and the carry-in waited for.
- **A walk that ran into a fight is not the node's failure.** `walk_clients` waits for
  ambient combat to preempt the node (`FIGHT_PREEMPT_WAIT`), which fights it out and runs
  the node again. A node never handles that fight itself.
- **An interact point may lie inside its own object** (a pedestal, a chest, a brazier: six in
  the shipped bots, by `check_bot_routes.py`). The client lists the object, so the walker closes
  it, and a goal on a closed gate has no route. `walk_clients(..., approach=True)` then walks to
  the object's edge, the gate's `Crossing.before`, where the prompt shows. `reach_prompt` and
  `take_turns` pass it for the walk to the prompt only; a reset, or a zone change's trigger,
  never does, since standing in front of a trigger fires nothing.
- **A walk into a fight is `walk_clients(..., fight=True, spare=[mob id])`**, as
  `combat_runner.join_fight` makes it: the walker leaves the mobs it means to meet out of its
  watch, which would otherwise wait on them forever, and their patrols out of its plan, and the
  fight starting ends the walk as done, with no wait for a preempt. Every other mob is avoided.
  A walk that meets a fight while ambient combat is already running a session waits for nothing.
- **A pickup walks to `node_helpers.pickup_spot`**, never onto the entity blindly: from outside
  a zone exit that holds it (or not at all, past `PICKUP_REACH`), and from the far side of an
  entity named in Keep Away From, whose prompt would show instead (#35).
- **A walk arrives within `walking.ARRIVED_WITHIN` (50) of its goal, not on it.** A check of
  "is the client at the point" uses that margin, as Mark Location's does; one of a few units
  sends the client walking again every pass.
- **Tests walk on `walk_double`** (`tests/conftest.py`): each walk is recorded on
  `FakeClient.walk_to`, and `moves(client)` lists teleports and walks together in order for
  a node that still does both. `tests/nodes/test_walking.py` walks core's `SimClient` for
  real.
