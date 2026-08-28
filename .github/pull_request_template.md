Closes #

<!--
One `Closes #N` line per issue — comma-separated keywords do not chain reliably.
Keywords only fire on merges into the default branch (`master`).
No issue? Delete the line and say why in the summary.
-->

## Summary

<!-- What this changes and why now, in a few lines. The diff shows what; say why. -->


## Changes

<!--
One `## #N — short title` section per issue for multi-issue PRs.
Include what a reviewer would otherwise have to reverse-engineer: a rejected
alternative and why, a limitation the tests do not cover, a deliberate behavior
change, a bug found on the way.
-->


## Verification

- [ ] `poetry run pytest -n auto` passes
- [ ] `poetry run ruff check` and `poetry run ruff format` clean
- [ ] Exercised against a live client, or N/A because:

<!--
Note any test verified to fail without the fix (load-bearing), and any flake you
saw but did not chase — a flake mentioned in a PR body is how it becomes an issue
later instead of folklore.
-->

## Screenshots

<!--
Required for any visible GUI change: before and after. Check both themes if the
change touches colors or stylesheets.
Delete this section if nothing visible changed. Redact any player data first.
-->

## Risk

- [ ] **No real player data** anywhere in this PR — wizard names, character or
      account ids, friend codes, or screenshots showing them — including test
      fixtures, bot files, docstrings, log excerpts, and this PR body
- [ ] Breaking change to bot files, settings, or a pinned library — **describe the
      migration below**, or leave unchecked

<!--
Bot files under `bots/` are user data. A schema change that is not migrated in
place silently rewrites or invalidates files on next open; see "App settings vs.
node params" in CLAUDE.md for how the quicksell params were handled.
-->

<details>
<summary>Convention checks — tick what applies, delete the rest</summary>

- [ ] **New node**: `NODE_SPEC` + `PARAM_SCHEMA` + `run()`, registered in *both*
      `NODE_REGISTRY` and `NODE_SPECS`, returning `SUCCESS`/`FAILURE` (or
      `TRUE`/`FALSE` for conditions)
- [ ] **New node, background task, or non-trivial utility**: ships with a test file
      covering the success path, one failure path, and any timeout/retry behavior
- [ ] **Widget sets a stylesheet from `THEME`**: overrides `changeEvent` to re-apply
      it on `QEvent.Type.StyleChange`
- [ ] **Widget starts an asyncio task or `QThread`**: cancelled or joined in
      `closeEvent`
- [ ] **Timing**: constants imported from `bartlebot/nodes/timing.py`, none defined
      locally; compressed cadences in tests, never ceilings
- [ ] **No private attributes** of another class touched (`machine._stop_flag` →
      `machine.is_stopped`)
- [ ] **No blocking Qt `.exec()`** called from inside an asyncio task
- [ ] **Pinned library changed**: commit tagged `bartlebot-pin-YYYYMMDD`, pushed,
      and `pyproject.toml` rev bumped with `poetry lock`

</details>
