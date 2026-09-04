"""Form an in-game group across every running Wizard101 client.

Runs the exact code path the app uses — bartlebot.wizard_names for name detection and
bartlebot.group_manager for the invite flow — but without the GUI, a bot file, or the
settings system. What works here works in a real run, and what breaks here breaks there.

PREREQUISITES
    * Two or more wizards logged in and in-game (not at character select).
    * Every account on every other account's friends list. The invite is sent by opening
      the target's profile from the leader's friends list, so an unfriended player simply
      cannot be found.

USAGE
    # Survey only — report who is grouped, without inviting anyone.
    uv run python tools/form_group.py --check

    # Detect wizard names only, without touching the group UI.
    uv run python tools/form_group.py --names

    # Form the group (client 1 leads by default).
    uv run python tools/form_group.py

    # Form the group with a specific leader (1-based, matching the printed list).
    uv run python tools/form_group.py --leader 2

Start with --check. It exercises the group read (social button, Group tab, Leave Group
button, member roster) on its own, which is the part most likely to misbehave, and makes
no changes. It also reports whether the clients are in one group or several — the roster
is what distinguishes those, since the Leave Group button reads True in both cases.

Every step is logged. Pass -q to see only the result.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot import group_manager, wizard_names


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # WizWalker logs prolifically at DEBUG and would drown out the group steps.
    logging.getLogger("wizwalker").setLevel(logging.WARNING)


def _label(client, index: int) -> str:
    try:
        from wizwalker.utils import get_window_title  # noqa: PLC0415

        return f"[{index + 1}] {get_window_title(client.window_handle)}"
    except Exception:
        return f"[{index + 1}] client"


async def _detect_names(clients: list) -> list[str | None]:
    """Resolve every client's wizard name, reporting each one as it is found."""
    print("\nDetecting wizard names (opens and closes the character page on each client)...")
    names = await wizard_names.resolve_names(clients)
    for i, (client, name) in enumerate(zip(clients, names)):
        status = name if name else "*** NOT DETECTED ***"
        print(f"  {_label(client, i):<34} {status}")
    if any(n is None for n in names):
        print(
            "\n  A name could not be read from the character page. That client cannot be\n"
            "  invited, because the leader finds it in the friends list by name."
        )
    return names


async def _survey(clients: list) -> list[group_manager.GroupState]:
    """Report which clients are currently in a group, and who each one is grouped with."""
    print("\nChecking group membership (social button -> Group tab -> Leave Group button)...")
    states = await group_manager.read_group_states(clients)
    for i, (client, state) in enumerate(zip(clients, states)):
        status = "IN A GROUP" if state.grouped else "not grouped"
        roster = ", ".join(state.roster) if state.roster else "<roster unreadable>"
        print(f"  {_label(client, i):<34} {status:<12} {roster}")
    return states


def _report_same_group(states: list[group_manager.GroupState]) -> None:
    """Print the verdict on whether every client is in ONE group."""
    verdict = group_manager.same_group(states)
    if verdict is True:
        print("\nEvery client is in the SAME group — the rosters list identical members.")
    elif verdict is False:
        print("\nClients are in SEPARATE groups — their rosters do not match. Running without")
        print("--check will dissolve all but the largest group and invite its members across.")
        print("Note that dissolving a group also drops any players in it who are not ours.")
    else:
        print("\nEvery client reports being in a group, but at least one roster could not be read,")
        print("so this cannot tell whether it is the same group. Run tools/probe_group_roster.py.")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report group membership and exit, without inviting")
    parser.add_argument("--names", action="store_true", help="detect wizard names and exit")
    parser.add_argument("--leader", type=int, default=1, help="1-based client that sends invitations (default: 1)")
    parser.add_argument("-q", "--quiet", action="store_true", help="less logging")
    args = parser.parse_args()

    _setup_logging(verbose=not args.quiet)

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

    for i, client in enumerate(clients):
        print(f"  {_label(client, i)}")

    try:
        if args.names:
            await _detect_names(clients)
            return 0

        if args.check:
            states = await _survey(clients)
            ungrouped = sum(1 for s in states if not s.grouped)
            if len(clients) < 2:
                print("\nOnly one client — a group needs at least two.")
            elif ungrouped:
                print(f"\n{ungrouped} client(s) are not in a group. Run without --check to form one.")
            else:
                _report_same_group(states)
            return 0

        if len(clients) < 2:
            print("\nNeed at least two clients to form a group.")
            return 1

        if not 1 <= args.leader <= len(clients):
            print(f"\n--leader must be between 1 and {len(clients)}.")
            return 1

        names = await _detect_names(clients)

        print("\nForming group...")
        result = await group_manager.form_group(
            clients,
            names,
            leader_index=args.leader - 1,
            # Mirror group_manager's own log lines to stdout so the flow is visible live.
            log=lambda msg, level=logging.INFO: print(f"  {msg}"),
        )

        print("\n" + "=" * 70)
        print("SUCCESS" if result.success else "INCOMPLETE")
        print("=" * 70)
        print(f"  {result.summary()}")
        if result.left:
            print(f"  left another group: {', '.join(result.left)}")
        if result.added:
            print(f"  added:  {', '.join(result.added)}")
        if result.failed:
            print(f"  failed: {', '.join(result.failed)}")

        print("\nVerifying final state...")
        final = await _survey(clients)
        if all(s.grouped for s in final):
            _report_same_group(final)

        return 0 if result.success else 1
    finally:
        for client in clients:
            try:
                await client.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
