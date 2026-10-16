# Issues, the pointer, and the stage merge

`docs/PROCESS.md` governs issues and the board. This file covers what a plan adds to it.

## The stage issue

The title is `<Stage name>: <what it lands>`, for example "Authoring variables: the
Variables section, condition rows, and conditions on the canvas".

```markdown
Stage N of the <plan name> plan for #E: <page url>

## Problem

What is missing or wrong once the earlier stages have landed. Write it from the user's
or the author's side, not the code's.

## Approach

The stage card restated. Use bold lead-ins per part, name real symbols, and include the
rules that matter. Give enough that the work can start from the issue without the page.

## Done when

- The stage card's Done when, word for word.

## Docs

The card's Docs line.
```

Write each body to a file in the scratchpad and pass `--body-file`. Never pass a
multi-line `--body` inline from PowerShell.

## Filing

```bash
R=BartleWorks/BartleBot

# One issue, with one area label, plus a milestone only if it blocks going public
gh issue create -R $R --title "…" --body-file <file> --label area/engine --milestone 1.0.0

# Its type (Feature, Task, Bug or Epic)
gh api -X PATCH repos/$R/issues/<n> -f type=Feature

# Link it under the epic. sub_issue_id is the issue's id, not its number
id=$(gh api repos/$R/issues/<n> -q .id)
gh api -X POST repos/$R/issues/<E>/sub_issues -F sub_issue_id=$id

# Put it on the board and set Product = BartleBot
gh project item-add 1 --owner BartleWorks --url https://github.com/$R/issues/<n>
gh project field-list 1 --owner BartleWorks --format json   # field and option ids
gh project item-edit --project-id <id> --id <item id> --field-id <Product field id> --single-select-option-id <BartleBot option id>
```

File the issues in landing order, so their numbers read in sequence (#186 to #190).
Check the links with `gh api repos/$R/issues/<E>/sub_issues -q '.[].number'`.

**The epic** is usually the issue the plan started from. Set its type to `Epic`, and
add a line to its body linking the page. Its own text stays as written.

**Board status.** Set `In progress` by hand when a stage's branch starts. `Done` follows
from the `Closes` line, so never set it by hand.

## The stage merge

This uses the `merge` skill's template, with these specifics:

- **Subject:** `<Stage title> (Stage N of epic #E)`, for example "Authoring variables
  (Stage 2 of epic #16)".
- `Closes #<stage issue>`. The last stage adds `Closes #E` on its own line.
- **Summary:** what the stage changes and why now. End with "The plan is the <name>
  page linked from #E; `docs/<FEATURE>.md`'s <section> is the part of it that governs
  code."
- **Changes:** the As built's What was done, as prose. Follow it with "Two things the
  issue did not ask for, both found on the way:" when that applies, and with
  "Deviation from the plan: …".
- **Verification:** the suite's count, ruff, what ran under the repaint tracer or
  against a live client, what was rendered offscreen in which themes, what wasn't run,
  and any flake with its test name.
- **Risk:** bot file and settings compatibility, and any intended visible change to
  shipped bots.

The As built box and the merge message describe the same facts. Write the box first,
then the message from it.

## The pointer: a section in `docs/EPICS.md`

Write it in Stage 1, and rewrite it in its finished form at close-out. The file's opening
already says the pages are private, how to read one, and that a complete page is frozen,
so a section never repeats it. *Runtime control flow* is the model. Its finished form:

```markdown
## <Plan name>

Epic #E: <the feature in one line>. <page url>

**Complete: all N stages landed by YYYY-MM-DD.**

| stage | issue | what |
|---|---|---|
| 1 | #a | … |

`docs/<FEATURE>.md` is the feature as built: …

After it, each needing a plan of its own: … (#x), … (#y). The stages' leftovers are
#… to #….

The rules every stage kept:

- **Rule in bold.** One-line reason.
```

While the work is in progress, the status line says "The page is kept current while the
stages land", and the table has a status column.

Add the plan's name to the `docs/EPICS.md` row of `CLAUDE.md`'s "Where the rest lives"
table, so the row keeps listing every epic it indexes.
