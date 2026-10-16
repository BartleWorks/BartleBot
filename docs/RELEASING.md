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
uv run --group package python tools/build_package.py pyinstaller
uv run --group package python tools/build_package.py nuitka            # needs Visual Studio 2022
uv run --group package python tools/build_package.py nuitka -- --zig   # or Zig, which Nuitka downloads
```

The packagers are in the `package` dependency group, so a plain `uv sync` leaves them
out. The build lands in `dist/<packager>/` with `build-report.json` beside it: the build
time, the folder's size and file count, and the self-check's lines. The script exits
non-zero when the packager or the self-check fails.

**A release is never built on a maintainer's machine.** CI builds every release from a
tag. A local build is for working on the build itself. The rule is about what a binary
can carry, and the spike found one way it happens. PyInstaller strips source paths, so
none of a local build's 1,332 frozen modules named the checkout. But `--copy-metadata`
copied the editable install's `direct_url.json`, which did. So the script deletes every
install record from the bundled `.dist-info` folders (`INSTALL_RECORDS`): the app reads
only `METADATA`. Nuitka compiles the metadata in, and its build named no local path in
any file. SignPath requires an automated build anyway.

### What is bundled, and why

`bartlebot.paths.project_root()` is `Path(__file__).parent.parent` from
`bartlebot/paths.py`, and in a build that is the build's own root: `_internal/` beside
PyInstaller's exe, and the `.dist` folder for Nuitka. So everything the app reads goes
at the same relative path under that root as under the project root, and no reader
needs to know it is frozen. The lists are in `tools/build_package.py`, and both packagers
read them:

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
  PyQt6 plugin says so on every build. Blocking work goes through `asyncio.to_thread`
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
dist/pyinstaller/BartleBot/BartleBot.exe --self-check
```

It loads everything the app reads at run time, prints one line per check, and exits
with the number that failed. It opens no window and writes nothing. `bartlebot.py`
dispatches it before Qt or logging start. Every check reads what the running app would
read, so in a build it reads the build (`bartlebot/packaging/self_check.py`).

| check | proves |
|---|---|
| `version` | the package metadata is bundled, and `importlib.metadata` reports a version |
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

Both were built in Stage 1 of #27 and judged on what matters for a user: does the build
hook a real client, and what does Defender make of it.

| | PyInstaller 6.22.3, onedir | Nuitka 4.3, standalone |
|---|---|---|
| build time, local (16 threads) | 0.7 min | 10.9 min, with Zig 0.16.0 |
| build time, CI (`windows-latest`, MSVC for Nuitka) | 1.3 min | 62.4 min |
| folder, CI | 162 MB in 515 files | 198 MB in 339 files |
| `--self-check`, local | 11 of 11 | 11 of 11 |
| `--self-check`, CI | 11 of 11 | 11 of 11 |
| Defender, local (engine 4.18.26090.9, signatures 1.461.43.0) | no threats | no threats |
| Defender, CI | no threats | no threats |
| hooks a real client and runs a bot to the end | to come | to come |

Two things the numbers leave out. Nuitka on Python 3.13 needs MSVC 14.3 (Visual Studio
2022) or Zig, and refused the 2019 Build Tools. And its PyQt6 plugin warns on every build
that Qt threading does not work, which is why the app has no `QThread`.

The choice, and why, goes here once the in-game run is in, and the losing configuration
leaves `tools/build_package.py`.

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
- **`Setup.exe --silent` does not start the app.** Without it, Setup opens the app once
  it has installed it, which is how the install opens into first-run setup.
- **The hash check covers the packages, not the feed.** A feed rewritten with the hashes
  of a tampered package passes it. That is the gap Stage 4's signature over
  `releases.win.json` closes.

## `package.yml`

`.github/workflows/package.yml` is started by hand (`workflow_dispatch`). It builds each
packager on a `windows-latest` runner, runs that build's `--self-check`, scans the build
folder with Defender, writes the report and the scan to the job summary, and uploads the
build as an artifact for 14 days. Each packager is its own job, so one failing leaves
the other's result. A workflow can only be started by hand once it is on the default
branch.
