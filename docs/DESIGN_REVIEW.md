# BartleBot Design Review

## Review Framework

### Quality Axes

1. **Modular Architecture** — New dungeons/scripts plug in cleanly
2. **Reliable Execution** — No brittle timing or race conditions
3. **Observability** — Logs, debugging, replayability
4. **Extensibility** — New behaviors don't require rewriting core logic
5. **Safety Controls** — Fail-safes, anti-ban considerations, stop conditions
6. **Testing** — Unit tests actually test what they claim; coverage is adequate
7. **Documentation** — Comments are frequent and helpful; auto-doc generation is feasible
8. **Consistency** — No conflicting language or inconsistent patterns across the codebase

### Deep-Dive Lenses

- **A. Architecture Audit** — Scalability, responsibility separation, 10x complexity breaking points
- **B. Abstraction Quality** — Real abstractions vs wrappers, interface stability, coupling
- **C. State Management & Flow Control** — Explicit vs implicit state, transition predictability, side effects
- **D. Reliability / Failure Modes** — UI detection failure, input drop, game state desync
- **E. Code Smell Detection** — Dead abstractions, over-engineering, inconsistent patterns, hidden duplication
- **F. Long-term Maintainability** — What becomes painful in 3 months

---

## Stage 1: Core Engine

Scope: How BartleBot interfaces with WizWalker/WizSprinter — input simulation, game memory hooking, timing, error recovery, and multi-client coordination.

### Understanding the Layers

```
BartleBot Nodes          ← REVIEW TARGET: timing, error handling, coordination
  └─ node_helpers.py     ← REVIEW TARGET: polling, window tree, shared utilities
      └─ WizSprinter     ← Given: safe wrappers (returns bool/None, catches exceptions)
          └─ WizWalker   ← Given: raw memory hooks, input simulation, no retries
              └─ Game    ← Opaque: Win32 process, memory structs, message loop
```

**What WizWalker provides:**
- Assembly code injection hooks into game memory (fragile to game updates — pattern scanning breaks on any binary change)
- SendMessage-based keyboard input (no delivery guarantee — game message loop may be blocked)
- Direct-memory-write teleportation with collision NOP patching (patches two JE instructions with NOPs during teleport)
- Pointer-based window tree traversal (addresses can become invalid at any time)
- All memory ops run in thread pool executor (truly async, but unbounded thread creation possible)
- No retry logic. Raises `MemoryReadError`, `PatternFailed`, `HookNotReady`, etc.

**What WizSprinter adds:**
- Safe wrappers that return `bool`/`None` instead of raising (teleport, entity lookup, potion use)
- Combat DSL system (`SprintyCombat`) with card selection, target resolution, fizzle detection
- Entity finding with distance/safety filtering (`find_closest_mob`, `get_health_wisps`)
- Built-in cast delays (0.4-0.6s between spells) but no zone-wait delays

**What BartleBot must handle:**
- All timing, retries, multi-client coordination, state verification, error recovery, and user-facing logging

---

### Findings by Axis

#### 1. Modular Architecture — GOOD

**Strengths:**
- Node registry pattern works well. Adding a new node = one file with `run()` + `NODE_SPEC`. No core changes needed.
- `node_helpers.py` provides shared utilities (polling, window tree, zone checks) that nodes compose cleanly.
- `paths.py` centralizes all window tree path constants — single place to update if game UI changes.
- Combat config is injected via `CombatConfigProvider` interface, not hardcoded.

**Concerns:**
- `handle_potions.py` is doing too much — it's a mini-bot inside a node (navigate home, find NPC, buy potions, navigate back). This will be the first node to break under complexity growth. It hardcodes Hilda Brewer's coordinates and the dorm→ravenwood→commons zone chain.
- Node helpers mix abstraction levels: `wait_until()` (generic polling) lives alongside `all_at_dungeon_entrance()` (game-specific). Not a problem yet, but the file will grow.

#### 2. Reliable Execution — MIXED

**Strengths:**
- The **"ever seen" pattern** for multi-client operations is well-designed. Rather than requiring all clients to be in a state simultaneously, it tracks which clients have *ever* triggered an event (loading screen, prompt) and re-presses keys for stragglers. This handles real-world timing variance gracefully.
- Zone validation on node entry catches state drift early.
- Retry loops with `MAX_RETRIES=3` and reset coordinates (`go_to_zone`, `enter_dungeon`, `interact`) provide recovery paths.
- `confirm_dungeon_warning_dialog()` polls for at least the full delay period before giving up — doesn't false-negative on slow dialogs.

**Concerns:**

***Hardcoded timing constants everywhere:***

Every node defines its own timeout constants as module-level variables. These are not configurable by the user or adaptable to system performance:

| Constant | Value | Used in |
|---|---|---|
| `POLL_INTERVAL` | 0.25s | 11 nodes |
| `PROMPT_TIMEOUT` | 5.0s | 4 nodes |
| `ZONE_CHANGE_TIMEOUT` | 30.0s | 5 nodes |
| `MAX_RETRIES` | 3 | 5 nodes |
| `TRIGGER_TIMEOUT` | 10.0s | 3 nodes |
| `BUTTON_TIMEOUT` | 35.0s | 3 nodes |

These values are reasonable defaults, but they're duplicated across files. If the game slows down (heavy zone, slow PC), there's no way to adjust without editing source code. A slow system might need `ZONE_CHANGE_TIMEOUT=60s`; a fast one might want `POLL_INTERVAL=0.1s`.

***No confirmation that input was received:***

WizWalker's `send_key()` uses `SendMessage` which has no delivery guarantee. The game's message loop might be blocked (loading, combat animation). BartleBot's nodes generally don't verify that a key press had the intended effect — they wait for the *consequence* (zone change, dialog appears) but if the key was silently dropped, they'll timeout and return FAILURE rather than retry the key press. The "ever seen" pattern mitigates this for some nodes by re-pressing keys for clients that haven't triggered, but not all nodes use it.

***Teleport has no post-verification in some nodes:***

`go_to_zone` teleports to a bounding box to trigger a zone transition, but if the teleport itself silently fails (WizWalker's teleport can fail if the hook isn't ready), the node will wait the full `ZONE_CHANGE_TIMEOUT` before retrying. `go_to_xyz` does verify position post-teleport (`POSITION_MARGIN=10.0`), which is good.

***Background watcher coordination:***

The ambient combat watcher checks `in_battle()` every 0.25s and runs SprintyCombat if combat is detected outside a `handle_combat` node. But the coordination mechanism is just checking if the current FSM state's `node_type` is `"handle_combat"` — if a node is doing pre-combat setup (teleporting to mob, waiting for combat entry), the ambient watcher could fire prematurely and interfere.

#### 3. Observability — ADEQUATE but INCOMPLETE

**Strengths:**
- Every node logs its entry with key params: `machine.log(f"go_to_zone: '{from_zone}' → '{to_zone}' ...")`.
- Step-by-step progress logged at DEBUG level (teleport attempts, timeout warnings, zone checks).
- Failures logged at WARNING with clear reason: `"FAILURE: interact prompt never appeared after 3 attempts"`.
- Drop detection logged per-item with client index.

**Concerns:**

***No transition-level logging in the machine:***

`BotMachine.run()` calls `on_transition(prev_state, result, next_state)` but only the GUI callback uses this. There's no persistent log of the FSM trace (sequence of states visited, results returned, time spent). A post-mortem analysis requires reading the GUI log, which mixes node-internal messages with state transitions.

***Background watcher activity is invisible:***

The ambient dialog watcher and ambient combat watcher run silently. If the dialog watcher presses SPACEBAR at the wrong time (dismissing a dialog the node was about to read), there's no log entry to explain what happened. The ambient combat watcher logs drops but not combat entry/exit.

***No timing information:***

Logs don't include timestamps or durations. You can't tell from logs whether a node took 2 seconds or 2 minutes. The GUI shows elapsed time for the whole run, but not per-state.

***Return values not logged by the machine:***

The machine stores `last_result` but doesn't log it. You have to infer the result from the node's own logging, which is inconsistent — some nodes log their return value explicitly, others don't.

#### 4. Extensibility — GOOD FOUNDATION, SOME LIMITS

**Strengths:**
- Node system is genuinely extensible — new nodes require zero core changes.
- Bot options system (`required_bot_options` auto-injected by nodes) is elegant.
- Combat config is fully pluggable via `BaseCombatBackend` interface.
- `to_machine_config()` cleanly transforms the editor model into the runtime model.

**Concerns:**
- The `_result_is_X` / `_flag_X` dynamic condition system via `__getattr__` is clever but limited. You can't express "result is SUCCESS AND option X is true" — conditions are OR'd across transitions (first matching transition wins). This is fine for simple FSMs but will be a bottleneck for complex decision logic.
- No inter-node data passing beyond `last_result` (a single string). If a future node needs to pass structured data (e.g., "which mob did we kill?", "what zone are we in?"), there's no mechanism. `machine.drops` is a special case but not generalizable.
- Sub-graphs/nested FSMs (H-FSM) would require significant `machine.py` changes — the current loop assumes a flat state space.

#### 5. Safety Controls — MINIMAL

**Strengths:**
- "Safety Delay" bot option (default 3.0s) provides a configurable wait for dialog/warning detection.
- `handle_potions` checks gold (charge unchanged after purchase = out of gold).
- Zone validation on node entry prevents operating in wrong zone.
- Stop mechanism clears `_stop_event` which is checked at loop top.

**Concerns:**

***No hard limits:***

No max iterations, max runtime, or idle detection. A bot with a self-loop on FAILURE will retry forever. A node that hangs (WizWalker memory read blocks indefinitely in executor) will block the machine loop with no timeout.

***Stop is not instant:***

`machine.stop()` sets an event, but it's only checked at the top of the run loop — between node executions. If a node is in a 60-second dialog timeout or a 30-second zone change timeout, stop won't take effect until that timeout completes. There's no `asyncio.Task.cancel()` to interrupt a running node.

***No anti-detection randomization:***

All timing is deterministic — `POLL_INTERVAL=0.25s`, key press duration `0.1s`, delays are fixed. A detection system could fingerprint the bot by its perfectly regular timing patterns. No jitter, no randomized delays.

***No disconnect/ban detection:***

No node checks for game disconnect, login screen, or ban notification. If the game disconnects mid-run, `MemoryReadError` will propagate from the next API call, get caught by the machine's except block, and the node will return FAILURE — but the bot will continue trying subsequent states against a dead client.

***Background watchers can't be stopped mid-action:***

The ambient combat watcher acquires `_ambient_combat_lock` and runs full `SprintyCombat`. If the user hits Stop during ambient combat, the lock prevents cleanup until combat finishes.

#### 6. Testing — NOT EVALUATED IN THIS STAGE

(Will be reviewed in a dedicated testing stage.)

#### 7. Documentation — SPARSE

- Nodes have no docstrings on their `run()` functions.
- `NodeSpec.description` serves as the only documentation per node, but it's a one-liner for the GUI tooltip, not a behavioral contract.
- `node_helpers.py` functions have no docstrings explaining their polling semantics, return values, or failure modes.
- The "ever seen" pattern is used in 5+ nodes but never explained in a comment — you have to reverse-engineer it from the loop structure.
- Timeout constants are defined but never documented (why 5s for prompts? why 30s for zone changes?).

#### 8. Consistency — MOSTLY CONSISTENT, SOME DRIFT

**Strengths:**
- All nodes follow the same structural pattern: validate zone → perform action → poll for result → return SUCCESS/FAILURE.
- Logging format is consistent: `"node_name: message"` prefix.
- Multi-client operations consistently use `asyncio.gather()`.
- Window tree paths are all centralized in `paths.py`.

**Concerns:**

***Timeout constant naming:***

Same concept, different names across nodes:
- `PROMPT_TIMEOUT` vs `_PROMPT_TIMEOUT` (leading underscore inconsistency)
- `ZONE_CHANGE_TIMEOUT` vs `_HOME_LOADING_TIMEOUT` (semantic vs. mechanism naming)
- `BUTTON_TIMEOUT` vs `_DORM_BUTTON_TIMEOUT` vs `MARK_COOLDOWN_TIMEOUT` (all 35s, all the same game cooldown)

***Return value vocabulary:***
- Action nodes return `"SUCCESS"` / `"FAILURE"`
- Condition nodes return `"TRUE"` / `"FALSE"`
- This is fine as a convention but isn't enforced — nothing prevents a node from returning `"MAYBE"` or misspelling `"SUCESS"`.

***Error handling varies:***
- Some nodes log FAILURE at WARNING level, others at DEBUG.
- Some nodes catch specific exceptions from WizWalker, others rely entirely on the machine's catch-all.
- `handle_potions` does extensive error recovery; `go_to_xyz` does almost none.

---

### Deep-Dive Findings

#### C. State Management & Flow Control

**State is mostly explicit** — `last_result` and `machine.drops` are the only shared runtime state. However:

- The ambient combat watcher modifies `machine.drops` concurrently with node execution. If a `handle_combat` node is running and the ambient watcher fires on a different client, both append to `drops` simultaneously. Python list append is thread-safe (GIL), but this is accidental safety, not designed safety.
- `machine.state` (the FSM's current state) is set by the transitions library, not by BartleBot code. The machine loop reads it, but background watchers also read it to decide whether to act. No lock protects this read.

#### D. Reliability / Failure Modes

| Scenario | What Happens | Assessment |
|---|---|---|
| **Client crash mid-node** | Next WizWalker call raises `MemoryReadError` → caught by machine → FAILURE → FSM continues on dead client | BAD: should detect and remove dead client |
| **Key press dropped** | Node waits for consequence, times out, retries or FAILURE | OK: "ever seen" pattern helps, but not all nodes use it |
| **Zone desync** | Node validates zone on entry → immediate FAILURE | GOOD: early detection |
| **Teleport silently fails** | Depends on node: `go_to_xyz` verifies position, `go_to_zone` waits for zone change | MIXED: inconsistent verification |
| **Dialog dismissed by ambient watcher** | Node's dialog detection fails (dialog already gone) → timeout → FAILURE | BAD: no coordination between ambient watcher and active node |
| **Combat entered during non-combat node** | Ambient combat watcher handles it | GOOD: but watcher can't be stopped |
| **Game disconnect** | `MemoryReadError` cascade → repeated FAILURE returns → bot loops on error states | BAD: no disconnect detection |

#### E. Code Smells

- **`handle_potions.py` is a god-node** (~200 lines of navigation, NPC interaction, shop UI automation, zone transitions). Should be decomposed or turned into a sub-graph.
- **Timeout constant duplication**: The value `35.0` (30s game cooldown + 5s margin) appears in 3 nodes under 3 different names. Should be a shared constant with a comment explaining the game mechanic.
- **The "ever seen" pattern is copy-pasted** across 5+ nodes rather than abstracted into a helper. The loop structure is identical each time: track set of triggered clients, re-press key for untriggered ones, poll until all triggered or timeout.

#### F. Long-term Maintainability

**Will become painful:**
- Hardcoded timeouts will need tuning as more dungeons/scenarios are added. A central timing config would prevent per-node spelunking.
- The ambient watcher coordination (checking `node_type == "handle_combat"`) is fragile. Adding new combat-related nodes requires updating the watcher's exclusion check.
- `node_helpers.py` will grow into a grab-bag. Should be split by concern (polling, window_tree, zone_checks) before it gets too large.
- No mechanism to gracefully handle dead clients means multi-client bots will degrade silently when one client disconnects.

---

### Summary: Priority Issues

| # | Issue | Severity | Axis |
|---|---|---|---|
| 1 | No dead client detection during bot run | High | Reliability, Safety |
| 2 | No disconnect/ban screen detection | High | Safety |
| 3 | Stop can't interrupt a running node | High | Safety |
| 4 | All timing constants hardcoded, duplicated | Medium | Reliability, Consistency, Maintainability |
| 5 | "Ever seen" pattern duplicated instead of abstracted | Medium | Consistency, Maintainability |
| 6 | Ambient watcher can interfere with active nodes | Medium | Reliability |
| 7 | No FSM trace logging (state→result→state with timestamps) | Medium | Observability |
| 8 | No anti-detection randomization (timing jitter) | Medium | Safety |
| 9 | Background watcher activity not logged | Low | Observability |
| 10 | Return values not type-constrained | Low | Consistency, Safety |
| 11 | `handle_potions` is a god-node | Low (for now) | Modular Architecture, Maintainability |
| 12 | No node docstrings | Low | Documentation |

---

## Stage 2: Behavior Layer (FSM)

Scope: The finite state machine implementation — how bot configs are represented, how the machine loop drives execution, how transitions are evaluated, how the orchestration layer bridges GUI to engine. Files reviewed: `machine.py` (288 lines), `schema.py` (117 lines), `app_controller.py` (330 lines), `registry.py` (58 lines), `test_machine.py` (357 lines).

### Understanding the FSM

```
BotConfig (Pydantic)
  ├─ states: list[StateModel]     ← id, node_type, params, option_overrides
  ├─ transitions: list[TransitionModel]  ← source, dest, when, flag, label
  └─ to_machine_config()          ← converts to flat dict for BotMachine
        │
        ▼
BotMachine (transitions.AsyncMachine wrapper)
  ├─ run() loop                   ← while not stopped: execute node → advance trigger
  ├─ __getattr__                  ← dynamic _result_is_X / _flag_X condition methods
  ├─ get_option()                 ← per-node overrides → bot-wide fallback
  ├─ Background watchers          ← ambient dialog + ambient combat (asyncio tasks)
  └─ Callbacks                    ← on_state_change, on_transition, on_drop, on_log
        │
        ▼
AppController (QObject)
  ├─ _start_bot()                 ← creates BotMachine, awaits run()
  ├─ Callback receivers           ← forward machine events to GUI
  ├─ _client_loop()               ← polls for new/dead WizSprinter clients
  └─ File operations              ← load/save BotConfig JSON
```

**How a tick works:**
1. `run()` reads current `self.state` and looks up `state_def` from `_state_map`
2. Fires `on_state_change` callback
3. Looks up node function from `_registry[node_type]` and awaits it
4. On exception: `print(traceback.format_exc())`, sets `last_result = "FAILURE"`
5. Checks for terminal state (no outgoing transitions) → stop
6. Calls `await self.advance()` — transitions library evaluates conditions on all outgoing transitions from current state, fires the first match
7. Fires `on_transition` callback with (prev_state, result, new_state)

**How conditions work:**
- `to_machine_config()` converts `when="SUCCESS"` → `conditions=["_result_is_SUCCESS"]`
- `to_machine_config()` converts `flag="repeat"` → appends `"_flag_repeat"` to conditions list
- `__getattr__` dynamically creates these condition methods at call time
- Multiple conditions on one transition are AND'd by the transitions library
- Multiple transitions from one state are evaluated in order; first match wins

---

### Findings by Axis

#### 1. Modular Architecture — GOOD

**Strengths:**
- Clean separation: schema defines data, machine drives execution, controller bridges to GUI. Each has a single responsibility.
- `to_machine_config()` is the only coupling point between schema and machine — machine takes a plain dict, no Pydantic dependency.
- Node registry is a simple `dict[str, Callable]` — maximum flexibility, no base class requirements.
- AppController doesn't reach into machine internals — communicates entirely via callbacks.

**Concerns:**
- `machine.py` imports `handle_combat` internals (`_resolve_combat_dsl`, `_run_combat`, `_split_combat_config`) for the ambient combat watcher. The machine should not know about specific node implementations — this breaks the clean separation. If a second combat-like node is added, the ambient watcher needs modification.
- `app_controller.py` accesses `self._handler._managed_handles` (private attribute of WizSprinter) in the client loop error path (line 249). This couples to WizSprinter internals.

#### 2. Reliable Execution — SIGNIFICANT GAPS

**Strengths:**
- The run loop correctly handles stop-while-paused (sets `_resume_event` to unblock).
- Exception in node → `FAILURE` result → FSM continues (doesn't crash).
- Terminal state detection prevents infinite loops on states with no outgoing transitions.
- Tests verify the key behaviors: advance on success, retry on failure, pause/resume, stop semantics.

**Concerns:**

***CRITICAL: Silent fall-through when no transition matches:***

When `await self.advance()` fires, the transitions library evaluates conditions on all outgoing transitions. If *none* match (e.g., node returns `"PARTIAL"` but only `"SUCCESS"` and `"FAILURE"` transitions exist), the trigger **silently does nothing** — `self.state` remains unchanged, and the machine loops back to execute the same node again. There's no log, no warning, no detection. The FSM will loop forever on the same state with the same result, burning CPU. This is the single most dangerous bug in the FSM.

The transitions library's `ignore_invalid_triggers=False` only catches triggers fired from states that have no transitions for that trigger name at all — it does NOT catch the case where transitions exist but their conditions all evaluate to False.

***CRITICAL: Callback exceptions crash the FSM:***

The `on_state_change`, `on_transition`, `on_drop`, and `on_log` callbacks are invoked without try/except (lines 138-139, 157-158). If any callback raises, the exception propagates into the run loop, which has no handler for it — the machine dies. Since callbacks are provided by AppController and ultimately execute GUI code, any PyQt6 signal error will kill the bot.

***HIGH: Exception logging bypasses the GUI:***

Line 145: `print(traceback.format_exc())` — when a node raises an unhandled exception, the traceback goes to stdout, not to `self.log()`. The user sees "FAILURE" in the GUI log but has no idea why. The `print()` output goes to the console that launched the app, which is typically invisible to the user.

***MEDIUM: Ambient combat watcher race with FSM state:***

The ambient combat watcher reads `self.state` (line 208) without synchronization. Between reading the state and acquiring `_ambient_combat_lock`, the FSM could advance into a `handle_combat` node. The re-check after acquiring the lock (line 230) partially mitigates this, but there's a window where both the watcher and a `handle_combat` node could be running combat simultaneously on different clients.

Since this is single-threaded asyncio, true data races are impossible — but logical races can occur across `await` points. The lock acquisition at line 226 (`async with self._ambient_combat_lock`) is an await point where the FSM run loop can advance.

#### 3. Observability — POOR at the FSM level

**Strengths:**
- `on_state_change` and `on_transition` callbacks provide hooks for GUI visualization.
- Tests verify that transition callbacks receive correct arguments.

**Concerns:**
- **No FSM-level logging at all.** The machine itself never calls `self.log()` during normal operation — not on state entry, not on transition, not on advance. All logging comes from individual nodes. If a node doesn't log, that tick is invisible.
- **Exception traceback goes to stdout** (covered above) — invisible to GUI users.
- **No timing.** No measurement of how long each node takes. A node that hangs for 5 minutes looks identical to one that completes in 100ms from the FSM's perspective.
- **`last_result` not logged by machine.** The result is stored but never emitted to `on_log`. You have to rely on nodes logging their own return values, which they do inconsistently.
- **Silent condition fall-through** (covered above) — the most dangerous failure mode is also the least observable.

#### 4. Extensibility — GOOD with caveats

**Strengths:**
- Dynamic condition generation (`__getattr__`) means new condition types require zero machine changes.
- The `flag` system for bot options is a clean way to add behavioral branching without modifying transition evaluation.
- `get_option()` with per-node overrides is well-designed — local overrides take priority, then bot-wide defaults, reading live from BotConfig so GUI changes propagate.

**Concerns:**

***Condition logic limited to AND within a transition, OR across transitions:***
- Multiple conditions on one transition are AND'd (transitions library behavior).
- Multiple transitions from one state are tried in order; first match wins (implicit OR).
- You cannot express: "if SUCCESS and NOT flag_repeat, go to state_c" — you'd need a `_not_flag_repeat` condition, which `__getattr__` doesn't generate.
- Negation, OR within a transition, or complex boolean logic requires machine changes.

***No inter-node data passing:***
- `last_result` is a single string. Nodes can't pass structured data to their successors.
- `machine.drops` is a special-purpose list, not a general mechanism.
- If a future node needs context from a previous node (e.g., "which zone are we in?" or "how many retries have we done?"), there's no blackboard, shared context dict, or state-attached data.

***`transitions` library features not utilized:***
- `final=True` states and `on_final` callbacks — machine manually checks for terminal states by scanning the transition list (O(n) per tick).
- `on_enter_<state>` / `on_exit_<state>` callbacks — could replace manual `on_state_change`.
- `on_exception` callback — could replace the try/except in the run loop.
- `before_state_change` / `after_state_change` — could provide FSM-level logging.
- `HierarchicalAsyncMachine` — could enable sub-graphs without custom code.
- `prepare` / `before` / `after` transition callbacks — could enable per-transition hooks.

#### 5. Safety Controls — WEAK at the FSM level

**Strengths:**
- `stop()` sets the flag AND unblocks pause — can't deadlock a paused bot.
- Terminal state detection prevents the machine from advancing into undefined states.

**Concerns:**
- **No max iterations.** A cyclic FSM runs forever until manually stopped. No configurable `max_ticks`, `max_runtime`, or `max_consecutive_failures` to auto-halt.
- **No idle detection.** If the same state returns the same result N times in a row (because no transition matches, or because the node is stuck), there's no detection or circuit-breaker.
- **Stop only checked between nodes.** A long-running node (30s zone change timeout, 60s combat) blocks stop for its full duration. No `asyncio.Task.cancel()` or stop-event checking within nodes.
- **Background watchers keep running during pause.** `pause()` only blocks the run loop's `_resume_event.wait()`. The ambient dialog watcher and ambient combat watcher continue running, potentially interfering with a paused bot's state.
- **Drop list grows unbounded.** `self.drops` appends forever with no cap or rotation.

#### 6. Testing — GOOD COVERAGE of happy paths, MISSING edge cases

**Strengths:**
- 13 tests covering: condition methods, FSM transitions (success/failure/mixed), pause/resume, stop (before/during/after), callbacks, exception handling.
- Tests use the exact same config format that `to_machine_config()` produces — testing the real path.
- Good structural variety: tests for initial states, self-loops, transitions, lifecycle.

**Concerns:**

***Not tested:***
- Silent fall-through (node returns unmapped value) — this is the most dangerous bug and has no test.
- Callback exceptions — no test for what happens when `on_state_change` or `on_transition` raises.
- Terminal state detection — no test for states with no outgoing transitions.
- `get_option()` — no test for per-node overrides or missing options.
- `_flag_X` conditions — no test for flag-based transitions.
- Ambient watchers — no tests at all.
- Multiple transitions with overlapping conditions — order-dependent behavior not tested.

***Test quality:***
- `test_run_can_be_stopped_externally` uses `task.cancel()` + `CancelledError` — it's testing asyncio cancellation, not the machine's stop mechanism.

#### 7. Documentation — MACHINE IS WELL-DOCUMENTED, SCHEMA SPARSE

**Strengths:**
- `BotMachine` has a good class docstring explaining the FSM model.
- `__getattr__` has a clear docstring explaining the dynamic condition system.
- `get_option()` documents the override priority chain.

**Concerns:**
- `TransitionModel` fields have inline comments but no class docstring.
- `to_machine_config()` has no docstring explaining the transformation.
- `AppController` has no docstrings on any method.
- No documentation of failure modes or the ambient watcher coordination model.

#### 8. Consistency — MIXED

**Strengths:**
- Callback naming is consistent: `on_state_change`, `on_transition`, `on_drop`, `on_log`.
- All callbacks are optional (default `None`) and checked before invocation.
- Schema uses consistent Pydantic patterns throughout.

**Concerns:**
- **`print()` vs `self.log()`**: Exception handling uses `print(traceback.format_exc())` while all other logging uses `self.log()`. Same inconsistency in `app_controller.py`.
- **Naming drift**: `_stop_flag` (asyncio.Event) vs `_resume_event` (also asyncio.Event) — inconsistent suffixes for the same type.
- **Option access**: `get_option()` iterates `self._bot_config.options` list (O(n)), inconsistent with `_state_map` dict lookup (O(1)).
- **`label` field**: `TransitionModel.label` is preserved through `to_machine_config()` but never used by `BotMachine`.

---

### Deep-Dive Findings

#### A. Architecture Audit

**Responsibility separation is clean** between schema, machine, and controller. The main architectural issue is the ambient combat watcher importing specific node internals (`handle_combat._resolve_combat_dsl`, `_run_combat`, `_split_combat_config`). This creates a circular dependency concern: `machine.py` → `handle_combat.py`, while `handle_combat.py` receives the machine as an argument. The combat handling logic should be extracted into a shared module.

**10x complexity breaking point:** The flat FSM will hit its limit when bots need conditional sub-routines (e.g., "if health < 50%, run a potion sub-graph, then return to where you were"). The current architecture has no stack, no sub-graphs, and no way to interrupt the current state sequence for a side-quest. `HierarchicalAsyncMachine` from the transitions library could address this.

#### B. Abstraction Quality

- `BotMachine` is a genuine abstraction — provides a meaningful FSM interface, not just a wrapper. Dynamic condition generation is a real design choice that simplifies the config format.
- `BotConfig.to_machine_config()` is a good data transformation — converts user-facing model to engine-facing model.
- `AppController` is more orchestrator than abstraction — appropriate for its role.
- `NODE_REGISTRY` as a plain dict is the right level of abstraction.

#### C. State Management & Flow Control

- **Explicit state:** `self.state` (managed by transitions library), `self.last_result`, `self.drops`, `self._stop_flag`, `self._resume_event`.
- **Implicit state:** The ambient watcher tasks read `self.state` to decide whether to act — creates implicit coupling between the run loop and watchers.
- **Side effects:** `on_state_change` fires before the node runs (line 138-139), not after. Correct for visualization ("executing state X") but could be confusing if used for other purposes.
- **Transition order matters:** Multiple transitions from one state are evaluated in list order. The config doesn't make this explicit — users might not realize that transition ordering determines priority.

#### D. Reliability / Failure Modes (FSM-specific)

| Scenario | What happens | Assessment |
|---|---|---|
| **Node returns unmapped value** | `advance()` fires, no condition matches, trigger silently does nothing, same state re-executes forever | CRITICAL: infinite silent loop |
| **Callback raises** | Exception propagates up, kills the run loop, watchers cancelled | CRITICAL: unguarded |
| **Node hangs indefinitely** | Run loop blocks forever at `await node_fn(...)`, stop flag never checked | HIGH: no timeout on node execution |
| **All transitions have conditions** | If none match, same as unmapped value — silent infinite loop | HIGH: common config error |
| **Duplicate state IDs in config** | `_state_map` dict will silently keep the last one | MEDIUM: no validation |
| **Transition references nonexistent state** | transitions library will raise at machine construction time | OK: caught early |
| **Stop during ambient combat** | Lock held until combat finishes, then watcher loop sees stop flag | OK: delayed but safe |

#### E. Code Smells

- **God-method tendency in `run()`**: Handles state lookup, node dispatch, exception handling, terminal detection, transition firing, and callback invocation in a single method.
- **`print()` for error logging**: Should be `self.log()` with `LogLevel.ERROR`.
- **Terminal state check is O(n)**: `any(t["source"] == state_id for t in self._config["transitions"])` scans all transitions every tick. Should be precomputed as a set at construction time.
- **`label` field passed through but unused**: Either use it (for logging) or strip it during transformation.
- **`_ambient_combat_lock.locked()` check before `async with`**: Performance optimization that looks like a race condition to readers. Needs a comment.

#### F. Long-term Maintainability

**Will become painful:**
- **Silent fall-through** will cause mysterious "bot stuck" reports — no log, no error, just a bot that appears frozen on one state.
- **No FSM-level metrics** means no way to detect performance regressions or identify slow nodes.
- **Flat FSM only** means complex bots will need workarounds (duplicate state sequences, condition flags to simulate sub-routines).
- **Ambient watcher coupling to `handle_combat` internals** means every combat handling change must be verified against two call sites.
- **No schema migration** — `BotConfig` version field exists but there's no migration system.

---

### `transitions` Library Utilization

The transitions library offers significantly more capability than BartleBot currently uses:

| Feature | Current BartleBot approach | Benefit if adopted |
|---|---|---|
| `final=True` states | Manual scan of transition list per tick | O(1) terminal detection, `on_final` callback |
| `on_enter_<state>` / `on_exit_<state>` | Manual `on_state_change` callback | Per-state hooks without modifying node code |
| `on_exception` | `try/except` in run loop + `print()` | Centralized error handling with proper logging |
| `before_state_change` / `after_state_change` | None | FSM-level logging, metrics, trace recording |
| `HierarchicalAsyncMachine` | Flat FSM only | Sub-graphs, nested state machines |
| `prepare` / `before` / `after` on transitions | None | Per-transition hooks (logging, validation) |
| Transition `unless` parameter | Not available via current schema | Negation conditions without custom code |
| State `on_enter` / `on_exit` | Not used | Resource setup/teardown per state |

---

### Summary: Priority Issues

| # | Issue | Severity | Axis |
|---|---|---|---|
| 1 | Silent fall-through when no transition condition matches — infinite loop with no warning | Critical | Reliability, Observability, Safety |
| 2 | Callback exceptions crash the FSM loop — no try/except around invocations | Critical | Reliability, Safety |
| 3 | Exception traceback goes to `print()` instead of `self.log()` — invisible to GUI users | High | Observability, Consistency |
| 4 | No max iterations / max runtime / idle detection — no circuit-breaker for stuck bots | High | Safety |
| 5 | No FSM-level logging — transitions, results, and timing not recorded by the machine | Medium | Observability |
| 6 | Ambient combat watcher imports `handle_combat` internals — architectural coupling | Medium | Modular Architecture |
| 7 | `transitions` library features significantly underutilized (`final`, `on_exception`, hierarchical) | Medium | Extensibility, Reliability |
| 8 | `_flag_X` / `_result_is_X` fragile to typos — no validation that referenced results/flags exist | Medium | Reliability, Consistency |
| 9 | Terminal state detection is O(n) per tick — should be precomputed | Low | Reliability |
| 10 | Background watchers continue during pause | Low | Safety, Consistency |
| 11 | Drop list grows unbounded | Low | Safety |
| 12 | `test_run_can_be_stopped_externally` tests asyncio cancel, not machine stop | Low | Testing |
| 13 | `label` field flows through config but is never used | Low | Code Smell |
| 14 | Option lookup is O(n) linear scan | Low | Consistency |

---

## Stage 3: UI/UX Layer

Scope: The PyQt6 GUI — main window layout, node graph canvas, sidebars, game window embedding, theme system, user interaction patterns, and visual feedback. Files reviewed: ~30 files across `bartlebot/gui/`, totaling ~6000+ lines.

### Understanding the UI Architecture

```
MainWindow (QMainWindow)
  ├─ Menu bar         ← File, View, Run, Launch
  ├─ Horizontal splitter (top)
  │   ├─ LeftSidebar (QTabWidget)    ← Bot / Properties / Log tabs
  │   ├─ GameViewPanel               ← Status header + embedded game windows
  │   └─ RightSidebar (Sidebar)      ← Clients + Drops sections
  └─ Vertical splitter (outer)
      └─ BotCanvas                   ← Node graph editor + toolbar + minimap
```

**Layout model:** Two-level splitter — horizontal splits left sidebar | game view | right sidebar across the top; vertical splits this top row from the canvas at the bottom. All splitter positions are user-resizable and persisted to settings.

**Mode system:** `BotMode.EDIT` (interactive editing) vs `BotMode.RUN` (read-only monitoring). Mode switch swaps toolbar buttons, toggles scene interactivity, and changes which sidebar widgets are editable.

**Signal flow:** BotCanvas emits `config_changed`, `mode_changed`, `tab_switch`, `bot_status_changed` → MainWindow routes to sidebars and game view panel. AppController wires everything together via callbacks.

---

### Findings by Axis

#### 1. Modular Architecture — VERY GOOD

**Strengths:**
- Clean widget hierarchy: each panel is self-contained with its own file, signals, and public API.
- `SidebarSection` base class provides consistent collapsible section pattern — all sections inherit the same header toggle, padding, and size policy.
- `Sidebar` base class provides scrollable container with consistent margins (8px) and minimum width.
- Theme is a frozen dataclass singleton (`THEME`) — all colors, fonts, and sizes centralized. Swapping the singleton enables full theme replacement.
- Canvas components separated by concern: `bot_canvas.py` (mode/toolbar), `graph_items.py` (rendering), `region_item.py` (grouping), `param_panel.py` (property editing), `layout.py` (auto-layout algorithm), `recording.py` (action recording).
- Game embedding isolated in `game_embed/` — `ClientTile` wraps Win32 `SetParent`, `GameView` manages tiling, `GameViewPanel` adds status header.
- Icon system uses SVG with runtime tinting — one icon asset serves all color contexts.

**Concerns:**
- `bot_canvas.py` is a ~1965-line file that handles mode management, toolbar construction, validation, search bar, focus mode, logging, status updates, option injection, and recording delegation. It's the god-file of the GUI.
- `graph_items.py` is ~1000+ lines combining `NodeItem`, `EdgeItem`, `GraphView`, and `_MinimapView` in one file. These are distinct responsibilities that should be separate files.
- `_ParamPanel` is owned by `BotCanvas` but displayed in the left sidebar's Properties tab — the ownership and display location are disconnected, making the signal routing non-obvious.
- Log widget is similarly owned by `BotCanvas` but parented into the left sidebar's Log tab.

#### 2. Reliable Execution — GOOD

**Strengths:**
- Real-time validation catches errors before the bot can run: unknown node types, missing required parameters, transitions to non-existent states. Error/warning badges appear directly on nodes with tooltip details.
- Run button is disabled when validation errors exist — prevents launching a broken bot.
- Warning pill auto-cleanup: clicking the warning count auto-deletes orphaned states (unreachable from initial).
- Game view placeholder gracefully handles the "no clients connected" state with a quick-launch UI showing stored accounts and launch profiles.
- Client loop in `AppController` detects dead clients (`remove_dead_clients()`) and handles `MemoryReadError` gracefully — clears client list and disables start button.
- Splitter positions and window state are persisted — restoring layout across sessions.

**Concerns:**
- `ClientSection` refreshes every 100ms via async gather. If a WizWalker memory read blocks or raises, the entire refresh stalls. No per-client timeout.
- No undo/redo system. Edit mode changes (node creation, deletion, edge creation, copy/paste) are immediately applied to the config. An accidental delete of a complex subgraph is unrecoverable except by reloading from disk.
- Copy/paste generates new state IDs deterministically — pasting the same clipboard multiple times could interact unexpectedly with validation if IDs collide with existing states (though the code likely handles this by appending `_copy_N`).

#### 3. Observability — GOOD

**Strengths:**
- Run-time status header shows: colored status dot (gray/green/orange), status text, elapsed timer (HH:MM:SS), bot name, and current state label — all visible at a glance.
- Active state highlighted with pulsing green animation (800ms sine wave between two green shades) — immediately identifies which node is executing.
- Transition visualization: `on_transition(prev, result, next)` callback highlights edges during execution.
- Log widget with level filtering (DEBUG/INFO/WARNING/ERROR), color-coded prefixes, monospace font, and auto-scroll.
- Drop tracking per client with search filter and CSV export.
- Node validation badges (red/amber) with hover tooltips explaining the specific error or warning.
- Minimap (160×110px, bottom-right) shows viewport rectangle for orientation in large graphs.

**Concerns:**
- Log entries have no timestamps — the log widget formats `[DBG] message` but doesn't prepend wall-clock time. This was flagged in Stage 2 for the machine; the GUI doesn't compensate for it either.
- No way to export or save the log to a file. If the bot runs for hours, the QPlainTextEdit will accumulate thousands of lines with no truncation or rotation.
- The minimap is small (160×110px) and only visible if enabled in settings — no visible toggle in the canvas toolbar. Easy to miss.
- Validation errors don't specify which node they refer to in the pill tooltip — you have to hover over individual nodes to see which ones are red.

#### 4. Extensibility — GOOD

**Strengths:**
- `_DynamicParamForm` generates parameter forms from `ParamField` schema — adding a new parameter type to a node automatically generates the correct widget. Supported types: text, string, int, float, bool, xyz, int_list, str_list.
- Node registry drives the "Add State" dropdown — new node types appear automatically.
- `required_bot_options` auto-injection: when a node type declares required options, the GUI adds them to the options panel automatically and removes them when the node is deleted.
- Theme tokens make visual customization straightforward — adding a dark theme is a matter of creating an alternate `_Theme` instance.
- Region system is fully dynamic — create, rename, recolor, delete, add/remove members.
- Settings dialog is tabbed and extensible — adding a new settings category is one new tab class.

**Concerns:**
- No plugin system for custom node renderers. All nodes share the same visual template (rounded rect, label, ports). If a future node type needs a different visual (e.g., a sub-graph node, a comment node, a decision diamond), the current `NodeItem` would need significant refactoring.
- `_DynamicParamForm` has no custom widget hook — you can't provide a specialized editor for a complex parameter (e.g., a zone picker, a spell selector, a coordinate map picker).
- Edge label rendering is fixed at 9pt text — can't show rich content (icons, multi-line, badges) on transitions.

#### 5. Safety Controls — ADEQUATE

**Strengths:**
- Mode separation prevents editing during bot execution — `BotMode.RUN` locks the canvas.
- Validation blocks running invalid bots — can't start with errors.
- "Done" (discard changes) vs "Save & Done" (persist) gives the user a safe exit from edit mode.
- File operations use standard Qt dialogs with error handling (QMessageBox on failure).
- Game view maximize (Ctrl+Alt+G) hides all editing UI — useful for monitoring-only sessions.

**Concerns:**
- No confirmation dialog before deleting nodes/edges — Delete key immediately removes selection.
- No confirmation before "Done" (discard) — unsaved edit changes are silently lost.
- No auto-save or crash recovery. If the application crashes during edit mode, all unsaved changes are lost.
- The quick-launch placeholder in the game view exposes stored account credentials (clickable account list). No password masking or confirmation before launching.

#### 6. Testing — NOT EVALUATED

No GUI tests exist. PyQt6 testing is notoriously difficult (requires event loop, widget rendering), but critical workflows (config serialization, validation logic, mode switching) could be tested in isolation.

#### 7. Documentation — SPARSE

- No widget docstrings. `MainWindow`, `BotCanvas`, `EditScene`, `GameView` — none have docstrings explaining their role, layout structure, or signal contracts.
- No comments explaining the splitter ratio strategy (why 300px for sidebars? why 220px for canvas?).
- The mode switching logic in `_apply_mode()` is complex (toolbar stack swaps, button visibility, scene interactivity) but uncommented.
- `_SearchBar`, `_MinimapView`, and focus mode have no documentation.
- SVG icon names are referenced by string literals (`"play"`, `"stop"`, `"pencil"`) — no inventory of available icons.

#### 8. Consistency — MOSTLY GOOD, SOME DRIFT

**Strengths:**
- All sidebar sections follow the same pattern: `SidebarSection` subclass with `_SectionHeader` toggle, consistent padding, and `config_changed` signal.
- Theme tokens are consistently used — no hardcoded colors in widget code (well, almost — see concerns).
- All icons use the same `svg_icon()`/`svg_pixmap()` system.
- Canvas items all follow `QGraphicsItem` patterns: `boundingRect()`, `paint()`, hover/selection state tracking.

**Concerns:**
- **Ownership vs display mismatch:** `_ParamPanel` and `_LogWidget` are owned by `BotCanvas` but parented into `LeftSidebar` tabs. This means the canvas creates widgets that live in the sidebar — confusing ownership model.
- **Signal naming:** `config_changed` is emitted by BotCanvas, EditScene, _ParamPanel, LeftSidebar, and individual sidebar sections — same name, different sources, different semantics. Hard to trace which `config_changed` fired.
- **Section header styling:** `_SectionHeader` uses SVG arrow icons for collapse/expand. These use a `#000000` tint, which is hardcoded rather than using a theme token.
- **Widget helpers inconsistency:** `field_label()`, `value_label()`, `value_edit()` helper functions live in `sidebar_section.py` rather than in the theme or a shared widget utils module.
- **Font size inconsistency:** Theme defines `font_node_main_size=9pt`, `font_node_sub_size=6pt`, `font_node_hdr_size=7pt`, but sidebar widgets use their own local font sizes (`11px` for field labels) not centralized in the theme.

---

### Deep-Dive Findings

#### A. Architecture Audit

**Widget hierarchy is clean** — `MainWindow` → splitters → panels → sections → widgets. Each level has clear responsibility. The main pain point is `bot_canvas.py` at ~1965 lines — it handles too many concerns. A natural split would be:

- `bot_canvas.py` — mode management, toolbar, status
- `edit_scene.py` — scene interaction, drag-to-connect, context menus
- `search_bar.py` — floating search overlay
- `focus_mode.py` — region focus with breadcrumb

**10x complexity breaking point:** The current canvas renders up to ~40 nodes comfortably (as seen in the screenshot). At 100+ nodes, the QGraphicsScene will slow down, and the minimap will become too dense to be useful. No level-of-detail (LOD) culling exists beyond the compact label mode at <0.6 zoom — edges, ports, and badges are always rendered. A "collapse region to single node" feature would help.

#### B. Abstraction Quality

- `SidebarSection` / `Sidebar` — good abstractions that enforce consistent layout and behavior. Every section gets the same collapsible header, padding, and scroll behavior for free.
- `GraphView` — clean reusable zoom/pan view. Antialiasing, wheel zoom, space+drag pan.
- `_DynamicParamForm` — excellent schema-driven form generation. The `disabled_by` field relationship is a smart design that avoids custom form logic per node type.
- `NodeItem` is doing too much — rendering, hit testing, port calculation, badge management, search highlighting, active animation, LOD switching, tooltip generation. Should separate rendering from interaction.
- `EditScene` mixes scene management with user interaction — drag-to-connect edge creation, context menus, clipboard operations, and region management are all in one class.

#### C. State Management & Flow Control

- **UI state is well-managed**: Mode (`BotMode`), run state (`_IDLE`/`_RUNNING`/`_PAUSED`), selection state, search state, focus state — all tracked explicitly.
- **Config is the single source of truth**: All UI changes modify the `BotConfig` model, then the UI refreshes from the model. This prevents UI/model drift.
- **Side effect:** `_inject_required_options()` silently modifies the options list when nodes are added/removed. If a user manually configures an option with the same name as a system-managed one, the injection can overwrite their settings.
- **Elapsed timer:** Updated via `bot_status_changed` signal from canvas — the timer is computed in the canvas, not in a dedicated timer widget. If the canvas freezes, the timer stops updating.

#### D. Reliability / Failure Modes (UI-specific)

| Scenario | What happens | Assessment |
|---|---|---|
| **Game window crashes while embedded** | `ClientTile` still holds dead HWND; `client_loop` detects and removes | OK: graceful cleanup |
| **Accidental node deletion** | Immediate, no undo | BAD: data loss risk |
| **Application crash during edit** | All unsaved changes lost | BAD: no auto-save |
| **Very large bot file (100+ nodes)** | Scene renders all items, minimap dense, layout slow | MEDIUM: no LOD/culling |
| **WizWalker raises during client refresh** | `MemoryReadError` caught, client list cleared | OK: handled |
| **Rapid mode switches (edit→run→edit)** | Toolbar stacks swap correctly | OK: tested path |
| **Resize window very small** | Minimum size enforced from settings | OK: configurable floor |

#### E. Code Smells

- **`bot_canvas.py` (~1965 lines)** — god-file handling mode management, toolbar, validation banner, search bar, focus mode, logging, status, option injection, recording, and parameter panel ownership. Should be decomposed.
- **`graph_items.py` (~1000+ lines)** — combines `NodeItem`, `EdgeItem`, `GraphView`, `_MinimapView` in one file. Four distinct classes with different concerns.
- **Cross-panel ownership:** Canvas creates widgets (log, params) that live in the sidebar. The sidebar doesn't own these widgets — it just provides a tab container. This inverts the natural ownership model and makes the code harder to follow.
- **Signal name reuse:** `config_changed` emitted by 5+ different classes with no namespacing or documentation of which fires when.
- **`_SectionHeader` arrow tint `#000000` hardcoded** — should use a theme token.
- **Sidebar helper functions** (`field_label`, `value_label`, etc.) defined in `sidebar_section.py` rather than a shared utility module — these are widget factories, not section-specific logic.

#### F. Long-term Maintainability

**Will become painful:**
- **No undo/redo** will frustrate users as bot complexity grows. Implementing undo after-the-fact requires either command pattern retrofit or QUndoStack integration across all edit operations.
- **`bot_canvas.py` size** will continue growing as new features are added (undo, search improvements, drag-and-drop, subgraph nodes). At ~2000 lines it's already at the threshold.
- **No dark theme** — the light theme is hardcoded. Adding dark mode will require auditing every widget for hardcoded colors and ensuring the theme token system covers all cases.
- **No log rotation** — the QPlainTextEdit accumulates indefinitely. For multi-hour runs, memory usage and rendering performance will degrade.
- **The 100ms client refresh timer** will become expensive with more clients (4+) and more data per client. Should be configurable or adaptive.

---

### UI/UX Observations from Screenshot

Cross-referencing the code analysis with the actual running application:

**What works well:**
- The three-panel layout is clear and well-proportioned — game windows dominate the center, sidebars provide context, canvas shows the bot graph.
- The status header ("Running 00:04:50 | The Great Spyre | Defeat Gurtok Piercer") provides immediate situational awareness.
- The node graph is readable even with ~30+ nodes — colored regions (red/amber/teal) help group related nodes visually.
- The active node highlighting (green) clearly shows current execution state.
- Two game windows are tiled evenly with client labels ("Client 1 ↗", "Client 2 ↗") for identification.
- Drop tracking is immediately visible in the right sidebar with per-client tabs.

**What could be improved:**
- The left sidebar is quite narrow (~240px visible) — the "Bot Info" fields (Description, Drops) are truncated. Text wrapping helps but multi-line content is cramped.
- The canvas at the bottom has limited vertical space (~25% of window height) — the node graph is compressed. With ~30 nodes, labels are small and edges are dense. The expand/collapse button helps but users may not discover it.
- The minimap in the bottom-right is small (160×110px) and may be hard to read at the current canvas density.
- No visible zoom indicator — users can't tell what zoom level they're at.
- The pause/stop buttons are small and centered at the bottom of the canvas — they could be more prominent during run mode.
- Region labels ("Gurtok Sequence", "Defeat Gurtok Piercer") are readable but the region fill is very subtle (~16% opacity). Some users might not notice regions at all.

---

### Summary: Priority Issues

| # | Issue | Severity | Axis |
|---|---|---|---|
| 1 | No undo/redo system for edit mode | High | Reliability, Safety |
| 2 | `bot_canvas.py` is a ~1965-line god-file | High | Modular Architecture, Maintainability |
| 3 | No auto-save or crash recovery | Medium | Safety, Reliability |
| 4 | No delete confirmation for nodes/edges | Medium | Safety |
| 5 | Log widget has no timestamps, no file export, no rotation | Medium | Observability |
| 6 | `graph_items.py` combines 4 unrelated classes (~1000+ lines) | Medium | Modular Architecture |
| 7 | Cross-panel widget ownership (canvas owns sidebar tab content) | Medium | Modular Architecture, Consistency |
| 8 | No dark theme support (light theme only) | Medium | Extensibility |
| 9 | `config_changed` signal emitted by 5+ classes with same name | Medium | Consistency, Observability |
| 10 | 100ms client refresh timer with no per-client timeout | Low | Reliability |
| 11 | No zoom level indicator | Low | Observability |
| 12 | Sidebar font sizes not centralized in theme | Low | Consistency |
| 13 | No GUI tests | Low | Testing |
| 14 | No widget docstrings | Low | Documentation |

---

## Stage 4: Infrastructure

Scope: Logging, configuration, dependency management, debugging tools, unit testing, import structure, error handling patterns, and development tooling. Cross-cutting concerns that affect the entire codebase.

### Understanding the Infrastructure

```
Entry point: bartlebot.py (38 lines)
  └─ QApplication + qasync.QEventLoop + AppController.run()

Logging:     LogLevel enum → machine.log() → on_log callback → GUI
Settings:    SettingsManager singleton → %APPDATA%/BartleBot/settings.json
Credentials: keyring (Windows Credential Manager) → CredentialStore wrapper
Recent files: ~/.bartlebot_recent.json (separate from settings)
Dependencies: Poetry + pyproject.toml (all versions unpinned with "*")
Testing:     pytest + pytest-asyncio + FakeClient/FakeMachine test doubles
CI/CD:       None
Linting:     None
```

---

### Findings by Axis

#### 1. Modular Architecture — ADEQUATE

**Strengths:**
- Clean package structure: `bot/` (engine), `nodes/` (behaviors), `gui/` (UI), `launcher/` (game launch), `utils/` (helpers). Each directory has a clear responsibility.
- Entry point is minimal (38 lines) — just wires QApplication + qasync + AppController.
- Settings isolated in `SettingsManager` singleton — GUI and bot engine don't directly touch the filesystem.
- Credentials separated from settings: passwords in Windows Credential Manager via `keyring`, usernames/aliases in settings.json. Passwords never touch disk.

**Concerns:**
- **Recent files stored separately** from settings. `recent_files.py` writes to `~/.bartlebot_recent.json` while `SettingsManager` writes to `%APPDATA%/BartleBot/settings.json`. Two different persistence locations, two different file formats, two different error handling approaches for the same application. Should be consolidated.
- **`utils/` is nearly empty** — only `drop_detection.py` and an empty `__init__.py`. The module adds organizational overhead without enough content to justify it. `drop_detection` could live in `nodes/` since it's only used by combat-related code.
- **No shared constants module** — timeout values, poll intervals, and magic numbers are defined per-file rather than centralized.

#### 2. Reliable Execution — CONCERNING

**Strengths:**
- Settings deep-merge survives schema changes: new settings get defaults, user-customized values are preserved.
- Corrupt settings file silently falls back to defaults — app always starts.
- `CredentialStore.delete()` catches `PasswordDeleteError` — no-op if credential doesn't exist.
- `recent_files` operations wrapped in try/except — file I/O failures don't crash the app.

**Concerns:**

***`print(traceback.format_exc())` is the only error reporting path:***

6 locations across the codebase use `print(traceback.format_exc())` as the sole error output:

| File | Line | Context |
|---|---|---|
| `bot/machine.py` | 145 | Node execution exception |
| `app_controller.py` | 251 | Client hook activation failure |
| `app_controller.py` | 291 | Client loop general exception |
| `gui/launcher_popup.py` | 55 | Launch failure |
| `gui/main_window.py` | 390 | Geometry save exception |
| `gui/main_window.py` | 407 | Geometry save exception |

These tracebacks go to stdout, which is invisible to the user unless they launched from a terminal. In a packaged application, stdout is typically discarded. There is no file-based log, no stderr redirect, and no crash report mechanism.

***Settings `save()` has no error handling:***

`SettingsManager.save()` calls `json.dump()` with no try/except. If the `%APPDATA%` directory is read-only, the disk is full, or the file is locked, the exception propagates up to the caller — which may or may not catch it. Since settings are saved frequently (on window resize, sidebar drag, etc.), this could surface at any time.

***No validation on settings values:***

`SettingsManager.set()` accepts any value for any key path. There's no schema, no type checking, and no validation. A typo in a key name (`"widnow_width"`) or a wrong type (`resolution = "800x600"` instead of `[800, 600]`) will silently persist incorrect data. The typed helpers (`game_resolution`) have defensive fallbacks, but the generic `get()`/`set()` path does not.

#### 3. Observability — POOR

**Strengths:**
- `LogLevel` enum with 4 levels (DEBUG, INFO, WARNING, ERROR) provides a reasonable vocabulary.
- Log messages from nodes are color-coded and level-filtered in the GUI.

**Concerns:**

***No Python `logging` module usage anywhere:***

The entire codebase has zero `import logging` statements. Instead of using Python's built-in logging infrastructure (with handlers, formatters, levels, hierarchical loggers, and file output), BartleBot implements a minimal callback-based system: `machine.log(msg, level)` → `on_log` callback → GUI `QPlainTextEdit`. This means:

- **No log output before the GUI is ready.** If the app crashes during startup (before `AppController` wires the `on_log` callback), there's no log at all.
- **No file-based log.** Nothing is written to disk. If the app crashes, all log history is lost.
- **No structured logging.** Log entries are plain strings with no metadata (timestamp, source module, state ID, client index).
- **No log aggregation.** Multiple clients, ambient watchers, and the FSM loop all log through the same channel with no source identification.
- **No log levels on `print()` paths.** The 6 `print(traceback.format_exc())` calls bypass the LogLevel system entirely.

***No timestamps on log entries:***

Log entries in the GUI show `[DBG] message` with no wall-clock time. Repeated in Stage 2 and 3 findings — still unfixed as a systemic issue. The logging infrastructure itself provides no timestamp injection point.

***No startup/shutdown logging:***

The app launches and shuts down with no logging. There's no record of: which Python version is running, which WizWalker/WizSprinter version is loaded, which game clients were detected, or how the app was configured. If a user reports a bug, there's no environment context.

#### 4. Extensibility — MIXED

**Strengths:**
- `SettingsManager` has generic `get()`/`set()` with dot-path keys — adding new settings requires no code changes beyond updating `_DEFAULTS`.
- `_deep_merge()` ensures forward compatibility: new default keys are added when users upgrade without losing their existing settings.
- `CredentialStore` is a clean wrapper that could swap backends (from `keyring` to another store) without affecting callers.

**Concerns:**
- **No settings migration system.** The `version` field exists in `BotConfig` but not in settings. If a settings key is renamed or restructured, old settings files will silently use defaults for the renamed keys and carry dead keys forever. No migration path.
- **No environment variable overrides.** Settings can only come from the JSON file. There's no way to override settings via environment variables (useful for CI, development, or debugging).
- **`LogLevel` is not extensible.** The 4-level enum can't accommodate a `TRACE` level (below DEBUG) or a `FATAL` level (above ERROR) without modifying the enum and all consumers.

#### 5. Safety Controls — WEAK

**Strengths:**
- Credentials stored in Windows Credential Manager — not in plaintext files.
- Censored usernames for display (`_censor()` shows `j***n` instead of `john`).
- Recent files capped at 8 entries — no unbounded growth.
- Recent launches capped at 5 entries.

**Concerns:**
- **No secret scanning in bot files.** Bot JSON files are loaded and saved with no check for sensitive data. If a user accidentally pastes a password into a node parameter, it will be saved to disk and could be shared.
- **`settings.json` stores account usernames in cleartext.** While passwords are in Credential Manager, the username list (`accounts: [{username: "john123"}]`) is in a plain JSON file. Depending on threat model, this could be a concern.
- **No file integrity checks.** Bot files and settings are loaded with `json.load()` and trusted completely. A malformed or malicious bot file could contain unexpected data that downstream code doesn't validate (beyond Pydantic's schema checks).

#### 6. Testing — GOOD FOUNDATION, STRUCTURAL GAPS

**Strengths:**
- **20 test files** covering: FSM engine (17 tests), all 15 node types (one test file each), schema validation, and editor helpers.
- **Well-designed test doubles**: `FakeClient` (AsyncMock-backed WizSprinter stub), `FakeMachine` (log/drop recorder), `FakeEntity` (entity stub). These are in `conftest.py` and shared across all tests.
- **pytest-asyncio with auto mode** — async test functions just work, no manual event loop setup.
- **Node tests use FakeClient** — no Wizard101 process required. Tests configure mock return values and assert node behavior.
- **`FakeMachine.get_option()` returns 0 for unknown options** — avoids real delays in tests (since Safety Delay etc. would otherwise sleep).

**Concerns:**

***No test for infrastructure code:***

| Module | Test file | Status |
|---|---|---|
| `settings_manager.py` | None | UNTESTED |
| `credentials.py` | None | UNTESTED |
| `recent_files.py` | None | UNTESTED |
| `log_level.py` | None | UNTESTED (trivial, but) |
| `schema.py` validation | `test_schema.py` | Tested |
| `machine.py` FSM | `test_machine.py` | Tested (happy paths) |
| `drop_detection.py` | None | UNTESTED |
| All GUI code | None | UNTESTED |

***No integration tests:***

All tests are unit tests with mocked clients. There's no test that:
- Loads a real bot JSON file and validates it round-trips through `BotConfig.load()` → `to_machine_config()` → `BotMachine` construction
- Runs a multi-node FSM through multiple transitions
- Verifies the full callback chain (machine → AppController → GUI)

***No test runner configuration beyond `asyncio_mode`:***

No coverage reporting (`--cov`), no test markers (e.g., `@pytest.mark.slow`), no test parallelization, no CI integration.

***`FakeClient` maintenance burden:***

The `FakeClient` manually mirrors WizSprinter's API surface — every new method on `SprintyClient` requires a corresponding `AsyncMock` in `FakeClient`. If WizSprinter adds a method and a node uses it, the test will fail with `AttributeError`, not with a helpful mock message.

#### 7. Documentation — MINIMAL

**Strengths:**
- `CredentialStore` has a good module docstring explaining the security model (keyring backend, no disk passwords).
- `SettingsManager` has a class docstring and typed helper docstrings.
- `FakeClient` and `FakeMachine` have clear docstrings explaining their purpose.

**Concerns:**
- `bartlebot.py` (entry point) has no module docstring explaining the bootstrap sequence.
- No `CONTRIBUTING.md` or developer setup guide.
- No explanation of the test double philosophy or how to write new tests.
- `_DEFAULTS` dict in `SettingsManager` has no comments explaining what each setting controls.
- `drop_detection.py` has no docstrings on its public functions.

#### 8. Consistency — MIXED

**Strengths:**
- All exception handlers that `print(traceback)` follow the exact same pattern — at least the anti-pattern is consistent.
- Test files follow a consistent naming convention: `test_<node_type>.py`.
- All test files share the same `conftest.py` test doubles.
- Settings access is consistent: `get("section", "key")` / `set("section", "key", value=v)`.

**Concerns:**

***Persistence location split:***
- Settings → `%APPDATA%/BartleBot/settings.json`
- Recent files → `~/.bartlebot_recent.json`
- Credentials → Windows Credential Manager

Three different locations for three types of user data. The recent files path (`~/.bartlebot_recent.json`) is particularly inconsistent — it's in the user's home directory with a dotfile convention, while settings are in `%APPDATA%`.

***Error handling inconsistency:***
- `SettingsManager._load()`: catches `Exception`, uses defaults (silent)
- `SettingsManager.save()`: no try/except (propagates)
- `recent_files.load()`: catches `Exception`, returns empty list (silent)
- `recent_files.add()`: catches `Exception` on write (silent), but file read in `load()` also silent
- `credentials.delete()`: catches specific `PasswordDeleteError` (correct)
- `app_controller` exception paths: `print(traceback)` + sleep + continue (semi-silent)

No consistent pattern — some paths are silent, some propagate, some print.

***Dependency version strategy:***

All runtime dependencies use `"*"` (any version). This is fine for development but risky for reproducibility. The `poetry.lock` file pins exact versions, but:
- If `poetry.lock` is not committed or is stale, `poetry install` will resolve to latest versions, which may break.
- `wizwalker` and `WizSprinter` pin to git branches (`development`, `lib-update`) — these are moving targets. A breaking change upstream will break BartleBot with no version constraint to prevent it.

---

### Deep-Dive Findings

#### A. Architecture Audit

**The logging gap is the biggest infrastructure debt.** Python's `logging` module is battle-tested, supports multiple handlers (console, file, network), hierarchical loggers (per-module), formatters (JSON, structured), and level filtering. BartleBot reimplements a tiny subset (4 levels, one callback) and loses all the benefits. Adopting `logging` would immediately solve: no file output, no timestamps, no startup logging, no structured data, and the `print(traceback)` problem (loggers have `logger.exception()` which logs the traceback at ERROR level).

**Settings and recent files should be consolidated.** Two JSON files in two locations for one application's state is unnecessary complexity. Recent files could be a key in `settings.json`.

#### B. Abstraction Quality

- `SettingsManager` is a reasonable abstraction — generic access with typed helpers for common paths. The deep-merge for forward compatibility is a good design choice.
- `CredentialStore` is clean — 4 static methods, clear responsibility, good security model.
- `LogLevel` is too thin to be useful as an abstraction — it's just an IntEnum. If it were paired with a `Logger` class that handled formatting, timestamps, and output routing, it would be a real abstraction.
- `FakeClient` is a maintenance liability disguised as a helper. Every `AsyncMock()` attribute is a manual mirror of `SprintyClient`'s API. A factory function or `spec=SprintyClient` mock would be more maintainable.

#### C. State Management & Flow Control

- **Settings state** is globally mutable via the singleton. Any code can call `SettingsManager.instance().set(...)` at any time. No events are emitted when settings change — consumers must re-read. The GUI does this explicitly (e.g., after settings dialog closes), but there's no reactive pattern.
- **Recent files state** is file-backed with no in-memory cache. Every `load()` reads from disk; every `add()` reads + writes. For the current usage (menu rebuild on file open), this is fine, but it's architecturally fragile.

#### D. Reliability / Failure Modes (Infrastructure-specific)

| Scenario | What happens | Assessment |
|---|---|---|
| **`%APPDATA%` doesn't exist** | `SettingsManager.save()` calls `mkdir(parents=True)` — creates it | OK |
| **Settings file corrupt** | Falls back to `_DEFAULTS` silently | OK: but user loses all settings with no warning |
| **Disk full during save** | `json.dump()` raises `OSError`, propagates to caller | BAD: no handling |
| **WizWalker git branch deleted** | `poetry install` fails | BAD: hard dependency on external git branches |
| **keyring backend unavailable** | `keyring.set_password()` raises | BAD: no fallback, no error message |
| **App crashes during startup** | No log, no crash report, just exits | BAD: invisible failure |
| **Python version < 3.13** | `pyproject.toml` requires `^3.13`, Poetry enforces | OK |

#### E. Code Smells

- **6 × `print(traceback.format_exc())`** — the single most pervasive code smell. Every instance should be `logger.exception("context message")`.
- **`settings.json` silent corruption recovery** — losing all user settings silently is worse than showing an error. The user will be confused when their sidebar widths, game resolution, and account list reset.
- **`FakeClient.__init__` is 26 lines of `AsyncMock()`** — this will grow with every new WizSprinter method. Should use `spec=SprintyClient` or a factory.
- **`_RECENT_PATH = Path.home() / ".bartlebot_recent.json"`** — dotfile in home directory is a Unix convention, not Windows. Should be in `%APPDATA%/BartleBot/` with settings.
- **Empty `bartlebot/utils/__init__.py`** — package exists for one module.

#### F. Long-term Maintainability

**Will become painful:**
- **No file-based logging** means debugging user-reported bugs requires asking "what did you see?" with no log to analyze. Every other desktop application writes a log file.
- **No CI/CD** means regressions are caught manually. As the codebase grows, this becomes unsustainable.
- **No linting/formatting** means code style drift is inevitable. Different contributors will use different conventions. No automated enforcement.
- **Git branch dependencies** (`wizwalker@development`, `WizSprinter@lib-update`) are time bombs — any upstream force-push or branch rename breaks the build.
- **`FakeClient` manual mirroring** will cause test failures every time WizSprinter's API changes, requiring manual updates to the mock.
- **No settings schema validation** means settings bugs (wrong types, missing keys, renamed keys) will surface as runtime errors in arbitrary GUI code, far from the settings load point.

---

### Summary: Priority Issues

| # | Issue | Severity | Axis |
|---|---|---|---|
| 1 | No Python `logging` module — no file output, no timestamps, no startup logging | High | Observability |
| 2 | 6 × `print(traceback.format_exc())` — errors invisible to users | High | Observability, Consistency |
| 3 | No CI/CD pipeline — no automated testing, linting, or quality checks | High | Testing, Maintainability |
| 4 | All dependency versions unpinned (`"*"`) + git branch deps are moving targets | Medium | Reliability |
| 5 | Settings and recent files in two separate locations with inconsistent error handling | Medium | Consistency, Modular Architecture |
| 6 | No settings schema validation — wrong types silently persist | Medium | Reliability, Safety |
| 7 | No linting or formatting tools configured (ruff, mypy, pre-commit) | Medium | Consistency, Maintainability |
| 8 | No infrastructure tests (settings, credentials, recent files, drop detection) | Medium | Testing |
| 9 | `FakeClient` manually mirrors WizSprinter API — maintenance burden | Medium | Testing, Maintainability |
| 10 | Settings corrupt recovery is silent — user loses all settings with no warning | Low | Reliability, Observability |
| 11 | No integration tests — no full round-trip test from JSON → FSM → execution | Low | Testing |
| 12 | No startup/shutdown logging or environment context | Low | Observability |
| 13 | No developer documentation (CONTRIBUTING.md, test guide) | Low | Documentation |
| 14 | `recent_files.py` uses `~/.bartlebot_recent.json` (Unix dotfile on Windows) | Low | Consistency |

---

## Plan of Action

Prioritized by bang-for-buck — fixes that are relatively low-effort but address the highest-severity or most cross-cutting issues. Each item is scoped for one focused session.

---

### 1. Adopt Python `logging` and eliminate all `print(traceback)`

**Why this is #1:** This is the single most cross-cutting fix. It resolves issues from Stage 2 (print vs self.log), Stage 3 (no timestamps in GUI log), and Stage 4 (no file output, no startup logging, 6× print(traceback)). Every other observability improvement builds on having a real logging system in place.

**What to do:**
- Create a module-level logger per file using `logging.getLogger(__name__)`.
- Replace all 6 `print(traceback.format_exc())` calls with `logger.exception("context")`.
- Configure a root logger at startup (in `bartlebot.py`) with two handlers:
  - `logging.FileHandler` writing to `%APPDATA%/BartleBot/bartlebot.log` (with rotation via `RotatingFileHandler`).
  - A custom handler or bridge that feeds into the existing `on_log` callback system for the GUI.
- Add a formatter with timestamps: `"%(asctime)s [%(levelname)s] %(name)s: %(message)s"`.
- Log environment context at startup: Python version, library versions, platform.
- `LogLevel` enum can remain as the GUI's display-level filter; it maps directly to `logging.DEBUG/INFO/WARNING/ERROR`.

**Files to modify:**
- `bartlebot.py` — configure root logger at startup
- `bartlebot/log_level.py` — add mapping to `logging` levels (or replace with `logging` levels)
- `bartlebot/bot/machine.py:145` — replace `print(traceback)` with `logger.exception()`; also consider routing `self.log()` through Python `logging`
- `bartlebot/app_controller.py:251,291` — replace `print(traceback)`
- `bartlebot/gui/launcher_popup.py:55` — replace `print(traceback)`
- `bartlebot/gui/main_window.py:390,407` — replace `print(traceback)`

**Not in scope:** Changing how nodes call `machine.log()` — that pattern is fine; just wire the callback to also emit via Python `logging`.

---

### 2. Fix the silent fall-through bug in the FSM

**Why this is high priority:** This is the most dangerous bug in the codebase. When a node returns a value that no transition condition matches, the FSM silently re-executes the same node forever with no warning. Users will report "bot is stuck" with no diagnostic information. It's also a quick fix.

**What to do:**
- After `await self.advance()`, check whether `self.state` actually changed. If it didn't, and the advance was expected to transition (i.e., the state has outgoing transitions), log a warning: `"No transition matched for result '{self.last_result}' in state '{state_id}' — re-executing"`.
- Add a consecutive-no-transition counter. After N consecutive no-matches (e.g., 10), log an ERROR and optionally stop the machine.
- Add a test in `test_machine.py` for a node returning an unmapped value (e.g., `"PARTIAL"` when only `"SUCCESS"`/`"FAILURE"` transitions exist).

**Files to modify:**
- `bartlebot/bot/machine.py` — the `run()` method, after line 156 (`await self.advance()`)
- `tests/test_machine.py` — add test case

**Context for agent:** The `advance` trigger is generated by the `transitions` library. When `ignore_invalid_triggers=False` is set (line 60), the library raises on unknown triggers but does NOT raise when all conditions on existing transitions evaluate to False — it silently does nothing. The state remains unchanged. The fix must detect this condition after `advance()` returns.

---

### 3. Guard callback invocations in the FSM loop

**Why:** A single unhandled exception in any GUI callback (`on_state_change`, `on_transition`, `on_drop`, `on_log`) kills the entire FSM loop. Since callbacks ultimately execute PyQt6 code, any signal/slot error becomes a bot crash.

**What to do:**
- Wrap each callback invocation in the `run()` method with try/except. Log the exception but don't let it propagate.
- There are 4 callback sites to guard: `on_state_change` (line 138-139), `on_transition` (line 157-158), and any future `on_drop`/`on_log` calls from within the loop.
- Add a test in `test_machine.py` where `on_state_change` raises — verify the FSM continues running.

**Files to modify:**
- `bartlebot/bot/machine.py` — wrap callback calls in `run()`
- `tests/test_machine.py` — add test for callback exception resilience

---

### 4. Add FSM-level logging (transitions, timing, results)

**Why:** Currently the machine itself never calls `self.log()` during normal operation. If a node doesn't log its own result, that tick is completely invisible. Combined with action #1, this gives full observability.

**What to do:**
- Log at the top of each tick: `"[FSM] Executing state '{state_id}' (node_type={node_type})"` at DEBUG level.
- Time each node execution and log it: `"[FSM] State '{state_id}' returned '{result}' in {duration:.1f}s"` at DEBUG level.
- Log transitions: `"[FSM] Transition: '{prev}' --{result}--> '{next}'"` at INFO level.
- Log terminal state detection and stop events at INFO level.
- These logs flow through `self.log()` and thus appear in the GUI log (with timestamps, once action #1 is done).

**Files to modify:**
- `bartlebot/bot/machine.py` — add logging calls in `run()`, around node execution and after advance

**Context for agent:** The `self.log()` method delegates to `self._on_log` callback. The GUI log widget is in `bartlebot/gui/canvas/bot_canvas.py` (the `_LogWidget` inner class). No changes needed there — just emitting more log messages from the machine will make them appear automatically.

---

### 5. Centralize timing constants

**Why:** 6 timeout values are duplicated across 11+ node files under different names (e.g., `BUTTON_TIMEOUT` / `_DORM_BUTTON_TIMEOUT` / `MARK_COOLDOWN_TIMEOUT` all = 35.0s). This makes tuning tedious and the inconsistent naming is confusing.

**What to do:**
- Create `bartlebot/nodes/timing.py` with all shared constants, each with a comment explaining the game mechanic it accounts for:
  ```python
  POLL_INTERVAL = 0.25          # General polling interval for all wait loops
  PROMPT_TIMEOUT = 5.0          # Time to wait for an NPC/interaction prompt to appear
  ZONE_CHANGE_TIMEOUT = 30.0    # Time to wait for a zone transition to complete
  MAX_RETRIES = 3               # Default retry count for recoverable failures
  TRIGGER_TIMEOUT = 10.0        # Time to wait for a UI trigger (button, dialog)
  GAME_COOLDOWN_TIMEOUT = 35.0  # 30s game cooldown + 5s margin (mark/recall/dorm)
  ```
- Update all node files to import from `timing.py` instead of defining their own.
- Verify the table from Stage 1 (the review has the full mapping of which constants appear in which nodes).

**Files to modify:**
- Create `bartlebot/nodes/timing.py`
- All files under `bartlebot/nodes/action/` that define timeout constants (11+ files)
- `bartlebot/nodes/node_helpers.py` — its `POLL_INTERVAL` should also come from or be exported to `timing.py`

**Context for agent:** The exact constant names and values per file are documented in Stage 1 under "Hardcoded timing constants everywhere." The key duplication is: `POLL_INTERVAL` (11 files), `PROMPT_TIMEOUT` / `_PROMPT_TIMEOUT` (4 files, inconsistent underscore), `ZONE_CHANGE_TIMEOUT` / `_HOME_LOADING_TIMEOUT` (5 files, inconsistent naming), `BUTTON_TIMEOUT` / `_DORM_BUTTON_TIMEOUT` / `MARK_COOLDOWN_TIMEOUT` (3 files, all 35.0s representing the same game cooldown).

---

### 6. Add ruff linting + formatting

**Why:** No code quality tooling exists. Ruff is a single tool that handles both linting (replaces flake8, pylint, isort) and formatting (replaces black) with near-zero configuration. It's fast enough to run on every save.

**What to do:**
- Add `ruff` to dev dependencies in `pyproject.toml`.
- Add a `[tool.ruff]` section with sensible defaults (line length, target Python version, selected rule sets).
- Run `ruff check --fix` and `ruff format` to auto-fix the initial batch.
- Optionally add a `pre-commit` hook, but even just having the tool configured is a big improvement.

**Files to modify:**
- `pyproject.toml` — add ruff dependency and config section
- All `.py` files — auto-format pass (this will be a large diff but mechanical)

**Context for agent:** The codebase is already fairly clean stylistically — this is mostly about establishing the tooling baseline. The main things ruff will catch: unused imports, bare excepts, inconsistent quote styles, import ordering. No major refactoring expected.

---

### 7. Pin dependency versions

**Why:** All 8 runtime dependencies use `"*"` (any version). `wizwalker` and `WizSprinter` point to git branches that can change at any time. A breaking upstream change silently breaks BartleBot.

**What to do:**
- Run `poetry show` to see currently resolved versions. Pin each dependency to at least a major version range (e.g., `PyQt6 = "^6.7"`, `pydantic = "^2.0"`).
- For `wizwalker` and `WizSprinter` git deps: pin to a specific commit hash instead of a branch name. Poetry supports `rev = "abc123"` for git dependencies.
- Ensure `poetry.lock` is committed and kept up to date.

**Files to modify:**
- `pyproject.toml` — update version specifiers

---

### 8. Abstract the "ever seen" pattern

**Why:** The same multi-client coordination loop (track which clients have triggered, re-press key for stragglers, poll until all triggered or timeout) is copy-pasted across 5+ node files. Abstracting it into a helper reduces ~50 lines per node and makes the pattern testable in isolation.

**What to do:**
- Add a helper in `bartlebot/nodes/node_helpers.py`:
  ```python
  async def wait_for_all_clients(clients, check_fn, action_fn, timeout, poll_interval):
      """Poll until check_fn returns True for all clients. Re-runs action_fn for stragglers."""
  ```
- The loop structure is identical in every node: initialize `seen = set()`, loop with `asyncio.sleep(poll_interval)`, check each client, add to `seen` if triggered, re-press key for non-triggered, break when `len(seen) == len(clients)` or timeout.
- Replace the copy-pasted loops in each node with a call to the helper.

**Files to modify:**
- `bartlebot/nodes/node_helpers.py` — add the helper
- Nodes using the pattern: `go_to_zone.py`, `enter_dungeon.py`, `interact.py`, `interact_to_zone.py`, `go_to_world_hub.py`, and likely others

**Context for agent:** The pattern is described in Stage 1 under "The 'ever seen' pattern is copy-pasted." Each instance follows the same structure but with different `check_fn` (e.g., `client.is_loading()`, `is_visible_by_path(client, PROMPT_PATH)`) and different `action_fn` (e.g., `client.send_key(Keycode.X, 0.1)`). The abstraction needs to accept async callables for both.

---

### 9. Consolidate recent files into SettingsManager

**Why:** Quick win for consistency. `recent_files.py` is a 31-line module that writes to `~/.bartlebot_recent.json` (Unix dotfile convention on Windows) separately from `SettingsManager` which writes to `%APPDATA%/BartleBot/settings.json`. Same pattern, different locations.

**What to do:**
- Add a `recent_files` key to `_DEFAULTS` in `SettingsManager` (default: `[]`).
- Add `recent_bot_files` property and `add_recent_bot_file()` method to `SettingsManager` (matching the existing `recent_launches` pattern, capped at 8).
- Update `app_controller.py` to use `SettingsManager` instead of `recent_files.load()` / `recent_files.add()`.
- Delete `bartlebot/recent_files.py`.
- Optionally: add a one-time migration that reads `~/.bartlebot_recent.json` into settings if it exists, then deletes the old file.

**Files to modify:**
- `bartlebot/settings_manager.py` — add recent files support
- `bartlebot/app_controller.py` — switch from `recent_files` module to `SettingsManager`
- Delete `bartlebot/recent_files.py`

---

### Items deferred (high effort or low urgency)

These are real issues but not worth tackling right now:

| Issue | Why deferred |
|---|---|
| **Undo/redo system** | Requires QUndoStack integration across all edit operations — large architectural change. Mitigate with auto-save instead. |
| **Split `bot_canvas.py`** | ~2000 lines is painful but functional. Defer until the next major canvas feature requires touching it. |
| **Dead client detection during bot run** | Requires WizWalker API knowledge to detect client death vs. temporary memory read failure. Needs investigation. |
| **Stop interrupting running nodes** | Requires `asyncio.Task.cancel()` with cooperative cancellation in every node's await points. Invasive change. |
| **HierarchicalAsyncMachine** | No current use case demands sub-graphs. Revisit when bot complexity requires it. |
| **Dark theme** | Pure polish. Theme token system is ready for it, but no functional benefit. |
| **Anti-detection randomization** | Important for safety but requires a design decision on jitter strategy. Separate task. |
| **CI/CD pipeline** | Important but depends on having linting (#6) set up first. GitHub Actions setup is straightforward once ruff is configured. |
