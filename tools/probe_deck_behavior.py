"""Diagnostic: can the equipped deck be read *without* opening the deck window?

WHY THIS EXISTS
    Applying a saved deck is slow, and most of the cost is paid even when the
    deck is already correct: ``apply_deck`` opens the deck window (a keypress,
    two clicks, several full UI-tree walks), and only then can
    ``_deck_already_matches`` read ``CardsInDeck`` / ``ItemSpells`` to discover
    there was nothing to do.  Four clients, sequentially.

    Our wizwalker fork carries ``ClientDeckBehavior``
    (``wizwalker/memory/memory_objects/deck_behavior.py``), reachable as
    ``client.client_object.try_get_deck_behavior()``.  It reads the equipped
    deck *item's* stored configuration straight out of memory:

        spell_list()      -> SpellData entries: template_id, enchantment, quantity
        exclusion_list()  -> [(spell_id, quantity)] item cards toggled OFF
        archmastery_school()

    If that is readable and accurate with the deck window closed, the
    already-matches check costs a few memory reads instead of the whole UI
    dance, and the window only has to open for clients that genuinely differ.

    There is prior suspicion that reading deck data without opening the deck
    does NOT work.  This probe exists to settle that before anything is built
    on it.  It answers four separate questions, in an order that matters:

      A. Is the behavior reachable and populated with the deck NEVER opened
         this session?  (If it only populates after the UI has been shown once,
         the fast path is worthless — that is the suspected failure.)
      B. With the window then opened, does the behavior agree with the
         authoritative ``CardsInDeck`` / ``ItemSpells`` reads?
      C. Does the serialized exclusion list correspond to the OFF item cards?
      D. After closing the window again, does the behavior still read the same?
         (Catches "valid only while the window is alive".)
      E. Does the LIVE exclusion map at +0x88 correspond to the OFF item cards?
         A/B/D have already confirmed the normal deck reads correctly with the
         window closed, and C has shown the *serialized* list at +0x98 to be
         stale.  E exists because the live map is the remaining candidate for
         reading item toggles without the UI.

    Section E is only meaningful with some item cards toggled ON and others
    OFF — with everything off, a live map and a historical record look
    identical.

HOW TO RUN
    1. Log a wizard in on each client.  Leave the deck page CLOSED — section A
       is only meaningful if the deck window has not been opened this session.
    2. uv run python tools/probe_deck_behavior.py

    Section A is read-only.  Sections B/C open the deck page and close it
    again; no cards are added, removed or toggled, so your deck is not
    modified.
"""

from __future__ import annotations

import asyncio
import sys

from wizwalker.extensions.scripting.utils import _maybe_get_named_window
from wizwalker.extensions.wizsprinter import WizSprinter
from wizwalker.memory.memory_object import Primitive
from wizwalker.memory.memory_objects.window import DeckListControlSpellEntry

from bartlebot.combat.deck_io import (
    ACTIVE_FLAG_OFFSET,
    ACTIVE_FLAG_ON,
    ENTRY_STRIDE,
    _control_for,
    _entry_vector,
)


class _Err:
    """Marker so a failed read is reported as its exception, not as None.

    A read that returns None and a read that raised mean very different things
    here — the first is real data, the second is a broken offset — and this
    probe exists precisely to tell them apart.
    """

    def __init__(self, message: str):
        self.message = message

    def __repr__(self) -> str:
        return f"<read failed: {self.message}>"


async def _safe(coro, default=None):
    try:
        return await coro
    except Exception as exc:
        return _Err(repr(exc)) if default is _ERR else default


_ERR = object()  # sentinel: "on failure, report the exception"


# ---------------------------------------------------------------------------
# Behavior read (no UI)
# ---------------------------------------------------------------------------


async def read_behavior(client) -> dict:
    """Everything ClientDeckBehavior exposes, plus why it failed if it did."""
    out: dict = {
        "reachable": False,
        "error": None,
        "spells": [],
        "exclusions": None,
        "school": None,
        "behavior": None,  # kept so section E can do raw reads off the same object
    }
    client_object = client.client_object

    try:
        behavior = await client_object.try_get_deck_behavior()
    except Exception as exc:
        out["error"] = f"try_get_deck_behavior() raised: {exc!r}"
        return out

    if behavior is None:
        # Worth distinguishing "no equipment behavior at all" from "equipment
        # found but no BasicDeckBehavior on any item" — the first means the read
        # path is broken, the second means no deck is equipped.
        try:
            equip = await client_object.try_get_equipment_behavior()
            if equip is None:
                out["error"] = "try_get_equipment_behavior() returned None"
            else:
                items = await equip.item_list()
                out["error"] = f"equipment found ({len(items)} item(s)) but no BasicDeckBehavior on any of them"
        except Exception as exc:
            out["error"] = f"try_get_deck_behavior() returned None; equipment probe raised: {exc!r}"
        return out

    out["reachable"] = True
    out["behavior"] = behavior

    try:
        for entry in await behavior.spell_list():
            out["spells"].append(
                {
                    "template_id": await _safe(entry.template_id()),
                    "enchantment": await _safe(entry.enchantment()),
                    "quantity": await _safe(entry.quantity()),
                }
            )
    except Exception as exc:
        out["error"] = f"spell_list() raised: {exc!r}"

    out["exclusions"] = await _safe(behavior.exclusion_list(), _ERR)
    out["school"] = await _safe(behavior.archmastery_school(), _ERR)
    return out


def behavior_counts(behavior: dict) -> dict[int, int]:
    """template_id -> total copies, per the behavior's spell list."""
    counts: dict[int, int] = {}
    for spell in behavior["spells"]:
        tid, qty = spell["template_id"], spell["quantity"]
        if tid is None:
            continue
        counts[tid] = counts.get(tid, 0) + (qty if isinstance(qty, int) else 1)
    return counts


# ---------------------------------------------------------------------------
# Ground truth read (deck window open)
# ---------------------------------------------------------------------------


async def _entry_identity(control, address: int) -> tuple[str, int] | None:
    """(name, template_id) for one DeckListControl entry.

    deck_io reads only the name; the template id is what the behavior speaks in,
    so this probe needs both to line the two sources up.
    """
    entry = DeckListControlSpellEntry(control.hook_handler, address)
    graphical = await entry.graphical_spell()
    if not graphical:
        return None
    template = await graphical.spell_template()
    if not template:
        return None
    name = await template.name()
    if not name:
        return None
    return name, await graphical.template_id()


async def read_window_deck(client, deck_window) -> list[tuple[str, int]]:
    """(name, template_id) for every card in CardsInDeck, in slot order."""
    control = await _control_for(client, deck_window, "CardsInDeck")
    begin, count = await _entry_vector(control)
    cards: list[tuple[str, int]] = []
    for idx in range(count):
        try:
            card = await _entry_identity(control, begin + idx * ENTRY_STRIDE)
        except Exception:
            continue
        if card:
            cards.append(card)
    return cards


async def read_window_items(client, deck_window) -> list[tuple[str, int, bool]]:
    """(name, template_id, active) for every item card slot."""
    control = await _control_for(client, deck_window, "ItemSpells")
    begin, count = await _entry_vector(control)
    slots: list[tuple[str, int, bool]] = []
    for idx in range(count):
        address = begin + idx * ENTRY_STRIDE
        try:
            card = await _entry_identity(control, address)
            if not card:
                continue
            flag = await control.read_bytes(address + ACTIVE_FLAG_OFFSET, 1)
        except Exception:
            continue
        slots.append((card[0], card[1], flag[0] == ACTIVE_FLAG_ON))
    return slots


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _print_behavior(behavior: dict, names: dict[int, str] | None = None) -> None:
    if not behavior["reachable"]:
        print(f"   NOT REACHABLE — {behavior['error']}")
        return
    spells = behavior["spells"]
    if not spells:
        print("   Reachable, but spell_list() is EMPTY.")
        if behavior["error"]:
            print(f"   {behavior['error']}")
    else:
        print(f"   {'#':>3}  {'template_id':>11}  {'qty':>4}  {'ench':>5}  name")
        print("   " + "-" * 62)
        for idx, spell in enumerate(spells):
            tid = spell["template_id"]
            label = (names or {}).get(tid, "?")
            print(f"   {idx:>3}  {tid!s:>11}  {spell['quantity']!s:>4}  {spell['enchantment']!s:>5}  {label}")
        total = sum(q for s in spells if isinstance(q := s["quantity"], int))
        print(f"   {len(spells)} distinct entries, {total} total copies.")
    print(f"   exclusion_list(): {behavior['exclusions']!r}")
    print(f"   archmastery_school(): {behavior['school']!r}")


async def section_a(client) -> dict:
    print("A. BEHAVIOR READ WITH THE DECK WINDOW NEVER OPENED")
    print()
    try:
        await _maybe_get_named_window(client.root_window, "DeckConfiguration", retries=0)
        print("   !! DeckConfiguration is ALREADY present in the UI tree.")
        print("      Either the deck page is open, or the window persists after")
        print("      closing. Section A cannot prove the closed-window case on")
        print("      this client — close the deck page and restart the client.")
        print()
    except ValueError:
        print("   DeckConfiguration absent from the UI tree (deck page closed). Good.")
        print()

    behavior = await read_behavior(client)
    _print_behavior(behavior)
    print()
    if behavior["reachable"] and behavior["spells"]:
        print("   >> The deck IS readable with the window closed. Section B checks")
        print("      whether what it says is actually correct.")
    else:
        print("   >> This is the suspected failure: no usable deck data without")
        print("      opening the window. Section B re-reads it with the window")
        print("      open to show whether opening the UI is what populates it.")
    print()
    return behavior


async def section_b(client, closed_behavior: dict) -> tuple[dict, dict[int, str]]:
    print("B. GROUND TRUTH (deck window open) vs THE BEHAVIOR")
    print()
    from wizwalker.extensions.scripting.deck_builder import DeckBuilder  # noqa: PLC0415

    names: dict[int, str] = {}
    async with DeckBuilder(client) as db:
        await db.refresh_deck_page()
        deck_window = await _maybe_get_named_window(client.root_window, "DeckConfiguration")
        cards = await read_window_deck(client, deck_window)
        items = await read_window_items(client, deck_window)
        open_behavior = await read_behavior(client)

    for name, tid in cards:
        names[tid] = name
    for name, tid, _active in items:
        names.setdefault(tid, name)

    window_counts: dict[int, int] = {}
    for _name, tid in cards:
        window_counts[tid] = window_counts.get(tid, 0) + 1

    print("   CardsInDeck (authoritative):")
    print(f"   {'template_id':>11}  {'qty':>4}  name")
    print("   " + "-" * 50)
    for tid, qty in sorted(window_counts.items(), key=lambda kv: names.get(kv[0], "")):
        print(f"   {tid:>11}  {qty:>4}  {names.get(tid, '?')}")
    print(f"   {len(window_counts)} distinct, {len(cards)} total copies.")
    print()

    print("   Behavior, re-read WITH the window open:")
    _print_behavior(open_behavior, names)
    print()

    open_counts = behavior_counts(open_behavior)
    closed_counts = behavior_counts(closed_behavior)

    print("   VERDICT")
    _compare("closed-window behavior", closed_counts, window_counts, names)
    _compare("open-window behavior  ", open_counts, window_counts, names)
    if closed_counts != open_counts:
        print()
        print("   !! The behavior read CHANGED once the window was opened, so it is")
        print("      populated (or refreshed) by the UI. A fast path cannot rely on")
        print("      it without opening the window first, which defeats the point.")
    print()
    return open_behavior, names


def _compare(label: str, counts: dict[int, int], truth: dict[int, int], names: dict[int, str]) -> None:
    if counts == truth:
        print(f"   {label}: MATCHES CardsInDeck exactly.")
        return
    print(f"   {label}: DIFFERS from CardsInDeck.")
    for tid in sorted(set(counts) | set(truth), key=lambda t: names.get(t, "")):
        got, want = counts.get(tid, 0), truth.get(tid, 0)
        if got != want:
            print(f"        {names.get(tid, '?'):<30} behavior={got}  window={want}  (tid {tid})")


async def section_c(client, behavior: dict, names: dict[int, str]) -> list[tuple[str, int, bool]]:
    print("C. SERIALIZED EXCLUSION LIST vs ITEM CARD TOGGLES")
    print()
    exclusions = behavior["exclusions"]
    if isinstance(exclusions, _Err):
        print(f"   exclusion_list() could not be read: {exclusions!r}")
        print()
        return []

    from wizwalker.extensions.scripting.deck_builder import DeckBuilder  # noqa: PLC0415

    async with DeckBuilder(client) as db:
        await db.refresh_deck_page()
        deck_window = await _maybe_get_named_window(client.root_window, "DeckConfiguration")
        items = await read_window_items(client, deck_window)

    on = [(n, t) for n, t, active in items if active]
    off = [(n, t) for n, t, active in items if not active]
    print(f"   ItemSpells holds {len(items)} slot(s): {len(on)} ON, {len(off)} OFF.")
    print()
    print("   Toggled OFF (what the exclusion list should describe):")
    for name, tid in sorted(off):
        print(f"        {name:<30} tid {tid}")
    if not off:
        print("        (none — toggle an item card off and re-run to test this)")
    print()
    print(f"   exclusion_list() returned: {exclusions!r}")
    print()

    for name, tid, _active in items:
        names.setdefault(tid, name)

    # Ids the deck remembers from gear that is not currently worn are expected
    # and carry no information, so compare only over cards ItemSpells grants.
    granted = {tid for _name, tid, _active in items}
    off_counts: dict[int, int] = {}
    for _name, tid in off:
        off_counts[tid] = off_counts.get(tid, 0) + 1
    serialized = {spell_id: qty for spell_id, qty in (exclusions or []) if spell_id in granted}

    if serialized == off_counts:
        print("   >> For every currently granted card, the serialized list matches")
        print("      the OFF slots.")
    else:
        print("   >> The serialized list disagrees with the OFF slots, for cards")
        print("      that ARE currently granted:")
        for tid in sorted(set(serialized) | set(off_counts), key=lambda t: names.get(t, "~")):
            got, want = serialized.get(tid, 0), off_counts.get(tid, 0)
            if got != want:
                print(f"        {names.get(tid, '?'):<30} list={got}  window={want}  (tid {tid})")
        print()
        print("      Section E reads the live map instead, which is the candidate")
        print("      explanation for these gaps.")
    print()
    return items


async def section_d(client, closed_behavior: dict) -> None:
    print("D. BEHAVIOR AFTER THE WINDOW IS CLOSED AGAIN")
    print()
    try:
        await _maybe_get_named_window(client.root_window, "DeckConfiguration", retries=0)
        print("   (DeckConfiguration still present in the tree — it persists after close.)")
    except ValueError:
        print("   (DeckConfiguration gone from the tree.)")
    print()
    after = await read_behavior(client)
    _print_behavior(after)
    print()
    before, now = behavior_counts(closed_behavior), behavior_counts(after)
    if not after["reachable"] or not after["spells"]:
        print("   >> Unreadable once the window closed: the behavior is only valid")
        print("      while the deck UI is alive. No fast path from here.")
    elif before == now:
        print("   >> Same as section A. The read is stable across open/close.")
    else:
        print("   >> Differs from section A, so the value seen before the window")
        print("      was ever opened was stale or wrong. Treat section A as the")
        print("      real answer for a cold start.")
    print()


# ---------------------------------------------------------------------------
# Section E: the LIVE exclusion map
# ---------------------------------------------------------------------------
#
# exclusion_list() parses m_serializedExclusionList at +0x98 -- a *serialized*
# copy, which the evidence says is refreshed on some event (save/equip) rather
# than on every toggle: a run with four item cards switched on still reported
# two of them at their pre-toggle counts, and one card that was toggled OFF was
# missing from the list entirely.
#
# The live state should be m_exclusionMap at +0x88, an MSVC std::map<uint32,
# uint16>, with its size at +0x90.  This section reads that directly.
#
# The tree *shape* is layout-invariant, so traversal is safe to hard-code:
#     +0x00 _Left   +0x08 _Parent   +0x10 _Right   +0x18 _Color   +0x19 _Isnil
# Where _Myval sits is NOT invariant -- it is padded to the value type's
# alignment.  wizwalker itself reads a pointer-valued map's key at +0x20
# (read_std_map) but a uint32 set's value at +0x1C (read_hashset_basic), which
# is exactly that difference.  For pair<const uint32, uint16> the alignment is
# 4, so +0x1C is the best guess -- but rather than assume, every plausible
# layout is decoded and scored against the item card template ids we know are
# real.

MAP_OFFSET = 0x88
MAP_SIZE_OFFSET = 0x90
SERIALIZED_OFFSET = 0x98

NODE_LEFT, NODE_PARENT, NODE_RIGHT, NODE_ISNIL = 0x00, 0x08, 0x10, 0x19
MAX_NODES = 4096  # cycle guard: a corrupt read must not spin forever

# (label, key_offset, key_primitive, value_offset, value_primitive)
CANDIDATE_LAYOUTS = (
    ("key@0x1C:u32 val@0x20:u16", 0x1C, Primitive.uint32, 0x20, Primitive.uint16),
    ("key@0x1C:u32 val@0x20:u32", 0x1C, Primitive.uint32, 0x20, Primitive.uint32),
    ("key@0x20:u32 val@0x24:u16", 0x20, Primitive.uint32, 0x24, Primitive.uint16),
    ("key@0x20:u32 val@0x28:u16", 0x20, Primitive.uint32, 0x28, Primitive.uint16),
    ("key@0x20:u64 val@0x28:u64", 0x20, Primitive.uint64, 0x28, Primitive.uint64),
)


async def walk_map_nodes(behavior) -> tuple[list[int], str | None]:
    """Addresses of every non-nil node in the map at +0x88.

    Returns (nodes, error).  MSVC keeps a sentinel head whose _Parent is the
    root; an empty map is the head pointing at itself.
    """
    try:
        head = await behavior.read_value_from_offset(MAP_OFFSET, Primitive.uint64)
    except Exception as exc:
        return [], f"could not read the map pointer at +0x{MAP_OFFSET:02X}: {exc!r}"
    if not head:
        return [], "map pointer is null"

    try:
        root = await behavior.read_typed(head + NODE_PARENT, Primitive.uint64)
    except Exception as exc:
        return [], f"could not read the root pointer: {exc!r}"
    if not root or root == head:
        return [], None  # legitimately empty

    nodes: list[int] = []
    seen: set[int] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if not node or node in seen:
            continue
        seen.add(node)
        if len(seen) > MAX_NODES:
            return nodes, f"stopped after {MAX_NODES} nodes — the walk is not terminating"
        try:
            if await behavior.read_typed(node + NODE_ISNIL, Primitive.bool):
                continue
            nodes.append(node)
            stack.append(await behavior.read_typed(node + NODE_LEFT, Primitive.uint64))
            stack.append(await behavior.read_typed(node + NODE_RIGHT, Primitive.uint64))
        except Exception as exc:
            return nodes, f"read failed part-way through the walk: {exc!r}"
    return nodes, None


async def decode_layout(behavior, nodes: list[int], layout) -> dict[int, int]:
    _label, key_off, key_prim, val_off, val_prim = layout
    out: dict[int, int] = {}
    for node in nodes:
        try:
            key = await behavior.read_typed(node + key_off, key_prim)
            val = await behavior.read_typed(node + val_off, val_prim)
        except Exception:
            continue
        out[key] = val
    return out


def score_layout(decoded: dict[int, int], known_ids: set[int]) -> tuple[int, int]:
    """(keys that are real template ids, values in a plausible copy range)."""
    hits = sum(1 for key in decoded if key in known_ids)
    sane = sum(1 for val in decoded.values() if 1 <= val <= 8)
    return hits, sane


async def section_e(client, items: list[tuple[str, int, bool]], names: dict[int, str]) -> None:
    print("E. LIVE EXCLUSION MAP (+0x88) vs THE SERIALIZED LIST (+0x98)")
    print()
    print("   Read with the deck window CLOSED — this is the state a fast path")
    print("   would actually see.")
    print()

    behavior_info = await read_behavior(client)
    behavior = behavior_info["behavior"]
    if behavior is None:
        print(f"   Behavior unreachable: {behavior_info['error']}")
        print()
        return

    serialized = behavior_info["exclusions"]
    serialized_len = len(serialized) if isinstance(serialized, list) else None

    try:
        size_u16 = await behavior.read_value_from_offset(MAP_SIZE_OFFSET, Primitive.uint16)
        size_u64 = await behavior.read_value_from_offset(MAP_SIZE_OFFSET, Primitive.uint64)
    except Exception as exc:
        size_u16 = size_u64 = None
        print(f"   Could not read the map size at +0x{MAP_SIZE_OFFSET:02X}: {exc!r}")

    try:
        raw = await behavior.read_bytes(await behavior.read_base_address() + MAP_OFFSET, 0x38)
        print(f"   raw +0x{MAP_OFFSET:02X}..+0x{MAP_OFFSET + 0x38:02X}: {raw.hex(' ', 4)}")
    except Exception as exc:
        print(f"   Could not dump the raw field bytes: {exc!r}")
    print()

    print(f"   m_exclusionCount as uint16 @ +0x{MAP_SIZE_OFFSET:02X}: {size_u16}")
    print(f"   same 8 bytes as uint64                : {size_u64}")
    print(f"   entries in the serialized list        : {serialized_len}")
    print()

    if size_u16 is not None and serialized_len is not None:
        if size_u16 == serialized_len:
            print("   >> Counts AGREE. That does not prove the serialized list is")
            print("      current — the stale entries seen earlier kept their slot and")
            print("      only their quantity was wrong — so the decode below is still")
            print("      the thing that settles it.")
        else:
            print("   >> Counts DISAGREE, so the serialized list is definitively not")
            print("      a current view of the map. Confirms the staleness diagnosis.")
    print()

    nodes, error = await walk_map_nodes(behavior)
    if error:
        print(f"   Tree walk problem: {error}")
    print(f"   Walked {len(nodes)} non-nil node(s).")
    if size_u16 is not None and len(nodes) != size_u16:
        print(f"   !! That disagrees with the map's own size ({size_u16}) — the node")
        print("      layout assumption for _Left/_Right may be wrong. Treat the")
        print("      decode below with suspicion.")
    print()

    if not nodes:
        print("   Nothing to decode. If the map is genuinely empty while item cards")
        print("   are toggled off, exclusions live somewhere else entirely.")
        print()
        return

    for node in nodes[:4]:
        try:
            raw = await behavior.read_bytes(node, 0x30)
            print(f"   node {node:#x}: {raw.hex(' ', 4)}")
        except Exception as exc:
            print(f"   node {node:#x}: unreadable ({exc!r})")
    print("   (bytes 0x00-0x17 are _Left/_Parent/_Right; the payload follows 0x1A)")
    print()

    known_ids = {tid for _name, tid, _active in items}
    off_counts: dict[int, int] = {}
    for _name, tid, active in items:
        if not active:
            off_counts[tid] = off_counts.get(tid, 0) + 1

    print("   LAYOUT SCORING (against the item card ids ItemSpells reported)")
    print(f"   {'layout':<28} {'keys':>5} {'real ids':>9} {'sane qty':>9}")
    print("   " + "-" * 56)
    best = None
    for layout in CANDIDATE_LAYOUTS:
        decoded = await decode_layout(behavior, nodes, layout)
        hits, sane = score_layout(decoded, known_ids)
        print(f"   {layout[0]:<28} {len(decoded):>5} {hits:>9} {sane:>9}")
        if best is None or hits > best[1]:
            best = (layout, hits, decoded)
    print()

    layout, hits, decoded = best
    if hits == 0:
        print("   >> No layout produced a single recognizable template id. Either the")
        print("      map is not at +0x88, or it does not hold what we think. The raw")
        print("      node bytes above are the evidence to work from.")
        print()
        return

    print(f"   BEST: {layout[0]} ({hits} recognizable id(s))")
    print()
    print(f"   {'template_id':>11}  {'map':>4}  {'OFF':>4}  name")
    print("   " + "-" * 52)
    for tid in sorted(set(decoded) | set(off_counts), key=lambda t: names.get(t, "~")):
        print(f"   {tid:>11}  {decoded.get(tid, 0)!s:>4}  {off_counts.get(tid, 0)!s:>4}  {names.get(tid, '?')}")
    print()

    restricted = {tid: qty for tid, qty in decoded.items() if tid in known_ids}
    if restricted == off_counts:
        print("   >> The live map matches the OFF slots EXACTLY, for every card")
        print("      currently granted. Item state is recoverable without the")
        print("      window, and the fast path is fully viable.")
    else:
        print("   >> The live map does NOT match the OFF slots. Differences, for")
        print("      granted cards only:")
        for tid in sorted(set(restricted) | set(off_counts), key=lambda t: names.get(t, "~")):
            got, want = restricted.get(tid, 0), off_counts.get(tid, 0)
            if got != want:
                print(f"        {names.get(tid, '?'):<30} map={got}  window={want}  (tid {tid})")
        print()
        print("      Ids in the map that ItemSpells does not grant are expected —")
        print("      the deck remembers cards from gear you are not wearing — and")
        print("      are excluded from this comparison.")
    print()


async def probe(client) -> None:
    closed_behavior = await section_a(client)
    open_behavior, names = await section_b(client, closed_behavior)
    items = await section_c(client, open_behavior, names)
    await section_d(client, closed_behavior)
    await section_e(client, items, names)


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
                print(f"  !! probe failed on this client: {exc!r}")
    finally:
        await asyncio.gather(*[c.close() for c in clients], return_exceptions=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
