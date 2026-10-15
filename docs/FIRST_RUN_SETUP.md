# First-run setup

Epic #215: the first time BartleBot opens, it walks the user through everything a first
run needs, Quick or Custom, and the whole walk can be run again from the File menu. **The
plan is on one page, and is kept current while the stages land:**
https://claude.ai/artifact/DMYNo3NfYVLYPogsHACncx

It holds the goals, the setup flows it was checked against, the lessons taken from them,
every decision with the alternative it beat, and each stage's "As built" deviations. The
page is private on claude.ai: open it signed in as the maintainer, or, from a Claude Code
session, read it with the Artifact tool's `read` action.

The stages land in this order, each building only on the ones before:

| stage | issue | what | status |
|---|---|---|---|
| 1 | #226 | the setup frame: Quick or Custom, no Skip, saved as you go, File › Set up BartleBot… | landed |
| 2 | #227 | game folder: install detection with Steam, the page, a Settings row | landed |
| 3 | #228 | accounts: the page, on the existing account editor | landed |
| 4 | #229 | launch profile: one profile from a checklist | landed |
| 5 | #230 | quicksell: three answers, the keep list, the per-run explanation off | planned |
| 6 | #231 | realm and Safety Delay: one page | planned |
| 7 | #232 | card art and Custom: the extractor in the app, the Options page, the art page | planned |

`docs/FIRST_RUN.md` is the feature as built: the pages, what each writes, and when each
counts as answered. `bartlebot/gui/CLAUDE.md` has the rules for writing a page.

The rules that hold across every stage:

- **Every page writes a choice, and there is no Skip.** A page that only explains gets
  skipped; closing keeps what was answered and asks the rest next launch.
- **A page writes through the function its Settings row uses.** Setup and Settings never
  disagree about what is stored.
- **No page writes a bot file, a bot option's declaration or a user override.** Every
  page writes an app setting or a shared preference, the level a Settings row writes.
- **What both products need is written where both read it.** The game folder, accounts,
  launch profiles and the combat answer go in `preferences.json` and `accounts.json`,
  with their Qt-free code in `bartlebot/shared/`.
- **An answered page is never shown on a first run.** A user who answered the combat
  question in #209, or in BartleBuddy, is not asked it again.
