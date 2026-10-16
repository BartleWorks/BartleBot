"""Check that a release tag names the version the app reports, and print that version.

    uv run python tools/release_version.py v1.2.0

BartleBot has one version number, ``[project] version`` in ``pyproject.toml``. The app
reads it through ``importlib.metadata``, from the metadata ``uv sync`` writes and
``tools/build_package.py`` bundles, and Velopack is given it as the package version. So a
release tag must be that number with a ``v`` in front, the number must be SemVer 2 (which
Velopack requires), and the metadata in this environment must say the same, or the app
would report a version its release does not have.

On success it prints the version alone, for the release workflow to read, and exits 0.
Otherwise it prints every problem and exits 1 (``docs/RELEASING.md``, "The version").
"""

from __future__ import annotations

import argparse
import importlib.metadata
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
DIST = "bartlebot"

#: SemVer 2.0.0's own pattern (semver.org), without build metadata, which Velopack ignores.
SEMVER = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
)


def project_version(pyproject: Path = PYPROJECT) -> str:
    """The version ``pyproject.toml`` declares."""
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def installed_version() -> str | None:
    """The version this environment's metadata reports, the one the app and a build read."""
    try:
        return importlib.metadata.version(DIST)
    except importlib.metadata.PackageNotFoundError:
        return None


def problems(tag: str, project: str, installed: str | None) -> list[str]:
    """Why ``tag`` cannot be released, in words; empty when it can."""
    found = []
    if not SEMVER.fullmatch(project):
        found.append(f"pyproject.toml's version {project!r} is not SemVer 2 (MAJOR.MINOR.PATCH), which Velopack needs")
    if tag != f"v{project}":
        found.append(f"the tag {tag!r} is not 'v' + pyproject.toml's version {project!r}")
    if installed is None:
        found.append(f"no metadata for {DIST} in this environment: run uv sync")
    elif installed != project:
        found.append(f"this environment's metadata says {installed!r}, not {project!r}: run uv sync")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="the release tag, such as v1.2.0")
    args = parser.parse_args(argv)

    project = project_version()
    found = problems(args.tag, project, installed_version())
    for problem in found:
        print(problem, file=sys.stderr)
    if found:
        return 1
    print(project)
    return 0


if __name__ == "__main__":
    sys.exit(main())
