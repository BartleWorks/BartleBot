# Upstream libraries

wizwalker and wizsprinter are forks with different upstreams, updated for different reasons. The local checkout paths and repo URLs are in the root `CLAUDE.md`.

## External Dependencies

- **WizWalker**: low-level Wizard101 memory hooking — fork `The-World-Tree/wizwalker`, branch `bartlebot`
- **WizSprinter**: safe wrappers + combat DSL — fork `The-World-Tree/wizsprinter`, branch `main`

Both are pinned by exact commit `rev` in `pyproject.toml`, using HTTPS URLs so CI
can fetch them without SSH credentials. They are **forks, not upstream**: they
carry fixes upstream does not have, including the `SpellListControl` /
`DeckListControl` memory offsets that `bartlebot/deck_io.py` depends on. Pointing
either back at its original repo will break deck reads.

## Where updates come from

The two libraries have *different* upstreams, for different reasons. Do not
assume what is true of one holds for the other.

| | Track | Why |
|---|---|---|
| **wizwalker** | `LaurenzLikeThat/wizwalker` (`development`) | `Deimos-Wizard101` is barely maintained and resistant to PRs. Laurenz keeps a fork current, and she is the practical source of memory offset fixes after a game patch. |
| **wizsprinter** | `Deimos-Wizard101/wizsprinter` (`main`) | Upstream here *is* responsive — PRs #7–#14 were merged, several from Laurenz. Her fork is not a parallel line of development; her work reaches us through Deimos. |

**Do not pull wizsprinter from Laurenz.** As of 2026-08 her fork differs from
Deimos only in packaging and docs — the library code is byte-identical — but she
has not taken the poetry→hatchling migration, so merging from her would regress
the build system. Commit counts between the two look wildly divergent (≈35 each
way); that is squash-merged PRs producing new SHAs for identical content, not
real drift. Compare trees, not commit counts.

Our fork carries **no** wizsprinter changes of its own: past work was accepted
upstream, so `The-World-Tree/wizsprinter` should stay a fast-forward of Deimos.
Divergence there is a smell.

## After a game update breaks memory offsets

Offsets change with most game patches. Laurenz usually fixes them first — check
before doing the work by hand.

```bash
cd ../wizwalker
git fetch laurenz
git log --oneline HEAD..laurenz/development     # anything new?
git merge laurenz/development                   # expect conflicts: our commits touch offsets too
uv run pytest                               # then smoke-test against a live client
git push origin bartlebot
```

Then bump the pin in this repo (see below). If Laurenz has not fixed it yet,
re-derive the offsets with `tools/probe_item_cards.py` and commit them to our
fork — that script exists specifically to make offsets recoverable.

For wizsprinter, the same shape at a much lower cadence:

```bash
cd ../wizsprinter && git fetch deimos && git merge deimos/main
```

## Bumping a pin

Local edits do **not** affect the app until they are pushed and the pin moves:

```bash
cd ../wizwalker && git commit -am "..." && git push origin bartlebot
cd ../BartleBot
# update the rev in pyproject.toml to the new commit hash
uv lock && uv sync
```

While iterating rapidly, reinstall the local copy as editable instead of bumping
the pin each time — undo it before trusting a test run, since it silently
overrides the pinned version:

```bash
uv pip install -e ../wizwalker    # live editing
uv sync                           # back to the pinned commit
```

**Tag every commit you pin.** A pinned SHA reachable only from a branch tip can
be orphaned by a force-push, rebase, or branch rename, and `uv sync` then fails
for everyone including CI. Tag it *before* bumping the pin:

```bash
cd ../wizwalker && git tag bartlebot-pin-$(date +%Y%m%d) <sha> && git push origin --tags
```

Tags follow `bartlebot-pin-YYYYMMDD`. Current pins are wizwalker `ba5c32c`
(`bartlebot-pin-20260828`) and wizsprinter `2118aa7` (`bartlebot-pin-20260802`).
Never delete a tag that an older commit of this repo still pins, or you break the
build at that revision.

**Commit in the fork with the fork's identity.** `../wizwalker` has a repo-local
`user.name` / `user.email` set in the checkout. Check `git config --local user.email`
before committing there — fixing an author afterwards means rewriting, and rewriting
is what orphans a pinned SHA.
