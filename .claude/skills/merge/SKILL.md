---
name: merge
description: Land a branch on master by direct merge — the local gate, the merge-commit message template, and the pre-merge read-through. Use when merging a branch, landing work on master, writing a merge commit message, or asking what to check before merging.
---

# Merging

Work happens on a branch and lands on `master` by a **direct merge**, not a pull
request. This is a solo project, so a PR's only reviewer was its author; what the
form was really buying was a place to write down *why*, and a CI gate.

Both are replaced rather than dropped:

- **The gate is local.** `uv run pytest -n auto` and `uv run ruff check`
  run before every commit. CI still runs — `.github/workflows/ci.yml` fires on
  `push: [master]` — but it now confirms after the fact instead of blocking. A
  red master is your own signal to fix forward immediately.
- **The narrative moves into the merge commit.** A PR body is GitHub metadata and
  will not survive the migration to the release repo; a commit message is part of
  the history and will. That makes the merge commit the *better* home for it, not
  a lesser one.

## How

```bash
uv run pytest -n auto && uv run ruff check    # the gate
git diff master...HEAD                                # the read-through a PR view used to force
git switch master
git merge --no-ff --no-commit <branch>
git commit -F <message-file>
```

`--no-ff` is not optional: the merge commit is where the message lives, and a
fast-forward has nowhere to put it. Write the message to a file and pass `-F` —
multi-line `-m` is awkward from PowerShell (see the same problem with
`gh pr create --body`).

Check what branch you are actually on before committing. A branch that was merged
and deleted earlier in the session leaves you on `master`, where a stray commit
bypasses this whole procedure.

## The message

```
<subject: what changed, imperative, ~70 chars>

Closes #N
Closes #M

<Summary — what this changes and why now, in a few lines. The diff shows what;
say why.>

<Changes — one `#N — short title` paragraph per issue on a multi-issue merge.
Include what a reader would otherwise have to reverse-engineer: a rejected
alternative and why, a limitation the tests do not cover, a deliberate behavior
change, a bug found on the way.>

<Verification — anything not obvious: a test verified to fail without the fix,
whether it was exercised against a live client, any flake seen but not chased.
A flake written down here is how it becomes an issue later instead of folklore.>

<Risk — a breaking change to bot files, settings, or a pinned library, and the
migration for it.>

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

One `Closes #N` line per issue — comma-separated keywords do not chain reliably,
and the keywords only fire on a push to the default branch, which a direct merge
to `master` is. No issue? Drop the line and say why in the summary.

## Before you merge

Not a form to tick — a list to read. It is the reminder that a reviewer used to
be. Each line names where the rule is documented and, where one exists, the test
that enforces it; the test is the durable record, so trust it over this list if
they ever disagree.

- **No real player data** anywhere in the diff or the message — wizard names,
  character or account ids, friend codes, screenshots showing them — including
  test fixtures, bot files, docstrings, and log excerpts.
  → `CLAUDE.md`; `test_no_bot_stores_a_friend_name`
- **Breaking change to bot files, settings, or a pinned library** — is the
  migration in the diff? Bot files under `bots/` are user data, and a schema
  change that is not migrated in place silently rewrites or invalidates them on
  next open. → `docs/BOT_OPTIONS.md`, `docs/UPSTREAM.md`
- **Visible GUI change** — did you look at it in both themes?
- **New node** — `NODE_SPEC` + `PARAM_SCHEMA` + `run()`, registered in *both*
  `NODE_REGISTRY` and `NODE_SPECS`, returning `SUCCESS`/`FAILURE` (or
  `TRUE`/`FALSE` for conditions). → `bartlebot/nodes/CLAUDE.md`
- **New node, background task, or non-trivial utility** — ships with a test file
  covering the success path, one failure path, and any timeout/retry behavior.
  → `tests/CLAUDE.md`
- **Widget builds anything from `THEME`** (a stylesheet, or a bare `svg_icon`) —
  inherits `ThemedMixin` first in the base list, implements `apply_theme_styles`.
  → `bartlebot/gui/CLAUDE.md`; `tests/test_theme_refresh_guard.py`
- **Widget starts an asyncio task or `QThread`** — cancelled or joined in
  `closeEvent`. → `bartlebot/gui/CLAUDE.md`
- **New option or declaration** — no bot pins a bot-wide option, borrows a name a
  NodeSpec uses, or declares a user-owned one. → `docs/BOT_OPTIONS.md`;
  `test_no_bot_pins_a_bot_wide_option_for_one_node`,
  `test_no_bot_declares_an_option_borrowed_from_a_node_it_does_not_run`,
  `test_no_bot_declares_a_user_owned_option`
- **Timing** — constants imported from `bartlebot/nodes/timing.py`, none defined
  locally; tests compress cadences, never ceilings. → `tests/CLAUDE.md`
- **No private attributes** of another class touched. → `CLAUDE.md`
- **No blocking Qt `.exec()`** from inside an asyncio task.
  → `bartlebot/gui/CLAUDE.md`
- **Pinned library changed** — commit tagged `bartlebot-pin-YYYYMMDD` and pushed
  before the rev bump, `uv lock` run. → `docs/UPSTREAM.md`

## When a PR is still right

Rarely, and always for a reason you can name: a change you want to sit on
overnight, or one where GitHub's review view genuinely helps you read a large
diff. Open one, and the board's `In progress` automation will still fire. It is
an exception, not a fallback.
