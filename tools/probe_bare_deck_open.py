"""Diagnostic: can the deck be read by pressing P and nothing else?

WHY THIS EXISTS
    Reading a deck currently goes through DeckBuilder.open_deck_page(), which
    presses P and then clicks twice -- the "Deck" tab and the "Cards_All" school
    filter -- before anything is read.  Neither click looks necessary for a
    *read*:

        * Cards_All filters AllPageSpellList, the list of spells available to
          add.  CardsInDeck and ItemSpells are separate controls.
        * The Deck tab is less clear.  wizwalker's own window map annotates
          CardsInDeck as "spells added to normal deck (may also be used for
          tc)".  If the treasure card view reuses the same control, a client
          left on that tab would read its treasure cards as the normal deck,
          and _deck_already_matches would silently compare against the wrong
          thing.

    open_deck_page also fetches "btnSpellbook" into a local and never uses it
    (it sends Keycode.P instead), and wraps that keypress in
    `async with client.mouse_handler`, activating and deactivating the mouseless
    cursor hook for an operation that never touches the mouse.  Both are
    measured here.

    If a bare P is sufficient, the no-op deck check gets much cheaper: press P,
    read two memory vectors, press P again.  The clicks would still be needed
    for the *write* path, where the school filter genuinely determines which
    spells AllPageSpellList offers.

HOW TO RUN
    Start with the deck page CLOSED on every client, then:

        uv run python tools/probe_bare_deck_open.py

    Sections A-D are safe: they read, and the only clicks are a school tab
    change that section D puts back.

    Section E is opt-in because it deliberately enters the treasure card view,
    which is documented (bartlebot/combat/deck_io.py module docstring) to leave the
    spell list in a state that breaks normal card restore:

        uv run python tools/probe_bare_deck_open.py --tc-test

    It restores the state afterwards with a close/reopen, which is what
    refresh_deck_page() does and what the old code relied on.  Do not run it
    with a bot about to start.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

from wizwalker import Keycode
from wizwalker.extensions.scripting.utils import _maybe_get_named_window
from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot.combat.deck_io import active_item_counts, count_names, read_deck_card_names, read_item_slots

SAMPLES = 5

# How long to keep polling for DeckConfiguration after pressing P, and how often.
OPEN_TIMEOUT = 5.0
OPEN_POLL = 0.02


async def _deck_window(client, retries: int = 0):
    """Return the DeckConfiguration window, or None if it is not open."""
    try:
        return await _maybe_get_named_window(client.root_window, "DeckConfiguration", retries=retries)
    except ValueError:
        return None


async def _press_p(client, *, with_mouse_handler: bool) -> None:
    if with_mouse_handler:
        async with client.mouse_handler:
            await client.send_key(Keycode.P)
    else:
        await client.send_key(Keycode.P)


async def _bare_open(client) -> tuple[object | None, float]:
    """Press P and poll until DeckConfiguration exists. Returns (window, seconds)."""
    start = time.perf_counter()
    existing = await _deck_window(client)
    if existing is not None:
        return existing, time.perf_counter() - start

    await _press_p(client, with_mouse_handler=False)
    while time.perf_counter() - start < OPEN_TIMEOUT:
        window = await _deck_window(client)
        if window is not None:
            return window, time.perf_counter() - start
        await asyncio.sleep(OPEN_POLL)
    return None, time.perf_counter() - start


async def _bare_close(client) -> None:
    if await _deck_window(client) is not None:
        await _press_p(client, with_mouse_handler=False)


async def _read_contents(client, deck_window) -> tuple[dict[str, int], dict[str, int]]:
    normal = count_names(await read_deck_card_names(client, deck_window))
    items = active_item_counts(await read_item_slots(client, deck_window))
    return normal, items


def _fmt(counts: dict[str, int]) -> str:
    if not counts:
        return "(empty)"
    return ", ".join(f"{name} x{n}" for name, n in sorted(counts.items()))


async def section_a(client) -> tuple[dict, dict] | None:
    """Read via the full DeckBuilder open. This is the reference result."""
    from wizwalker.extensions.scripting.deck_builder import DeckBuilder  # noqa: PLC0415

    print("A. REFERENCE READ (full open_deck_page, both clicks)")
    print()
    start = time.perf_counter()
    async with DeckBuilder(client):
        deck_window = await _deck_window(client, retries=4)
        if deck_window is None:
            print("   !! DeckConfiguration never appeared.")
            return None
        contents = await _read_contents(client, deck_window)
        opened = time.perf_counter() - start
    total = time.perf_counter() - start

    normal, items = contents
    print(f"   normal deck : {_fmt(normal)}")
    print(f"   item cards  : {_fmt(items)}")
    print()
    print(f"   open+read {opened:.2f}s, with close {total:.2f}s")
    print()
    return contents


async def section_b(client, reference) -> tuple[dict, dict] | None:
    """Read after a bare P, no clicks at all."""
    print("B. BARE READ (press P, read, press P)")
    print()
    start = time.perf_counter()
    deck_window, open_secs = await _bare_open(client)
    if deck_window is None:
        print(f"   !! DeckConfiguration never appeared after {OPEN_TIMEOUT:.0f}s of polling.")
        return None
    contents = await _read_contents(client, deck_window)
    read_done = time.perf_counter() - start
    await _bare_close(client)
    total = time.perf_counter() - start

    normal, items = contents
    print(f"   normal deck : {_fmt(normal)}")
    print(f"   item cards  : {_fmt(items)}")
    print()
    print(f"   window appeared after {open_secs * 1000:.0f}ms")
    print(f"   open+read {read_done:.2f}s, with close {total:.2f}s")
    print()
    if reference is None:
        print("   No reference read to compare against.")
        print()
        return contents
    if contents == reference:
        print("   >> MATCHES the reference read. The two clicks are not needed to read.")
    else:
        print("   >> DIFFERS from the reference read:")
        if normal != reference[0]:
            print(f"        normal  reference={_fmt(reference[0])}")
            print(f"                bare     ={_fmt(normal)}")
        if items != reference[1]:
            print(f"        items   reference={_fmt(reference[1])}")
            print(f"                bare     ={_fmt(items)}")
        print("      A bare P is NOT sufficient; find out which click matters before")
        print("      changing the read path.")
    print()
    return contents


async def section_c(client) -> None:
    """How soon after P is the data actually readable, and what does the
    mouse_handler wrapper around the keypress cost?"""
    print("C. TIMING DETAIL")
    print()

    await _bare_close(client)
    await asyncio.sleep(0.5)

    appear = []
    for _ in range(SAMPLES):
        _window, secs = await _bare_open(client)
        appear.append(secs)
        await _bare_close(client)
        await asyncio.sleep(0.3)
    print(f"   P -> DeckConfiguration readable   median {statistics.median(appear) * 1000:>6.0f}ms")

    # The hook activate/deactivate that open_deck_page wraps its keypress in.
    hook = []
    for _ in range(SAMPLES):
        start = time.perf_counter()
        async with client.mouse_handler:
            pass
        hook.append(time.perf_counter() - start)
    print(f"   mouse_handler enter+exit (no-op)  median {statistics.median(hook) * 1000:>6.0f}ms")

    # The dead btnSpellbook lookup open_deck_page and close_deck_page both do.
    walk = []
    for _ in range(SAMPLES):
        start = time.perf_counter()
        await client.root_window.get_windows_with_name("btnSpellbook")
        walk.append(time.perf_counter() - start)
    print(f"   dead btnSpellbook lookup (x2)     median {statistics.median(walk) * 1000:>6.0f}ms")
    print()


async def section_d(client, reference) -> None:
    """Does a non-All school filter change what CardsInDeck reports?"""
    print("D. SCHOOL FILTER INDEPENDENCE")
    print()
    deck_window, _ = await _bare_open(client)
    if deck_window is None:
        print("   !! could not open the deck page.")
        print()
        return

    try:
        fire_tab = await _maybe_get_named_window(deck_window, "Cards_Fire", retries=0)
    except ValueError:
        print("   Cards_Fire not found; skipping.")
        print()
        await _bare_close(client)
        return

    async with client.mouse_handler:
        await client.mouse_handler.click_window(fire_tab)
    await asyncio.sleep(0.3)

    contents = await _read_contents(client, deck_window)

    # Put the filter back where the write path expects it.
    try:
        all_tab = await _maybe_get_named_window(deck_window, "Cards_All", retries=0)
        async with client.mouse_handler:
            await client.mouse_handler.click_window(all_tab)
        await asyncio.sleep(0.3)
    except ValueError:
        print("   (could not restore the Cards_All filter)")

    await _bare_close(client)

    print(f"   with Fire filter: {_fmt(contents[0])}")
    print()
    if reference is None:
        print("   No reference read to compare against.")
    elif contents == reference:
        print("   >> Unchanged. The school filter does not affect the deck read,")
        print("      so Cards_All is only needed for the write path.")
    else:
        print("   >> CHANGED. The school filter does affect the read; Cards_All is")
        print("      required before reading after all.")
    print()


async def section_e(client, reference) -> None:
    """Does the treasure card view make CardsInDeck report treasure cards?"""
    print("E. TREASURE CARD TAB HAZARD")
    print()
    deck_window, _ = await _bare_open(client)
    if deck_window is None:
        print("   !! could not open the deck page.")
        print()
        return

    try:
        tc_button = await _maybe_get_named_window(deck_window, "TreasureCardButton", retries=0)
    except ValueError:
        print("   TreasureCardButton not found; skipping.")
        print()
        await _bare_close(client)
        return

    async with client.mouse_handler:
        await client.mouse_handler.click_window(tc_button)
    await asyncio.sleep(0.5)

    contents = await _read_contents(client, deck_window)
    print(f"   CardsInDeck on the TC tab: {_fmt(contents[0])}")
    print()
    if reference is None:
        print("   No reference read to compare against.")
    elif contents[0] == reference[0]:
        print("   >> Same as the normal deck. CardsInDeck is not reused by the TC")
        print("      view, so the Deck tab click is not needed to read safely.")
    else:
        print("   >> DIFFERENT. CardsInDeck reports treasure cards on this tab, so a")
        print("      client left on the TC view would be misread. Reading without the")
        print("      Deck tab click needs a guard that the deck tab is selected.")
    print()

    # Restore: close and reopen, which is what refresh_deck_page did and what
    # the old code depended on to undo exactly this.
    print("   Restoring (close + reopen + Cards_All)...")
    await _bare_close(client)
    await asyncio.sleep(0.5)
    from wizwalker.extensions.scripting.deck_builder import DeckBuilder  # noqa: PLC0415

    async with DeckBuilder(client):
        pass
    print("   done.")
    print()


async def probe(client, tc_test: bool) -> None:
    reference = await section_a(client)
    await section_b(client, reference)
    await section_c(client)
    await section_d(client, reference)
    if tc_test:
        await section_e(client, reference)
    else:
        print("E. TREASURE CARD TAB HAZARD -- skipped (pass --tc-test to run it)")
        print()
    await _bare_close(client)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tc-test",
        action="store_true",
        help="also run section E, which enters the treasure card view",
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
        for i, client in enumerate(clients, start=1):
            print()
            print("=" * 88)
            print(f"CLIENT {i}")
            print("=" * 88)
            print()
            await probe(client, args.tc_test)
    finally:
        await asyncio.gather(*[c.close() for c in clients], return_exceptions=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
