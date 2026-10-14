# Default strategies

Epic #218: the shipped default strategies fight the way Wizard101 players do. One hitter
and the rest support on a boss, everyone hits in round 1 of a team mob fight, a solo
wizard buffs and then hits, and a support never spends a buff meant for the hitter.
**The plan is on one page, and is kept current while the stages land:**
https://claude.ai/artifact/JNffqBsHsanEtdhhNXP3RK

It holds the goals, the patterns checked against other tools, the lessons taken from
them, every decision with the alternative it beat, and each stage's "As built"
deviations. Read it when a design question comes up. The page is private on claude.ai:
open it signed in as the maintainer, or, from a Claude Code session, read it with the
Artifact tool's `read` action.

The stages land in this order, each building only on the ones before:

| stage | issue | what | status |
|---|---|---|---|
| 1 | #219 | stacking check: the `stacks` card-template keyword in wizsprinter, and the pin that brings it | landed |
| 2 | #220 | fight harness: a test helper that walks a DSL through one round of a described fight | landed |
| 3 | #221 | supports: Default Support and the two cheat-boss supports rewritten to the support ladder | landed |
| 4 | #222 | hitters: Default rewritten to the hitter ladder, and Default Team Hitter for the team duels | landed |
| 5 | #223 | two duels go: Default Team Boss Duel and Default Turn 1 AoE Mob Duel leave the Official set | landed |

`docs/COMBAT_CONFIG.md` is the feature as built: "The shipped defaults", "What a
support does" (Stage 3) and "What the hitter does" (Stage 4) say what each default does.
wizsprinter's `CONDITIONS.md` documents `stacks`, and `docs/WIZSPRINTER_REFERENCE.md`
has a summary.

The rules that hold across every stage:

- **The duels keep their ids**, and BartleBuddy reads them from
  `bartlebot/shared/combat_defaults.py`. Everything that changes is in the player
  strategies they link.
- **`LAST_RESORT_DSL` is Default's text, word for word.**
- **Every shipped strategy that buffs enchants ahead first**, and the cheat-boss supports
  never fuse an enchant into the card their boss's cheat watches.
- **The fight kind is read by one rule**: `?(any(enemies).is_boss == 1)` for a boss fight,
  `?(all(enemies).is_boss == 0)` for a mob fight; never `boss.health` or `boss.hp`.
- **`SHARED_SET_VERSION` rises with every change to a shared Official item.**
- **Nothing writes to a bot or a Bot Combat Strategy file at load or at run time.**
- **`bartlebot/shared/` imports nothing from the rest of `bartlebot`.**
