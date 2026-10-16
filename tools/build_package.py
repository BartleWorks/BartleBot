"""Build a packaged BartleBot with PyInstaller, check it with ``--self-check``, and report what it cost.

    uv run --group package python tools/build_package.py

A PyInstaller onedir build of the files in the lists below. It lands in
``dist/BartleBot/``, with ``dist/build-report.json`` beside it: the build time, the
folder's size and file count, and the self-check's lines. The exit code is non-zero when
PyInstaller or the self-check fails. ``docs/RELEASING.md`` has why each list holds what
it does, and why PyInstaller rather than Nuitka.

A release is never built this way on a maintainer's machine: a local build embeds local
paths in the binaries. CI runs this script (``.github/workflows/package.yml``).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "bartlebot.py"
NAME = "BartleBot"

#: Every folder the app reads at run time, at the same path under the build's root as
#: under the project root, since ``bartlebot.paths.project_root`` is that root in a build.
DATA_DIRS = (
    "bots",
    "templates",
    "subgraphs",
    "strategies",
    "bartlebot/game/data",
    "bartlebot/gui/icons",
)
DATA_FILES = ("bartlebot/gui/logo/bartlebot.svg",)
#: Distributions whose metadata the app reads: ``importlib.metadata.version("bartlebot")``.
METADATA = ("bartlebot",)
#: Packages PyInstaller's import scan misses whole. katsuba's ``op`` and ``wad`` are
#: submodules its native extension registers when it loads, so no file names them.
WHOLE_PACKAGES = ("katsuba",)


def pyinstaller_command(dist: Path, work: Path) -> tuple[list[str], Path]:
    """The onedir build, and the exe it makes. Data lands beside ``bartlebot/`` in ``_internal``."""
    sep = os.pathsep
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(ENTRY),
        "--name",
        NAME,
        "--onedir",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(work),
    ]
    for folder in DATA_DIRS:
        command += ["--add-data", f"{ROOT / folder}{sep}{folder}"]
    for file in DATA_FILES:
        command += ["--add-data", f"{ROOT / file}{sep}{Path(file).parent.as_posix()}"]
    for dist_name in METADATA:
        command += ["--copy-metadata", dist_name]
    for package in WHOLE_PACKAGES:
        command += ["--collect-all", package]
    return command, dist / NAME / f"{NAME}.exe"


#: Files an installer leaves in a ``.dist-info`` that say where and how it installed:
#: ``direct_url.json`` names the checkout's path, since the project is an editable install.
#: The app reads only ``METADATA``, so these never ship.
INSTALL_RECORDS = ("direct_url.json", "uv_build.json", "uv_cache.json", "INSTALLER", "REQUESTED", "RECORD")


def strip_install_records(folder: Path) -> list[Path]:
    """Remove :data:`INSTALL_RECORDS` from every ``.dist-info`` under ``folder``, and return what went."""
    removed = [p for p in folder.rglob("*.dist-info/*") if p.name in INSTALL_RECORDS and p.is_file()]
    for path in removed:
        path.unlink()
    return removed


def folder_size(folder: Path) -> tuple[int, int]:
    """The total bytes and the number of files under ``folder``."""
    files = [p for p in folder.rglob("*") if p.is_file()]
    return sum(p.stat().st_size for p in files), len(files)


def self_check(exe: Path) -> subprocess.CompletedProcess:
    """Run the build's ``--self-check`` from outside the project, so nothing in the checkout can stand in."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BARTLEBOT_DEV")}
    return subprocess.run(
        [str(exe), "--self-check"], capture_output=True, text=True, timeout=600, cwd=exe.parent, env=env
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="where the build goes")
    args = parser.parse_args(argv)

    dist = args.out
    work = ROOT / "build" / "pyinstaller"
    for folder in (dist / NAME, work):
        shutil.rmtree(folder, ignore_errors=True)
    command, exe = pyinstaller_command(dist, work)

    started = time.perf_counter()
    built = subprocess.run(command, cwd=ROOT)
    seconds = time.perf_counter() - started
    if built.returncode != 0 or not exe.is_file():
        print(f"PyInstaller failed (exit {built.returncode}); no {exe}", file=sys.stderr)
        return built.returncode or 1

    strip_install_records(exe.parent)
    size, files = folder_size(exe.parent)
    checked = self_check(exe)
    report = {
        "packager": "pyinstaller",
        "exe": exe.relative_to(dist).as_posix(),
        "build_seconds": round(seconds),
        "folder_bytes": size,
        "folder_files": files,
        "self_check_exit": checked.returncode,
        "self_check": checked.stdout.splitlines(),
        "self_check_stderr": checked.stderr.splitlines()[-40:],
    }
    (dist / "build-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(checked.stdout, end="")
    if checked.stderr:
        print(checked.stderr, end="", file=sys.stderr)
    print(
        f"PyInstaller: {seconds / 60:.1f} min, {size / 1e6:.0f} MB in {files} files, self-check exit {checked.returncode}"
    )
    return 0 if checked.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
