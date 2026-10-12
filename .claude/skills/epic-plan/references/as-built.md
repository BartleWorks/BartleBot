# The As built box

The box goes at the end of the stage's `section.stage`, after the Docs line. It is
written before the merge (with the SHA filled in once the merge commit exists) and
never removed. The planned text above it stays as written: the box is how a reader
tells what was planned from what was built.

## Shape

```html
<div class="built" id="sN-built">
  <h4>As built · YYYY-MM-DD</h4>
  <p>Landed on <code>master</code> as merge <code>abc1234</code>, from five commits on
  <code>branch-name</code>. Every item in Done when has a test, and the full suite (8,439)
  and ruff pass. Not yet pushed, so <code>Closes #N</code> fires on the next push.</p>

  <h5>What was done</h5>
  <ul>
    <li><b>The part.</b> What exists now, by name: classes, functions, constants and
    their values, files. Enough that a reader can find it without the diff.</li>
  </ul>

  <h5>Deviations, and why</h5>
  <ul>
    <li><b>What differs from the card, as a fact.</b> Why: the case the plan got wrong,
    what was tried, what broke.</li>
    <li><b>Not in the plan: what was added.</b> Why it had to land here.</li>
  </ul>

  <h5>Open items</h5>
  <ul>
    <li><b>For #N:</b> what the later stage has to do or allow for, and the hook this
    stage left for it.</li>
    <li>Not run in the interactive app or in game. Rendered offscreen in both themes
    and looked at.</li>
    <li><code>test_name</code> failed once under <code>-n auto</code> and passed
    alone. Not chased.</li>
  </ul>
</div>
```

## The opening paragraph

It always says:

- **where it landed**: the merge SHA on `master`, and how many commits came from which
  branch;
- **Done when**: every item has a test, or which ones don't and why ("the first and
  third items are met; the second is half met: …");
- **the gate**: the full suite's count and ruff, with any flake named;
- **the push state**: "Not yet pushed, so `Closes #N` fires on the next push", or "which
  closed #N", and for the last stage, the epic too.

## What was done

Use one bullet per part of the stage, each with a bold lead-in that matches the card's
`h4`s where it can (The file. The machine. The nodes. Validation. On the canvas.). Name
real symbols. Include the guards: the tests that pin the behavior, and any library-wide
test that now covers every shipped bot. When it matters, a separate
`<h5>Guards added</h5>` lists those tests on their own, as Official and Shared Content
did.

## Deviations, and why

These count most. Each one says what differs and why, and is concrete enough that a
reader who disagrees can find what to change:

- "**The label hangs where the edge turns toward its destination, not next to its
  source port.** Every edge from one port shares its first stretch, so a switch's labels
  would sit on top of each other at the port."
- "**Not in the plan: a labeled edge used to fail to build the machine.** … No shipped
  edge has a label, so it never fired. The machine now takes `label` and `guard` off
  first, and a test runs a labeled edge."

Include as deviations: checks beyond the card's list, a rule narrowed because it
flagged shipped bots that had nothing wrong with them, a bug found and fixed on the
way, and a planned behavior that turned out to be invisible or wrong in use. When a
deviation changes what a later stage must do, add a matching open item "For #N".

"Built as planned" is a fine opening when it's true, as long as the box still lists
the small decisions made while building.

## Open items

Every item names where it goes:

- **For #N:** work a later stage in this plan picks up.
- **Filed as #N**: a new issue for something outside the plan.
- **Unverified surfaces**: the live app, the game, the light or dark theme.
- **Flakes**: the test, how it failed, and whether it passed alone.

As later stages land, append the resolution to the item in bold, and never delete or
reword it:

| resolution | when |
|---|---|
| **Done in Stage N**; see its As built | a later stage did it |
| **Used as planned in Stage N.** | a hook this stage left was used as intended |
| **Closed by Stage N.** | a later stage made it moot (for example it ran the whole in game) |
| **Filed as #N.** | it became an issue |
| **Accepted;** no issue. | a known limit, judged not worth fixing, with the reason in the item |
| **Left by decision;** no issue. | deliberately not done, with the reason in the item |
| **Not seen again** in Stage N's runs. | a one-off that didn't recur |

By close-out every item carries one of these.

## Splitting a stage

When a stage is split (10.1, 10.2, …), each part gets its own box headed
`As built · 10.N, <code>sha</code>`. When a part sits on the branch before the merge,
its head says so: "on `stage-10-templates`. Not merged; not yet run in the live app."
Fix-up rounds (review fixes, live test fixes) get boxes of their own in the same way.
