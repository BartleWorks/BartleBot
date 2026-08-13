"""Diagnostic: find a window property that tells us WHICH TAB the social window is showing.

is_in_group() has to get the social window onto the Group tab before it can read the Leave
Group button. It does that by clicking Tab_Group and then sleeping a fixed
GROUP_UI_SETTLE (1 s), because we have nothing to wait for — and it clicks unconditionally,
even when the window is already on that tab, for the same reason.

That sleep is the single largest fixed cost in a group membership survey, so it is worth
replacing with a poll. The obvious candidate was GroupPageNumber, which lives inside
GroupLayout and so ought to exist only on the Group page. It does not work: it reads
visible=True on every tab. That is the hazard is_visible_by_path documents — a window's
is_visible() flag is its OWN flag and ignores its parents, so a control inside a hidden
page still reports visible.

This probe looks for a property that actually does vary with tab selection. It clicks
through every tab under TabLayout and, for each one, records a pile of properties for every
window under SocialSystemsWindow. It then reports the (window, property) pairs whose value
on the Group tab differs from their value on EVERY other tab.

Two details that make the difference between a useful answer and another false lead:

  * "Differs from every other tab", not "differs from one other tab". A property that
    separates Group from Friends may well be identical between Group and Ignore, and a
    predicate built on it would silently misfire on the third tab.

  * The Group tab is sampled TWICE, at the start and the end of the sweep. Anything that
    changes between those two samples is the UI moving under us — animations, alpha fades,
    a scrolling list — not a tab indicator. Those pairs are dropped as noise. Without this
    the report is dominated by rectangles and alphas that mean nothing.

RESULT (2026-08-11): none. Across every tab, nothing under SocialSystemsWindow holds a
value on the Group tab that it does not also hold on some other tab. The selected tab is
simply not readable, so _open_social_window clicks Tab_Group unconditionally and sleeps
GROUP_UI_SETTLE. Shortening that sleep was considered and rejected — 1.0 s is what covers
the page populating under load, and a misread makes the next regroup send spurious
invites. The cost was addressed by running the whole survey concurrently instead
(group_manager.read_group_flags), which turns it into one second for the fleet rather than
one per client.

This script is kept for re-running after a game update, when the tree may change.

HOW TO RUN
    1. Log a wizard in. Only one client is inspected.
    2. poetry run python tools/probe_group_tab.py
    3. Paste the report back.

Clicks tabs inside the social window and opens/closes that window. Otherwise read-only.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot.nodes import node_helpers
from bartlebot.nodes.paths import (
    GROUP_WINDOW_CLOSE_PATH,
    SOCIAL_BUTTON_PATH,
    SOCIAL_GROUP_TAB_PATH,
    SOCIAL_WINDOW_PATH,
)
from bartlebot.nodes.timing import POLL_INTERVAL, TRIGGER_TIMEOUT

# The tab strip, and the name of the tab we actually care about.
TAB_LAYOUT_PATH = ["WorldView", "SocialSystemsWindow", "TabLayout"]
GROUP_TAB_NAME = SOCIAL_GROUP_TAB_PATH[-1]

# How long to let a tab switch settle before reading. Deliberately generous — this is a
# diagnostic, and a value read mid-animation is exactly the noise the double-sample of the
# Group tab exists to catch.
TAB_SETTLE = 1.5

# Depth limit for the subtree walk. The group page nests further than the social window's
# top level, so this goes deeper than probe_group_ui.py's 6.
MAX_DEPTH = 8

# Properties recorded for every window. Breadth is the point: `visible` is already known to
# be useless here, so the answer is likely to be one of the less obvious ones. Values are
# stored as repr strings so that everything is comparable and printable regardless of type.
_PROPERTIES = (
    "is_visible",
    "is_control_grayed",
    "maybe_checked",
    "maybe_text",
    "alpha",
    "target_alpha",
    "disabled_alpha",
    "flags",
    "style",
    "window_rectangle",
    "scale",
    "offset",
    "parent_offset",
)

ABSENT = "<absent>"


async def _read_properties(window) -> dict[str, str]:
    """Record every property we could branch on, as repr strings."""
    out: dict[str, str] = {}
    for prop in _PROPERTIES:
        getter = getattr(window, prop, None)
        if getter is None:
            continue
        try:
            out[prop] = repr(await getter())
        except Exception as exc:
            # Plenty of these are type-specific (maybe_checked on a non-checkbox, say).
            # Record the failure rather than dropping the property: a getter that raises
            # on one tab and succeeds on another is itself a discriminator.
            out[prop] = f"<{type(exc).__name__}>"
    return out


async def _dump_subtree(window, prefix: str = "", depth: int = 0) -> dict[str, dict[str, str]]:
    """Return {path_string: {property: repr}} for every window under `window`."""
    out: dict[str, dict[str, str]] = {}
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
        # Duplicate sibling names are real in this tree, so disambiguate rather than
        # silently overwriting one with the other.
        unique = key
        n = 2
        while unique in out:
            unique = f"{key}#{n}"
            n += 1
        out[unique] = await _read_properties(child)
        out.update(await _dump_subtree(child, unique, depth + 1))
    return out


def find_candidates(
    snapshots: dict[str, dict[str, dict[str, str]]],
    group_label: str,
    recheck_label: str,
) -> tuple[list[tuple[str, str, str]], set[tuple[str, str]]]:
    """Return the (path, property, group_value) triples that identify the Group tab.

    `snapshots` maps a tab label to that tab's subtree dump. A pair qualifies when its
    value under `group_label` differs from its value under every OTHER tab — excluding
    `recheck_label`, which is a second sample of the Group tab itself.

    Pairs whose value differs between the two Group samples are unstable (animation, a
    live-updating list) and are excluded, and returned separately so the caller can say how
    much was discarded.

    Pure so it can be tested without a game client; the probe is otherwise untestable.
    """
    group = snapshots[group_label]
    recheck = snapshots.get(recheck_label, group)
    others = {label: dump for label, dump in snapshots.items() if label not in (group_label, recheck_label)}
    if not others:
        # Nothing to contrast against. The uniqueness test below is an `all()` over the
        # other tabs, which is vacuously true when there are none — so without this guard
        # a sweep that failed to visit any other tab would report EVERY property as a
        # perfect indicator. That is the most dangerous output this probe could produce.
        return [], set()

    every_path = set(group) | set(recheck)
    for dump in others.values():
        every_path |= set(dump)

    unstable: set[tuple[str, str]] = set()
    candidates: list[tuple[str, str, str]] = []

    for path in sorted(every_path):
        props = set(group.get(path, {})) | set(recheck.get(path, {}))
        for dump in others.values():
            props |= set(dump.get(path, {}))
        for prop in sorted(props):
            value = group.get(path, {}).get(prop, ABSENT)
            if value != recheck.get(path, {}).get(prop, ABSENT):
                unstable.add((path, prop))
                continue
            if all(dump.get(path, {}).get(prop, ABSENT) != value for dump in others.values()):
                candidates.append((path, prop, value))

    # Cheap-to-read, unambiguous properties first: a bool predicate is what the caller
    # actually wants, and a rectangle that happens to differ is a much worse bet.
    def rank(item: tuple[str, str, str]) -> tuple[int, str, str]:
        path, prop, _ = item
        tier = 0 if prop in ("is_visible", "is_control_grayed", "maybe_checked") else 1
        return (tier, path, prop)

    return sorted(candidates, key=rank), unstable


async def _tab_names(client) -> list[str]:
    """Return the names of every tab button under TabLayout, in tree order."""
    layout = await node_helpers._get_window_from_path(client.root_window, TAB_LAYOUT_PATH)
    if layout is None:
        return []
    names = []
    for child in await layout.children():
        try:
            names.append(await child.name())
        except Exception:
            continue
    return names


async def _snapshot(client, tab_name: str, label: str) -> dict[str, dict[str, str]]:
    """Select one tab and dump the whole social window subtree."""
    print(f"\n--- {label}: clicking {tab_name} ---")
    clicked = await node_helpers.click_by_path(client, TAB_LAYOUT_PATH + [tab_name])
    print(f"    clicked: {clicked}" + ("" if clicked else "   (missing or grayed — dump may be meaningless)"))
    await asyncio.sleep(TAB_SETTLE)

    social = await node_helpers._get_window_from_path(client.root_window, SOCIAL_WINDOW_PATH)
    dump = await _dump_subtree(social) if social is not None else {}
    print(f"    windows recorded: {len(dump)}")
    return dump


def report(snapshots: dict[str, dict[str, dict[str, str]]], group_label: str, recheck_label: str) -> None:
    candidates, unstable = find_candidates(snapshots, group_label, recheck_label)
    others = [label for label in snapshots if label not in (group_label, recheck_label)]

    print("\n" + "=" * 78)
    print("REPORT")
    print("=" * 78)
    print(f"\nCompared the Group tab against {len(others)} other tab(s): {', '.join(others)}")
    print(f"Discarded {len(unstable)} unstable (window, property) pair(s) that moved between")
    print("the two Group samples.")

    # The negative result that prompted this probe, stated explicitly so a future reader
    # does not retry it.
    print("\nThe element that did NOT work, for the record:")
    for path in sorted(p for p in snapshots[group_label] if p.endswith("GroupPageNumber")):
        print(f"\n  {path}")
        for label, dump in snapshots.items():
            shown = dump.get(path, {})
            print(
                f"    {label:<18} is_visible={shown.get('is_visible', ABSENT)}  text={shown.get('maybe_text', ABSENT)}"
            )

    print("\n" + "-" * 78)
    if not others:
        print("\nOnly the Group tab was sampled, so there is nothing to contrast against and")
        print("no conclusion to draw. Check the tab discovery above.")
        print("=" * 78)
        return
    if not candidates:
        print("\nNO usable indicator found. Nothing under SocialSystemsWindow holds a value on")
        print("the Group tab that it does not also hold on some other tab, so the fixed sleep")
        print("in _open_social_window has to stay.")
        print("\nThis matches the 2026-08-11 result, i.e. nothing has changed. Do NOT respond by")
        print("shortening the sleep: it covers the page populating under load, and a misread")
        print("makes the next regroup send spurious invites. read_group_flags already runs the")
        print("survey concurrently, so the sleep costs one second for the fleet, not per client.")
        print("=" * 78)
        return

    print(f"\n{len(candidates)} candidate(s), best first. Each is UNIQUE to the Group tab:\n")
    for path, prop, value in candidates:
        print(f"  {path}")
        print(f"    {prop} = {value}   on the Group tab")
        for label in others:
            print(f"      {label:<18} {snapshots[label].get(path, {}).get(prop, ABSENT)}")
        print()

    best_path, best_prop, best_value = candidates[0]
    print("=" * 78)
    print("USE THIS:")
    print(f"  path:     {best_path}")
    print(f"  property: {best_prop}")
    print(f"  value on the Group tab: {best_value}")
    if best_prop not in ("is_visible", "is_control_grayed", "maybe_checked"):
        print("\n  NOTE: this is not a boolean property, so node_helpers has no ready-made")
        print("  predicate for it. Prefer a boolean candidate from the list above if there is")
        print("  one; otherwise a new helper is needed.")
    print("\nConfirm by re-running before building anything on it: a value that is stable")
    print("within one sweep can still differ between sessions or after a game patch.")
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
            if not await node_helpers.is_visible_by_path(client, SOCIAL_WINDOW_PATH):
                await node_helpers.click_by_path(client, SOCIAL_BUTTON_PATH)
                opened = await node_helpers.poll_until(
                    lambda: node_helpers.is_visible_by_path(client, SOCIAL_WINDOW_PATH),
                    timeout=TRIGGER_TIMEOUT,
                    poll_interval=POLL_INTERVAL,
                )
                if not opened:
                    print("The social window did not open.")
                    return 1

            tabs = await _tab_names(client)
            if not tabs:
                print(f"No tab buttons found under {'/'.join(TAB_LAYOUT_PATH)}.")
                return 1
            print(f"Tabs found: {', '.join(tabs)}")
            if GROUP_TAB_NAME not in tabs:
                print(f"{GROUP_TAB_NAME} is not among them — SOCIAL_GROUP_TAB_PATH may be stale.")
                return 1

            # Group first, then every other tab, then Group again. The two Group samples
            # bracket the sweep, so anything drifting over its whole duration is caught.
            snapshots = {"Tab_Group": await _snapshot(client, GROUP_TAB_NAME, "Tab_Group")}
            for tab in tabs:
                if tab != GROUP_TAB_NAME:
                    snapshots[tab] = await _snapshot(client, tab, tab)
            snapshots["Tab_Group (recheck)"] = await _snapshot(client, GROUP_TAB_NAME, "Tab_Group (recheck)")

        report(snapshots, "Tab_Group", "Tab_Group (recheck)")

        await node_helpers.click_by_path(client, GROUP_WINDOW_CLOSE_PATH)
        return 0
    finally:
        for c in clients:
            try:
                await c.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
