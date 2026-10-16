"""Pack a build with Velopack: Setup.exe, the full package, a delta, and the feed.

    uv run python tools/pack_release.py 1.2.0 --vpk <path to vpk>

The release workflow runs this after ``tools/build_package.py`` and ``vpk download github``
(``.github/workflows/release.yml``). It packs ``dist/BartleBot/`` into ``releases/``. When
the previous release's full package is already there, which is what the download puts
there, ``vpk pack`` writes a delta against it as well. ``releases.win.json`` is the feed
an installed app reads to find updates.

A release is never packed on a maintainer's machine, for the reason a release is never
built on one (``docs/RELEASING.md``). Packing a local build into a scratch folder, to try
an install or an update, is what this is for outside CI.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bartlebot.shared.vendor import PRODUCT, VENDOR  # noqa: E402
from tools.build_package import NAME  # noqa: E402

#: The Velopack version, for ``vpk`` and the ``velopack`` package alike: the one the
#: Stage 1 spike tried. ``release.yml`` installs ``vpk`` at this version, and
#: ``pyproject.toml`` pins the package to it.
VELOPACK_VERSION = "1.2.161"
#: Velopack's id for the app: the install folder is ``%LocalAppData%\<id>``, and an update
#: is matched to an install by it, so it never changes.
PACK_ID = PRODUCT
#: The Start Menu entry and nothing else. ``vpk pack``'s default adds a Desktop shortcut.
SHORTCUTS = "StartMenuRoot"
DEFAULT_BUILD = ROOT / "dist"
DEFAULT_OUT = ROOT / "releases"


def pack_command(version: str, vpk: str = "vpk", build: Path = DEFAULT_BUILD, out: Path = DEFAULT_OUT) -> list[str]:
    """The ``vpk pack`` command for ``build`` (the folder ``build_package.py`` wrote) at ``version``."""
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
        "--shortcuts",
        SHORTCUTS,
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
    for required in (Path(command[command.index("--packDir") + 1]), Path(command[command.index("--icon") + 1])):
        if not required.exists():
            print(f"{required} is missing: run tools/build_package.py first", file=sys.stderr)
            return 1
    return subprocess.run(command).returncode


if __name__ == "__main__":
    sys.exit(main())
