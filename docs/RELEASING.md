# Releasing

How BartleBot becomes something a user with no Python can run. This file covers the
packaged build and the checks that hold it to the checkout. The installer, the release
workflow and the updater are added by the later stages of epic #27 (`docs/EPICS.md`,
*Installer and updater*).

## A packaged build

A build is a folder: an exe, the Python runtime, every module the app imports, and the
data it reads. It is never a single self-unpacking exe (PyInstaller's onefile), which
unpacks into `%TEMP%` at every launch and looks like a dropper to antivirus. The
installer makes onefile pointless anyway.

```bash
uv run --group package python tools/build_package.py
```

The build is a PyInstaller onedir (*The packager*, below, has why). PyInstaller is in the
`package` dependency group, so a plain `uv sync` leaves it out. The build lands in
`dist/BartleBot/` with `dist/build-report.json` beside it: the build time, the folder's
size and file count, and the self-check's lines. The script exits non-zero when
PyInstaller or the self-check fails.

**A release is never built on a maintainer's machine.** CI builds every release from a
tag. A local build is for working on the build itself. The rule is about what a binary
can carry, and the spike found one way it happens. PyInstaller strips source paths, so
none of a local build's 1,332 frozen modules named the checkout. But `--copy-metadata`
copied the editable install's `direct_url.json`, which did. So the script deletes every
install record from the bundled `.dist-info` folders (`INSTALL_RECORDS`): the app reads
only `METADATA`. After that, no file in the build names a local path. SignPath requires an
automated build anyway.

### What is bundled, and why

`bartlebot.paths.project_root()` is `Path(__file__).parent.parent` from
`bartlebot/paths.py`, and in a build that is the build's own root, `_internal/` beside
the exe. So everything the app reads goes at the same relative path under that root as
under the project root, and no reader needs to know it is frozen. The lists are in
`tools/build_package.py`:

| list | holds | why |
|---|---|---|
| `DATA_DIRS` | `bots/`, `templates/`, `subgraphs/`, `strategies/`, `bartlebot/game/data/`, `bartlebot/gui/icons/` | the Official content, the game snapshots and the type dump, and the icons |
| `DATA_FILES` | `bartlebot/gui/logo/bartlebot.svg` | the logo, drawn at every size from this one file |
| `METADATA` | `bartlebot` | `importlib.metadata.version("bartlebot")` is how the app knows its version |
| `WHOLE_PACKAGES` | `katsuba` | `katsuba.op` and `katsuba.wad` are submodules its native extension registers when it loads. No file names them, so an import scan leaves the package out |

`tools/` never ships: it is developer scripts. That is why the type dump art extraction
reads moved to `bartlebot/game/data/` (`tools/CLAUDE.md`, "Type dumps").

### What the build must not depend on

- **No `QThread`.** Nuitka documents Qt threading as unsupported under PyQt6, and its
  PyQt6 plugin says so on every build. PyInstaller won, but the rule stays, so the
  packager can change without the app changing. Blocking work goes through `asyncio.to_thread`
  from a task the widget owns (`bartlebot/gui/CLAUDE.md`, "Background task lifetime").
  `test_no_qthread_remains_in_bartlebot` keeps it out.
- **No backend keyring has to find.** keyring finds its backends through package entry
  points, and a frozen build can lose the metadata those are read from. keyring then
  falls back to a backend that refuses every read and write. `bartlebot.py` names
  `WinVaultKeyring` before anything else runs (`shared/keyring_backend.py`).
- **No module only imported by name.** The node library is a static table
  (`nodes/registry.py`), so the import scan sees every node. A new module loaded through
  `importlib.import_module` would be invisible to it: list it in `WHOLE_PACKAGES`, or
  better, import it.

## The self-check

```bash
uv run python bartlebot.py --self-check          # from the checkout
dist/BartleBot/BartleBot.exe --self-check
```

It loads everything the app reads at run time, prints one line per check, and exits
with the number that failed. It opens no window and writes nothing. `bartlebot.py`
dispatches it before Qt or logging start. Every check reads what the running app would
read, so in a build it reads the build (`bartlebot/packaging/self_check.py`).

| check | proves |
|---|---|
| `version` | the package metadata is bundled, and `importlib.metadata` reports a version. Its line also says `packaged (PyInstaller)` or `checkout` (`packaging/build_kind.py`), as the session log's header does |
| `keyring` | the backend is Windows Credential Manager, and a read through it works. It reads an entry that does not exist, so nothing is written |
| `app` | `app_controller` imports, which pulls in nearly every module, and the node library is not empty |
| `bots` | every Official bot loads |
| `templates` | every template's draft and every released version load, and each release has the bytes its index recorded |
| `subgraphs` | every Official definition loads |
| `shared set` | every duel, player strategy and deck loads, and every shipped default id in `shared/combat_defaults.py` is among them |
| `game data` | every snapshot in `game/data/` is there and parses. The list is the readers' own path constants (`game_data_files`), and a test fails when a file there has no entry |
| `type dump` | the dump art extraction reads is there, and katsuba's native extension opens it |
| `native` | curl_cffi (wiki import) and Pillow's DDS decoder (game art) import, and the combat DSL grammar parses the last-resort DSL |
| `qt` | a `QApplication` starts on Qt's platform plugin, and every icon and the logo render through QtSvg |

A check that raises is a failure with the exception's text, and one failure stops none
of the others, so one run lists everything a build is missing.
`test_the_self_check_passes_from_the_checkout` runs the real command on the checkout, so
a missing file fails a test before it fails a build. A store folder that is missing
fails its own check, which names the folder.

When the app starts reading something new at run time, give it a check, or add it to
one that already reads its kind.

## The packager

**PyInstaller, onedir.** Both packagers were built in Stage 1 of #27 and judged first on
what matters for a user: does the build hook a real client, and what does Defender make
of it. Both hooked and ran a bot, and Defender found nothing in either, so the tie went
to the rest of the table.

| | PyInstaller 6.22.3, onedir | Nuitka 4.3, standalone |
|---|---|---|
| build time, CI (`windows-latest`) | 1.3 min | 62.4 min |
| build time, local (16 threads) | 0.7 min | 10.9 min, with Zig 0.16.0 |
| folder, CI | 162 MB in 515 files | 198 MB in 339 files |
| `--self-check`, CI and local | 11 of 11 | 11 of 11 |
| Defender, CI and local (engine 4.18.26090.9, signatures 1.461.43.0) | no threats | no threats |
| hooks a real client and runs a bot (in game, a fresh profile) | yes | yes, with one unexplained freeze in one session |

Why PyInstaller:

- **A build takes a minute.** Every release, and every run of `package.yml`, waits on it.
  Nuitka took 62 minutes on the runner.
- **A smaller download,** by 36 MB.
- **No caveat on the GUI toolkit.** Nuitka calls its PyQt6 support imperfect and
  recommends PySide6. The one freeze in testing was on Nuitka. Its log shows nothing, so
  it is not known to be Nuitka's fault, but nothing like it happened on PyInstaller.
- **No compiler to keep.** Nuitka on Python 3.13 needs MSVC 14.3 (Visual Studio 2022) or
  Zig, and refused the 2019 Build Tools.

What Nuitka would have bought: its code is compiled rather than bytecode, and its folder
has fewer files. Neither matters to a user here. PyInstaller's known cost is its shared
bootloader, which antivirus tools have flagged because malware uses it too. Defender
did not flag this build, and Stage 6's checklist submits every release to Microsoft's
false-positive portal.

## Velopack

What the Stage 1 spike showed, with Velopack 1.2.161 (`vpk` and the `velopack` Python
package at the same version). A probe app, frozen with PyInstaller onedir, logged every
hook. It was packed as 1.0.0, 1.0.1 and 1.0.2, installed from 1.0.0's Setup.exe, updated
and uninstalled on a Windows 11 machine with Smart App Control off.

| question | answer |
|---|---|
| Does a `vpk pack` build install and run fully unsigned? | **Yes.** `vpk pack` only warns that nothing will be signed. Setup.exe installs per user into `%LocalAppData%\<packId>` with no elevation, and writes the uninstall entry under HKCU. A file with no Mark of the Web never meets SmartScreen, so the SmartScreen prompt a downloaded Setup.exe shows is Stage 6's to describe |
| Do the install, update and uninstall hooks reach Python? | **Yes, all four.** `on_after_install_fast_callback` ran as `--veloapp-install 1.0.0` in 240 ms, against a 30 s limit. `on_before_update_fast_callback` ran in the old version, `on_after_update_fast_callback` in the new one, and `on_before_uninstall_fast_callback` on uninstall. Each was passed the version |
| Does `UpdateManager` apply an update from a local folder? | **Yes.** `UpdateManager(<folder>)` found 1.0.1, chose the 26 KB delta over the 11 MB full package, downloaded it, and `apply_updates_and_exit` left 1.0.1 installed |
| Does it refuse a package whose SHA-256 differs from the feed's? | **Yes.** With one byte changed in 1.0.2's delta and in its full package, the delta failed its SHA-256 check, Velopack fell back to the full package, that failed too, and `download_updates` raised. The install stayed at 1.0.1 |

Things the spike showed that a later stage has to handle:

- **A Desktop shortcut by default.** `vpk pack` writes `Desktop,StartMenuRoot` into the
  package's shortcut list. Stage 2 passes `--shortcuts StartMenuRoot`, since the plan
  promises a Start Menu entry and nothing more.
- **`Setup.exe --silent` does not start the app.** Velopack documents that a Setup run
  without it opens the app once it has installed it, which is how the install opens into
  first-run setup. The spike did not try that, so Stage 2's Done when checks it.
- **The hash check covers the packages, not the feed.** Velopack checks a package against
  the hashes in `releases.win.json`, so a feed rewritten with a tampered package's hashes
  would pass it. The spike did not try that, since it follows from where the hashes are.
  It is the gap Stage 4's signature over the feed closes.

## `package.yml`

`.github/workflows/package.yml` is started by hand (`workflow_dispatch`). It builds on a
`windows-latest` runner, runs the build's `--self-check`, scans the build folder with
Defender, writes the report and the scan to the job summary, and uploads the build as an
artifact for 14 days. A workflow can only be started by hand once it is on the default
branch. Stage 1 ran it from its branch with a temporary `push` trigger, dropped before
the merge (run 38927474706, both packagers). Stage 2's `release.yml` builds the same way
on a tag.
