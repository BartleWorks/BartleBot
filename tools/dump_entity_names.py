"""Regenerate bartlebot/game/data/entity_names.json from the game files.

Run this when a game patch adds an NPC, mob or world object, or renames one. Everything
else reads the checked-in snapshot, so neither the app nor its tests need the game
installed.

WHAT IT READS
    Every object template under Root.wad's ObjectData/. A template's m_objectName is the
    internal name the client keeps in memory -- what WizWalker's ClientObject.object_name()
    returns, and what the Entity Browser lists -- while m_displayName is a langcode like
    NPCs_01747837, resolved through Locale/en-US/<file>.lang. The snapshot is the join:
    object name -> the text a player sees.

WHY NOT JUST CALL WIZWALKER
    ClientObject.display_name() already does this, but it resolves the langcode by opening
    Root.wad at run time -- a 173k-entry archive, per lookup, behind a hard dependency on
    the game sitting at a guessable path. tools/CLAUDE.md rules that out; a snapshot is the
    standing answer. Reading the langcode out of memory instead and looking it up here would
    also work, but it costs an extra memory read per entity and buys nothing: the object
    name the browser already has is the same key.

WHICH TEMPLATES
    GameObjectTemplate and its subclasses, minus the ItemTemplate branch. That is the
    family the client instantiates as a world entity -- mobs, NPCs, props, sigils,
    teleporters -- and it is what get_base_entity_list() hands back. The 76k WizItemTemplate
    entries under it are inventory, never in the entity list, and including them would
    quadruple the file for rows that can never be shown.

WHY ENGLISH ONLY
    Unlike zone_names.json or dialog_captions.json, nothing here is matched against text
    the client resolved into memory -- the key is an object name, which is the same in every
    locale. This snapshot only feeds a display column, so one locale is all it can use, and
    seven would cost about seven times the file for nothing.

USAGE
    uv run python tools/dump_entity_names.py              # about a minute: it reads every ObjectData template
    uv run python tools/dump_entity_names.py --verify     # no write; check the current file
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools" / "zone_graph"))

from wad_common import DEFAULT_TYPES, GAME_DATA, Deserializer, parse_lang, root_wad, text  # noqa: E402

from bartlebot.game.entity_names import ENTITY_NAMES_PATH  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT_CLASS = "class GameObjectTemplate"
_EXCLUDED_CLASS = "class ItemTemplate"
_LOCALE = "en-US"


def _ancestry(types: dict, cls: str) -> list[str]:
    """`cls` and every class it derives from, as the type dump spells them."""
    chain = [cls]
    while cls in types:
        bases = types[cls].get("bases") or []
        if not bases:
            break
        cls = bases[0] if bases[0].startswith("class ") else f"class {bases[0]}"
        chain.append(cls)
    return chain


def entity_template_hashes(types_path: Path = DEFAULT_TYPES) -> set[int]:
    """Type hashes of every world-entity template class, so items can be skipped."""
    types = json.loads(types_path.read_text(encoding="utf-8"))
    hashes = set()
    for cls, spec in types.items():
        if not isinstance(spec, dict) or "hash" not in spec:
            continue
        line = _ancestry(types, cls)
        if _ROOT_CLASS in line and _EXCLUDED_CLASS not in line:
            hashes.add(spec["hash"])
    return hashes


def build_snapshot(game_data: Path, types_path: Path = DEFAULT_TYPES) -> dict:
    root = root_wad(game_data)
    wanted = entity_template_hashes(types_path)
    deserialize = Deserializer()

    # Sorted, so the handful of object names used by two templates resolve the same way on
    # every run. Only two exist today, and one of the pair is a genuinely different name:
    # KT-TeleporterEntranceHall is both "To Entrance Hall" and "To Well of Spirits". There
    # is nothing in the template to break the tie with, so first-by-path is the rule, and
    # --verify prints them rather than letting the choice go unnoticed.
    codes: dict[str, str] = {}
    conflicts: dict[str, set[str]] = {}
    for path in sorted(root.iter_glob("ObjectData/**/*.xml")):
        try:
            template = deserialize(bytes(root[path]))
        except Exception:
            continue  # a template our type dump cannot read; it predates or postdates the dump
        if template.type_hash not in wanted:
            continue
        name, code = text(template.get("m_objectName")), text(template.get("m_displayName"))
        if not name or not code:
            continue
        if name in codes:
            if codes[name] != code:
                conflicts.setdefault(name, {codes[name]}).add(code)
            continue
        codes[name] = code

    tables: dict[str, dict[str, str]] = {}

    def resolve(code: str) -> str | None:
        stem, _, key = code.rpartition("_")
        if stem not in tables:
            try:
                tables[stem] = parse_lang(bytes(root[f"Locale/{_LOCALE}/{stem}.lang"]))
            except Exception:
                tables[stem] = {}
        return tables[stem].get(key)

    names = {name: display for name, code in codes.items() if (display := resolve(code))}
    return {
        "_comment": "Generated by tools/dump_entity_names.py -- do not hand-edit. See bartlebot/game/entity_names.py.",
        "generated": date.today().isoformat(),
        "locale": _LOCALE,
        "unresolved": sorted(set(codes) - set(names)),
        "shared_object_names": {name: sorted(seen) for name, seen in sorted(conflicts.items())},
        "names": dict(sorted(names.items())),
    }


def verify(snapshot: dict) -> int:
    """Report what the snapshot could not name. Returns an exit code.

    Never a failure on its own: plenty of templates have no player-facing name, and the
    browser falls back to the object name. This is here so a *jump* in the unresolved count
    after a patch is visible, rather than names quietly disappearing from the column.
    """
    names, unresolved = snapshot.get("names", {}), snapshot.get("unresolved", [])
    shared = snapshot.get("shared_object_names", {})
    print(f"  {len(names)} named, {len(unresolved)} with a langcode no .lang file resolves")
    for name, codes in shared.items():
        print(f"  ?? {name}: two templates, {', '.join(codes)} -- kept {names.get(name)!r}")
    return 0 if names else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--game-data", type=Path, default=GAME_DATA, help="the Wizard101 Data/GameData directory")
    ap.add_argument("--types", type=Path, default=DEFAULT_TYPES, help="the wiztype dump to read classes from")
    ap.add_argument("--out", type=Path, default=ENTITY_NAMES_PATH, help="where to write the snapshot")
    ap.add_argument("--verify", action="store_true", help="check the existing snapshot; do not write")
    args = ap.parse_args()

    if args.verify:
        snapshot = json.loads(args.out.read_text(encoding="utf-8"))
    else:
        snapshot = build_snapshot(args.game_data, args.types)
        args.out.write_text(json.dumps(snapshot, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {args.out}: {len(snapshot['names'])} entity names")
    code = verify(snapshot)
    print("ok" if code == 0 else "problems found")
    return code


if __name__ == "__main__":
    sys.exit(main())
