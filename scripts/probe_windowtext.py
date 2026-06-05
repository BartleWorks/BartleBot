"""
Diagnostic script to assess maybe_text() correctness across control types.

HOW TO USE:
  Run once per UI state you want to test, e.g.:
    - Quicksell UI open
    - Realm-change dialog open
    - Zone-selection list open
    - Any message box visible
    - Normal gameplay (world map, overworld, etc.)

  Run:  poetry run python scripts/probe_windowtext.py

  The script walks the entire visible UI tree and for every window that has
  readable text at offset 712 or 736 it reports:
    - the raw read at offset 712
    - the raw read at offset 736
    - what maybe_text() actually returned
  so you can spot any mismatches in the branching logic.
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


async def _call_maybe_text(window) -> tuple[str | None, str]:
    """Call window.maybe_text() and return (result_or_None, status_string)."""
    try:
        text = await window.maybe_text()
        if text:
            return text, repr(text)
        return None, repr(text)
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

        maybe_text_result, maybe_text_status = await _call_maybe_text(window)

        if any_text:
            results.append((depth, name, type_name, readings, maybe_text_result, maybe_text_status))

        children = await window.children()
        for child in children:
            await _walk_tree(child, results, depth + 1, max_depth)
    except Exception:
        pass


def _correct_offset_for(type_name: str) -> int:
    """Return the offset maybe_text() should use for this type."""
    return 712 if type_name == "ControlText" else 736


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

        # Classify each result relative to Laurenz's branching logic.
        correct, wrong, no_text = [], [], []
        for entry in results:
            _, _, type_name, readings, maybe_text_result, _ = entry
            correct_offset = _correct_offset_for(type_name)
            expected_text = readings[correct_offset][0]

            if not expected_text:
                # Neither offset had text — maybe_text() returning nothing is fine.
                no_text.append(entry)
            elif maybe_text_result == expected_text:
                correct.append(entry)
            else:
                wrong.append(entry)

        def print_entry(depth, name, type_name, readings, maybe_text_result, maybe_text_status):
            indent = "  " * depth
            correct_offset = _correct_offset_for(type_name)
            print(f"{indent}[{name!r}] ({type_name})  ← correct offset: {correct_offset}")
            for offset in PROBE_OFFSETS:
                _, status = readings[offset]
                marker = " ✓" if offset == correct_offset else ""
                print(f"{indent}  raw {offset}{marker}: {status}")
            print(f"{indent}  maybe_text(): {maybe_text_status}")

        print(f"{'=' * 70}")
        print(f"  CORRECT — maybe_text() matches expected offset ({len(correct)} windows)")
        print(f"{'=' * 70}")
        for entry in correct:
            print_entry(*entry)
            print()

        print(f"{'=' * 70}")
        print(f"  WRONG — maybe_text() does NOT match expected offset ({len(wrong)} windows)")
        print(f"{'=' * 70}")
        for entry in wrong:
            print_entry(*entry)
            print()

        print(f"{'=' * 70}")
        print(f"  NO RAW TEXT at either offset ({len(no_text)} windows with maybe_text result)")
        print(f"{'=' * 70}")
        for entry in no_text:
            print_entry(*entry)
            print()


if __name__ == "__main__":
    asyncio.run(main())
