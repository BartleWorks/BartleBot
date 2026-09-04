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
