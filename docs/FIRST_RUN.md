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

A first run opens only when `needs_asking` finds a page unanswered that applies under
Quick or Custom. The Launch profile page, unanswered with fewer than two accounts, opens
nothing on its own; it is in the walk whenever another page opens setup, so accounts
added on the way bring it in.

After either, finished or closed, the Combat tab rereads its source, since the combat page
may have changed the Team default, and `MainWindow.catch_up_with_accounts` rebuilds the
Launch menu, the window minimum and the launch screen's account and profile cards, since
the Accounts and Launch profile pages write accounts and profiles without the window
seeing it. An account or profile made in setup is on the launch screen and under Launch
as soon as setup closes.

Then the window is fitted to the screen, before the launch screen is judged against it:
`MainWindow.fit_layout_to_launches`, through `screen_fit.fitted_layout`. The game area keeps
the tallest and widest grid the user can launch, the bot canvas takes the height left, and
the sidebars the width left, evened up: the narrower one grows until the two match, and
what remains is shared equally. A fixed 220 px canvas left most of a 1080p screen empty
above one or two accounts, and a taller fixed height would have stopped four fitting at
all; a 4K screen left both empty around the grid.

The global resolution is fitted with it, since 800×600 is a quarter of a 4K screen for a
single account. Launch profiles with a resolution of their own decide the layout first,
since a user with one runs it more than a single account; the global resolution is then
the largest that fits in the game area they leave, for one client and for every profile
following it. With no such profile it is the largest one client fits that still leaves
the canvas 23% of the screen's usable height (`game_sizing.SINGLE_CLIENT_CANVAS_SHARE`),
and decides the layout itself. Without that bar the largest wins however little it leaves:
1680×1050 left a 1440p screen a 231 px canvas, where 1600×900 leaves 381. It is 23 rather
than a round 25 because 4K at 150% keeps 2560×1440 with 329 px of its 1400 (23.5%); at 25%
it fell to 1920×1200, a much smaller game for a canvas it did not need. A taller taskbar
can still tip it there. The other way round, the largest single client would be the tallest
grid on every screen and squeeze the canvas of every user with a profile.

| 1080p less a taskbar | profile | one client | canvas | sidebars |
|---|---|---|---|---|
| one account | | 960×540 | 381 | 466 / 466 |
| two accounts | 560×420 | 746×420 | 501 | 404 / 356 |
| four accounts | 534×300 | 960×540 | 270 | 406 / 406 |

(Sidebars at the reference floors; the measured floors move them by a few pixels.)

Each is stored where its Settings row writes it, `game_embed.resolution` and the three
pane sizes in `general`, and marked as setup's in a `fitted_` key beside it. A later setup
changes one only while it is still the shipped value or setup's own, so a value the user
chose in Settings stays theirs, and the panes still setup's take what it leaves. A launch
that cannot fit anyway is left out, and the canvas is never below 220.

## The pages

| page | writes | answered when |
|---|---|---|
| Quick or Custom | nothing | never: it is always the first page |
| Where is Wizard101 installed? | `game.install_dir` in `preferences.json`, through `shared/game_install.set_install_dir`, only for a folder the user changed to | `find_install` finds an install, stored or detected |
| Which accounts should BartleBot log in to? | each account as it is added, edited or removed, to `accounts.json` (and its password to Credential Manager), through `accounts_tab`'s card actions; on Next, `setup.accounts_answered` in `preferences.json`, through `shared/account_setup.record_accounts_answered` | `accounts_answered`: `accounts.json` holds an account, from either product, or the key is set |
| Which accounts launch together? (two or more accounts) | one launch profile of the accounts checked, to `accounts.json`, through `shared/launch_profiles.add_profile`, the function Settings › Game's Add uses; and `setup.launch_profile_answered` in `preferences.json`, through `record_launch_profile_answered` | `launch_profile_answered`: `accounts.json` holds a launch profile, from either product, or the key is set |
| How should your team take on bosses? | the Team default, through `shared/first_run.record_answer`, and `setup.combat_defaults_answered` in `preferences.json` | `combat_question_answered`: the key is set (by either product, or #209's Skip), or the Team default is a duel neither answer names |
| What may bots sell? | the four quicksell switches and `quicksell_keep_list`, through `options/quicksell_setup.write_sell_flags` and `write_keep_list`, the functions Settings › Bot uses; `show_quicksell_explanation = False`; and `setup.quicksell_answered`, all in BartleBot's `settings.json` (`general`), through `record_quicksell` | `quicksell_answered`: the key is set, or any quicksell setting is off its default (a switch, the keep list, or the explanation turned off) |

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


### Launch profile

The page applies once there are two or more accounts, so it follows the Accounts page
only when that page leaves two. It lists every account as a check box, all checked, in
the Accounts page's order, which is player order: the first checked is Player 1. The list
is read each time the page is entered (`FirstRunPage.enter`), so an account added or
removed on the page before is there, and one the user unchecked stays unchecked.

Next makes one profile of the checked accounts, named **Everyone**, or the first of
"Everyone 2", "Everyone 3"... no profile has (`shared/launch_profiles.free_name`). It is
built and added by `build_profile` and `add_profile`, which Settings › Game's Add uses
too. A line under the list says what Next will do.

The profile gets a resolution of its own, from a combo under the list that starts on the
largest that fits the accounts checked (`screen_fit.setup_resolution`), with every size
too big for them grayed. Left on the global 800×600, two accounts on a 1080p screen would
make a profile the launch screen refuses as soon as setup closes; 560×420 is what fits
them. The fit is judged with the sidebars at their floors and the canvas at 220 px,
whatever they are set to (`setup_area`), so panes setup already fitted to one account
leave room for four. A smaller size the
user picks is kept while it still fits the accounts checked, and falls back to the largest
once it doesn't. With none that fits, the line says so and the profile follows the global
resolution.

- **Fewer than two checked** makes no profile, and still records the page.
- **The same accounts in the same order already launching together** make no second
  profile (`matching_profile`), so a rerun, or Next tried again after a failed write,
  finds the one already made.
- **A failed write** keeps the user on the page, as any page's does.

More profiles, and every edit after, are in Settings › Game, under Launch Profiles.

### Quicksell

The page shows Settings › Bot's quicksell rows, built by the same
`gui/settings/quicksell_group.QuicksellSettings`: **Enable Quicksell**, heading **Sell
Pets**, **Sell Socket Wrenches**, **Sell Skeleton Keys** and **Always keep**, on the stored
values, which on a first run are the defaults (sell drops and pets, keep wrenches and
keys). Turning Enable Quicksell off grays the rest, as on the tab. Each row has its revert
arrow and its description, and the group never folds on the page (`foldable=False`), so
every row is shown whatever the user folded in Settings.

Above the rows, a line says what the explanation before each run used to: bots sell what
dropped except what these settings keep, an item locked in the game is never sold, with
where Backpack Item Lock is turned on, and that these are the Settings › Bot rows a bot's
Options can change for that bot. The tab's **Show Quicksell explanation before each run**
row is not on the page, since Next turns it off.

Next writes through `options/quicksell_setup.record_quicksell`: the switches, the keep
list, `show_quicksell_explanation = False` and `setup.quicksell_answered`, then saves
`settings.json`. A failed save keeps the user on the page. A sale a bot's own file
declares beyond these settings is still flagged once per bot by the safety check
(`docs/BOT_OPTIONS.md`, "Beside the levels").

A user with any quicksell setting already off its default is not asked on a first run:
they changed it in Settings › Bot, or turned the explanation off under it, so they have
decided. The page is BartleBot's alone, so its key is in `settings.json`, not in the
shared `preferences.json`.
