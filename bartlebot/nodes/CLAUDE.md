# Node conventions

Conventions for everything under `bartlebot/nodes/`, plus the two game-facing problems every node hits: telling one modal dialog from another, and pathing to a UI element when several match.

## Adding a New Node

1. Create `bartlebot/nodes/action/<name>.py` (or `condition/`)
2. Export `NODE_SPEC` (NodeSpec), `PARAM_SCHEMA` (list[ParamField]), and `async def run(machine, clients, params) -> str`
3. Register in `bartlebot/nodes/registry.py` — add to both `NODE_REGISTRY` and `NODE_SPECS`
4. Action nodes return `"SUCCESS"` / `"FAILURE"`; condition nodes return `"TRUE"` / `"FALSE"`
5. The GUI auto-generates parameter forms and tooltips from `NODE_SPEC`

## Multi-client coordination
Use `poll_each_until_seen()` from `bartlebot/nodes/node_helpers.py` for operations that must complete across all clients (zone changes, prompts, loading screens).

Some world objects only serve one player at a time — a second client interacting while the
first is mid-interaction has its keypress silently swallowed. Use `take_turns()` for those:
it parks every client at `reset_xyz`, then sends them to the object one at a time, handing
each to a `turn_fn` that owns the object until it returns. The release moment is what varies
between nodes (a menu confirmed open, a zone list dismissed, a loading screen begun), which
is why it is a callback rather than a fixed sequence.

This is offered as an optional `sequential` param on `interact`, `interact_menu`,
`enter_zone_via_object`, and `enter_zone_via_list`; it defaults off, and the parallel path
in each of those nodes is untouched by it. `apply_level_scaling` predates the helper and is
unconditionally sequential against its one hardcoded station.

Pass `park_after_turn=False` when the interaction removes the client itself (a zone
transition) — otherwise the helper aims a teleport at a client already on a loading screen.

## Keeping a session log readable

A five-hour four-client run writes tens of thousands of lines, and the difference
between a log you can debug from and one you scroll past is entirely in what does
*not* get written twice. Three rules keep it that way (#156):

- **`machine.log()` is the node-facing API, and files the record under the running
  node** — `bartlebot.node.quicksell`, not `bartlebot.bot.machine`. That is what makes
  "everything quicksell did" a grep. The machine's own bookkeeping uses
  `self._log_machine()` instead, which always lands on `bartlebot.bot.machine`. The
  split is not cosmetic: the ambient dialog and combat watchers run *concurrently*
  with whatever node is executing, so a watcher using `log()` would file its message
  under a node that had nothing to do with it.
- **Never log the same event twice.** A GUI widget that restates something another
  logger already wrote passes `extra=GUI_ONLY`, which keeps it in the in-app log
  widget and out of the session file. `bot_canvas`'s `Entered:` / `Result:` lines are
  the case this exists for — they were a fifth of the file.
- **Never log a wizard name, or its `m_nameKeys`.** Logs get pasted into bug reports,
  and `tools/name_keys.py --decode` turns the key straight back into the name, so it
  is not a safer stand-in. See *No real player data*.

`RepeatCollapsingFilter` keeps the first of a run of identical messages and reports a
count when the streak ends. It only collapses *consecutive* repeats, deliberately: two
failures alternating are the case where a count would hide something. That also limits
what it can do — a warning that fires once per loop pass is separated by everything else
that pass logged, so it does not collapse. `=== pass N ===` is what makes that shape
legible instead.

## Telling modal dialogs apart

Every in-game modal is the same `MessageBoxModalWindow` tree, so "a modal is up"
does not say which one. Identify it by caption, comparing against the key constants
in `bartlebot/dialog_captions.py`.

Most nodes want `resolve_modal()` rather than `identify_modal()` — it acts on the
dialog and reports only what the caller has to decide about:

```python
outcome = await node_helpers.resolve_modal(client)
if outcome.refusal:                       # the game said no; stop
    machine.log(f"FAILURE: … — {dialog_captions.describe(outcome.refusal)}")
    return "FAILURE"
if outcome.answered:                      # a question was confirmed; skip this tick's retry
    return
```

Pick by the shape of the wait that follows:

| wait | use |
|---|---|
| a retry loop on `is_loading()` | `ModalAwareRetry(action_fn)` as `poll_each_until_seen`'s `action_fn`, then check `.refusal` |
| a *sticky* condition (left the zone, arrived) | `await_instance_prompt()`, or `answer_instance_prompts()` for a whole group |
| one-off, mid-loop | `resolve_modal()` directly |

Never answer a dialog with a blocking wait ahead of an `is_loading()` check: it
costs the full Safety Delay whenever no dialog appears, and three seconds spent
before watching for a loading screen can miss it entirely.

Do **not** infer identity from which buttons are showing. That only works while no
two dialogs a node can hit share a layout, and it already broke once: the dungeon
exit warning and the friend-busy dialog both show a right button, so
`teleport_to_friend` reported every dungeon exit as an unreachable friend (#84).

Captions come from `bartlebot/data/dialog_captions.json`, all seven locales per key
— memory holds the *resolved* text, so an English substring match fails silently on
any other locale. To add a dialog: append to `KNOWN_KEYS`, run
`uv run python tools/dump_dialog_captions.py`, and commit the regenerated JSON.
`--verify` re-checks the snapshot for keys whose text no longer identifies them
uniquely. An unrecognised caption returns `None`, and callers must fall back rather
than treat it as "no dialog".

## Ambiguous UI paths

Window-tree paths match on **name only**. `_get_window_from_path` walks depth-first and
returns the first branch that completes the path, backtracking out of dead ends — which is
what makes Wizard101's duplicate `ButtonLayout` siblings safe to path through, as long as
only one branch completes.

When *several* branches complete it — a list menu whose rows are structurally identical —
the first wins silently. Narrow it instead:

| need | use |
|---|---|
| every match, to count or iterate | `find_windows_by_path(parent, path)` |
| one match, chosen by label or position | `resolve_path_match(client, path, match_text=…, match_index=…)` |
| click the one you chose | `click_window(client, window)`, not `click_by_path` |

`click_path` exposes this as **Match Text** / **Match Text: Exact** / **Match Index**, and
warns when a path matched more than one element and neither param narrowed it. Match Index
applies *after* Match Text. The text is matched against the element and any element inside
it, case-insensitively, with the game's HTML wrapping stripped (`strip_html`).

Find the text in the **UI Tree Browser's Text column** — that is what distinguishes rows a
path cannot. Select a row to read its text, or press **Load Text** to fill the column for
the selected element and everything inside it; Copy Text puts the full string on the
clipboard. Text is never read for the whole tree: `maybe_text()` is one synchronous
`ReadProcessMemory` each, and doing it for several thousand windows freezes the dialog on
load. Any new per-node read in that browser has the same problem.

Resolve once and act on the window: re-walking the path for the visibility check and again
for the click can land on two different rows.
