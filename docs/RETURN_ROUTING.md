# Return routing

What the graph does with a return value it does not route anywhere: the default
failure edge, the explicit stop, and why neither is drawn as an edge.


Every node returns a named value -- `SUCCESS`/`FAILURE`, or `TRUE`/`FALSE` for a
condition -- and the graph routes each one with a transition carrying that name in its
`when`. This file is about the ports nothing is routed off, which turned out to be most
of them.

## The default failure edge

**A `FAILURE` or `FALSE` port with no edge on it sends the run to the bot's
`recovery_state`.** The two names are `schema.FALLTHROUGH_RETURNS`; the engine is
`BotMachine._fallthrough_dest`, reached from the run loop when `advance()` matched
nothing.

The reason is that almost every failure in a real graph already went there. Before the
default landed, **790 of the shipped library's 2799 transitions were a failure edge into
that bot's own recovery node** -- 58% of every failure edge, and 28% of every edge drawn
on the canvas. `mt_olympus` alone carried 55 of them and `waterworks` 50. They are not
just numerous: the recovery node is `teleport_to_world_hub` in 57 of the 87 bots, so they
are long *backward* edges converging on one hub, which is the geometry that makes a
canvas unreadable (issue #60 is a screenshot of exactly one of them).

So the edge is not drawn at all. Instead the port wears the same amber recovery badge the
recovery node already wears, and reading the two as one route is the whole point --
`NodeItem._paint_port_mark`, `edit_scene.port_marks_for`.

Four cases the default deliberately does **not** cover, each of which would change what a
bot does:

- **Any return value but those two.** An unrouted `SUCCESS` landing on the recovery node
  is nonsense, not a convenience.
- **The recovery state's own failure.** Defaulting it loops the node onto itself, and the
  same-state idle limit would only turn that into a stop several minutes later. Its
  uncovered failure is still a validation warning, because there it is a real gap.
- **A state with no outgoing transitions at all.** That is a terminal state and stops the
  run cleanly; the terminal check runs before the fall-through and stays that way.
- **A config with no `recovery_state`.** Only a hand-built one in a test. The default is
  simply off, and the old no-match behavior stands.

An explicit edge always wins: the default is what happens when nothing is routed, never an
override. That includes a flag-guarded edge whose guard is false at run time -- the guard
did not fire, so the port is unrouted *this tick*, and the run recovers rather than
spinning to the no-match limit.

### Reachability follows it

An unrouted failure is an edge the engine takes and the file does not record, so any walk
over the graph has to follow it too. `edit_scene._reachable_states` does, and
`tools/audit_bots.py` delegates to it rather than keeping a second copy -- without that,
the one node reachable from everywhere is reported unreachable, warned about on the
canvas, and offered up to `BotCanvas._cleanup_orphans` for deletion.

## The explicit stop

Once an unrouted failure means "recover", an author needs a way to say **"no, this one is
fatal."** Before the default, the way to express that was to leave the port unrouted and
let the run spin to the no-match limit -- which the fall-through makes safe, and so
removes. The two had to ship together.

`StateModel.stops` maps a return name to the reason shown to the user. It is a property of
the *port*, not an edge to a pseudo-node, which is why it is stored on the state and why
the canvas draws it as a red stop badge on the port rather than as a route to anywhere.
`StateModel.stop_reason` is the read side, and **an empty string is a real answer** -- the
port stops, the author simply gave no reason -- so every caller tests against `None`, not
truthiness.

Three rules:

- **A stop outranks everything**, including the terminal check and any edge that would
  otherwise have fired. Otherwise a routed return could not be made fatal without deleting
  its edge.
- **The reason reaches the user, not only the session log.** `BotMachine` stops itself and
  then calls `on_explicit_stop`; `AppController._on_machine_explicit_stop` puts the reason
  in a dialog, deferred through the event loop for the Python 3.13 re-entrancy trap in
  `CLAUDE.md`. The machine stops *before* the callback for the same reason the
  restricted-zone stop does: the FSM has to halt with no GUI attached.
- **`stops` is `None`, not `{}`, when empty**, so `exclude_none` keeps the key out of
  every state in every bot file. `EditScene._set_port_stop` puts it back to `None` when the
  last entry goes.

`check_zone` finding the wrong world is the case the feature was built for: a run that
merely ends is indistinguishable from one that crashed.

## Authoring

Right-click an output port with no edge on it: **Stop Bot on `<RETURN>`…** asks for the
reason, and **Clear Stop on `<RETURN>` Port** undoes it. The action is offered only on an
unrouted port -- a stop on a routed one would be a way to disable an edge that is still
drawn, which is the confusion this whole design exists to remove.

There is deliberately **no shortcut for "link this failure edge to the recovery node"**,
which issue #11 originally asked for. With the default in place that shortcut creates a
redundant edge: the thing it used to save is now the thing the migration deletes.

## The failure-edge toggle

`F`, or the eye button at the right-hand end of Bar B, cycles three modes rather than
two (`bot_canvas._FAILURE_EDGE_CYCLE`):

| mode | glyph | shows |
|---|---|---|
| `all` | clear eye | every failure edge |
| `hide_recovery` | eye behind a broken slash | only failure edges going somewhere other than the recovery node |
| `none` | eye behind a solid slash | no failure edges |

The middle mode asks where the edge *goes*, not what it is. An explicit failure edge into
the recovery node says exactly what the default already says, so hiding it leaves a graph
that reads like one written after the default existed. Every other failure edge is a real
branch and stays.

It matters most for bots the migration has not been run over -- a user's own files, and
anything authored before this landed. The setting is `canvas.failure_edge_mode`;
`canvas.show_failure_edges` is the bool it replaced, still written so a downgrade reads
the closer of the two answers, and still read when the mode key is absent.

## The library migration

`tools/drop_default_failure_edges.py` is the one-shot tool that removed the redundant
edges from `bots/`. It dropped 710 of the 790 across 71 of the 87 bots; the other 80 it
refused, and each refusal is a case where deleting the edge would change what the bot
does:

- the edge carries a **flag guard** -- that says *when* to recover, which the
  fall-through cannot express;
- its source is the **recovery state itself**, which the fall-through skips;
- it is the source's **last outgoing edge**, so dropping it would make the state terminal;
- its source is a member of a **linked subgraph instance**, whose edges belong to the
  definition in the library rather than to this file.

It is deliberately a tool and not a load-time migration. Silently rewriting a user's bot
file on open is the thing `docs/BOT_OPTIONS.md` is careful never to do either, and the
three-mode toggle is what an unmigrated file gets instead.
