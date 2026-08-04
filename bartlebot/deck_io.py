"""Reading and restoring a client's deck.

Replaces DeckBuilder.get_deck_preset() / set_deck_preset(), both of which are
unusable: they reach the treasure card view (which leaves the spell list in a
state that breaks normal card restore) and they enumerate item cards by paging
until ``is_control_grayed()`` reports the next-page button greyed — a read that
is unreliable, so the loop can page forward forever.

Instead, both card lists are read straight out of memory.  ``CardsInDeck`` and
``ItemSpells`` are ``DeckListControl`` windows whose entry vector holds every
card at once, item cards included — no hovering, no paging, no page-count
discovery.  Clicks are still needed to *toggle* an item card, but the slot to
click is computed from the card's index rather than searched for.

Treasure cards are not supported in either direction: they cannot be read or
restored, and merely switching to their view corrupts the spell list.

MEMORY LAYOUT
    The offsets below are game-build specific and were derived with
    ``tools/probe_item_cards.py``.  Re-run that after a game patch if deck
    reads start coming back empty.

    DeckListControl entry vector   0x300 (begin) / 0x308 (end), stride 0x28
        wizwalker's DeckListControl.spell_entries() still reads 0x280, which is
        null in the current build — that is why it returns nothing.
    Entry +0x15 (uint8)            0x00 = card toggled ON, 0x01 = toggled OFF
        Only meaningful for ItemSpells.  Note entry +0x10
        (valid_graphical_spell) is 0 for every entry and carries no state.

    Item cards sit in a row-major 8x2 grid, 16 slots per page, so a card at
    vector index i is at page i // 16, slot i % 16.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from wizwalker.extensions.scripting.utils import _maybe_get_named_window
from wizwalker.memory.memory_object import Primitive
from wizwalker.memory.memory_objects.window import (
    DeckListControlSpellEntry,
    DynamicDeckListControl,
)

from bartlebot.nodes.timing import DECK_APPLY_TIMEOUT, DECK_ITEM_PAGE_SETTLE, DECK_ITEM_TOGGLE_SETTLE

if TYPE_CHECKING:
    from bartlebot.bot.strategy_models import DeckData

logger = logging.getLogger(__name__)

# --- memory layout (see module docstring) ---------------------------------
ENTRY_VECTOR_OFFSET = 0x300
ENTRY_STRIDE = 0x28
ACTIVE_FLAG_OFFSET = 0x15
ACTIVE_FLAG_ON = 0x00

# --- item card grid -------------------------------------------------------
GRID_COLUMNS = 8
GRID_ROWS = 2
SLOTS_PER_PAGE = GRID_COLUMNS * GRID_ROWS


@dataclass(frozen=True)
class ItemSlot:
    """One item card slot, located by its index in the ItemSpells vector."""

    name: str
    index: int
    active: bool

    @property
    def page(self) -> int:
        return self.index // SLOTS_PER_PAGE

    @property
    def slot_index(self) -> int:
        """0-based position within the 8x2 grid on this slot's page."""
        return self.index % SLOTS_PER_PAGE


# ---------------------------------------------------------------------------
# Pure planning helpers — no game access, directly unit tested
# ---------------------------------------------------------------------------


def count_names(names: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return counts


def active_item_counts(slots: list[ItemSlot]) -> dict[str, int]:
    return count_names([s.name for s in slots if s.active])


def plan_item_toggles(slots: list[ItemSlot], desired: dict[str, int]) -> list[ItemSlot]:
    """Return the slots to click so the active set matches ``desired``.

    Each click toggles one slot, so the plan is per-name: activate more copies
    where there are too few, deactivate where there are too many.  Names in
    ``desired`` that the account no longer grants are skipped — gear changes,
    so a saved deck naming a card that is not present is expected, not an error.

    Returned in page order to keep the click sweep moving forward.
    """
    by_name: dict[str, list[ItemSlot]] = {}
    for slot in slots:
        by_name.setdefault(slot.name, []).append(slot)

    to_click: list[ItemSlot] = []
    for name, group in by_name.items():
        want = max(0, desired.get(name, 0))
        active = [s for s in group if s.active]
        inactive = [s for s in group if not s.active]
        if len(active) < want:
            # Turn on as many extra copies as the account actually has.
            to_click.extend(inactive[: want - len(active)])
        elif len(active) > want:
            to_click.extend(active[: len(active) - want])

    missing = sorted(set(desired) - set(by_name))
    if missing:
        logger.warning("Item cards not available on this account, skipped: %s", ", ".join(missing))

    return sorted(to_click, key=lambda s: s.index)


def page_count(total_slots: int) -> int:
    """Number of item card pages holding ``total_slots`` cards (minimum 1)."""
    if total_slots <= 0:
        return 1
    return (total_slots + SLOTS_PER_PAGE - 1) // SLOTS_PER_PAGE


# ---------------------------------------------------------------------------
# Memory reads
# ---------------------------------------------------------------------------


async def _entry_vector(control) -> tuple[int, int]:
    """Return (begin_address, entry_count) for a DeckListControl's entries."""
    begin = await control.read_value_from_offset(ENTRY_VECTOR_OFFSET, Primitive.uint64)
    end = await control.read_value_from_offset(ENTRY_VECTOR_OFFSET + 8, Primitive.uint64)
    if not begin or end <= begin:
        return 0, 0
    return begin, (end - begin) // ENTRY_STRIDE


async def _control_for(client, deck_window, window_name: str):
    window = await _maybe_get_named_window(deck_window, window_name)
    return DynamicDeckListControl(client.hook_handler, await window.read_base_address())


async def _entry_name(control, address: int) -> str | None:
    entry = DeckListControlSpellEntry(control.hook_handler, address)
    graphical = await entry.graphical_spell()
    if not graphical:
        return None
    template = await graphical.spell_template()
    if not template:
        return None
    return (await template.name()) or None


async def read_deck_card_names(client, deck_window) -> list[str]:
    """Names of every card in the normal deck, in slot order."""
    control = await _control_for(client, deck_window, "CardsInDeck")
    begin, count = await _entry_vector(control)
    names: list[str] = []
    for idx in range(count):
        try:
            name = await _entry_name(control, begin + idx * ENTRY_STRIDE)
        except Exception:
            logger.debug("Unreadable deck entry at index %d", idx, exc_info=True)
            continue
        if name:
            names.append(name)
    return names


async def read_item_slots(client, deck_window) -> list[ItemSlot]:
    """Every item card the account grants, with its toggled-on state."""
    control = await _control_for(client, deck_window, "ItemSpells")
    begin, count = await _entry_vector(control)
    slots: list[ItemSlot] = []
    for idx in range(count):
        address = begin + idx * ENTRY_STRIDE
        try:
            name = await _entry_name(control, address)
            if not name:
                continue
            flag = await control.read_bytes(address + ACTIVE_FLAG_OFFSET, 1)
        except Exception:
            logger.debug("Unreadable item entry at index %d", idx, exc_info=True)
            continue
        slots.append(ItemSlot(name=name, index=idx, active=flag[0] == ACTIVE_FLAG_ON))
    return slots


# ---------------------------------------------------------------------------
# Item card toggling
# ---------------------------------------------------------------------------


async def _apply_item_toggles(db, deck_window, plan: list[ItemSlot], total_pages: int) -> None:
    """Click each planned slot, walking the item pages forward exactly once.

    ``plan`` is in index order, so pages are visited in ascending order and the
    sweep never needs to go back.  Rewinding to page 0 clicks Prev a fixed
    ``total_pages - 1`` times — clicking Prev on the first page is a harmless
    no-op, which is what lets us skip the unreliable is_control_grayed() probe.
    """
    if not plan:
        return

    client = db.client

    prev_btn = await _maybe_get_named_window(deck_window, "PrevItemSpells")
    async with client.mouse_handler:
        for _ in range(max(0, total_pages - 1)):
            await client.mouse_handler.click_window(prev_btn)
            await asyncio.sleep(DECK_ITEM_PAGE_SETTLE)

    current_page = 0
    for slot in plan:
        while current_page < slot.page:
            next_btn = await _maybe_get_named_window(deck_window, "NextItemSpells")
            async with client.mouse_handler:
                await client.mouse_handler.click_window(next_btn)
            await asyncio.sleep(DECK_ITEM_PAGE_SETTLE)
            current_page += 1

        rect = await db.get_item_spells_rectangle()
        cells = db.divide_rectangle(rect, columns=GRID_COLUMNS, rows=GRID_ROWS)
        async with client.mouse_handler:
            await client.mouse_handler.click(*cells[slot.slot_index].center())
        await asyncio.sleep(DECK_ITEM_TOGGLE_SETTLE)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def read_deck(db) -> dict:
    """Snapshot the normal deck and active item cards from an open DeckBuilder."""
    await db.refresh_deck_page()
    deck_window = await _maybe_get_named_window(db.client.root_window, "DeckConfiguration")
    normal = await read_deck_card_names(db.client, deck_window)
    items = await read_item_slots(db.client, deck_window)
    logger.debug("Read deck: %d normal cards, %d item card slots", len(normal), len(items))
    return {"normal": count_names(normal), "tc": {}, "item": active_item_counts(items)}


async def _deck_already_matches(client, deck_window, deck: DeckData) -> bool:
    """Whether the deck currently open on ``client`` is an exact match for ``deck``."""
    normal = await read_deck_card_names(client, deck_window)
    items = await read_item_slots(client, deck_window)
    return count_names(normal) == deck.normal and active_item_counts(items) == deck.item


async def write_deck(db, deck: DeckData) -> None:
    """Restore a saved deck to the client — normal cards and item card toggles.

    If the deck already open on the client is an exact match for ``deck``, skip the
    clear/rewrite entirely — clearing and rebuilding a deck that's already correct
    just costs time and click risk for no benefit.

    Item state is read *after* the deck is cleared, because ClearDeckButton may
    reset item toggles too; planning against the post-clear state is correct
    either way.
    """
    await db.refresh_deck_page()
    deck_window = await _maybe_get_named_window(db.client.root_window, "DeckConfiguration")

    if await _deck_already_matches(db.client, deck_window, deck):
        logger.info("Deck already matches saved configuration, skipping clear/rewrite")
        return

    await db.clear_deck()
    await db.view_deck_cards()

    if deck.normal:
        # Private upstream method: it precomputes every card position and makes
        # one forward sweep, where the public add_by_name() re-reads the spell
        # list and rewinds the pages per card.  Re-check on a wizwalker bump.
        await db._add_all_normal_cards(deck.normal)  # noqa: SLF001

    slots = await read_item_slots(db.client, deck_window)
    plan = plan_item_toggles(slots, deck.item)
    if plan:
        logger.info("Toggling %d item card slot(s)", len(plan))
        await _apply_item_toggles(db, deck_window, plan, page_count(len(slots)))
        await _verify_item_state(db.client, deck_window, deck.item)


async def apply_deck(client, deck: DeckData, timeout: float = DECK_APPLY_TIMEOUT) -> None:
    """Open the deck window on ``client`` and restore ``deck``, bounded by ``timeout``.

    The single entry point for every deck-apply path — the manual "Apply" button and
    the automatic pre-run apply.  Both previously had their own implementation, and
    the pre-run one had drifted onto ``DeckBuilder.set_deck_preset()``: the very API
    this module exists to replace (see the module docstring — it reaches the treasure
    card view and can page forward forever), with no timeout, so a stuck apply hung
    the run before the FSM ever started.

    The ``DeckBuilder`` context is inside the timeout so a hang while *opening* the
    deck window is bounded too, not just the write itself.

    Raises ``TimeoutError`` if the apply does not finish in time; callers decide
    whether that is fatal.
    """

    async def _open_and_write() -> None:
        from wizwalker.extensions.scripting.deck_builder import DeckBuilder  # noqa: PLC0415

        async with DeckBuilder(client) as db:
            await write_deck(db, deck)

    await asyncio.wait_for(_open_and_write(), timeout=timeout)


async def _verify_item_state(client, deck_window, desired: dict[str, int]) -> None:
    """Log a warning if the item cards did not end up as requested.

    A mismatch is not raised: the normal deck is already in place by this point,
    and a partially-applied item set is better reported than rolled back.
    """
    try:
        slots = await read_item_slots(client, deck_window)
    except Exception:
        logger.debug("Could not verify item card state", exc_info=True)
        return

    actual = active_item_counts(slots)
    available = {s.name for s in slots}
    expected = {name: n for name, n in desired.items() if name in available and n > 0}
    if actual != expected:
        logger.warning("Item cards after apply: %s (expected %s)", actual or "{}", expected or "{}")
