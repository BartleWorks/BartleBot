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
| `TODO` | Max iterations / max runtime / idle detection | Low | Add `max_ticks`, `max_runtime_seconds`, `max_consecutive_same_state` to `BotMachine`. Counter and timer in the run loop. |
| `TODO` | Dead client detection | Medium | Health check before each tick in `BotMachine.run()` — call a lightweight WizWalker method, mark client dead after 3 consecutive `MemoryReadError`. |
| `TODO` | Disconnect/ban detection | Medium | Check zone name or window state for login/disconnect indicators before each node execution. |
| `TODO` | Background watchers pause during bot pause | Low | Check `_resume_event.is_set()` in watcher loops. One-line fix per watcher. |

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
