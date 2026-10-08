# Runtime flow control

Epic #16: bot variables, named values the bot sets as it runs and reads back to choose
its next node. **The plan is on one page:**
https://claude.ai/artifact/VnoJzdBGUjJw4a8gZG9U5M

**All five stages landed by 2026-10-08. The page is complete and no longer maintained, but
it is still the record of the design.** It holds the goals, the flow-control features
checked against other tools, the lessons taken from them, every decision with the
alternative it beat, and each stage's "As built" deviations. Read it when a design
question comes up, but don't update it: a later fix is not added there. The page is
private on claude.ai: open it signed in as the maintainer, or, from a Claude Code session,
read it with the Artifact tool's `read` action.

The stages landed in this order, each building only on the ones before:

| stage | issue | what |
|---|---|---|
| 1 | #186 | the engine: declarations, variable conditions on edges, `set_variables` and `branch`, validation, the template diff |
| 2 | #187 | authoring: the Variables section, condition and assignment rows, condition labels on the canvas, the badge |
| 3 | #188 | watching a run: live values in Run mode, the condition in the transition log, final values in the summary |
| 4 | #189 | edge assignments: rows applied when an edge is taken |
| 5 | #190 | Heretical Academy's sword puzzle rebuilt on variables, run in game |

`docs/VARIABLES.md` is the feature as built: the model, the rules, authoring, reading a
run, and when a subgraph is the better tool.

After it, each needing a plan of its own: subgraph-local variables with by-reference
ports (#194), and variables that feed node params (#195). The stages' leftovers are
#196 to #200.

The rules that hold across every stage:

- **Options are the user's; variables are the bot's.** An option is read-only during a
  run, and a variable never becomes a setting or a param.
- **Only the run loop writes, and a write never waits.** Watchers and a node's game code
  are refused.
- **Fresh when the bot starts; after that, only the bot resets a value.** Nothing a
  variable holds is ever written to disk.
- **Rows, never expressions.** Share files stay data only.
- **No zone ever comes from a variable**, so the restricted-zone check stays complete.
