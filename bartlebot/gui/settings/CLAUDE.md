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

Two rules about the settings dialog itself:

- **Every key in `_DEFAULTS[section]` must also be a field on that section's
  pydantic model.** Pydantic *ignores* an extra key rather than rejecting it, so a
  key in one and not the other is not an error — it is simply never type-checked,
  and a hand-edited `settings.json` can then put a string where a bool belongs and
  have it reach the widget. `streamer_mode` and `bot_library_path` were both in
  that state. `test_every_default_is_declared_on_its_model` is the guard.
- **A row explains itself on the page, not on hover.** `settings/rows.py`'s
  `hint()` is the dimmed line under a control; a tooltip is for a longer *why*
  and never carries the whole explanation on its own. Before this the Log, Layout
  and Auto-save rows had no explanation at all and the rest had theirs only where
  a reader has to already suspect it is there.
  `test_every_settings_row_explains_itself_on_the_page` checks that a hint
  follows every control, within two rows so a pair sharing one hint is allowed.
- **A section is a `RuleHeading`, not a bold label.** Every hint is two rows of
  height, so a tab is long, and on a long page a bold line of text is not enough
  to stop the eye — the Bot tab's five sections ran together as one column of
  rows. `widgets/rule_heading.py` is the same heading the detail panes use: the
  name at the left margin the fields align to, and a hairline out to the right.
  It carries its own space above, so a section adds the heading and nothing else.
  `test_every_settings_section_is_a_rule_heading` is the guard.

- **The dialog fits the screen; the tabs do not have to.** A visible hint under
  every row is two rows of height per setting, which grew the dialog taller than a
  1080p screen and put OK and Cancel under the taskbar. The height stays —
  it is what the descriptions cost — and the three form tabs are wrapped in
  `rows.ScrollableTab` instead. Wrap a new form tab too. Do **not** wrap a tab
  that carries the layout's stretch, as the Game tab's two card columns do:
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
