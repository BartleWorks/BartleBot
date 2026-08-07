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

from bartlebot.nodes.timing import (
    DECK_APPLY_TIMEOUT,
    DECK_FUSION_CLICK_SETTLE,
    DECK_FUSION_FLYOUT_SETTLE,
    DECK_ITEM_PAGE_SETTLE,
    DECK_ITEM_TOGGLE_SETTLE,
)

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

# --- tiered ("fusion") spell names ----------------------------------------
# Variants are named "Novus Storm - T05 - A": base name, tier, upgrade path.
# wizwalker splits on the same marker.
TIER_MARKER = " - T"

# A spellbook entry whose path is this is not a castable variant at all — it is
# the *root* of a fusion tree, and clicking it opens the variant flyout.  Probed
# against a live client (tools/probe_spell_list.py): a wizard owning Novus Storm
# at both T05-A and T05-B lists "Novus Storm - T01 - Base" in the spellbook, and
# only the flyout names the two real variants.
FUSION_ROOT_PATH = "Base"

# Both routing paths substitute tiers, and a wizard reading the log should not
# have to notice that two differently-worded lines mean the same thing.
_SUBSTITUTION_MSG = "Using '%s' in place of '%s' — the highest tier this wizard has unlocked"

# NOTE: SpellTemplate.spell_base() looks like it should give the base name and
# does not — the same probe returned "Ammit" for "Soul Burner - T03 - B",
# "Giant" for "Sharpened Blade" and "Storm Trap" for "Windstorm".  It is some
# internal derived-from field.  Parsing template.name() is the only reliable
# route to a base name.


@dataclass(frozen=True)
class TieredName:
    """A parsed "Novus Storm - T05 - A" spell name.

    ``tier`` and ``path`` are None for a plain, untiered spell, in which case
    ``base`` is the whole name.
    """

    base: str
    tier: int | None = None
    path: str | None = None

    @property
    def is_tiered(self) -> bool:
        return self.tier is not None

    @property
    def is_fusion_root(self) -> bool:
        """True for "… - T01 - Base": a placeholder, not something castable.

        Clicking one in the spellbook opens the variant flyout instead of adding
        a card, which is the difference between a wizard who has unlocked
        several variants and one who has unlocked exactly one.
        """
        return self.path == FUSION_ROOT_PATH


@dataclass(frozen=True)
class NormalCardPlan:
    """What of a saved deck's normal cards this account can actually supply."""

    addable: dict[str, int]  # cards this wizard can supply, before routing
    missing: list[str]  # requested names with no matching spellbook entry at all


@dataclass(frozen=True)
class CardRouting:
    """How each supplyable card must be added, and what changed on the way."""

    direct: dict[str, int]  # exact spellbook entries — safe for the deck builder
    fusion: dict[str, int]  # needs its variant flyout opened; resolved at click time
    substitutions: dict[str, str]  # requested name → the entry used instead
    unresolvable: list[str]  # matched a base, but no usable variant


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


def parse_tiered_name(name: str) -> TieredName:
    """Split "Storm Lord - T05 - A" into base / tier / path.

    Anything that does not carry the tier marker, or whose tier is not a number,
    is treated as a plain spell name — the game does ship oddly-named cards, and
    guessing wrong here would mean substituting one spell for an unrelated one.
    """
    marker = name.find(TIER_MARKER)
    if marker == -1:
        return TieredName(base=name)
    base = name[:marker]
    remainder = name[marker + len(TIER_MARKER) :]
    tier_text, _, path = remainder.partition(" - ")
    if not tier_text.isdigit():
        return TieredName(base=name)
    return TieredName(base=base, tier=int(tier_text), path=path or None)


def resolve_tier(requested: str, owned: list[str]) -> str | None:
    """Return the best owned variant for ``requested``, or None if there is none.

    Exact match wins.  Otherwise the highest owned tier *below* the requested one
    is used: a strategy written around Storm Lord T05 still works with T03, it
    just hits softer, and that is far better than the wizard entering the fight
    without the spell at all.

    Path is never crossed.  The suffix after the tier ("- A", "- B", "- C", and
    however many the game adds later) selects an upgrade *branch*, not a rank:
    two spells at the same tier on different paths do genuinely different things.
    Substituting across them would silently change what the strategy does rather
    than merely weaken it, so paths are compared for equality and never ordered.

    Tiers above the requested one are not used either.  The saved deck is the
    author's statement of what the strategy was tuned for; quietly upgrading
    could break a pip budget the DSL depends on.
    """
    if requested in owned:
        return requested
    want = parse_tiered_name(requested)
    if not want.is_tiered:
        return None

    candidates = []
    for name in owned:
        have = parse_tiered_name(name)
        if not have.is_tiered or have.base != want.base or have.path != want.path:
            continue
        if have.tier <= want.tier:
            candidates.append((have.tier, name))
    if not candidates:
        return None
    return max(candidates)[1]


def plan_normal_cards(desired: dict[str, int], available: list[str]) -> NormalCardPlan:
    """Split a saved deck's normal cards into what this account can and cannot supply.

    Runs *before* the deck is cleared.  wizwalker's ``_add_all_normal_cards``
    raises on the first name it cannot place, and ``write_deck`` clears the deck
    first — so a single unowned card used to leave the wizard with an empty deck
    and a log line claiming the run would "continue with whatever was set".
    Filtering up front turns that into a partial deck, matching how item cards
    have always behaved (see ``plan_item_toggles``).

    Tiered names are kept when their *base* spell is owned; which variants the
    account actually has cannot be read until the tier flyout is opened, so that
    resolution happens later, in ``_resolve_tier_substitutions``.

    Matching is base-to-base, not name-to-base.  wizwalker assumes a tiered
    spell sits in the spellbook under its bare base name ("Novus Storm"), but a
    spellbook that instead lists the wizard's current variant ("Novus Storm -
    T04 - B") would fail that lookup and make an owned spell look unowned.
    Reducing *both* sides to their base makes the match hold either way — see
    ``tools/probe_spell_list.py``, which dumps what the list really contains.
    """
    owned = set(available)
    owned_bases = {parse_tiered_name(entry).base for entry in available}
    addable: dict[str, int] = {}
    missing: list[str] = []
    for name, count in desired.items():
        if count <= 0:
            continue
        if name in owned or parse_tiered_name(name).base in owned_bases:
            addable[name] = count
        else:
            missing.append(name)
    return NormalCardPlan(addable=addable, missing=sorted(missing))


def partition_addable(addable: dict[str, int], available: list[str]) -> CardRouting:
    """Split cards by how they must be added, and report what was substituted.

    Three cases, because the spellbook represents a tiered spell in two very
    different ways depending on how many variants the wizard has unlocked:

    *direct* — the name is already an exact spellbook entry, or resolves to one.
        A wizard who has unlocked exactly one variant sees it listed outright
        ("Novus Storm - T04 - B"), so a deck asking for T05-B resolves down to
        that entry and it can simply be clicked.  Plain untiered cards land here
        too.  These are safe to hand to wizwalker's ``_add_all_normal_cards``,
        which takes its no-op "name in spell_names" branch and cannot raise.

    *fusion* — the spellbook entry is a "… - T01 - Base" root, so the real
        variants are only visible once its flyout is open.  wizwalker cannot add
        these at all: its tiered branch looks up the *bare* base ("Novus Storm"),
        which no build lists.  ``_add_fusion_cards`` handles them instead.

    *unresolvable* — no spellbook entry shares the base, or the only unlocked
        variant is on another path or a higher tier than asked for.

    Splitting up front is what keeps ``_add_all_normal_cards`` from ever seeing
    a name it would raise on, which matters because it runs after the clear.

    ``substitutions`` records every card whose name changed on the way into
    *direct*.  It is returned rather than logged because this function is pure;
    ``write_deck`` reports it.  Without that the single-variant path swapped a
    tier silently — the deck the wizard ends up holding is not the deck that was
    saved, and nothing said so.
    """
    entries = {parse_tiered_name(entry).base: entry for entry in available}
    direct: dict[str, int] = {}
    fusion: dict[str, int] = {}
    substitutions: dict[str, str] = {}
    unresolvable: list[str] = []

    for name, count in addable.items():
        if name in available:
            direct[name] = direct.get(name, 0) + count
            continue
        entry = entries.get(parse_tiered_name(name).base)
        if entry is None:
            unresolvable.append(name)
        elif parse_tiered_name(entry).is_fusion_root:
            fusion[name] = fusion.get(name, 0) + count
        elif (resolved := resolve_tier(name, [entry])) is not None:
            direct[resolved] = direct.get(resolved, 0) + count
            if resolved != name:
                substitutions[name] = resolved
        else:
            unresolvable.append(name)

    return CardRouting(
        direct=direct,
        fusion=fusion,
        substitutions=substitutions,
        unresolvable=sorted(unresolvable),
    )


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


async def _spell_list_names(db) -> list[str]:
    """Template names of every card in the account's spell list, in list order.

    Order matters: ``calcuate_position_of_card_in_page`` derives a card's page
    and slot from its index here, so this must stay a positional list rather
    than a set.
    """
    names: list[str] = []
    for spell in await db.get_graphical_spell_cards():
        template = await spell.spell_template()
        names.append(await template.name() if template else "")
    return names


async def _owned_tier_variants(db) -> list[str]:
    """Template names currently shown in the open tier flyout."""
    names: list[str] = []
    for spell in await db.get_graphical_tiered_spell_cards():
        if not spell:
            continue
        template = await spell.spell_template()
        if template:
            name = await template.name()
            if name:
                names.append(name)
    return names


async def _close_tier_flyout(db) -> None:
    """Dismiss the tier flyout, falling back to a full deck-page refresh."""
    try:
        close_btn = await _maybe_get_named_window(db._deck_config_window, "CloseTSMPUnlockedPageButton")  # noqa: SLF001
        async with db.client.mouse_handler:
            await db.client.mouse_handler.click_window(close_btn)
    except Exception:
        logger.debug("Tier flyout close button unavailable, refreshing the deck page", exc_info=True)
        await db.refresh_deck_page()


async def _add_fusion_cards(db, wanted: dict[str, int], spell_names: list[str]) -> None:
    """Add fusion variants by opening each root's flyout and clicking inside it.

    wizwalker cannot do this.  Its tiered branch locates the base spell by its
    *bare* name ("Novus Storm"), but the spellbook lists a fusion root as
    "Novus Storm - T01 - Base", so the lookup misses and it raises — after the
    deck has already been cleared.

    Resolution happens here rather than before the clear because the flyout is
    the only place the unlocked variants are legible, and opening it twice (once
    to read, once to click) would double the click cost for no benefit: nothing
    below raises, so a failure costs the affected cards and nothing more.

    One flyout open/close per distinct base spell, regardless of how many
    variants or copies of it the deck asks for.
    """
    by_base: dict[str, dict[str, int]] = {}
    for name, count in wanted.items():
        by_base.setdefault(parse_tiered_name(name).base, {})[name] = count
    entries = {parse_tiered_name(entry).base: entry for entry in spell_names}

    for base, requested in by_base.items():
        entry = entries.get(base)
        if entry is None:  # partition_addable only routes cards here when it exists
            continue
        try:
            page_idx, card_idx = db.calcuate_position_of_card_in_page(spell_names, entry)
            position = await db.calculate_card_position(card_idx)
            await db._go_to_spell_page(page_idx)  # noqa: SLF001
            async with db.client.mouse_handler:
                await db.client.mouse_handler.click(*position)
            # The click is what populates the variant list; reading straight
            # away can return the previous spell's variants, or nothing.
            await asyncio.sleep(DECK_FUSION_FLYOUT_SETTLE)
            owned = await _owned_tier_variants(db)
        except Exception:
            logger.warning("Could not open the variant list for '%s' — its cards are skipped", base, exc_info=True)
            continue

        try:
            await _click_fusion_variants(db, requested, owned, base)
        except Exception:
            logger.warning("Failed while adding variants of '%s'", base, exc_info=True)
        finally:
            await _close_tier_flyout(db)


async def _click_fusion_variants(db, requested: dict[str, int], owned: list[str], base: str) -> None:
    """Click each requested variant inside the already-open flyout."""
    for name, count in requested.items():
        resolved = resolve_tier(name, owned)
        if resolved is None:
            logger.warning(
                "No usable variant of '%s' for this wizard (deck wants '%s', unlocked: %s) — skipping",
                base,
                name,
                ", ".join(owned) or "none",
            )
            continue
        if resolved != name:
            logger.info(_SUBSTITUTION_MSG, resolved, name)
        _page, variant_idx = db.calcuate_position_of_card_in_page(owned, resolved)
        variant_position = await db.calculate_tiered_spell_card_position(variant_idx)
        async with db.client.mouse_handler:
            for _ in range(count):
                await db.client.mouse_handler.click(*variant_position)
                await asyncio.sleep(DECK_FUSION_CLICK_SETTLE)


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

    Which cards can be placed is worked out *before* the clear, because
    ``_add_all_normal_cards`` raises on the first name it cannot place and by
    then the deck is gone: one unplaceable card used to leave the wizard holding
    an empty deck while the caller logged that the run would "continue with
    whatever was set".  If nothing at all can be placed the deck is left
    untouched, since clearing is only ever worth doing as the first half of a
    rewrite.

    Item state is read *after* the deck is cleared, because ClearDeckButton may
    reset item toggles too; planning against the post-clear state is correct
    either way.
    """
    await db.refresh_deck_page()
    deck_window = await _maybe_get_named_window(db.client.root_window, "DeckConfiguration")

    if await _deck_already_matches(db.client, deck_window, deck):
        logger.info("Deck already matches saved configuration, skipping clear/rewrite")
        return

    spell_names: list[str] = []
    direct: dict[str, int] = {}
    fusion: dict[str, int] = {}
    if deck.normal:
        await db.view_deck_cards()
        spell_names = await _spell_list_names(db)
        plan = plan_normal_cards(deck.normal, spell_names)
        routing = partition_addable(plan.addable, spell_names)
        direct, fusion = routing.direct, routing.fusion
        # Substitutions for *fusion* cards are reported by _add_fusion_cards,
        # which is where they are decided; these are the ones settled here.
        for requested, used in sorted(routing.substitutions.items()):
            logger.info(_SUBSTITUTION_MSG, used, requested)
        skipped = sorted(plan.missing + routing.unresolvable)
        if skipped:
            logger.warning(
                "Cards in the saved deck could not be matched against this wizard's spellbook, skipped: %s",
                ", ".join(skipped),
            )
            # The spellbook is the evidence for that claim, so log it alongside:
            # "card not owned" and "card owned under a name we failed to match"
            # are indistinguishable from the warning above, and the second is a
            # bug in our matching rather than anything the user can act on.
            logger.debug("Spellbook holds %d entries: %s", len(spell_names), ", ".join(spell_names) or "(none)")

    if deck.normal and not direct and not fusion:
        # Clearing is only ever worth doing as the first half of a rewrite.  With
        # nothing to put back, going ahead would strip the wizard's deck to
        # nothing and leave them entering combat with no cards — strictly worse
        # than the deck they already had, which at least someone chose.  An
        # empty or unmatchable spellbook read is far more likely to be our bug
        # than a genuine account with none of these cards.
        logger.error(
            "Not applying the saved deck: none of its %d card type(s) could be matched, "
            "so clearing would leave this wizard with an empty deck. Leaving the current deck untouched.",
            len(deck.normal),
        )
        return

    await db.clear_deck()
    await db.view_deck_cards()

    if direct:
        # Private upstream method: it precomputes every card position and makes
        # one forward sweep, where the public add_by_name() re-reads the spell
        # list and rewinds the pages per card.  Re-check on a wizwalker bump.
        # Every name here is an exact spellbook entry, so it takes the plain
        # branch and its raising tiered path is never reached.
        await db._add_all_normal_cards(direct)  # noqa: SLF001

    if fusion:
        await _add_fusion_cards(db, fusion, spell_names)

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
