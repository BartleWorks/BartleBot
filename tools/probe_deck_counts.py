"""Diagnostic: which reading tells a strategy how many cards are left to draw?

WHY THIS EXISTS
    The default strategies are to fuse enchants into cards ahead of time, to free hand
    slots so a big deck cycles faster. That is wasted on a deck the hand already holds
    (a 7 or 8 card deck), and on a big deck that has run dry late in a fight: freeing a
    slot draws nothing. So the enchant slot is to be gated on a new condition,
    `?(deck.remaining >= 2)`, and this probe finds out what `remaining` can be read from.

    The candidates:

    1. The `CountText` label. `SprintyCombat.get_card_counts()` already reads it every
       round and adds its first number to the hand size for fizzle detection. It parses
       the label by slicing off `<center>` and deleting the word "of", so it may not
       survive a non-English client.
    2. `CombatParticipant.m_pPlayDeck` -> `m_deckToSave` and `m_graveYardToSave`. The
       type dump places them (0x118, then 0x48 and 0x60), but the `ToSave` names suggest a
       snapshot written for a save, which may be empty or stale in a live fight.
    3. The same pair under `m_pSavedPlayDeck` (0x120).
    4. `m_deckFullness` (0x374), a float. Ruled out by the first run, below.

    Hand size is read two ways too, the card windows and `m_pHand`, so a pile count can
    be checked against what the hand did.

FIRST RUN (2026-10-14, one client, a 20-card deck, English)
    Round 1 discarded a card and cast one; round 2 fused an enchant into Tempest and cast it.

    * CountText is right: `13 of 20` with 7 in hand is cards left to draw, then the deck
      size. After the discard and the cast it read 11, so both freed slots drew.
    * Both play decks read nothing. That run could not tell a null pointer from a failed
      read; the columns now say which (`null` or `err`).
    * m_deckFullness stayed 1.0 while the pile fell from 13 to 11. Dropped.
    * The card windows read 0 once a card is cast; m_pHand holds the true 5.

    `rx` is the parse proposed in place of get_card_counts': the integers on the label's
    last line, first and last, with no word read, so the language cannot matter as long
    as the numbers come in that order.

SECOND RUN (2026-10-14, the same deck, French)
    Each round fused an enchant before casting: a blade, a trap, then an AoE.

    * The label reads `13 sur 20`, and get_card_counts' parse fails on it, so today it
      returns (0, 0) on any non-English client -- fizzle detection included. `rx` reads it.
    * The pile went 13, 11, 8: it falls by exactly the slots each round freed.
    * Both deck pointers are null. The client does not hold the deck; the memory
      candidates are closed, and their columns are gone.

WHAT TO LOOK FOR
    A reading that is right steps down by exactly one for every card drawn, and holds
    still on a round where the hand was already full. Enchanting a card fuses two into
    one, and the next round draws one more than it would have. A discard frees a slot
    the same way. Drawing a treasure card should not move the main pile at all.

HOW TO RUN
    Read-only: never clicks, casts or writes, so it cannot disturb a fight.

    uv run python tools/probe_deck_counts.py           # one reading per client, now
    uv run python tools/probe_deck_counts.py --watch   # a row whenever a reading changes

    Still to see, all about the edges of the label:

    * A deck of 7 or 8 cards. The first number should be 0 or 1 from the first round.
    * A treasure card drawn once, to see whether either number counts it.
    * A fight long enough to empty the pile: does the label read 0, or disappear? If it
      disappears, `label` reads `none`, and get_card_counts would spend its 2 s timeout
      every round before answering (0, 0).

    Paste the output back. Card names are printed; wizard names never are.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass

from wizwalker.combat import CombatHandler
from wizwalker.extensions.wizsprinter import WizSprinter

#: How often --watch samples, in seconds. A fight's planning phase lasts far longer.
_POLL = 0.5


async def _safe(coro, default=None):
    try:
        return await coro
    except Exception:
        return default


@dataclass(frozen=True)
class Reading:
    """Every candidate count at one moment. A row is printed when this changes."""

    round: int | None
    hand_ui: int | None
    hand_mem: int | None
    count_text: str | None
    count_parsed: tuple[int, int] | None
    count_rx: tuple[int, int] | None
    max_hand: int | None


def parse_sprinty(raw: str) -> tuple[int, int] | None:
    """The slicing get_card_counts does, so a failure here is a failure there."""
    try:
        _, second = raw.splitlines()
        a, b = second[8:-9].replace("of", "").strip().split()
        return int(a), int(b)
    except Exception:
        return None


def parse_rx(raw: str) -> tuple[int, int] | None:
    """The proposed parse: the first and last integers on the last line, no words read."""
    lines = [line for line in raw.splitlines() if line.strip()]
    numbers = re.findall(r"\d+", re.sub(r"<[^>]*>", "", lines[-1])) if lines else []
    if len(numbers) < 2:
        return None
    return int(numbers[0]), int(numbers[-1])


async def _count_text(client) -> str | None:
    windows = await _safe(client.root_window.get_windows_with_name("CountText"), []) or []
    if not windows:
        return None
    return await _safe(windows[0].maybe_text())


async def _participant(handler: CombatHandler):
    member = await _safe(handler.get_client_member(retries=2, sleep_time=0.2))
    if member is None:
        return None
    return await _safe(member.get_participant())


async def read(client, handler: CombatHandler) -> Reading | None:
    participant = await _participant(handler)
    if participant is None:
        return None

    cards = await _safe(handler.get_cards())
    hand = await _safe(participant.hand())
    hand_spells = await _safe(hand.spell_list()) if hand is not None else None
    raw = await _count_text(client)

    return Reading(
        round=await _safe(handler.round_number()),
        hand_ui=len(cards) if cards is not None else None,
        hand_mem=len(hand_spells) if hand_spells is not None else None,
        count_text=raw,
        count_parsed=parse_sprinty(raw) if raw is not None else None,
        count_rx=parse_rx(raw) if raw is not None else None,
        max_hand=await _safe(participant.max_hand_size()),
    )


_HEADER = f"{'round':>5} {'hand':>4} {'hmem':>4} {'label':>5} {'text':>7} {'rx':>7} {'max':>4}"


def _cell(value, width: int) -> str:
    return f"{'-' if value is None else value!s:>{width}}"


def _pair(value: tuple[int, int] | None) -> str | None:
    return f"{value[0]}/{value[1]}" if value else None


def row(r: Reading) -> str:
    return " ".join(
        [
            _cell(r.round, 5),
            _cell(r.hand_ui, 4),
            _cell(r.hand_mem, 4),
            _cell("yes" if r.count_text is not None else "none", 5),
            _cell(_pair(r.count_parsed), 7),
            _cell(_pair(r.count_rx), 7),
            _cell(r.max_hand, 4),
        ]
    )


def legend() -> None:
    print("   hand   card windows on screen (what the strategy sees)")
    print("   hmem   spells in m_pHand")
    print("   label  whether the CountText window exists at all")
    print("   text   the CountText label, parsed as get_card_counts does: left to draw/deck size")
    print("   rx     the same label, parsed by its integers alone (the proposed parse)")
    print("   max    m_maxHandSize")
    print()


async def once(client) -> None:
    if not await _safe(client.in_battle(), False):
        print("   Not in a battle. Start a fight and re-run.")
        return
    handler = CombatHandler(client)
    reading = await read(client, handler)
    if reading is None:
        print("   Could not find this client's combat participant. Re-run in the planning phase.")
        return
    legend()
    print("   " + _HEADER)
    print("   " + row(reading))
    print()
    print(f"   CountText raw: {reading.count_text!r}")


async def watch(clients) -> None:
    """One row per client whenever any reading changes, until Ctrl+C."""
    last: dict[int, Reading | None] = {}
    in_fight: dict[int, bool] = {}
    legend()
    print(f"   {'c':>2} " + _HEADER)
    while True:
        for i, client in enumerate(clients, start=1):
            battle = bool(await _safe(client.in_battle(), False))
            if battle != in_fight.get(i, False):
                in_fight[i] = battle
                last[i] = None
                print(f"   -- client {i} {'entered' if battle else 'left'} a battle --")
            if not battle:
                continue
            reading = await read(client, CombatHandler(client))
            if reading is None or reading == last.get(i):
                continue
            if last.get(i) is None or reading.count_text != last[i].count_text:
                print(f"   {i:>2}  CountText raw: {reading.count_text!r}")
            last[i] = reading
            print(f"   {i:>2} " + row(reading))
        await asyncio.sleep(_POLL)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--watch", action="store_true", help="print a row whenever a reading changes")
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
        if args.watch:
            print("Watching. Ctrl+C to stop.")
            print()
            await watch(clients)
        else:
            for i, client in enumerate(clients, start=1):
                print()
                print("=" * 78)
                print(f"CLIENT {i}")
                print("=" * 78)
                print()
                try:
                    await once(client)
                except Exception as exc:
                    print(f"  probe failed: {exc!r}")
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for client in clients:
            try:
                await client.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
