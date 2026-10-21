# Upstream libraries

wizwalker and wizsprinter are **ours**. Both live under `BartleWorks`, both are
maintained here rather than staged for someone else's PR queue, and both are pinned by
exact commit. The local checkout paths and repo URLs are in the root `CLAUDE.md`.

## The libraries

- **WizWalker**: low-level Wizard101 memory hooking — `BartleWorks/wizwalker`, branch `main`
- **WizSprinter**: safe wrappers + combat DSL — `BartleWorks/wizsprinter`, branch `main`

Both are pinned by exact commit `rev` in `pyproject.toml`, using HTTPS URLs so CI can
fetch them without SSH credentials. They carry fixes the original projects do not have,
including the `SpellListControl` / `DeckListControl` memory offsets that
`bartlebot/combat/deck_io.py` depends on. Pointing either back at its original repo will break
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

While iterating rapidly, install the local copy as editable instead of bumping the pin
each time. **Every plain `uv run` syncs the venv back to `uv.lock` first**, which
uninstalls the editable copy and reinstalls the pinned commit without a word, so the
run that follows tests the pin, not your edit. Pass `--no-sync` on every command while
the editable copy is in:

```bash
uv pip install --no-deps -e ../wizsprinter   # live editing; --no-deps leaves the other pin alone
uv run --no-sync pytest                      # --no-sync every time, or the pin comes back
uv sync                                      # back to the pinned commit
```

Before trusting a result, check which copy was imported — this names the checkout when
the editable copy is live, and `.venv` when it is not:

```bash
uv run --no-sync python -c "import wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser as p; print(p.__file__)"
```

Never copy an edited file over the one in `.venv` instead. uv installs by hardlinking
from its cache, so the copy rewrites the cached pinned wheel too, and `uv sync` then
"restores" your edit. If it has happened, `uv cache clean <lib>` and then
`uv sync --reinstall-package <lib>`.

**Tag every commit you pin.** A pinned SHA reachable only from a branch tip can be
orphaned by a force-push, rebase, or branch rename, and `uv sync` then fails for
everyone including CI. Tag it *before* bumping the pin:

```bash
cd ../wizwalker && git tag bartlebot-pin-$(date +%Y%m%d) <sha> && git push origin --tags
```

Tags follow `bartlebot-pin-YYYYMMDD`. Current pins are wizwalker `7900369`
(`bartlebot-pin-20261021`, which waits for the friend-teleport confirmation popup by polling
its caption instead of a fixed second, on top of `4af3353`, `bartlebot-pin-20261018`, a review's fixes: polls that never yielded the event loop, the
mouseless hook's user32 writes and its 200 ms re-parse, a dead client's leaked process
handle, unbounded reads and broken string writes, and fewer reads per UI search, on top of
`override_cache_folder` for #234) and wizsprinter `e82212b`
(`bartlebot-pin-20261018`, on its `review-fixes` branch, which bounds the draw and discard
waits, reads the combatants once a round and builds the config parser once, on top of
`condition-attributes`, which names a combatant
in the turn trace by its slot and refuses a condition attribute that can never be read,
on top of `harmful-aura`, which adds the
`harmful_aura` and `stronger` template keywords, on top of `turn-trace`, which logs each turn's decisions
and casts the card a move's own enchant fused into, on top of `enchant-only-lookup`,
which looks up a card aimed at a spell among enchants only, on top of `stacks` and its
`stacks` template keyword, on top of `enchant-ahead`). A second pin on the same day takes a
letter after the date, as `bartlebot-pin-20260926b` did. Never
delete a tag that an older commit of this repo still pins, or you break the build at
that revision.

## Remotes and identity

Each checkout has the same shape:

| remote | points at |
|---|---|
| `origin` | `BartleWorks/<lib>` — where our work goes |
| `laurenz` | `LaurenzLikeThat/wizwalker` — offset fixes (wizwalker only) |
| `deimos` | `Deimos-Wizard101/<lib>` — the original project |

Both checkouts set a repo-local `user.name` / `user.email`. Check
`git config --local user.email` before committing — fixing an author afterwards means
rewriting, and rewriting is what orphans a pinned SHA.
