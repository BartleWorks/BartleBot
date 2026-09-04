"""Diagnostic: locate the LIVE SocialSystemsManager and the field that tracks group state.

probe_group_state.py found that group_gid, player_gid and current_party_gid all read 0 and
never change. player_gid is the decisive one: a logged-in character always has a non-zero
global id, so reading 0 means the object being read is not the live one.

CurrentSocialSystemsManager finds the SSM by scanning the heap for pointers to its vtable.
When several candidates match it commits to one (social_systems_manager.py,
_find_ssm_via_constructor_pattern) -- and it evidently picked a zeroed instance.

This probe does three things:

  1. Reads the character's TRUE global id from two independent sources that are known to
     work (client_object.global_id_full and game_client.player_gid). That value is the
     fingerprint of the real object.

  2. Lists every SSM candidate and checks each one's 0x128 against that fingerprint. The
     candidate that matches IS the live SSM. If none match, it scans the whole heap for
     the fingerprint and reports what it finds, so the object can be located even if the
     vtable scan is the broken part.

  3. Snapshots the live object before/after grouping and reports which offsets changed,
     which is what identifies the real group field.

HOW TO RUN
    1. Log in at least two wizards. Start UNGROUPED.
    2. uv run python tools/probe_group_offsets.py
    3. Follow the prompts: ungrouped -> grouped -> left the group.
    4. Paste the report back.

Read-only: only reads memory. It never clicks, types, or writes.
"""

from __future__ import annotations

import asyncio
import struct
import sys

import pymem.process
import regex
from wizwalker.extensions.wizsprinter import WizSprinter
from wizwalker.memory.memory_objects.social_systems_manager import (
    _EXE_NAME,
    _SSM_CONSTRUCTOR_PATTERN,
)

# The documented layout runs to 0x37D; 0x400 covers it with room for unmapped fields.
OBJECT_SIZE = 0x400

# Offset of player_gid within SocialSystemsManager. Used as the fingerprint that tells the
# live object apart from zeroed duplicates.
PLAYER_GID_OFFSET = 0x128

KNOWN_FIELDS = {
    0x0A8: "adventure_party_list ptr",
    0x100: "has_group_quest",
    0x128: "player_gid",
    0x1B0: "stored_party_gid",
    0x1D8: "current_party_gid",
    0x260: "group_gid  <-- believed wrong",
    0x268: "adventure_party_state",
    0x364: "join_cooldown",
}


async def _safe(coro):
    try:
        return await coro
    except Exception as exc:
        return f"<{type(exc).__name__}>"


async def true_player_gid(client) -> int | None:
    """Read the character's global id from sources independent of the SSM."""
    print("\nReading the character's true global id from known-good sources:")

    from_object = await _safe(client.client_object.global_id_full())
    print(f"  client_object.global_id_full() = {from_object}")

    from_game_client = None
    try:
        game_client = await client.game_client()
        from_game_client = await _safe(game_client.player_gid())
    except Exception as exc:
        from_game_client = f"<{type(exc).__name__}>"
    print(f"  game_client.player_gid()       = {from_game_client}")

    for value in (from_game_client, from_object):
        if isinstance(value, int) and value != 0:
            return value
    return None


async def find_all_ssm_candidates(client) -> list[int]:
    """Return every heap address whose first qword points at the SSM vtable."""
    hook_handler = client.hook_handler
    module = pymem.process.module_from_name(hook_handler.process.process_handle, _EXE_NAME)
    module_base = module.lpBaseOfDll
    module_end = module_base + module.SizeOfImage

    match_addr = await hook_handler.pattern_scan(_SSM_CONSTRUCTOR_PATTERN, module=_EXE_NAME)
    disp32 = struct.unpack("<i", await hook_handler.read_bytes(match_addr + 3, 4))[0]
    vtable_addr = (match_addr + 7) + disp32
    print(f"  SSM vtable resolved to 0x{vtable_addr:X}")

    matches = await hook_handler.pattern_scan(regex.escape(struct.pack("<Q", vtable_addr)), return_multiple=True)
    if isinstance(matches, int):
        matches = [matches]
    return [a for a in matches if a < module_base or a >= module_end]


async def scan_for_gid(client, gid: int) -> list[int]:
    """Find every address in the process holding the given 64-bit value.

    Used when no vtable candidate carries the right player_gid: the live object still has
    that value at +0x128, so each hit minus 0x128 is a possible object base.
    """
    try:
        matches = await client.hook_handler.pattern_scan(regex.escape(struct.pack("<Q", gid)), return_multiple=True)
    except Exception as exc:
        print(f"  heap scan failed: {exc!r}")
        return []
    if isinstance(matches, int):
        matches = [matches]
    return matches


def _qword(buf: bytes, off: int) -> int:
    return struct.unpack_from("<Q", buf, off)[0]


def changed_offsets(before: bytes, after: bytes) -> list[int]:
    """Return 8-byte-aligned offsets whose qword differs between two snapshots.

    Aligned qwords rather than single bytes: every id field here is a uint64, so a
    byte-wise diff would report the same field up to eight times.
    """
    return [off for off in range(0, min(len(before), len(after)) - 7, 8) if _qword(before, off) != _qword(after, off)]


def report_object(addr: int, snaps: list[bytes], labels: list[str]) -> bool:
    """Print the diff analysis for one object. Returns True if anything changed."""
    ungrouped, grouped, left = snaps
    joined = changed_offsets(ungrouped, grouped)
    departed = changed_offsets(grouped, left)

    print(f"\n  Object @ 0x{addr:X}")
    print(f"    player_gid (0x128) = {_qword(ungrouped, PLAYER_GID_OFFSET)}")

    if not joined and not departed:
        print("    NO fields changed across any snapshot.")
        return False

    print(f"    {len(joined)} qword(s) changed {labels[0]} -> {labels[1]}")
    print(f"    {len(departed)} qword(s) changed {labels[1]} -> {labels[2]}")

    group_like = [
        off for off in joined if _qword(ungrouped, off) == 0 and _qword(grouped, off) != 0 and _qword(left, off) == 0
    ]
    if group_like:
        print("\n    *** GROUP ID CANDIDATES (0 -> non-zero -> 0) ***")
        for off in group_like:
            known = KNOWN_FIELDS.get(off, "")
            print(
                f"      offset 0x{off:03X}  grouped value = {_qword(grouped, off)}{'   [' + known + ']' if known else ''}"
            )

    other = [off for off in joined if off not in group_like]
    if other:
        print("\n    Other offsets that changed on grouping:")
        for off in other:
            known = KNOWN_FIELDS.get(off, "")
            print(
                f"      offset 0x{off:03X}  {_qword(ungrouped, off)} -> {_qword(grouped, off)} -> "
                f"{_qword(left, off)}{'   [' + known + ']' if known else ''}"
            )
    return True


def report_known_fields(addr: int, snaps: list[bytes], labels: list[str]) -> None:
    print(f"\n  Documented fields on 0x{addr:X}  ({' / '.join(labels)}):")
    for off, name in sorted(KNOWN_FIELDS.items()):
        print(f"    0x{off:03X} {name:<34} {' / '.join(str(_qword(s, off)) for s in snaps)}")


async def main() -> int:
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

    client = clients[0]

    # --- Step 1: fingerprint -------------------------------------------------
    gid = await true_player_gid(client)
    if gid is None:
        print(
            "\nCould not read the character's global id from ANY source. That is a bigger\n"
            "problem than group state -- the memory offsets are likely stale after a game\n"
            "patch. Paste this output back."
        )
        return 1
    print(f"\n  Using {gid} as the fingerprint of the live object.")

    # --- Step 2: locate the live object -------------------------------------
    print("\nLocating SocialSystemsManager candidates:")
    try:
        candidates = await find_all_ssm_candidates(client)
    except Exception as exc:
        print(f"  vtable scan failed: {exc!r}")
        candidates = []

    print(f"  {len(candidates)} candidate object(s) found via the vtable scan:")
    live: list[int] = []
    for addr in candidates:
        try:
            value = _qword(await client.hook_handler.read_bytes(addr, OBJECT_SIZE), PLAYER_GID_OFFSET)
        except Exception as exc:
            print(f"    0x{addr:X}  unreadable ({type(exc).__name__})")
            continue
        marker = "  <-- MATCHES, this is the live SSM" if value == gid else ""
        print(f"    0x{addr:X}  player_gid={value}{marker}")
        if value == gid:
            live.append(addr)

    if not live:
        print(
            "\n  No candidate carries the right player_gid, so the vtable scan is finding\n"
            "  only dead instances. Scanning the whole process for the fingerprint instead..."
        )
        hits = await scan_for_gid(client, gid)
        print(f"  {len(hits)} address(es) hold the value {gid}.")
        # Each hit could be an object's player_gid field, putting the base at hit-0x128.
        derived = [h - PLAYER_GID_OFFSET for h in hits]
        for base, hit in list(zip(derived, hits))[:40]:
            print(f"    value at 0x{hit:X}  ->  possible object base 0x{base:X}")
        if len(hits) > 40:
            print(f"    ... and {len(hits) - 40} more")
        live = derived[:40]
        if not live:
            print("\n  The fingerprint appears nowhere in memory. Paste this output back.")
            return 1

    watched = live[:40]
    print(f"\nWatching {len(watched)} object(s) across the group transitions.")

    # --- Step 3: diff across group transitions -------------------------------
    labels = ["ungrouped", "grouped", "left"]
    prompts = [
        "Make sure client 1 is NOT in a group, then press Enter...",
        "Now put client 1 IN a group, then press Enter...",
        "Now have client 1 LEAVE the group, then press Enter...",
    ]

    snaps_by_addr: dict[int, list[bytes]] = {addr: [] for addr in watched}
    for prompt in prompts:
        print()
        input(prompt)
        for addr in watched:
            try:
                snaps_by_addr[addr].append(await client.hook_handler.read_bytes(addr, OBJECT_SIZE))
            except Exception:
                pass
        print("    snapshot taken")

    print("\n" + "=" * 78)
    print("REPORT")
    print("=" * 78)

    any_changed = False
    for addr in watched:
        snaps = snaps_by_addr[addr]
        if len(snaps) != 3:
            continue
        if report_object(addr, snaps, labels):
            any_changed = True
            report_known_fields(addr, snaps, labels)

    print("\n" + "=" * 78)
    if not any_changed:
        print(
            "NOTHING CHANGED IN ANY WATCHED OBJECT.\n\n"
            "Group membership is not tracked where we are looking, so no offset fix will\n"
            "help. The realistic fallback is to detect group state from the UI instead of\n"
            "memory -- slower and less precise, but it does not depend on offsets.\n"
            "Paste this output back."
        )
    else:
        print("Look for an offset under GROUP ID CANDIDATES -- that is the field to read.")
    print("=" * 78)

    for c in clients:
        try:
            await c.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
