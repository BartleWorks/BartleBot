# Settings

The settings store and the settings dialog. What a *bot option* is, and how it outranks an app setting, is in `docs/BOT_OPTIONS.md`.

Use `SettingsManager` singleton. Persisted to `%APPDATA%/Bartleby/BartleBot/settings.json`.
Credentials stored via `keyring` (Windows Credential Manager), never on disk.

**Accounts and launch profiles are not in that file.** They are about the *account*, not
about this app, so they live one level up in the vendor-level store —
`%APPDATA%/Bartleby/accounts.json`, `bartlebot/shared/account_store.py` — where a second
product by this publisher can read them. `SettingsManager.accounts` and `.launch_profiles`
are a facade over it and are still what you call; `SettingsManager.account_store` is the
seam, and the only reason to reach it is a change to *both* lists that must be one write.
The vendor prefix, on disk and in Credential Manager, is `shared/vendor.py` and is defined
exactly once.

**Where a value lives, of the three files:**

| file | holds |
|---|---|
| `%APPDATA%/Bartleby/preferences.json` | a preference about the *user* that every product reads, such as the combat defaults. Flat, dotted keys (`combat.default_duel.solo`), `"version": 1`. `bartlebot/shared/preferences.py`, `PreferenceStore` |
| `%APPDATA%/Bartleby/accounts.json` | accounts and launch profiles |
| `%APPDATA%/Bartleby/BartleBot/settings.json` | what only this app has: layout, sidebars, Safety Delay, bot options |

**The write rule for the two shared files:** two products can be open at once, so a
shared file is never written from memory. Every write is
`bartlebot/shared/json_update.update_json(path, mutate)`: read the file as it is, change
only the key you own, check nobody wrote in between (retrying up to `UPDATE_RETRIES`),
and rename a temporary over it. Keys you do not know are kept; the last writer of a key
wins, which is right for a preference and why the strategy library, with authored content
to lose, refuses and asks instead. A new shared record goes through `update_json` too,
and `PreferenceStore` has no cache, so a read sees the other product's last write.
`AccountStore` does cache, and its callers write whole lists from that copy, so within a
key it merges per entry (an account by `username`, a profile by `name`) against what it
last read: only the entries it changed are applied, and the other product's adds, edits
and removes stand. A list is one value only for a preference.

Two rules about the settings dialog itself:

- **Every key in `_DEFAULTS[section]` must also be a field on that section's
  pydantic model.** Pydantic *ignores* an extra key rather than rejecting it, so a
  key in one and not the other is not an error — it is simply never type-checked,
  and a hand-edited `settings.json` can then put a string where a bool belongs and
  have it reach the widget. `streamer_mode` and `bot_library_path` were both in
  that state. `test_every_default_is_declared_on_its_model` is the guard.
- **A row explains itself on the page, not on hover — in one line.** Every
  setting is a `rows.SettingRow`: the title, one line under it saying what the
  setting does, and the control right-aligned in a fixed-width slot. The line is
  always on screen; anything longer, the *why*, goes in `details=` and folds behind
  the row's `?`, and the page's *Show descriptions · Hide descriptions* open or
  fold every row at once (#148); a form with no `details=` to open hides them
  (`SettingsForm.finish`). Before the description-row rework the Log, Layout
  and Auto-save rows had no explanation at all; after it every row carried a
  paragraph, and the Bot tab was 1,392px tall. A tooltip still never carries the
  whole explanation on its own.
  `test_every_settings_row_explains_itself_on_the_page` checks every row has its
  line, and that no tab still builds a `QFormLayout` row.
- **Build a tab from `rows`, not by hand.** A form tab subclasses
  `rows.SettingsForm` (`add_heading`, `add_row`, `add_group`, `finish`); a tab that
  is not all form — Theme, Game — holds one with `margin=0` for its rows. The
  dialog finds every form, so one tab's description links reach the others. A row
  gets the sidebar's gutter through `build_gutter_row`: the override bar lights
  when the value has left the app's default (`settings_manager.default_for`, in the
  control's own terms), and `RevertButton` puts the default back — as pending as
  any other edit until OK. Each setting is its own bordered card; sections stay
  `RuleHeading`s.
- **Only a setting with dependents folds.** A `rows.SettingGroup` is the parent's
  own row (a check box) with the assignment card's chevron in its last slot, and the rows
  that only count while it is on — Quicksell, Auto-save, Grid. Off grays them,
  never hides them; collapsed, a change inside lights the header's bar. Layout is
  not a group: Maximize being *on* is what makes the window size moot. Every group
  id is declared in `SETTINGS_FOLD_DEFAULTS`, or the others' reads prune its fold.
- **Folds go in `ui_state.json`, not `settings.json`.** Which groups a reader left
  open is view state, safe to lose: `bartlebot/gui/ui_state.py` keeps only departures
  from a default, keyed by surface and group, and reads the file afresh on every
  call so two holders cannot overwrite each other. Whether descriptions are shown
  is a setting of its own, `show_setting_descriptions`, written at once whatever
  the dialog's OK or Cancel, and apart from the sidebar's
  `show_option_descriptions`.
- **A section is a `RuleHeading`, not a bold label.** A tab is long, and on a
  long page a bold line of text is not enough to stop the eye — the Bot tab's five sections ran together as one column of
  rows. `widgets/labels/rule_heading.py` is the same heading the detail panes use: the
  name at the left margin the fields align to, and a hairline out to the right.
  It carries its own space above, so a section adds the heading and nothing else.
  `test_every_settings_section_is_a_rule_heading` is the guard.

- **The dialog fits the screen; the tabs do not have to.** A visible hint under
  every row once grew the dialog taller than a 1080p screen and put OK and Cancel
  under the taskbar. A tab with every description and group open is still taller
  than the dialog, so the five form tabs are wrapped in `rows.ScrollableTab`,
  which caps the column at `PAGE_MAX_WIDTH_PX`, centers it, and reserves the scroll
  bar's space so a fold does not jolt the column sideways (#112). Wrap a new
  form tab too. Do **not** wrap a tab that carries the layout's stretch, as the Game tab's two card columns do:
  inside a scroll area they collapse to a couple of visible rows, and a column
  that scrolls inside a page that scrolls is two bars for one list. `_fit_to_screen` caps the
  dialog at `screen_fit.size_within_screen` on the way up as the last defense, and
  a wrapper forwards `apply()` so the tab in the bar is still one `apply_all` can
  call.

## Card actions

Every write to an account or a launch profile goes through one of the module-level
functions in `accounts_tab.py` — `add_`/`edit_`/`delete_account`, and
`add_`/`edit_`/`duplicate_`/`delete_launch_profile`. **Never open `_AccountDialog` or
`ProfileDialog` yourself, and never write `sm.accounts` or `sm.launch_profiles` from a
widget.** Two surfaces manage these, and since the Game tab card rework they draw the same cards out of
the same `CardColumn`; a second copy of the delete cascade is how they drift.
`reorder_accounts` and `reorder_launch_profiles` are the same rule for a drag —
both refuse a permutation that no longer describes the stored list, and both go through
`card_columns.apply_order`, which is where that guard lives now that three surfaces take
a permutation from a column.

The profile editor is `profile_editor.py`, not this module: it draws the same
`AccountCard`s in the same `CardColumn` as the Game tab, in `CardMode.MEMBER`. Its two
controls divide one job — the picker owns *which* accounts, the column owns *what order*
— and both removal routes go through `reconcile_membership`. Do not give either control
its own list; that is how a card whose cross removes it is left ticked in the popup.

Three things every one of them does, and a new one must:

- **Return whether anything was written.** That is the caller's cue to refresh. It is
  false for a cancelled dialog, a declined confirm, and a name that was refused.
- **Persist it.** Assigning `sm.accounts` or `sm.launch_profiles` writes the shared
  store through, so no `SettingsManager.save()` follows — but an action that changes
  *both*, as `delete_account` does, goes through `sm.account_store` with `save=False` on
  the first so the pair reaches disk once. A card on the launch screen has no Settings
  dialog behind it, so an action that persists nothing survives until the process exits
  and no further — which is exactly how the Add-card work shipped.
- **Be called from a synchronous Qt slot.** Each runs `exec()`, and Python 3.13 forbids
  re-entering Qt's event loop from inside a running asyncio task.

Deleting an account cascades: the credential, the character roster, and the username in
every profile that launched it. That is why it is a function and not a button handler.

`SettingsDialog.apply_all` iterates the tab bar rather than naming four tabs.
A hand-written list is one line to forget when a tab is added, and forgetting it
discards everything the user typed into the new tab with no error anywhere;
Everything on the Game tab that a card action writes persists eagerly, so
`AccountsTab.apply` writes only its own two form rows — the launch options under
*On launch*, which wait for OK like every other row in the dialog. Two ways to
persist on one tab is not an oversight: a card on the launch screen has no
Settings dialog behind it to press OK on, and a check box in this dialog that
ignored Cancel would be the odd one out.

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
and every NodeSpec that declares the option against it.

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
