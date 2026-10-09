"""Reading and restoring a client's deck.

Replaces DeckBuilder.get_deck_preset() / set_deck_preset(), both of which are
unusable: they reach the treasure card view (which leaves the spell list in a
state that breaks normal card restore) and they enumerate item cards by paging
until ``is_control_grayed()`` reports the next-page button grayed — a read that
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
        wizwalker's DeckListControl.spell_entries() read 0x280 until the
        bartlebot-pin-20260915 pin, and returned nothing; it now reads 0x300.
    Entry +0x15 (uint8)            0x00 = card toggled ON, 0x01 = toggled OFF
        Only meaningful for ItemSpells.  Note entry +0x10
        (valid_graphical_spell) is 0 for every entry and carries no state.

    Item cards sit in a row-major 8x2 grid, 16 slots per page, so a card at
    vector index i is at page i // 16, slot i % 16.

PAGING
    Pages are turned by writing the list's first-visible index, never by
    clicking the page buttons.  Derived with ``tools/probe_deck_page_index.py``:

    SpellListControl start index   0x388, page * 6   (DeckBuilder.set_page)
    DeckListControl start index    0x320, page * 16  (ItemSpells)

    The game keeps a second page counter that only the buttons update.  It
    decides whether the buttons are enabled -- Prev does nothing on a written
    page -- and a tier flyout close restores the page from it, to page one after
    a write.  So every click is preceded by a write of its page, and writes are
    never mixed with the buttons.  Pages index the raw entry vector, which is why
    the spellbook is read with unreadable entries kept as blanks.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from wizwalker import Keycode
from wizwalker.extensions.scripting.utils import _maybe_get_named_window
from wizwalker.memory.memory_object import Primitive
from wizwalker.memory.memory_objects.conditionals import school_to_str
from wizwalker.memory.memory_objects.window import (
    DeckListControlSpellEntry,
    DynamicDeckListControl,
    DynamicSpellListControl,
)

from bartlebot.combat.card_kinds import classify_effects
from bartlebot.nodes.paths import ARCHMASTERY_GAUGE_PATH, archmastery_school_path
from bartlebot.nodes.timing import (
    ARCHMASTERY_ATTEMPTS,
    ARCHMASTERY_CONFIRM_POLL,
    ARCHMASTERY_CONFIRM_TIMEOUT,
    ARCHMASTERY_MENU_POLL,
    ARCHMASTERY_MENU_SETTLE,
    ARCHMASTERY_MENU_TIMEOUT,
    DECK_APPLY_TIMEOUT,
    DECK_CARD_CONFIRM_POLL,
    DECK_CARD_CONFIRM_TIMEOUT,
    DECK_CARD_COUNT_POLL,
    DECK_CARD_LAND_TIMEOUT,
    DECK_CARD_STABLE_WINDOW,
    DECK_FUSION_CLICK_SETTLE,
    DECK_FUSION_CLOSE_SETTLE,
    DECK_FUSION_FLYOUT_SETTLE,
    DECK_ITEM_PAGE_SETTLE,
    DECK_ITEM_TOGGLE_SETTLE,
    DECK_PAGE_OPEN_TIMEOUT,
    DECK_PAGE_POLL,
    DECK_SETTLE_POLL,
    DECK_SETTLE_TIMEOUT,
    DECK_SPELL_PAGE_SETTLE,
)

if TYPE_CHECKING:
    from bartlebot.shared.strategy_models import DeckData

# The slot whose deck this task is applying, for the log.  Up to four applies run
# at once under one gather, and a line saying "Only 1 of 3 copies of 'Epic'" is
# no use when it cannot say whose deck it was (#107).  A context variable rather
# than a parameter threaded through every helper: each gathered apply runs in its
# own task, and so in its own copy of the context.
_apply_slot: contextvars.ContextVar[int | None] = contextvars.ContextVar("deck_io_apply_slot", default=None)


class _SlotLogger(logging.LoggerAdapter):
    """Prefixes each record with ``[P<slot>]`` while an apply for that slot is running."""

    def process(self, msg, kwargs):
        slot = _apply_slot.get()
        return (f"[P{slot}] {msg}" if slot is not None else msg), kwargs


logger = _SlotLogger(logging.getLogger(__name__))

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
#
# It is not the only spelling a base form takes — see ``resolved_at_click_time``.
FUSION_ROOT_PATH = "Base"

# How many times a card's clicks are repeated when the deck does not grow by as
# much as was asked for.  Bounded because a refusal is indistinguishable from a
# lost click: the deck holds 64 cards and each spell has its own copy limit, so
# a click the game declines leaves the count flat exactly like a click that was
# swallowed.  Retrying forever would spin out the apply on a full deck.
CARD_CLICK_ATTEMPTS = 3

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

        Only some spells name their base form this way, and it opens a flyout
        only for a wizard holding two paths or more.
        ``resolved_at_click_time`` is the question callers actually want to ask.
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


@dataclass
class DeckApplyReport:
    """What actually happened to the deck, so the caller can say so.

    A partial apply used to be indistinguishable from a clean one: ``write_deck``
    returned ``None`` and wrote its findings to the log, so a wizard missing
    three of a shared deck's cards got a half-built deck and no indication that
    anything was wrong.  Saved decks name specific spells, and a deck built for
    someone else's wizard routinely names cards this one does not have — a
    different school's hitter cannot be substituted at all — so this is the
    normal case for a shared strategy, not an edge case.
    """

    substitutions: dict[str, str] = field(default_factory=dict)  # requested name → what was used
    skipped: list[str] = field(default_factory=list)  # requested names that never made it in
    # Skipped card → cards of the same kind this wizard *does* own.  Nothing is
    # substituted automatically: only the user knows which of their blades suits
    # the hitter the strategy was built around.
    alternatives: dict[str, list[str]] = field(default_factory=dict)
    kinds: dict[str, str] = field(default_factory=dict)  # skipped card → its recorded kind
    # Requested card → (copies that went in, copies asked for), for cards the
    # deck accepted only partly.  Distinct from ``skipped``, which is all-or-
    # nothing: a deck holding two of three copies is neither clean nor missing
    # the card, and saying nothing would put it back in the class of partial
    # applies that look like clean ones.
    short: dict[str, tuple[int, int]] = field(default_factory=dict)
    # Deck card → (copies it holds, copies asked for), for a card that ended up
    # with more copies than asked for and could not be trimmed back.  A late
    # copy racing a re-click can do this (#107), and an extra copy of a hitter
    # changes the deck's odds just as a missing one does.
    over: dict[str, tuple[int, int]] = field(default_factory=dict)
    applied: bool = True  # False when nothing was placeable and the deck was left alone
    unchanged: bool = False  # already matched, so no clear/rewrite was needed
    # The archmastery school this apply actually set, or None if it was left
    # alone -- because the deck did not opt in, recorded no school, or already
    # matched.  Reported so a run's log says a persistent character setting
    # changed; a *successful* change is not worth a dialog, since opting in is
    # what asked for it.
    archmastery_set: str | None = None
    # The school an opted-in apply asked for and could not set.  This one does
    # warrant telling the user: they turned it on, and it silently did nothing.
    archmastery_failed: str | None = None

    @property
    def is_clean(self) -> bool:
        """True when there is nothing about this apply worth telling the user.

        Cards that went in under their own name, and an archmastery school that
        either was not asked for or was set successfully.  A failed school set
        counts as unclean for the same reason a skipped card does: it was asked
        for and did not happen.
        """
        return (
            not self.substitutions
            and not self.skipped
            and not self.short
            and not self.over
            and not self.archmastery_failed
        )

    def merge(self, substitutions: dict[str, str], skipped: list[str]) -> None:
        self.substitutions.update(substitutions)
        self.skipped = sorted(set(self.skipped) | set(skipped))


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


def resolved_at_click_time(requested: str, entry: str) -> bool:
    """Whether ``entry`` is a *base form*, so what it yields is only known once clicked.

    A wizard unlocks at most one tier per upgrade path, and the spellbook shows
    one entry per fusion spell whose meaning depends on how many paths they have:

    ======  ==============================  ==========================
    paths   the entry reads                 clicking it
    ======  ==============================  ==========================
    0       the base form                   adds the base card
    1       that variant, "… - T04 - A"     adds that card
    2+      the base form                   opens the variant flyout
    ======  ==============================  ==========================

    The base form is spelled either way — "Novus Storm - T01 - Base" says so,
    while "Bunyips Rage" is just the plain name — and which convention a spell
    follows is not derivable, so both are treated the same.  Nor does the
    spelling track when the spell entered the game: one spellbook holds
    "Storm Lord - T02 - A" and a bare "Bunyips Rage" at once.

    Rows 0 and 2 are *identical* on the page, which is why this returns only
    "resolve it later" and not which of the two it is.  ``_add_fusion_cards``
    clicks and looks: a flyout means row 2, and no flyout means row 0 and the
    base card is already in the deck.  Verified with
    ``tools/probe_spell_list.py`` — clicking a bare "Bunyips Rage" on a wizard
    holding two paths fills TieredSpellMPUnlockedList with "- T05 - A" and
    "- T05 - B".

    The tell is the pair, not the entry: the deck asked for a tier, so the spell
    has tiers, and the entry carries none, so it is the base form.  A card that
    genuinely has no tiers cannot be asked for by tiered name, because saved
    decks record the name the game itself reported.  Row 1 is excluded on the
    same evidence — a tiered entry is the wizard's one unlocked variant, and
    ``resolve_tier`` decides against it directly.

    This was the multi-tier spell bug.  Treating "- T01 - Base" as the base form's only spelling
    sent every plainly-named one to ``unresolvable``, reporting spells the
    wizard had as untrained.  It worked before fusion routing existed only by
    accident: wizwalker's tiered branch looks up the bare base, which is exactly
    the spelling this handles.
    """
    have = parse_tiered_name(entry)
    if have.is_fusion_root:
        return True
    return parse_tiered_name(requested).is_tiered and not have.is_tiered


def plan_normal_cards(desired: dict[str, int], available: list[str]) -> NormalCardPlan:
    """Split a saved deck's normal cards into what this account can and cannot supply.

    Runs *before* the deck is cleared.  wizwalker's ``_add_all_normal_cards``,
    which placed these cards before ``_add_direct_cards``, raised on the first
    name it could not place after ``write_deck`` had cleared the deck — so a single unowned card used to leave the wizard with an empty deck
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
        too.  ``_add_direct_cards`` clicks these straight off the spellbook.

    *fusion* — the spellbook entry is the spell's *base form*, which yields
        either a variant flyout or the base card itself depending on how many
        upgrade paths this wizard has unlocked — a difference the page does not
        show.  See ``resolved_at_click_time``.  ``_add_fusion_cards`` clicks and
        finds out, because wizwalker cannot: its tiered branch looks up the bare
        base, which happens to match a plainly-named base form and misses every
        "… - T01 - Base" one.

    *unresolvable* — no spellbook entry shares the base, or the only unlocked
        variant is on another path or a higher tier than asked for.

    Splitting up front means nothing that runs after the clear has to decide
    what kind of name it was handed.

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
        elif resolved_at_click_time(name, entry):
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


def expected_normal_deck(direct: dict[str, int], fusion_placed: dict[str, int]) -> dict[str, int]:
    """The normal deck a completed write should produce, keyed by spellbook name.

    This is deliberately *not* the deck that was asked for.  Cards this wizard
    cannot supply were dropped before the clear, a tiered card may have gone in
    under a lower tier, and a click may have been refused outright — but all of
    that is known by the time the write finishes, so the end state is known
    exactly.  That is what lets the settle wait for a match instead of waiting
    out a timeout on every apply.

    ``direct`` is keyed by the entry that was clicked, since ``partition_addable``
    resolves substitutions into it, and like ``fusion_placed`` it holds the
    copies ``_place_copies`` confirmed rather than the copies asked for.  ``fusion_placed`` is keyed by the card that
    was actually clicked and counted by ``_add_fusion_cards``, which is why no
    renaming or skip-filtering happens here any more: a count that was confirmed
    against the deck already reflects every substitution and every shortfall.
    """
    expected = dict(direct)
    for card, count in fusion_placed.items():
        expected[card] = expected.get(card, 0) + count
    return expected


def requested_per_card(
    direct: dict[str, dict[str, int]], fusion: dict[str, int], fusion_subs: dict[str, str]
) -> dict[str, dict[str, int]]:
    """Each deck card that went in, mapped to the requested names behind it and their counts.

    ``direct`` is keyed by spellbook entry already (see ``direct_requesters``);
    a fusion name lands on the variant it resolved to, or on itself when it was
    placed as asked.  A card both routes reached gets both sets of names.  The
    sum for a card is how many copies of it the deck asked for: the ceiling the
    settle trims down to, and what the final read is reported against.
    """
    asked: dict[str, dict[str, int]] = {card: dict(names) for card, names in direct.items()}
    for name, count in fusion.items():
        asked.setdefault(fusion_subs.get(name, name), {})[name] = count
    return asked


def tally_against_request(
    asked: dict[str, dict[str, int]], held: dict[str, int]
) -> tuple[dict[str, tuple[int, int]], dict[str, tuple[int, int]]]:
    """Compare what the deck holds against what was asked for: ``(short, over)``.

    ``short`` is keyed by requested name, like ``DeckApplyReport.short``, with a
    card's copies shared out across the names that asked for it.  ``over`` is
    keyed by deck card, because an extra copy belongs to no request.
    """
    short: dict[str, tuple[int, int]] = {}
    over: dict[str, tuple[int, int]] = {}
    for card, names in asked.items():
        total = sum(names.values())
        got = held.get(card, 0)
        if got > total:
            over[card] = (got, total)
        elif got < total:
            for name, share in _share_shortfall(names, got).items():
                short[name] = (share, names[name])
    return short, over


def direct_requesters(addable: dict[str, int], routing: CardRouting) -> dict[str, dict[str, int]]:
    """The deck names behind each direct spellbook entry, with their counts.

    ``partition_addable`` folds a substituted tier into the entry it resolved to,
    and sums the copies when two names land on one entry.  The report still has to
    say which *requested* card fell short or never went in, so this undoes the
    fold.  The counts for one entry sum to ``routing.direct[entry]``.
    """
    by_entry: dict[str, dict[str, int]] = {}
    for name, count in addable.items():
        entry = routing.substitutions.get(name, name)
        if entry in routing.direct and name not in routing.fusion and name not in routing.unresolvable:
            by_entry.setdefault(entry, {})[name] = count
    return by_entry


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


async def _template_kind(template) -> str | None:
    """Classify a spell from its template's effects, or None if it cannot be read.

    Never raises: a card whose kind we cannot work out is simply unlabeled, and
    losing a label is not worth failing a deck import over.
    """
    try:
        effects = await template.effects()
        readings = []
        for effect in effects or []:
            readings.append(
                (
                    await effect.effect_type(),
                    await effect.effect_target(),
                    await effect.effect_param(),
                    await effect.num_rounds(),
                )
            )
        return classify_effects(readings)
    except Exception:
        logger.debug("Could not classify a spell template", exc_info=True)
        return None


async def _entry_card(control, address: int, classify: bool) -> tuple[str, str | None] | None:
    entry = DeckListControlSpellEntry(control.hook_handler, address)
    graphical = await entry.graphical_spell()
    if not graphical:
        return None
    template = await graphical.spell_template()
    if not template:
        return None
    name = (await template.name()) or None
    if not name:
        return None
    return name, (await _template_kind(template) if classify else None)


async def _entry_name(control, address: int) -> str | None:
    card = await _entry_card(control, address, classify=False)
    return card[0] if card else None


async def read_deck_cards(client, deck_window, *, classify: bool = False) -> list[tuple[str, str | None]]:
    """Every card in the normal deck as ``(name, kind)``, in slot order.

    ``classify`` is off by default because it costs an extra effects read per
    card and only the *import* path needs it — the equality check in
    ``_deck_already_matches`` runs on every apply and cares only about names.
    """
    control = await _control_for(client, deck_window, "CardsInDeck")
    begin, count = await _entry_vector(control)
    cards: list[tuple[str, str | None]] = []
    for idx in range(count):
        try:
            card = await _entry_card(control, begin + idx * ENTRY_STRIDE, classify)
        except Exception:
            logger.debug("Unreadable deck entry at index %d", idx, exc_info=True)
            continue
        if card:
            cards.append(card)
    return cards


async def read_deck_card_names(client, deck_window) -> list[str]:
    """Names of every card in the normal deck, in slot order."""
    return [name for name, _kind in await read_deck_cards(client, deck_window)]


async def _deck_entry_names(client, deck_window) -> list[str | None]:
    """Every ``CardsInDeck`` entry's name by vector index, None where one cannot be read.

    Kept positional, unlike ``read_deck_card_names``, because the index is the
    grid slot a click on the deck lands on.
    """
    control = await _control_for(client, deck_window, "CardsInDeck")
    begin, count = await _entry_vector(control)
    names: list[str | None] = []
    for idx in range(count):
        try:
            names.append(await _entry_name(control, begin + idx * ENTRY_STRIDE))
        except Exception:
            names.append(None)
    return names


async def _trim_copy(db, deck_window, name: str) -> bool:
    """Take one copy of ``name`` out of the deck by clicking it there.  True once it is gone.

    Clicking a card in the deck removes one copy of it.  The click goes on the
    card's last copy, located by vector index in the 8x8 grid wizwalker's
    ``calculate_deck_card_position`` lays out.

    The result is read back rather than assumed, and only ``name`` going down
    counts as success.  Anything else — nothing leaving, or a different card
    leaving because the grid is not in vector order after all — returns False,
    and ``_settle_deck`` stops trimming, so a wrong guess costs one card and is
    reported rather than repeated.
    """
    names = await _deck_entry_names(db.client, deck_window)
    slots = [idx for idx, entry in enumerate(names) if entry == name]
    if not slots:
        return False
    before = count_names([entry for entry in names if entry])
    position = await db.calculate_deck_card_position(slots[-1] + 1)
    async with db.client.mouse_handler:
        await db.client.mouse_handler.click(*position)

    lost: set[str] = set()
    deadline = time.perf_counter() + DECK_CARD_STABLE_WINDOW
    while True:
        await asyncio.sleep(DECK_CARD_COUNT_POLL)
        try:
            now = count_names(await read_deck_card_names(db.client, deck_window))
        except Exception:
            now = {}
        if now:  # an empty read is a failed one, not a deck that lost everything
            lost = {card for card, n in before.items() if now.get(card, 0) < n}
            if lost == {name}:
                return True
        if time.perf_counter() >= deadline:
            break
    if lost - {name}:
        logger.warning(
            "Clicking an extra '%s' in the deck took out %s instead — not trimming any further",
            name,
            ", ".join(sorted(lost - {name})),
        )
    return False


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


def _item_active_reader(client, deck_window):
    """An async callable reading item slot ``index``'s active flag, or None if unreadable.

    Mirrors ``card_counter``: built once and reused for the whole toggle sweep,
    and rebuilt on the next call if a read fails rather than raising, since a
    stale control is exactly as recoverable as an unreadable card count. The
    entry vector holds every item card regardless of which page is on screen,
    so this confirms a click without needing to be rebuilt when the page turns.
    """
    control = None
    begin = None

    async def read(index: int) -> bool | None:
        nonlocal control, begin
        try:
            if control is None:
                control = await _control_for(client, deck_window, "ItemSpells")
                begin, _total = await _entry_vector(control)
            flag = await control.read_bytes(begin + index * ENTRY_STRIDE + ACTIVE_FLAG_OFFSET, 1)
        except Exception:
            logger.debug("Could not read item slot %d while confirming a toggle", index, exc_info=True)
            control = None
            return None
        return flag[0] == ACTIVE_FLAG_ON

    return read


async def _await_item_toggle(read_active, index: int, want_active: bool) -> bool:
    """Poll until item slot ``index`` reads as ``want_active``.  Returns whether it did.

    A click here is exactly as easy to lose as a spell-card click, and used to
    be trusted after one fixed ``DECK_ITEM_TOGGLE_SETTLE`` pause — the caller
    would find out only once ``_settle_deck`` timed out, and never retried.
    This polls the flag directly instead, the same shape as
    ``_await_card_growth``: an unreadable slot is not evidence the click
    failed, so it is reported as settled rather than driving a retry that could
    toggle an already-successful click right back off.
    """
    deadline = time.perf_counter() + DECK_CARD_CONFIRM_TIMEOUT
    readable = False
    while True:
        state = await read_active(index)
        if state is not None:
            readable = True
            if state == want_active:
                return True
        if time.perf_counter() >= deadline:
            return not readable
        await asyncio.sleep(DECK_CARD_CONFIRM_POLL)


async def _set_item_page(client, deck_window, page: int, total_pages: int) -> None:
    """Put ItemSpells on ``page`` (0-based) by writing the list's start index.

    Replaces rewinding with Prev and clicking Next, which cannot be mixed with a
    write: Prev stays disabled on a written page.  See PAGING in the module
    docstring.  Only whole pages inside the list are written; nothing past the
    end has been probed.
    """
    if not 0 <= page < total_pages:
        raise ValueError(f"item page {page} is outside the {total_pages} page(s) of item cards")
    control = await _control_for(client, deck_window, "ItemSpells")
    await control.write_start_index(page * SLOTS_PER_PAGE)
    await asyncio.sleep(DECK_ITEM_PAGE_SETTLE)


async def _apply_item_toggles(db, deck_window, plan: list[ItemSlot], total_pages: int) -> None:
    """Click each planned slot, writing its item page first whenever it changes.

    ``plan`` is in index order, so each page is written once.  The first slot's
    page is always written, because the page the list was left on is unknown.

    Each click is confirmed against the slot's own active flag, and re-clicked
    up to ``CARD_CLICK_ATTEMPTS`` times if it does not land — see
    ``_await_item_toggle``.  A slot whose page cannot be written is skipped
    rather than clicked, since the click would toggle whatever sits there on the
    page still showing.
    """
    if not plan:
        return

    client = db.client
    read_active = _item_active_reader(client, deck_window)

    current_page: int | None = None
    for slot in plan:
        if slot.page != current_page:
            try:
                await _set_item_page(client, deck_window, slot.page, total_pages)
            except Exception:
                logger.warning(
                    "Could not turn the item cards to page %d — slot %d ('%s') is not toggled",
                    slot.page + 1,
                    slot.index,
                    slot.name,
                    exc_info=True,
                )
                current_page = None
                continue
            current_page = slot.page

        rect = await db.get_item_spells_rectangle()
        cells = db.divide_rectangle(rect, columns=GRID_COLUMNS, rows=GRID_ROWS)
        center = cells[slot.slot_index].center()
        want_active = not slot.active

        settled = False
        for _attempt in range(CARD_CLICK_ATTEMPTS):
            async with client.mouse_handler:
                await client.mouse_handler.click(*center)
            await asyncio.sleep(DECK_ITEM_TOGGLE_SETTLE)
            settled = await _await_item_toggle(read_active, slot.index, want_active)
            if settled:
                break
            logger.debug("Item card slot %d ('%s') did not toggle, clicking again", slot.index, slot.name)
        if not settled:
            logger.warning(
                "Item card slot %d ('%s') did not toggle after %d attempt(s)",
                slot.index,
                slot.name,
                CARD_CLICK_ATTEMPTS,
            )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def suggest_alternatives(
    skipped: list[str],
    deck: DeckData,
    owned_kinds: dict[str, str],
) -> dict[str, list[str]]:
    """For each skipped card of a known kind, the wizard's own cards of that kind.

    Cards already in the deck are excluded — offering one the deck is about to
    place anyway is not a substitution, just noise.  Skipped cards with no
    recorded kind (an attack spell, or a deck saved before kinds existed) get no
    entry: there is nothing useful to say about a missing Storm hitter to a Fire
    wizard, and inventing a suggestion would be worse than silence.
    """
    out: dict[str, list[str]] = {}
    for name in skipped:
        kind = deck.card_kinds.get(name)
        if not kind:
            continue
        matches = sorted(owned for owned, owned_kind in owned_kinds.items() if owned_kind == kind and owned != name)
        if matches:
            out[name] = matches
    return out


async def _spell_list_kinds(db) -> dict[str, str]:
    """Name → kind for every classifiable card in the account's spell list.

    Only called once an apply has already skipped something, so its extra
    per-spell effects reads never touch the path where the deck applies cleanly.
    """
    kinds: dict[str, str] = {}
    for spell in await db.get_graphical_spell_cards():
        template = await spell.spell_template()
        if not template:
            continue
        name = await template.name()
        if not name or name in kinds:
            continue
        kind = await _template_kind(template)
        if kind:
            kinds[name] = kind
    return kinds


async def _spell_list_control(db):
    window = await _maybe_get_named_window(db._deck_config_window, "AllPageSpellList")  # noqa: SLF001
    return DynamicSpellListControl(db.client.hook_handler, await window.read_base_address())


async def _spell_list_names(db) -> list[str]:
    """Template names of every entry in the account's spell list, in vector order.

    Order matters: ``calcuate_position_of_card_in_page`` derives a card's page
    and slot from its index here, so this must stay a positional list rather
    than a set.

    Read from the raw entry vector, not wizwalker's ``get_graphical_spell_cards``.
    That one drops entries it cannot read, and every card after a dropped entry
    would then be computed one slot early -- the wrong card, or the wrong page,
    because the page written is an index into the raw vector.  An unreadable
    entry is kept as ``""`` so the positions still line up.
    """
    control = await _spell_list_control(db)
    names: list[str] = []
    for entry in await control.spell_entries():
        try:
            graphical = await entry.graphical_spell()
            template = await graphical.spell_template() if graphical else None
            names.append((await template.name() or "") if template else "")
        except Exception:
            logger.debug("Unreadable spellbook entry at index %d", len(names), exc_info=True)
            names.append("")
    return names


async def _owned_tier_variants(db) -> list[str]:
    """Template names currently shown in the tier flyout, empty if there is none.

    No flyout is a real answer, not a failure.  The game only offers one for a
    spell with two or more paths unlocked; with one or none, clicking the
    spellbook entry adds a card outright.  Since the entry looks the same either
    way (see ``resolved_at_click_time``), this read is how the caller finds out
    which happened, so a missing window has to come back as "nothing unlocked"
    rather than as an exception.

    Only the window lookup is forgiven.  A memory read that fails once the
    flyout *is* open still raises, because that is our bug and guessing "nothing
    unlocked" would hide it behind a base-form substitution.
    """
    names: list[str] = []
    try:
        spells = await db.get_graphical_tiered_spell_cards()
    except ValueError:
        logger.debug("No tier flyout is open; treating this spell as having no unlocked variants")
        return []
    for spell in spells:
        if not spell:
            continue
        template = await spell.spell_template()
        if template:
            name = await template.name()
            if name:
                names.append(name)
    return names


def card_counter(client, deck_window):
    """An async callable giving how many copies of one card the normal deck holds, or None if unreadable.

    Counts the card by *name*, read fresh on every call.  This used to count the
    whole deck from its entry vector's begin and end pointers, through a control
    built once and reused, and confirm a click by the deck growing.  That count
    stayed flat for seconds while the copies went in anyway (#107): three clicks
    on Epic confirmed one, the retries clicked four more, and the deck came out
    holding the copy limit.  The per-name read here is the one ``_settle_deck``
    makes, which read that same deck correctly.

    Naming every card costs about four memory reads a card rather than two for
    the whole deck, which is why this is polled at ``DECK_CARD_COUNT_POLL``.
    """

    async def count(name: str) -> int | None:
        try:
            names = await read_deck_card_names(client, deck_window)
        except Exception:
            logger.debug("Could not read the deck to count '%s'", name, exc_info=True)
            return None
        return names.count(name)

    return count


async def _await_card_count(count_card, name: str, have: int, target: int) -> int:
    """Poll ``name``'s copy count until it reaches ``target`` or stops moving.  Returns the highest count read.

    Stops moving means unchanged for ``DECK_CARD_STABLE_WINDOW``: a count that is
    still climbing is copies arriving late, and clicking again then is how a deck
    overshoots.  ``DECK_CARD_LAND_TIMEOUT`` bounds a count that creeps forever.

    The highest reading wins, not the latest.  Copies only arrive while a card is
    being placed, so a lower reading is a read that caught the list mid-update
    or skipped an unreadable entry, and trusting it would click a copy again.

    Returns ``target`` when the deck cannot be read at all.  An unreadable count
    is not evidence that the click failed, and treating it as failure would make
    the caller click again — turning a broken memory read into duplicate copies.
    """
    start = time.perf_counter()
    last_change = start
    best = have
    readable = False
    while True:
        await asyncio.sleep(DECK_CARD_COUNT_POLL)
        now = await count_card(name)
        tick = time.perf_counter()
        if now is not None:
            readable = True
            if now > best:
                best, last_change = now, tick
        if best >= target:
            return best
        if tick - last_change >= DECK_CARD_STABLE_WINDOW or tick - start >= DECK_CARD_LAND_TIMEOUT:
            break
    if not readable:
        logger.debug("Deck unreadable while confirming '%s', assuming the clicks went in", name)
        return target
    return best


async def _place_copies(db, count_card, position: tuple[int, int], wanted: int, label: str) -> int:
    """Click ``position`` until ``wanted`` more copies of ``label`` are in the deck.  Returns how many landed.

    Confirming beats sleeping here because a click can be lost outright — the
    flyout used to be closed 50 ms after the last one, which is the whole reason
    this exists — and no fixed delay can tell a slow click from a dropped one.

    Every round clicks only what the deck, read just now, still lacks, and only
    after the count has stopped moving (see ``_await_card_count``).  Adding up
    what each round confirmed instead is what #107 was: the first round's late
    copies never got counted, so every later round clicked them again.

    The count can still come out over ``wanted`` if a copy arrives later than
    the stable window allows for.  That is returned as it is rather than capped;
    ``_settle_deck`` is what takes the extra back out.
    """
    baseline = await count_card(label)
    if baseline is None:
        # Nothing to confirm against; the clicks are all we have.
        async with db.client.mouse_handler:
            for _ in range(wanted):
                await db.client.mouse_handler.click(*position)
                await asyncio.sleep(DECK_FUSION_CLICK_SETTLE)
        return wanted

    target = baseline + wanted
    have = baseline
    for attempt in range(CARD_CLICK_ATTEMPTS):
        if attempt:
            # Once more right before clicking, for a copy that lands in the gap.
            now = await count_card(label)
            have = max(have, now or 0)
            if have >= target:
                break
            logger.debug("'%s': %d of %d copies in the deck, clicking again", label, have - baseline, wanted)
        async with db.client.mouse_handler:
            for _ in range(target - have):
                await db.client.mouse_handler.click(*position)
                await asyncio.sleep(DECK_FUSION_CLICK_SETTLE)
        have = await _await_card_count(count_card, label, have, target)
        if have >= target:
            break

    landed = have - baseline
    if landed < wanted:
        logger.warning("Only %d of %d copies of '%s' went into the deck", landed, wanted, label)
    elif landed > wanted:
        logger.info("%d copies of '%s' went in where %d were clicked for", landed, label, wanted)
    return landed


async def _close_tier_flyout(db) -> None:
    """Dismiss the tier flyout.

    Either way the spellbook page is lost.  The close button restores it from the
    game's button-driven page counter, which a written page never moved, so it
    lands on page one; the fallback reopens the deck page, which does the same.
    Callers write the page again before their next click rather than track
    where it went.
    """
    try:
        close_btn = await _maybe_get_named_window(db._deck_config_window, "CloseTSMPUnlockedPageButton")  # noqa: SLF001
        async with db.client.mouse_handler:
            await db.client.mouse_handler.click_window(close_btn)
    except Exception:
        logger.debug("Tier flyout close button unavailable, refreshing the deck page", exc_info=True)
        await db.refresh_deck_page()


async def _set_spell_page(db, page: int) -> None:
    """Put the spellbook on ``page`` (0-based) by writing the list's start index.

    One write from anywhere, where the PageDown clicks it replaces cost a click
    per page plus a full rewind from an unknown position.  wizwalker's
    ``set_page`` raises ``ValueError`` for a page outside the spellbook.  Never
    mix this with the page buttons -- see PAGING in the module docstring.
    """
    await db.set_page(page)
    await asyncio.sleep(DECK_SPELL_PAGE_SETTLE)


async def _add_direct_cards(
    db, count_cards, direct: dict[str, int], requested_by: dict[str, dict[str, int]], spell_names: list[str]
) -> tuple[dict[str, int], list[str], dict[str, tuple[int, int]]]:
    """Click each direct card straight off the spellbook, writing its page first.

    Replaces wizwalker's ``_add_all_normal_cards``, which paged with PageUp and
    PageDown -- a rewind of up to fifty clicks, then one click per page -- and
    trusted every card click after a fixed pause.  Here reaching a page is one
    write, and each card goes through ``_place_copies``, so a lost click is
    noticed and clicked again the same way a fusion variant's is.

    Positions are computed before any page is written: slot coordinates come
    from the list control's rectangle and do not change with the page.

    ``requested_by`` maps each spellbook entry to the deck names that resolved to
    it (see ``direct_requesters``), because the report speaks in requested names.
    Returns ``(placed, skipped, short)``: confirmed copies by entry, requested
    names of which not one copy went in, and requested name -> (got, asked) for
    the rest.  Never raises -- this runs after the clear.
    """
    placed: dict[str, int] = {}
    skipped: list[str] = []
    short: dict[str, tuple[int, int]] = {}

    sweep: list[tuple[int, str, tuple[int, int]]] = []
    for entry in direct:
        try:
            page_idx, card_idx = db.calcuate_position_of_card_in_page(spell_names, entry)
            sweep.append((page_idx, entry, await db.calculate_card_position(card_idx)))
        except Exception:
            logger.warning("Could not locate '%s' in the spellbook — it is skipped", entry, exc_info=True)
            skipped.extend(requested_by.get(entry, {entry: direct[entry]}))
    sweep.sort(key=lambda item: item[0])

    for page_idx, entry, position in sweep:
        wanted = direct[entry]
        requesters = requested_by.get(entry, {entry: wanted})
        try:
            await _set_spell_page(db, page_idx)
            landed = await _place_copies(db, count_cards, position, wanted, entry)
        except Exception:
            # Which copies went in before the failure is not knowable, so report
            # the card rather than claim a clean partial -- as the fusion pass does.
            logger.warning("Failed while adding '%s'", entry, exc_info=True)
            skipped.extend(requesters)
            continue
        if not landed:
            skipped.extend(requesters)
            continue
        placed[entry] = min(landed, wanted)  # an extra copy is the settle's to trim
        if landed < wanted:
            for name, got in _share_shortfall(requesters, landed).items():
                short[name] = (got, requesters[name])
    return placed, skipped, short


async def _add_fusion_cards(
    db, count_cards, wanted: dict[str, int], spell_names: list[str]
) -> tuple[dict[str, str], list[str], dict[str, int], dict[str, int]]:
    """Add fusion variants by opening each root's flyout and clicking inside it.

    wizwalker cannot do this.  Its tiered branch locates the base spell by its
    *bare* name ("Novus Storm"), but the spellbook lists a fusion root as
    "Novus Storm - T01 - Base", so the lookup misses and it raises — after the
    deck has already been cleared.

    Resolution happens here rather than before the clear because the flyout is
    the only place the unlocked variants are legible, and opening it twice (once
    to read, once to click) would double the click cost for no benefit: nothing
    below raises, so a failure costs the affected cards and nothing more.

    The click is also the only way to learn whether there is a flyout at all.  A
    base-form entry opens one for a wizard holding two or more upgrade paths and
    simply adds the base card for a wizard holding none, and the spellbook page
    reads the same in both cases (see ``resolved_at_click_time``).  So this
    clicks, then looks: variants mean the first, nothing means the second and
    ``_fill_base_form`` finishes what the click started.

    One flyout open/close per distinct base spell, regardless of how many
    variants or copies of it the deck asks for.  Each base's page is written
    directly (``_set_spell_page``) rather than paged to, so reaching it costs the
    same wherever the spellbook was left -- which matters, because closing a
    flyout resets a written page.  Bases are still visited in page order, so the
    log reads in spellbook order.

    Every click is confirmed against the deck's card count rather than trusted
    after a fixed delay.  The flyout used to be closed 50 ms after the last
    click of each base, which is long enough to lose it — the same bug
    ``_settle_deck`` was written for, one scope in.  Confirming also means the
    counts returned here are what the deck actually holds, so the settle is not
    handed an expectation the write already knows to be wrong.

    Returns ``(substitutions, skipped, placed, short)`` across every base, so a
    partial result here reaches the user rather than stopping at the log.
    """
    substitutions: dict[str, str] = {}
    skipped: list[str] = []
    placed: dict[str, int] = {}
    short: dict[str, int] = {}
    by_base: dict[str, dict[str, int]] = {}
    for name, count in wanted.items():
        by_base.setdefault(parse_tiered_name(name).base, {})[name] = count
    entries = {parse_tiered_name(entry).base: entry for entry in spell_names}

    # Positions are computed before a single page is turned, so the sweep below
    # is clicks and nothing else.  Slot coordinates come from the list control's
    # own rectangle and do not vary by page, and wizwalker precomputes them for
    # the same reason: reading the spell list mid-sweep risks a vector that the
    # clicks have since disturbed.
    sweep: list[tuple[int, str, str, tuple[int, int], dict[str, int]]] = []
    for base, requested in by_base.items():
        entry = entries.get(base)
        if entry is None:  # partition_addable only routes cards here when it exists
            skipped.extend(requested)
            continue
        try:
            page_idx, card_idx = db.calcuate_position_of_card_in_page(spell_names, entry)
            position = await db.calculate_card_position(card_idx)
        except Exception:
            logger.warning("Could not locate '%s' in the spellbook — its cards are skipped", base, exc_info=True)
            skipped.extend(requested)
            continue
        sweep.append((page_idx, base, entry, position, requested))
    sweep.sort(key=lambda item: item[0])

    for page_idx, base, entry, position, requested in sweep:
        try:
            # Written for every base, even one on the page already showing: the
            # previous base's flyout close reset it (see _close_tier_flyout).
            await _set_spell_page(db, page_idx)
            # Taken before the click, because this one is ambiguous: it opens a
            # flyout for a wizard holding two paths and adds a card for one
            # holding none, and only the count says which happened.
            before = await count_cards(entry)
            async with db.client.mouse_handler:
                await db.client.mouse_handler.click(*position)
            # The click is what populates the variant list; reading straight
            # away can return the previous spell's variants, or nothing.
            await asyncio.sleep(DECK_FUSION_FLYOUT_SETTLE)
            owned = await _owned_tier_variants(db)
        except Exception:
            logger.warning("Could not open the variant list for '%s' — its cards are skipped", base, exc_info=True)
            skipped.extend(requested)
            continue

        if not owned:
            # No flyout opened, so that click did not open anything — it added the
            # base card.  This wizard has no path unlocked, which the spellbook
            # cannot say in advance.  Finish the job rather than undo it: the base
            # form is the same spell, weaker, and it is the only version they have.
            base_subs, base_placed, base_short = await _fill_base_form(
                db, count_cards, requested, entry, position, before
            )
            if not base_placed:
                # Not one copy reached the deck, so there is no substitution to
                # claim — the wizard is simply without the card.
                skipped.extend(requested)
                continue
            substitutions.update(base_subs)
            short.update(base_short)
            for card, n in base_placed.items():
                placed[card] = placed.get(card, 0) + n
            continue

        try:
            base_subs, base_skipped, base_placed, base_short = await _click_fusion_variants(
                db, count_cards, requested, owned, base
            )
            substitutions.update(base_subs)
            skipped.extend(base_skipped)
            short.update(base_short)
            for card, n in base_placed.items():
                placed[card] = placed.get(card, 0) + n
        except Exception:
            logger.warning("Failed while adding variants of '%s'", base, exc_info=True)
            # Which of this base's cards made it in before the failure is not
            # knowable, so report the lot rather than claim a clean partial.
            skipped.extend(requested)
        finally:
            # The count that confirmed each click can run ahead of the server
            # actually committing the card; closing the flyout in that gap has
            # been observed to drop it. See DECK_FUSION_CLOSE_SETTLE.
            await asyncio.sleep(DECK_FUSION_CLOSE_SETTLE)
            await _close_tier_flyout(db)

    return substitutions, skipped, placed, short


async def _fill_base_form(
    db, count_cards, requested: dict[str, int], entry: str, position: tuple[int, int], before: int | None
) -> tuple[dict[str, str], dict[str, int], dict[str, int]]:
    """Top up the base card that opening the flyout turned out to add, and report it.

    Reached only when the flyout never appeared, which means the click that was
    meant to open it added ``entry`` to the deck instead.  ``before`` is the
    count taken just ahead of that click, so how many copies it really placed is
    measured rather than assumed to be one — a click that landed on nothing
    would otherwise leave the deck a card short of what is reported.

    Every requested variant of this base collapses onto the same card, so the
    copies are summed: a deck asking for two "- T05 - A" and one "- T05 - B"
    gets three base cards, which is the shape the strategy expects even though
    every one of them is weaker than asked for.

    Returned as substitutions rather than skips because the card *is* in the
    deck.  Reporting it as skipped would be a lie in both directions — the wizard
    would be told they lack a spell they are holding, and ``expected_normal_deck``
    would wait out its timeout on a deck that is exactly right.
    """
    total = sum(requested.values())
    # Waited for rather than read once: a copy that is only late would be topped
    # up by one click too many (#107).
    now = None if before is None else await _await_card_count(count_cards, entry, before, before + 1)
    already = 1 if before is None or now is None else max(0, min(total, now - before))
    landed = already
    if total > already:
        landed += await _place_copies(db, count_cards, position, total - already, entry)
    for name in requested:
        logger.info(
            "Using '%s' in place of '%s' — this wizard has no upgrade path unlocked for it",
            entry,
            name,
        )
    landed = min(landed, total)  # an extra copy is the settle's to trim
    placed = {entry: landed} if landed else {}
    short = _share_shortfall(requested, landed) if landed < total else {}
    return dict.fromkeys(requested, entry), placed, short


def _share_shortfall(requested: dict[str, int], landed: int) -> dict[str, int]:
    """Split ``landed`` copies of one card back across the names that asked for it.

    Every variant of a base form collapses onto the same card, so when fewer
    copies go in than were asked for there is no saying which request went
    unfilled.  Filling them in request order is arbitrary but complete, which is
    what the report needs: the per-name counts add up to what the deck holds.
    """
    short: dict[str, int] = {}
    remaining = landed
    for name, count in requested.items():
        got = min(count, remaining)
        remaining -= got
        if got < count:
            short[name] = got
    return short


async def _click_fusion_variants(
    db, count_cards, requested: dict[str, int], owned: list[str], base: str
) -> tuple[dict[str, str], list[str], dict[str, int], dict[str, int]]:
    """Click each requested variant inside the already-open flyout.

    Returns ``(substitutions, skipped, placed, short)``.  Which variant a fusion
    card resolves to is only knowable with the flyout open, so this is the only
    place those facts exist — the caller cannot recompute them.

    ``placed`` counts what reached the deck, keyed by the variant actually
    clicked, and ``short`` records any card that fell behind what was asked for.
    Both are measured rather than assumed: the caller feeds ``placed`` to
    ``expected_normal_deck``, so a card that went in twice out of three times
    leaves the settle expecting two, not three.
    """
    substitutions: dict[str, str] = {}
    skipped: list[str] = []
    placed: dict[str, int] = {}
    short: dict[str, int] = {}
    for name, count in requested.items():
        resolved = resolve_tier(name, owned)
        if resolved is None:
            logger.warning(
                "No usable variant of '%s' for this wizard (deck wants '%s', unlocked: %s) — skipping",
                base,
                name,
                ", ".join(owned) or "none",
            )
            skipped.append(name)
            continue
        if resolved != name:
            logger.info(_SUBSTITUTION_MSG, resolved, name)
            substitutions[name] = resolved
        _page, variant_idx = db.calcuate_position_of_card_in_page(owned, resolved)
        variant_position = await db.calculate_tiered_spell_card_position(variant_idx)
        landed = await _place_copies(db, count_cards, variant_position, count, resolved)
        if landed:
            placed[resolved] = placed.get(resolved, 0) + min(landed, count)  # the settle trims any extra
        if landed < count:
            short[name] = landed
        if landed == 0:
            # Nothing of this card reached the deck, which is what "skipped"
            # means.  Reported there so it sits beside the cards the spellbook
            # genuinely lacks, rather than as a substitution that never happened.
            skipped.append(name)
            substitutions.pop(name, None)
    return substitutions, skipped, placed, short


async def read_archmastery_school(client) -> str | None:
    """The wizard's archmastery school by name, or None if unset or unreadable.

    Read straight out of memory rather than off the deck page: the school lives
    on the equipped deck *item*'s behavior (``m_archmasterySchool`` at +0xB8,
    see wizwalker's ``ClientDeckBehavior``), not in the ``DeckConfiguration``
    window, so no UI has to be open for this.

    Archmastery is not tied to a world or a level bracket, so None means only
    "no school is recorded here" — never anything about the wizard.  Every
    failure resolves to None: a wizard with no deck equipped has no behavior to
    read, and an id the school table does not know is not worth guessing at.
    Neither is worth failing a whole deck import over.
    """
    try:
        behavior = await client.client_object.try_get_deck_behavior()
    except Exception:
        logger.debug("Could not reach the deck behavior for the archmastery school", exc_info=True)
        return None
    if behavior is None:
        logger.debug("No deck behavior on this client — archmastery school unknown")
        return None
    try:
        school_id = await behavior.archmastery_school()
    except Exception:
        logger.debug("Could not read the archmastery school id", exc_info=True)
        return None
    school = school_to_str.get(school_id)
    if school is None:
        logger.debug("Archmastery school id %r is not in wizwalker's school table", school_id)
    return school


#: App setting backing ``DeckData.apply_archmastery`` when the deck does not pin
#: an answer, and the value to assume if settings cannot be read at all.  Off:
#: the school is a persistent character setting, and changing one the user did
#: not ask about is worse than leaving a deck slightly wrong.
ARCHMASTERY_APP_SETTING = "default_apply_archmastery"
ARCHMASTERY_APP_FALLBACK = False

#: The seven schools an archmastery pip can be pointed at, and so the only
#: SC_* icons the selector has.  school_to_str also holds Sun, Moon, Star,
#: Shadow and the non-combat schools, which have no icon -- walking the window
#: tree for each of those was nine fruitless full-tree searches per apply.
PIP_SCHOOLS = ("Fire", "Ice", "Storm", "Myth", "Life", "Death", "Balance")


def _app_default_apply_archmastery() -> bool:
    """The user's app-level answer, or the shipped fallback if unreadable."""
    try:
        from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415

        return bool(SettingsManager.instance().get("general", ARCHMASTERY_APP_SETTING))
    except Exception:
        logger.debug("Could not read the archmastery app setting", exc_info=True)
        return ARCHMASTERY_APP_FALLBACK


def resolve_apply_archmastery(deck: DeckData, app_default: bool | None = None) -> tuple[bool, str]:
    """Whether this apply should set the school, and which level decided.

    The one place the three-state value collapses to a bool -- everything
    downstream sees a plain True/False, which is what keeps the rest of this
    module testable without touching SettingsManager.  ``app_default`` is for
    tests and for callers that have already read the settings; when it is None
    the settings are consulted, but only if the deck has no opinion.

    A deck with no recorded school never opts in whatever the setting says:
    there is nothing to set, and "apply the school" cannot mean "clear it".
    """
    if not deck.archmastery_school:
        return False, "no school saved with this deck"
    if isinstance(deck.apply_archmastery, bool):
        return deck.apply_archmastery, "this deck"
    if app_default is None:
        app_default = _app_default_apply_archmastery()
    return bool(app_default), "the app setting"


def archmastery_menu_is_open(rects: list) -> bool:
    """Whether the school menu is open, judged from the icons' geometry.

    The menu announces itself nowhere in the window tree -- no window appears,
    changes text, or changes visibility when it opens.  The seven school icons
    are always present and always report visible and enabled; what moves is
    where they are.  Closed, they are parked stacked behind the gauge; open,
    they fan out to positions that can be clicked.

    So "are they spread out" is the question, and the icons' own width is the
    threshold: stacked icons share a corner, and fanned ones are at least their
    own width apart.  Deriving it from the icons means there is no pixel
    constant here to recalibrate when the UI scale or the layout changes.

    Fewer than two icons cannot be spread apart at all; callers must not ask.
    """
    if len(rects) < 2:
        return False
    # Rectangle.__iter__ yields (x1, x2, y1, y2) -- not the (x1, y1, x2, y2)
    # order the name suggests -- so read the attributes rather than unpacking.
    spread = max(
        max(r.x1 for r in rects) - min(r.x1 for r in rects),
        max(r.y1 for r in rects) - min(r.y1 for r in rects),
    )
    width = max(max(r.x2 - r.x1 for r in rects), 1)
    return spread > width


async def _icon_rects(icons: list) -> list:
    """Current rectangles for already-resolved icon windows.

    The windows are resolved once by the caller and re-read here: re-walking the
    path every tick would be seven path walks per poll, and CLAUDE.md's "resolve
    once and act on the window" exists for exactly this shape of loop.
    """
    return [rect for icon in icons if (rect := await _safe_rect(icon)) is not None]


async def _safe_rect(window):
    try:
        return await window.window_rectangle()
    except Exception:
        return None


async def _settled_menu_state(icons: list, *, want: bool | None = None) -> bool | None:
    """Wait until the icons stop moving; return whether the menu ended up open.

    Stability is the point.  The move is animated, so geometry *changing* only
    means it has started -- anything decided from a single read can be a
    decision about a position the icon is still leaving.  The dwell is measured
    as elapsed time rather than two equal samples, because two polls can land
    inside one animation frame.

    ``want`` waits for a particular state: settling into the other one is not an
    answer, because the animation may not have started yet.  ``None`` returns
    whichever state the icons settle into, which is what a caller asking "what
    is it doing now" wants.

    Returns None if the ceiling passes without the icons holding still.
    """
    deadline = time.perf_counter() + ARCHMASTERY_MENU_TIMEOUT
    last_state = None
    settled_at = 0.0
    while True:
        now = time.perf_counter()
        rects = await _icon_rects(icons)
        state = tuple((r.x1, r.y1, r.x2, r.y2) for r in rects)
        if state != last_state:
            last_state, settled_at = state, now
        elif now - settled_at >= ARCHMASTERY_MENU_SETTLE:
            is_open = archmastery_menu_is_open(rects)
            if want is None or is_open == want:
                return is_open
        if now >= deadline:
            logger.debug("Archmastery icons never settled (waiting for %s)", want)
            return None
        await asyncio.sleep(ARCHMASTERY_MENU_POLL)


async def _wait_for_menu(icons: list, *, want_open: bool) -> bool:
    """Wait for the menu to settle into a particular state."""
    return await _settled_menu_state(icons, want=want_open) == want_open


async def _await_school(client, school: str) -> bool:
    """Poll until the client reports ``school``, or the ceiling passes.

    Reading the value is trustworthy even though writing it is not: a school
    changed through the UI reads back correctly, which is what makes this a
    usable confirmation.  A *memory write* to the same field is cosmetic --
    the client renders it and the server never hears, so it does not survive a
    relog and does not reach combat.  That route was measured and rejected;
    see tools/probe_archmastery_ui.py.
    """
    deadline = time.perf_counter() + ARCHMASTERY_CONFIRM_TIMEOUT
    while True:
        if await read_archmastery_school(client) == school:
            return True
        if time.perf_counter() >= deadline:
            return False
        await asyncio.sleep(ARCHMASTERY_CONFIRM_POLL)


async def set_archmastery_school(client, school: str) -> bool:
    """Set the wizard's archmastery school by driving the deck page's selector.

    Assumes the deck page is open.  Returns True if the school ends up as
    ``school`` -- including when it already was, in which case nothing is
    clicked at all.

    Never raises: a cosmetic setting must not be able to fail a deck apply, so
    every failure is a False plus a log line and the caller decides.

    The menu is left closed on the way out, successfully or not.  An open menu
    overlays the deck page, and the card clicks that follow would land in it.
    """
    from bartlebot.nodes.node_helpers import _get_window_from_path  # noqa: PLC0415

    if await read_archmastery_school(client) == school:
        return True

    gauge = await _safe(
        _get_window_from_path(client.root_window, ARCHMASTERY_GAUGE_PATH),
        what="resolving the gauge",
    )
    target = await _safe(
        _get_window_from_path(client.root_window, archmastery_school_path(school)),
        what=f"resolving the {school} icon",
    )
    icons = await _resolve_icons(client)
    logger.debug(
        "Archmastery selector: gauge=%s, %s icon=%s, %d icons resolved",
        gauge is not None,
        school,
        target is not None,
        len(icons),
    )
    if gauge is None or target is None:
        logger.warning(
            "Archmastery selector not found on the deck page (gauge found: %s, %s icon found: %s) "
            "— leaving the school alone",
            gauge is not None,
            school,
            target is not None,
        )
        return False

    if len(icons) < 2:
        # The open/closed test is "are the icons spread apart", which one icon
        # can never satisfy — the wait would sit out its ceiling reporting
        # "closed" forever. Better to do nothing than to click blind.
        logger.warning("Found %d archmastery school icons, need at least 2 to tell the menu apart", len(icons))
        return False

    try:
        for attempt in range(1, ARCHMASTERY_ATTEMPTS + 1):
            if not await _click(client, gauge):
                logger.debug("Archmastery gauge click %d did not land", attempt)
                continue
            if not await _wait_for_menu(icons, want_open=True):
                continue
            if not await _click(client, target):
                logger.debug("Archmastery '%s' icon click %d did not land", school, attempt)
                continue
            if await _await_school(client, school):
                logger.info("Archmastery school set to %s", school)
                return True
            logger.debug("Archmastery still not %s after attempt %d", school, attempt)
        logger.warning("Could not set the archmastery school to %s after %d attempts", school, ARCHMASTERY_ATTEMPTS)
        return False
    finally:
        await _close_menu(client, gauge, icons)


async def _click(client, window) -> bool:
    """Click a resolved window. False if the click could not be issued.

    Deliberately not ``node_helpers.click_window``: that refuses to click
    anything ``is_control_grayed()`` reports as grayed, and the grayed byte is
    only a real field on the button classes (see ui_tree_browser's
    ``has_grayed_field``).  The gauge and the school icons are sprites, so that
    read lands on whatever happens to sit at the offset and can refuse a
    perfectly good click without ever saying why.

    Every other click in this module goes straight through the mouse handler
    for the same reason -- item card slots are not buttons either.
    """
    try:
        async with client.mouse_handler:
            await client.mouse_handler.click_window(window)
        return True
    except Exception:
        logger.debug("Archmastery click failed", exc_info=True)
        return False


async def _resolve_icons(client) -> list:
    """Every school icon window, resolved once for the polling loops."""
    from bartlebot.nodes.node_helpers import _get_window_from_path  # noqa: PLC0415

    found = []
    for name in PIP_SCHOOLS:
        window = await _safe(
            _get_window_from_path(client.root_window, archmastery_school_path(name)),
            what=f"resolving the {name} icon",
        )
        if window is not None:
            found.append(window)
    return found


async def _close_menu(client, gauge, icons: list) -> None:
    """Put the menu away if it is still open. Best effort — never raises.

    Waits for the icons to stop moving *before* deciding, which a single read
    cannot do: picking a school closes the menu by itself, and during the
    restack animation the icons are still spread apart.  Reading once caught
    them mid-close, called it open, and clicked the gauge -- which reopened the
    menu the click had just dismissed.
    """
    try:
        if await _settled_menu_state(icons) is not True:
            return  # already closed, or never settled — either way, do not click
        await _click(client, gauge)
        if not await _wait_for_menu(icons, want_open=False):
            logger.warning("The archmastery menu would not close; deck clicks after this may land in it")
    except Exception:
        logger.debug("Failed to close the archmastery menu", exc_info=True)


async def _safe(coro, *, what: str = ""):
    """Await ``coro``, returning None instead of raising.

    Always logs.  A silent swallow here made "the selector was not found"
    indistinguishable from "reading the window tree raised", which are very
    different problems with the same symptom.
    """
    try:
        return await coro
    except Exception:
        logger.debug("Archmastery: %s failed", what or "read", exc_info=True)
        return None


async def read_deck(client) -> dict:
    """Snapshot ``client``'s normal deck and active item cards, opening the page itself.

    Card kinds are recorded here and nowhere else: this wizard owns every card
    in the deck, so its templates are readable, and a wizard the deck is later
    applied to will not own the ones that matter.

    The page is opened by ``deck_page_open`` -- one P to open, one to close --
    because everything read here is memory under ``DeckConfiguration``, which is
    what that door is for.  This used to go through ``DeckBuilder``, whose open
    clicks the Deck tab and the school filter, and then ``refresh_deck_page()``
    on top, which pressed P twice more to close and reopen the page just opened:
    the deck visibly opened a second time on every import, and each keypress and
    click activated and unhooked the mouseless hook on its own.  Import all ran
    that on four clients at once and read one wizard's deck as empty (#120).

    Raises ``RuntimeError`` when the page will not open, so the caller reports
    the slot as failed rather than saving an empty deck over the real one.
    """
    async with deck_page_open(client) as deck_window:
        if deck_window is None:
            raise RuntimeError("the deck page did not open")
        cards = await read_deck_cards(client, deck_window, classify=True)
        items = await read_item_slots(client, deck_window)
    kinds = {name: kind for name, kind in cards if kind}
    school = await read_archmastery_school(client)
    logger.debug(
        "Read deck: %d normal cards, %d item card slots, %d classified, archmastery=%s",
        len(cards),
        len(items),
        len(kinds),
        school,
    )
    return {
        "normal": count_names([name for name, _ in cards]),
        "tc": {},
        "item": active_item_counts(items),
        "card_kinds": kinds,
        "archmastery_school": school,
    }


async def _deck_already_matches(client, deck_window, deck: DeckData) -> bool:
    """Whether the deck currently open on ``client`` is an exact match for ``deck``.

    ``archmastery_school`` is deliberately not compared here.  Whether the school
    is acted on at all depends on the opt-in, so the comparison belongs where
    that answer is known: ``deck_matches`` takes ``check_archmastery``, and
    ``write_deck`` sets the school before ever reaching this function.  Folding
    it in unconditionally would send an apply down the full clear-and-rewrite
    path over a difference it has decided not to touch.
    """
    normal = await read_deck_card_names(client, deck_window)
    items = await read_item_slots(client, deck_window)
    return count_names(normal) == deck.normal and active_item_counts(items) == deck.item


async def _find_deck_window(client):
    """The DeckConfiguration window if the deck page is open, else None.

    ``retries=0`` because this asks a question whose answer is usually "no", and
    the default ladder spends 1.6 s sleeping to say so.
    """
    try:
        return await _maybe_get_named_window(client.root_window, "DeckConfiguration", retries=0)
    except ValueError:
        return None


async def _wait_for_page(client, *, present: bool):
    """Poll until the deck page is open (or gone). Returns the window, or None.

    Returns whatever the last read saw if the deadline passes, so callers see the
    real state rather than an exception.
    """
    deadline = time.perf_counter() + DECK_PAGE_OPEN_TIMEOUT
    while True:
        window = await _find_deck_window(client)
        if (window is not None) == present or time.perf_counter() >= deadline:
            return window
        await asyncio.sleep(DECK_PAGE_POLL)


@asynccontextmanager
async def deck_page_open(client):
    """Open the deck page with a keypress and nothing else, yielding its window.

    ``DeckBuilder.open_deck_page`` also clicks the "Deck" tab and the "Cards_All"
    school filter, and neither is needed to *read* a deck: ``Cards_All`` filters
    ``AllPageSpellList`` (the spells available to add), while ``CardsInDeck`` and
    ``ItemSpells`` are separate controls that ignore it.  Verified on four clients
    with ``tools/probe_bare_deck_open.py`` — identical results, 3.2 s down to 0.3 s.

    Those clicks are still required before *writing*, where the filter genuinely
    determines which spells the spellbook offers, so this is a read-only door.

    No mouse handler is taken.  ``send_key`` is a plain ``SendMessageW`` to the
    window handle and the mouseless hook only governs cursor position, so wrapping
    the keypress the way ``open_deck_page`` does buys nothing and costs ~180 ms of
    hook activate/deactivate.

    A page found already open is closed and reopened first.  Opening the deck page
    always lands on the deck itself — wizwalker's own ``close_deck_page`` notes
    "the next time you open deck, it has to be on deck page" — but a page someone
    left open could be on any of its sub-pages: the treasure card view, which may
    reuse ``CardsInDeck`` and would then be read as the normal deck, or Gardening,
    Fishing, Cantrips or Castle Magic.  Reopening costs two keypresses and makes
    the sub-page a non-question.  It never fires in the pre-run case, where the
    page starts closed.

    The page is left closed, whether or not it started that way.  An open deck
    page overlays the screen and swallows clicks aimed at what is behind it, so
    leaving one up to preserve how we found it would trade a tidy invariant for a
    class of missed clicks later in the run.  The one exception is the give-up
    path below, where the page is already not responding to P; the caller's full
    path closes it on the way out.
    """
    was_open = await _find_deck_window(client) is not None

    if was_open:
        await client.send_key(Keycode.P)
        if await _wait_for_page(client, present=False) is not None:
            # It would not close. Pressing P again would toggle something we
            # cannot predict, so stop here and let the caller take the full path,
            # which knows how to put the page right.
            logger.debug("Deck page did not close when asked; skipping the bare-keypress read")
            yield None
            return

    await client.send_key(Keycode.P)
    window = await _wait_for_page(client, present=True)

    try:
        yield window
    finally:
        # Only if it actually opened: pressing P after a keypress that did nothing
        # would open the page, not close it.
        if window is not None:
            await client.send_key(Keycode.P)


async def deck_matches(client, deck: DeckData, *, check_archmastery: bool = False) -> bool:
    """Whether ``client``'s deck already matches ``deck``, without opening the UI properly.

    Never raises.  A failed read means "cannot confirm a match", which sends the
    caller down the full path — slower, but correct.

    ``check_archmastery`` folds the school into the comparison, and is passed
    only when this apply has actually opted in to setting it.  Comparing it
    unconditionally would send every apply down the full path over a difference
    the apply has decided not to act on.  It is checked first because it is a
    memory read: a school that already differs means the fast path cannot
    succeed, and there is no point opening the deck page to find that out.
    """
    if check_archmastery and await read_archmastery_school(client) != deck.archmastery_school:
        return False
    try:
        async with deck_page_open(client) as deck_window:
            if deck_window is None:
                logger.debug("Deck page did not open for the fast match check")
                return False
            return await _deck_already_matches(client, deck_window, deck)
    except Exception:
        logger.debug("Fast deck match check failed, falling back to the full apply", exc_info=True)
        return False


async def _maybe_set_archmastery(
    client,
    deck: DeckData,
    report: DeckApplyReport,
    set_archmastery: bool | None,
) -> None:
    """Set the archmastery school if this apply opted in, recording what happened.

    ``set_archmastery`` is the already-resolved decision; None means "resolve it
    here", which is what a caller holding only a deck gets.  ``apply_deck``
    resolves once and passes it down so the settings are read once per apply and
    the reason is logged once.

    ``archmastery_set`` records a *change*, so the current school is read first:
    ``set_archmastery_school`` reports success when the school already matched,
    and calling that "set" would tell the user a persistent setting moved when
    it did not.
    """
    if set_archmastery is None:
        set_archmastery, source = resolve_apply_archmastery(deck)
        logger.debug("Archmastery apply: %s (%s)", set_archmastery, source)
    if not set_archmastery or not deck.archmastery_school:
        return
    if await read_archmastery_school(client) == deck.archmastery_school:
        return
    if await set_archmastery_school(client, deck.archmastery_school):
        report.archmastery_set = deck.archmastery_school
    else:
        report.archmastery_failed = deck.archmastery_school


async def write_deck(db, deck: DeckData, *, set_archmastery: bool | None = None) -> DeckApplyReport:
    """Restore a saved deck to the client — normal cards and item card toggles.

    Returns a :class:`DeckApplyReport` describing what was substituted and what
    was skipped, so a partial apply can be shown to the user rather than left in
    the log for them to find afterwards.

    If the deck already open on the client is an exact match for ``deck``, skip the
    clear/rewrite entirely — clearing and rebuilding a deck that's already correct
    just costs time and click risk for no benefit.

    Which cards can be placed is worked out *before* the clear, because
    wizwalker's ``_add_all_normal_cards``, which this once called, raised on the
    first name it could not place, and by then the deck was gone: one unplaceable card used to leave the wizard holding
    an empty deck while the caller logged that the run would "continue with
    whatever was set".  If nothing at all can be placed the deck is left
    untouched, since clearing is only ever worth doing as the first half of a
    rewrite.

    Item state is read *after* the deck is cleared, because ClearDeckButton may
    reset item toggles too; planning against the post-clear state is correct
    either way.

    The deck page is assumed to be open and on the deck tab already: the only
    caller is ``apply_deck``, whose ``DeckBuilder`` context manager has just done
    exactly that.  This used to open with ``refresh_deck_page()``, which — because
    ``_deck_open`` is True by then — pressed P twice to close and reopen the window
    ``__aenter__`` had only just opened.  That cost two blocking keypresses and then
    a lookup of a window mid-open-animation, which is precisely the case
    ``_maybe_get_named_window`` answers by sleeping through its 1.6 s retry ladder.
    """
    report = DeckApplyReport()
    deck_window = await _maybe_get_named_window(db.client.root_window, "DeckConfiguration")

    # Before the cards, and before the already-matches check: a deck whose cards
    # are right and whose school is wrong still has work to do, and doing the
    # school first means the early-out below stays a pure statement about cards.
    await _maybe_set_archmastery(db.client, deck, report, set_archmastery)

    if await _deck_already_matches(db.client, deck_window, deck):
        logger.info("Deck already matches saved configuration, skipping clear/rewrite")
        report.unchanged = True
        return report

    spell_names: list[str] = []
    direct: dict[str, int] = {}
    fusion: dict[str, int] = {}
    requested_by: dict[str, dict[str, int]] = {}
    if deck.normal:
        await db.view_deck_cards()
        spell_names = await _spell_list_names(db)
        plan = plan_normal_cards(deck.normal, spell_names)
        routing = partition_addable(plan.addable, spell_names)
        direct, fusion = routing.direct, routing.fusion
        requested_by = direct_requesters(plan.addable, routing)
        # Substitutions for *fusion* cards are reported by _add_fusion_cards,
        # which is where they are decided; these are the ones settled here.
        for requested, used in sorted(routing.substitutions.items()):
            logger.info(_SUBSTITUTION_MSG, used, requested)
        skipped = sorted(plan.missing + routing.unresolvable)
        report.merge(routing.substitutions, skipped)
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
            report.kinds = {name: deck.card_kinds[name] for name in skipped if name in deck.card_kinds}
            if report.kinds:
                try:
                    report.alternatives = suggest_alternatives(skipped, deck, await _spell_list_kinds(db))
                except Exception:
                    # Cosmetic: the skip is already reported, this only enriches it.
                    logger.debug("Could not gather alternatives for skipped cards", exc_info=True)

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
        report.applied = False
        return report

    await db.clear_deck()
    await db.view_deck_cards()

    count_cards = card_counter(db.client, deck_window)

    fusion_subs: dict[str, str] = {}
    fusion_skipped: list[str] = []
    direct_skipped: list[str] = []
    direct_placed: dict[str, int] = {}
    if direct:
        direct_placed, direct_skipped, direct_short = await _add_direct_cards(
            db, count_cards, direct, requested_by, spell_names
        )
        for name in direct_skipped:
            report.substitutions.pop(name, None)  # nothing went in, so nothing was substituted
        report.merge({}, direct_skipped)
        report.short.update(direct_short)

    fusion_placed: dict[str, int] = {}
    if fusion:
        fusion_subs, fusion_skipped, fusion_placed, fusion_short = await _add_fusion_cards(
            db, count_cards, fusion, spell_names
        )
        report.merge(fusion_subs, fusion_skipped)
        report.short.update(
            {name: (got, fusion[name]) for name, got in fusion_short.items() if name not in fusion_skipped}
        )

    slots = await read_item_slots(db.client, deck_window)
    plan = plan_item_toggles(slots, deck.item)
    if plan:
        logger.info("Toggling %d item card slot(s)", len(plan))
        # A slot's page comes from its vector index, and read_item_slots skips
        # unreadable entries, so count pages from the highest index, not the count.
        total_pages = page_count(max(s.index for s in slots) + 1)
        await _apply_item_toggles(db, deck_window, plan, total_pages)

    asked = requested_per_card(
        {entry: requested_by.get(entry, {entry: direct[entry]}) for entry in direct_placed},
        {name: n for name, n in fusion.items() if name not in fusion_skipped},
        fusion_subs,
    )

    # Last thing before the caller closes the page: the game's card list lags the
    # clicks that filled it, and closing early loses the stragglers.
    await _settle_deck(
        db.client,
        deck_window,
        expected_normal_deck(direct_placed, fusion_placed),
        deck.item,
        ceiling={card: sum(names.values()) for card, names in asked.items()},
        trim=lambda name: _trim_copy(db, deck_window, name),
    )

    # The report says what the deck holds, not what the clicks confirmed: a copy
    # that arrived after its card was given up on is in the deck all the same.
    # An empty read is not taken at its word: every card in ``asked`` was
    # confirmed in the deck, so reading none back is a failed read.
    try:
        held = count_names(await read_deck_card_names(db.client, deck_window))
    except Exception:
        logger.debug("Could not read the deck back for the report", exc_info=True)
    else:
        if held:
            report.short, report.over = tally_against_request(asked, held)
    return report


async def apply_deck(
    client, deck: DeckData, timeout: float = DECK_APPLY_TIMEOUT, *, slot: int | None = None
) -> DeckApplyReport:
    """Open the deck window on ``client`` and restore ``deck``, bounded by ``timeout``.

    The single entry point for every deck-apply path — the manual "Apply" button and
    the automatic pre-run apply.  Both previously had their own implementation, and
    the pre-run one had drifted onto ``DeckBuilder.set_deck_preset()``: the very API
    this module exists to replace (see the module docstring — it reaches the treasure
    card view and can page forward forever), with no timeout, so a stuck apply hung
    the run before the FSM ever started.

    The ``DeckBuilder`` context is inside the timeout so a hang while *opening* the
    deck window is bounded too, not just the write itself.

    A deck that already matches is settled first, by ``deck_matches``, which opens
    the page with a bare keypress and reads it.  That is ~0.3 s against ~3.2 s for
    the full open, and restarting a bot after editing it is the common case.  Only
    a real rewrite pays for the tab clicks.  The check is skipped for an empty
    ``deck`` — matching nothing is not a meaningful question, and ``write_deck``
    has its own handling for it.

    Raises ``TimeoutError`` if the apply does not finish in time; callers decide
    whether that is fatal.  Otherwise returns the :class:`DeckApplyReport` from
    ``write_deck``, which is what the caller shows the user.

    ``slot`` only labels this apply's log lines, which interleave with the other
    slots' when several decks go on at once.
    """

    async def _open_and_write() -> DeckApplyReport:
        from wizwalker.extensions.scripting.deck_builder import DeckBuilder  # noqa: PLC0415

        set_archmastery, source = resolve_apply_archmastery(deck)
        if set_archmastery:
            logger.info("Archmastery school will be set to %s (%s)", deck.archmastery_school, source)

        if not deck.is_empty() and await deck_matches(client, deck, check_archmastery=set_archmastery):
            logger.info("Deck already matches saved configuration, skipping the deck UI entirely")
            report = DeckApplyReport()
            report.unchanged = True
            return report

        # One mouse_handler for the whole write. Each `async with client.mouse_handler`
        # inside DeckBuilder would otherwise activate and unhook MouselessCursorMoveHook
        # on its own — ~180 ms a time, four times per open/close cycle. The handler is
        # ref-counted precisely so an outer hold collapses those into one.
        async with client.mouse_handler, DeckBuilder(client) as db:
            # open_deck_page() has just clicked the "Deck" tab, so we are on the
            # deck page — but it never records that, leaving _on_deck_page at the
            # False it was constructed with.  view_deck_cards() reads that flag and
            # would "correct" it by clicking through to the treasure card view,
            # which corrupts the spell list (see the module docstring).
            #
            # refresh_deck_page() used to set the flag as a side effect, which is
            # the only reason this was ever safe.  Dropping that redundant reopen
            # means saying it outright.  The real fix belongs in open_deck_page();
            # until the fork is bumped, assert it here.
            db._on_deck_page = True  # noqa: SLF001
            return await write_deck(db, deck, set_archmastery=set_archmastery)

    token = _apply_slot.set(slot)
    try:
        return await asyncio.wait_for(_open_and_write(), timeout=timeout)
    finally:
        _apply_slot.reset(token)


def _within(held: dict[str, int], floor: dict[str, int], top: dict[str, int]) -> bool:
    """True when every card holds between its floor and its top, and nothing else is in the deck."""
    if set(held) - set(top):
        return False
    return all(floor.get(card, 0) <= held.get(card, 0) <= n for card, n in top.items())


async def _settle_deck(
    client,
    deck_window,
    expected_normal: dict[str, int],
    desired_item: dict[str, int],
    *,
    ceiling: dict[str, int] | None = None,
    trim=None,
) -> bool:
    """Wait until the deck reads back as expected, before the page is closed.

    Cards go into the deck by clicking, and the list the game renders lags those
    clicks.  Closing the page on the last click meant a deck could come out short
    even though every card was found and every click landed — the apply looked
    clean and the wizard entered combat missing cards.

    ``expected_normal`` is the floor, the copies placement confirmed (see
    :func:`expected_normal_deck`), and ``ceiling`` the copies that were asked
    for.  They differ only where placement gave up on copies that may yet turn
    up (#107), so the deck is settled the moment it holds the ceiling exactly,
    or once every card sits between the two and nothing has changed for
    ``DECK_CARD_STABLE_WINDOW``.  Without a ceiling the floor is both, and only
    an exact match settles.  The timeout is only reached when the deck is never
    going to match.

    A card over its ceiling is a copy that arrived after it was clicked again.
    ``trim`` takes one out per read, and is dropped the first time it fails
    (see ``_trim_copy``), leaving the extra for the report.

    Item cards are checked in the same loop, filtered to the ones this account
    actually grants — a saved deck naming item cards from gear the wizard is no
    longer wearing is expected, not a failure.

    Never raises, and a False return is not fatal: the cards are already placed,
    and a partially-applied deck is better reported than rolled back.
    """
    top = dict(expected_normal)
    for card, n in (ceiling or {}).items():
        top[card] = max(n, top.get(card, 0))
    full = {card: n for card, n in top.items() if n > 0}
    deadline = time.perf_counter() + DECK_SETTLE_TIMEOUT
    normal: dict[str, int] | None = None
    item: dict[str, int] | None = None
    expected_item: dict[str, int] = {}
    last_seen: dict[str, int] | None = None
    steady_since = time.perf_counter()

    while True:
        try:
            normal = count_names(await read_deck_card_names(client, deck_window))
            slots = await read_item_slots(client, deck_window)
            item = active_item_counts(slots)
            available = {s.name for s in slots}
            expected_item = {name: n for name, n in desired_item.items() if name in available and n > 0}
        except Exception:
            # A read can fail mid-update; that is exactly what we are waiting out.
            logger.debug("Could not read the deck while waiting for it to settle", exc_info=True)
        else:
            if normal != last_seen:
                last_seen, steady_since = normal, time.perf_counter()
            over = sorted(card for card, n in normal.items() if card in top and n > top[card])
            if over and trim is not None:
                logger.info("Taking an extra copy of '%s' back out of the deck", over[0])
                try:
                    trimmed = await trim(over[0])
                except Exception:
                    logger.debug("Trimming '%s' failed", over[0], exc_info=True)
                    trimmed = False
                if not trimmed:
                    trim = None
            elif item == expected_item:
                if normal == full:
                    logger.debug("Deck settled: %s", normal or "{}")
                    return True
                if (
                    top != expected_normal
                    and _within(normal, expected_normal, top)
                    and time.perf_counter() - steady_since >= DECK_CARD_STABLE_WINDOW
                ):
                    logger.debug("Deck settled short of the full request: %s", normal)
                    return True

        if time.perf_counter() >= deadline:
            break
        await asyncio.sleep(DECK_SETTLE_POLL)

    if normal is None or not _within(normal, expected_normal, top):
        logger.warning(
            "Deck did not settle within %.0fs — closing anyway. Deck holds %s, expected %s",
            DECK_SETTLE_TIMEOUT,
            normal if normal is not None else "(unreadable)",
            full or "{}",
        )
    if item != expected_item:
        logger.warning(
            "Item cards after apply: %s (expected %s)",
            item if item is not None else "(unreadable)",
            expected_item or "{}",
        )
    return False
