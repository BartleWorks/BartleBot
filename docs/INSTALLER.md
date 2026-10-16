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
| `HKCU\Software\Bartleby\BartleBot` | the install key, written by our install hook (*The install key*) |
| `HKCU\Software\Classes\.bbot`, five more, and their ProgIDs | the share file types, registered by our install hook (*The file types*) |

Velopack also keeps its own log at `%LocalAppData%\velopack\velopack.log`, which
outlives an uninstall, and works in `%TEMP%\velopack_BartleBot`, which does not.

The install folder is read-only to the app, as the project root is to a checkout:
nothing the app writes goes in it (`test_install_dir_writes`). An update replaces it, so
anything written there would be lost.

**What the app writes is elsewhere, and survives everything here:**

- `%APPDATA%\Bartleby`: the settings, logs, autosaves, the user's own content, and what
  every product by the vendor shares (`shared/vendor.py`).
- `%LocalAppData%\Bartleby\BartleBot\updates`: an update being downloaded, the verified
  feed and its packages (*The update check*). Emptied at the start of every download, and
  removed by the update and uninstall hooks, since it is never the user's data.
- `%LocalAppData%\Bartleby\BartleBot\cache`: Qt's cache, where the themed stylesheet
  icons are drawn (`icon_utils`). Qt names it from the organization and the application,
  which `AppController` sets before the first stylesheet. **Never drop the organization
  name:** without it, Qt's cache is `%LocalAppData%\BartleBot\cache`, inside the install
  folder.
- `%LocalAppData%\Bartleby\cache\wizwalker`: wizwalker's cache of what it reads from the
  game's wads (`wad_cache.data`, `template_ids.json`, `langmap.json`), at the vendor level
  because it describes the game rather than this product, and local because
  `wad_cache.data` records this machine's game files. `AppController` points wizwalker at it
  before the handler exists (`clients/wizwalker_cache.py`, #234); wizwalker's own default is
  `%LocalAppData%\StarrFox\WizWalker\Cache`, after its original author. An older one left
  there is neither moved nor deleted: it is rebuilt on demand, and other wizwalker tools
  share it. The `tools/` scripts still use that default.

## Install

Setup.exe needs no elevation, and asks for none. It extracts the app, makes the Start
Menu entry, runs the install hook, writes the uninstall entry, and then starts the app,
which opens into first-run setup (`docs/FIRST_RUN.md`). That setup is the interactive
part of the install: Velopack shows no pages of its own, only the logo with a progress bar
along its foot for the few seconds it takes (`docs/RELEASING.md`, `--splashImage`).
`Setup.exe --silent` installs the same way but shows nothing and starts nothing. An update
shows no splash.

## Update

Velopack downloads a delta when the feed has one against the installed version, and the
full package when it doesn't. It checks each package against the SHA-256 in
`releases.win.json`, and refuses one that differs. Then it replaces `current\` whole, so
a file dropped from a release is gone afterwards. The before-update hook runs in the old
version, and the after-update hook in the new one. When and how the app offers an update,
and how it knows the feed is ours, is *The update check* below.

## Uninstall

`Update.exe --uninstall` runs the uninstall hook, which deletes the install key and the
file types (below), then removes the Start Menu entry, the install folder and the
uninstall entry. It asks nothing, and keeps all user data:
`%APPDATA%\Bartleby` and Qt's cache are not touched. A hook cannot show a prompt, so
there is no "remove my data too" option. Anyone who wants the data gone deletes the
folder.

## The hooks

At each of those moments Velopack runs the installed app with an argument naming it
(`--veloapp-install 1.2.0` and so on). `bartlebot.py` calls `run_velopack()` before
anything else (`packaging/install_hooks.py`). In an installed build that hands the launch
to `velopack.App().run()`, which calls the matching hook and exits the process, so the
app never starts. Any other launch returns from `run()` and starts as usual.

| hook | runs | does | limit |
|---|---|---|---|
| `after_install` | after install, before the app is started | writes the install key, registers the six types | 30 s |
| `before_update` | in the old version, before its folder is replaced | nothing | 15 s |
| `after_update` | in the new version, after the swap | the same as `after_install`, and removes the staged update | 15 s |
| `before_uninstall` | before the folder is removed | deletes the key and BartleBot's types, hands on or removes the shared ones, and removes any staged update | 30 s |

Velopack kills a hook that runs past its limit, and the hook runs without a console or
a window, so a hook may never show UI and never wait on anything. That is why
`run_velopack()` comes before Qt, logging and the keyring backend. A hook writes a few
registry values and removes one folder of the app's own, and nothing else: on the spike's machine the install hook took 0.6 s
while it did nothing, almost all of it the frozen interpreter starting, and the suite
holds the three that write to a tenth of the 15 s limit.

A hook never raises. Nothing it writes is needed for the app to start, and a hook that
raised would fail the install, update or uninstall around it, so what fails is printed to
stderr (logging is not set up yet) and the step goes on.

`after_update` writes exactly what `after_install` did. The values are the same, since
nothing in them changes with an update, so writing them again only mends a key or a type
that something removed, and registers a share type a new release added.

**A checkout never calls Velopack**, so it never writes the registry either. `run_velopack()` returns at once unless
`packaging.build_kind.is_packaged()`, without importing Velopack. Called from a checkout,
`App().run()` finds no install and says so on stderr, but still returns. A packaged build
that is not installed, such as CI's, does the same and starts as usual.

## The install key

Each product by the vendor records itself under `HKCU\Software\Bartleby\<product>`
(`shared/install_key.py`), so each can find the others. It holds two values, both the same
across updates, so no update hook has to rewrite them and a hook that fails cannot leave
them stale:

| value | holds |
|---|---|
| `InstallDir` | the folder the product's shipped content is under, the one holding `strategies/`. For a PyInstaller build it is `current\_internal`, `project_root()` in the installed app |
| `Executable` | Velopack's stub, `%LocalAppData%\BartleBot\BartleBot.exe`, which starts `current\BartleBot.exe` with the arguments it was given (`install_hooks.installed_executable`) |

Two things read it. The shared Official set is read from whichever install directory has
the highest version (`shared/official_set.py`, `docs/COMBAT_CONFIG.md`), and that version
is read from `strategies/official_set.json` in each directory, never from the registry, so
it can never disagree with the files beside it. A key whose directory is gone is skipped.
And the uninstall hook reads the other products' `Executable` to hand them the shared file
types.

Uninstall deletes only this product's key. The vendor key goes too once no product has
one left.

## The file types

Users send each other bots and strategies as files (`docs/SHARING.md`). The install and
update hooks register all six kinds per user, under `HKCU\Software\Classes`, which needs
no elevation (`shared/file_types.py`):

| extension | ProgID | Explorer says | owned by |
|---|---|---|---|
| `.bbot` | `BartleBot.Bot` | BartleBot bot | BartleBot |
| `.bbcombat` | `BartleBot.BotCombatStrategy` | BartleBot Bot Combat Strategy | BartleBot |
| `.bbsubgraph` | `BartleBot.Subgraph` | BartleBot subgraph | BartleBot |
| `.bbduel` | `Bartleby.DuelStrategy` | Bartleby duel strategy | every product |
| `.bbplayer` | `Bartleby.PlayerStrategy` | Bartleby player strategy | every product |
| `.bbdeck` | `Bartleby.Deck` | Bartleby deck | every product |

The descriptions are `FILE_TYPES` in `content/share_export.py`, the words the save dialog
uses too. Explorer hides a registered extension by default and shows the description, so
the description is what a user reads. Each ProgID has the exe's icon and an open command,
`"<Executable>" "%1"`. Each extension names its ProgID, and lists it under
`OpenWithProgids`. Explorer reads associations once and keeps them, so every change ends
with `SHChangeNotify(SHCNE_ASSOCCHANGED)`.

`.bbtemplate` is not registered: templates are not shared until after 1.0.0 (#165).

**The shared three belong to every product.** Each product registers them with the same
ProgID, so the one that registered last opens them. Uninstalling one hands them on: the
uninstall hook removes BartleBot's own three, then points the shared three at the first
other product whose key names an `Executable` that is there, or removes them when there is
none. An extension another program has claimed since is left to it, with only our ProgID
taken off its `OpenWithProgids`.

**The suite never writes the machine's registry.** `tests/conftest.py` points both roots,
`install_key.REGISTRY_KEY` and `file_types.CLASSES_KEY`, at a throwaway key per worker,
removed at the end of the session, and Explorer is never told. A test that writes on
purpose works under a scratch key of its own.

## One app per user

A double-clicked share file starts the exe with the file as its argument, and a second
launch would otherwise open a second app with its own window and its own clients. So the
first launch listens on a `QLocalServer`, and every later one connects, sends its
arguments and exits (`gui/single_instance.py`). `bartlebot.py` does this as soon as the
`QApplication` exists, before the log starts, so a launch that only hands over neither
writes a session log nor prunes a real one from the ten kept.

The running app brings its window to the front at once, restoring it if it is minimized.
The second launch calls `AllowSetForegroundWindow` first, since Windows gives the
foreground only to the process the user acted on. The files wait for any modal dialog to
close (`LaunchQueue`), so an import's summary never opens over setup or over another
import's. Then each share file goes through `_import_share_file`, as a file named at the
app's own launch does, which refuses while a bot runs (#181).

**The name** is `Bartleby.BartleBot.` and a hash of the Windows user, so two people
signed in to one machine each have one app, and a pipe listing shows no user name. The
server answers only its own user (`UserAccessOption`). A checkout adds `.checkout`, and
`BARTLEBOT_DEV` adds `.dev`, so a developer's launches never reach the installed app and
the installed app never takes theirs. A packaged build that is not installed, such as
CI's, shares the installed app's name.

This keeps one *window* per user. It does not keep two processes off one game client: a
checkout, BartleBuddy or another tool can still hook it. That is #233.

## The update check

An installed BartleBot looks for a newer release once, after its window is up, and offers
it once. It never updates on its own, and never while a bot runs. A checkout never checks:
only a build Velopack installed has anything to update, and `updates.check.installed()`
asks for Velopack's manifest, `sq.version`, beside the exe.

| piece | where |
|---|---|
| the check: is there a newer release, and getting it ready for Velopack | `bartlebot/updates/check.py` |
| reading the feed, and its signature | `bartlebot/updates/feed.py` |
| the keys a feed may be signed by | `bartlebot/updates/keys.py`, `TRUSTED_KEYS` |
| when the popup opens, the title bar's button, Help › Check for Updates… | `bartlebot/gui/update_prompt.py` |
| the popup | `bartlebot/gui/dialogs/update_dialog.py` |
| the setting | Settings › General › Updates, `updates.check_at_startup` (`gui/settings/CLAUDE.md`) |

### When it checks

At startup, in a worker thread started before setup or a launch-time import opens, so the
window never waits on the network, and only with "Check for updates at startup" on. It is
on by default: a release is the only way a fixed Official bot reaches anyone. Help › Check
for Updates… runs the same check whatever the setting says, and always answers: up to
date, couldn't check, or an update refused. It is disabled in a checkout, with a tooltip
saying why. Nothing checks again while the app stays open.

**Being offline is ordinary.** A feed that cannot be read is logged at INFO, and the
startup check shows nothing.

### The signature

Velopack checks every package against the SHA-256 in the feed, `releases.win.json`, and
refuses one that differs. That protects the packages and not the feed: whoever could swap
a package on the release could swap the feed beside it. So CI signs the feed with Ed25519
into `releases.win.json.sig` (`docs/RELEASING.md`, *The signing key*), and the app reads
both from the latest release, `releases/latest/download/`, and verifies the signature
against every key in `TRUSTED_KEYS`. **A feed whose signature does not verify, or that has
none, is logged as a WARNING and its update is never offered.**

Velopack never fetches a feed itself. The verified bytes are written to
`%LocalAppData%\Bartleby\BartleBot\updates`, and that folder is Velopack's source
(`UpdateManager(<folder>)`), so the feed it checks each package against is the one that
verified, never a second copy fetched a moment later. Velopack is asked what it needs from
there: the deltas from the installed version when the feed lists them, or the full package.
Only those are downloaded into the folder, from the same release. A delta that will not
apply falls back to the full package, as Velopack itself would.

The signature guards against a release asset swapped after the build, and a download
changed on the way. It does not guard against whoever controls the GitHub organization,
who could sign with the key CI holds: that was accepted when the key went into CI (epic
#27's plan).

**A key is replaced by a release.** `TRUSTED_KEYS` is a list, and any key in it will do.
A release signed by the old key adds the new one, and every installed copy accepts it and
takes the new list with the update. A later release can be signed by the new key, and one
after that can drop the old. `docs/RELEASING.md` has the steps.

### The popup

**Once per version.** `updates.last_offered` in `settings.json` is the newest version the
popup has opened for. A version newer than it opens the popup, and opening it records the
version. A newer version opens the popup again.

**Never over anything.** First-run setup, an import's summary and any other modal dialog
hold it back, and so does a bot that is starting or running (`AppController._bot_busy`). It
looks again every second (`RETRY_MS`) and opens once the app is free. Every dialog here
opens from a timer's callback, never from inside the check's task (`bartlebot/gui/CLAUDE.md`,
qasync).

It shows the release's notes, from the feed's `NotesMarkdown`, or a link to the release
page when the feed has none, and three choices:

| choice | what happens |
|---|---|
| **Install when I close BartleBot** (Enter) | downloads the update now, with a bar, then `wait_exit_then_apply_updates(restart=False)`: Velopack's updater waits for the app to exit, however long that is, then replaces the install. A bot that is running carries on |
| **Restart now** | downloads it, then `wait_exit_then_apply_updates(restart=True)`, and closes the window the usual way, through the quit gate, so every client is released and the game's display settings are put back before the swap. **Disabled while a bot runs**, since a restart would end it |
| **Not now** (Escape) | closes the popup. The title bar keeps a quiet button, "Update 1.2.0", which opens it again. The next launch shows the button, not the popup, for the same version |

`apply_updates_and_restart` is never called: it exits the process itself, past the quit
path, leaving clients hooked. A download that fails says why in the popup and changes
nothing, so either choice can be tried again. Once an update is waiting for the app to
close, the button goes, and Help › Check for Updates… says it installs on close rather than
handing Velopack the same update twice.

## Trying an install, an update and an uninstall

A draft's downloads need a GitHub sign-in, and a published release is offered to every
user, so an install or an update is tried from a local folder instead. This touches the account it runs in, so
try it in the test account (`docs/RELEASING.md`, *Testing a draft*), or check that
`%LocalAppData%\BartleBot` does not already exist, and uninstall afterwards.

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

**To try the app's own update check**, sign the second folder's feed and point the
installed app at it. `BARTLEBOT_UPDATE_FEED` names another place to read the feed and its
packages from, a URL or a folder (`updates/feed.py`). It changes where, never what is
trusted, so the feed must still be signed by a key in `TRUSTED_KEYS`: sign it with
`tools/sign_feed.py sign` and the key's backup, or build the first version with a
throwaway key added to `TRUSTED_KEYS` in that build only, never committed. Start the
installed 0.1.0 with the variable set, and the popup offers 0.1.1.
