# Upstream libraries

wizwalker and wizsprinter are **ours**. Both live under `BartleWorks`, both are
maintained here rather than staged for someone else's PR queue, and both are pinned by
exact commit. The local checkout paths and repo URLs are in the root `CLAUDE.md`.

## The libraries

- **WizWalker**: low-level Wizard101 memory hooking — `BartleWorks/wizwalker`
- **WizSprinter**: safe wrappers + combat DSL — `BartleWorks/wizsprinter`

Both are pinned by exact commit `rev` in `pyproject.toml`, using HTTPS URLs so CI can
fetch them without SSH credentials. They carry fixes the original projects do not have,
including the `SpellListControl` / `DeckListControl` memory offsets that
`bartlebot/combat/deck_io.py` depends on. Pointing either back at its original repo will break
deck reads.

## Branches

Both repos keep the same two long-lived branches:

| branch | holds | moves when |
|---|---|---|
| `main` | what BartleBot may pin | `dev` has been run in game: `git merge --ff-only dev`, then tag |
| `dev` | merged work, its test suite green | a feature branch is done |

A change is a feature branch off `dev`, merged back into it. **Pin only from `main`.**
`main` only ever fast-forwards to `dev`, so the two cannot drift and promoting is one
command; a fix that cannot wait goes on `main` and is then merged into `dev`.

`dev` is the state that had no home before: passing the suite but not yet tried in
game. Without it, wizsprinter's pins sat on the tip of a stack of feature branches
(`enchant-ahead` through `review-fixes`) while its `main` fell behind; those were folded
into `main` with `bartlebot-pin-20261021`.

Each library has its own suite (`uv sync && uv run pytest` in the checkout), against fakes
of the game's memory and windows: run it before merging into `dev`. wizsprinter's runs
against our wizwalker at the commit pinned here, named in its dev dependency group, so
move that with this pin. It sits there and not in `[tool.uv.sources]` because uv reads a
git dependency's sources too, and two wizwalker pins then refuse to lock.

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
doing the work by hand. `docs/GAME_UPDATES.md` is the whole procedure for an update, from
the Test realm on: which hooks it breaks, the type dump, and replacing a pattern ourselves.
Changes for an update not yet live go on a branch named for it, here and in the library,
and none merges until the update is live.

```bash
cd ../wizwalker
git fetch laurenz
git switch dev
git log --oneline HEAD..laurenz/development     # anything new?
git merge laurenz/development                   # a real merge, never squashed or rebased;
                                                # expect conflicts: our commits touch offsets too
uv run pytest                                   # then smoke-test against a live client
git switch main && git merge --ff-only dev
git push origin main dev
```

Then bump the pin in this repo (see below). If Laurenz has not fixed it yet, re-derive
the offsets with `tools/probe_item_cards.py` and commit them to our repo — that script
exists specifically to make offsets recoverable.

## Bumping a pin

Local edits do **not** affect the app until they reach `main`, are pushed, and the pin
moves:

```bash
cd ../wizwalker
git switch main && git merge --ff-only dev      # once dev has been run in game
git tag bartlebot-pin-$(date +%Y%m%d) main
git push origin main dev refs/tags/bartlebot-pin-$(date +%Y%m%d)
cd ../BartleBot
# update the rev in pyproject.toml to main's full commit hash
uv lock && uv sync
```

Bumping wizwalker's pin also means moving the wizwalker reference in wizsprinter's dev
dependency group, on its `dev`, so its suite keeps testing against what ships.

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
cd ../wizwalker && git tag bartlebot-pin-$(date +%Y%m%d) main && git push origin refs/tags/bartlebot-pin-$(date +%Y%m%d)
```

Tags follow `bartlebot-pin-YYYYMMDD`. A second pin on the same day takes a letter after
the date, as `bartlebot-pin-20260926b` did. Never delete a tag that an older commit of
this repo still pins, or you break the build at that revision. The tags are also what
keeps a pin reachable once the feature branch it was made on is deleted.

Current pins, both on `main`:

- **wizwalker `8783db0`** (`bartlebot-pin-20261021b`): its test suite, `utils.poll_until`,
  and a review's fixes. The ones BartleBot meets: a port to a friend from a friends list
  left on a later page went to whoever sat in that row there; `in_battle()` never
  returned before the client hook had run; `DeckBuilder.open_deck_page` never recorded
  the deck page (so `deck_io.apply_deck` no longer sets `_on_deck_page` itself);
  `clear_deck` answered its confirm box before the box was up; and a combatant's Health
  window was never waited for. The deck page, friends list and message boxes are polled
  where they were fixed waits, and the round waits read the duel every 0.2 s, not 0.5 s.
  On top of `7900369` (`bartlebot-pin-20261021`), which polls for the friend-teleport
  confirmation.
- **wizsprinter `6f06c87`** (`bartlebot-pin-20261021`): its test suite, including copies
  of the `SprintyCombat` tests in `tests/wizsprinter` here, which stay as this repo's
  guard on what it pins while the forks have no CI; each card effect read once
  however many requirements ask about it; an enchant's fuse seen when it lands rather
  than after a fixed `cast_time * 2`; `CombatConfigProvider.from_string`; and the
  generator backend's unawaited round. On top of `e82212b` (`bartlebot-pin-20261018`) and
  the feature branches before it, which `git log` and the earlier tags record.

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
