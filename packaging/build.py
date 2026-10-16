"""Build a packaged BartleBot, check it with ``--self-check``, and report what it cost.

    uv run --group package python packaging/build.py pyinstaller
    uv run --group package python packaging/build.py nuitka
    uv run --group package python packaging/build.py nuitka -- --zig   # without Visual Studio 2022

Both packagers bundle the same files, from the lists below, so their builds differ only
in how they freeze Python. The build lands in ``dist/<packager>/``, with
``build-report.json`` beside it: the build time, the folder's size and file count, and
the self-check's lines. The exit code is non-zero when the packager or the self-check
fails. ``docs/RELEASING.md`` has why each list holds what it does.

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
#: Packages a packager's import scan misses whole. katsuba's ``op`` and ``wad`` are
#: submodules its native extension registers when it loads, so no file names them.
WHOLE_PACKAGES = ("katsuba",)


def pyinstaller_command(dist: Path, work: Path) -> tuple[list[str], Path]:
    """PyInstaller's onedir build, and the exe it makes. Data lands beside ``bartlebot/`` in ``_internal``."""
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


def nuitka_command(dist: Path, _work: Path) -> tuple[list[str], Path]:
    """Nuitka's standalone build, and the exe it makes. Data lands beside ``bartlebot/`` in the ``.dist`` folder."""
    command = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--enable-plugin=pyqt6",
        # A console when started from one, so --self-check prints; none when double-clicked.
        "--windows-console-mode=attach",
        "--assume-yes-for-downloads",
        f"--output-dir={dist}",
        f"--output-filename={NAME}.exe",
        "--remove-output",
        # The test and build tooling in the environment, which the app never imports.
        "--nofollow-import-to=pytest,_pytest,pytestqt,coverage,nuitka,PyInstaller,ruff",
    ]
    for folder in DATA_DIRS:
        command.append(f"--include-data-dir={ROOT / folder}={folder}")
    for file in DATA_FILES:
        command.append(f"--include-data-files={ROOT / file}={file}")
    for dist_name in METADATA:
        command.append(f"--include-distribution-metadata={dist_name}")
    for package in WHOLE_PACKAGES:
        command.append(f"--include-package={package}")
    command.append(str(ENTRY))
    return command, dist / f"{ENTRY.stem}.dist" / f"{NAME}.exe"


PACKAGERS = {"pyinstaller": pyinstaller_command, "nuitka": nuitka_command}


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
    parser.add_argument("packager", choices=sorted(PACKAGERS))
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="where dist/<packager>/ goes")
    parser.add_argument("extra", nargs="*", help="more options for the packager, after --: nuitka -- --zig")
    args = parser.parse_args(argv)

    dist = args.out / args.packager
    work = ROOT / "build" / args.packager
    for folder in (dist, work):
        shutil.rmtree(folder, ignore_errors=True)
    command, exe = PACKAGERS[args.packager](dist, work)
    # Before Nuitka's script argument, which comes last; anywhere for PyInstaller.
    at = len(command) - 1 if command[-1] == str(ENTRY) else len(command)
    command[at:at] = args.extra

    started = time.perf_counter()
    built = subprocess.run(command, cwd=ROOT)
    seconds = time.perf_counter() - started
    if built.returncode != 0 or not exe.is_file():
        print(f"{args.packager} failed (exit {built.returncode}); no {exe}", file=sys.stderr)
        return built.returncode or 1

    size, files = folder_size(exe.parent)
    checked = self_check(exe)
    report = {
        "packager": args.packager,
        "exe": exe.relative_to(args.out).as_posix(),
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
        f"{args.packager}: {seconds / 60:.1f} min, {size / 1e6:.0f} MB in {files} files, self-check exit {checked.returncode}"
    )
    return 0 if checked.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
