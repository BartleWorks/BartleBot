"""Regenerate bartlebot/data/spell_names.json from the game's Root.wad.

SpellTemplate's internal name -- what ``deck_io.py`` stores and keys decks by --
is a stable identity but not what the player sees in-game: tiered/fusion variants
like "Novus Storm - T05 - A" and spells the game has since renamed (e.g. the card
stored as "Soul Burner" now displays as "Ammut") both print their internal name
verbatim unless something resolves it. This snapshot is that something.

WHERE THE DATA COMES FROM
    Every spell is its own binary ("BINd") file under ``Spells/`` in Root.wad,
    named after its internal name exactly: ``Spells/Tiered Spells/Novus Storm -
    T05 - A.xml`` decodes to the template whose ``name()`` is "Novus Storm - T05
    - A". So the WAD path is the join key -- no deserializer needed to find
    *which* template a file is, only to pull one field out of it.

    That field is the display-name langcode (e.g. "Spells_00003940"), which
    ``get_langcode_name`` then resolves via ``Locale/en-US/Spells.lang`` -- the
    same two-step lookup ``CombatCard.display_name`` does against a live client
    (wizwalker/wizwalker/combat/card.py), just against the archive instead of
    memory. That's what makes this safe to precompute: nothing here depends on
    a connected client, so it works for decks belonging to characters that are
    not currently running.

    The field is pulled without a full wiztype deserializer: each BINd file
    encodes a string as a one-byte length (twice the character count) followed
    by that many ASCII bytes, so the langcode is found by searching for the
    literal b"Spells_" marker and reading the length byte immediately before it.
    Confirmed against ~18,000 files; the minority with no match (treasure
    cards, mob-only and housing/cantrip effects, pet templates with no player-
    facing name) are simply omitted -- callers fall back to the internal name
    for anything not in the snapshot, which is the correct answer for those too.

WHY A SNAPSHOT IS SAFE
    Same reasoning as tools/dump_name_tables.py: the app does not read Root.wad
    at runtime, both because that's a ~170k-entry archive and because it would
    require the game to be installed at a guessable path. A stale snapshot just
    means a spell added since the snapshot was taken shows its internal name
    until this is regenerated -- never a wrong name, since lookups are by exact
    key rather than by position.

USAGE
    uv run python tools/dump_spell_names.py
    uv run python tools/dump_spell_names.py --wad "D:/Games/Wizard101/Data/GameData/Root.wad"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path, PurePosixPath

from wizwalker.file_readers.cache_handler import CacheHandler
from wizwalker.file_readers.wad import Wad

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bartlebot.spell_names import NAMES_PATH  # noqa: E402

DEFAULT_WAD = Path(r"C:\ProgramData\KingsIsle Entertainment\Wizard101\Data\GameData\Root.wad")
SPELLS_PREFIX = "Spells/"
LANGCODE_MARKER = b"Spells_"
MAX_LANGCODE_LEN = 80


def extract_langcode(data: bytes) -> str | None:
    """Pull the display-name langcode out of one spell's BINd data.

    Each string field is a one-byte length (twice the character count) followed by
    that many raw bytes, so the byte immediately before a "Spells_" match gives its
    exact extent -- this avoids guessing where the token ends from content alone,
    which matters because some langcodes end in a word (e.g. "Spells_Fire Elf")
    rather than digits.
    """
    search_from = 0
    while True:
        idx = data.find(LANGCODE_MARKER, search_from)
        if idx == -1:
            return None
        search_from = idx + 1
        if idx == 0:
            continue
        length = data[idx - 1] // 2
        if not 0 < length <= MAX_LANGCODE_LEN or idx + length > len(data):
            continue
        candidate = data[idx : idx + length]
        if not all(32 <= b < 127 for b in candidate):
            continue
        try:
            text = candidate.decode("ascii")
        except UnicodeDecodeError:
            continue
        if text.startswith("Spells_"):
            return text


async def build_snapshot(wad_path: Path) -> dict:
    wad = Wad(str(wad_path))
    cache_handler = CacheHandler()

    spell_files = [n for n in await wad.names() if n.startswith(SPELLS_PREFIX) and n.endswith(".xml")]

    names: dict[str, str] = {}
    skipped = 0
    for path in spell_files:
        internal_name = PurePosixPath(path).stem
        data = await wad.get_file(path)
        langcode = extract_langcode(data)
        if langcode is None:
            skipped += 1
            continue
        try:
            display_name = await cache_handler.get_langcode_name(langcode)
        except ValueError:
            skipped += 1
            continue
        names[internal_name] = display_name

    return {
        "_comment": "Generated by tools/dump_spell_names.py -- do not hand-edit. See that file for how.",
        "generated": date.today().isoformat(),
        "source": str(wad_path),
        "skipped": skipped,
        "names": names,
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wad", type=Path, default=DEFAULT_WAD, help="path to Root.wad")
    ap.add_argument("--out", type=Path, default=NAMES_PATH, help="where to write the snapshot")
    args = ap.parse_args()

    if not args.wad.exists():
        print(f"Root.wad not found at {args.wad}; pass --wad")
        return 1

    snapshot = await build_snapshot(args.wad)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snapshot, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"wrote {args.out} ({args.out.stat().st_size // 1024} KB)")
    print(f"  {len(snapshot['names'])} resolved, {snapshot['skipped']} skipped (no player-facing name)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
