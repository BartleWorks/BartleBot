"""Verify what the Group tab's member roster actually contains.

group_manager reads the roster to answer a question the Leave Group button cannot: not
"am I in a group?" but "am I in the SAME group as the others?". Two clients in two
different groups both report grouped=True, so without the roster a split group is
invisible, and the code silently decides there is nothing to do.

The roster read rests on layout assumptions that this probe exists to check, because the
last two attempts to read something off this window were both built on a plausible premise
that turned out to be false (GroupPageNumber reading visible=True on every tab, and then
the whole tab-indicator search coming back empty — see tools/probe_group_tab.py).

QUESTIONS THIS ANSWERS

  Q1. Does the path resolve at all?
      ["WorldView", "SocialSystemsWindow", "GroupLayout", "SubGroupLayout",
       "LineN", "NameText"]
      If it does not, group_manager reads an empty roster on every client and every
      same-group check degrades to "cannot tell" — safe, but useless.

  Q2. How many Line windows exist? GROUP_ROSTER_LINES assumes Wizard101's cap of 4. A
      fifth would mean rosters silently truncate in a full group, which would make two
      clients in the SAME group look like they are in different ones.

  Q3. Is Line1 always the wizard doing the viewing?
      This is NOT relied on by the current code — same_group compares rosters as unordered
      sets precisely so that line meaning cannot break it. But if Line1 is reliably self,
      the roster becomes a free source of wizard names, replacing the character-page open
      that wizard_names.detect_wizard_name costs per client. That is worth having, and
      this is the check that would justify it.

  Q4. Is the name text clean, or does it carry markup or decoration (a level, a leader
      crown, "(Leader)")? strip_markup handles KI color tags; anything else would need
      handling before names can be compared between clients.

      One answer is already in: the first EMPTY slot is an invitation prompt whose NameText
      reads "Left", leftover placeholder text from the game's layout. It appeared on every
      client in every group, making a two-wizard group read as three members.
      group_manager filters it by name (_ROSTER_PLACEHOLDERS). If another one shows up
      after a game update, this probe's raw output is how to identify it.

  Q5. What does the roster look like when NOT in a group? If Line1 still shows self, an
      ungrouped client reads as a one-member roster, which same_group must not mistake for
      agreement with another one-member roster.

USAGE
    # Log every wizard in, then:
    uv run python tools/probe_group_roster.py

    # Run it twice, once grouped and once not, and compare:
    uv run python tools/probe_group_roster.py --label ungrouped
    uv run python tools/probe_group_roster.py --label grouped

Read-only apart from opening and closing the social window, which it restores afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot import group_manager
from bartlebot.game import wizard_names
from bartlebot.nodes import node_helpers
from bartlebot.nodes.paths import (
    GROUP_ROSTER_LINES,
    SOCIAL_BUTTON_PATH,
    SOCIAL_GROUP_TAB_PATH,
    SOCIAL_WINDOW_PATH,
    group_member_name_path,
)
from bartlebot.nodes.timing import GROUP_UI_SETTLE, POLL_INTERVAL, TRIGGER_TIMEOUT

# Probe one line past the assumed cap. If this one resolves, GROUP_ROSTER_LINES is too
# small and full groups are being truncated — see Q2.
_PROBE_LINES = GROUP_ROSTER_LINES + 1


def _label(client, index: int) -> str:
    try:
        from wizwalker.utils import get_window_title  # noqa: PLC0415

        return f"[{index + 1}] {get_window_title(client.window_handle)}"
    except Exception:
        return f"[{index + 1}] client"


async def _open_group_tab(client) -> bool:
    """Get the social window onto the Group tab. Returns True once it is showing.

    Deliberately duplicates group_manager._open_social_window rather than calling it. This
    probe exists to check assumptions about the window's layout, so it should keep working
    — and keep reporting honestly — if that private helper is refactored around it.
    """
    if not await node_helpers.is_visible_by_path(client, SOCIAL_WINDOW_PATH):
        await node_helpers.click_by_path(client, SOCIAL_BUTTON_PATH)
        opened = await node_helpers.poll_until(
            lambda: node_helpers.is_visible_by_path(client, SOCIAL_WINDOW_PATH),
            timeout=TRIGGER_TIMEOUT,
            poll_interval=POLL_INTERVAL,
        )
        if not opened:
            return False
    await node_helpers.click_by_path(client, SOCIAL_GROUP_TAB_PATH)
    await asyncio.sleep(GROUP_UI_SETTLE)
    return True


async def _probe_one(client) -> dict:
    """Open the Group tab and record every roster line, raw and cleaned."""
    try:
        if not await _open_group_tab(client):
            return {"opened": False}

        lines = []
        for line in range(1, _PROBE_LINES + 1):
            path = group_member_name_path(line)
            try:
                raw = await node_helpers.get_text_by_path(client, path)
            except Exception as exc:
                lines.append({"line": line, "raw": f"<{type(exc).__name__}>", "clean": ""})
                continue
            lines.append({"line": line, "raw": raw, "clean": wizard_names.strip_markup(raw)})
        return {"opened": True, "lines": lines}
    finally:
        await group_manager.close_social_window(client)


def _report(client_label: str, detected: str | None, result: dict) -> None:
    print(f"\n{client_label}")
    print("-" * len(client_label))
    if not result.get("opened"):
        print("  social window did not open — nothing could be read")
        return

    print(f"  grouped (Leave Group button): {result['grouped']}")
    print(f"  wizard name (character page): {detected or '<not detected>'}")
    print("  roster lines:")
    for entry in result["lines"]:
        marker = ""
        if entry["line"] > GROUP_ROSTER_LINES and entry["clean"]:
            marker = "   <-- BEYOND GROUP_ROSTER_LINES, rosters are being truncated"
        shown = entry["raw"] if entry["raw"] else "<empty / no window>"
        print(f"    Line{entry['line']}: {shown!r} -> {entry['clean']!r}{marker}")

    clean = [e["clean"] for e in result["lines"] if e["clean"]]
    if not clean:
        print("\n  NO NAMES READ. The path does not resolve, or the labels are empty.")
        print("  Same-group checks will report 'cannot tell' on every survey.")
        return

    if detected:
        first = clean[0]
        if first.casefold() == detected.casefold():
            print(f"\n  Line1 == this wizard ({first!r}) — Q3 holds for this client.")
        else:
            print(f"\n  Line1 is {first!r}, NOT this wizard ({detected!r}) — Q3 FAILS.")
            print("  Do not derive wizard names from Line1. same_group is unaffected (it")
            print("  compares rosters as unordered sets), but the name shortcut is off.")
        if not any(name.casefold() == detected.casefold() for name in clean):
            print(f"  WARNING: {detected!r} does not appear on its own roster at all.")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default="", help="tag for this run, e.g. 'grouped' or 'ungrouped'")
    parser.add_argument("--no-names", action="store_true", help="skip character-page name detection (Q3/Q5 only)")
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
        header = "GROUP ROSTER PROBE" + (f"  [{args.label}]" if args.label else "")
        print("\n" + "=" * 78)
        print(header)
        print("=" * 78)

        names: list[str | None] = [None] * len(clients)
        if not args.no_names:
            print("\nDetecting wizard names (opens and closes the character page on each client)...")
            names = await wizard_names.resolve_names(clients)

        states = await group_manager.read_group_states(clients)
        results = await asyncio.gather(*[_probe_one(c) for c in clients])
        for i, (client, result) in enumerate(zip(clients, results)):
            result["grouped"] = states[i].grouped
            _report(_label(client, i), names[i], result)

        print("\n" + "=" * 78)
        verdict = group_manager.same_group(states)
        if verdict is True:
            print("SAME GROUP: every roster lists the same members.")
        elif verdict is False:
            print("SEPARATE GROUPS: the rosters disagree. If these wizards ARE grouped together")
            print("in game, the roster read is wrong and same_group will block regrouping.")
        else:
            print("UNDETERMINED: at least one roster was unreadable, so same_group returned None")
            print("and every caller falls back to the per-client yes/no, as it did before.")
        print("=" * 78)
        return 0
    finally:
        for client in clients:
            try:
                await client.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
