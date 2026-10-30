# Settings

The settings store and the settings dialog. What a *bot option* is, and how it outranks an app setting, is in `docs/BOT_OPTIONS.md`.

Use `SettingsManager` singleton. Persisted to `%APPDATA%/Bartleby/BartleBot/settings.json`.
Credentials stored via `keyring` (Windows Credential Manager), never on disk.

**Accounts and launch profiles are not in that file.** They are about the *account*, not
about this app, so they live one level up in the vendor-level store —
`%APPDATA%/Bartleby/accounts.json`, core's `bartleby/shared/account_store.py` — where a second
product by this publisher can read them. `SettingsManager.accounts` and `.launch_profiles`
are a facade over it and are still what you call; `SettingsManager.account_store` is the
seam, and the only reason to reach it is a change to *both* lists that must be one write.
The vendor prefix, on disk and in Credential Manager, is `shared/vendor.py` and is defined
exactly once.

**Where a value lives, of the three files:**

| file | holds |
|---|---|
| `%APPDATA%/Bartleby/preferences.json` | a preference about the *user* that every product reads, such as the combat defaults. Flat, dotted keys (`combat.default_duel.solo`), `"version": 1`. core's `bartleby/shared/preferences.py`, `PreferenceStore` |
| `%APPDATA%/Bartleby/accounts.json` | accounts and launch profiles |
| `%APPDATA%/Bartleby/BartleBot/settings.json` | what only this app has: layout, sidebars, Safety Delay, bot options |

**The write rule for the two shared files:** two products can be open at once, so a
shared file is never written from memory. Every write is
core's `bartleby/shared/json_update.update_json(path, mutate)`: read the file as it is, change
only the key you own, check nobody wrote in between (retrying up to `UPDATE_RETRIES`),
and rename a temporary over it. Keys you do not know are kept; the last writer of a key
wins, which is right for a preference and why the strategy library, with authored content
to lose, refuses and asks instead. A new shared record goes through `update_json` too,
and `PreferenceStore` has no cache, so a read sees the other product's last write.
`AccountStore` does cache, and its callers write whole lists from that copy, so within a
key it merges per entry (an account by `username`, a profile by `name`) against what it
last read: only the entries it changed are applied, and the other product's adds, edits
and removes stand. A list is one value only for a preference.

One rule about the settings dialog itself, and where the rest went:

- **Every key in `_DEFAULTS[section]` must also be a field on that section's
  pydantic model.** Pydantic *ignores* an extra key rather than rejecting it, so a
  key in one and not the other is not an error — it is simply never type-checked,
  and a hand-edited `settings.json` can then put a string where a bool belongs and
  have it reach the widget. `streamer_mode` and `bot_library_path` were both in
  that state. `test_every_default_is_declared_on_its_model` is the guard.
- **The rows, the dialog, the card actions and the Game folder row are core's**
  (`bartleby/qt/settings/`, Stage 11 of #167), and so are their rules: core's
  `bartleby/qt/CLAUDE.md`, *Settings rows*, which loads with this file. BartleBot's tabs
  are built from them; its folding groups are declared in this package's `__init__.py`.

## The Game art row

Under Game folder (`game_art_row.py`, #232), and an **action, not a value**: its control is
**Extract**, its binding reads and writes nothing, so it is never changed and has nothing
to revert. Its line says when the art was extracted and how many pictures, from the index
(`card_art.manifest()`), or that there is none. Extract opens `ArtExtractionDialog`, the
same `ArtExtraction` setup's Game art page holds: a progress bar and the step, Cancel while
it runs, Close once it ended, and the row's line rereads the index when the dialog closes.
A run that wrote art announces `art_changed` (core's `bartleby/qt/CLAUDE.md`, "Art extracted while
the app is open"), and Settings itself is open behind the dialog, so its own art surfaces
follow it: the Combat Defaults tab's strategy cards and the quicksell keep list.
It reads the game folder stored, not one the Game folder row shows but OK has not written.
Its description is the text setup's Extract game art choice carries (`EXTRACT_DETAILS`), so
the cost, about 35 MB in AppData, is said once.

## The Updates row

General's *Check for updates at startup* (`general_tab.py`), on by default: whether an
installed build looks for a newer release once, after its window is up
(`docs/INSTALLER.md`, *The update check*). It is `updates.check_at_startup` in
`settings.json`, its own section beside `updates.last_offered`, the newest version the
update popup has opened for, which no row shows. A `settings.json` key rather than a
shared preference, since each product updates itself. It waits for OK like every other
row, and is read only at startup, so it changes nothing until the next one.

- **Off stops only the startup check.** Help › Check for Updates… looks whatever it says,
  and the row's description says so. It is the user's way to look on their own terms.
- It shows in a checkout too, where nothing ever checks: one form for both, and a
  developer reading the row sees what a user does.

## The Bot tab

Every row on it is the **app setting** level of the four-level resolution below,
so the disclaimer that a bot or an Option Values value outranks it is stated once at the
top of the tab rather than repeated into eight tooltips. That is why it is a tab
and not a section of General: it is one idea, and General's other five sections
are not levels of anything.

Two Quicksell rows are the exception, and their hints say so instead of the note at
the top: *Always keep*, the user's own keep list, which every bot applies on top of its
own rather than as a level beneath it, and *Show Quicksell explanation before each run*,
which the safety check's own check box also turns off. `docs/BOT_OPTIONS.md` has why
neither can be a level.

That is also the test for whether a row belongs here at all: **a setting the app
applies to a client while it is starting is a Game tab row, not a Bot tab one.**
Character selection and quick friend teleport both sat here and both moved —
neither is a level of any option's resolution, and both happen to every launched
client whether or not a bot is ever run.

`Safety Delay`'s description lives in `bot_options.SAFETY_DELAY_DESCRIPTION`,
because the app setting and the *option* are the same value and one value
described twice is how the two texts come to disagree —
`test_the_safety_delay_setting_and_option_read_the_same` checks the tab's hint
and every NodeSpec that declares the option against it. Its row and Favorite Realm's are
built by `realm_delay_rows`, which setup's Realm and Safety Delay page shows too, as
`quicksell_group` is for the quicksell rows; so the delay's guidance by region is in that
one text, not on the page.

Anything naming a settings row in user-facing text has to name its tab too: the
`relog` node's help, the "waiting at character select" recovery message and
`teleport_to_friend`'s error all said *Settings → General* and all had to move.

## The Combat tab

The Solo and Team **Default Duel Strategies** (epic #201, `docs/COMBAT_CONFIG.md`): what a
fight with nothing tailored to it runs, with one client connected or with two to four.
`combat_defaults_tab.py`. They are levels 3 and 4 of a fight's resolution, the way the
Bot tab's rows are the app-setting level of an option, but they live in the vendor-wide
`preferences.json`, not `settings.json`, because BartleBuddy fights with the same two.

- **Two cards, not rows.** Each is a `DefaultDuelCard` in a `SettingCard`: Solo or Team,
  one line saying when it answers, the duel in use drawn as the combat dialog's
  `StrategyCard` (with its source pill), and a note under it. A duel's summary is what
  the user is choosing between, and a 210px control slot cannot hold one.
  `SettingsForm.add_card` places a card that is not built from rows.
- **Change…** opens `CombatStrategyDialog` in Pick mode on the library alone, with the
  Team size filter preset to one or two (`team_size=`) as a chip the user can remove.
  **Use recommended** goes back to the shipped duel.
- **The apply rule holds:** a choice is pending until OK, and Cancel drops it. `apply`
  writes only a card whose choice changed since the tab opened, so a key the other
  product wrote meanwhile stands, through `combat_defaults.set_default_choice`. That
  stores the recommended duel as **no key** (picking the shipped duel by name is the
  same), so a later fix to it reaches everyone who kept it. A write that fails is a
  WARNING, and the choice stays for the next OK.
- **Record combat decisions**, under *Log*, is the one row: whether the session file gets
  wizsprinter's turn trace (`logging_setup.set_turn_trace`). It is a `settings.json` key,
  not a preference, since it is about this app's log. Off by default, as it adds a few
  kilobytes a round for each client; developer mode records it either way. OK applies it
  at once, with no restart.
- **The note says what a run will do with an odd choice** (`default_choice_notes`): one
  gone from the library, one that does not fit every size it answers for (a Team default
  for four, run with two), or one linking a player strategy that is gone. Each falls back
  at run time exactly as the note says, with nothing written.
