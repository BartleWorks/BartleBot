# Releasing

How BartleBot becomes something a user with no Python can run. This file covers the
packaged build and the checks that hold it to the checkout, the version, the release
workflow that turns a tag into a signed draft release, the download site, how a draft is
tested before it is published, and the key it signs with. What an
install puts where, what install, update and uninstall do, and how an installed app finds
and checks an update, is `docs/INSTALLER.md`. The plan behind both is epic #27
(`docs/EPICS.md`, *Installer and updater*).

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
PyInstaller or the self-check fails. It also draws `dist/bartlebot.ico` from the logo's
SVG (`tools/export_logo.py`'s `write_icon`) and gives it to the exe, so the Start Menu
entry, the uninstall entry and Explorer show the logo rather than PyInstaller's icon.
Beside it goes `dist/bartlebot-splash.png` (`write_splash`), the logo on its square at
288 px, which only `vpk pack` reads: Setup.exe shows it while it installs.

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
| `METADATA` | `bartlebot`, `PyQt6`, `qasync`, `transitions`, `wizwalker` | `importlib.metadata.version("bartlebot")` is how the app knows its version, and the session log's header names it and the four libraries' (`logging_setup.HEADER_LIBRARIES`) |
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
| `native` | curl_cffi (wiki import), Pillow's DDS decoder (game art) and Velopack import, the combat DSL grammar parses the last-resort DSL, and cryptography loads every key in `TRUSTED_KEYS` and refuses a signature of zeros (the update feed) |
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

What the spike showed, and what was done about it:

- **A Desktop shortcut by default.** `vpk pack` writes `Desktop,StartMenuRoot` into the
  package's shortcut list. `tools/pack_release.py` passed `--shortcuts StartMenuRoot`,
  since the plan promised a Start Menu entry and nothing more. #240 put the Desktop
  shortcut back, so a user finds the app without knowing where `%LocalAppData%` is.
- **`Setup.exe --silent` does not start the app.** A Setup run without it does, and the
  app opens into first-run setup. Stage 2 saw that from a fresh profile
  (`docs/INSTALLER.md`).
- **The hash check covers the packages, not the feed.** Velopack checks a package against
  the hashes in `releases.win.json`, so a feed rewritten with a tampered package's hashes
  would pass it. The spike did not try that, since it follows from where the hashes are.
  It is the gap the signature over the feed closes (*The signing key*).

## `package.yml`

`.github/workflows/package.yml` is started by hand (`workflow_dispatch`). It builds on a
`windows-latest` runner, runs the build's `--self-check`, scans the build folder with
Defender, writes the report and the scan to the job summary, and uploads the build as an
artifact for 14 days. A workflow can only be started by hand once it is on the default
branch. Stage 1 ran it from its branch with a temporary `push` trigger, dropped before
the merge (run 38927474706, both packagers). `release.yml` builds the same way on a tag.

## The version

**BartleBot has one version number: `[project] version` in `pyproject.toml`.** The app
reads it through `importlib.metadata`, from the metadata `uv sync` writes and the build
bundles (`METADATA`). Share files record it, the self-check prints it, Velopack is given
it as the package version, and the release tag names it. Nothing else holds a version.

It is SemVer 2, `MAJOR.MINOR.PATCH` with an optional `-prerelease`, because Velopack
compares versions that way and refuses anything else. To release a version:

1. Set it in `pyproject.toml`, and run `uv lock`. The lock records the project's version,
   so a bump without relocking fails `uv sync --locked` in CI.
2. Write its notes, `release_notes/<version>.md` (*The release notes*, below).
3. Commit, and tag that commit `v<version>`: `v1.2.0` for `1.2.0`.

`tools/release_version.py <tag>` is the workflow's first step. It fails when the tag is
not `v` + the version, when the version is not SemVer 2, when the environment's
metadata says something else (a version bumped without `uv sync`), or when the version
has no notes file or an empty one, and prints every reason. On success it prints the
version alone, for the steps after it.

**The betas are 0.x, and 1.0.0 is the first release that is not one.** A beta is an
ordinary release in every other way: it is published as GitHub's latest release, never
marked a pre-release, and nothing passes `--pre` to `vpk`. Both readers of a release
skip pre-releases: the app's `FEED_URL` is `releases/latest/download/`, and
`vpk download github`, which fetches the release the next delta is made against, takes
the latest stable one unless told `--pre`. A beta marked as a pre-release would be
offered to no one and left out of the next delta. It also means the betas run the
release path 1.0.0 will, rather than one of their own.

## A release

`.github/workflows/release.yml` runs on every pushed `v*` tag. It never runs on a branch,
and is never started by hand. It is three jobs, so the signing key reaches only the one
that needs it: `build`, on `windows-latest`, runs every dependency's code and never sees
the key; `sign`, on `ubuntu-latest`, reads only the feed; and `publish`, on
`windows-latest`, is the only one whose token can write.

`build`:

1. **Check the tag**: `tools/release_version.py`, before anything is built.
2. **Build and self-check**: `tools/build_package.py`, as `package.yml` does. A failing
   self-check fails the release.
3. **Fetch the previous release**: `vpk download github` puts the latest *published*
   release's full package and feed in `releases/`. With none published yet, it says so
   and exits 0, and the release has no delta.
4. **Pack**: `tools/pack_release.py <version>`, which composes the release notes and runs
   `vpk pack`. It writes Setup.exe, the full package, a delta against the previous
   release when step 3 found one, and `releases.win.json`, the feed. `vpk` also writes `RELEASES` and
   `assets.win.json`, which it uses itself. Everything goes up as the `releases`
   artifact, and the feed alone as `feed`.

`sign`:

5. **Sign the feed**: `tools/sign_feed.py sign`, with the key in `FEED_SIGNING_KEY`,
   writes `releases.win.json.sig`. Only `cryptography` is installed for it, at uv.lock's
   version (`CRYPTOGRAPHY_VERSION`, which a test holds to the lock).
6. **Check it against the app's keys**: `tools/sign_feed.py verify` fails the release
   when the signature matches no key in `TRUSTED_KEYS`, so a wrong secret fails here
   rather than shipping a feed no installed copy would accept.

`publish`:

7. **Check the feed being published**: the same `verify`, on the `releases` artifact's
   feed and the signature, so the feed uploaded is byte for byte the one signed.
8. **Create the draft**: `vpk upload github`, without `--publish`, creates a draft
   release on the tag with every asset.
9. **Attach the signature**: `gh release upload`, since `vpk` uploads only what it
   packed.

**CI never publishes.** What ships is Official, and Official means tested in game, which
CI cannot do. A maintainer installs from the draft, tests, and publishes it (*Testing a
draft*, below). Publishing is
what makes users see it, and what makes it the "previous release" the next delta is made
against. A draft no one publishes is never offered, so a bad build costs nothing.

**To rebuild a draft, delete it before moving the tag.** A fix found while testing goes in
a new commit, and the tag moves to it. But step 8 refuses when a release on the tag
already exists, draft or not, and fails the run only after the whole build:

```
gh release delete v<version> --yes
git tag -f v<version>
git push -f origin v<version>
```

`gh release delete` without `--cleanup-tag` leaves the tag alone. A run that has already
failed at step 8 needs no new build: delete the draft, then `gh run rerun <run> --failed`
runs `publish` again on the artifacts `build` and `sign` left. The checklist copy in
`C:\Users\Public\BartleBot\<version>` stays as it was, so delete it too when the
checklist changed, or when the ticks were for the build being replaced.

What `vpk pack` is told is in `tools/pack_release.py`, held there so a test can read it:

| constant or flag | value | why |
|---|---|---|
| `PACK_ID` | `PRODUCT` | names the install folder, and matches an update to its install, so it never changes |
| `--packTitle` | `PRODUCT` | the Start Menu entry's and the uninstall entry's name |
| `--packAuthors` | `VENDOR` | the uninstall entry's publisher |
| `--icon` | `dist/bartlebot.ico` | Setup.exe's icon |
| `--splashImage` | `dist/bartlebot-splash.png` | the window Setup.exe shows while it installs, in place of Velopack's plain progress dialog. Velopack draws it borderless at its own size times the monitor's scaling, resampled, so above 100% the cells' edges soften a little; and it lays a 12 px bar, scaled the same, along its bottom edge. So it is the square, opaque to the corners, and 24 px cells leave 36 px of background under the tree for the bar |
| `SPLASH_PROGRESS_COLOR` | `#22b14c` | that bar's color: the leaves' green. Velopack's default is `#00FF00` |
| `SHORTCUTS` | `Desktop,StartMenuRoot` | a Desktop shortcut and the Start Menu entry, so the app is found without knowing where `%LocalAppData%` is. It is `vpk`'s default, spelled out so a change to that default changes nothing here |
| `--noPortable` | | Setup.exe is the one way to install. A portable copy runs outside the install and the updater, and the plan leaves it out of scope |
| `--releaseNotes` | `dist/release-notes.md` | the version's notes and the Installing footer, composed by `release_notes` (*The release notes*, below): the release's description, and the popup's notes |

**Velopack is pinned at one version, `VELOPACK_VERSION`: 1.2.161,** for the `velopack`
package in `pyproject.toml` and for `vpk` in `release.yml` alike. The Stage 1 spike
tried that version. `test_one_velopack_version_for_the_package_and_vpk` holds the lock,
the tool and the workflow to it, so a bump changes all three at once. Before a bump,
read Velopack's advisories and repeat the install, update and uninstall in
`docs/INSTALLER.md`.

### The release notes

Each version's notes are `release_notes/<version>.md`, written by hand before the tag
and committed with the version bump. They say what changed for a user, in their words:
what a release fixes or adds, not which files moved. Write paragraphs and lists, and no
headings, since the popup draws a markdown heading larger than its own.

`tools/pack_release.py` composes what `vpk pack --releaseNotes` is given, into
`dist/release-notes.md`: the version's notes, then the line `INSTALL_NOTES_MARKER`
(`<!-- installing -->`, in `bartlebot/updates/feed.py`), then `INSTALL_FOOTER`: one
paragraph naming the file to run and linking the download site's install page
(`INSTALL_URL`). Links in the version's notes are made absolute at the release's tag
(`absolute_links`): `raw/<tag>/` for an image and `blob/<tag>/` for a link, resolved
against the notes file, since a relative path leads nowhere on a release page, and the tag
keeps an old release's notes showing what that release shipped.

Those notes go two places:

- **The release's description.** `vpk upload github` makes the package's notes the body
  of the draft it creates (Velopack 1.2.161, `_GitRelease.cs`), so nothing in
  `release.yml` writes one. Someone who reaches BartleBot through GitHub rather than the
  site lands on the release page, which is why it ends with the footer.
- **The update popup**, through the feed's `NotesMarkdown`. `feed.parse` cuts the notes at
  the marker, since whoever is offered an update has installed already. GitHub draws the
  marker, an HTML comment, as nothing.

The release page is a changelog, as most apps' are, so the install steps are not copied
into it. Through 0.1.0 it carried the whole install page, which made every release page
mostly the same long text with the changes at the top. The site holds the one copy now,
and `test_the_install_url_the_release_notes_link_is_the_sites_install_page` keeps the
footer's link on it. `test_the_checkouts_version_has_notes` fails a checkout whose version
has no notes before a tag would, and
`test_everything_the_release_notes_link_in_the_repository_is_there` one whose notes link a
file that is not there.

## The download site

`site/` is the site at `bartleworks.github.io/BartleBot`: a landing page with one download
button, and the install page. It is built with [Zensical](https://zensical.org/), pinned in
the `site` dependency group, and is separate from `docs/`, which are the developer docs.
The rest of the user docs, the node reference and the guides, are #25's.

```powershell
uv run --group site zensical serve -f site/zensical.toml   # preview at localhost:8000
uv run --group site zensical build --strict -f site/zensical.toml   # into site/build/
```

**The landing page** is drawn by `site/overrides/home.html`, which `index.md` names in its
front matter (`template: home.html`). The template holds the hero: the title, the download
button, the link to the install page and what is said beside them. The page's words stay
in `index.md`: the hero's `tagline` in its front matter, and below the hero its own
markdown, where a list is drawn as a grid of cards and a bold run at the start of an item
is that card's heading (`stylesheets/extra.css`). A strict build checks no link in a
template, so `test_the_home_templates_links_are_pages_of_the_site` does.

**The download button** links `releases/latest/download/BartleBot-win-Setup.exe`, set
once as `download_url` under `[project.extra]` in `zensical.toml`
(`feed.FEED_URL` and `pack_release.SETUP_NAME`): the same redirect the updater reads its
feed through, which names the newest published release and never a draft or a
pre-release. So the site never changes for a release, and the button fetches each one as
soon as it is published.

**`.github/workflows/site.yml`** builds the site on every push to `master` and every pull
request into it, with `--strict`, which fails the build on a link to a page or an anchor
that is not there. A missing image is no warning to Zensical, so `tests/test_site.py`
checks those, with the rest a build cannot see: the button's URL, the logo, which is a
copy of `bartlebot/gui/logo/bartlebot.svg` because a page can show only files under
`site/pages/`, and no web fonts, so a visit sends nothing to anyone but GitHub. The
workflow publishes the build to GitHub Pages from `master` only, and only while the repo
is public: GitHub Free serves Pages from no private repo.

**Publishing it the first time**, once the repo is public and before the first beta's
tag:

1. Turn Pages on, with GitHub Actions as its source:
   `gh api -X POST repos/BartleWorks/BartleBot/pages -f build_type=workflow`
2. Publish `master`'s site without a commit: `gh workflow run site.yml --ref master`
3. Open the site from a browser that is signed out of GitHub, and check both pages and
   the button. Until the first release is published, the button's link answers 404.

## Testing a draft

Official means tested in game, so every draft is installed and tried before it is
published, in a Windows account that has never run BartleBot. The steps are
`docs/RELEASE_CHECKLIST.md`. This is why they are what they are.

**The account.** `tools/release_test_user.ps1` makes a standard account, `BartleTest`, and
makes it again from nothing whenever the checklist needs a fresh one. Everything an install
and the app write is per user (`docs/INSTALLER.md`, *What installs where*), and all of it
is in the profile the script deletes. From an elevated PowerShell, with the account signed
out:

```powershell
powershell -ExecutionPolicy Bypass -File tools\release_test_user.ps1 -Version 1.2.0 -Previous 1.1.0
powershell -ExecutionPolicy Bypass -File tools\release_test_user.ps1 -Remove
```

It copies the checklist into `C:\Users\Public\BartleBot\<version>`, beside the draft, with
the versions filled in, and puts a shortcut to it on the account's desktop. The copy is
outside the account, so its ticks survive the reset partway through, and a rerun keeps it.
`-Previous` is left out for the first release, and `-WhatIf` says what it would do.

**Why it is a fair test:**

- It has no Python. The python.org installer and uv both install into the user's own
  profile, so a DLL the build left out fails here, rather than being found on the
  maintainer's `PATH`.
- It is a standard user, so an install that needed elevation would stop and ask for an
  administrator's password.
- It shares the game, under `C:\ProgramData\KingsIsle Entertainment`, where Everyone has
  full control, so it launches and patches the same copy.

It cannot show what is machine-wide: the Windows version, the graphics drivers, the Visual
C++ runtimes, the fonts. Before the first public release, and again before 1.0.0, try one
draft on a machine that has never had a development environment, such as a VM or another
PC.

**The draft's assets** need a GitHub sign-in, public repo or not, since only the org's
members can see a draft. So the checklist fetches them with `gh` from the maintainer's
account into `C:\Users\Public`, which every account can read. A file fetched that way
carries no Mark of the Web, so SmartScreen says nothing about it. To see what a user sees,
the checklist gives Setup.exe the mark a browser would: a `Zone.Identifier` stream naming
the Internet zone (`ZoneId=3`), which is what SmartScreen reads. Once a release is
published, the first install from it is a real browser download, which shows whether the
two agree. An update carries no mark at all, since the app downloads it itself rather than
a browser, which is why the install page says Windows does not warn about one. Local feeds have
borne that out, and the checklist's last section confirms it for an update fetched from
GitHub.

**What is tried in game** is every shipped item the release changed since the previous
one: each Official bot, template, subgraph and strategy whose file differs from the
previous tag's, run to its end. The first release has no previous one, so it tries every
shipped item. What ships is Official, and Official means tested in game, so an item that
fails is put back as it was, or the draft waits for its fix.

**The draft is reported to Microsoft** once it has passed, before it is published:
Setup.exe and the app's own exe go to Microsoft's false-positive submission page
(<https://www.microsoft.com/en-us/wdsi/filesubmission>), as software, saying it is an
unsigned open-source app that reads a game's memory. Publishing does not wait for the
verdict, which can take days: the submission is what clears a detection on a release users
already have. Users are never told to add an exclusion, so a detection is cleared this way
or not at all.

**Velopack's advisories are read before tagging**, on its security page
(<https://github.com/velopack/velopack/security/advisories>). Squirrel.Windows, which
Velopack replaced, had a DLL search-path flaw (CVE-2022-46330), and an installer framework
runs on every user's machine. An advisory that affects the pinned version means a bump
first, and a bump means the install, update and uninstall in `docs/INSTALLER.md` again.

**The update is tried both ways.** Updating the previous release to the draft runs the
previous release's update code, which has already shipped. The draft's own runs only when
the next release is offered to it. So for the first release, and whenever a draft changes
the update code or the Velopack pin, the checklist also updates *from* the draft, to a
version packed locally.

**The site's button is tried once the release is published.** It is the one way in most
users take, and it fetches whatever GitHub calls the latest release, so it is the check
that publishing made this one the latest. Comparing the download's hash with the draft's
Setup.exe says it fetched this version rather than the one before.

**A step that fails** leaves the draft unpublished: fix the problem, and release a new
version.

## The signing key

Every feed is signed with one Ed25519 key, so an installed app can tell a feed CI built
from one swapped in afterwards (`docs/INSTALLER.md`, *The signature*). Its public half is
in `bartlebot/updates/keys.py`'s `TRUSTED_KEYS`. Its private half is the
`FEED_SIGNING_KEY` Actions secret, which only `release.yml`'s `sign` job reads, and a
maintainer's backup. **It never goes in the repo, a log, an issue or a plan page.**

**Where the secret is.** In the `release` environment, which only `v*` tags may deploy
to, and which the `sign` job names (`environment: release`). A push to a branch or a pull
request cannot reach it, even one that edits the workflow. Whoever owns the org owns the
key that way, so handing the project over needs nothing beyond the org. Until the repo was
public it was a repository secret, since GitHub Free gives a private repository no
environments; it moved when the repo went public, with the first beta release (#240):

```bash
gh api -X PUT repos/BartleWorks/BartleBot/environments/release \
  -F "deployment_branch_policy[protected_branches]=false" \
  -F "deployment_branch_policy[custom_branch_policies]=true"
gh api -X POST repos/BartleWorks/BartleBot/environments/release/deployment-branch-policies \
  -f name="v*" -f type=tag
gh secret set FEED_SIGNING_KEY --env release < <the backed-up key file>
gh secret delete FEED_SIGNING_KEY
```

**The key is backed up before the first release**, somewhere outside GitHub that its
owner controls, such as a password manager. A secret cannot be read back out of GitHub,
so without a backup a lost org is a lost key. Losing the key strands every installed copy
on the version it has: none of them will accept a feed signed by a new key until a release
they already trust tells them to. A user stranded that way installs the next version from
its Setup.exe, which is not checked against the feed.

**To make a key**, once, or to replace one:

```bash
uv run python tools/sign_feed.py generate <a file outside the repo>
```

It writes the private key, the base64 of its 32-byte seed, to that file, refuses to
overwrite one, and prints the public key. Back the file up, set it as the secret
(`gh secret set FEED_SIGNING_KEY < <file>`), and delete the file.

**To replace a key**, without stranding anyone:

1. Make the new key and back it up.
2. Add its public key to `TRUSTED_KEYS`, after the old one, and release as usual. That
   release is still signed by the old key, so every installed copy takes it, and with it
   the new list.
3. Once that release is out, set the secret to the new key. The next release is signed
   by it, and every copy that took step 2's release accepts it.
4. A later release drops the old key from `TRUSTED_KEYS`.

An app reads only the latest release's feed, so a copy that has not taken step 2's
release by the time step 3's is out sees a feed signed by a key it does not trust, and is
offered nothing from then on. Its user installs the latest Setup.exe over it, which keeps
their data. So leave step 2's release out for a while, a release or more, before step 3.
