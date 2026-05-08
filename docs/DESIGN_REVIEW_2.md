# BartleBot Design Review 2.0

## Scope & Approach

This review covers BartleBot as of May 2026. The original `docs/DESIGN_REVIEW.md` produced 9 fixes and a phased roadmap tracked in `docs/DESIGN_REVIEW_FOLLOWUP.md`. All items in that roadmap are now marked DONE.

Since the original review, the codebase has grown substantially:

| Metric | Then | Now |
|--------|------|-----|
| `machine.py` | 288 lines | 593 lines |
| `app_controller.py` | 330 lines | 882 lines |
| `bot_canvas.py` | ~2000 lines | 1,566 lines |
| Node count | ~15 | 29 |
| Background watchers | 0 | 4 |
| Test files | ~18 | 30 |

New systems since the last review (all unreviewed until now): recording, undo/redo, node browser dialog, wiki import, entity browser, UI tree browser, safety warning dialog, crash recovery dialog, favorites bar, loading screen watcher, client recovery procedure.

**What this review skips** (confirmed done and untouched since): Python logging adoption, ruff config, dependency pinning, poll_each_until_seen helper, SettingsManager Pydantic models, CI/CD pipeline structure, dark/custom theme system.

---

## Stage 1: Node Library

Scope: 29 nodes across 5 subdirectories, plus `timing.py`, `node_helpers.py`, `registry.py`, and `param_schema.py`. 14 nodes are new since the original review.

### Understanding the Node Layer

```
BotMachine.run()
  └─ NODE_REGISTRY[node_type](machine, clients, params) → "SUCCESS"|"FAILURE"|"TRUE"|"FALSE"
       └─ node_helpers.py  ← poll_each_until_seen, is_visible_by_path, poll_until, ...
            └─ timing.py   ← all shared timeout/interval constants
                 └─ WizSprinter / WizWalker (external)
```

### Findings by Axis

#### 1. Modular Architecture — GOOD, TWO GOD-NODES

Node structure is clean. Each file exports `NODE_SPEC`, `PARAM_SCHEMA`, and `async def run()`. The registry maps 29 nodes across 5 semantic categories. `param_schema.py` supports 11 widget types and all nodes use only supported types. `registry.py` is complete — all 29 nodes registered in both `NODE_REGISTRY` and `NODE_SPECS`.

**Concerns:**

Two nodes have grown well beyond single-responsibility:

| File | Lines | Distinct Responsibilities |
|------|-------|--------------------------|
| `handle_potions.py` | 508 | 12+ (potion use, inventory check, zone validation, mark/recall, home teleport, dorm→Ravenwood→Commons navigation, shop interaction, potion buying loop, two private helpers) |
| `change_realm.py` | 381 | UI navigation + page pagination logic + realm selection state machine + cooldown waiting |
| `quicksell.py` | 399 | Inventory check + UI navigation + item filtering loop + post-sell dialogs |

`handle_potions.py` is the critical one. The original review flagged it at ~200 lines. It now has 12 identifiable responsibilities, including two private helpers (`_teleport_home_and_verify_dorm` at 65 lines and `_open_potion_shop` at 33 lines) that are full sub-procedures. The node calls `mark_location.run()` and `enter_zone.run()` internally — effectively a mini-bot composed of other nodes.

`change_realm.py` and `quicksell.py` are complex but more justifiable — their complexity is intrinsic to the game feature, not caused by accumulation of unrelated concerns.

#### 2. Reliable Execution — MOSTLY GOOD, TWO ISSUES

**Timing constants:** All nodes correctly import from `timing.py`. No node defines its own `POLL_INTERVAL`. `timing.py` is well-structured with 14 constants and a `jitter()` function.

**`interact.py` DIALOG_TIMEOUT:** Moved to `timing.py` (was the only timing constant defined outside the shared module). `interact.py` now imports it alongside `MAX_RETRIES` and `POLL_INTERVAL`.

**`handle_potions.py` hardcoded sleeps:** Replaced. `_SHOP_CLICK_DELAY = 0.25` and `_EXIT_CLICK_INTERVAL = 0.125` are now defined as named module-level constants at the top of `handle_potions.py` (prefixed `_` since they are too shop-specific to belong in `timing.py`). The three raw `asyncio.sleep()` literals reference these constants.

**`handle_potions.py` unsafe position snapshot:** Line 256–257 snapshots position from the first client only and applies it to all. This assumes clients start synchronized. If they don't, the recall target will be wrong for non-primary clients.

**Multi-client coordination:** Correct — `poll_each_until_seen()` is used where appropriate. Nodes with per-client conditional actions implement their own loops (appropriate when action differs per-client state).

**Cooperative cancellation:** Consistent pattern. All nodes rely on `asyncio.CancelledError` propagating from WizWalker `wait_for()` calls. Long-wait nodes (`wait_for_health`, `enter_dungeon_teamup`) add periodic AFK keystrokes during waits. No node explicitly checks `machine.is_stopped` mid-execution — acceptable given the grace period mechanism in `machine.stop()`.

#### 3. Observability — GOOD with minor inconsistency

All nodes log via `machine.log()` using Python logging levels. No `print()` calls found in any node.

**Inconsistency:** `wait_for_health.py:83` and `click_path.py:90, 106, 111, 126` pass the string `"WARNING"` to `machine.log()` instead of `logging.WARNING`. This is a coercion risk — behavior depends on how `machine.log()` handles unexpected level values.

#### 4. Extensibility — GOOD

Return values are exactly `"SUCCESS"`, `"FAILURE"`, `"TRUE"`, or `"FALSE"` across all 29 nodes. No anomalies. `param_schema.py` covers all param types used. All 14 new nodes follow established conventions.

#### 5. Consistency — MOSTLY GOOD

New nodes (14 total) follow conventions well. Minor issues:
- Logging level passed as string in 2 nodes (see Observability above)
- `handle_potions.py` hardcoded sleeps (see Reliable Execution)

### Summary: Priority Issues

| # | Issue | File | Severity |
|---|-------|------|----------|
| 1 | ~~`DIALOG_TIMEOUT = 60.0` defined locally~~ | `interact.py` | High — **DONE** |
| 2 | God-node: 12+ responsibilities, 508 lines | `handle_potions.py` | High |
| 3 | ~~Hardcoded `asyncio.sleep(0.25/0.125)`~~ | `handle_potions.py` | Medium — **DONE** |
| 4 | Position snapshot from first client only | `handle_potions.py:256` | Medium |
| 5 | Logging level passed as string `"WARNING"` | `wait_for_health.py:83`, `click_path.py:90,106,111,126` | Low |

---

## Stage 2: FSM & Background Watchers

Scope: `machine.py` (593 lines, up from 288), `schema.py`, `combat_runner.py`. Focus on 4 background watchers and client recovery added since the original review.

### Understanding the FSM

```
BotMachine (transitions.AsyncMachine)
  ├─ run() loop                   ← execute node → advance trigger → evaluate conditions
  ├─ __getattr__                  ← dynamic _result_is_X / _flag_X / _flag_compound_X
  ├─ get_option()                 ← per-node overrides → bot-wide fallback
  ├─ Background watchers (4)
  │   ├─ _ambient_dialog_watcher  ← dismisses game dialogs; pauses with FSM
  │   ├─ _ambient_combat_watcher  ← handles unexpected combat; pauses FSM during combat
  │   ├─ _client_status_watcher   ← polls zone_name(); detects unresponsive clients
  │   └─ _loading_screen_watcher  ← detects loading screen hangs > 60s
  └─ Callbacks                    ← on_state_change, on_transition, on_drop, on_client_disconnected
```

### Verification: Original Critical Bugs

All three critical bugs from the original review are confirmed fixed:

| Bug | Fix | Verified |
|-----|-----|----------|
| Silent fall-through | `_no_match_count` counter (line 66), threshold 10, stops bot + logs ERROR | ✓ |
| Callback exceptions crash FSM | `on_state_change`, `on_transition`, `on_client_disconnected` all wrapped in try/except | ✓ (with caveat — see below) |
| `print(traceback)` for errors | Zero `print()` calls; all use `logger.exception()` or `self.log()` | ✓ |

### Findings by Axis

#### 1. Reliable Execution — GOOD, ONE UNGUARDED CALLBACK

**`on_drop` callback not wrapped (machine.py:172-173):** The `on_state_change` and `on_transition` callbacks are correctly guarded. `on_drop` is not. If `AppController._on_machine_drop()` raises (e.g., a PyQt6 signal error while updating the drops widget), the exception propagates into `run()`, which has no handler at that point — the bot crashes. This is the same class of bug that was fixed for the other callbacks and was simply missed.

**Loading screen watcher false-positive risk:** The watcher flags a client dead after `is_loading()` returns `True` for 60 continuous seconds (threshold from `timing.LOADING_SCREEN_TIMEOUT`). Legitimate Wizard101 loads on slow PCs or large areas can reach 45–50 seconds. A 60-second threshold is conservative but not bulletproof. A false-positive triggers full reconnect: force-close game → wait for WizSprinter to detect removal → relaunch → spam Enter → wait up to 120s for hook.

**Ambient combat watcher race window (machine.py:467-492):** The watcher checks `self.state != "handle_combat"` at line 467, then awaits `_ambient_combat_lock` at line 492. Between the check and lock acquisition, the FSM can advance into a `handle_combat` node. The re-check after acquiring the lock (line 492-499) partially mitigates this, but two combat handlers could theoretically start simultaneously. In asyncio (single-threaded), a true parallel execution cannot occur, but logical interleaving across await points is possible.

**Conditional-first evaluation (commit 04eb83c):** `BotConfig.to_machine_config()` now calls `sort_transitions()`, which stable-sorts all outgoing transitions for each state so that conditional edges (with `when` or `flag`) are evaluated before unconditional fallback edges. This is a critical correctness fix: previously, JSON order determined evaluation order, and a fallback edge placed first in the file would fire unconditionally even if a conditional edge should have matched. The fix is implemented correctly and documented in the commit; it should also be noted in this review.

#### 2. Observability — GOOD

State transitions are logged with labels. The `label` field from `TransitionModel` is now used in log output (line 197–198). All 4 watchers log their actions at appropriate levels. No print() calls remain.

**Minor:** Watcher errors are logged at DEBUG level, which makes them easy to miss in production logs filtered to INFO+.

#### 3. Safety Controls — GOOD, ONE GAP

`on_drop` callback unguarded (HIGH — see above). All other safety controls are sound: pause/resume via `_resume_event`, grace period stop (1.0s default), idempotent pause calls.

The `_loading_screen_watcher` intentionally continues during FSM pause — correct, since loading hangs should be detected even when the FSM is waiting. The `_ambient_dialog_watcher` and `_ambient_combat_watcher` correctly pause when the FSM is paused.

#### 4. Modular Architecture — ADEQUATE, GROWING CONCERN

`machine.py` is no longer single-responsibility. It now has 5 distinct areas: FSM execution loop (~122 lines), background health monitoring (~219 lines), pause/resume/stop lifecycle (~40 lines), condition evaluation (~43 lines), and data aggregation + logging (~35 lines). At 593 lines with 7+ reasons to change, it qualifies as an orchestrator.

**Import coupling resolved:** `combat_runner.py` was extracted from `handle_combat.py`. `machine.py` imports only from `combat_runner`, not from any specific node file. `combat_runner.py` does not import from `machine.py` — no circular dependency.

**Natural extraction:** A `ClientHealthMonitor` class wrapping `_client_status_watcher` and `_loading_screen_watcher` (with shared `_status_check_flagged` set) would reduce machine.py by ~120 lines and isolate the health monitoring concern. However, these watchers call `self.pause()` directly — extraction would require a callback. Defer until the codebase adds more watchers or tests require isolating them.

#### 5. Consistency — GOOD

No `print()` calls. All exception handling uses `logger.exception()`. Callback wrapping is consistent (except `on_drop`). Pause behavior of each watcher is intentional and documented via inline comments.

**Remaining minor items from original review:**
- `self.drops` is still unbounded (no cap) — low risk for current session lengths, but a multi-hour run appending thousands of items will accumulate unbounded memory
- `get_option()` is still an O(n) linear scan — negligible with typical n=2–5 options
- `transitions` library features (`final=True`, `on_exception`) still underutilized — acceptable, no current use case

### Background Watcher Assessment

| Watcher | Lifecycle | Error Handling | Pause Behavior | Hazards |
|---------|-----------|----------------|----------------|---------|
| `_ambient_dialog_watcher` | Created line 202; cancelled + awaited in finally | `gather(return_exceptions=True)` + DEBUG log | Pauses with FSM | Safe; read-only |
| `_ambient_combat_watcher` | Created line 203; cancelled + awaited in finally | `gather(return_exceptions=True)` + lock + try/except | Pauses with FSM | Race window: check @ 467, lock @ 492 |
| `_client_status_watcher` | Created line 204; cancelled + awaited in finally | Per-client try/except, callback wrapped | Intentionally NOT paused | Safe; coordinates with loading watcher via `_status_check_flagged` |
| `_loading_screen_watcher` | Created line 205; cancelled + awaited in finally | Per-client try/except, callback wrapped | Intentionally NOT paused | Safe; defers to status watcher |

### Summary: Priority Issues

| # | Issue | File | Severity |
|---|-------|------|----------|
| 1 | `on_drop` callback not wrapped in try/except | `machine.py:172` | High |
| 2 | Ambient combat watcher race window (check at 467, lock at 492) | `machine.py:467-492` | Medium |
| 3 | Loading screen watcher false-positive on 45–50s legitimate loads | `machine.py:405` | Medium |
| 4 | `self.drops` list unbounded | `machine.py:60,171` | Low |
| 5 | BotMachine responsibility growth (5+ concerns, 593 lines) | `machine.py` | Code smell |

---

## Stage 3: New GUI Systems

Scope: All major GUI systems added since the original review — recording, undo/redo, node browser, wiki import, entity browser, UI tree browser, safety warning, recovery dialog, favorites bar. None were reviewed previously.

### Per-System Findings

#### Recording System (`recording.py`, 449 lines)

Async coroutines dispatch per node type via `_REGISTRY`. Each coroutine updates the param form in real time as the user walks through the game. Error paths are correct: `CancelledError` is caught and the Record button is restored in all exit paths. Client disconnect mid-recording triggers an exception catch, shows an error message, and exits cleanly.

**Coverage gaps:** The registry covers 11 of 29 node types. The 18 non-recordable nodes are appropriate (conditions, passive-wait nodes). However, some nodes with XYZ parameters (`collect_wisps`, `quicksell`) could benefit from coordinate recording — no blocker, just a future-feature gap.

**Concurrent session risk:** `param_panel.py` cancels any existing `_record_task` before starting a new one. This is correct for single-panel usage. No guard in `recording.py` itself — protection relies on the assumption that only one param panel is active. This is documented implicitly but not asserted.

#### Undo/Redo (`undo_manager.py`, 138 lines)

Snapshot-based with a 50-state deque and 500ms debounce for param edits. Each snapshot is a full `model_dump()` of `BotConfig`. For a bot with 50 states + 70 transitions (~20 KB serialized), 50 snapshots costs ~1 MB — acceptable.

**No tests exist.** This is the highest-risk gap: silent undo logic failures or regressions from `model_validate()` changes will not be caught. (See Stage 5.)

**Edit coverage:** `push()` and `begin_debounce()` are called from `param_panel.py` and structural operations in `bot_canvas.py`. Potential gaps in `edit_scene.py` region operations were not confirmed — this should be audited when writing tests.

#### Node Browser Dialog (`node_browser_dialog.py`, 797 lines)

Two-mode dialog (add-node + read-only reference). Driven entirely by `NODE_SPECS` and `NODE_CATEGORIES`. Dual-mode UI, category tabs, search, detail panel, geometry persistence, favorites toggling. 797 lines is justified by this breadth.

**Critical bug — line 740:** ~~`new_item.update_star(True)` was called on a `_NodeListItem` instance, which has no `update_star()` method.~~ **Fixed** — the spurious call is removed. The Favorites tab list is rebuilt from `SettingsManager` state on each toggle, so no additional update call on the new item is needed.

**Documentation error:** Docstring mentions "drag-to-canvas" but no drag/drop is implemented. Nodes are added by double-click or dialog acceptance only.

#### Wiki Import (`wiki_import_dialog.py`, 404 lines)

`QThread`-based HTTP fetch correctly runs off the Qt main thread. Signals marshal results back safely. BeautifulSoup parsing handles two formats (Wizard101 Central drop-category divs and heading-based fallback). Error handling covers non-200 status, connection errors, and parse failures — all emit a user-visible error message.

**`curl_cffi` dependency:** Used for Cloudflare bypass via `chrome124` impersonation. This is a hard runtime dependency — if `curl_cffi` is unavailable or incompatible, the import fails. Replacing with `urllib` + User-Agent would likely be insufficient for Cloudflare protection.

**Thread cleanup:** The `_FetchWorker` QThread is not explicitly joined on dialog close. Parent destruction should clean it up, but the timing is implicit — a background fetch could complete after the dialog is gone and emit signals into deallocated objects. This is low risk but not clean.

**Offline behavior:** Dialog opens and closes gracefully without internet. Fetch button works but fails cleanly.

#### UI Tree Browser (`ui_tree_browser.py`, 323 lines)

Shows the live WizWalker window tree with a transparent red rectangle overlay on the selected element.

**Overlay cleanup risk:** The overlay is a separate `QWidget` (Tool window). It is hidden and `deleteLater()`'d in `closeEvent`. However: if an exception occurs during `_load_tree()` before `closeEvent` fires, the overlay can be orphaned. If an `_update_overlay()` async task is pending when the dialog closes, it may attempt to update an already-deleted widget.

**Input blocking:** The overlay uses `Qt.WindowType.Tool` and `WA_ShowWithoutActivating`. These should prevent focus stealing and mouse interception, but this has not been verified empirically against the embedded game window.

**Client disconnect:** A `MemoryReadError` during `_update_overlay()` is caught silently (line 313). The overlay state becomes undefined. Subsequent selections may silently fail to update.

#### Safety Warning Dialog (`safety_warning_dialog.py`, 170 lines)

Hard-codes warnings for 3 node types: `quicksell`, `collect_reagent`, `teleport_to_friend`. Adding a new risky node requires editing `collect_safety_warnings()` directly — no registry or `NodeSpec` field indicates danger. `change_realm`, `enter_gauntlet` (wrong team size), and `handle_potions` on shared realms are not warned about.

**Bypass friction:** The "Run Bot" button has default focus and is always enabled. One click dismisses all warnings and starts the bot. No acknowledgment required.

**False positive:** `quicksell` warning fires even when all sell flags are `False`, because the current code checks for the presence of the node, not for dangerous param values.

#### Recovery Dialog (`recovery_dialog.py`, 102 lines)

Simple table of autosave candidates. If `original_path` is `None` (unsaved new bot), the dialog returns it as a candidate — the calling code must handle restoration of an unsaved file. No explicit validation in the dialog.

#### Favorites Bar (`favorites_bar.py`)

10-slot hotkey bar (keys 1–9, 0) with drag-to-rearrange and right-click assignment. Correctly overrides `changeEvent` for theme compliance. Clean implementation with no notable issues.

### Issues Table

| File | Line | Issue | Severity |
|------|------|-------|----------|
| `node_browser_dialog.py` | 740 | ~~`AttributeError`: `_NodeListItem.update_star()` does not exist~~ | Critical — **DONE** |
| `undo_manager.py` | — | No tests; silent regressions possible | High |
| `safety_warning_dialog.py` | 37–100 | Warning coverage limited to 3 hard-coded node types | High |
| `ui_tree_browser.py` | 320–323 | Overlay not cleaned up if exception occurs before closeEvent | Medium |
| `recording.py` | — | Concurrent session protection relies on single-panel assumption (undocumented) | Medium |
| `wiki_import_dialog.py` | 285–288 | QThread not explicitly joined on dialog close | Low |
| `wiki_import_dialog.py` | 75 | `curl_cffi` hard dependency; no fallback | Low |
| `node_browser_dialog.py` | 10 | Docstring claims drag-to-canvas; feature not implemented | Low |
| `safety_warning_dialog.py` | 165 | "Run Bot" always enabled; zero-click bypass of all warnings | Low |

### Summary: Priority Issues

| # | Issue | File | Severity |
|---|-------|------|----------|
| 1 | ~~`AttributeError` on favorite toggle in node browser~~ | `node_browser_dialog.py` | Critical — **DONE** |
| 2 | Undo/redo system completely untested | `undo_manager.py` | High |
| 3 | Safety warning coverage: only 3 of many risky nodes | `safety_warning_dialog.py` | High |
| 4 | UI tree overlay not cleaned up on exception | `ui_tree_browser.py:320-323` | Medium |
| 5 | Wiki import thread not joined on dialog close | `wiki_import_dialog.py:285-288` | Low |

---

## Stage 4: Architecture & Responsibility

Scope: `app_controller.py` (882 lines), `bot_canvas.py` (1,566 lines), `edit_scene.py` (1,572 lines).

### app_controller.py — God Class

AppController has grown from 330 to 882 lines (2.7×) and now manages 10 distinct responsibilities:

| Responsibility | Methods | Lines |
|---|---|---|
| File operations | `_new_bot`, `_open_bot`, `_close_bot`, `_save_bot`, `_save_bot_as` | 142–272 |
| Bot execution lifecycle | `_on_run_requested`, `_start_bot`, `_pause_bot`, `_resume_bot`, `_stop_bot` | 277–336 |
| Machine callbacks | `_on_machine_state_change`, `_on_machine_transition`, `_on_machine_drop`, `_on_machine_client_disconnected` | 342–361 |
| Client loop & detection | `_client_loop`, `_refresh_account_map` | 388–493 |
| Client recovery orchestration | `_on_clients_removed`, `_run_recovery_queue`, `_reconnect_client` | 495–721 |
| Client ordering & embedding | `_build_ordered_clients`, `_sync_embedded_clients`, `swap_clients` | 521–741 |
| Signal routing | `_wire`, `_on_config_changed`, `_on_mode_changed` | 79–383 |
| Theming | `_apply_theme` | 750–762 |
| Auto-save & crash recovery | `_do_autosave`, `_delete_autosave`, `_check_crash_recovery` | 750–883 |

**This is a god class.** 10 responsibilities, 7+ reasons to change, 882 lines.

**Machine coupling violations:** AppController directly accesses private BotMachine internals:
- `machine._stop_flag.is_set()` — lines 505, 515, 584 (3 occurrences)
- `self._current_machine.clients = self._clients` — lines 416, 453, 463, 471 (direct mutation of private state)

These should be public methods (`machine.is_stopped` property, `machine.set_clients()`). If BotMachine ever renames these internals, AppController silently breaks.

**Recovery procedure fault-tolerance:** `_reconnect_client()` (lines 599–721, 122 lines) is the most complex procedure in the codebase. It has moderate fault-tolerance but:
- If the 2nd of 3 queued clients fails reconnect, the 1st is recovered but `_pending_recovery` is left dirty — subsequent client disconnects may interact with stale recovery state
- Wrong/missing password fails silently with no user-facing dialog (line 617–621)
- Exceptions during the Enter-spam loop (line 675–689) are swallowed with no escalation

**Recommended split:**

```
AppController (file ops, signal wiring, theming, auto-save — ~300 lines)
├─ ClientManager (new class — client loop, recovery, ordering — ~400 lines)
│   ├─ _client_loop, _refresh_account_map
│   ├─ _on_clients_removed, _run_recovery_queue, _reconnect_client
│   ├─ _build_ordered_clients, _sync_embedded_clients, swap_clients
│   └─ Owns: _hwnd_account_map, _client_order, _pending_recovery, _recovery_in_progress
└─ BotRunner (new class, optional — execution + callbacks — ~180 lines)
```

This is the highest-priority architectural work in the codebase. Extract `ClientManager` first (it's the most self-contained). The recovery logic is a hairball that deserves isolation and independent testing.

### bot_canvas.py and edit_scene.py — Large but Mostly Sound

Both files are ~1,570 lines. Their responsibilities are cleanly separated: BotCanvas owns UI/UX (mode switching, toolbars, search, validation display), EditScene owns data (nodes, edges, regions, clipboard, positions).

**One coupling violation:** BotCanvas directly calls EditScene private methods at lines 1110, 1117, 1129, 1138 (`_find_empty_grid_cell`, `_create_state_at`). These should be public API.

**`_ParamPanel` still misplaced:** Created in BotCanvas (line 565) and displayed in LeftSidebar (passed as a constructor parameter). Neither owns it semantically — it's an edit-state concern. This is the same issue flagged in the original review, still unresolved. Low day-to-day impact but creates confusion about ownership.

**Theme compliance gap:** BotCanvas constructs `_error_pill` (line 679) and `_warning_pill` (line 685) with THEME-colored stylesheets in `__init__`. The `changeEvent` override (line 999) re-tints button icons but does not re-apply pill stylesheets. Pills will show stale colors after a theme switch.

**Signal discipline:** Well-resolved. EditScene uses specific signal names (`graph_changed`, `about_to_change`, `focus_region_requested`, `node_type_chosen`). BotCanvas signals are all specific. The generic `config_changed` concern from the original review is fully addressed.

**Region management in edit_scene.py:** 7 methods and ~150 lines managing regions. This cluster has minimal coupling to the rest of EditScene (accesses `_config` and `_node_items`, emits `graph_changed`) and is a natural extraction candidate into a `region_manager.py`. Defer until touching this code for another reason.

**Top extraction candidates from bot_canvas.py:**
1. Mode switching engine (`_apply_mode()`, run-state buttons, tick timer) — ~80 lines, many state mutations
2. Validation badge manager (error/warning pills, banner) — ~46 lines, cleanly scoped
3. Toolbar & button management — ~100 lines, would simplify init and changeEvent

### Issues Table

| File | Line | Issue | Severity |
|------|------|-------|----------|
| `app_controller.py` | 505, 515, 584 | `machine._stop_flag.is_set()` — private attribute accessed directly | Critical |
| `app_controller.py` | 416, 453, 463, 471 | `machine.clients = ...` — private state mutated directly | Critical |
| `app_controller.py` | 564–597 | Recovery queue state leak on partial multi-client failure | High |
| `bot_canvas.py` | 1110, 1117, 1129, 1138 | Calls EditScene private methods directly | High |
| `app_controller.py` | 617–621 | No user-facing dialog on password retrieval failure in recovery | High |
| `bot_canvas.py` | 565 | `_ParamPanel` owned by canvas, displayed in sidebar — split ownership | Medium |
| `bot_canvas.py` | 679, 685 | Validation pill stylesheets not re-applied on theme change | Medium |
| `app_controller.py` | — | God class (10 responsibilities, 882 lines) — `ClientManager` not extracted | Medium |
| `edit_scene.py` | 1350–1472 | Region management (7 methods, 150 lines) extractable | Low |

### Summary: Priority Issues

| # | Issue | Severity |
|---|-------|----------|
| 1 | `app_controller.py` accesses `machine._stop_flag` and `.clients` directly | Critical |
| 2 | Extract `ClientManager` from `app_controller.py` | Medium (growing concern) |
| 3 | Validation pills don't re-apply stylesheet on theme change | Medium |
| 4 | BotCanvas calls EditScene private methods | High |
| 5 | Recovery queue partial-completion state leak | High |

---

## Stage 5: Testing & Infrastructure

### Node Coverage

26 of 29 nodes have test files. Three nodes are completely untested:

| Node | Category | Missing Tests |
|------|----------|--------------|
| `enter_gauntlet` | Zone Entry | All — entry flow, wrong zone, queue timeout, match failure |
| `change_realm` | Utility | All — realm switch, loading, cooldown, pagination |
| `sleep` | Utility | All — trivial, but no coverage |

All 26 tested nodes use consistent patterns (FakeClient + FakeMachine fixtures, no game process required).

### New GUI System Coverage

| System | Test File | Coverage |
|--------|-----------|----------|
| `undo_manager.py` | None | **Untested** |
| `recording.py` | None | **Untested** |
| `wiki_import_dialog.py` (HTML parsing) | None | **Untested** |
| `safety_warning_dialog.py` | None | **Untested** |
| `node_browser_dialog.py` | None | **Untested** |
| `entity_browser.py` | None | Untested (lower priority) |
| `ui_tree_browser.py` | None | Untested (lower priority) |

### Integration Test Coverage (`test_integration.py`)

**Covered:**
- BotConfig round-trip JSON serialization
- `to_machine_config()` condition string format
- BotMachine construction from real bot file
- Flag-guarded transitions (single flag, true/false)
- Compound flag (AND/OR) transitions
- Real node execution through FSM (SUCCESS routing, FAILURE self-loop + idle limit)
- Per-node `option_overrides` visible via `get_option()`

**Not covered:**
- Recovery procedure (`force_state` + `resume`)
- Machine stop during execution (graceful shutdown path)
- No-match counter behavior (silent fall-through guard)
- Background watcher behavior in any form

### FakeClient Assessment

FakeClient in `tests/conftest.py` manually mocks 25 methods with `AsyncMock()`. No `spec=SprintyClient` is used.

**Risk:** When WizSprinter adds, removes, or renames methods, tests will not detect the divergence. `FakeClient` will happily respond to any attribute access (`MagicMock` behavior), and tests pass while production code fails on the first real WizSprinter call. WizSprinter is pinned to a specific commit hash, which mitigates the immediate risk — but when the pin is updated, the FakeClient should be validated against the new API.

**Adding `spec=SprintyClient`** would enforce that only real methods are mocked. This is a 5-minute change but requires importing `SprintyClient` in conftest.py.

### Dependencies Audit

All runtime dependencies are correctly pinned to `^MAJOR.MINOR` ranges. New dependencies since the original review:

| Dependency | Version Constraint | Notes |
|---|---|---|
| `wakepy` | `^1.0` | Prevent system sleep during runs — properly pinned |
| `curl_cffi` | `^0.15` | Cloudflare bypass for wiki import — properly pinned |
| `beautifulsoup4` | `^4.0` | HTML parsing for wiki import — properly pinned |

WizSprinter correctly pinned to a specific commit hash (`rev = "222fc7a..."`). WizWalker kept on `branch = "development"` due to Poetry solver conflict, but `poetry.lock` pins the exact commit.

### CI/CD

GitHub Actions runs on `windows-latest` (required for WizWalker Windows APIs). Steps: `poetry install` → `ruff check` → `ruff format --check` → `pytest`. Pre-commit config exists with ruff hooks. No known flaky tests reported.

### Issues Table

| Area | Issue | Severity |
|------|-------|----------|
| Node coverage | `enter_gauntlet` has no test file | High |
| Node coverage | `change_realm` has no test file | Medium |
| Node coverage | `sleep` has no test file | Low |
| New systems | `undo_manager.py` completely untested | High |
| New systems | `recording.py` completely untested | High |
| New systems | `safety_warning_dialog.py` (`collect_safety_warnings`) untested | High |
| New systems | `wiki_import_dialog.py` HTML parsing untested | Medium |
| FakeClient | No `spec=SprintyClient`; API drift undetected | High |
| Integration | Recovery procedure not tested | Medium |
| Integration | Machine stop during execution not tested | Medium |

### Summary: Priority Issues

| # | Issue | Severity |
|---|-------|----------|
| 1 | FakeClient lacks `spec=SprintyClient` | High |
| 2 | `enter_gauntlet` node untested | High |
| 3 | `undo_manager.py` untested | High |
| 4 | `recording.py` untested | High |
| 5 | `safety_warning_dialog.collect_safety_warnings()` untested | High |
| 6 | `change_realm` node untested | Medium |
| 7 | Wiki import HTML parsing untested | Medium |
| 8 | Recovery + stop paths missing from integration tests | Medium |

---

## Plan of Action

Prioritized by bang-for-buck — fixes that address the highest-severity issues with the lowest effort. Each item is scoped for one focused session.

---

### 1. Fix the `node_browser_dialog.py` AttributeError (line 740)
**Status:** `DONE`

Removed the `new_item.update_star(True)` call. `_NodeListItem` has no such method — only `_DetailPanel` does. The Favorites tab reconstructs the list from settings state on each toggle, so no post-add update is needed.

---

### 2. Guard the `on_drop` callback in `machine.py`
**Status:** `TODO`

**Why:** The same class of bug (unguarded callback) was fixed for `on_state_change` and `on_transition` in the original review. `on_drop` was simply missed. If AppController's drop handler raises (PyQt6 signal error, widget update failure), the FSM crashes.

**What to do:** Wrap lines 172–173 in try/except:
```python
if self._on_drop:
    try:
        self._on_drop(item, quantity, client_index)
    except Exception:
        logger.exception("on_drop callback raised for item=%r qty=%d", item, quantity)
```

---

### 3. Move `DIALOG_TIMEOUT` to `timing.py`
**Status:** `DONE`

`DIALOG_TIMEOUT = 60.0` added to `timing.py` with a comment. Local definition removed from `interact.py`; the constant is now imported alongside `MAX_RETRIES` and `POLL_INTERVAL`.

---

### 4. Fix `handle_potions.py` timing issues
**Status:** `DONE`

`_SHOP_CLICK_DELAY = 0.25` and `_EXIT_CLICK_INTERVAL = 0.125` added as named module-level constants in `handle_potions.py` (too shop-specific for `timing.py`). The three raw `asyncio.sleep()` literals are replaced with these constants.

---

### 5. Fix `machine._stop_flag` and `.clients` direct access in `app_controller.py`
**Status:** `TODO`

**Why:** Three accesses to `machine._stop_flag.is_set()` and four direct mutations of `machine.clients`. This is the most dangerous coupling in the codebase — BotMachine's public API is not being used.

**What to do:**
- Add `machine.is_stopped` property if not already present (check line 182)
- Replace all `machine._stop_flag.is_set()` calls with `machine.is_stopped`
- Add `machine.set_clients(clients: list)` public method
- Replace all direct `self._current_machine.clients = ...` with `self._current_machine.set_clients(self._clients)`

---

### 6. Add tests for `undo_manager.py`
**Status:** `TODO`

**Why:** Core editor feature with no test coverage. Silent regressions from `model_validate()` changes or undo stack logic bugs will not be caught.

**What to do:** Create `tests/test_undo_manager.py`:
- push/pop: verify config is restored on undo
- redo: verify config is re-applied on redo
- max depth: verify oldest snapshot is dropped when 50-limit is exceeded
- debounce: verify multiple rapid edits produce one snapshot
- interleave: verify undo after structural edit (push) then param edit (begin_debounce)

---

### 7. Add `spec=SprintyClient` to FakeClient
**Status:** `TODO`

**Why:** FakeClient manually mirrors WizSprinter's API with no validation. When WizSprinter is updated (by bumping the pinned commit), the FakeClient will not detect removed or renamed methods.

**What to do:** Import `SprintyClient` in `tests/conftest.py` and add `spec=SprintyClient` to the AsyncMock/MagicMock constructors. Audit which mocked methods no longer match the actual API and update accordingly.

---

### 8. Add tests for `enter_gauntlet`, `change_realm`, and `sleep` nodes
**Status:** `TODO`

**Why:** Three nodes have no test files. `enter_gauntlet` is a production path (Zone Entry) with no validation at all.

**What to do:**
- `tests/test_enter_gauntlet.py`: success path (match entered, zone reached), wrong source zone (immediate FAILURE), queue timeout (FAILURE after threshold), cancelled match (FAILURE)
- `tests/test_change_realm.py`: success path (realm switched), timeout (FAILURE), realm not found (FAILURE)
- `tests/test_sleep.py`: success path (trivial — returns SUCCESS after sleep)

---

### 9. Add tests for `safety_warning_dialog.collect_safety_warnings()`
**Status:** `TODO`

**Why:** Safety-critical function with zero tests. A bug here means dangerous operations silently run without warning.

**What to do:** Create `tests/test_safety_warning.py`:
- Config with quicksell node having `sell_crowns=True` → warning emitted
- Config with quicksell node having all sell flags False → no warning (or warning with accurate details)
- Config with `teleport_to_friend` → warning emitted
- Config with no risky nodes → empty warning list

---

### 10. Extend safety warning coverage to more risky nodes
**Status:** `TODO`

**Why:** `change_realm`, `enter_gauntlet` (with team-size mismatches), and `handle_potions` on shared realms have no safety warnings. The current design hard-codes 3 node types in `collect_safety_warnings()`.

**What to do:** Add a `safety_warning: str | None` field to `NodeSpec` in `param_schema.py`. Nodes that are inherently risky set this field. `collect_safety_warnings()` becomes a loop over `NODE_SPECS` rather than a hard-coded if-chain. This makes the system extensible — new risky nodes just declare their warning in `NODE_SPEC`.

---

### Items Deferred

| Issue | Why Deferred |
|---|---|
| Extract `ClientManager` from `app_controller.py` | High-value but high-effort. Recovery procedure complexity makes isolation non-trivial. Defer until the next time recovery logic needs modification. |
| Decompose `handle_potions.py` god-node | Large refactor, fragile procedure. Acceptable as-is while covered by tests. Revisit when adding a new potion type or NPC location. |
| Fix loading screen watcher false-positive risk | Threshold is conservative (60s). Real-world occurrence rate is low. Acceptable until a user reports it. |
| Extract `region_manager.py` from `edit_scene.py` | Low coupling, good extraction point, but no immediate pain. Defer until touching region code. |
| Extract `ClientHealthMonitor` from `machine.py` | Watchers are tightly coupled to `pause()/resume()`. Clean extraction requires a callback interface. Defer until watcher count grows or tests require isolation. |
| `_ParamPanel` ownership cleanup | Functional, just confusing. Low day-to-day impact. |
| Recovery queue partial-completion state leak | Rare scenario (multiple clients crash in rapid succession). Acceptable as-is with a code comment. |
| FakeClient → `spec=SprintyClient` (full audit) | Item #7 above covers adding spec=. Full method audit is deferred — do it when bumping the WizSprinter pin. |
| Ambient combat watcher race window | Asyncio single-threaded; logical race requires specific interleaving. Accept current mitigation (post-lock re-check). |
