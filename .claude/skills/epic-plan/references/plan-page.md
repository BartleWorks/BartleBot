# The plan page, section by section

The page is one reading column beside a sticky contents rail. Sections come in this
order, and every one of them is required unless it is marked optional. The class names
are the ones in `assets/plan-template.html`.

## Header

- **Eyebrow** (`.eyebrow`): a row of short spans: `BartleBot`, `Epic #E` (linked), the
  stage issue range (`#186 – #190`), `Milestone 1.0.0` when it has one, then status
  spans that build up as the work goes: `Stages 1 and 2 landed 2026-10-07`, and
  finally `Complete`.
- **Title** (`h1`): the feature, in sentence case ("Runtime flow control"). The
  `<title>` uses the artifact's title case ("Runtime Flow Control").
- **Lede** (`.lede`): what the feature gives the user, in two to four sentences, with
  the key nouns in `<strong>`. Then "This page is the plan for epic #E. Each stage has
  its own issue, which restates its part." At close-out it also says the plan is
  complete and no longer maintained.
- **Status strip** (`.stages`): one `.stg` per stage, with `N · #issue` and a short
  title. Add `.here` while the stage is in progress, and `.done` plus
  `<span class="s">Landed</span>` once it lands. Later stages use `.stg.later` (dashed).
  A `.legend-row` sits below the strip.

## The goal (`#goal`)

- A **headline** (`.headline`) of one sentence: the outcome, in terms of what a bot or
  a user can now do.
- A paragraph on the problem today, ending with "Every decision below is judged against
  these outcomes, in order of weight."
- **Numbered goals** (`.goals` > `.goal`), usually three or four, with the bold
  first sentence as the goal and the rest as what it means.
- **"These stay exactly as they are:"**, a list of the invariants the plan must not
  break: round-trip byte-identical files, the restricted-zone check, data-only share
  files, and whatever else applies. Stages are checked against this list.

## Checking the plan against the goals (`#check`)

- **Goal 1 against other tools.** A table with the columns *feature | where it comes
  from | in BartleBot | status*. Status is a `.st` span: `have` (Have), `new` (Stage
  N), `warn` (Later · #N), or `out` (Ruled out / Out of scope). Every feature that a
  comparable tool offers appears, including the ones ruled out on purpose.
- **"How we'll know."** A table with the columns *goal | delivered by | we'll know
  when*. Each "we'll know when" is something observable or testable, and it usually
  becomes a Done when item.

## The model (`#model`)

- A **keyline** (`.keyline`): the whole model in two or three sentences, the ones
  you'd want someone to remember.
- **One inline SVG figure** of who writes, reads or owns what, drawn with the `.sv-*`
  classes so it follows the theme. Give it a `<title>` that says the whole picture in
  words, and a `figcaption` that says which stages bring which parts.
- **A comparison table** against the nearest existing concept (option versus variable,
  linked versus copied, function concept versus subgraph equivalent).
- **A worked example** from the real library, with numbers before and after (nodes,
  edges, wasted steps, seconds). Name real bots and dungeons, and never real players.
- **"When not to use one."** The case where the feature is the wrong tool, and what to
  use instead.

## Lessons from other tools (`#lessons`)

A table with the columns *lesson | learned by | in this plan*. Each lesson is a failure
or a success some real tool had, stated as a general truth ("Variables created by their
first write turn every typo into a new variable"). "Learned by" names the tool and the
specific feature. "In this plan" names the decision or stage that applies it. Aim for
roughly ten rows, and cite the sources in the footer.

## What we decided, and why (`#decisions`)

Open with: 'The tag under each names the tools that settled it. "Rejected" names the
alternative and why it lost.' Then one `.dec` card per decision:

```html
<div class="dec">
  <h4>The decision, stated as a fact</h4>
  <p>What it means concretely: names, rules, the edge cases it settles.</p>
  <p class="was"><b>Rejected:</b> the alternative, and the concrete reason it lost.</p>
  <span class="prec">Tool A · Tool B feature</span>
</div>
```

- A decision that replaces an earlier design uses `<b>Was:</b>` instead of Rejected.
- A decision not yet settled gets `<span class="proposed">proposed</span>` after its
  `h4` text, and is listed as open in the log.
- Leave out Rejected only when there was no real alternative.

## Stages (`#stages`)

- An intro: "In the order they land; each builds only on the ones before." Then a
  sentence on which stages deliver which goals.
- A **deps strip** (`.deps`): each stage as a `.dep` with an arrow between them. Use
  `.dep.par` (dashed) for one that can land alongside the others.
- A `.note` callout for any cross-cutting constraint, such as a date, a format version,
  or what share files need.
- **One `section.stage` per stage**, with `id="sN"`:
  - `.stage-head`: `<span class="num">Stage N</span><h3>Title</h3>`
  - `dl.stage-meta` with **Lands** (what the stage delivers), **UI** (what the user
    sees, or "none"), **Depends on** ("nothing", or the stages it needs), and **Issue**
    (linked).
  - `h4` subsections for the parts of the work (the file, the machine, the nodes,
    validation, on the canvas). Name real modules, classes and functions, the ones
    that exist and the ones to be added.
  - `.done-when`: a list of testable conditions. These are the stage's contract, and
    each one gets a test.
  - `<p class="muted"><b>Docs:</b> …</p>`: the docs this stage writes or updates.
  - After it lands, the As built box (`references/as-built.md`).
- **Later** (`#later`): `section.stage.later` cards, each with `later · #N` and a
  paragraph. These are follow-on work that needs a plan of its own.

## Out of scope (`#scope`)

A list in which each item has a bold name and a one-line reason. It covers what the
survey offered and the plan refuses, and what someone will ask for later.

## Decisions log (`#log`)

A `ul.log` of dated entries (`<time>YYYY-MM-DD</time><span>…</span>`), oldest first.
Entries record:

- the plan being drafted, and what it was drafted from (the issue, the code, the tools
  surveyed);
- each decision proposed, settled, reversed or restored, with the reason;
- the stage issues being filed;
- each stage landing: `Stage N landed (#issue, merge <sha>).`, then the deviations that
  matter in a sentence or two, then `All of it is in <a href="#sN-built">Stage N's As
  built</a>.`;
- the close-out: "The plan is complete.", followed by every issue filed for later
  stages and open items.

If a question is open, say so above the list ("One question is open: …").

## Optional sections

Add them when the work calls for them, in this order after Stages:

- **Deviations that affect later stages** (`#deviations`, `.dev` cards with a `.hits`
  line naming the stages affected). Use it when deviations pile up and later stages
  have to plan around them, as in Subgraph Calls.
- **Plan terms vs the UI** (`#wording`): a table, when the shipped names differ from
  the plan's.
- **What gets deleted** (`#deletions`): code and concepts the plan removes.
- **Parked**: ideas set aside on purpose, with the reason.
- **Design review** (`#review`): a review of the work before it lands, with a gate
  table, "What holds up", `.finding` cards (number, title, `.sev.block` "Before merge"
  or `.sev` "Should fix", the linked issue, the problem, `<p class="fix"><b>Fix:</b>
  …</p>`), nits, and items left for later without an issue yet. Official and Shared
  Content has one.

## Footer

- "Code this plan names:", a list of the files and symbols the plan refers to, grouped
  `Since Stage N:` as stages add them.
- "Sources for the lessons:", links grouped by tool.
- Optionally, "Based on `master` at `<sha>`", which says what the plan was written
  against.

## After each merge: what changes on the page

1. Read the live page with the Artifact tool and edit the HTML file the read saves. A
   publish to a page this conversation hasn't read is refused.
2. In the strip, give the stage `.done` and the "Landed" label, and remove `.here`.
3. Add or extend the eyebrow's status span ("Stages 3 to 5 landed 2026-10-08").
4. Add the As built box at the end of the stage's section.
5. For each earlier open item this stage resolved, append the resolution in bold
   ("**Done in Stage 4**; see its As built"). Never delete the item.
6. Add the log entry.
7. Add a footer line, `Since Stage N:`, listing the new files and symbols.
8. Republish to the same URL with no `icon`, so the page keeps the icon it has.

## Voice

The pages read the way the repo's docs do:

- **Plain, declarative and present tense.** "Only the run loop writes." Not "We will
  ensure that writes are restricted to…".
- **Concrete.** Name the class, the function, the file, the constant and its value.
  Give counts and timings: "8,527 tests", "17 ms to 6.7 ms", "23 edges".
- **The reason comes with the claim.** Every rule, rejection and deviation says why, in
  the same sentence or the next.
- **Short sentences, and no marketing.** No "seamless", "robust" or "powerful". No
  hedging where the fact is known. Where it isn't known, say so plainly ("not yet run
  in game").
- **Bold lead-ins** in lists (`<b>The machine.</b> …`), so a reader can skim.
- **American English** ("color", "behavior", "center").
- Game terms as the game says them, and the repo's terms as `CLAUDE.md` and the
  memories fix them (for example "World Gate").
