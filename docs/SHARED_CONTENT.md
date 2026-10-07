# Official and shared content

Epic #173: user templates (#164), Official content (#149), and sharing bots, strategies,
decks and subgraphs as files. **The plan is on one page:**
https://claude.ai/artifact/4A9XUEeoTXQAfohUHP3F1n

**Ready to start on 2026-10-07, and maintained while the work lands.** The page holds the
goals, the model, the lessons taken from other tools, every decision with the alternative
it beat, and each stage's done-when. The page is private on claude.ai: open it signed in
as the maintainer, or, from a Claude Code session, read it with the Artifact tool's
`read` action. Each stage's issue restates its part, so the work can start from the issue.

The stages land in this order, each building only on the ones before:

| stage | issue | what |
|---|---|---|
| 1 | #168 | storage roots: user bots and templates beside the shipped ones; duel strategies, player strategies and decks move to the vendor root |
| 2 | #149 | Official: the label, read-only Official bots, and Make my own copy |
| 3 | #169 | content hashes, histories, "Official updated", and the subgraph signature warning |
| 4 | #170 | share files: one format, an extension per kind, and Export |
| 5 | #171 | share files: Import, matched by id, defaulting to Import as copy |
| 6 | #164 | the Templates dialog, and user templates as forks |
| 7 | #172 | subgraph categories |

`docs/SHARING.md` is the share file's format, Export and Import, as built in Stages 4
and 5.

All seven are in 1.0.0. After it: templates in share files (#165), the release check
that enforces "nothing released is removed" (#166), and extracting `bartlebot/shared/` to
a shared library repo once BartleBuddy development starts (#167). The installer's part,
including the file type registrations, is in #27.

The rules that hold across every stage:

- **Official comes from where a file is, never from what it says.** It means tested and
  proven in game, and shipping is the gate.
- **Linked things are overridden; copied things are forked.** Subgraphs and strategies
  keep their same-id override; an Official template or bot is customized by forking it.
- **Official content arrives only with app updates, and nothing released is ever removed.**
- **A share file carries everything Custom and names everything Official.** Combat picks
  and option values live in settings and never travel.
- **Duel strategies, player strategies and decks are shared with BartleBuddy.** Their
  code is staged in `bartlebot/shared/`, which imports nothing from `bartlebot` outside
  itself.
- **Shared files are data only, permanently.**
