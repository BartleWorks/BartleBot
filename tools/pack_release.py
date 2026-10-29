"""Pack a build with Velopack: Setup.exe, the full package, a delta, and the feed.

    uv run python tools/pack_release.py 1.2.0 --vpk <path to vpk>

The release workflow runs this after ``tools/build_package.py`` and ``vpk download github``
(``.github/workflows/release.yml``). It packs ``dist/BartleBot/`` into ``releases/``. When
the previous release's full package is already there, which is what the download puts
there, ``vpk pack`` writes a delta against it as well. ``releases.win.json`` is the feed
an installed app reads to find updates.

The release notes are ``release_notes/<version>.md``, with its links made absolute at the
release's tag, followed by a short footer naming the file to run and linking the download
site's install page. ``vpk pack`` puts them in the package, the feed carries them to the
update popup, and ``vpk upload github`` makes them the release's description. The popup
leaves the footer out (``bartlebot.updates.feed.INSTALL_NOTES_MARKER``).

A release is never packed on a maintainer's machine, for the reason a release is never
built on one (``docs/RELEASING.md``). Packing a local build into a scratch folder, to try
an install or an update, is what this is for outside CI.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bartleby.shared.vendor import VENDOR  # noqa: E402

from bartlebot.packaging.product import NAME as PRODUCT  # noqa: E402
from bartlebot.updates.feed import INSTALL_NOTES_MARKER, REPOSITORY  # noqa: E402
from tools.build_package import NAME  # noqa: E402

#: The Velopack version, for ``vpk`` and the ``velopack`` package alike: the one the
#: Stage 1 spike tried. ``release.yml`` installs ``vpk`` at this version, and
#: ``pyproject.toml`` pins the package to it.
VELOPACK_VERSION = "1.2.161"
#: Velopack's id for the app: the install folder is ``%LocalAppData%\<id>``, and an update
#: is matched to an install by it, so it never changes.
PACK_ID = PRODUCT
#: The installer ``vpk pack`` writes, named for the pack id. The download site's button
#: links to it in whichever release is the latest (``site/zensical.toml``'s ``download_url``).
SETUP_NAME = f"{PACK_ID}-win-Setup.exe"
#: A Desktop shortcut and the Start Menu entry, so the app is found without knowing where
#: ``%LocalAppData%`` is. This is ``vpk pack``'s own default, spelled out so it never drifts.
SHORTCUTS = "Desktop,StartMenuRoot"
#: The splash's progress bar: the logo's own leaf green, where Velopack's default is #00FF00.
SPLASH_PROGRESS_COLOR = "#22b14c"
DEFAULT_BUILD = ROOT / "dist"
DEFAULT_OUT = ROOT / "releases"
#: One file per version, ``<version>.md``, written before the version is tagged.
NOTES_DIR = ROOT / "release_notes"
#: The download site's install page, which every release's footer links. It covers
#: SmartScreen, Smart App Control and antivirus, so the release page does not repeat them.
INSTALL_URL = "https://bartleworks.github.io/BartleBot/install/"
#: What follows every release's own notes on its GitHub page, for someone who arrives there
#: to install it rather than through the site.
INSTALL_FOOTER = (
    f"**Installing:** download **{SETUP_NAME}** below and run it. Windows may warn about it "
    f"the first time; the [install guide]({INSTALL_URL}) shows what to do."
)
#: Where the composed notes are written, in the build folder, for ``vpk pack`` to read.
NOTES_NAME = "release-notes.md"

#: A markdown link or image whose target is relative to the file it is in: neither
#: ``http(s):`` nor ``mailto:``.
_RELATIVE_LINK = re.compile(r"(!?)\[([^\]]*)\]\((?!https?://|mailto:)([^)\s]+)\)")


def notes_path(version: str, notes_dir: Path = NOTES_DIR) -> Path:
    """The file holding ``version``'s own notes."""
    return notes_dir / f"{version}.md"


def absolute_links(markdown: str, tag: str, source: str) -> str:
    """``markdown`` with every relative link pointing at the repository at ``tag``.

    ``source`` is the file ``markdown`` came from, as a path in the repository, and its
    relative links are relative to it. The notes are read on the release's page and in the
    popup, where such a path leads nowhere. An image needs the file itself (``raw``), a link
    the page about it (``blob``), and a bare ``#anchor`` is in ``source``. Pinning to the
    tag keeps an old release's notes showing what that release shipped.
    """
    folder = posixpath.dirname(source)

    def absolute(match: re.Match[str]) -> str:
        image, text, target = match.groups()
        if target.startswith("#"):
            target = f"{source}{target}"
        else:
            target = posixpath.normpath(posixpath.join(folder, target))
        return f"{image}[{text}]({REPOSITORY}/{'raw' if image else 'blob'}/{tag}/{target})"

    return _RELATIVE_LINK.sub(absolute, markdown)


def release_notes(version: str, notes_dir: Path = NOTES_DIR) -> str:
    """``version``'s notes, then :data:`INSTALL_NOTES_MARKER`, then :data:`INSTALL_FOOTER`."""
    own = notes_path(version, notes_dir).read_text(encoding="utf-8").strip()
    own = absolute_links(own, f"v{version}", f"{NOTES_DIR.name}/{version}.md")
    return f"{own}\n\n{INSTALL_NOTES_MARKER}\n\n{INSTALL_FOOTER}\n"


def pack_command(version: str, vpk: str = "vpk", build: Path = DEFAULT_BUILD, out: Path = DEFAULT_OUT) -> list[str]:
    """The ``vpk pack`` command for ``build`` (the folder ``build_package.py`` wrote) at ``version``.

    The notes it names are :func:`release_notes`, which :func:`main` writes into ``build``.
    """
    return [
        vpk,
        "pack",
        "--packId",
        PACK_ID,
        "--packVersion",
        version,
        "--packDir",
        str(build / NAME),
        "--mainExe",
        f"{NAME}.exe",
        "--packTitle",
        PRODUCT,
        "--packAuthors",
        VENDOR,
        "--icon",
        str(build / "bartlebot.ico"),
        # Shown while Setup.exe installs. Without one, Velopack shows a plain progress
        # dialog instead.
        "--splashImage",
        str(build / "bartlebot-splash.png"),
        "--splashProgressColor",
        SPLASH_PROGRESS_COLOR,
        "--shortcuts",
        SHORTCUTS,
        # A portable zip runs from wherever it is unpacked, outside the install and the
        # updater, so it is not made (the plan's Out of scope).
        "--noPortable",
        "--releaseNotes",
        str(build / NOTES_NAME),
        "--outputDir",
        str(out),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="the version to pack, as tools/release_version.py printed it")
    parser.add_argument("--vpk", default="vpk", help="the vpk executable")
    parser.add_argument("--build", type=Path, default=DEFAULT_BUILD, help="the folder build_package.py wrote")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where the packages and the feed go")
    args = parser.parse_args(argv)

    command = pack_command(args.version, args.vpk, args.build, args.out)
    for flag in ("--packDir", "--icon", "--splashImage"):
        required = Path(command[command.index(flag) + 1])
        if not required.exists():
            print(f"{required} is missing: run tools/build_package.py first", file=sys.stderr)
            return 1
    try:
        notes = release_notes(args.version, NOTES_DIR)
    except OSError as exc:
        print(f"no release notes: {exc}", file=sys.stderr)
        return 1
    (args.build / NOTES_NAME).write_text(notes, encoding="utf-8")
    return subprocess.run(command).returncode


if __name__ == "__main__":
    sys.exit(main())
