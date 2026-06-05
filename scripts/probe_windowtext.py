"""
Diagnostic script to find the correct wstring offset for window text.

HOW TO USE:
  Run once per UI state you want to test, e.g.:
    - Quicksell UI open
    - Realm-change dialog open
    - Zone-selection list open
    - Any message box visible
    - Normal gameplay (world map, overworld, etc.)

  Run:  poetry run python scripts/probe_windowtext.py

  The script walks the entire visible UI tree and reports every window where
  offset 712 or 736 yields readable text, shown side-by-side so you can
  compare which is correct.
"""

import asyncio

from wizwalker import ClientHandler
from wizwalker.errors import MemoryReadError
from wizwalker.memory.memory_object import Primitive

PROBE_OFFSETS = (712, 736)


async def _read_wstring_raw(window, offset: int) -> tuple[str | None, str]:
    """
    Read a wstring at base+offset with no validity filter.
    Returns (text_or_None, status_string).
    text_or_None is set only when the result looks like real text.
    """
    try:
        base = await window.read_base_address()
        addr = base + offset
        length = await window.read_typed(addr + 16, Primitive.int32)

        if length <= 0 or length > 200:
            return None, f"<bad length {length}>"

        byte_len = length * 2
        if byte_len >= 8:
            ptr = await window.read_typed(addr, Primitive.int64)
            if ptr < 0x10000:
                return None, f"<null ptr, len={length}>"
            raw = await window.read_bytes(ptr, byte_len)
        else:
            raw = await window.read_bytes(addr, byte_len)

        text = raw.decode("utf-16-le", errors="replace")
        garbage = text.count("�") + sum(1 for c in text if not (c.isprintable() or c in "\n\r\t"))
        if garbage > len(text) * 0.2:
            return None, f"<garbage ({garbage} bad chars): {text!r:.40}"
        return text, repr(text)

    except (MemoryReadError, ValueError) as e:
        return None, f"<error: {e}>"
    except Exception as e:
        return None, f"<exception: {e}>"


async def _walk_tree(window, results: list, depth: int = 0, max_depth: int = 12):
    if depth > max_depth:
        return
    try:
        name = await window.name()
        type_name = await window.maybe_read_type_name()

        readings = {}
        any_text = False
        for offset in PROBE_OFFSETS:
            text, status = await _read_wstring_raw(window, offset)
            readings[offset] = (text, status)
            if text:
                any_text = True

        if any_text:
            results.append((depth, name, type_name, readings))

        children = await window.children()
        for child in children:
            await _walk_tree(child, results, depth + 1, max_depth)
    except Exception:
        pass


async def main():
    async with ClientHandler() as handler:
        clients = handler.get_new_clients()
        if not clients:
            print("ERROR: No Wizard101 clients found. Start the game first.")
            return

        client = clients[0]
        print(f"Found client: {client}")
        print("Activating hooks (this may take a moment)...")
        await client.activate_hooks()
        print("Hooks active.")
        print("Walking UI tree — this may take several seconds...\n")

        results = []
        await _walk_tree(client.root_window, results)

        if not results:
            print("No windows with readable text found at offsets 712 or 736.")
            return

        # Categorise: agree (both same), 712-only, 736-only, disagree (both text but different)
        agree, only_712, only_736, disagree = [], [], [], []
        for entry in results:
            _, _, _, readings = entry
            t712 = readings[712][0]
            t736 = readings[736][0]
            if t712 and t736:
                if t712 == t736:
                    agree.append(entry)
                else:
                    disagree.append(entry)
            elif t712:
                only_712.append(entry)
            else:
                only_736.append(entry)

        def print_entry(depth, name, type_name, readings):
            indent = "  " * depth
            print(f"{indent}[{name!r}] ({type_name})")
            for offset in PROBE_OFFSETS:
                _, status = readings[offset]
                print(f"{indent}  offset {offset}: {status}")

        print(f"{'=' * 70}")
        print(f"  ONLY offset 736 has text ({len(only_736)} windows) — these need 736")
        print(f"{'=' * 70}")
        for entry in only_736:
            print_entry(*entry)
            print()

        print(f"{'=' * 70}")
        print(f"  ONLY offset 712 has text ({len(only_712)} windows) — these need 712")
        print(f"{'=' * 70}")
        for entry in only_712:
            print_entry(*entry)
            print()

        print(f"{'=' * 70}")
        print(f"  BOTH offsets have text but DISAGREE ({len(disagree)} windows)")
        print(f"{'=' * 70}")
        for entry in disagree:
            print_entry(*entry)
            print()

        print(f"{'=' * 70}")
        print(f"  Both offsets agree / same text ({len(agree)} windows) — either works")
        print(f"{'=' * 70}")
        for entry in agree:
            print_entry(*entry)
            print()


if __name__ == "__main__":
    asyncio.run(main())
