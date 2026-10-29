"""Diagnostic: inspect the friends-list windows and confirm the list can be closed.

Kept for re-checking after a game patch. What it established:

  * There are TWO independent friends-list windows, each its own UI subtree. Only one is
    ever on screen, which is what made this hard to diagnose:
      - "NewFriendsListWindow" — the compact list. Opens from nothing on F / btnFriends.
      - "wndFriendsList" — the larger, detailed list. Opens from the compact list on a
        second F, or via its "More Friends" button.
    F cycles closed -> compact -> large -> closed.
  * btnFriendListClose belongs to wndFriendsList and does NOT close NewFriendsListWindow.
    With nothing overlapping it, clicking it left the compact list open through a full
    retry cycle. Earlier runs where it seemed to work were coincidence: both windows place
    a close button in the same spot, so the click sometimes hit whichever list was up.
  * A control's is_visible() ignores its parents, so close buttons report visible=True
    even inside a hidden window. Never poll a close button to decide whether its window
    shut — read the window.

close_friends_list() therefore presses F until NEITHER window is visible.

This probe records both windows on every client, before and after opening the list, and
after attempting a close.

HOW TO RUN
    1. Log in the same clients you saw the problem on. Start with friends lists CLOSED.
    2. uv run python tools/probe_friends_list.py
    3. Paste the report back.

This one DOES click: it opens the friends list and tries to close it, on every client.
"""

from __future__ import annotations

import asyncio
import sys

from bartleby.runtime.paths import FRIENDS_LIST_PATHS
from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot.nodes import node_helpers

# Both friends-list windows, plus every close button worth distinguishing.
CANDIDATE_WINDOWS = [path[-1] for path in FRIENDS_LIST_PATHS]
CLOSE_BUTTON_NAMES = ["btnFriendListClose", "btnClose", "Close_Button", "exit"]


async def _find_by_name(client, name: str):
    """Locate a window anywhere in the tree by name; None if absent or ambiguous."""
    try:
        found = await client.root_window.get_windows_with_name(name)
    except Exception:
        return None
    return found[0] if found else None


async def _props(window) -> str:
    if window is None:
        return "absent"
    out = []
    for label, getter in (("visible", window.is_visible), ("grayed", window.is_control_grayed)):
        try:
            out.append(f"{label}={await getter()}")
        except Exception as exc:
            out.append(f"{label}=<{type(exc).__name__}>")
    return ", ".join(out)


async def _describe(client, label: str) -> None:
    print(f"\n  [{label}]")
    for name in CANDIDATE_WINDOWS:
        window = await _find_by_name(client, name)
        print(f"    {name:<22} {await _props(window)}")
        if window is None:
            continue
        # Report every child that looks like a close button, wherever it sits.
        for child_name in CLOSE_BUTTON_NAMES:
            try:
                children = await window.get_windows_with_name(child_name)
            except Exception:
                continue
            for child in children:
                print(f"      -> {child_name:<18} {await _props(child)}")

    # The signal the code actually uses to decide whether the list is open — either window.
    print(f"    is_friends_list_open = {await node_helpers.is_friends_list_open(client)}")


async def probe(client, index: int) -> None:
    try:
        from wizwalker.utils import get_window_title  # noqa: PLC0415

        title = get_window_title(client.window_handle)
    except Exception:
        title = "client"

    print("\n" + "=" * 78)
    print(f"CLIENT {index + 1}: {title}")
    print("=" * 78)

    await _describe(client, "before opening the friends list")

    print("\n  opening the friends list via open_friends_list()...")
    try:
        async with client.mouse_handler:
            await node_helpers.open_friends_list(client)
        await asyncio.sleep(1.0)
    except Exception as exc:
        print(f"    open_friends_list failed: {exc!r}")

    await _describe(client, "after opening")

    print("\n  attempting the current close (close_friends_list)...")
    closed = await node_helpers.close_friends_list(client)
    print(f"    close_friends_list returned {closed}")
    await asyncio.sleep(1.0)

    await _describe(client, "after attempting to close")


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

    try:
        for i, client in enumerate(clients):
            await probe(client, i)

        print("\n" + "=" * 78)
        print("WHAT TO LOOK FOR")
        print("=" * 78)
        print(
            "  * NewFriendsListWindow should read visible=False, then True after opening,\n"
            "    then False again after the close.\n"
            "  * wndFriendsList should read visible=False throughout: open_friends_list()\n"
            "    opens only the compact list, and F closes it before reaching the large one.\n"
            "    If it is True at the end, the close stopped one press short.\n"
            "  * is_friends_list_open must end False — it covers BOTH windows, and it is the\n"
            "    signal the close loop terminates on.\n"
            "  * close_friends_list should return True with no timeout warning.\n"
            "  * Close buttons reading visible=True while their window is hidden is EXPECTED\n"
            "    — a control's visibility flag ignores its parents."
        )
        return 0
    finally:
        for client in clients:
            try:
                await client.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
