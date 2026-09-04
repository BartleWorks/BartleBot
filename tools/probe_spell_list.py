"""Diagnostic: what does the spellbook actually call a tiered spell?

WHY THIS EXISTS
    Applying a deck containing "Novus Storm - T05 - B" reported the card as not
    on the account, on a wizard that owns it.  Deck restore matches a saved card
    against the spellbook list (``AllPageSpellList``) like this, in wizwalker's
    ``_add_all_normal_cards``:

        if name in spell_names:                      # exact match
            ...
        elif " - T" in name:
            base_name = name[:name.find(" - T")]      # "Novus Storm"
            if base_name not in spell_names:
                raise Exception("Base spell not found")

    So it assumes a tiered spell appears in the spellbook under its *bare base
    name*.  If this build instead lists it as, say, "Novus Storm - T01 - A",
    both branches miss and every tiered card looks unowned.

    This probe prints what the list really holds so the matching rule can be
    written against reality instead of that assumption.

    It also hunts for the tiered-variant window.  wizwalker opens
    "TieredSpellMPUnlockedList", but our wizwalker fork documents a
    ``SpellFusionListControl`` whose child window is "SpellFusionList"
    (DeckPage +0x2E0) — if the window was renamed, the tier flyout read is
    broken independently of the naming question.

HOW TO RUN
    1. Log a wizard in and press P to open the deck page.
    2. To also capture section C, first CLICK the tiered spell in the spellbook
       so its variant flyout is open, then run this without closing it.
    3. uv run python tools/probe_spell_list.py

    Run it on each client that behaves differently — the reported case had one
    wizard with paths A/B/C available and another with only B, which is exactly
    the kind of difference that should show up here.

Read-only: never clicks or writes, so it cannot disturb your deck.
"""

from __future__ import annotations

import asyncio
import sys

from wizwalker.extensions.scripting.utils import _maybe_get_named_window
from wizwalker.extensions.wizsprinter import WizSprinter
from wizwalker.memory.memory_objects.window import DynamicSpellListControl

# Names worth trying for the tiered/fusion variant list, best guess first.
TIERED_WINDOW_CANDIDATES = (
    "SpellFusionList",
    "TieredSpellMPUnlockedList",
    "TieredSpellList",
    "SpellFusionMPUnlockedList",
)


async def _safe(coro, default=None):
    try:
        return await coro
    except Exception:
        return default


async def _entry_names(client, parent, window_name: str) -> list[tuple[str, str, str]]:
    """Return (template.name, spell_base, display_name) for each entry, or []."""
    try:
        window = await _maybe_get_named_window(parent, window_name)
    except ValueError:
        return []
    control = DynamicSpellListControl(client.hook_handler, await window.read_base_address())
    entries = await _safe(control.spell_entries(), []) or []
    rows: list[tuple[str, str, str]] = []
    for entry in entries:
        gfx = await _safe(entry.graphical_spell())
        if not gfx:
            continue
        tmpl = await _safe(gfx.spell_template())
        if not tmpl:
            continue
        rows.append(
            (
                str(await _safe(tmpl.name(), "?")),
                str(await _safe(tmpl.spell_base(), "?")),
                str(await _safe(tmpl.display_name(), "?")),
            )
        )
    return rows


def _print_rows(rows: list[tuple[str, str, str]]) -> None:
    print(f"   {'#':>3}  {'template.name()':<34} {'spell_base()':<22} display_name()")
    print("   " + "-" * 86)
    for idx, (name, base, display) in enumerate(rows):
        print(f"   {idx:>3}  {name[:34]:<34} {base[:22]:<22} {display[:24]}")
    print()


async def section_a(client, deck_window) -> None:
    print("A. SPELLBOOK LIST (AllPageSpellList)")
    print()
    rows = await _entry_names(client, deck_window, "AllPageSpellList")
    if not rows:
        print("   !! no readable entries.")
        print("   If this is empty while the deck page is open, the read itself is")
        print("   broken — not the name matching — and plan_normal_cards would")
        print("   correctly-but-uselessly report every card as unowned.")
        print()
        return
    _print_rows(rows)
    print(f"   {len(rows)} entries.")
    tiered = [n for n, _, _ in rows if " - T" in n]
    print()
    if tiered:
        print("   >> Entries carrying a tier suffix:")
        for name in tiered:
            print(f"        {name}")
        print()
        print("   This is the failure: deck restore looks up the BARE base name")
        print("   ('Novus Storm'), which is not present, so the card is skipped.")
    else:
        print("   >> No entry carries a tier suffix, so tiered spells are listed")
        print("      under a bare base name and the matching assumption holds.")
        print("      In that case the problem is elsewhere — compare the base")
        print("      names above against the card your deck asks for.")
    print()


async def section_b(client, deck_window) -> None:
    print("B. TIERED-VARIANT WINDOW NAME")
    print()
    for candidate in TIERED_WINDOW_CANDIDATES:
        try:
            await _maybe_get_named_window(deck_window, candidate)
        except ValueError:
            print(f"   {candidate:<28} not found")
            continue
        print(f"   {candidate:<28} FOUND  <-- use this name")
    print()
    print("   wizwalker's get_tiered_spell_list() hard-codes")
    print("   'TieredSpellMPUnlockedList'. If only another name is found, the")
    print("   tier flyout read cannot work regardless of the naming question.")
    print()


async def section_c(client, deck_window) -> None:
    print("C. TIERED VARIANTS CURRENTLY SHOWN")
    print("   (only populated if you clicked the tiered spell before running)")
    print()
    any_found = False
    for candidate in TIERED_WINDOW_CANDIDATES:
        rows = await _entry_names(client, deck_window, candidate)
        if not rows:
            continue
        any_found = True
        print(f"   from {candidate}:")
        _print_rows(rows)
    if not any_found:
        print("   No variant list populated. Click the tiered spell in the")
        print("   spellbook first, leave the flyout open, and re-run.")
        print()
    else:
        print("   These are the names resolve_tier() gets to choose from, so the")
        print("   tier and path format here is what it must parse.")
        print()


async def probe(client) -> None:
    try:
        deck_window = await _maybe_get_named_window(client.root_window, "DeckConfiguration")
    except ValueError:
        print("  !! DeckConfiguration not found — open the deck page (press P) first.")
        return
    await section_a(client, deck_window)
    await section_b(client, deck_window)
    await section_c(client, deck_window)


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
            print("=" * 88)
            print(f"CLIENT {i}")
            print("=" * 88)
            print()
            await probe(client)
    finally:
        await asyncio.gather(*[c.close() for c in clients], return_exceptions=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
