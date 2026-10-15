# Combat defaults

Epic #201: every fight runs the most tailored duel strategy available for the team on the
field, and a run stops to ask only when something the user chose is broken. **The plan is
on one page:**
https://claude.ai/artifact/X6DUPQ7YrZwkjosGJnSbo4

**All seven stages landed by 2026-10-13, and the close-out was finished on 2026-10-15. The
page is complete and no longer maintained, but it is still the record of the design.** It
holds the goals, the interruption patterns checked against other tools, the lessons taken
from them, every decision with the alternative it beat, each stage's "As built"
deviations, and the review after the last stage. Read it when a design question comes up,
but don't update it: a later fix is not added there. The page is private on claude.ai:
open it signed in as the maintainer, or, from a Claude Code session, read it with the
Artifact tool's `read` action.

The stages landed in this order, each building only on the ones before:

| stage | issue | what |
|---|---|---|
| 1 | #204 | one vocabulary: Bot Combat Strategy everywhere, "profile" left to launch profiles |
| 2 | #205 | shared preferences: `preferences.json`, the re-read-and-replace write, `AccountStore` on it |
| 3 | #206 | default duels: Default Support's fallback, a Default player strategy, three shipped duels |
| 4 | #207 | levels and tiers: the four-level resolution, faults and notices, the dialog for faults only |
| 5 | #208 | showing the source: the Combat tab banner, Settings › Combat |
| 6 | #209 | first run: one question about the team, answered once for both products |
| 7 | #210 | after a lost fight: a mark where the fight is drawn when it was fled on a default |

`docs/COMBAT_CONFIG.md` is the feature as built; its "Naming" section is the part of
Stage 1 that governs code, "The shipped defaults" the part of Stage 3, "Resolution at
run time" and "Before a run: faults and notices" the part of Stage 4, "Showing the
source, and choosing the defaults" the part of Stage 5, "First run: one question about
the team" the part of Stage 6, and "After a lost fight" the part of Stage 7.

The close-out (#211) played the shipped defaults in game (#212, after epic #218 reworked
them), checked DSL attribute names before a run (#214), and checked the Combat tab's
marks and banners in the live app (#216). After it, each needing work of its own:
Official Bot Combat Strategies (#213), and more first-run pages, an epic with its own
plan (#215, `docs/FIRST_RUN_SETUP.md` once its Stage 1 lands).

The rules that hold across every stage:

- **Nothing writes to a bot or a Bot Combat Strategy file at load or at run time.** Every
  level is resolved when the fight starts, and every bot file round-trips byte-identical.
- **A fallback is shown, never asked about.** Only a broken choice the user made stops a
  run, and going back is the default.
- **The checks that tell rather than ask stay first and blocking**: restricted zones,
  Player Realms, variables, and players already in a fight.
- **Share files carry no preference and no pick**, and keep their formats.
- **`bartlebot/shared/` imports nothing from the rest of `bartlebot`.**
- **The dialog's Save/Cancel boundary is untouched**, and the Assemble tier shows no DSL.
- **"Profile" means a launch profile, and only that.**
