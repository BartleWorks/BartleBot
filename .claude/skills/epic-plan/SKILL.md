---
name: epic-plan
description: Plan and carry out a multi-stage epic the way Subgraph Calls, Official and Shared Content, and Runtime Flow Control were done — a claude.ai plan page (goals, model, lessons from other tools, decisions with the alternative each beat, stage cards with Done when, As built boxes with deviations and open items, a decisions log), one issue per stage under an Epic, one branch per stage merged to master with the suite green, and the page kept current until it is frozen. Use when starting a plan for an epic or large feature, executing or landing the next stage of one, writing a stage's As built box, or closing a plan out.
---

# Epic plans

The last three epics ran the same way, and this skill is that way written down.
The three plans, worth reading when unsure what "good" looks like (read them with the
Artifact tool's `read` action, never a web fetch):

| page | epic | pointer doc |
|---|---|---|
| Runtime Flow Control: https://claude.ai/artifact/VnoJzdBGUjJw4a8gZG9U5M | #16 | `docs/RUNTIME_CONTROL_FLOW.md` |
| Official and Shared Content: https://claude.ai/artifact/4A9XUEeoTXQAfohUHP3F1n | #173 | `docs/SHARED_CONTENT.md` |
| Subgraph Calls: https://claude.ai/artifact/66pY2mPVrPYosopv1yaNNe | successor to #37 | `docs/SUBGRAPH_CALLS.md` |

Runtime Flow Control is the most refined of the three, so the template follows it.
**Never republish any of the three**: they are frozen records.

## The pieces

| piece | what it is | lifetime |
|---|---|---|
| **The plan page** | a private claude.ai artifact: the design, and each stage's record as built | kept current while stages land, then frozen |
| **The epic issue** | type `Epic`; each stage is a linked sub-issue | closed by the last stage's merge |
| **A stage issue** | restates its stage card, so work can start from the issue alone | closed by its stage's merge |
| **A stage branch** | one per stage, named for it (`variables-engine`, `edge-assignments`) | merged with `--no-ff`, then deleted |
| **`docs/<PLAN>.md`** | the pointer: the page's link, the stage-to-issue table, the rules every stage keeps | updated at the start and at the close |
| **`docs/<FEATURE>.md`** | the feature as built: the doc that governs code | written by the stages, maintained afterwards |

The page records why the feature is the way it is. The feature doc says how it works
now. A later fix goes in the feature doc, never on a frozen page.

## Phase 1: Draft the plan

1. **Read first.** Read the issue or issues behind the epic, the code the plan will
   touch, and the docs that govern it. Then survey real tools that solved the same
   problem (web search; programming languages, state-machine and visual-scripting tools,
   game and automation tools). Both the lessons table and the decisions cite what the
   survey finds, and the footer lists its sources.
2. **Write the page** from [assets/plan-template.html](assets/plan-template.html),
   section by section as [references/plan-page.md](references/plan-page.md) describes.
   Before the first publish, do whatever the Artifact tool requires (quickstart, the
   artifact-design skill). Then use this template anyway: it is the house style, it
   already meets the artifact contract, and the plans should look like one series. Keep
   the template's CSS whole, even the classes a page doesn't use yet. Later stages tend
   to need them.
3. **Publish it.** The title is the plan's name in two to four words. Use the icon
   `plan`, and a one-sentence description. Give the user the link.
4. **Settle every open decision before the stage that depends on it starts.** Mark an
   unsettled decision with the `proposed` pill and list it in the decisions log as
   "Proposed, to confirm before Stage N starts". Put each one to the user with
   AskUserQuestion, recommended option first. When the user settles it, remove the pill,
   fill in the "Rejected:" line, and add a dated log entry. A plan whose decisions keep
   changing during review is normal: Runtime Flow Control dropped edge assignments and
   then brought them back on the same day, and the log records both.

## Phase 2: File the work

Show the user the list of issues you mean to file (titles, types, areas, milestone),
and file them once they agree. [references/issues.md](references/issues.md) has the
body templates and the `gh` commands.

- **The epic.** Usually an existing issue that becomes type `Epic`. Its body links the
  page.
- **One issue per stage, filed in landing order.** Its body opens with
  `Stage N of the <plan> plan for #E: <page url>` and then has Problem, Approach, Done
  when and Docs, restated from the stage card. Each issue needs a type, exactly one
  `area/*` label, the board with `Product = BartleBot`, and a sub-issue link to the
  epic. Give it a milestone only if it blocks going public.
- **Put the numbers on the page.** Add them to the eyebrow's issue range, the status
  strip, each stage card's Issue row, and a log entry: "Stage issues filed as
  sub-issues of #E: #a, #b, …". Republish.
- **Write the pointer doc** `docs/<PLAN>.md` and its row in `CLAUDE.md`'s "Where the
  rest lives" table. Both go in Stage 1's branch.

## Phase 3: Execute a stage

**One stage per request.** The user asking for a stage is the request to commit on its
branch and merge it. It never extends to the next stage, and it never covers a push.

1. **Start clean.** Branch from an up-to-date `master` with a clean tree. Set the stage
   issue to `In progress` on the board by hand, because nothing sets it automatically.
2. **Re-read before building.** Read the stage card, its Done when, and every open item
   an earlier stage left "For #N" on this stage's issue. Those items are part of this
   stage's work.
3. **Build in small commits on the branch.** Each item in Done when is a contract and
   gets a test. The docs named on the card's Docs line are written in this stage, not
   later.
4. **The gate is green at every merge.** Run `uv run pytest -n auto` and
   `uv run ruff check` on the final tree. Never merge while either is red. A failure
   this stage caused is fixed, never skipped or marked `xfail`. A flake gets rerun
   alone, and you record which test failed, how often, and that it passed alone. Write
   that in As built and in the merge message, and don't chase it unless it is this
   stage's. Note the suite's test count, since the As built box cites it.
5. **Say what wasn't checked.** That covers the live app, the game, and both themes for
   any visible change. Offscreen renders count as "rendered offscreen and looked at",
   not as having been run. Anything unchecked becomes an open item.
6. **Write the As built box before merging**, following
   [references/as-built.md](references/as-built.md), so the box and the merge message
   say the same thing. The stage card's planned text stays as written, and the box
   records what changed.
7. **Merge with the `merge` skill.** The subject is `<Stage title> (Stage N of epic #E)`,
   with `Closes #<stage issue>`. The last stage also closes the epic. If the work isn't
   pushed, the As built says "Not yet pushed, so `Closes #N` fires on the next push."
8. **Update the page and republish it to the same URL.** Read the live page first and
   build from the file the read saves; [references/plan-page.md](references/plan-page.md)
   lists everything that changes after a merge. Briefly: the strip, the eyebrow, the As
   built box with the merge SHA, earlier open items this stage resolved, a log entry,
   and a "Since Stage N" line in the footer.
9. **Report, then stop.** Give the merge SHA, the test count, the deviations, the open
   items and what the next stage is, plus the page link. Don't start the next stage.

**When a stage turns out bigger than planned,** split it into parts (Subgraph Calls'
10.1 to 10.6). Each part gets its own As built box, and the split gets a log entry.
**When the order has to change,** move the stage, update the deps strip, and log why.
Never quietly redo an earlier stage's work in a later one.

## Phase 4: Close out

After the last stage lands:

1. **Every open item on every stage gets a destination**, written in bold after it:
   Done in Stage N, Closed by Stage N, Filed as #N, Accepted (no issue), or Left by
   decision (no issue). File whatever needs an issue, and confirm the list with the
   user first.
2. **Later stages and follow-ups become issues.** The dashed "Later" cards link them,
   and each one that needs design is "a plan of its own".
3. **Mark the page complete.** Add "Complete" to the eyebrow. The lede says every stage
   is done, that the plan is complete and no longer maintained, and that the page
   stands as the record of the design. The final log entry is "The plan is complete."
   followed by every issue filed at close-out.
4. **Rewrite the pointer doc in its finished form** (template in
   [references/issues.md](references/issues.md)), and update its `CLAUDE.md` row to say
   complete.
5. **Save a project memory**: the epic, its completion date, and "the page is a frozen
   record; never republish it". Then publish the page for the last time.

## Rules every plan keeps

- **The planned text never changes after its stage lands.** A reader has to see both
  what was planned and what was built, so the As built box records the difference.
  Before a stage lands, its card can be revised freely, with a log entry.
- **Every deviation gives its reason.** A change the plan didn't foresee starts with
  "Not in the plan:". A deviation that changes a later stage also becomes an open item
  "For #N", or goes in a page-level Deviations section when there are many.
- **Every open item gets a destination** by close-out. An item left as "should probably"
  turns into folklore.
- **`master` is green after every merge**, so each stage can be shipped or reverted on
  its own.
- **Each stage builds only on the stages before it.** A stage that needs a later one is
  in the wrong order.
- **Be honest about verification.** Record test counts, what ran in the live app and in
  game, which themes were checked, and flakes seen but not chased.
- **No real player data** on the page or in an issue, including log excerpts and worked
  examples. Pages can be shared, and the repo goes public.
- **Write in American English and plain declarative sentences**, with real symbol names
  in code. [references/plan-page.md](references/plan-page.md) has the voice.
