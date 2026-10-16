# Issue and project tracking

Issues, the backlog board, and how work lands. The merge procedure itself is the `merge` skill.


Issues live in the GitHub repo; the backlog board is the **org-level** project
**The World Tree Backlog** (`gh project … --owner BartleWorks`, number 1), linked to
BartleBot, BartleBuddy and BartleBuilder.

The board is org-level because **milestones are per-repo and cannot cross products**.
Its `Product` field carries what a shared milestone would have.

Three mechanisms, three questions. Do not make one do another's job:

| | answers | cardinality |
|---|---|---|
| **Issue type** | what kind of work is this | one per issue, org-wide |
| **Labels** | what is this about | many per issue, permanent |
| **Milestone** | when does it ship | one per issue, closes |
| **Project board** | what am I doing next | one board, ongoing |

Issue types are **org-level** (`Bug` / `Feature` / `Task` / `Epic`), so they mean the same
thing in all three repos. Labels are **per-repo** and carry `area/*` only — do not
reintroduce `bug`, `enhancement`, `blocked` or `epic` as labels. The first two are issue
types now, `blocked` is `Status = Blocked` on the board, and `epic` is the `Epic` type.

Two of the board's built-in workflows still run: an issue is auto-added at
`Backlog`, and moves to `Done` when it closes — which a `Closes #N` line in a
merge commit does, exactly as a PR body used to. Do not hand-set `Done`.

The third, `In progress` on a linked pull request, no longer fires, because work
lands by direct merge (the `merge` skill). `In progress`, `Ready` and
`Blocked` are all set by hand now, and all three are judgment calls.

Closing an issue by dragging its card to `Done` is deliberately *not* enabled —
the **Auto-close issue** workflow is off, so a mis-drag cannot close an issue with
no commit trail behind it.

## Filing

- **A type and one label, always, at creation**: an issue type (`Bug` / `Feature` /
  `Task` / `Epic`) and exactly one `area/*`. If two areas fit equally well, it is
  probably two issues.
- **On the board, with its Product set, at creation.** Every new issue goes on The World
  Tree Backlog with `Product` set to the repo it was filed in. The board is shared by
  three products, and an issue with no `Product` drops out of every per-product view.
- **A milestone is a commitment.** Add `1.0.0` only if it blocks going public. No
  milestone is the default and carries no stigma.
- **Priority and Size are set at triage, not at creation.** Filing is cheap;
  estimating is not. Leave them empty and sweep them periodically.
- **Never put work items in a markdown checklist.** A checklist line cannot be
  labeled, scheduled, assigned, or closed. More than about three sub-tasks means
  the issue is an `Epic`: set that type, decompose it into real sub-issues and link them
  (`POST /repos/:owner/:repo/issues/:n/sub_issues`).

## Bodies

- **Bug**: what happened, what you expected, how to reproduce, and the relevant log
  excerpt — redacted per *No real player data*, which applies to issue bodies and
  pasted logs just as much as to committed files.
- **Feature**: the problem first, then a sketch of the approach. Explain *why* before
  *what* — an approach with no problem behind it cannot be reviewed or dropped.

## Closing

- **Issues close via a merge commit, not by hand.** Every merge into `master`
  carries one `Closes #N` line per issue it resolves. Multi-issue merges are
  normal here; each issue still needs its own line, because comma-separated
  keywords do not chain reliably.
- Closing keywords fire on any push to the **default branch** (`master`), which
  is what makes this work without a pull request.
- **Close by hand only for won't-do or duplicate**, and set the reason to
  `not planned` so the issue does not read as completed.

## Merging

Branches land on `master` by **direct merge, not a pull request** — the full
convention, the merge-commit message template, and the pre-merge read-through
are in the `merge` skill. The three things that matter here:

- **`git merge --no-ff`, always.** The merge commit is where the message lives,
  and a fast-forward has nowhere to put it.
- **The message carries what a PR body used to** — why now, the rejected
  alternative, the limitation the tests do not cover, the `Closes #N` lines. A
  PR body is GitHub metadata and will not survive the migration to the release
  repo at 1.0.0; a commit message will.
- **The gate is local.** `uv run pytest -n auto` and `uv run ruff check`
  before every commit. CI runs on push to `master` and now confirms after the
  fact rather than blocking, so a failure there is fixed forward at once.

## Board

- `Ready` is a pull queue, not a wish list — it means scoped enough to start
  without further thinking. Keep it short.
- `In progress` is set by hand when work genuinely starts. Nothing sets it for
  you any more: it was driven by a linked pull request, and there is no longer
  one. A card left in `Backlog` while its branch exists is the failure mode to
  watch for.
- `Blocked` needs a comment naming the blocker, or it is just a stale `Ready`.

## Areas

One `area/*` label per issue. Three exist in **every** product repo, so a board filter
on them means the same thing across BartleBot, BartleBuddy and BartleBuilder:

| label | scope |
|---|---|
| `area/app-ui` | app shell, sidebar, settings, windowing |
| `area/clients` | client launching, accounts, characters |
| `area/meta` | project, process, release |

The rest are **BartleBot-only**, because they name things the other products do not have:

| label | scope |
|---|---|
| `area/editor` | bot canvas and node editing UX |
| `area/engine` | FSM, run semantics, control flow |
| `area/nodes` | node library |
| `area/bot-library` | shipped bot files under `bots/` |
| `area/combat` | combat DSL, decks, fight tables |

Replicating all eight into every repo was considered and rejected: BartleBuddy has no
bot canvas, and a label that can never apply is noise in every picker it appears in.
When BartleBuddy needs an area of its own, add it there rather than everywhere.
