"""Diagnostic: confirm the DeckListControl entry offset and find the active flag.

Round 2 established:
  * DeckListControl.spell_entries() reads offset 0x280, which is null in this
    build -- CardsInDeck returned 0 entries despite holding cards.
  * An offset scan of ItemSpells found a vector at 0x300 whose length at stride
    0x28 is exactly the item card count (17), spanning BOTH pages.
  * SpellListControl (AllPageSpellList) is unaffected and reads correctly.

This round confirms that and locates the toggled-on/off flag:

  A. Read CardsInDeck and ItemSpells at BOTH 0x280 and 0x300.  If 0x300 yields
     your real deck size and item card count, the offset fix is confirmed and
     item cards can be enumerated from memory with no paging at all.

  B. Decode every ItemSpells entry -- name plus the documented fields.

  C. Dump the raw 0x28 bytes of each entry and automatically report every byte
     offset whose values split the entries into a small group and a large one.
     With 2 of 17 cards enabled, an offset that splits 2/15 is the active flag.

HOW TO RUN
    1. Same setup as before: wizard logged in, deck page open (press P), some
       cards in the normal deck, and a known number of item cards toggled ON.
    2. uv run python tools/probe_item_cards.py

    WRITE DOWN THE NAMES of the item cards you have toggled ON -- section C
    reports candidate flags, and the names are how we confirm the right one.

Read-only: never clicks or writes, so it cannot disturb your deck.
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter

from wizwalker.extensions.scripting.utils import _maybe_get_named_window
from wizwalker.extensions.wizsprinter import WizSprinter
from wizwalker.memory.memory_object import Primitive
from wizwalker.memory.memory_objects.window import (
    DeckListControlSpellEntry,
    DynamicDeckListControl,
)

ENTRY_STRIDE = 0x28
CANDIDATE_OFFSETS = (0x280, 0x300)


async def _safe(coro, default=None):
    try:
        return await coro
    except Exception:
        return default


async def _vector_at(control, offset: int) -> tuple[int, int, int]:
    """Return (begin, end, count) for a stride-0x28 vector at `offset`."""
    begin = await _safe(control.read_value_from_offset(offset, Primitive.uint64), 0) or 0
    end = await _safe(control.read_value_from_offset(offset + 8, Primitive.uint64), 0) or 0
    count = (end - begin) // ENTRY_STRIDE if end > begin else 0
    return begin, end, count


async def section_a(deck_window, client) -> tuple[int, int]:
    print("A. OFFSET CONFIRMATION")
    print()
    found = {}
    for name in ("CardsInDeck", "ItemSpells"):
        try:
            win = await _maybe_get_named_window(deck_window, name)
        except ValueError:
            print(f"   {name:<14} window not found")
            continue
        control = DynamicDeckListControl(client.hook_handler, await win.read_base_address())
        for offset in CANDIDATE_OFFSETS:
            begin, end, count = await _vector_at(control, offset)
            marker = "  <-- plausible" if 0 < count <= 256 else ""
            print(f"   {name:<14} 0x{offset:03x}  begin=0x{begin:012x}  count={count}{marker}")
            if offset == 0x300:
                found[name] = count
        print()
    print("   Expect CardsInDeck == your normal deck size and")
    print("   ItemSpells == your total item cards (both pages).")
    print()
    return found.get("CardsInDeck", 0), found.get("ItemSpells", 0)


async def section_b(control, begin: int, count: int) -> list[str]:
    print("B. ITEM CARD ENTRIES (decoded)")
    print()
    print(f"   {'#':>3}  {'name':<32} {'valid':>6}  {'item':>5}  {'side':>5}  {'tc':>5}")
    print("   " + "-" * 68)
    names: list[str] = []
    for idx in range(count):
        addr = begin + idx * ENTRY_STRIDE
        entry = DeckListControlSpellEntry(control.hook_handler, addr)
        valid = await _safe(entry.valid_graphical_spell(), "?")
        name = "(none)"
        item = side = tc = "-"
        gfx = await _safe(entry.graphical_spell())
        if gfx:
            tmpl = await _safe(gfx.spell_template())
            if tmpl:
                name = await _safe(tmpl.name(), "?") or "(empty)"
            item = await _safe(gfx.item_card(), "?")
            side = await _safe(gfx.side_board(), "?")
            tc = await _safe(gfx.treasure_card(), "?")
        names.append(str(name))
        print(f"   {idx:>3}  {str(name)[:32]:<32} {str(valid):>6}  {str(item):>5}  {str(side):>5}  {str(tc):>5}")
    print()
    return names


async def section_c(control, begin: int, count: int, names: list[str]) -> None:
    print("C. RAW ENTRY BYTES + AUTOMATIC FLAG DETECTION")
    print()
    blobs: list[bytes] = []
    for idx in range(count):
        addr = begin + idx * ENTRY_STRIDE
        data = await _safe(control.read_bytes(addr, ENTRY_STRIDE), b"")
        blobs.append(data or b"")

    print("   raw entries:")
    for idx, blob in enumerate(blobs):
        hexed = " ".join(f"{b:02x}" for b in blob)
        print(f"   {idx:>3}  {hexed}")
    print()

    usable = [b for b in blobs if len(b) == ENTRY_STRIDE]
    if len(usable) != count:
        print(f"   !! only {len(usable)}/{count} entries read cleanly; skipping analysis")
        return

    print("   byte offsets that split the entries into distinct groups")
    print("   (ignoring offsets that are all-same or all-different):")
    print()
    interesting = 0
    for byte_off in range(ENTRY_STRIDE):
        values = [b[byte_off] for b in usable]
        tally = Counter(values)
        if len(tally) < 2 or len(tally) > 4:
            continue
        interesting += 1
        groups = []
        for val, n in sorted(tally.items(), key=lambda kv: -kv[1]):
            members = [names[i][:18] for i, v in enumerate(values) if v == val]
            shown = ", ".join(members[:4]) + ("..." if len(members) > 4 else "")
            groups.append(f"0x{val:02x}×{n} [{shown}]")
        print(f"   +0x{byte_off:02x}: " + "  |  ".join(groups))
    if not interesting:
        print("   (no offset splits the entries into small groups)")
    print()
    print("   The offset whose SMALL group matches the cards you toggled ON")
    print("   is the active flag. Note that 0x00-0x07 is the graphical spell")
    print("   pointer and 0x10-0x13 is valid_graphical_spell.")
    print()


async def probe(client) -> None:
    root = client.root_window
    try:
        deck_window = await _maybe_get_named_window(root, "DeckConfiguration")
    except ValueError:
        print("  !! DeckConfiguration not found -- open the deck page (press P) first.")
        return

    deck_count, item_count = await section_a(deck_window, client)
    print(f"   => CardsInDeck={deck_count}  ItemSpells={item_count}")
    print()

    if not item_count:
        print("   No item card vector at 0x300 -- nothing further to analyse.")
        return

    item_win = await _maybe_get_named_window(deck_window, "ItemSpells")
    control = DynamicDeckListControl(client.hook_handler, await item_win.read_base_address())
    begin, _end, count = await _vector_at(control, 0x300)

    names = await section_b(control, begin, count)
    await section_c(control, begin, count, names)


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

    try:
        for i, client in enumerate(clients, start=1):
            print()
            print("=" * 78)
            print(f"CLIENT {i}")
            print("=" * 78)
            print()
            try:
                await probe(client)
            except Exception as exc:
                print(f"  probe failed: {exc!r}")
    finally:
        for client in clients:
            try:
                await client.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
