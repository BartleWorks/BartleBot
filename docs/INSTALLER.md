# The installer

What an install puts where, and what each of install, update and uninstall does. Velopack
does the installing, per user and with no elevation. How a release is built is
`docs/RELEASING.md`. The plan behind both is epic #27 (`docs/EPICS.md`, *Installer and
updater*).

## What installs where

Setup.exe installs into `%LocalAppData%\BartleBot`. The folder is named for Velopack's pack
id, `PACK_ID` in `tools/pack_release.py`, which is `PRODUCT` and never changes, since an
update finds its install by it.

| path | what it is |
|---|---|
| `%LocalAppData%\BartleBot\BartleBot.exe` | Velopack's stub. It starts `current\BartleBot.exe`, and its path stays the same across updates, so this is the exe anything outside the folder should name |
| `%LocalAppData%\BartleBot\Update.exe` | Velopack's updater and uninstaller |
| `%LocalAppData%\BartleBot\current\` | the app: the PyInstaller build, as `tools/build_package.py` made it, plus Velopack's `sq.version` manifest |
| `%LocalAppData%\BartleBot\packages\` | the installed version's full package, which the next update's delta is applied against |
| `%APPDATA%\Microsoft\Windows\Start Menu\Programs\BartleBot.lnk` | the Start Menu entry, and the only shortcut (`SHORTCUTS`). No Desktop shortcut |
| `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\BartleBot` | the entry in Settings › Apps. Publisher is `VENDOR`, and the uninstall command is `Update.exe --uninstall` |

Velopack also keeps its own log at `%LocalAppData%\velopack\velopack.log`, which
outlives an uninstall, and works in `%TEMP%\velopack_BartleBot`, which does not.

The install folder is read-only to the app, as the project root is to a checkout:
nothing the app writes goes in it (`test_install_dir_writes`). An update replaces it, so
anything written there would be lost.

**What the app writes is elsewhere, and survives everything here:**

- `%APPDATA%\Bartleby`: the settings, logs, autosaves, the user's own content, and what
  every product by the vendor shares (`shared/vendor.py`).
- `%LocalAppData%\Bartleby\BartleBot\cache`: Qt's cache, where the themed stylesheet
  icons are drawn (`icon_utils`). Qt names it from the organization and the application,
  which `AppController` sets before the first stylesheet. **Never drop the organization
  name:** without it, Qt's cache is `%LocalAppData%\BartleBot\cache`, inside the install
  folder.

## Install

Setup.exe needs no elevation, and asks for none. It extracts the app, makes the Start
Menu entry, runs the install hook, writes the uninstall entry, and then starts the app,
which opens into first-run setup (`docs/FIRST_RUN.md`). That setup is the interactive
part of the install: Velopack shows no pages of its own, only a progress window for the
few seconds it takes. `Setup.exe --silent` installs the same way but starts nothing.

## Update

Velopack downloads a delta when the feed has one against the installed version, and the
full package when it doesn't. It checks each package against the SHA-256 in
`releases.win.json`, and refuses one that differs. Then it replaces `current\` whole, so
a file dropped from a release is gone afterwards. The before-update hook runs in the old
version, and the after-update hook in the new one. When and how the app offers an update
is epic #27's updater stage.

## Uninstall

`Update.exe --uninstall` runs the uninstall hook, then removes the Start Menu entry, the
install folder and the uninstall entry. It asks nothing, and keeps all user data:
`%APPDATA%\Bartleby` and Qt's cache are not touched. A hook cannot show a prompt, so
there is no "remove my data too" option. Anyone who wants the data gone deletes the
folder.

## The hooks

At each of those moments Velopack runs the installed app with an argument naming it
(`--veloapp-install 1.2.0` and so on). `bartlebot.py` calls `run_velopack()` before
anything else (`packaging/install_hooks.py`). In an installed build that hands the launch
to `velopack.App().run()`, which calls the matching hook and exits the process, so the
app never starts. Any other launch returns from `run()` and starts as usual.

| hook | runs | limit |
|---|---|---|
| `after_install` | after install, before the app is started | 30 s |
| `before_update` | in the old version, before its folder is replaced | 15 s |
| `after_update` | in the new version, after the swap | 15 s |
| `before_uninstall` | before the folder is removed | 30 s |

Velopack kills a hook that runs past its limit, and the hook runs without a console or
a window, so a hook may never show UI and never wait on anything. That is why
`run_velopack()` comes before Qt, logging and the keyring backend. The hooks do nothing
yet. On the spike's machine the install hook took 0.6 s, almost all of it the frozen
interpreter starting.

**A checkout never calls Velopack.** `run_velopack()` returns at once unless
`packaging.build_kind.is_packaged()`, without importing Velopack. Called from a checkout,
`App().run()` finds no install and says so on stderr, but still returns. A packaged build
that is not installed, such as CI's, does the same and starts as usual.

## Trying an install, an update and an uninstall

The repo is private until 1.0.0, so a release's downloads need authentication, and an
install is tried from a local folder instead. This touches the machine it runs on, so
check that `%LocalAppData%\BartleBot` does not already exist, and uninstall afterwards.

1. Build twice: `tools/build_package.py --out <dir>`, and a copy of that folder with one
   extra file in `BartleBot\`.
2. Pack the copy as `0.1.0` into one folder, then pack the build as `0.1.1` into a second
   folder that already holds the 0.1.0 full package (`tools/pack_release.py`, with
   `--vpk`). The second pack makes the delta.
3. Run 0.1.0's `BartleBot-win-Setup.exe`. To see first-run setup from a fresh profile,
   start it with `APPDATA` pointed at an empty folder: the app it starts inherits the
   variable.
4. Update with `velopack.UpdateManager(<second folder>, None, locator)`. The locator is a
   `VelopackLocatorConfig` naming the install's paths, since the script is not the
   installed app. `apply_updates_and_exit` applies it once the script has exited.
5. Uninstall with the uninstall entry's quiet command, `Update.exe --uninstall --silent`.

Stage 2 of #27 did exactly this. Its As built box on the plan page has the numbers.
