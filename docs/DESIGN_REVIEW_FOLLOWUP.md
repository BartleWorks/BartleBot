# Plan: CLAUDE.md Creation + Design Review Follow-up + Path Forward

## Context

The BartleBot design review (`docs/DESIGN_REVIEW.md`) identified issues across 4 stages and prescribed 9 fixes, all now completed. This plan covers three follow-up tasks:
1. Create a `CLAUDE.md` for future agents
2. Verify the 9 fixes and address any remaining gaps
3. Chart a prioritized path forward for deferred issues

---

## Task 1: Create `CLAUDE.md`

**File:** `c:\GitHub\BartleBot\CLAUDE.md` (new)

Target ~80 lines following official best practices: concise, specific, only what Claude can't derive from code. Use `@` imports to reference existing docs for deeper context without bloating the file.

### Content outline

```markdown
# BartleBot

Wizard101 bot automation app. Python 3.13+, PyQt6, qasync, transitions FSM.

## Quick Start
poetry install
poetry run python bartlebot.py

## Build / Test / Lint
poetry install              # all deps including dev
poetry run pytest           # pytest-asyncio, asyncio_mode = "auto"
poetry run ruff check       # lint
poetry run ruff format      # auto-format

## Architecture (overview)
- FSM engine: `bartlebot/bot/machine.py` (transitions.AsyncMachine)
- Nodes: `bartlebot/nodes/action/` and `condition/` — each exports NODE_SPEC, PARAM_SCHEMA, run()
- GUI: PyQt6 + qasync in `bartlebot/gui/`
- Bot files: JSON in `bots/`, validated by Pydantic in `bartlebot/bot/schema.py`

Reference docs (use @ imports):
@docs/ARCHITECTURE.md, @docs/FSM_ARCHITECTURE.md, @docs/BOT_SCHEMA.md,
@docs/GUI_ARCHITECTURE.md, @docs/DESIGN_REVIEW.md

## Adding a New Node
1. Create file in `bartlebot/nodes/action/<name>.py` or `condition/`
2. Export NODE_SPEC (NodeSpec), PARAM_SCHEMA (list[ParamField]), async def run(machine, clients, params) -> str
3. Register in `bartlebot/nodes/registry.py` (both NODE_REGISTRY and NODE_SPECS)
4. Action nodes return "SUCCESS"/"FAILURE"; condition nodes return "TRUE"/"FALSE"

## Key Conventions
- Timing: import from `bartlebot/nodes/timing.py` — never define local timeout constants
- Multi-client: use `poll_until_each_seen()` from `bartlebot/nodes/node_helpers.py`
- Logging: Python `logging` module, per-module `logger = logging.getLogger(__name__)`. Never print().
  Configured in `bartlebot/logging_setup.py` (RotatingFileHandler to `logs/` + GUI bridge).
- GUI theming: use THEME singleton from `bartlebot/gui/theme.py`. No hardcoded colors.
- Settings: SettingsManager singleton → `%APPDATA%/BartleBot/settings.json`
- Tests: FakeClient, FakeMachine, FakeEntity in `tests/conftest.py`. No Wizard101 process needed.

## External Dependencies
- WizWalker: game memory hooking (Git branch `development` — pinned via poetry.lock)
- WizSprinter: safe wrappers + combat DSL (pinned to commit hash in pyproject.toml)
- After game patches: update WizSprinter rev in pyproject.toml, run `poetry lock`

## Ruff
Line length 120, target py313. Rules: E, W, F, I, UP, B.
See pyproject.toml [tool.ruff] for intentional ignores (B905, E402, E501, B008).

## Preferences
- No auto-commits — only commit when explicitly asked
- Prefer library solutions over custom implementations
```

### Implementation steps
1. Create `CLAUDE.md` at project root with the content above (refined for accuracy)
2. Verify `@` imports resolve to existing files in `docs/`

---

## Task 2: Assess Remaining Issues from the 9 Fixes

**Conclusion: All 9 fixes are complete. Three minor follow-ups remain deferred with good reason.**

### Verification of completions
| Fix | Verified by |
|-----|-------------|
| 1. Python logging | `bartlebot/logging_setup.py` exists with RotatingFileHandler, _GuiBridgeHandler, startup logging |
| 2. Silent fall-through | Design review DONE status; consecutive no-transition counter in machine.py |
| 3. Callback guards | Design review DONE status; try/except wrapping in run() |
| 4. FSM-level logging | Design review DONE status; machine logs execution/timing/transitions |
| 5. Timing constants | `bartlebot/nodes/timing.py` with 7 centralized constants |
| 6. Ruff | `pyproject.toml` has full [tool.ruff] config, ruff in dev deps |
| 7. Pinned deps | pyproject.toml shows ^MAJOR.MINOR ranges, WizSprinter pinned to commit hash |
| 8. poll_until_each_seen | `node_helpers.py` helper (per design review DONE) |
| 9. Recent files consolidated | SettingsManager (per design review DONE) |

### Outstanding follow-ups (continue deferring)
1. **Pre-commit hook** (from Fix #6): Ruff works manually. Add `.pre-commit-config.yaml` when setting up CI/CD.
2. **B905 zip(strict=)** (from Fix #6): Intentionally ignored; all zip() pairs same-length gather results. Re-evaluate when more contributors join.
3. **wizwalker on branch= not rev=** (from Fix #7): Poetry solver conflict. `poetry.lock` provides deterministic builds.

**No new action items needed. Document these follow-ups in the path forward.**

---

## Task 3: Path Forward for Deferred Fixes

### Phase 1: Safety & Reliability (Next Priority)

These directly impact whether the bot can run unattended.

| Status | Item | Effort | What to do |
|--------|------|--------|------------|
| `DONE` | Idle detection (`max_consecutive_same_state`) | Low | `max_ticks` skipped — impractical because FSMs loop by design. `max_runtime_seconds` deferred — no clear use case identified. `max_consecutive_same_state` implemented as a per-node default via `NodeSpec.max_consecutive_retries` (baked into the machine config by `BotConfig.to_machine_config(node_specs=NODE_SPECS)`). Each node declares a sensible limit based on its worst-case tick duration; the machine stops and logs a WARNING when the threshold is reached. A machine-level `max_consecutive_same_state` fallback is also available on `BotMachine.__init__`. `handle_combat` was also updated to retry target acquisition up to 3× with a 3 s delay before returning FAILURE, so the "enemy not yet loaded" scenario is absorbed internally rather than burning FSM retries. |
| `DONE` | Dead client detection + reconnect | Medium | **Status watcher + full reconnect procedure implemented** (`machine.py: _client_status_watcher`, `app_controller.py: _on_clients_removed / _run_recovery_queue / _reconnect_client`). Design: background task polls `zone_name()` every `_CLIENT_STATUS_CHECK_INTERVAL` seconds; after `_CLIENT_STATUS_CHECK_FAIL_THRESHOLD` consecutive failures, the client is flagged dead — machine is paused and `on_client_disconnected(hwnd)` fires. AppController force-closes the game window, which `_client_loop / remove_dead_clients()` detects as a removal; this queues the account into `_pending_recovery` and starts `_run_recovery_queue`. Recovery relaunches the account, spams the Enter key via `PostMessageW` to advance through login loading screens and character select (hooks only activate once in-game), waits up to 120 s for `_client_loop` to hook the new instance, then calls `force_state(recovery_state)` and `resume()`. `force_state` resets both idle counters and correctly calls `self.machine.set_state()` on the `AsyncMachine` instance (was calling the missing `self.set_state()` — fixed). `stop()` is now idempotent w.r.t. its log message. `_run_recovery_queue` wraps each `_reconnect_client` call in `try/except` so an unexpected error logs the traceback and stops the bot cleanly instead of silently dying as an unhandled task. `ClientClosedError` propagating out of a node during normal execution is caught specifically in `machine.py: run()` before the general `except Exception` handler — it resets both idle counters, calls `self.pause()`, and `continue`s the loop, preventing retry-counter burn-down during the sub-second window between the crash and `_client_loop` detecting the removal. |
| `DONE` | Disconnect/ban detection | Medium | Deliberately **not** implemented via dialog path matching — the disconnect dialog uses the same generic `MessageBoxModalWindow` hierarchy as many other in-game dialogs, making reliable path-based detection impractical. Instead, the `_client_status_watcher` treats any unresponsive client (disconnect dialog, crash, freeze, or ban screen) identically: consecutive `zone_name()` failures → client flagged dead → reconnect procedure (see above). This is strictly more robust: it handles every failure mode rather than only a subset of them. The `_disconnect_watcher` stub and `DISCONNECT_POPUP_OK_PATH` placeholder have been removed. |
| `DONE` | Background watchers pause during bot pause | Low | Added `if not self._resume_event.is_set(): continue` after each sleep in `_ambient_dialog_watcher` and `_ambient_combat_watcher`. `_client_status_watcher` intentionally left running — it is the mechanism that detects failures and initiates pause/recovery. |

**Key file:** `bartlebot/bot/machine.py`

### Phase 2: Code Quality (Before Next Feature Sprint)

| Status | Item | Effort | What to do |
|--------|------|--------|------------|
| `TODO` | CI/CD pipeline | Low | GitHub Actions: `poetry install` → `ruff check` → `ruff format --check` → `pytest`. Add pre-commit hook simultaneously. |
| `TODO` | Anti-detection timing jitter | Low | Add `jitter(base, factor=0.2)` to `timing.py`. Update nodes to use it. |
| `TODO` | Decouple ambient combat watcher | Medium | Extract combat execution into `bartlebot/nodes/combat_runner.py`. Both watcher and handle_combat import from it. |
| `TODO` | Settings schema validation | Low | Pydantic model or type-check dict for `_DEFAULTS`. Validate on load. |

**Key files:** new `.github/workflows/ci.yml`, `bartlebot/nodes/timing.py`, `bartlebot/bot/machine.py`, `bartlebot/settings_manager.py`

### Phase 3: Structural Improvements (Opportunistic)

Fix when touching adjacent code:

| Status | Item | Trigger |
|--------|------|---------|
| `TODO` | Split `bot_canvas.py` (~2000 lines) | Next canvas feature (undo, subgraphs) |
| `TODO` | Split `graph_items.py` (~1000 lines) | Next node rendering or minimap change |
| `TODO` | Cross-panel widget ownership | Sidebar redesign or undo/redo |
| `TODO` | `config_changed` signal disambiguation | Any signal chain refactor |
| `TODO` | Auto-save / crash recovery | Before distribution to other users |
| `TODO` | Delete confirmation for nodes/edges | Any edit mode UX improvement |
| `TODO` | Log widget (file export, rotation) | Observability improvements |
| `TODO` | Integration tests | Next batch of nodes |

### Phase 4: Future (No Current Need)

| Status | Item | Why defer |
|--------|------|-----------|
| `TODO` | Undo/redo (QUndoStack) | Large effort, high bug risk. Auto-save (Phase 3) is the interim safety net. |
| `TODO` | Stop interrupting running nodes | Cooperative cancellation in every node. Current 5-35s wait is acceptable. |
| `TODO` | HierarchicalAsyncMachine | No use case yet. Flat FSM handles all current bots. |
| `TODO` | Dark theme | Pure polish. Token system ready when desired. |
| `TODO` | FakeClient → spec=SprintyClient | Current approach fine for 15 nodes. Revisit on API churn. |

---

## Verification

After implementation:
1. `poetry run ruff check CLAUDE.md` — ensure no syntax issues in the markdown
2. Verify each `@docs/...` import in CLAUDE.md points to an existing file
3. Read the CLAUDE.md as a new agent would — does it answer "how do I build, test, lint, and add a node?"
4. Confirm no content duplicates what's already derivable from reading code or pyproject.toml
5. Review path forward phases against the original design review to ensure nothing was missed

---

## Files to Create/Modify

| File | Action |
|------|--------|
| `CLAUDE.md` (project root) | **Create** — main deliverable |
| `docs/DESIGN_REVIEW.md` | **No changes** — already complete, serves as reference |
