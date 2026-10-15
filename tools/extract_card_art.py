"""Extract spell card art from the installed game into the local art cache, from the command line.

The app does the same from setup's Card art page and Settings › General › Card art; this
is the command line over the one function all three call,
``bartlebot.game.art_extraction.extract_art``, which has the reasoning::

    uv run python tools/extract_card_art.py
    uv run python tools/extract_card_art.py --dry-run
    uv run python tools/extract_card_art.py --game-dir "D:/Games/Wizard101/Data/GameData"

``--game-dir`` defaults to the ``Data/GameData`` of the folder the app launches from
(``find_install``: the folder chosen in setup or Settings, the KingsIsle launcher's, or a
Steam library's), and ``--types`` to the newest type dump in ``tools/``. The output goes to
``%APPDATA%/Bartleby/BartleBot/art``, outside the repo, and is never tracked.

``--out`` is **replaced whole**, not written into: a run builds ``<out>.partial`` beside it
and swaps it in, deleting the folder that was there, so anything else in it goes too.
Point it only at a folder of its own.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wizwalker.utils import DEFAULT_INSTALL  # noqa: E402

from bartlebot.game.art_extraction import (  # noqa: E402
    ExtractionError,
    Progress,
    extract_art,
    game_data_dir,
    installed_game_data_dir,
    type_dump,
)
from bartlebot.game.card_art import INDEX_FILENAME, art_dir  # noqa: E402


def default_game_dir() -> Path:
    """The ``Data/GameData`` of the folder the app launches from, or of the launcher's when none is found."""
    return installed_game_data_dir() or game_data_dir(Path(DEFAULT_INSTALL))


def _printer():
    """A progress callback printing each step once, as it starts."""
    last: list[str] = []

    def show(progress: Progress) -> None:
        if not last or last[-1] != progress.step:
            last.append(progress.step)
            print(f"{progress.step}...")

    return show


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--game-dir", type=Path, default=None, help="the game's Data/GameData directory")
    ap.add_argument("--types", type=Path, default=None, help="path to a wiztype types JSON dump")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="the folder to replace with the cache, deleting what is in it (default: the art cache)",
    )
    ap.add_argument("--dry-run", action="store_true", help="report what would be written, write nothing")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    out_dir = args.out or art_dir()
    try:
        result = extract_art(
            args.game_dir or default_game_dir(),
            _printer(),
            types_path=args.types or type_dump(),
            out_dir=out_dir,
            dry_run=args.dry_run,
        )
    except ExtractionError as exc:
        print(f"{exc} Pass --game-dir or --types.")
        return 1

    print(f"{'would write' if args.dry_run else 'wrote'} {result.files} images for {result.templates} templates")
    print(f"  {result.icons} school, archmastery, world, badge, tag, drop and duel-slot icons")
    if result.size_bytes:
        print(f"  {result.size_bytes / 1e6:.1f} MB in {out_dir}")
    if result.missing:
        print(f"  {result.missing} templates name a texture the game files do not contain")
    if result.failed:
        print(f"  {result.failed} textures could not be converted")
    if not args.dry_run:
        print(f"  index: {out_dir / INDEX_FILENAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
