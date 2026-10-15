# First-run setup

The first time BartleBot opens, it asks what a first run needs, one page at a time. Every
page writes a real setting, the same one its Settings row writes, and the whole walk can
be run again from **File › Set up BartleBot…**. The plan behind it is epic #215
(`docs/FIRST_RUN_SETUP.md`).

The code is `bartlebot/gui/dialogs/first_run_dialog.py`; what a page stores lives in a
Qt-free module, so BartleBuddy can ask the same question and write the same keys.

## The walk

1. **Quick or Custom.** Two cards, Quick first, recommended and selected. The choice is
   not stored: it only decides which pages follow. Custom adds pages Quick applies at
   their defaults (none yet: the Options page arrives with #232).
2. **Every page after it that applies.** A page applies under the mode chosen and with
   what is set up so far.

The footer is **Back** and **Next**, and on the last page that applies, **Finish**. Under
the heading, **Step N of M**, where M counts the pages that apply now, Quick or Custom
included, so choosing Custom can change it.

- **Next records the page shown**, and moves on only once that reached disk. A failed
  write keeps the user on the page, says so under it, and logs a warning.
- **Next waits for a page that isn't ready.** A page's `ready()` says its answer can be
  recorded, and it emits `ready_changed` when that may have changed; Next is disabled
  until it is. The game folder page is the one that uses it.
- **Back records nothing.**
- **There is no Skip.** Escape and the close button reject and write nothing more. What
  was answered stays answered, and the next launch opens on the pages still unanswered.

## First run and rerun

`AppController._offer_first_run` builds the pages (`setup_pages`) and opens the dialog
when any page is unanswered. It runs from `_after_window_shown`, before a file named at
launch is imported, so the two modals never stack.

- **A first run** walks the pages unanswered when the dialog opened. A page answered on
  the way stays in the walk, so Back still reaches it.
- **A rerun**, from File › Set up BartleBot… (after Settings…), walks every page that
  applies, each on the current value.

After either, finished or closed, the Combat tab rereads its source, since the combat page
may have changed the Team default, and `MainWindow.catch_up_with_accounts` rebuilds the
Launch menu, the window minimum and the launch screen's cards, since the Accounts page
writes accounts without the window seeing it.

## The pages

| page | writes | answered when |
|---|---|---|
| Quick or Custom | nothing | never: it is always the first page |
| Where is Wizard101 installed? | `game.install_dir` in `preferences.json`, through `shared/game_install.set_install_dir`, only for a folder the user changed to | `find_install` finds an install, stored or detected |
| Which accounts should BartleBot log in to? | each account as it is added, edited or removed, to `accounts.json` (and its password to Credential Manager), through `accounts_tab`'s card actions; on Next, `setup.accounts_answered` in `preferences.json`, through `shared/account_setup.record_accounts_answered` | `accounts_answered`: `accounts.json` holds an account, from either product, or the key is set |
| How should your team take on bosses? | the Team default, through `shared/first_run.record_answer`, and `setup.combat_defaults_answered` in `preferences.json` | `combat_question_answered`: the key is set (by either product, or #209's Skip), or the Team default is a duel neither answer names |

### The combat question

Two cards, the recommended answer (one hitter, the others support) first.
`docs/COMBAT_CONFIG.md` ("First run: one question about the team") has what each answer
stores. The page opens on the answer the stored Team default already is. On a rerun that
default can be a duel neither answer names, chosen in Settings › Combat; then no card is
selected, and Next with none selected keeps that duel and records only the key.

### The game folder

wizwalker's `get_wiz_install()` looks only where the KingsIsle launcher installs, so a
Steam install was never found. `bartlebot/shared/game_install.py` looks further, and
`find_install` takes the first of these that holds `Bin/WizardGraphicalClient.exe`:

1. **The folder chosen**, `game.install_dir` in `preferences.json`, written by this page
   or Settings › General › Game folder. BartleBuddy reads the same key.
2. **The launcher's folder**, `C:/ProgramData/KingsIsle Entertainment/Wizard101`
   (wizwalker's `DEFAULT_INSTALL`).
3. **The launcher's install record**, `InstallLocation` under its uninstall key in
   `HKEY_CURRENT_USER`.
4. **Each Steam library.** Steam's root comes from the registry (`SteamPath`, then the
   machine's `InstallPath`); its `steamapps/libraryfolders.vdf` (or `config/` on older
   clients) lists the libraries, in either of that file's two layouts, read with the
   `vdf` package. A library counts when it holds Wizard101's manifest,
   `steamapps/appmanifest_799960.acf`, and the game is in the `installdir` it names under
   `steamapps/common`.

A folder without the client is passed over for the next, so a stale choice or a library
on a drive that is gone falls back rather than failing. At startup `AppController` calls
`clients/install_location.apply_install`, which hands what is found to wizwalker's
`override_wiz_install_location` before anything asks `get_wiz_install()`; nothing found
clears the override, and wizwalker's own checks apply. The page and the Settings row call
it again whenever they store a folder.

The page shows a path field with **Browse…**, and a line under it: the place the folder
was found in, that the game is installed there, or that it isn't, naming the missing
client. Next is enabled only for a folder holding it. On a rerun the page opens on the
folder in use, and Next on a detected folder left as it was writes nothing, so a detected
install that moves is still found.

The suite looks in no real place: `tests/conftest.py` puts `NoPlaces` in, so a game on the
developer's machine can't decide which pages setup shows. A test that wants an install
sets `game_install._places` to one of its own.

### Accounts

The page draws the account cards Settings › Game draws, in the same `CardColumn`, with
**Add account** at its foot. Add, Edit (a click on the card, or its pencil), Remove (its
trash) and dragging to reorder are `accounts_tab`'s card actions, the ones Settings and the
launch screen call, so the editor, the duplicate and empty-field refusals, the delete
cascade and the writes are theirs. Each writes `accounts.json` as it happens, and a
password goes to Windows Credential Manager through `CredentialStore`, never to disk. An
account added here is on Settings › Game and in the Launch menu at once.

So Next has nothing of the accounts left to store. It records `setup.accounts_answered`,
which is what keeps a user who chose no account from being asked again: the page may be
left empty, since clients started by hand can still be hooked, and with none it says so
and names **Launch › Manage Accounts…** for later. With an account in `accounts.json`,
added by either product, the page is answered without the key.

Every change on the page emits `walk_changed`, since how many accounts there are decides
whether the Launch profile page follows.

