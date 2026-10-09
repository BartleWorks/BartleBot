"""Diagnostic: where does a no-op deck apply actually spend its time?

WHY THIS EXISTS
    The pre-run deck apply is bearable with one client and slow with four,
    because ``_apply_pre_run_decks`` awaits ``apply_deck`` per client in a plain
    ``for`` loop.  Running the four concurrently is the obvious fix, but how much
    it can possibly win depends on a split that cannot be eyeballed:

        * ``asyncio.sleep`` -- the 4x0.4s retry ladder inside
          ``_maybe_get_named_window`` when a window is not found yet, the double
          keypress in ``refresh_deck_page``, click predelays.  These overlap
          perfectly across clients, so concurrency divides them by N.

        * synchronous memory reads -- every ``get_windows_with_name`` is a full
          recursive walk of the game's window tree, reading a name string per
          node.  Despite the ``async def``, WizWalker's reads call
          ``ReadProcessMemory`` inline on the event loop thread.  These do NOT
          overlap; concurrency just queues them.

    If walks dominate, making the apply concurrent adds failure modes and buys
    nothing.  This measures the split instead of assuming it.

HOW TO RUN
    Section A is read-only and always runs:

        uv run python tools/probe_deck_apply_timing.py

    Sections B and C drive the real ``apply_deck`` path and need --apply:

        uv run python tools/probe_deck_apply_timing.py --apply

    Run --apply with as many clients logged in as you normally bot with; the
    whole point is the four-client wall clock.

WHAT --apply DOES TO YOUR DECK
    Nothing, by design.  It reads each client's *current* deck and applies that
    same deck straight back, so ``write_deck`` hits ``_deck_already_matches``
    and returns before clearing anything.  That is exactly the no-op path we
    want to time.  If a match unexpectedly fails, the deck is cleared and
    rebuilt from what was just read -- still the same deck, but slower and with
    real click risk, so the probe reports any client whose apply came back
    ``unchanged=False`` rather than letting it pass quietly.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

from wizwalker.extensions.scripting.utils import _maybe_get_named_window
from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot.combat.deck_io import (
    _control_for,
    _entry_vector,
    apply_deck,
    read_deck,
    read_deck_card_names,
    read_item_slots,
)
from bartlebot.shared.strategy_models import DeckData

# Repeat each micro-benchmark this many times and report the median. A single
# timing is dominated by whatever else the game happened to be doing.
SAMPLES = 5

# _maybe_get_named_window's retry ladder, mirrored here so the derived cost of a
# missing window can be reported without re-deriving it by hand.
LOOKUP_RETRIES = 4
LOOKUP_RETRY_SLEEP = 0.4


async def _time(coro_factory, samples: int = SAMPLES) -> tuple[float, float]:
    """Return (median, max) seconds over `samples` runs of `coro_factory()`."""
    timings = []
    for _ in range(samples):
        start = time.perf_counter()
        try:
            await coro_factory()
        except Exception:
            return (float("nan"), float("nan"))
        timings.append(time.perf_counter() - start)
    return statistics.median(timings), max(timings)


def _row(label: str, median: float, worst: float, note: str = "") -> None:
    if median != median:  # NaN
        print(f"   {label:<44} {'failed':>9}")
        return
    print(f"   {label:<44} {median * 1000:>7.1f}ms {worst * 1000:>8.1f}ms  {note}")


async def section_a(client) -> float | None:
    """Micro-benchmark the pieces. Returns the median full-walk cost, or None."""
    print("A. COST OF THE INDIVIDUAL PIECES (read-only)")
    print()
    print(f"   {'operation':<44} {'median':>9} {'worst':>9}")
    print("   " + "-" * 78)

    root = client.root_window

    # A name that exists: the walk can stop early, so this is a best case.
    hit_med, hit_max = await _time(lambda: root.get_windows_with_name("btnSpellbook"))
    _row("full tree walk, name present", hit_med, hit_max)

    # A name that cannot exist: the walk visits every node. This is the cost
    # _maybe_get_named_window pays on each of its five attempts before raising.
    miss_med, miss_max = await _time(lambda: root.get_windows_with_name("NoSuchWindowName"))
    _row("full tree walk, name absent", miss_med, miss_max, "<- exhaustive")

    try:
        deck_window = await _maybe_get_named_window(root, "DeckConfiguration", retries=0)
    except ValueError:
        deck_window = None

    if deck_window is None:
        print()
        print("   Deck page is closed, so the subtree reads below were skipped.")
        print("   Press P on this client and re-run to get the full picture.")
        print()
    else:
        cin_med, cin_max = await _time(lambda: _control_for(client, deck_window, "CardsInDeck"))
        _row("subtree walk -> CardsInDeck control", cin_med, cin_max)

        its_med, its_max = await _time(lambda: _control_for(client, deck_window, "ItemSpells"))
        _row("subtree walk -> ItemSpells control", its_med, its_max)

        cards_control = await _control_for(client, deck_window, "CardsInDeck")
        _, card_count = await _entry_vector(cards_control)
        items_control = await _control_for(client, deck_window, "ItemSpells")
        _, item_count = await _entry_vector(items_control)

        rd_med, rd_max = await _time(lambda: read_deck_card_names(client, deck_window))
        _row("read_deck_card_names", rd_med, rd_max, f"{card_count} entries")

        ri_med, ri_max = await _time(lambda: read_item_slots(client, deck_window))
        _row("read_item_slots", ri_med, ri_max, f"{item_count} entries")

    print()
    if miss_med == miss_med:
        ladder = LOOKUP_RETRIES * LOOKUP_RETRY_SLEEP
        walks = (LOOKUP_RETRIES + 1) * miss_med
        print("   >> Derived: one _maybe_get_named_window for a window that is not")
        print(f"      there yet costs {walks:.2f}s of walking + {ladder:.2f}s of sleeping")
        print(f"      = {walks + ladder:.2f}s. Only the {ladder:.2f}s overlaps across clients.")
        print()
    return miss_med if miss_med == miss_med else None


async def _noop_apply(client, deck: DeckData) -> tuple[float, bool]:
    """Apply `deck` to `client` and return (seconds, was_unchanged)."""
    start = time.perf_counter()
    report = await apply_deck(client, deck)
    return time.perf_counter() - start, bool(report.unchanged)


async def _snapshot(client) -> DeckData:
    from wizwalker.extensions.scripting.deck_builder import DeckBuilder  # noqa: PLC0415

    async with DeckBuilder(client) as db:
        return DeckData(**await read_deck(db))


async def section_b(clients, decks) -> float:
    print("B. SEQUENTIAL APPLY (what _apply_pre_run_decks does today)")
    print()
    start = time.perf_counter()
    per_client = []
    for i, (client, deck) in enumerate(zip(clients, decks), start=1):
        elapsed, unchanged = await _noop_apply(client, deck)
        per_client.append(elapsed)
        flag = "" if unchanged else "  <-- NOT a no-op, see docstring"
        print(f"   client {i}: {elapsed:6.2f}s{flag}")
    total = time.perf_counter() - start
    print()
    print(f"   total {total:.2f}s")
    print()
    return total


async def section_c(clients, decks) -> float:
    print("C. CONCURRENT APPLY (one task per client)")
    print()
    start = time.perf_counter()
    results = await asyncio.gather(
        *[_noop_apply(c, d) for c, d in zip(clients, decks)],
        return_exceptions=True,
    )
    total = time.perf_counter() - start
    for i, result in enumerate(results, start=1):
        if isinstance(result, BaseException):
            print(f"   client {i}: FAILED {result!r}")
            continue
        elapsed, unchanged = result
        flag = "" if unchanged else "  <-- NOT a no-op, see docstring"
        print(f"   client {i}: {elapsed:6.2f}s{flag}")
    print()
    print(f"   total {total:.2f}s")
    print()
    return total


def verdict(sequential: float, concurrent: float, n: int) -> None:
    print("VERDICT")
    print()
    if concurrent <= 0:
        print("   No usable concurrent timing.")
        return
    speedup = sequential / concurrent
    saved = sequential - concurrent
    print(f"   {n} clients: {sequential:.2f}s sequential -> {concurrent:.2f}s concurrent")
    print(f"   {speedup:.2f}x faster, {saved:.2f}s saved per run")
    print()
    if speedup >= n * 0.6:
        print("   Sleeps dominate. Concurrency is the right fix and most of the")
        print("   theoretical win is actually available.")
    elif speedup >= 1.5:
        print("   Mixed. Concurrency is worth doing, but synchronous tree walks are")
        print("   a real floor -- cutting walk count (retries=0 probes, dropping the")
        print("   redundant refresh_deck_page) would compound with it.")
    else:
        print("   Synchronous tree walks dominate. Concurrency buys little here and")
        print("   adds failure modes; cut the number of walks instead.")
    print()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="also run sections B and C, which drive the real apply_deck path",
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
            await section_a(client)

        if not args.apply:
            print("=" * 88)
            print()
            print("Sections B and C skipped. Re-run with --apply to time the real")
            print("sequential-vs-concurrent apply. It re-applies each client's own")
            print("current deck, so it is a no-op by construction.")
            print()
            return 0

        print("=" * 88)
        print("SNAPSHOTTING CURRENT DECKS (so the applies below are no-ops)")
        print("=" * 88)
        print()
        decks = []
        for i, client in enumerate(clients, start=1):
            deck = await _snapshot(client)
            print(f"   client {i}: {len(deck.normal)} card type(s), {len(deck.item)} item card type(s)")
            decks.append(deck)
        print()

        print("=" * 88)
        print()
        sequential = await section_b(clients, decks)
        concurrent = await section_c(clients, decks)
        verdict(sequential, concurrent, len(clients))
    finally:
        await asyncio.gather(*[c.close() for c in clients], return_exceptions=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
