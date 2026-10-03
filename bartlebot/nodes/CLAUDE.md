# Node conventions

Conventions for everything under `bartlebot/nodes/`, plus the two game-facing problems every node hits: telling one modal dialog from another, and pathing to a UI element when several match.

## Adding a New Node

1. Create `bartlebot/nodes/action/<name>.py` (or `condition/`)
2. Export `NODE_SPEC` (NodeSpec), `PARAM_SCHEMA` (list[ParamField]), and `async def run(machine, clients, params) -> str`
3. Register in `bartlebot/nodes/registry.py` — add to both `NODE_REGISTRY` and `NODE_SPECS`
4. Action nodes return `"SUCCESS"` / `"FAILURE"`; condition nodes return `"TRUE"` / `"FALSE"`
5. The GUI auto-generates parameter forms and tooltips from `NODE_SPEC`
6. Every `BotOptionSpec` in `required_bot_options` names its `group` — the card it is drawn
   in on Option Values and Options (`bartlebot/option_groups.py`). Reuse the group an existing
   declaration of the same option uses; `test_every_declaration_of_one_option_agrees_on_its_card`
   fails otherwise

Use those four names and no others unless the node genuinely has a third outcome. The
failure pair is `schema.FALLTHROUGH_RETURNS`: a graph that routes neither of them sends
the run to the bot's recovery state, so a node inventing `"ERROR"` gets no default and
spins to the no-match limit instead. `docs/RETURN_ROUTING.md` has the rest, including how
an author marks a return value fatal.

## A node can be cancelled at any await

Two things cancel a running node: `stop()` after its grace period, and the ambient combat
watcher when a client is pulled into a fight mid-node (#116). The watcher's cancel is not an
end: the run loop runs the same state again from the start once the fight is over, with no
result counted. So:

- **Never swallow `CancelledError`.** Catch `Exception`, not `BaseException`, and never a bare
  `except:`. A node that turns the cancel into a return value is treated as having finished,
  so the FSM moves on as if the step had worked, and the watcher fights beside a node that
  never actually stopped.
- **Clean up in `finally` or `async with`**, which run on a cancel. WizWalker's
  `mouse_handler` is an `async with`, so a click cut short still releases the hook.
- **Expect to be run again from a half-done state.** A client may be somewhere else, or a
  window may still be open. A node that cannot pick up from there should fail, which sends
  the run to recovery; it must not assume the world is as it was before its first run.

`handle_combat` and `relog` are never cancelled this way (`machine.NEVER_PREEMPTED`).
`docs/AMBIENT_COMBAT.md` has the rest of what the watcher does once the node is out of the way.

## Multi-client coordination
Use `poll_each_until_seen()` from `bartlebot/nodes/node_helpers.py` for operations that must complete across all clients (zone changes, prompts, loading screens).

A node that teleports its clients to a point until a prompt shows uses `reach_prompt()` with
a `PromptKind` (`INTERACT_PROMPT`, `DUNGEON_ENTRANCE_PROMPT`) rather than its own loop: it
clears a stale prompt via `reset_xyz`, retries up to `MAX_RETRIES`, and logs the failure.
It lived six times over until #104, and a fix to it had to be made six times.

Some world objects only serve one player at a time — a second client interacting while the
first is mid-interaction has its keypress silently swallowed. Use `take_turns()` for those:
it parks every client at `reset_xyz`, then sends them to the object one at a time, handing
each to a `turn_fn` that owns the object until it returns. The release moment is what varies
between nodes (a menu confirmed open, a zone list dismissed, a loading screen begun), which
is why it is a callback rather than a fixed sequence.

This is offered as an optional `sequential` param on `interact`, `interact_menu` and
`enter_zone_via_object`; it defaults off. `enter_zone_via_list` still reads it, but nothing
sets it any more: it stopped being a node with #36, and every list object the map knows serves
players in parallel. `apply_level_scaling`
predates the helper and is unconditionally sequential against its one hardcoded station.
`handle_level_scaling` reuses that loop (`apply_elixir`), so it is sequential too.

`enter_zone_via_object`'s parallel path also falls back to turns on its own: when some clients
went through and the rest are still standing there after the re-press window, the rest take
turns and a warning names the object, since nothing says an object serves one player at a time
until someone has watched it happen. It never gives a turn to a client that is loading (#70).
A sequential turn that fails for one client no longer fails the step for the group; the arrival
check decides, as it does for the parallel path.

`wait_for_zone_with_recovery`'s teleport-to-player recovery can be switched off for everything
inside a block with `node_helpers.without_straggler_recovery()`. Zone navigation does this
around every step it runs, because a straggler ported partway through a route lands in a zone
the others are about to leave; it ports them once, at the end (see `docs/ZONE_NAVIGATION.md`).

Pass `park_after_turn=False` when the interaction removes the client itself (a zone
transition) — otherwise the helper aims a teleport at a client already on a loading screen.

**One zone is not one realm.** Players on different realms enter separate dungeons and battles,
and a zone name cannot tell them apart. Where a node needs its players together, check it by
sight -- `read_sightings`, and `ensure_together` to repair -- and let the `Player Realms` option
decide whether they should be (`docs/PLAYER_REALMS.md`). A node that moves players declares
`player_realms.SPEC`; one that puts them all on one realm by nature declares `merges_players`.
A zone-entry node calls `note_realm_peers` just before its transition, so straggler recovery in
a Separate bot knows who shares whose realm.

### Never act on a client that is loading

A client on a loading screen reports the zone it *left*, the position it left from, and the
UI it left behind, for as long as the screen is up — and a client whose trigger has just
fired has not started its screen yet. Both read exactly like a client that never moved, so a
retry loop watching anything other than `is_loading()` will chase one that is already on its
way. The retry is then applied when it lands: a teleport issued mid-load pulls the client
straight back out of the zone it just reached, which is issue #70 and looks on screen like a
successful transition being undone.

Two guards, and a node that moves clients between zones wants whichever fits:

- `skip_while_loading(action_fn)` wraps `poll_each_until_seen`'s `action_fn` whenever
  `check_fn` is *not* `is_loading()` — a zone name, a position, a window. A loop whose
  `check_fn` is `is_loading()` already counts a loading client as seen and needs nothing.
- `wait_for_zone_with_recovery()` holds off both its teleport-to-player recovery and its
  own timeout verdict while any client that is not yet in the destination is loading, and
  gives a non-loading straggler `ZONE_SETTLE_GRACE` to start loading before treating it as
  stuck. Callers get this for free; a caller passing `settle_grace=0` is opting out of the
  second half only.

`poll_until(none_loading(clients), ...)` is not this guard. It answers "has every screen
finished", and a screen that has not started yet answers yes — which is how a stale zone
read reaches the check that follows it.

### A port to a player does not always load

Porting to a player shows a loading screen only when it changes zone or realm. Within one
zone and realm the player's position jumps onto the target instead, and a port onto someone
you are already standing beside does nothing visible at all. A wait that counts only loading
screens re-ports a player that already arrived, forever (#69), and cannot tell a player that
was already there from one whose port failed (#14).

`node_helpers.port_clients` accepts all three, so start any port to a player through it
(`port_to_friend`, `group_manager.port_to_group_member`) rather than watching `is_loading()`
yourself. Pass `maybe_there` only for clients with a reason to already be at the target: those
alone may be presumed there after a re-issue, and every presumption should be logged by player
number. Realm cannot be read without opening the spellbook, so never skip porting a player for
sharing the target's zone or coordinates. When the target is one of ours, use
`gather_at_player`, which checks arrival against the target's own zone and position.

## Comparing a zone a bot names with one a client is in

Always through `node_helpers.zone_matches`, never `==` or `!=`. Besides globs, it treats every
duplicate of one place as the same zone, comparing both sides as `zone_names.canonical_zone`:

- **A Challenge Mode copy** (#143) is a separate zone whose id is the ordinary one plus
  `_Challenge`, `_Hard` or `_Adv`. Its canonical zone is the ordinary one.
- **A gauntlet room in another level band.** The game picks the band from the players' levels,
  and each band is its own zone (`PA_Gauntlet_40/PA_Gauntlet_40_03`). Its canonical zone is the
  same room in the lowest band. The families are declared in `zone_names.TIERED_GAUNTLETS`,
  never found by a suffix: `_T1`…`_T9` and Treasure Tower's `_L01`…`_L30` are different rooms.

A raw `!=` in `enter_zone` read a client standing in the copy of `from_zone` as having already
left it. Two clients' live zones can be compared directly, since all of them are in one copy.

Anything that writes a client's zone into a bot writes its canonical zone, as Record and Fill
from Player do, so no bot needs a glob to take in a duplicate.

Skeleton keys look like the same problem and are not: a key's boss changes with the players'
levels, but inside one zone. That is an enemy name, not a zone.

## Keeping a session log readable

A five-hour four-client run writes tens of thousands of lines, and the difference
between a log you can debug from and one you scroll past is entirely in what does
*not* get written twice. Three rules keep it that way:

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
`teleport_to_friend` reported every dungeon exit as an unreachable friend.

Captions come from `bartlebot/data/dialog_captions.json`, all seven locales per key
— memory holds the *resolved* text, so an English substring match fails silently on
any other locale. To add a dialog: append to `KNOWN_KEYS`, run
`uv run python tools/dump_dialog_captions.py`, and commit the regenerated JSON.
`--verify` re-checks the snapshot for keys whose text no longer identifies them
uniquely. An unrecognized caption returns `None`, and callers must fall back rather
than treat it as "no dialog".

## NPC dialogue, and side quests

NPC dialogue is a different window, `WorldView/wndDialogMain`, and is pressed only
through `node_helpers.advance_dialog_once` — never a bare SPACEBAR or a click on
`ADVANCE_DIALOG_PATH`. A side quest's offer page puts ACCEPT on the right button, the
one SPACEBAR presses on every other page, so pressing blind takes every side quest an
NPC offers. The helper clicks DECLINE (`DECLINE_QUEST_PATH`, the left button) instead.

That check reads which buttons are showing, which the section above forbids for
modals. It holds here because the left button appears on the offer page and nowhere
else in this window. It is also read only while the right button shows, because a
child's visibility flag outlives its closed parent. The helper holds a per-client lock
across each read and press, so the ambient watcher and a node's own advance loop cannot
both press the page before an offer.

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
