"""Diagnostic: find a window property that actually distinguishes grouped from ungrouped.

is_in_group() currently reads the Leave Group button with is_enabled_by_path(), which
returns True whenever the window EXISTS and is not grayed — it never checks visibility.
Against live clients that were not in a group, it reported both as grouped, so that
predicate is wrong. Wizard101 commonly hides controls rather than graying them, so a
hidden button still reads as "enabled".

That same gap means we also cannot yet confirm the social window opens at all: the button
path resolves whether or not the window is showing.

This probe answers both questions by recording, for every window under
SocialSystemsWindow, the three properties we could branch on — exists / visible / grayed
— plus its text, in two states you set up:

    snapshot A: NOT in a group
    snapshot B: IN a group

It then reports every window whose properties DIFFER between the two. Any such window is
a candidate indicator; the report calls out the ones on the Leave Group button itself.

HOW TO RUN
    1. Log a wizard in. Only the first client is inspected.
    2. uv run python tools/probe_group_ui.py
    3. Follow the prompts: ungrouped first, then grouped.
    4. Paste the report back.

Read-only apart from opening and closing the social window itself.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot.nodes import node_helpers
from bartlebot.nodes.paths import (
    GROUP_WINDOW_CLOSE_PATH,
    LEAVE_GROUP_BUTTON_PATH,
    SOCIAL_BUTTON_PATH,
    SOCIAL_GROUP_TAB_PATH,
    SOCIAL_WINDOW_PATH,
)
from bartlebot.nodes.timing import POLL_INTERVAL, TRIGGER_TIMEOUT

# Depth limit for the subtree dump. The social window nests a few layers; beyond this the
# output stops being readable without adding information.
MAX_DEPTH = 6


async def _probe_path(client, path: list[str]) -> dict:
    """Record every property we could branch on for one window path."""
    window = await node_helpers._get_window_from_path(client.root_window, path)
    if window is None:
        return {"exists": False, "visible": None, "grayed": None, "text": None}
    out = {"exists": True}
    for key, getter in (
        ("visible", window.is_visible),
        ("grayed", window.is_control_grayed),
        ("text", window.maybe_text),
    ):
        try:
            out[key] = await getter()
        except Exception as exc:
            out[key] = f"<{type(exc).__name__}>"
    return out


async def _dump_subtree(window, prefix: str = "", depth: int = 0) -> dict[str, dict]:
    """Return {path_string: properties} for every window under `window`."""
    out: dict[str, dict] = {}
    if depth > MAX_DEPTH:
        return out
    try:
        children = await window.children()
    except Exception:
        return out
    for child in children:
        try:
            name = await child.name()
        except Exception:
            continue
        key = f"{prefix}/{name}" if prefix else name
        entry = {}
        for prop, getter in (
            ("visible", child.is_visible),
            ("grayed", child.is_control_grayed),
            ("text", child.maybe_text),
        ):
            try:
                entry[prop] = await getter()
            except Exception as exc:
                entry[prop] = f"<{type(exc).__name__}>"
        # Duplicate sibling names are real here (wndCharacter has two ButtonLayouts), so
        # disambiguate rather than silently overwriting one with the other.
        unique = key
        n = 2
        while unique in out:
            unique = f"{key}#{n}"
            n += 1
        out[unique] = entry
        out.update(await _dump_subtree(child, unique, depth + 1))
    return out


async def snapshot(client, label: str) -> dict:
    """Open the social window, record everything, and close it again."""
    print(f"\n--- snapshot: {label} ---")

    closed_state = await _probe_path(client, SOCIAL_WINDOW_PATH)
    print(f"  before opening: SocialSystemsWindow {closed_state}")

    clicked = await node_helpers.click_by_path(client, SOCIAL_BUTTON_PATH)
    print(f"  clicked social button: {clicked}")

    opened = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, SOCIAL_WINDOW_PATH),
        timeout=TRIGGER_TIMEOUT,
        poll_interval=POLL_INTERVAL,
    )
    print(f"  social window reported visible: {opened}")

    tab_clicked = await node_helpers.click_by_path(client, SOCIAL_GROUP_TAB_PATH)
    print(f"  clicked Group tab: {tab_clicked}")
    await asyncio.sleep(1.0)

    result = {
        "social_window": await _probe_path(client, SOCIAL_WINDOW_PATH),
        "group_tab": await _probe_path(client, SOCIAL_GROUP_TAB_PATH),
        "leave_button": await _probe_path(client, LEAVE_GROUP_BUTTON_PATH),
    }
    for name, props in result.items():
        print(f"  {name:<16} {props}")

    social = await node_helpers._get_window_from_path(client.root_window, SOCIAL_WINDOW_PATH)
    result["subtree"] = await _dump_subtree(social) if social is not None else {}
    print(f"  subtree windows recorded: {len(result['subtree'])}")

    await node_helpers.click_by_path(client, GROUP_WINDOW_CLOSE_PATH)
    return result


def report(a: dict, b: dict) -> None:
    print("\n" + "=" * 78)
    print("REPORT")
    print("=" * 78)

    print("\nKey paths (ungrouped -> grouped):")
    for key in ("social_window", "group_tab", "leave_button"):
        pa, pb = a[key], b[key]
        changed = "  <-- DIFFERS" if pa != pb else ""
        print(f"\n  {key}{changed}")
        for prop in ("exists", "visible", "grayed", "text"):
            mark = " *" if pa.get(prop) != pb.get(prop) else "  "
            print(f"   {mark} {prop:<8} {pa.get(prop)!r:<28} {pb.get(prop)!r}")

    print("\n" + "-" * 78)
    print("Every window under SocialSystemsWindow whose properties differ:")
    keys = sorted(set(a["subtree"]) | set(b["subtree"]))
    differing = [k for k in keys if a["subtree"].get(k) != b["subtree"].get(k)]
    if not differing:
        print("\n  NONE. Nothing under the social window distinguishes the two states,")
        print("  so group membership cannot be read from this window at all.")
    for k in differing:
        pa = a["subtree"].get(k, "<absent>")
        pb = b["subtree"].get(k, "<absent>")
        print(f"\n  {k}")
        print(f"    ungrouped: {pa}")
        print(f"    grouped:   {pb}")

    print("\n" + "=" * 78)
    leave_a, leave_b = a["leave_button"], b["leave_button"]
    for prop in ("exists", "visible", "grayed"):
        if leave_a.get(prop) != leave_b.get(prop):
            print(
                f"USE THIS: leave_button.{prop} distinguishes the states "
                f"({leave_a.get(prop)!r} ungrouped, {leave_b.get(prop)!r} grouped)."
            )
            break
    else:
        print("The Leave Group button looks IDENTICAL in both states — is_in_group() cannot")
        print("use it. Check the differing-windows list above for a usable indicator.")
    print("=" * 78)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client", type=int, default=1, help="1-based client to inspect (default: 1)")
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

    if not 1 <= args.client <= len(clients):
        print(f"--client must be between 1 and {len(clients)}.")
        return 1
    client = clients[args.client - 1]

    try:
        async with client.mouse_handler:
            input("\nMake sure this wizard is NOT in a group, then press Enter...")
            ungrouped = await snapshot(client, "NOT in a group")

            input("\nNow put this wizard IN a group, then press Enter...")
            grouped = await snapshot(client, "IN a group")

        report(ungrouped, grouped)
        return 0
    finally:
        for c in clients:
            try:
                await c.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
