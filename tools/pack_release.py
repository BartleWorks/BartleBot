"""Pack a build with Velopack: Setup.exe, the full package, a delta, and the feed.

    uv run python tools/pack_release.py 1.2.0 --vpk <path to vpk>

The release workflow runs this after ``tools/build_package.py`` and ``vpk download github``
(``.github/workflows/release.yml``). It packs ``dist/BartleBot/`` into ``releases/``. When
the previous release's full package is already there, which is what the download puts
there, ``vpk pack`` writes a delta against it as well. ``releases.win.json`` is the feed
an installed app reads to find updates.

The release notes are ``release_notes/<version>.md`` followed by the README's install
page, with its links made absolute at the release's tag. ``vpk pack`` puts them in the
package, the feed carries them to the update popup, and ``vpk upload github`` makes them
the release's description, which is the page someone arriving to install it reads. The
popup leaves the install page out (``bartlebot.updates.feed.INSTALL_NOTES_MARKER``).

A release is never packed on a maintainer's machine, for the reason a release is never
built on one (``docs/RELEASING.md``). Packing a local build into a scratch folder, to try
an install or an update, is what this is for outside CI.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bartlebot.shared.vendor import PRODUCT, VENDOR  # noqa: E402
from bartlebot.updates.feed import INSTALL_NOTES_MARKER, REPOSITORY  # noqa: E402
from tools.build_package import NAME  # noqa: E402

#: The Velopack version, for ``vpk`` and the ``velopack`` package alike: the one the
#: Stage 1 spike tried. ``release.yml`` installs ``vpk`` at this version, and
#: ``pyproject.toml`` pins the package to it.
VELOPACK_VERSION = "1.2.161"
#: Velopack's id for the app: the install folder is ``%LocalAppData%\<id>``, and an update
#: is matched to an install by it, so it never changes.
PACK_ID = PRODUCT
#: A Desktop shortcut and the Start Menu entry, so the app is found without knowing where
#: ``%LocalAppData%`` is. This is ``vpk pack``'s own default, spelled out so it never drifts.
SHORTCUTS = "Desktop,StartMenuRoot"
#: The splash's progress bar: the logo's own leaf green, where Velopack's default is #00FF00.
SPLASH_PROGRESS_COLOR = "#22b14c"
DEFAULT_BUILD = ROOT / "dist"
DEFAULT_OUT = ROOT / "releases"
#: One file per version, ``<version>.md``, written before the version is tagged.
NOTES_DIR = ROOT / "release_notes"
README = ROOT / "README.md"
#: The README's install page lies between these two lines, and is copied into every
#: release's notes.
INSTALL_START = "<!-- install:start -->"
INSTALL_END = "<!-- install:end -->"
#: Where the composed notes are written, in the build folder, for ``vpk pack`` to read.
NOTES_NAME = "release-notes.md"

#: A markdown link or image whose target is relative to the README: neither ``http(s):``
#: nor ``mailto:``.
_RELATIVE_LINK = re.compile(r"(!?)\[([^\]]*)\]\((?!https?://|mailto:)([^)\s]+)\)")


def notes_path(version: str, notes_dir: Path = NOTES_DIR) -> Path:
    """The file holding ``version``'s own notes."""
    return notes_dir / f"{version}.md"


def install_page(readme: str) -> str:
    """The README's install page: the lines between :data:`INSTALL_START` and :data:`INSTALL_END`."""
    start, end = readme.find(INSTALL_START), readme.find(INSTALL_END)
    if start < 0 or end < start:
        raise ValueError(f"README.md has no install page between {INSTALL_START} and {INSTALL_END}")
    return readme[start + len(INSTALL_START) : end].strip()


def absolute_links(markdown: str, tag: str) -> str:
    """``markdown`` with every README-relative link pointing at the repository at ``tag``.

    The notes are read on the release's page and in the popup, where a path relative to the
    README leads nowhere. An image needs the file itself (``raw``), a link the page about it
    (``blob``), and a bare ``#anchor`` is in the README. Pinning to the tag keeps an old
    release's notes showing what that release shipped.
    """

    def absolute(match: re.Match[str]) -> str:
        image, text, target = match.groups()
        if target.startswith("#"):
            target = f"README.md{target}"
        return f"{image}[{text}]({REPOSITORY}/{'raw' if image else 'blob'}/{tag}/{target})"

    return _RELATIVE_LINK.sub(absolute, markdown)


def release_notes(version: str, notes_dir: Path = NOTES_DIR, readme: Path = README) -> str:
    """``version``'s notes, then :data:`INSTALL_NOTES_MARKER`, then the README's install page."""
    own = notes_path(version, notes_dir).read_text(encoding="utf-8").strip()
    page = install_page(readme.read_text(encoding="utf-8"))
    return absolute_links(f"{own}\n\n{INSTALL_NOTES_MARKER}\n\n{page}\n", f"v{version}")


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
        notes = release_notes(args.version, NOTES_DIR, README)
    except (OSError, ValueError) as exc:
        print(f"no release notes: {exc}", file=sys.stderr)
        return 1
    (args.build / NOTES_NAME).write_text(notes, encoding="utf-8")
    return subprocess.run(command).returncode


if __name__ == "__main__":
    sys.exit(main())
