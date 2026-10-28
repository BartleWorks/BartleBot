"""Regenerate bartlebot/game/data/spell_names.json from the game's Root.wad.

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
    *which* template a file is.

    A deserializer *is* needed to find the right *field*, though.
    ``katsuba`` (the same library the wizdb/Kimerith-WindHammer toolchain uses,
    and already used by ``wizwalker/examples/read_deck.py`` in our fork) reads
    a spell's BINd bytes against the newest wiztype dump in ``bartlebot/game/data/`` -- the
    one ``typedump.py`` reads -- and returns a real object with
    ``m_displayName``, the exact field ``CombatCard.display_name`` reads from a
    live client (wizwalker/wizwalker/combat/card.py). That value is a langcode
    (e.g. "Spells_00003940"): the record "00003940" of ``Locale/en-US/Spells.lang``,
    read out of the same archive.

    The text comes from that archive too, never from wizwalker's ``CacheHandler``.
    That reads whichever install wizwalker finds rather than ``--wad``, so a run on
    the Test realm paired its spells with live names, and its own wad reader took
    over nine minutes where ``katsuba`` takes seconds.

    An earlier version of this tool skipped the deserializer: it searched the
    raw bytes for the first literal ``b"Spells_"`` and read the length byte
    immediately before it, assuming that would be ``m_displayName``. It usually
    was, but field order in the serialized bytes is not fixed -- for
    "MonsterMash_Trainable - T02 - B", ``m_description``'s langcode sits at byte
    offset 74 and the real ``m_displayName``'s at offset 107, so the byte scan
    silently returned the description's template text (things like
    "$eA$ $dT_image$$Damage_image$...") as the "name" for 1457 spells, and missed
    2332 more that it happened not to match at all. Reading the named field
    directly does not have this failure mode.

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
    uv run python tools/dump_spell_names.py --types tools/r800294_WizardDev.json   # an older build's types
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path, PurePosixPath

from katsuba.op import STATEFUL_FLAGS, Serializer, SerializerOptions, TypeList
from katsuba.wad import Archive

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bartlebot.game.art_extraction import TYPE_DUMP_DIR, newest_type_dump  # noqa: E402
from bartlebot.game.spell_names import NAMES_PATH  # noqa: E402
from tools.dump_world_names import parse_lang  # noqa: E402

DEFAULT_WAD = Path(r"C:\ProgramData\KingsIsle Entertainment\Wizard101\Data\GameData\Root.wad")
# The newest dump, from game/data/ where the app ships it or tools/ where older ones stay.
DEFAULT_TYPES = newest_type_dump(TYPE_DUMP_DIR, Path(__file__).resolve().parent)
SPELLS_GLOB = "Spells/**/*.xml"


def _decode(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _build_serializer(types_path: Path) -> Serializer:
    types = TypeList.open(str(types_path))
    opts = SerializerOptions()
    opts.flags = STATEFUL_FLAGS
    opts.shallow = False
    opts.skip_unknown_types = True
    return Serializer(opts, types)


def _lang_table(archive: Archive, tables: dict[str, dict[str, str]], stem: str) -> dict[str, str]:
    """``Locale/en-US/<stem>.lang`` as {record: text}, read once; empty when the archive has none."""
    if stem not in tables:
        path = f"Locale/en-US/{stem}.lang"
        tables[stem] = parse_lang(bytes(archive[path])) if path in set(archive.iter_glob(path)) else {}
    return tables[stem]


def build_snapshot(wad_path: Path, types_path: Path) -> dict:
    archive = Archive.mmap(str(wad_path))
    ser = _build_serializer(types_path)
    tables: dict[str, dict[str, str]] = {}

    names: dict[str, str] = {}
    skipped = 0
    errors = 0
    for path in archive.iter_glob(SPELLS_GLOB):
        internal_name = PurePosixPath(path).stem
        try:
            data = archive[path]
            if data.startswith(b"BINd"):
                data = data[4:]
            obj = ser.deserialize(data)
            langcode = _decode(obj.get("m_displayName"))
        except Exception:
            errors += 1
            continue
        if not langcode:
            skipped += 1
            continue
        # "Spells_00003940" -> the "00003940" record of Spells.lang.
        stem, _, record = langcode.partition("_")
        display_name = _lang_table(archive, tables, stem).get(record)
        if not display_name:
            skipped += 1
            continue
        names[internal_name] = display_name

    return {
        "_comment": "Generated by tools/dump_spell_names.py -- do not hand-edit. See that file for how.",
        "generated": date.today().isoformat(),
        "source": str(wad_path),
        "skipped": skipped,
        "errors": errors,
        "names": names,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wad", type=Path, default=DEFAULT_WAD, help="path to Root.wad")
    ap.add_argument("--types", type=Path, default=DEFAULT_TYPES, help="path to a wiztype types JSON dump")
    ap.add_argument("--out", type=Path, default=NAMES_PATH, help="where to write the snapshot")
    args = ap.parse_args()

    if not args.wad.exists():
        print(f"Root.wad not found at {args.wad}; pass --wad")
        return 1
    if not args.types.exists():
        print(f"types JSON not found at {args.types}; pass --types")
        return 1

    snapshot = build_snapshot(args.wad, args.types)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snapshot, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"wrote {args.out} ({args.out.stat().st_size // 1024} KB)")
    print(
        f"  {len(snapshot['names'])} resolved, {snapshot['skipped']} skipped "
        f"(no player-facing name), {snapshot['errors']} errors"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
