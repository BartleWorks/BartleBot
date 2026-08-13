"""Decode ClientWizPlayerNameBehavior.m_nameKeys into the wizard's name.

SOLVED (2026-08-12).  The wizard name is not stored as text anywhere in the client -- it
is three indices into the game's own name tables, packed into one uint32, resolved against
a dictionary at draw time.  That is why every plaintext memory scan for it failed.

The decoding itself lives in ``bartlebot/name_tables.py``, since it is production code
now; this is the command-line front end and the regression check against recorded samples.
See that module for the byte layout and why the checked-in table snapshot is safe.

VERIFIED against tools/name_key_samples.json.  The layout fell out of comparing characters
chosen to share different name parts -- every shared part showed up as a shared byte, which
is what identified one byte per name component:

    Luke  SkullHunter    56900648  [3, 100, 60, 40]
    Luke  CycloneHunter  56916008  [3, 100, 120, 40]
    Misha SkullSeer      64109768  [3, 210, 60, 200]

    Luke is 100 in both of the first two and Hunter is 40 in both; Skull is 60 in the first
    and third.  byte0 is 3 for male and 2 for female, so it selects the first-name table.

    Those samples are synthetic -- built from the shipped tables, not from anyone's account.
    See CLAUDE.md, "No real player data".

USAGE
    poetry run python tools/name_keys.py --verify           # check every recorded sample
    poetry run python tools/name_keys.py --decode 55397123
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bartlebot.name_tables import (  # noqa: E402
    DEFAULT_LOCALE,
    GENDER_MALE,
    decode_name_keys,
    load_name_tables,
    split_name_keys,
)

SAMPLES_PATH = Path(__file__).parent / "name_key_samples.json"


def cmd_verify(args) -> int:
    if not SAMPLES_PATH.exists():
        print(f"no samples at {SAMPLES_PATH}; record some with probe_name_behavior.py --record")
        return 1
    samples = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))
    tables = load_name_tables(args.locale)
    if tables is None:
        print("could not load name tables; regenerate with tools/dump_name_tables.py")
        return 1

    print(
        f"tables ({args.locale}): male={len(tables.first_male)} female={len(tables.first_female)} "
        f"middle={len(tables.middle)} last={len(tables.last)}\n"
    )
    failures = 0
    for sample in samples:
        expected = sample["wizard_name"]
        # Explicit None check: m_eGender is 0 for female, which `or` would coerce to male.
        gender = sample.get("m_eGender")
        gender = GENDER_MALE if gender is None else gender
        got = decode_name_keys(sample["m_nameKeys"], gender, args.locale, tables)
        ok = got == expected
        failures += not ok
        print(f"  {'OK  ' if ok else 'FAIL'}  {expected:<24} {sample['m_nameKeys']:>10}  ->  {got}")
    print(f"\n{len(samples) - failures}/{len(samples)} decoded exactly")
    return 1 if failures else 0


def cmd_decode(args) -> int:
    flags, first, middle, last = split_name_keys(args.decode)
    print(f"raw 0x{args.decode:08X}  bytes [flags={flags}, first={first}, middle={middle}, last={last}]")
    name = decode_name_keys(args.decode, args.gender, args.locale)
    print(f"name: {name if name is not None else '<unresolved -- snapshot may be stale>'}")
    return 0 if name is not None else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--verify", action="store_true", help="decode every recorded sample and check it")
    ap.add_argument("--decode", type=lambda s: int(s, 0), help="decode one raw m_nameKeys value")
    ap.add_argument("--gender", type=int, default=GENDER_MALE, help="m_eGender for --decode (1 = male)")
    ap.add_argument("--locale", default=DEFAULT_LOCALE)
    args = ap.parse_args()

    if args.decode is not None:
        return cmd_decode(args)
    return cmd_verify(args)


if __name__ == "__main__":
    sys.exit(main())
