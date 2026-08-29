"""Drive the handle_potions route end to end WITHOUT buying anything.

Testing this route through the node itself is painful: handle_potions only travels when a
client has actually run out of potion charges, so exercising it means draining potions
first, and then every run spends gold on a purchase that is not the part under test. The
navigation is where the bugs are — the castle unequip, the HOME press, two zone
transitions, and the approach to Hilda. Buying is a few clicks in a window that is already
open.

So this runs steps 5-10 and 12 of handle_potions.run() unconditionally, on whichever clients
are logged in, and skips step 11:

    mark each client's current position
    -> unequip castles, HOME to the dorm, re-equip
    -> Dorm -> Ravenwood -> Commons
    -> approach Hilda and open the shop
    -> close the shop WITHOUT buying
    -> recall to the mark

It calls handle_potions.navigate_to_potion_vendor(), which is the same function run() calls,
so this exercises the real code rather than a copy of it that can drift.

HOW TO RUN
    Stand the wizards you want to test wherever a bot would call handle_potions from, then:

        poetry run python tools/dry_run_potion_route.py
        poetry run python tools/dry_run_potion_route.py --client 2      # just one client
        poetry run python tools/dry_run_potion_route.py --no-recall     # stay in the Commons
        poetry run python tools/dry_run_potion_route.py --no-shop       # stop before Hilda

    Nothing is bought and no gold is spent. Clients ARE teleported around the spiral, their
    mark location IS overwritten, and any equipped castle is unequipped and re-equipped.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot.nodes import wizard_city
from bartlebot.nodes.combat import handle_potions
from bartlebot.nodes.teleportation import mark_location, teleport_to_marked_location

# Matches the node's own default. The route waits on server responses in several places, so
# this is the one knob worth exposing.
DEFAULT_SAFETY_DELAY = 3.0


class ConsoleMachine:
    """The slice of BotMachine that the potion route actually uses: log() and get_option().

    Nodes call machine.log(msg, level) and machine.get_option(name, default) and nothing
    else on this path (confirmed across handle_potions, enter_zone, mark_location and
    teleport_to_marked_location), so a real FSM is not needed to drive them.
    """

    def __init__(self, safety_delay: float):
        self._options = {"Safety Delay": safety_delay}
        self.warnings: list[str] = []

    def log(self, msg: str, level: int = logging.INFO) -> None:
        stamp = time.strftime("%H:%M:%S")
        print(f"  [{stamp}] {msg}")
        if level >= logging.WARNING:
            self.warnings.append(msg)

    def get_option(self, name: str, default=None):
        return self._options.get(name, default)


def _step(title: str) -> None:
    print(f"\n=== {title} ===")


async def _run(clients: list, machine: ConsoleMachine, recall: bool, shop: bool) -> bool:
    _step("Recording each client's position for the mark")
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    positions = await asyncio.gather(*[c.body.position() for c in clients])
    recall_params = [{"zone": zone, "xyz": {"x": pos.x, "y": pos.y, "z": pos.z}} for zone, pos in zip(zones, positions)]
    for i, (zone, pos) in enumerate(zip(zones, positions), start=1):
        print(f"  client {i}: {zone}  XYZ({pos.x:.1f}, {pos.y:.1f}, {pos.z:.1f})")

    _step("Marking")
    marks = await asyncio.gather(*[mark_location.run(machine, [c], rp) for c, rp in zip(clients, recall_params)])
    if not all(r == "SUCCESS" for r in marks):
        print("\nFAILED: mark_location did not succeed; stopping before anything moves.")
        return False

    if not shop:
        # Stop after the dorm hop so the castle unequip can be checked on its own — that is
        # the newest part of the route and the one most likely to misbehave.
        _step("Teleporting home (stopping before Hilda, per --no-shop)")
        reached = await wizard_city.teleport_home_and_verify_dorm(machine, clients)
        print(f"\n{'Reached the dorm.' if reached else 'FAILED: did not reach the dorm.'}")
        return reached

    _step("Travelling to Hilda Brewer")
    started = time.monotonic()
    arrived = await handle_potions.navigate_to_potion_vendor(machine, clients)
    print(f"\n  route took {time.monotonic() - started:.1f}s")
    if not arrived:
        print("FAILED: did not reach the potion vendor with the shop open.")
        return False

    _step("Closing the shop WITHOUT buying")
    await asyncio.gather(*[handle_potions.close_potion_shop(c) for c in clients])
    print("  shop closed; no gold spent")

    if not recall:
        print("\nDone — clients left in the Commons (--no-recall).")
        return True

    _step("Recalling to the mark")
    recalls = await asyncio.gather(
        *[teleport_to_marked_location.run(machine, [c], rp) for c, rp in zip(clients, recall_params)]
    )
    if not all(r == "SUCCESS" for r in recalls):
        print("\nFAILED: teleport_to_marked_location did not succeed for every client.")
        return False

    final_zones = await asyncio.gather(*[c.zone_name() for c in clients])
    for i, (want, got) in enumerate(zip(zones, final_zones), start=1):
        verdict = "ok" if want == got else f"MISMATCH (expected {want})"
        print(f"  client {i}: back in {got}  {verdict}")
    return True


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client", type=int, default=None, help="1-based client to drive (default: all)")
    parser.add_argument("--no-recall", action="store_true", help="leave clients in the Commons")
    parser.add_argument("--no-shop", action="store_true", help="stop after the dorm hop, before Hilda")
    parser.add_argument(
        "--safety-delay",
        type=float,
        default=DEFAULT_SAFETY_DELAY,
        help=f"seconds allowed for server responses (default: {DEFAULT_SAFETY_DELAY})",
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

    if args.client is not None:
        if not 1 <= args.client <= len(clients):
            print(f"--client must be between 1 and {len(clients)}.")
            return 1
        driving = [clients[args.client - 1]]
    else:
        driving = clients

    machine = ConsoleMachine(args.safety_delay)
    try:
        ok = await _run(driving, machine, recall=not args.no_recall, shop=not args.no_shop)
    finally:
        for c in clients:
            try:
                await c.close()
            except Exception:
                pass

    print("\n" + "=" * 70)
    if machine.warnings:
        # Warnings are worth surfacing even on success: a castle that failed to re-equip
        # does not fail the route, but the user still needs to know about it.
        print(f"{len(machine.warnings)} warning(s) during the run:")
        for warning in machine.warnings:
            print(f"  - {warning}")
    print("ROUTE OK" if ok else "ROUTE FAILED")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
