# Upstream libraries

wizwalker and wizsprinter are **ours**. Both live under `The-World-Tree`, both are
maintained here rather than staged for someone else's PR queue, and both are pinned by
exact commit. The local checkout paths and repo URLs are in the root `CLAUDE.md`.

## The libraries

- **WizWalker**: low-level Wizard101 memory hooking — `The-World-Tree/wizwalker`, branch `main`
- **WizSprinter**: safe wrappers + combat DSL — `The-World-Tree/wizsprinter`, branch `main`

Both are pinned by exact commit `rev` in `pyproject.toml`, using HTTPS URLs so CI can
fetch them without SSH credentials. They carry fixes the original projects do not have,
including the `SpellListControl` / `DeckListControl` memory offsets that
`bartlebot/deck_io.py` depends on. Pointing either back at its original repo will break
deck reads.

## Where updates come from

Both began as forks of projects that still exist, and **the two relationships are not
the same**. Do not assume what is true of one holds for the other.

| | Track | How |
|---|---|---|
| **wizwalker** | `LaurenzLikeThat/wizwalker` (`development`) | **Unchanged by the move.** Laurenz is still the practical source of memory offset fixes after a game patch. Merge from her. |
| **wizsprinter** | `Deimos-Wizard101/wizsprinter` (`main`) | Selective. Take individual fixes worth having; their `main` is **not** our baseline. |

### wizwalker — still merge from Laurenz

Owning the library did not change this. `Deimos-Wizard101` is barely maintained and
resistant to PRs; Laurenz keeps a fork current, and she is the practical source of
offset fixes after a game patch.

The merge base with her `development` branch is what makes that a *merge* rather than a
manual re-derivation, so **never squash or rewrite wizwalker's history**. Losing the
common ancestor turns every post-patch update into a whole-tree conflict across every
file, permanently.

### wizsprinter — divergence is expected

We maintain this library. Changes land here directly; there is no upstream PR to open
first, and divergence from Deimos is the normal state rather than a smell. #6 is the
first change under this policy.

Pull from Deimos *selectively* — cherry-pick or merge a specific fix when it is worth
having, and read what changed before taking it:

```bash
cd ../wizsprinter && git fetch deimos && git log --oneline main..deimos/main
```

Do not fast-forward onto their `main`, and do not treat being behind it as a problem
to fix.

**Do not pull wizsprinter from Laurenz.** As of 2026-08 her fork differs from Deimos
only in packaging and docs — the library code is byte-identical — but she has not taken
the poetry→hatchling migration, so merging from her would regress the build system.

Commit counts between any two of these repos look wildly divergent (≈35 each way); that
is squash-merged PRs producing new SHAs for identical content, not real drift.
**Compare trees, not commit counts.**

## After a game update breaks memory offsets

Offsets change with most game patches. Laurenz usually fixes them first — check before
doing the work by hand.

```bash
cd ../wizwalker
git fetch laurenz
git log --oneline HEAD..laurenz/development     # anything new?
git merge laurenz/development                   # expect conflicts: our commits touch offsets too
uv run pytest                                   # then smoke-test against a live client
git push origin main
```

Then bump the pin in this repo (see below). If Laurenz has not fixed it yet, re-derive
the offsets with `tools/probe_item_cards.py` and commit them to our repo — that script
exists specifically to make offsets recoverable.

## Bumping a pin

Local edits do **not** affect the app until they are pushed and the pin moves:

```bash
cd ../wizwalker && git commit -am "..." && git push origin main
cd ../BartleBot
# update the rev in pyproject.toml to the new commit hash
uv lock && uv sync
```

While iterating rapidly, reinstall the local copy as editable instead of bumping the
pin each time — undo it before trusting a test run, since it silently overrides the
pinned version:

```bash
uv pip install -e ../wizwalker    # live editing
uv sync                           # back to the pinned commit
```

**Tag every commit you pin.** A pinned SHA reachable only from a branch tip can be
orphaned by a force-push, rebase, or branch rename, and `uv sync` then fails for
everyone including CI. Tag it *before* bumping the pin:

```bash
cd ../wizwalker && git tag bartlebot-pin-$(date +%Y%m%d) <sha> && git push origin --tags
```

Tags follow `bartlebot-pin-YYYYMMDD`. Current pins are wizwalker `26affba`
(`bartlebot-pin-20260915`) and wizsprinter `77b7539`
(`bartleby-wizsprinter-20260909`). Never
delete a tag that an older commit of this repo still pins, or you break the build at
that revision.

## Remotes and identity

Each checkout has the same shape:

| remote | points at |
|---|---|
| `origin` | `The-World-Tree/<lib>` — where our work goes |
| `laurenz` | `LaurenzLikeThat/wizwalker` — offset fixes (wizwalker only) |
| `deimos` | `Deimos-Wizard101/<lib>` — the original project |

Both checkouts set a repo-local `user.name` / `user.email`. Check
`git config --local user.email` before committing — fixing an author afterwards means
rewriting, and rewriting is what orphans a pinned SHA.
