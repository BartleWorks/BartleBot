"""
Diagnostic script to find the correct wstring offset for quicksell item names.

HOW TO USE:
  1. Start Wizard101 and log in.
  2. Open your backpack (B), switch to jewel page (Y), then open the quicksell UI.
     Make sure at least one item is visible in the list (e.g. a Skeleton Key).
  3. Run:  poetry run python scripts/probe_windowtext.py
  4. Paste the output back so we can identify the correct offset.
"""

import asyncio

from wizwalker import ClientHandler
from wizwalker.memory.memory_object import Primitive


async def _try_read_wstring_at_offset(window, offset: int) -> str | None:
    """
    Attempt to read a std::wstring at base_address + offset.
    Returns the decoded string if it looks valid, else None.
    Handles both pointer (len >= 8 bytes) and inline SSO paths.
    """
    try:
        base = await window.read_base_address()
        addr = base + offset
        length = await window.read_typed(addr + 16, Primitive.int32)
        if not (1 <= length <= 80):
            return None

        byte_len = length * 2  # UTF-16-LE: 2 bytes per char

        if byte_len >= 8:
            ptr = await window.read_typed(addr, Primitive.int64)
            if ptr < 0x10000:  # null or garbage pointer
                return None
            raw = await window.read_bytes(ptr, byte_len)
        else:
            raw = await window.read_bytes(addr, byte_len)

        text = raw.decode("utf-16-le", errors="replace")
        # Require mostly printable, non-replacement characters
        if text.count("�") > 1:
            return None
        if not all(c.isprintable() or c in ("\n", "\r", "\t") for c in text):
            return None
        return text

    except Exception:
        return None


async def probe_window(window, label: str, scan_start: int = 400, scan_end: int = 1000):
    base = await window.read_base_address()
    type_name = await window.maybe_read_type_name()
    wname = await window.name()
    print(f"\n{'=' * 60}")
    print(f"  Window: {label!r}  name={wname!r}  type={type_name!r}")
    print(f"  Base address: {hex(base)}")
    print(f"  Scanning offsets {scan_start}..{scan_end} (step 8)...")
    print()

    hits = []
    for offset in range(scan_start, scan_end, 8):
        text = await _try_read_wstring_at_offset(window, offset)
        if text:
            hits.append((offset, text))
            print(f"    offset {offset:4d} (0x{offset:03x}): {text!r}")

    if not hits:
        print("    (no readable strings found in range)")

    return hits


async def navigate_path(root_window, path: list[str]):
    window = root_window
    for segment in path:
        try:
            window = await window.get_child_by_name(segment)
        except ValueError:
            return None, segment
    return window, None


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
        print("Hooks active.\n")

        # --- Probe the quicksell item title windows ---
        quicksell_base = ["WorldView", "shopGUI", "buyWindow", "column0"]
        for row in range(3):  # probe first 3 rows
            path = quicksell_base + [f"shoplist{row}"]
            window, failed_at = await navigate_path(client.root_window, path)
            if window is None:
                print(f"shoplist{row}: could not navigate to '{failed_at}' — is the quicksell UI open?")
                continue
            await probe_window(window, f"shoplist{row} (item title row {row})")

        # --- Also probe the jewel inventory space text (known-working reference) ---
        jewel_path = ["WorldView", "DeckConfiguration", "JewelSpellbookPage", "inventorySpace"]
        window, failed_at = await navigate_path(client.root_window, jewel_path)
        if window is not None:
            print("\n--- Reference: inventorySpace (jewel capacity, known text field) ---")
            await probe_window(window, "inventorySpace", scan_start=400, scan_end=900)

        # --- Print known offsets for comparison ---
        print(f"\n{'=' * 60}")
        print("Known offsets for reference:")
        print("  maybe_text()          -> reads wstring at base + 712  (Laurenz update)")
        print("  get_text_by_path fallback -> reads wstring at base + 616  (BartleBot node_helpers)")
        print()


if __name__ == "__main__":
    asyncio.run(main())
