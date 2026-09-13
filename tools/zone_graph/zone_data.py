"""Inspect one zone's own wad: its objects, arrival points, map markers and trigger strings.

Each zone ships as ``GameData/<zone path with / as ->.wad``. The useful entries:

    gamedata.bin   zone record: m_zoneDisplayName, m_locationList (named arrival points),
                   m_objectList (every placed object with position; gates are named for
                   where they lead, e.g. WC_GateCommons_ToUnicornWay)
    Compass.xml    map markers: WizardCompassLocs langcode + world position
    spawnData.xml  spawn points
    triggers.xml   teleport/quest triggers -- katsuba cannot read it with the current
    volumes.xml    type dump (unknown type tags), so use `strings`

USAGE
    uv run python tools/zone_graph/zone_data.py list WizardCity/WC_Hub
    uv run python tools/zone_graph/zone_data.py summary WizardCity/WC_Hub --tag "Gate|Teleport"
    uv run python tools/zone_graph/zone_data.py dump WizardCity/WC_Hub gamedata.bin --out hub.local.json
    uv run python tools/zone_graph/zone_data.py strings WizardCity/WC_Hub triggers.xml --grep Teleport
    uv run python tools/zone_graph/zone_data.py hubs
    uv run python tools/zone_graph/zone_data.py names "Colossus|Caliburn"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import zonemap_nav
from wad_common import (
    GAME_DATA,
    Deserializer,
    display_name,
    printable_strings,
    root_wad,
    text,
    to_plain,
    xyz,
    zone_display_names,
    zone_wad,
)


def zone_record(zone: str, deserialize: Deserializer, game_data: Path = GAME_DATA):
    return deserialize(bytes(zone_wad(zone, game_data)["gamedata.bin"]))


def locations(record) -> list[tuple[str, tuple[float, float, float]]]:
    return [(text(loc["m_locName"]), xyz(loc["m_location"])) for loc in (record["m_locationList"] or []) if loc]


def objects(record) -> list[tuple[str, tuple[float, float, float]]]:
    """(zone tag, position) for every placed object that has a tag."""
    out = []
    for obj in record["m_objectList"] or []:
        if obj and text(obj["m_zoneTag"]):
            out.append((text(obj["m_zoneTag"]), xyz(obj["m_location"])))
    return out


def _fmt(p: tuple[float, float, float]) -> str:
    return f"({p[0]:10.1f}, {p[1]:10.1f}, {p[2]:8.1f})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--game-data", type=Path, default=GAME_DATA, help="the Wizard101 Data/GameData directory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list", help="entries in the zone's wad")
    p_list.add_argument("zone")
    p_sum = sub.add_parser("summary", help="display name, arrival points and tagged objects")
    p_sum.add_argument("zone")
    p_sum.add_argument("--tag", help="only objects whose tag matches this regex")
    p_dump = sub.add_parser("dump", help="deserialize one wad entry to JSON")
    p_dump.add_argument("zone")
    p_dump.add_argument("entry", help="e.g. gamedata.bin, Compass.xml, spawnData.xml")
    p_dump.add_argument("--out", type=Path, help="write here instead of stdout")
    p_str = sub.add_parser("strings", help="printable strings of an entry katsuba cannot read")
    p_str.add_argument("zone")
    p_str.add_argument("entry")
    p_str.add_argument("--grep", help="only strings matching this regex")
    sub.add_parser("hubs", help="each world's hub and spiral-door arrival zone, from WorldHubZones.xml")
    p_names = sub.add_parser("names", help="English display name of every zone in zonemap.nav (reads every zone wad)")
    p_names.add_argument("pattern", nargs="?", help="only zones whose path or name matches this regex")
    args = ap.parse_args()

    if args.cmd == "list":
        wad = zone_wad(args.zone, args.game_data)
        for name in wad.iter_glob("*"):
            print(f"{len(wad[name]):10}  {name}")

    elif args.cmd == "summary":
        record = zone_record(args.zone, Deserializer(), args.game_data)
        names = zone_display_names(root_wad(args.game_data))
        key = text(record["m_zoneDisplayName"])
        print(f"{text(record['m_zoneName'])}  display={key} -> {display_name(record, names) or '?'}")
        print("\narrival points:")
        for name, pos in locations(record):
            print(f"  {_fmt(pos)}  {name}")
        rx = re.compile(args.tag, re.IGNORECASE) if args.tag else None
        print("\nobjects:")
        for tag, pos in objects(record):
            if rx is None or rx.search(tag):
                print(f"  {_fmt(pos)}  {tag}")

    elif args.cmd == "dump":
        data = bytes(zone_wad(args.zone, args.game_data)[args.entry])
        payload = json.dumps(to_plain(Deserializer()(data)), indent=1, ensure_ascii=False)
        if args.out:
            args.out.write_text(payload, encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            print(payload)

    elif args.cmd == "strings":
        data = bytes(zone_wad(args.zone, args.game_data)[args.entry])
        rx = re.compile(args.grep, re.IGNORECASE) if args.grep else None
        seen: set[str] = set()
        for s in printable_strings(data):
            if s not in seen and (rx is None or rx.search(s)):
                seen.add(s)
                print(s)

    elif args.cmd == "hubs":
        record = Deserializer()(bytes(root_wad(args.game_data)["WorldHubZones.xml"]))
        for hub in record["m_hubZoneMapping"]:
            print(
                f"{text(hub['m_world']):16} hub={text(hub['m_hubZone']):40} spiral door={text(hub['m_universeTPZone'])}"
            )

    elif args.cmd == "names":
        names = zone_display_names(root_wad(args.game_data))
        deserialize = Deserializer()
        rx = re.compile(args.pattern, re.IGNORECASE) if args.pattern else None
        for zone in zonemap_nav.load(args.game_data).names:
            try:
                name = display_name(zone_record(zone, deserialize, args.game_data), names)
            except (FileNotFoundError, KeyError):
                name = None  # no wad, or no gamedata.bin in it
            if rx is None or rx.search(zone) or (name and rx.search(name)):
                print(f"{zone:60}  {name or '?'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
