# Subgraph calls

The successor epic to #37. **The plan lives on one page:**
https://claude.ai/artifact/66pY2mPVrPYosopv1yaNNe

Read it before any subgraph work. It is the only copy — nothing here duplicates it — and
it carries the stage status, so a stage that lands is marked done there rather than here.
It is a private claude.ai page: open it signed in as the maintainer, or, from a Claude Code
session, read it with the Artifact tool's `read` action.

What it covers, in the order the work lands:

| stage | what |
|---|---|
| 0–2 | landed: the plan, one copy of a definition (`BARTLEBOT_DEV`), the node audit |
| 3 | param types: a `zone` type, widgets chosen by type |
| 4 | the signature: Entry/Exit port nodes, the port map, typed inputs, input and node panels, the held rule |
| 5 | the definition document: editing a definition as its own document; deletes edit-in-place |
| 6 | the call node: an instance drawn as one node, with its input form and Record |
| 7 | stepping in and out: one crumb bar, double-click in, Esc out |
| 8 | Player Sync, and the templates rebuilt on it — must precede 9 |
| 9 | the library rewrite (#59) |
| 10 | template provenance — alongside any stage, required before the first post-release template change |

It supersedes several decisions in `docs/SUBGRAPHS.md`: the composite-node rejection, the
three-level param model (an instance now sets inputs and nothing else), authoring in place
(a definition is now its own document), instance locking, and instance-owned member layout.
What that file says about derived ids, follow-latest with snapshot fallback, the two storage
roots, nesting and cycles still holds.

Per-bot detail for Stages 2, 8 and 9 is in `docs/LIBRARY_MIGRATION.md`.
