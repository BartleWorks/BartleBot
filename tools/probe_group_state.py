"""Diagnostic: watch in-game group state across every logged-in client.

OUTCOME: the memory route is DEAD on this game build. group_gid, player_gid and
current_party_gid all read 0 and never change across joins and leaves — player_gid being 0
is decisive, since a logged-in wizard always has a non-zero global id, so the object being
read is not the live one (see probe_group_offsets.py). group_manager.py therefore reads
membership from the UI instead. Keep this probe for re-checking after a game patch, in case
a future build resolves the manager correctly.

The questions it was written to answer, when the plan still rested on
SocialSystemsManager.group_gid():

  Q1. Does group_gid() read 0 when the wizard is NOT in a group?
      If it instead keeps a stale non-zero value after leaving a group, is_grouped()
      would wrongly conclude everyone is still grouped and skip the regroup entirely --
      the feature would silently do nothing.

  Q2. Does the leader's OWN group_gid change when the first invite creates a group?
      form_group() re-reads the leader's GID before each invite on the assumption that
      it does. If the GID is instead fixed, that re-read is merely redundant.

It also answers two smaller questions that shape the code:

  Q3. Do all members of one group share the SAME gid value? (is_grouped compares them.)
  Q4. Does the gid survive a relaunch, or is a new one issued? (Recovery re-reads it.)

HOW TO RUN
    1. Log in every wizard you normally bot with -- at least two.
    2. uv run python tools/probe_group_state.py
    3. Leave it running and do this in-game, watching the table after each step:
         a. Make sure NOBODY is grouped.        -> expect all gid = 0            (Q1)
         b. Invite player 2 into a group.       -> watch BOTH gids change        (Q2, Q3)
         c. Invite player 3, if you have one.   -> does the gid change again?    (Q2)
         d. Have player 2 leave the group.      -> does its gid return to 0?     (Q1)
         e. Log player 2 out and back in.       -> what gid does it come back with? (Q4)
    4. Ctrl+C to stop.

Only rows that CHANGED are reprinted, so the output is a timeline of transitions rather
than a wall of repeated numbers. Every reading is timestamped so it can be lined up with
what you did in-game.

Read-only: this only reads memory. It never clicks, types, or writes, so it cannot
disturb the clients it is watching.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime

from wizwalker.extensions.wizsprinter import WizSprinter

POLL_INTERVAL = 0.5


async def _safe(coro) -> object:
    """Return the value of an awaitable, or the exception rendered as a string.

    Every field here is read speculatively: some are only populated once the game has
    received the relevant server message, so a failure is information, not an error.
    """
    try:
        return await coro
    except Exception as exc:
        return f"<{type(exc).__name__}>"


async def read_state(client) -> dict:
    """Read every group-related field this feature depends on, for one client."""
    ssm = client.social_systems_manager
    return {
        "group_gid": await _safe(ssm.group_gid()),
        "player_gid": await _safe(ssm.player_gid()),
        "current_party_gid": await _safe(ssm.current_party_gid()),
        "has_group_quest": await _safe(ssm.has_group_quest()),
        "zone": await _safe(client.zone_name()),
    }


def _fmt_row(label: str, state: dict) -> str:
    return (
        f"  {label:<22} "
        f"group_gid={str(state['group_gid']):<22} "
        f"player_gid={str(state['player_gid']):<22} "
        f"party_gid={str(state['current_party_gid']):<22} "
        f"quest={str(state['has_group_quest']):<7} "
        f"{state['zone']}"
    )


def _verdict(states: list[dict]) -> str:
    """Summarize what the current readings imply, so the answers are not left to eyeballing."""
    gids = [s["group_gid"] for s in states]
    numeric = [g for g in gids if isinstance(g, int)]
    if not numeric:
        return "group_gid unreadable on every client"
    if all(g == 0 for g in numeric):
        return "NOBODY GROUPED (all group_gid == 0)"
    if len(set(numeric)) == 1:
        return f"ALL GROUPED TOGETHER (shared group_gid == {numeric[0]})"
    nonzero = {g for g in numeric if g != 0}
    if len(nonzero) == 1 and 0 in numeric:
        return f"PARTIALLY GROUPED (group {nonzero.pop()}, plus {numeric.count(0)} ungrouped)"
    return f"SPLIT ACROSS GROUPS (distinct gids: {sorted(nonzero)})"


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

    if len(clients) < 2:
        print("\nNOTE: only one client is running. Q2/Q3 need at least two wizards logged in.\n")

    labels = []
    for i, client in enumerate(clients, start=1):
        try:
            from wizwalker.utils import get_window_title  # noqa: PLC0415

            labels.append(f"[{i}] {get_window_title(client.window_handle)}")
        except Exception:
            labels.append(f"[{i}] client")

    print("\nWatching group state. Do the in-game steps from the module docstring.")
    print("Only changed readings are printed. Press Ctrl+C to stop.\n")

    previous: list[dict] | None = None
    try:
        while True:
            states = [await read_state(c) for c in clients]
            if states != previous:
                stamp = datetime.now().strftime("%H:%M:%S")
                print(f"[{stamp}] {_verdict(states)}")
                for label, state in zip(labels, states):
                    print(_fmt_row(label, state))
                print()
                previous = states
            await asyncio.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        for client in clients:
            try:
                await client.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
