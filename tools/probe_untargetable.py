"""Diagnostic: can we tell from memory whether a duel participant can be targeted? (#192)

The Heretical Academy dragon fight has four heads and only one is targetable at a time.
SprintyCombat's `enemy(0)` is the first member on the other team, targetable or not, so the
bot casts at a head it cannot hit. Before changing the DSL (#192 step 2), this checks the
thing that change would rest on: does a field on the combat participant flip with the
game's own notion of "can be targeted"?

The candidates, all on `CombatParticipant` and all present in the type dump:

  m_untargetable        bool   offset 206   (wizwalker: untargetable())
  m_untargetableRounds  int    offset 208   (untargetable_rounds())
  m_restrictedTarget    bool   offset 212   (restricted_target())

The game also has effect types `untargetable`, `make_targetable` and `force_targetable`,
so the mechanic is modelled as a status effect; which field the dragon fight uses is what
this finds out. Two others are printed as controls, because they are easy to mistake for
it: `confused_target` (a confused wizard hitting the wrong side) and `exit_combat`.

OUTCOME (one full Heretical Academy run, four players, heads killed 1-4-3-2):
  `m_untargetable` is the field. Each phase had exactly one head reading false, and it was
  always the one that could be clicked; the flag moved to the next head as each was killed.
  `restricted_target`, `confused_target`, `exit_combat` and `stunned` never changed.
  Three traps, each of which the first reading of this run fell into:
    - `m_untargetableRounds` is only a countdown (98 at the start, minus one per round, 0
      once the flag clears). It carries nothing the bool does not.
    - A killed head reads `untargetable == false`. Untargetable is not "not a valid target":
      a dead head is "targetable" until the game removes it from the circle, and it is still
      listed at 0 health for a while first. A filter must also require health > 0.
    - `CombatMember.is_dead()` read False for heads at 0 health, so use `health()`.
  Slot positions in `get_members()` SHIFT when a dead member is removed, so a probe (or a
  bot) that watches "slot 0" sees another head's state. Identify a member by its participant
  address, as this probe now does.
  Not tested: an untargetable state from a spell effect rather than a boss script, which the
  game's `untargetable` / `make_targetable` / `force_targetable` effect types suggest sets
  the same flag.

WHAT TO LOOK FOR
  In each phase of the fight, exactly one enemy head should read as the targetable one.
  Compare against the game window: the head you can click is the targetable one.
  A field is the answer if it differs between that head and the other three, and the
  difference moves to the new head when the phase changes. The TRANSITIONS section of the
  summary lists every change in the order it was seen, with the round number, so a field
  that flips on its own (or only at a round boundary) is visible without watching the table.

  If no field separates them, the fight uses something this does not read, and the issue's
  original idea stands: cast an enemy-only and an ally-only spell and see which is accepted.

HOW TO RUN
    1. Log in a wizard and start the Heretical Academy final fight (or any fight; a mob
       that is never untargetable is still a useful negative control).
    2. uv run python tools/probe_untargetable.py [--client N] [--interval 0.5]
    3. Play the fight by hand. A row table is printed whenever any value changes, and
       once at the start. Ctrl+C, or the fight ending, prints the summary.

Only one client is read (--client, 1-based, default the first one found in battle). Wizard
names and ids are never read or printed: a participant is labelled by its position in the
combat circle and by `player` or `monster`, and a name is shown for monsters only, which
is game data.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field

from wizwalker.combat import CombatHandler
from wizwalker.extensions.wizsprinter import WizSprinter

POLL_FOR_BATTLE = 0.5
# Fields read off each participant, in column order. Each is a (label, participant method).
FIELDS = (
    ("untgt", "untargetable"),
    ("untgt_rnds", "untargetable_rounds"),
    ("restricted", "restricted_target"),
    ("confused", "confused_target"),
    ("exit", "exit_combat"),
    ("stunned", "stunned"),
)


async def _safe(coro):
    """The awaited value, or None if the read raised. Every read here is speculative."""
    try:
        return await coro
    except Exception:
        return None


@dataclass
class Row:
    """One participant at one moment. `values` is what a change is detected on."""

    slot: int  # position in get_members() order, 0-based; SHIFTS when a dead member is removed
    ident: int | None  # the participant's address: stable for the fight, unlike slot
    side: str  # "me", "ally" or "enemy"
    kind: str  # "player" or "monster"
    name: str  # monsters only; empty for players
    health: int | None
    dead: bool | None  # health == 0: member.is_dead() read False for 0-hp heads in the first live run
    values: tuple  # one entry per FIELDS
    head: int | None = None  # enemies only: 1-based, in the order first seen, stable as slots shift


@dataclass
class Observed:
    """Everything seen for one enemy over the fight, for the summary."""

    name: str = ""
    combos: set = field(default_factory=set)  # distinct value tuples seen while alive


async def _read_rows(handler) -> list[Row] | None:
    """Every participant now, or None if the combat circle cannot be read."""
    members = await _safe(handler.get_members())
    if members is None:
        return None

    my_team = None
    for member in members:
        if await _safe(member.is_client()):
            participant = await _safe(member.get_participant())
            my_team = await _safe(participant.team_id()) if participant else None
            break

    rows = []
    for slot, member in enumerate(members):
        participant = await _safe(member.get_participant())
        if participant is None:
            continue
        team = await _safe(participant.team_id())
        is_me = bool(await _safe(member.is_client()))
        is_player = bool(await _safe(member.is_player()))
        if is_me:
            side = "me"
        elif team is not None and team == my_team:
            side = "ally"
        else:
            side = "enemy"
        # A player's name is a wizard name; never read it.
        name = "" if is_player else str(await _safe(member.name()) or "")
        values = tuple([await _safe(getattr(participant, method)()) for _, method in FIELDS])
        health = await _safe(member.health())
        rows.append(
            Row(
                slot=slot,
                ident=await _safe(participant.read_base_address()),
                side=side,
                kind="player" if is_player else "monster",
                name=name,
                health=health,
                dead=None if health is None else health == 0,
                values=values,
            )
        )
    return rows


def _signature(rows: list[Row]) -> tuple:
    return tuple((r.ident, r.side, r.dead, r.values) for r in rows)


def _fmt(value) -> str:
    if value is None:
        return "?"
    if isinstance(value, bool):
        return "T" if value else "."
    return str(value)


def _print_table(rows: list[Row], round_number, elapsed: float) -> None:
    print(f"\n[t+{elapsed:6.1f}s  round {round_number}]")
    header = f"  {'slot':>4} {'head':>4} {'side':<5} {'kind':<7} {'hp':>7} {'dead':<4} " + " ".join(
        f"{label:>10}" for label, _ in FIELDS
    )
    print(header + "  name")
    for r in rows:
        hp = "?" if r.health is None else str(r.health)
        cells = " ".join(f"{_fmt(v):>10}" for v in r.values)
        head = "-" if r.head is None else str(r.head)
        print(f"  {r.slot:>4} {head:>4} {r.side:<5} {r.kind:<7} {hp:>7} {_fmt(r.dead):<4} {cells}  {r.name}")


def _diff(before: list[Row], after: list[Row]) -> list[str]:
    """One line per field that changed on a participant present in both snapshots."""
    old = {r.ident: r for r in before}
    lines = []
    for r in after:
        prev = old.get(r.ident)
        if prev is None:
            continue
        label = f"head {r.head}" if r.head else f"slot {r.slot} ({r.side})"
        for (field_label, _), was, now in zip(FIELDS, prev.values, r.values):
            if was != now:
                lines.append(f"{label}: {field_label} {_fmt(was)} -> {_fmt(now)}")
        if prev.dead != r.dead:
            lines.append(f"{label}: dead {_fmt(prev.dead)} -> {_fmt(r.dead)}")
    return lines


async def _pick_client(clients, wanted: int | None):
    if wanted is not None:
        if not 1 <= wanted <= len(clients):
            print(f"No client {wanted}; have {len(clients)}.")
            return None
        return clients[wanted - 1]
    print("Waiting for any client to be in battle...")
    while True:
        for client in clients:
            if await _safe(client.in_battle()):
                return client
        await asyncio.sleep(POLL_FOR_BATTLE)


async def _watch(
    client, interval: float, transitions: list[tuple[str, int, str]], enemies: dict[int, Observed]
) -> None:
    """Print a table on every change until the fight ends, filling *transitions* and *enemies*.

    The caller owns both, so a Ctrl+C that cancels this still leaves the summary its data.
    """
    # The client has no combat_handler attribute; build one.
    handler = CombatHandler(client)
    loop = asyncio.get_running_loop()
    start = loop.time()
    heads: dict[int | None, int] = {}
    last_rows: list[Row] = []
    last_sig = None
    last_round = None
    out_of_battle = 0

    while True:
        if not await _safe(client.in_battle()):
            out_of_battle += 1
            if out_of_battle >= 3:
                print("\nThe client has left battle; the fight is over.")
                break
            await asyncio.sleep(interval)
            continue
        out_of_battle = 0

        rows = await _read_rows(handler)
        if rows is None:
            await asyncio.sleep(interval)
            continue
        round_number = await _safe(handler.round_number())
        elapsed = loop.time() - start

        for r in rows:
            if r.side == "enemy":
                r.head = heads.setdefault(r.ident, len(heads) + 1)
            if r.side == "enemy" and not r.dead:
                seen = enemies.setdefault(r.head, Observed())
                seen.name = r.name or seen.name
                seen.combos.add(r.values)

        sig = _signature(rows)
        if sig != last_sig or round_number != last_round:
            _print_table(rows, round_number, elapsed)
            for line in _diff(last_rows, rows):
                print(f"  >> {line}")
                transitions.append((f"t+{elapsed:.1f}s", round_number or 0, line))
            last_rows, last_sig, last_round = rows, sig, round_number
        await asyncio.sleep(interval)


def _summary(transitions: list[tuple[str, int, str]], enemies: dict[int, Observed]) -> None:
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("\n  TRANSITIONS (every change seen, in order)")
    if not transitions:
        print("      none: no field changed during the fight")
    for when, round_number, line in transitions:
        print(f"      {when:>9}  round {round_number:<3} {line}")

    print("\n  PER ENEMY HEAD: distinct value tuples seen at hp > 0")
    print(f"      columns: {', '.join(label for label, _ in FIELDS)}")
    for slot, seen in sorted(enemies.items()):
        print(f"      head {slot} {seen.name or '(unnamed)'}: {len(seen.combos)} distinct")
        for combo in sorted(seen.combos, key=str):
            print("          " + "  ".join(f"{label}={_fmt(v)}" for (label, _), v in zip(FIELDS, combo)))

    varying = [
        label
        for index, (label, _) in enumerate(FIELDS)
        if len({combo[index] for seen in enemies.values() for combo in seen.combos}) > 1
    ]
    print("\n  fields that took more than one value across enemies:", ", ".join(varying) or "none")
    print("  Compare these against which head you could actually click in each phase.")
    print("=" * 72)


async def main(args: argparse.Namespace) -> int:
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
        client = await _pick_client(clients, args.client)
        if client is None:
            return 2
        transitions: list[tuple[str, int, str]] = []
        enemies: dict[int, Observed] = {}
        try:
            await _watch(client, args.interval, transitions, enemies)
        except asyncio.CancelledError:
            # Ctrl+C under asyncio.run cancels this task; swallowing it still prints the summary.
            print("\nStopped by Ctrl+C.")
        _summary(transitions, enemies)
        return 0
    finally:
        for c in clients:
            try:
                await c.close()
            except Exception:
                pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--client", type=int, metavar="N", help="Client to read (1-based); default the first in battle."
    )
    parser.add_argument("--interval", type=float, default=0.5, help="Seconds between reads.")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(_parse_args())))
