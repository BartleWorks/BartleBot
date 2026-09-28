"""Diagnostic: how long do clicked copies take to show up in CardsInDeck (#107)?

WHY THIS EXISTS
    Applying Epic x3 confirmed one copy, re-clicked four more, and left the deck
    holding four. The old confirm counted the whole deck from the entry vector's
    begin/end pointers, through a control built once and reused, and gave up
    after 2 s per round. Two explanations fit the logs, and they call for
    different fixes:

        * lag -- the copies really do take several seconds to appear, so any
          fixed window too short will re-click them;
        * a stale count -- the reused control stopped tracking the list, so the
          count stayed flat while a fresh read would have seen the copies.

    ``deck_io`` now counts by name, fresh, and waits for the count to stop
    moving (``DECK_CARD_STABLE_WINDOW``) before re-clicking. This measures the
    lag that window has to cover, and whether the two totals ever disagree.

    It also checks the assumption ``_trim_copy`` rests on: that clicking a card
    in the deck grid, at the slot its vector index gives, takes out one copy of
    *that* card. Only a deck holding more than one card can show that, so the
    removal is done on the last card named, in whatever deck the run built.

    First finding: Epic x3 alone, on an empty deck, on one client, lands every
    copy before the first sample. Both logged failures placed the short card
    after others, during an apply on several clients at once, which is what
    ``--card`` repeated and ``--all-clients`` reproduce.

HOW TO RUN
    Log the wizards in, then e.g. the two decks from the issue:

        uv run python tools/probe_deck_click_lag.py --card "Tri Blade:3" --card "Sharpened Blade:3"
        uv run python tools/probe_deck_click_lag.py --card Frenzy --card Tempest --card "Epic:3" --all-clients

    ``NAME:COPIES`` (default 1 copy). Cards are placed in spellbook page order,
    as ``write_deck`` places them, whatever order they are given in.
    ``--csv out.local.csv`` also writes every sample.

WHAT IT DOES TO YOUR DECK
    It CLEARS the open deck on every client it runs on, adds the cards, then
    removes one copy of the last card by clicking it in the deck. Re-apply your
    saved decks afterwards.

    Nothing identifying is printed or written: card names only, never a wizard
    name or ``m_nameKeys``. ``--csv`` refuses any path not ending in
    ``.local.csv``, which is gitignored.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import time

from wizwalker.extensions.scripting.utils import _maybe_get_named_window
from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot.deck_io import (
    _control_for,
    _deck_entry_names,
    _entry_vector,
    _set_spell_page,
    _spell_list_names,
    count_names,
    read_deck_card_names,
)
from bartlebot.nodes.timing import DECK_FUSION_CLICK_SETTLE

SAMPLE_EVERY = 0.05


def _parse_card(spec: str) -> tuple[str, int]:
    name, sep, copies = spec.rpartition(":")
    if sep and copies.isdigit():
        return name, int(copies)
    return spec, 1


async def _watch(client, deck_window, card: str, cached, tag: str, phase: str, watch_for: float, rows: list[dict]):
    """Sample every SAMPLE_EVERY for ``watch_for`` seconds, printing each change."""
    start = time.perf_counter()
    last = None
    while (elapsed := time.perf_counter() - start) < watch_for:
        try:
            _b, cached_total = await _entry_vector(cached)
        except Exception:
            cached_total = None
        try:
            _b, fresh_total = await _entry_vector(await _control_for(client, deck_window, "CardsInDeck"))
        except Exception:
            fresh_total = None
        try:
            named = (await read_deck_card_names(client, deck_window)).count(card)
        except Exception:
            named = None
        rows.append(
            {
                "client": tag,
                "phase": phase,
                "card": card,
                "t": round(elapsed, 3),
                "cached_total": cached_total,
                "fresh_total": fresh_total,
                "named": named,
            }
        )
        state = (cached_total, fresh_total, named)
        if state != last:
            flag = "  <-- totals disagree" if cached_total != fresh_total else ""
            print(f"   {tag} {elapsed:6.3f}s  cached={cached_total}  fresh={fresh_total}  '{card}'={named}{flag}")
            last = state
        await asyncio.sleep(SAMPLE_EVERY)


async def run(client, tag: str, cards: list[tuple[str, int]], watch_for: float, rows: list[dict]) -> None:
    from wizwalker.extensions.scripting.deck_builder import DeckBuilder  # noqa: PLC0415

    async with client.mouse_handler, DeckBuilder(client) as db:
        db._on_deck_page = True  # noqa: SLF001 -- as apply_deck does; see its comment
        deck_window = await _maybe_get_named_window(client.root_window, "DeckConfiguration")
        await db.view_deck_cards()
        spell_names = await _spell_list_names(db)

        sweep = []
        for name, copies in cards:
            if name not in spell_names:
                print(f"   {tag} '{name}' is not in this wizard's spellbook; skipped.")
                continue
            page_idx, card_idx = db.calcuate_position_of_card_in_page(spell_names, name)
            sweep.append((page_idx, name, copies, await db.calculate_card_position(card_idx)))
        sweep.sort(key=lambda item: item[0])
        if not sweep:
            return

        await db.clear_deck()
        await db.view_deck_cards()
        cached = await _control_for(client, deck_window, "CardsInDeck")  # built once, as the old counter did

        for step, (page_idx, name, copies, position) in enumerate(sweep, start=1):
            await _set_spell_page(db, page_idx)
            print(
                f"\n{tag} {step}. {copies} click(s) on '{name}' (page {page_idx}), {DECK_FUSION_CLICK_SETTLE * 1000:.0f} ms apart"
            )
            for _ in range(copies):
                await client.mouse_handler.click(*position)
                await asyncio.sleep(DECK_FUSION_CLICK_SETTLE)
            await _watch(client, deck_window, name, cached, tag, "add", watch_for, rows)

        target = sweep[-1][1]
        names = await _deck_entry_names(client, deck_window)
        slots = [i for i, entry in enumerate(names) if entry == target]
        if not slots:
            print(f"\n{tag} No '{target}' in the deck to remove; skipped. Deck: {names}")
            return
        before = count_names([n for n in names if n])
        print(f"\n{tag} Remove: click the deck's last '{target}' (vector index {slots[-1]}). Deck before: {names}")
        await client.mouse_handler.click(*await db.calculate_deck_card_position(slots[-1] + 1))
        await _watch(client, deck_window, target, cached, tag, "remove", watch_for, rows)
        after_names = await _deck_entry_names(client, deck_window)
        after = count_names([n for n in after_names if n])
        lost = {card: before[card] - after.get(card, 0) for card in before if after.get(card, 0) < before[card]}
        verdict = "OK, only that card" if lost == {target: 1} else f"UNEXPECTED, lost {lost or 'nothing'}"
        print(f"   {tag} Deck after: {after_names}\n   {tag} Removal: {verdict}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--card", action="append", required=True, help="NAME or NAME:COPIES; repeat for more cards")
    parser.add_argument("--all-clients", action="store_true", help="run on every client at once, as Apply all does")
    parser.add_argument("--watch", type=float, default=10.0, help="seconds to sample after each step")
    parser.add_argument("--csv", help="also write every sample here; must end in .local.csv")
    args = parser.parse_args()
    if args.csv and not args.csv.endswith(".local.csv"):
        print("--csv must end in .local.csv, which is gitignored.")
        return 2
    cards = [_parse_card(spec) for spec in args.card]

    clients = WizSprinter().get_new_clients()
    if not clients:
        print("No Wizard101 clients found. Start the game and log a wizard in.")
        return 1
    if not args.all_clients:
        clients = clients[:1]
    await asyncio.gather(*[c.activate_hooks() for c in clients])
    rows: list[dict] = []
    try:
        results = await asyncio.gather(
            *[run(c, f"[C{i}]", cards, args.watch, rows) for i, c in enumerate(clients, start=1)],
            return_exceptions=True,
        )
        for i, result in enumerate(results, start=1):
            if isinstance(result, BaseException):
                print(f"[C{i}] FAILED: {result!r}")
    finally:
        await asyncio.gather(*[c.close() for c in clients], return_exceptions=True)

    if args.csv and rows:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nWrote {len(rows)} samples to {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
