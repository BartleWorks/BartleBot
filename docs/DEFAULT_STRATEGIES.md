# Default strategies

Epic #218: the shipped default strategies fight the way Wizard101 players do. One hitter
and the rest support on a boss, everyone hits in round 1 of a team mob fight, a solo
wizard shields, buffs and then hits, and a support never spends a buff meant for the
hitter. **The plan is on one page:**
https://claude.ai/artifact/JNffqBsHsanEtdhhNXP3RK

**All six stages landed by 2026-10-15. The page is complete and no longer maintained, but
it is still the record of the design.** It holds the goals, the patterns checked against
other tools, the lessons taken from them, every decision with the alternative it beat, and
each stage's "As built" deviations. Read it when a design question comes up, but don't
update it: a later fix is not added there. The page is private on claude.ai: open it
signed in as the maintainer, or, from a Claude Code session, read it with the Artifact
tool's `read` action.

The stages landed in this order, each building only on the ones before:

| stage | issue | what |
|---|---|---|
| 1 | #219 | stacking check: the `stacks` card-template keyword in wizsprinter, and the pin that brings it |
| 2 | #220 | fight harness: a test helper that walks a DSL through one round of a described fight |
| 3 | #221 | supports: Default Support and the two cheat-boss supports rewritten to the support ladder |
| 4 | #222 | hitters: Default rewritten to the hitter ladder, and Default Team Hitter for the team duels |
| 5 | #223 | two duels go: Default Team Boss Duel and Default Turn 1 AoE Mob Duel leave the Official set |
| 6 | #224 | guardian and Attenuate: Default Support only buffs, Default Guardian Support shields and heals P1 first, every support casts a negative aura on the boss through wizsprinter's `harmful_aura` and `stronger`, and every hitter shields first |

`docs/COMBAT_CONFIG.md` is the feature as built: "The shipped defaults", "What a
support does", "What the guardian does" and "What the hitter does" say what each default
does. wizsprinter's `CONDITIONS.md` documents `stacks`, `harmful_aura` and `stronger`, and
`docs/WIZSPRINTER_REFERENCE.md` has a summary.

After it: playing the defaults in game (#212), which every stage's unverified items point
at; smart combat, hitting when a hit kills (#30, 2.0.0); and a Willcast that reads its card
(#225, 2.0.0). Each of the last two needs a plan of its own.

The rules that hold across every stage:

- **The duels keep their ids**, and BartleBuddy reads them from
  `bartlebot/shared/combat_defaults.py`. Everything that changes is in the player
  strategies they link.
- **`LAST_RESORT_DSL` is Default's text, word for word.**
- **Every shipped strategy that buffs enchants ahead first**, and the cheat-boss supports
  never fuse an enchant into the card their boss's cheat watches.
- **The fight kind is read by one rule**: `?(any(enemies).is_boss == 1)` for a boss fight,
  `?(all(enemies).is_boss == 0)` for a mob fight; never `boss.health` or `boss.hp`. P1's
  death is `ally(last).health == 0`, never `is_dead`.
- **`SHARED_SET_VERSION` rises with every change to a shared Official item.**
- **Nothing writes to a bot or a Bot Combat Strategy file at load or at run time.**
- **`bartlebot/shared/` imports nothing from the rest of `bartlebot`.**
