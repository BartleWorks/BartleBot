"""Diagnostic: dump the raw string struct behind a UI control's text.

WHY THIS EXISTS
    Running the game in French, `housing.read_page` died with MemoryReadError. This probe
    found the cause, and the answer is recorded here because the reasoning is easy to get
    wrong -- two plausible theories were checked and both were wrong.

WHAT IT ACTUALLY IS
    `wizwalker`'s Window.maybe_text() reads the character pointer unconditionally:

        # this is a guess, but it seems like its awlways a pointer regardless of length
        string_address = await self.read_typed(base_address, Primitive.int64)

    MSVC's std::basic_string is a 16-byte union followed by size and capacity:

        +0  union { _Elem _Buf[_BUF_SIZE]; _Elem *_Ptr; }
        +16 size_type _Mysize      (characters)
        +24 size_type _Myres       (capacity, in characters)

    While the capacity fits the inline buffer, those first 16 bytes ARE the characters.
    Reading them as an address yields the text reinterpreted as a pointer. A castle named
    "Ferme" faulted at 30681262094352454 = 0x006D007200650046 -- the letters F, e, r, m.

    CAPACITY IS THE TEST, NOT LENGTH. A std::basic_string never returns to its inline
    buffer once it has allocated, so a control that has held a long string keeps a heap
    pointer while showing a short one. Both states show up in the same client: an
    inventory row showing "Ferme" (5 chars) is inline and faults, while a spiral door
    option showing "MeuhShu" (7 chars) reads fine, because that window previously listed
    "Sorcelleville". A length-based check gets the second case wrong in the other
    direction -- reading a real pointer as if it were text.

TWO THEORIES THIS RULED OUT, SO NOBODY RE-RUNS THEM

    * WRONG OFFSET. Deimos reads checkbox rows at offset 616 via
      `read_control_checkbox_text`, which suggested maybe_text's 736/712 was wrong for
      these controls. It is not: 736 is correct for both InventoryPageWindow::InventoryItem
      and ControlCheckBox. Offsets 616 and 712 hold a float and a small int -- visibly not
      strings. 616 is a red herring; do not chase it again.

    * A LOCALE OR ENCODING PROBLEM. There is none. The junk text "ஈ៨翷" seen on the
      housing tab decodes to bytes 88 0b e8 17 f7 7f, the low six bytes of
      0x00007FF717E80B88 -- an image-base pointer read as text. French only mattered
      because it happened to produce a short string in a freshly built control.

WHAT IT DOES
    For each control the app reads text from, dumps the string struct at the candidate
    offsets: length, capacity, the raw union bytes, and the text decoded BOTH ways --
    inline and through the pointer. The correct reading is the one the capacity selects.

    Read-only. Nothing is written to the game or to disk.

USAGE
    poetry run python tools/probe_control_text.py            # housing page (press U first)
    poetry run python tools/probe_control_text.py --spiral   # spiral door (open it first)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from wizwalker.extensions.wizsprinter import WizSprinter
from wizwalker.memory.memory_object import Primitive

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bartlebot.nodes import node_helpers  # noqa: E402
from bartlebot.nodes.paths import (  # noqa: E402
    HOUSING_CASTLES_TAB_PATH,
    HOUSING_EQUIP_BUTTON_PATH,
    housing_castle_row_path,
    universe_zone_option_path,
)

# Castle names and world names are not character names, but a castle list is still the
# user's own data — print it, never write it anywhere.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CANDIDATE_OFFSETS = (616, 712, 736)

# MSVC std::wstring keeps its characters inline in the 16-byte union while the CAPACITY
# fits it -- _BUF_SIZE is 16 / sizeof(wchar_t) = 8. Capacity, not length: a string never
# returns to the inline buffer once it has allocated, so a control that once showed a long
# string keeps a heap pointer while showing a short one.
WIDE_BUF_CHARS = 8


async def _decode_both_ways(window, offset: int) -> str:
    """Report the string struct at `offset` read as inline chars AND as a pointer."""
    try:
        base = await window.read_base_address() + offset
        raw = await window.read_bytes(base, 32)
        length = await window.read_typed(base + 16, Primitive.int32)
        capacity = await window.read_typed(base + 24, Primitive.uint64)
    except Exception as exc:
        return f"      +{offset}: unreadable ({type(exc).__name__})"

    ptr = int.from_bytes(raw[:8], "little")
    lines = [f"      +{offset}: len={length:<6} cap={capacity:<6} raw={raw[:8].hex(' ')}  ptr=0x{ptr:016X}"]

    if not 0 < length < 512:
        lines.append(f"           length {length} is not plausible — wrong struct")
        return "\n".join(lines)

    inline = raw[: min(length * 2, 16)]
    try:
        lines.append(f"           as inline : {inline.decode('utf-16-le', errors='replace')!r}")
    except Exception:
        pass

    if ptr > 0x7FFFFFFFFFFF:
        lines.append("           as pointer: non-canonical — this is inline text misread as a pointer")
    else:
        try:
            data = await window.read_bytes(ptr, length * 2)
            lines.append(f"           as pointer: {data.decode('utf-16-le', errors='replace')!r}")
        except Exception as exc:
            lines.append(f"           as pointer: unreadable ({type(exc).__name__})")

    if capacity < WIDE_BUF_CHARS:
        lines.append(f"           cap {capacity} < {WIDE_BUF_CHARS}: text is INLINE — the pointer reading is bogus")
    else:
        lines.append(f"           cap {capacity} >= {WIDE_BUF_CHARS}: text is behind the POINTER")
    return "\n".join(lines)


async def _report(client, label: str, path: list[str]) -> None:
    print(f"\n  {label}")
    print(f"    path: {path}")
    matches = await node_helpers.find_windows_by_path(client.root_window, path)
    if not matches:
        print("    NOT FOUND — is the right page open?")
        return
    if len(matches) > 1:
        print(f"    note: path matched {len(matches)} windows; reporting the first")
    window = matches[0]

    try:
        type_name = await window.maybe_read_type_name()
    except Exception as exc:
        type_name = f"<unreadable: {type(exc).__name__}>"
    print(f"    type: {type_name}")

    try:
        text = await window.maybe_text()
        print(f"    maybe_text() -> {text!r}   (this is what the app sees today)")
    except Exception as exc:
        print(f"    maybe_text() -> RAISED {type(exc).__name__}: {exc}")

    for offset in CANDIDATE_OFFSETS:
        print(await _decode_both_ways(window, offset))


async def probe(client, spiral: bool) -> None:
    if spiral:
        print("SPIRAL DOOR — open the World Tree door by hand before running this.")
        targets = [(f"opt{i}", universe_zone_option_path(i)) for i in range(4)]
    else:
        print("HOUSING PAGE — press U and select the Castles tab by hand before running this.")
        targets = [
            ("Castles tab", HOUSING_CASTLES_TAB_PATH),
            ("Equip_Item", HOUSING_EQUIP_BUTTON_PATH),
        ]
        targets += [(f"castle row {row}", housing_castle_row_path(row)) for row in range(1, 5)]

    for label, path in targets:
        await _report(client, label, path)

    print(
        "\nRead the table above like this: the offset whose `len` matches the visible\n"
        "character count, and whose decoded text matches what is on screen, is the right\n"
        "one. If that text shows up under `as inline` while maybe_text() raised or\n"
        "returned junk, the short-string bug is what bit you. If the correct text is at a\n"
        "different offset than 736/712 entirely, it is the wrong-offset bug."
    )


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--spiral", action="store_true", help="probe the spiral door options instead of the housing page")
    args = ap.parse_args()

    sprinter = WizSprinter()
    clients = sprinter.get_new_clients()
    if not clients:
        print("No Wizard101 clients found.")
        return 1

    client = clients[0]
    if len(clients) > 1:
        print(f"Found {len(clients)} clients; probing the first one only.")

    print("Activating hooks...")
    try:
        await client.activate_hooks()
    except Exception as exc:
        print(f"Hook activation failed: {exc!r}")
        return 1

    try:
        print()
        await probe(client, spiral=args.spiral)
    finally:
        for other in clients:
            try:
                await other.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
