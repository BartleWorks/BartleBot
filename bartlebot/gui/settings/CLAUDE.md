# Settings

The settings store and the settings dialog. What a *bot option* is, and how it outranks an app setting, is in `docs/BOT_OPTIONS.md`.

Use `SettingsManager` singleton. Persisted to `%APPDATA%/BartleBot/settings.json`. Credentials stored via `keyring` (Windows Credential Manager), never on disk.

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

- **The dialog fits the screen; the tabs do not have to.** A visible hint under
  every row is two rows of height per setting, which grew the dialog taller than a
  1080p screen and put OK and Cancel under the taskbar (#188). The height stays —
  it is what the descriptions cost — and the three form tabs are wrapped in
  `rows.ScrollableTab` instead. Wrap a new form tab too. Do **not** wrap a tab
  that carries the layout's stretch, as the Game tab's two card columns do:
  inside a scroll area they collapse to a couple of visible rows, and a column
  that scrolls inside a page that scrolls is two bars for one list. `_fit_to_screen` caps the
  dialog at `screen_fit.size_within_screen` on the way up as the last defence, and
  a wrapper forwards `apply()` so the tab in the bar is still one `apply_all` can
  call.

## Card actions

Every write to an account or a launch profile goes through one of the module-level
functions in `accounts_tab.py` — `add_`/`edit_`/`delete_account`, and
`add_`/`edit_`/`duplicate_`/`delete_launch_profile`. **Never open `_AccountDialog` or
`_ProfileDialog` yourself, and never write `sm.accounts` or `sm.launch_profiles` from a
widget.** Two surfaces manage these, and since #192 they draw the same cards out of
the same `CardColumn`; a second copy of the delete cascade is how they drift.
`reorder_accounts` and `reorder_launch_profiles` are the same rule for a drag —
both refuse a permutation that no longer describes the stored list, because the
column that emitted it was built before the drop.

Three things every one of them does, and a new one must:

- **Return whether anything was written.** That is the caller's cue to refresh. It is
  false for a cancelled dialog, a declined confirm, and a name that was refused.
- **Call `SettingsManager.save()`.** `set` only writes memory. A card on the launch
  screen has no Settings dialog behind it, so an action that skips this survives until
  the process exits and no further — which is exactly how #195 shipped.
- **Be called from a synchronous Qt slot.** Each runs `exec()`, and Python 3.13 forbids
  re-entering Qt's event loop from inside a running asyncio task.

Deleting an account cascades: the credential, the character roster, and the username in
every profile that launched it. That is why it is a function and not a button handler.

`SettingsDialog.apply_all` iterates the tab bar rather than naming four tabs.
A hand-written list is one line to forget when a tab is added, and forgetting it
discards everything the user typed into the new tab with no error anywhere;
`AccountsTab.apply` is a documented no-op (it persists eagerly), which is what
makes iterating safe.

## The Bot tab

Every row on it is the **app setting** level of the four-level resolution below,
so the disclaimer that a bot or a Setup value outranks it is stated once at the
top of the tab rather than repeated into eight tooltips. That is why it is a tab
and not a section of General: it is one idea, and General's other five sections
are not levels of anything.

`Safety Delay`'s description lives in `bot_options.SAFETY_DELAY_DESCRIPTION`,
because the app setting and the *option* are the same value and one value
described twice is how the two texts come to disagree —
`test_the_safety_delay_setting_and_option_read_the_same` checks the tab's hint
and every NodeSpec that declares the option against it.

Anything naming a settings row in user-facing text has to name its tab too: the
`relog` node's help, the "waiting at character select" recovery message and
`teleport_to_friend`'s error all said *Settings → General* and all had to move.
