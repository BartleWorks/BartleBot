"""Diagnostic: find the deck page's scroll index, and test whether writing it turns the page (#48).

WHY THIS EXISTS
    Restoring a deck clicks PageDown once per spellbook page, 0.1 s a click, and
    ItemSpells' Next once per item page at 0.3 s.  On a 20+ page spellbook that
    is most of the apply.  wizwalker's ``DeckBuilder.set_page()`` used to skip it
    by writing ``page * 6`` straight into the list control's first-visible index.

    It broke because the offset went stale, not because the idea did.  The
    index has sat 4 bytes before ``m_cardSize`` in both builds it worked in:

        build      index    m_cardSize
        2024       0x2C0    0x2C4
        r794561    0x308    0x30C
        r800294    0x388    0x38C   (bartlebot/game/data/r800294_WizardDev.json; confirmed below)

    r800294 shifted the class by 0x80, leaving the write on 0x308 -- now the
    entry vector's end pointer, which is why it crashed the client and why
    wizwalker commit eb233f7 replaced it with button clicks.  0x388 is the
    candidate for SpellListControl.  ItemSpells is a DeckListControl, whose
    entry vector is 0x300-0x318 and whose m_cardSize is 0x324, so a page field
    there, if any, is likely 0x31C or 0x320.

    None of that is confirmed, and two separate things have to be:

      1. WHERE the index lives        -- scan mode, the default
      2. WHETHER writing it is enough -- --write OFFSET

    The second can fail on its own: the field may only drive what is drawn while
    the game tracks the page somewhere else, in which case clicks land on the
    wrong cards and the next PageDown jumps back.

FINDINGS (r800294, AllPageSpellList, 12-page spellbook)
    * 0x388 is the only field in 0x240-0x400 that follows the page, as page * 6.
      The entry vector does not move while paging.
    * Writing it turns the page: the game draws it, keeps the value, a click
      lands on the predicted card, and PageDown continues from it.
    * The game also keeps a second page counter that only the buttons update,
      outside the scanned range.  Two symptoms: PageUp still reads grayed on a
      written page 11, and closing a tier flyout restores the list from that
      counter -- to page 1 after a write, but to the right page after PageDown.
      deck_io's _close_tier_flyout ("the close button leaves the page alone")
      is therefore correct for the click-driven sweep it was written for.
    * Writing 0x388 again after the close recovers fully.  So a write-driven
      sweep must re-write the page after every flyout close, and must not rely
      on the page buttons' grayed state.
    * ItemSpells (18 cards, 2 pages): 0x320 is the only field that follows the
      page, as page * 16.  Writing 16 draws page 2 and a click toggles entry 16.
    * The buttons are enabled by the hidden counter, not by 0x388 / 0x320.
      After writing page 2, PrevItemSpells stayed grayed and a click on it did
      nothing; PageDown from a written spell page worked only because the hidden
      counter still said page 1, where PageDown is enabled.  So a write-driven
      sweep must never mix in button clicks -- move by writing, including back
      to page 1.
    * --odd and --past-end untested; write only whole pages within the list.

HOW TO RUN
    1. Log a wizard in, press P, and stay on the Deck tab with All cards shown.
       Use a wizard whose spellbook has at least 4 pages.
    2. Scan (clicks page buttons, never writes memory):
           uv run python tools/probe_deck_page_index.py
           uv run python tools/probe_deck_page_index.py --list items
    3. Write test, with the offset the scan reported:
           uv run python tools/probe_deck_page_index.py --write 0x388
           uv run python tools/probe_deck_page_index.py --list items --write 0x320
       It asks you to look at the game at a few points, so keep it visible.

    --write adds one card to the deck and removes it again (spells), or toggles
    one item card and toggles it back (items).  It asks before clicking.  With a
    full deck the add cannot land; that is reported, not an error.  --slot picks
    which card on the page to click (default 1).

    A multi-path spell opens its variant flyout instead of adding a card.  That
    counts too: the flyout's variants must share the expected spell's base name,
    and the flyout is closed with its own button -- never by reopening the deck
    page, which would reset it to page 1 -- before 0x388 is re-read, which also
    shows whether closing a flyout keeps the page.

    After a written page, closing a flyout has been seen to reset it to page 1.
    If the page moved, the probe writes it again and checks that recovers it.
    --via-buttons reaches the page with PageDown instead of a write, as a control:
    if the reset happens there too it is the game's behavior on every close
    (and deck_io's _close_tier_flyout, which assumes the page is kept, is wrong);
    if not, the game restores the page from a copy the write does not update.

    --odd also writes an index that is not a whole page.  --past-end writes one
    a page beyond the last, and MAY CRASH THE CLIENT if the game indexes the
    entry vector with it unchecked; that is the question it answers.

Reads only list structure and spell template names -- no wizard or account data.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass

from wizwalker.extensions.scripting.deck_builder import DeckBuilder
from wizwalker.extensions.scripting.utils import _maybe_get_named_window
from wizwalker.extensions.wizsprinter import WizSprinter
from wizwalker.memory.memory_object import DynamicMemoryObject, Primitive
from wizwalker.memory.memory_objects.spell import DynamicGraphicalSpell

from bartlebot.combat.deck_io import parse_tiered_name
from bartlebot.nodes.timing import DECK_ITEM_PAGE_SETTLE, DECK_SPELL_PAGE_SETTLE

SCAN_START = 0x240
SCAN_END = 0x400
ENTRY_VECTOR = 0x300
ACTIVE_FLAG_OFFSET = 0x15  # DeckListControl entry: 0x00 on, 0x01 off (see deck_io)
READBACK_DELAYS = (0.0, 0.25, 1.0)
CONFIRM_TIMEOUT = 2.0
CONFIRM_POLL = 0.05


@dataclass(frozen=True)
class ListSpec:
    window: str
    prev_button: str
    next_button: str
    stride: int  # entry size in the list's vector
    step: int  # entries per page
    settle: float


LISTS = {
    "spells": ListSpec("AllPageSpellList", "PageUp", "PageDown", 0x78, 6, DECK_SPELL_PAGE_SETTLE),
    "items": ListSpec("ItemSpells", "PrevItemSpells", "NextItemSpells", 0x28, 16, DECK_ITEM_PAGE_SETTLE),
}


async def _safe(coro, default=None):
    try:
        return await coro
    except Exception:
        return default


def _ask(prompt: str) -> bool:
    try:
        return input(f"   ?? {prompt} [y/n] ").strip().lower().startswith("y")
    except EOFError:
        return False


class Probe:
    def __init__(self, client, spec: ListSpec):
        self.client = client
        self.spec = spec
        self.db = DeckBuilder(client)  # only its rectangle helpers; never opened
        self.deck_window = None
        self.control = None

    async def attach(self) -> bool:
        try:
            self.deck_window = await _maybe_get_named_window(self.client.root_window, "DeckConfiguration")
        except ValueError:
            print("  !! DeckConfiguration not found -- open the deck page (press P) first.")
            return False
        try:
            window = await _maybe_get_named_window(self.deck_window, self.spec.window)
        except ValueError:
            print(f"  !! {self.spec.window} not found -- is the Deck tab showing All cards?")
            return False
        self.control = DynamicMemoryObject(self.client.hook_handler, await window.read_base_address())
        # The DeckBuilder is never opened (that would press P and close the page), so
        # its list reads have no deck window until one of its rectangle helpers finds it.
        await self.db.get_spell_list_rectangle()
        return True

    # --- reads -------------------------------------------------------------

    async def vector(self) -> tuple[int, int, int]:
        begin = await self.control.read_value_from_offset(ENTRY_VECTOR, Primitive.uint64)
        end = await self.control.read_value_from_offset(ENTRY_VECTOR + 8, Primitive.uint64)
        count = (end - begin) // self.spec.stride if begin and end > begin else 0
        return begin, end, count

    async def pages(self) -> int:
        _begin, _end, count = await self.vector()
        return max(1, -(-count // self.spec.step))

    async def int_at(self, offset: int) -> int:
        return await self.control.read_value_from_offset(offset, Primitive.int32)

    async def snapshot(self) -> bytes:
        return await self.control.read_bytes(await self.control.read_base_address() + SCAN_START, SCAN_END - SCAN_START)

    async def entry_name(self, list_begin: int, index: int, stride: int) -> str:
        addr = await _safe(self.control.read_typed(list_begin + index * stride, Primitive.uint64), 0)
        if not addr:
            return "(null entry)"
        template = await _safe(DynamicGraphicalSpell(self.client.hook_handler, addr).spell_template())
        name = await _safe(template.name()) if template else None
        return name or "(no template)"

    async def button_states(self) -> str:
        parts = []
        for name in (self.spec.prev_button, self.spec.next_button):
            try:
                button = await _maybe_get_named_window(self.deck_window, name)
            except ValueError:
                parts.append(f"{name}=missing")
                continue
            parts.append(f"{name} grayed={await _safe(button.is_control_grayed(), '?')}")
        return ", ".join(parts)

    # --- clicks ------------------------------------------------------------

    async def click_button(self, name: str) -> None:
        button = await _maybe_get_named_window(self.deck_window, name)
        async with self.client.mouse_handler:
            await self.client.mouse_handler.click_window(button)
        await asyncio.sleep(self.spec.settle)

    async def click_at(self, x: int, y: int) -> None:
        # Held per click, never across a prompt: while the handler is held the game
        # follows wizwalker's cursor and ignores the real mouse.
        async with self.client.mouse_handler:
            await self.client.mouse_handler.click(x, y)

    async def rewind(self) -> None:
        # Prev on the first page is a no-op, so a fixed count needs no grayed read.
        # One hold for the run of clicks; the handler is ref-counted.
        async with self.client.mouse_handler:
            for _ in range(await self.pages() - 1):
                await self.click_button(self.spec.prev_button)


# --- scan --------------------------------------------------------------------


def _ints(blob: bytes) -> dict[int, int]:
    return {SCAN_START + i: int.from_bytes(blob[i : i + 4], "little", signed=True) for i in range(0, len(blob) - 3, 4)}


async def scan(probe: Probe) -> None:
    spec = probe.spec
    begin, end, count = await probe.vector()
    pages = await probe.pages()
    print(f"A. {spec.window}: {count} entries, {pages} page(s) at {spec.step} per page")
    print(f"   vector 0x{ENTRY_VECTOR:03x} begin=0x{begin:012x} end=0x{end:012x}")
    print()
    if pages < 2:
        print("   !! Need at least 2 pages to tell an index from a constant. Use a fuller spellbook.")
        return

    top = min(pages - 1, 3)
    path = list(range(top + 1)) + list(range(top - 1, -1, -1))  # 0 1 2 3 2 1 0
    print(f"   rewinding, then visiting pages {' '.join(map(str, path))} with the buttons...")
    await probe.rewind()

    samples: list[tuple[int, dict[int, int]]] = []
    current = 0
    for page in path:
        while current < page:
            await probe.click_button(spec.next_button)
            current += 1
        while current > page:
            await probe.click_button(spec.prev_button)
            current -= 1
        samples.append((page, _ints(await probe.snapshot())))
    print()

    exact, page_numbers, tracking = [], [], []
    for offset in samples[0][1]:
        values = [(page, ints[offset]) for page, ints in samples]
        if len({v for _, v in values}) < 2:
            continue
        by_page: dict[int, set[int]] = {}
        for page, value in values:
            by_page.setdefault(page, set()).add(value)
        if any(len(v) > 1 for v in by_page.values()):
            continue  # changes without the page changing: noise, not a page field
        if all(value == page * spec.step for page, value in values):
            exact.append(offset)
        elif all(value == page for page, value in values):
            page_numbers.append(offset)
        else:
            tracking.append((offset, [v for _, v in values]))

    print("B. FIELDS THAT FOLLOW THE PAGE")
    print()
    print(f"   value == page * {spec.step} (first visible entry) -- the write target:")
    for offset in exact:
        print(f"      0x{offset:03x}")
    print("      (none)" if not exact else "")
    print("   value == page number:")
    for offset in page_numbers:
        print(f"      0x{offset:03x}")
    print("      (none)" if not page_numbers else "")
    print("   depends on the page some other way (scroll pixels, pointers into the vector):")
    for offset, seq in tracking:
        print(f"      0x{offset:03x}  {seq}")
    print("      (none)" if not tracking else "")
    print()

    begin2, end2, _ = await probe.vector()
    moved = (begin2, end2) != (begin, end)
    print(f"   entry vector {'MOVED while paging' if moved else 'unchanged while paging'} -- ", end="")
    print("positions cached before paging go stale" if moved else "safe to precompute positions")
    print()
    print("   Next: --write <offset> with a value from the first list.")


# --- write test --------------------------------------------------------------


async def _readback(probe: Probe, offset: int, wrote: int) -> None:
    elapsed = 0.0
    for delay in READBACK_DELAYS:
        await asyncio.sleep(delay - elapsed)
        elapsed = delay
        value = await _safe(probe.int_at(offset), "?")
        note = "" if value == wrote else "  <-- game changed it"
        print(f"      after {delay:.2f}s: {value}{note}")


async def _deck_names(probe: Probe) -> list[str]:
    window = await _maybe_get_named_window(probe.deck_window, "CardsInDeck")
    deck = DynamicMemoryObject(probe.client.hook_handler, await window.read_base_address())
    begin = await deck.read_value_from_offset(ENTRY_VECTOR, Primitive.uint64)
    end = await deck.read_value_from_offset(ENTRY_VECTOR + 8, Primitive.uint64)
    if not begin or end <= begin:
        return []
    return [await probe.entry_name(begin, i, 0x28) for i in range((end - begin) // 0x28)]


async def _poll(read, predicate):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + CONFIRM_TIMEOUT
    while True:
        value = await _safe(read())
        if value is not None and predicate(value):
            return value, True
        if loop.time() >= deadline:
            return value, False
        await asyncio.sleep(CONFIRM_POLL)


async def _flyout_open(probe: Probe) -> bool:
    """True while the tier flyout is showing.

    Visibility, not whether its variants read: the window stays in the tree once
    closed, still holding the last spell's variants.
    """
    try:
        window = await _maybe_get_named_window(probe.deck_window, "TieredSpellMPUnlockedList")
    except ValueError:
        return False
    return bool(await _safe(window.is_visible(), False))


async def _flyout_variants(probe: Probe) -> list[str]:
    """Names held by the tier flyout's list -- stale once it closes, so check _flyout_open first."""
    try:
        spells = await probe.db.get_graphical_tiered_spell_cards()
    except ValueError:
        return []
    names = []
    for gfx in spells:
        template = await _safe(gfx.spell_template()) if gfx else None
        name = await _safe(template.name()) if template else None
        if name:
            names.append(name)
    return names


async def _close_flyout(probe: Probe, offset: int, wrote: int) -> None:
    # Never the refresh_deck_page() fallback deck_io uses: reopening the page
    # resets it to page 1 and would wreck section D.
    try:
        close_btn = await _maybe_get_named_window(probe.deck_window, "CloseTSMPUnlockedPageButton")
    except ValueError:
        print("   !! flyout close button not found -- close the flyout by hand before answering the next prompt")
        _ask("Closed it?")
        return
    async with probe.client.mouse_handler:
        await probe.client.mouse_handler.click_window(close_btn)
    _open, closed = await _poll(lambda: _flyout_open(probe), lambda is_open: not is_open)
    print("   closed the flyout" if closed else "   !! flyout still reads visible -- close it by hand if it is")
    value = await _safe(probe.int_at(offset), "?")
    kept = "page kept" if value == wrote else "<-- the page moved"
    print(f"   0x{offset:03x} after close: {value}  {kept}")


async def _click_test_spells(probe: Probe, target_index: int, slot: int, offset: int, wrote: int) -> str:
    begin, _end, _count = await probe.vector()
    expected = await probe.entry_name(begin, target_index, probe.spec.stride)
    # wizwalker's own list drops unreadable entries, so its indices may not match the vector's.
    names = []
    for gfx in await probe.db.get_graphical_spell_cards():
        template = await _safe(gfx.spell_template())
        names.append(await _safe(template.name(), "") if template else "")
    filtered_name = names[target_index] if target_index < len(names) else "(past end)"
    print(f"   raw vector entry {target_index}: {expected}")
    print(f"   wizwalker's filtered list [{target_index}]: {filtered_name}")
    if expected != filtered_name:
        print("   !! these differ: calcuate_position_of_card_in_page indexes the filtered list,")
        print("      so a page computed from it would be off for this spellbook.")

    print(f"   The probe will now click card {slot} itself: it adds one '{expected}' and")
    print("   removes it again, or opens and closes its flyout.")
    if not _ask("Go ahead? (n skips this section)"):
        return "skipped"
    before = await _deck_names(probe)
    if await _flyout_open(probe):
        print("   !! a flyout was already open before the click -- close it and re-run. Inconclusive.")
        return "inconclusive"
    x, y = await probe.db.calculate_card_position(slot)
    await probe.click_at(x, y)

    # A multi-path spell opens its variant flyout instead of adding a card, and
    # which one happens cannot be told from the spellbook -- so wait for either.
    async def outcome() -> tuple[list[str], list[str]]:
        variants = await _flyout_variants(probe) if await _flyout_open(probe) else []
        return await _deck_names(probe), variants

    state, landed = await _poll(outcome, lambda s: len(s[0]) > len(before) or bool(s[1]))
    after, variants = state if state else ([], [])
    if variants:
        bases = sorted({parse_tiered_name(v).base for v in variants})
        want = parse_tiered_name(expected).base
        verdict = "PASS" if bases == [want] else "FAIL"
        print(f"   {verdict}: opened the flyout for {bases}, expected ['{want}']  (variants: {variants})")
        # Read before closing, so a reset can be pinned on the open or on the close.
        print(f"   0x{offset:03x} with the flyout open: {await _safe(probe.int_at(offset), '?')}")
        await _close_flyout(probe, offset, wrote)
        return verdict.lower()
    if not landed:
        print("   !! deck did not grow and no flyout opened -- full deck, or the click missed. Inconclusive.")
        return "inconclusive"
    gained = [n for n in set(after) if after.count(n) > before.count(n)]
    verdict = "PASS" if gained == [expected] else "FAIL"
    print(f"   {verdict}: added {gained}, expected ['{expected}']")

    index = after.index(gained[0])
    x, y = await probe.db.calculate_deck_card_position(index + 1)
    await probe.click_at(x, y)
    _final, restored = await _poll(lambda: _deck_names(probe), lambda names: len(names) == len(before))
    print("   removed it again" if restored else "   !! could NOT remove it -- take it out of the deck by hand")
    return verdict.lower()


async def _click_test_items(probe: Probe, target_index: int, slot_number: int) -> str:
    begin, _end, count = await probe.vector()
    stride = probe.spec.stride
    name = await probe.entry_name(begin, target_index, stride)

    # Every flag, not just the target's: if the write did not really turn the
    # page, the click toggles some other card, and that one must be undone too.
    async def flags() -> list[int]:
        return [(await probe.control.read_bytes(begin + i * stride + ACTIVE_FLAG_OFFSET, 1))[0] for i in range(count)]

    start = await flags()
    print(f"   raw vector entry {target_index}: {name}  ({'on' if start[target_index] == 0 else 'off'})")
    print(f"   The probe will now click item slot {slot_number} itself: it toggles '{name}'")
    print("   and toggles it back.")
    if not _ask("Go ahead? (n skips this section)"):
        return "skipped"
    rect = await probe.db.get_item_spells_rectangle()
    slot = probe.db.divide_rectangle(rect, columns=8, rows=2)[slot_number - 1].center()
    await probe.click_at(*slot)
    now, toggled = await _poll(flags, lambda v: v != start)
    if not toggled:
        print("   !! no item card changed -- the click missed. Inconclusive.")
        return "inconclusive"
    changed = [i for i in range(count) if now[i] != start[i]]
    verdict = "PASS" if changed == [target_index] else "FAIL"
    print(f"   {verdict}: toggled entries {changed}, expected [{target_index}]")
    await probe.click_at(*slot)
    _value, restored = await _poll(flags, lambda v: v == start)
    print("   toggled back" if restored else "   !! could NOT toggle it back -- fix it by hand")
    return verdict.lower()


async def write_test(
    probe: Probe, offset: int, target: int | None, slot: int, via_buttons: bool, odd: bool, past_end: bool
) -> None:
    spec = probe.spec
    pages = await probe.pages()
    if pages < 2:
        print("   !! Need at least 2 pages, or there is nothing to write.")
        return
    if not 1 <= slot <= spec.step:
        print(f"   !! --slot must be 1-{spec.step} for {spec.window}.")
        return
    # Section D needs a page after the written one; with only 2 pages it is skipped.
    last = pages - 2 if pages >= 3 else pages - 1
    target = max(1, min(target if target is not None else last, last))
    print(f"A. REWIND ({pages} pages)")
    await probe.rewind()
    print(f"   0x{offset:03x} on page 1 reads {await probe.int_at(offset)} (expect 0)")
    print(f"   buttons: {await probe.button_states()}")
    print()

    wrote = target * spec.step
    redrew = None
    if via_buttons:
        # Control run: the page the game reaches on its own, to compare the write against.
        print(f"B. PAGE {target + 1} VIA {spec.next_button} (no write)")
        async with probe.client.mouse_handler:
            for _ in range(target):
                await probe.click_button(spec.next_button)
        print(f"   0x{offset:03x} reads {await _safe(probe.int_at(offset), '?')} (expect {wrote})")
    else:
        print(f"B. WRITE {wrote} (page {target + 1}) to 0x{offset:03x}")
        await probe.control.write_value_to_offset(offset, wrote, Primitive.int32)
        await _readback(probe, offset, wrote)
        print(f"   buttons: {await probe.button_states()}")
        redrew = _ask(f"Does the game now show page {target + 1} of the {spec.window} cards?")
    print()

    print("C. CLICK LANDS ON THE PREDICTED CARD")
    if spec is LISTS["spells"]:
        clicked = await _click_test_spells(probe, wrote + slot - 1, slot, offset, wrote)
    else:
        clicked = await _click_test_items(probe, wrote + slot - 1, slot)
    print()

    # A flyout close has been seen to reset the page.  Recovering with a write
    # is the #48 answer to that, so test it rather than stop.
    after_click = await _safe(probe.int_at(offset), "?")
    page_reset = after_click != wrote
    rewrite_redrew = None
    if page_reset:
        print(f"C2. PAGE WAS RESET TO {after_click} -- RECOVER BY WRITING {wrote}")
        await probe.control.write_value_to_offset(offset, wrote, Primitive.int32)
        await _readback(probe, offset, wrote)
        rewrite_redrew = _ask(f"Does the game show page {target + 1} again?")
        print()

    print(f"D. {spec.next_button} FROM PAGE {target + 1}")
    followed = None
    if target + 1 >= pages:
        print(f"   skipped: page {target + 1} is the last, so there is no next page to test. Needs 3+ pages.")
    else:
        base = await _safe(probe.int_at(offset), "?")
        await probe.click_button(spec.next_button)
        value = await _safe(probe.int_at(offset), "?")
        if isinstance(base, int) and value == base + spec.step:
            verdict = f"continues from {base} -- this value is the real page state"
        elif value == spec.step:
            verdict = "jumped back to page 2 -- the game keeps its own page; writing is NOT enough"
        else:
            verdict = "unexpected"
        print(f"   0x{offset:03x} read {base} before, {value} after: {verdict}")
        followed = _ask(f"Does the game now show page {target + 2}?")
    print()

    if odd:
        odd_value = wrote + spec.step // 2
        print(f"E. NON-PAGE INDEX {odd_value}")
        await probe.control.write_value_to_offset(offset, odd_value, Primitive.int32)
        await _readback(probe, offset, odd_value)
        _ask("Does the list show a half-shifted page (cards from two pages)?")
        print()

    if past_end:
        beyond = pages * spec.step
        print(f"F. PAST-END INDEX {beyond}  (may crash the client)")
        if _ask("Really write it?"):
            await probe.control.write_value_to_offset(offset, beyond, Primitive.int32)
            await _readback(probe, offset, beyond)
            _ask("Is the client still running and the list sane?")
        print()

    print("G. RESTORE")
    await probe.rewind()
    rewound = await _safe(probe.int_at(offset), "?")
    print(f"   0x{offset:03x} reads {rewound} after rewinding")
    if rewound != 0:
        # The button-only page counter can still say page 1 after a write, so Prev may do nothing.
        print(f"   !! {spec.prev_button} did not reach page 1 from a written page -- writing 0 instead")
        await probe.control.write_value_to_offset(offset, 0, Primitive.int32)
        print(f"   0x{offset:03x} reads {await _safe(probe.int_at(offset), '?')}")
    print()
    print("SUMMARY")
    print(f"   page reached by:  {'buttons' if via_buttons else 'write'}")
    if not via_buttons:
        print(f"   redraw on write:  {'yes' if redrew else 'no'}")
    print(f"   click lands:      {clicked}")
    print(f"   page after click: {f'reset (read {after_click})' if page_reset else 'kept'}")
    if page_reset:
        print(f"   rewrite redraws:  {'yes' if rewrite_redrew else 'no'}")
    print(f"   next continues:   {'skipped (needs 3+ pages)' if followed is None else 'yes' if followed else 'no'}")
    print(f"   prev rewinds:     {'yes' if rewound == 0 else f'no (read {rewound})'}")
    usable = (
        (via_buttons or redrew) and followed is not False and clicked == "pass" and (not page_reset or rewrite_redrew)
    )
    partial = " (next-page check skipped)" if usable and followed is None else ""
    print(f"   Usable for #48:   {'yes' + partial if usable else 'not yet -- every line above must be yes/pass'}")


# --- entry -------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", choices=sorted(LISTS), default="spells")
    parser.add_argument("--write", type=lambda s: int(s, 0), metavar="OFFSET", help="run the write test at OFFSET")
    parser.add_argument("--page", type=int, help="0-based page to write (default: second-to-last)")
    parser.add_argument("--slot", type=int, default=1, help="1-based card on the page to click (default 1)")
    parser.add_argument(
        "--via-buttons", action="store_true", help="control run: reach the page with PageDown instead of a write"
    )
    parser.add_argument("--odd", action="store_true", help="also write an index that is not a whole page")
    parser.add_argument("--past-end", action="store_true", help="also write past the last page (may crash)")
    parser.add_argument("--client", type=int, default=1, help="1-based client to probe (default 1)")
    args = parser.parse_args()

    clients = WizSprinter().get_new_clients()
    if not clients:
        print("No Wizard101 clients found. Start the game and log a wizard in.")
        return 1
    if not 1 <= args.client <= len(clients):
        print(f"--client {args.client} out of range; {len(clients)} client(s) found.")
        return 1
    client = clients[args.client - 1]

    print(f"Found {len(clients)} client(s); probing client {args.client}. Activating hooks...")
    try:
        await client.activate_hooks()
    except Exception as exc:
        print(f"Hook activation failed: {exc!r}")
        return 1

    try:
        probe = Probe(client, LISTS[args.list])
        if not await probe.attach():
            return 1
        print()
        if args.write is None:
            await scan(probe)
        else:
            await write_test(probe, args.write, args.page, args.slot, args.via_buttons, args.odd, args.past_end)
    finally:
        for c in clients:
            await _safe(c.close())
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
