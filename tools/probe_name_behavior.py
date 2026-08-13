"""Diagnostic: is the wizard name readable from ClientWizPlayerNameBehavior?

WHY THIS EXISTS
    probe_wizard_name.py concluded there was no usable memory route, but it could not
    identify the object that actually holds the name -- reaching it "would mean identifying
    its type and finding its live instance by vtable-from-constructor-pattern plus a heap
    scan".  The r800294 type dump names a strong candidate outright:

        class ClientWizPlayerNameBehavior : NameOverrideBehavior : BehaviorInstance
            0x078  m_wsNameOverride    std::wstring
            0x1b8  m_badgeTitle        std::string
            0x1f8  m_publicPlayerName  std::string
            0x218  m_gamerTag          std::wstring

    That removes the hard part.  A BehaviorInstance is not found by heap scan -- it hangs
    off ClientObject, and wizwalker already walks there via search_behavior_by_name().

WHY THE EARLIER NEGATIVE DOES NOT SETTLE IT
    probe_wizard_name.py reported that a UTF-8 scan never matched, and m_publicPlayerName
    is a narrow std::string.  That is real counter-evidence, but it is not decisive, for a
    structural reason: that probe only promoted a hit to a "field candidate" when the
    owning std::string sat within 0x2000 of the ClientObject base.  A behavior instance is
    a separate heap allocation, so a name living there could never have been reported by
    it, no matter what the scan found.

    The likeliest reconciliation is that m_publicPlayerName is empty on the local player --
    "public" name reads like what *other* clients are told about you.  If so, the wide
    m_wsNameOverride is the better target, and it matches the earlier observation that
    every name in this process is UTF-16.  This probe reads all four fields rather than
    betting on one.

CONFIRMED (2026-08-12, live client, Karamelle / dorm / Arcanum)
    The behavior exists, listed as "WizPlayerNameBehavior" -- not the dump's class name.
    All four *string* fields read empty except m_badgeTitle, and the object survives zone
    changes: its address moved (0x1C3F6035B00 -> 0x1C382287030) while the fields kept
    reading, because the behavior is re-resolved through the behavior list each poll rather
    than cached.  That is the durability the previous attempt never had.

    The decisive observation is what m_badgeTitle contains: 'Badges_00006184', and it
    changed to 'Badges_00002858' when the badge was changed in game.  That is a lookup key,
    not display text.  This object stores the nameplate as IDs and resolves them against a
    dictionary at draw time -- which is the standing explanation for why a full-process
    scan for the name as text found nothing.  The name is here; it is just not characters.

    All three nameplate elements are present, in that form:
        name   -> m_nameKeys   0x160  unsigned int   (packed dictionary indices)
        guild  -> m_guildName  0x1EC  unsigned int
        badge  -> m_badgeTitle 0x1B8  std::string    ('Badges_NNNNNNNN')

    m_nameKeys also appears on AdvPvPLeaderboardEntry and PlayerStatuePvPBlob -- both
    display a player name -- and neither stores text beside it, which is consistent with
    the keys being the only representation the client keeps.

WHAT IS STILL OPEN
    How to turn m_nameKeys into text.  Nothing in wizwalker, wizsprinter or Deimos decodes
    it, so the packing has to be derived from samples: --record pairs the value with a name
    you supply and accumulates them in name_key_samples.json.  Names sharing a first name
    or a surname are the most informative, since a shared part should appear as a shared
    field.  Until that is solved, detect_wizard_name()'s keypress route stays.

WHAT WOULD SINK IT
    m_nameKeys turning out to be a server-side handle rather than dictionary indices --
    i.e. two characters with the same first name showing no common structure. Then the
    text genuinely is not in the client and no amount of decoding recovers it.

THE TWO THINGS THAT KILLED THE LAST ATTEMPT
    A read that works once at session start proves nothing -- that is exactly what heap
    aliasing looked like last time.  --watch polls through zone changes and character
    switches, which is what broke the previous offsets.  Treat a field as real only if it
    survives both.  character_id (0x1C0) is polled alongside as the known-good control: if
    it keeps reading while a name field dies, the base object is still live and the name
    field is the thing that is wrong.

PET FALSE POSITIVE
    ClientPetNameBehavior *inherits* from ClientWizPlayerNameBehavior, so it carries
    m_publicPlayerName at the same 0x1F8.  Anything that matches behaviors loosely, or
    scans for the offset, can return the pet's name instead of the wizard's.  This probe
    matches the behavior name exactly and reports every name-ish behavior it finds so the
    ambiguity is visible rather than silently resolved.

USAGE
    poetry run python tools/probe_name_behavior.py                 # one-shot, all clients
    poetry run python tools/probe_name_behavior.py --watch         # poll through zone/char changes
    poetry run python tools/probe_name_behavior.py --list          # dump every behavior name
    poetry run python tools/probe_name_behavior.py --record "Fred Dragonrider"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from wizwalker.extensions.wizsprinter import WizSprinter
from wizwalker.memory.memory_object import Primitive

# Offsets from the r800294 type dump; see tools/typedump.py.
#   poetry run python tools/typedump.py show ClientWizPlayerNameBehavior
#
# The dump's *class* name is not the runtime *behavior template* name, and the difference
# is not derivable -- confirmed against a live client's behavior list:
#
#     class ClientWizPlayerNameBehavior  ->  "WizPlayerNameBehavior"
#     class ClientTreasureBookBehavior   ->  "BasicTreasureBookBehavior"
#     class ClientInventoryBehavior      ->  "WizardInventoryBehavior"
#
# Client -> Wiz, Client -> Basic, Client -> Wizard: there is no rule to apply. So rather
# than hardcode a translation, match on the "NameBehavior" suffix that both the class and
# template names share. That survives the next rename and needs no second lookup table.
NAME_BEHAVIOR_SUFFIX = "namebehavior"

# A pet is a separate ClientObject, so its behavior should not appear on the player's --
# but ClientPetNameBehavior *inherits* from ClientWizPlayerNameBehavior and carries
# m_publicPlayerName at the same 0x1F8, so anything matching loosely could pick it up.
PET_MARKER = "pet"

# (label, offset, wide) -- wide selects read_wide_string_from_offset for std::wstring.
NAME_FIELDS: list[tuple[str, int, bool]] = [
    ("m_wsNameOverride", 0x078, True),
    ("m_badgeTitle", 0x1B8, False),
    ("m_publicPlayerName", 0x1F8, False),
    ("m_gamerTag", 0x218, True),
]

# The integer fields. m_badgeTitle reads 'Badges_00006184' -- a lookup key, not display
# text -- which is the tell for how this whole object works: the nameplate is stored as
# IDs and resolved against a dictionary at draw time. That is why every plaintext scan for
# the name failed, and it means the name is here as m_nameKeys, not as characters.
#
# m_nameKeys also appears on AdvPvPLeaderboardEntry and PlayerStatuePvPBlob -- both places
# a player name is *displayed* -- and neither carries a text name beside it. Nothing in
# wizwalker, wizsprinter or Deimos decodes it, so the packing has to be derived from
# samples with known ground truth. Hence --record.
INT_FIELDS: list[tuple[str, int, object]] = [
    ("m_nameKeys", 0x160, Primitive.uint32),
    ("m_eGender", 0x1B0, Primitive.uint32),
    ("m_eRace", 0x1B4, Primitive.uint32),
    ("m_guildName", 0x1EC, Primitive.uint32),
    ("m_guildNameLocale", 0x1F0, Primitive.int32),
    ("m_localeID", 0x1E4, Primitive.uint32),
]

# Wizard101 names are "First Lastpart1lastpart2" drawn from fixed dictionaries, so three
# indices packed into 32 bits is the natural encoding. These are the splits worth eyeballing
# against a known name; the real one falls out once a few samples exist.
BIT_SPLITS: list[tuple[str, tuple[int, ...]]] = [
    ("10/10/10", (10, 10, 10)),
    ("8/12/12", (8, 12, 12)),
    ("8/8/8/8", (8, 8, 8, 8)),
    ("16/16", (16, 16)),
]

SAMPLES_PATH = Path(__file__).parent / "name_key_samples.json"

# The known-good control from probe_wizard_name.py: a reliable character identity that
# held steady through a zone change and tracked character switches correctly.
CHARACTER_ID_OFFSET = 0x1C0

WATCH_INTERVAL = 3.0


async def _safe(coro):
    """Await a memory read, turning any failure into the exception itself.

    Reads against a half-torn-down object fail in several unrelated ways
    (AddressOutOfRange, ValueError on a bad length prefix, MemoryReadError). Which one
    fired is diagnostic, so it is kept rather than flattened to None.
    """
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001 - the exception type is the diagnostic
        return exc


def _show(value) -> str:
    if isinstance(value, Exception):
        return f"<{type(value).__name__}>"
    return repr(value)


async def find_name_behaviors(client_object) -> dict[str, object]:
    """Every behavior on this object whose template name ends in "NameBehavior".

    Discovered by walking the behavior list rather than asked for by name, because the
    runtime template name cannot be derived from the dump's class name (see above). Any
    match is returned so a pet's inherited copy of the field stays visible in the output
    instead of quietly masquerading as the wizard's.
    """
    behaviors = await _safe(client_object.inactive_behaviors())
    if isinstance(behaviors, Exception) or not behaviors:
        return {}

    found: dict[str, object] = {}
    for behavior in behaviors:
        name = await _safe(behavior.behavior_name())
        if isinstance(name, str) and name.lower().endswith(NAME_BEHAVIOR_SUFFIX):
            found[name] = behavior
    return found


async def read_name_fields(behavior) -> dict[str, object]:
    """Read every candidate name field off one behavior instance."""
    out: dict[str, object] = {}
    for label, offset, wide in NAME_FIELDS:
        reader = behavior.read_wide_string_from_offset if wide else behavior.read_string_from_offset
        out[label] = await _safe(reader(offset))
    return out


async def read_int_fields(behavior) -> dict[str, object]:
    """Read the packed-ID fields, where the name itself is expected to live."""
    out: dict[str, object] = {}
    for label, offset, primitive in INT_FIELDS:
        out[label] = await _safe(behavior.read_value_from_offset(offset, primitive))
    return out


def split_bits(value: int, widths: tuple[int, ...]) -> list[int]:
    """Split an integer into fields of the given bit widths, most significant first."""
    out: list[int] = []
    shift = sum(widths)
    for width in widths:
        shift -= width
        out.append((value >> shift) & ((1 << width) - 1))
    return out


def describe_name_keys(value: object) -> list[str]:
    """Render m_nameKeys under each candidate packing, for eyeballing against a real name."""
    if not isinstance(value, int):
        return [f"        m_nameKeys unreadable: {_show(value)}"]
    lines = [f"        m_nameKeys = {value}  0x{value:08X}  0b{value:032b}"]
    for label, widths in BIT_SPLITS:
        parts = ", ".join(str(p) for p in split_bits(value, widths))
        lines.append(f"            as {label:<9} -> [{parts}]")
    return lines


async def snapshot(client) -> dict:
    """One poll of the control field plus every name field, for one client."""
    client_object = client.client_object
    snap: dict = {
        "zone": await _safe(client.zone_name()),
        "character_id": await _safe(client_object.read_value_from_offset(CHARACTER_ID_OFFSET, Primitive.uint64)),
        "behaviors": {},
    }
    for name, behavior in (await find_name_behaviors(client_object)).items():
        snap["behaviors"][name] = {
            "address": await _safe(behavior.read_base_address()),
            "fields": await read_name_fields(behavior),
            "ints": await read_int_fields(behavior),
        }
    return snap


def format_snapshot(title: str, snap: dict) -> list[str]:
    lines = [
        f"{title}",
        f"    zone         = {_show(snap['zone'])}",
        f"    character_id = {_show(snap['character_id'])}   (control: 0x1C0)",
    ]
    if not snap["behaviors"]:
        lines.append("    no *NameBehavior on this client object -- route is dead")
        return lines
    for name, info in snap["behaviors"].items():
        addr = info["address"]
        addr_txt = f"0x{addr:X}" if isinstance(addr, int) else _show(addr)
        tag = "  <- PET, not the wizard" if PET_MARKER in name.lower() else ""
        lines.append(f"    {name} @ {addr_txt}{tag}")
        for label, offset, _wide in NAME_FIELDS:
            lines.append(f"        +0x{offset:03X} {label:<20} = {_show(info['fields'][label])}")
        ints = info.get("ints", {})
        for label, offset, _prim in INT_FIELDS:
            lines.append(f"        +0x{offset:03X} {label:<20} = {_show(ints.get(label))}")
        lines.extend(describe_name_keys(ints.get("m_nameKeys")))
    return lines


async def cmd_list(clients) -> int:
    """Dump every behavior name on each client object.

    Worth running first: this list holds the runtime template names, which are the only
    authoritative source for what to search for -- they are not derivable from the dump's
    class names. It is what revealed the real name is "WizPlayerNameBehavior".
    """
    for i, client in enumerate(clients):
        print(f"\n=== client {i} ===")
        names = await _safe(client.client_object.list_behavior_names())
        if isinstance(names, Exception):
            print(f"  could not list behaviors: {_show(names)}")
            continue
        for name in sorted(names):
            mark = "  <-- name behavior" if "Name" in name else ""
            print(f"  {name}{mark}")
    return 0


def record_sample(path: Path, wizard_name: str, snap: dict) -> dict | None:
    """Append one (known name -> m_nameKeys) observation to the sample file.

    Decoding the packing needs several names whose text is known. Accumulating them in a
    file means the samples survive between sessions and across characters, which is the
    only way this gets solved -- one sample constrains almost nothing.
    """
    behaviors = snap.get("behaviors", {})
    wiz = next((info for name, info in behaviors.items() if PET_MARKER not in name.lower()), None)
    if wiz is None:
        return None
    keys = wiz.get("ints", {}).get("m_nameKeys")
    if not isinstance(keys, int):
        return None

    sample = {
        "wizard_name": wizard_name,
        "m_nameKeys": keys,
        "character_id": snap.get("character_id") if isinstance(snap.get("character_id"), int) else None,
        "m_eGender": wiz["ints"].get("m_eGender") if isinstance(wiz["ints"].get("m_eGender"), int) else None,
        "m_eRace": wiz["ints"].get("m_eRace") if isinstance(wiz["ints"].get("m_eRace"), int) else None,
    }

    existing: list[dict] = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = []
    # Re-recording the same character should correct the entry, not duplicate it.
    existing = [s for s in existing if s.get("wizard_name") != wizard_name]
    existing.append(sample)
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return sample


async def cmd_record(clients, wizard_name: str) -> int:
    snap = await snapshot(clients[0])
    for line in format_snapshot("\n=== client 0 ===", snap):
        print(line)
    sample = record_sample(SAMPLES_PATH, wizard_name, snap)
    if sample is None:
        print("\nCould not record: no readable m_nameKeys on this client.")
        return 1
    total = len(json.loads(SAMPLES_PATH.read_text(encoding="utf-8")))
    print(
        f"\nRecorded {wizard_name!r} -> m_nameKeys={sample['m_nameKeys']} in {SAMPLES_PATH.name} ({total} sample(s))."
    )
    print("Switch character and record again. Names sharing a first name or a surname are")
    print("the most informative -- a shared part should show up as a shared field.")
    return 0


async def cmd_once(clients) -> int:
    for i, client in enumerate(clients):
        for line in format_snapshot(f"\n=== client {i} ===", await snapshot(client)):
            print(line)
    print(
        "\nA single good read proves nothing -- the previous attempt also worked once at "
        "session start. Re-run with --watch and change zone, then switch character."
    )
    return 0


async def cmd_watch(clients, interval: float) -> int:
    print(
        f"Polling {len(clients)} client(s) every {interval}s. Change zone, then switch character.\n"
        "A field that survives both is real; one that dies at a zone change is heap aliasing.\n"
        "Ctrl-C to stop.\n"
    )
    start = time.monotonic()
    previous: dict[int, str] = {}
    while True:
        for i, client in enumerate(clients):
            snap = await snapshot(client)
            rendered = "\n".join(format_snapshot(f"[{time.monotonic() - start:6.1f}s] client {i}", snap))
            # Only print on change: a steady field scrolling past every 3s buries the
            # transition that actually matters.
            if previous.get(i) != rendered.split("]", 1)[1]:
                previous[i] = rendered.split("]", 1)[1]
                print(rendered)
        await asyncio.sleep(interval)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--watch", action="store_true", help="poll through zone changes and character switches")
    parser.add_argument("--list", action="store_true", help="list every behavior name and exit")
    parser.add_argument("--interval", type=float, default=WATCH_INTERVAL, help="seconds between polls with --watch")
    parser.add_argument(
        "--record",
        metavar="NAME",
        help='record this client\'s m_nameKeys against a known wizard name, e.g. --record "Fred Dragonrider"',
    )
    args = parser.parse_args()

    sprinter = WizSprinter()
    clients = sprinter.get_new_clients()
    if not clients:
        print("No Wizard101 clients found. Start the game and log a wizard in.")
        return 1

    print(f"Found {len(clients)} client(s); activating hooks...")
    try:
        await asyncio.gather(*[c.activate_hooks() for c in clients])
    except Exception as exc:
        print(f"Hook activation failed: {exc!r}")
        return 1

    try:
        if args.list:
            return await cmd_list(clients)
        if args.record:
            return await cmd_record(clients, args.record)
        if args.watch:
            return await cmd_watch(clients, args.interval)
        return await cmd_once(clients)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nStopped.")
        return 0
    finally:
        for c in clients:
            try:
                await c.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
