"""Diagnostic: how to tell an EQUIPPED castle from an unequipped one, and how to find a
named castle again after the list resorts.

handle_potions wants to temporarily unequip a non-dorm primary residence so that
GotoDormButton lands in the dorm on the first press. Today a client whose home is a castle
lands in that castle instead, and the node has to wait out COMPASS_TELEPORT_TIMEOUT (35 s)
for the teleport cooldown before it can press HOME a second time. Unequipping first turns
that wait into two clicks.

There are two unknowns, and one probe run should settle both.

QUESTION 1 — which property means "equipped"?

Equip_Item is a TOGGLE with no readable label state. Two situations look identical from the
outside:

    * a player with a castle equipped        -> we want to click Equip_Item (unequips)
    * a player with castles, none equipped   -> clicking Equip_Item EQUIPS one

The second is a silent, unwanted change to somebody's account, and it is strictly worse than
the 35 s wait we are trying to remove. So the flow cannot be built until some property
distinguishes the two. The leading candidate is the `fist` icon on the row
(Item_1/fist), but whether it is absent, hidden, or merely grayed when nothing is equipped
is unknown, and "the window exists" is NOT the same as "the icon is showing" — is_visible()
reports a window's own flag and ignores its parents, so a child of a hidden row still reads
visible. That hazard is what made probe_group_tab.py necessary and it applies here too.

QUESTION 2 — where is a castle's NAME, and how many rows are there?

The equipped castle is always the first row, but only while the window stays open; closing
and reopening it resorts the list alphabetically. handle_potions must close the window to
teleport, so on the way back the castle it unequipped is no longer Item_1 and nothing is
equipped to mark it. Re-equipping by position would equip whichever castle happens to sort
first — again a silent change to somebody's account. So the flow has to cache the name on
the way out and search for it on the way back, which needs to know where a row's name label
lives, how many rows the page shows at once, and whether a long list pages or scrolls.

This is why the dump covers the whole FurnitureSpellbookPage rather than just Item_1: the
row labels and any paging controls are the answer to question 2.

WHAT THIS DOES

Read-only by default: opens the housing page, selects the Castles tab, and dumps every
property of every window on the page. Run it once with a castle equipped and once with none
equipped, and compare the two reports.

With --toggle it does the comparison itself in one run: dump, click Equip_Item, dump again,
then click it a second time to put the castle back. That is the more reliable comparison
because it removes any doubt about the two runs being otherwise identical, but it does
change live account state twice, so it is opt-in and it verifies the restore. The window is
never closed between the two clicks, so the row order cannot resort underneath it and
Item_1 is still the same castle when it is put back.

HOW TO RUN
    1. Log a wizard in that HAS at least one castle. Only one client is inspected.
    2. uv run python tools/probe_housing_equip.py                 # read-only
       uv run python tools/probe_housing_equip.py --toggle        # equip/unequip cycle
    3. Paste the report back.

    Run it on a wizard with no castle equipped too — that is the case the whole probe
    exists for, and it is the one a --toggle run cannot safely produce on its own.

RESULT (2026-08-13): both questions answered, from two read-only runs on one wizard — one
with a castle equipped, one with none.

  Q1: Item_N/fist is_visible is the indicator. True on the equipped row; False on every
      other row, and False on every row when nothing is equipped.

      is_control_grayed and maybe_checked on that same icon ALSO differed between the two
      runs, and both are traps. The full-page dump is what caught them: unrelated hidden
      windows (crafted2, crowns0, pvp0, lock0) flipped their grayed values between two
      otherwise identical read-only runs, so those properties are uninitialized memory on
      hidden windows rather than state. Had this probe dumped only Item_1, there would have
      been three plausible-looking candidates and no way to tell them apart.

  Q2: the castle's name is the ROW window's own text (Item_1 reads 'Arcanum Apartment');
      there is no separate label child. Empty slots read ''. The page shows 8 rows, with
      leftscroll/rightscroll for paging (both hidden when everything fits).

Because a row can be found by its fist and found again by its name, bartlebot/nodes/housing.py
never depends on row order at all, which makes the close-and-reopen alphabetical resort
irrelevant. Re-run this after a game update if that module starts misbehaving.

Opens and closes the housing page. With --toggle, equips/unequips the first castle twice.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from wizwalker import Keycode
from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot.nodes import node_helpers
from bartlebot.nodes.paths import (
    HOUSING_CASTLES_TAB_PATH,
    HOUSING_EQUIP_BUTTON_PATH,
    HOUSING_PAGE_PATH,
    housing_castle_equipped_icon_path,
    housing_castle_row_path,
)
from bartlebot.nodes.timing import POLL_INTERVAL, TRIGGER_TIMEOUT

# The whole Castles page is dumped, not just the row of interest: question 2 needs the other
# Item_N rows (to see where names live and how many are shown) and any paging controls.
HOUSING_FIRST_CASTLE_PATH = housing_castle_row_path(1)
HOUSING_FIRST_CASTLE_EQUIPPED_ICON_PATH = housing_castle_equipped_icon_path(1)

# Depth limit for the subtree walk. Rows are shallow, but the button sits three levels down
# under windowForBtns/Layout, so this covers the page comfortably.
MAX_DEPTH = 6

# Pause after a click that changes equipped state, before reading. Generous on purpose —
# a value read mid-animation is exactly the noise this probe must not report as signal.
_EQUIP_SETTLE = 1.5

# Properties recorded for every window. Breadth is the point: `is_visible` may well be
# useless here (see the module docstring), so the answer is likely one of the others.
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

# Properties a caller could actually branch on with an existing node_helpers predicate.
_BOOLEAN_PROPS = ("is_visible", "is_control_grayed", "maybe_checked")


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
            # Many of these are type-specific (maybe_checked on a non-checkbox). Record the
            # failure rather than dropping it: a getter that raises in one state and
            # succeeds in the other is itself a discriminator.
            out[prop] = f"<{type(exc).__name__}>"
    return out


async def _dump_subtree(window, prefix: str, depth: int = 0) -> dict[str, dict[str, str]]:
    """Return {path_string: {property: repr}} for `window` and everything under it."""
    out: dict[str, dict[str, str]] = {prefix: await _read_properties(window)}
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
        key = f"{prefix}/{name}"
        unique = key
        n = 2
        while unique in out:
            unique = f"{key}#{n}"
            n += 1
        out.update(await _dump_subtree(child, unique, depth + 1))
    return out


async def _snapshot(client, label: str) -> dict[str, dict[str, str]]:
    """Dump the whole Castles page, keyed by path string."""
    window = await node_helpers._get_window_from_path(client.root_window, HOUSING_PAGE_PATH)
    if window is None:
        print(f"    {label}: FurnitureSpellbookPage not found")
        return {}
    out = await _dump_subtree(window, "FurnitureSpellbookPage")
    print(f"    {label}: {len(out)} window(s) recorded")
    return out


def describe_rows(dump: dict[str, dict[str, str]]) -> list[tuple[str, str]]:
    """Return (row_path, name_text) for every Item_N row that carries readable text.

    Answers question 2: a row whose own maybe_text is empty but which has a child holding
    the castle name tells the caller exactly what to read when searching the resorted list.
    Rows with no text anywhere mean names are not readable from the tree at all, and the
    unequip flow cannot restore reliably.

    Pure so it can be tested without a game client.
    """
    found: list[tuple[str, str]] = []
    for path in sorted(dump):
        parts = path.split("/")
        # Only consider the row itself and its descendants, not the page furniture.
        if not any(part.startswith("Item_") for part in parts):
            continue
        text = dump[path].get("maybe_text", ABSENT)
        if text in (ABSENT, "''", '""', "None", "'None'"):
            continue
        found.append((path, text))
    return found


def diff_states(before: dict[str, dict[str, str]], after: dict[str, dict[str, str]]) -> list[tuple[str, str, str, str]]:
    """Return (path, property, before, after) for every value that changed.

    A property missing from one side is reported as ABSENT rather than skipped: "the window
    is not there at all when unequipped" is a perfectly good discriminator, and quietly
    dropping it would hide the most useful possible answer.

    Boolean properties sort first — those are the ones node_helpers already has predicates
    for, so a bool beats a window_rectangle that merely happens to differ. Among the
    booleans, _BOOLEAN_PROPS order wins rather than alphabetical order: is_visible has a
    ready-made helper and an obvious meaning, and recommending is_control_grayed over it
    just because the letter c sorts first would be a worse answer for no reason.

    Pure so it can be tested without a game client; the rest of the probe cannot be.
    """
    paths = set(before) | set(after)
    changed: list[tuple[str, str, str, str]] = []
    for path in paths:
        props = set(before.get(path, {})) | set(after.get(path, {}))
        for prop in props:
            b = before.get(path, {}).get(prop, ABSENT)
            a = after.get(path, {}).get(prop, ABSENT)
            if b != a:
                changed.append((path, prop, b, a))

    def rank(item: tuple[str, str, str, str]) -> tuple[int, int, str, str]:
        path, prop, _, _ = item
        # The fist icon is the hypothesis under test, so surface it above everything else.
        tier = _BOOLEAN_PROPS.index(prop) if prop in _BOOLEAN_PROPS else len(_BOOLEAN_PROPS)
        return (0 if path.endswith("/fist") else 1, tier, path, prop)

    return sorted(changed, key=rank)


async def _open_housing_page(client, machine_log=print) -> bool:
    """Press U until the housing page is showing. Returns False if it never appears."""
    for attempt in range(1, 4):
        if await node_helpers.is_visible_by_path(client, HOUSING_CASTLES_TAB_PATH):
            return True
        machine_log(f"  pressing U (attempt {attempt}/3)...")
        await client.send_key(Keycode.U, 0.1)
        if await node_helpers.poll_until(
            lambda: node_helpers.is_visible_by_path(client, HOUSING_CASTLES_TAB_PATH),
            timeout=TRIGGER_TIMEOUT,
            poll_interval=POLL_INTERVAL,
        ):
            return True
        # Something else owns the spellbook; back out and try again.
        await client.send_key(Keycode.ESC, 0.1)
        await asyncio.sleep(POLL_INTERVAL)
    return False


async def _select_castles_tab(client) -> bool:
    """Make sure the Castles tab is showing. Returns False if Equip_Item never appears."""
    if await node_helpers.is_visible_by_path(client, HOUSING_EQUIP_BUTTON_PATH):
        return True
    await node_helpers.click_by_path(client, HOUSING_CASTLES_TAB_PATH)
    return await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, HOUSING_EQUIP_BUTTON_PATH),
        timeout=TRIGGER_TIMEOUT,
        poll_interval=POLL_INTERVAL,
    )


async def _select_first_castle(client) -> bool:
    """Click Item_1 and wait for Equip_Item to become enabled."""
    await node_helpers.click_by_path(client, HOUSING_FIRST_CASTLE_PATH)
    return await node_helpers.poll_until(
        lambda: node_helpers.is_enabled_by_path(client, HOUSING_EQUIP_BUTTON_PATH),
        timeout=TRIGGER_TIMEOUT,
        poll_interval=POLL_INTERVAL,
    )


def report(before: dict, after: dict | None, restored: dict | None) -> None:
    print("\n" + "=" * 78)
    print("REPORT")
    print("=" * 78)

    # Printed for every run, not just read-only ones: the paging controls question 2 asks
    # about are only answerable from the full page listing, and a --toggle run needs it too.
    print("\nEvery window on the page:")
    for path in sorted(before):
        props = before[path]
        print(
            f"  {path:<52} visible={props.get('is_visible', ABSENT):<7} "
            f"grayed={props.get('is_control_grayed', ABSENT):<7} text={props.get('maybe_text', ABSENT)}"
        )

    print("\n--- QUESTION 2: where do castle names live? ---")
    rows = describe_rows(before)
    if not rows:
        print("\nNo Item_N row carries readable text anywhere in its subtree.")
        print("If names are not readable, the flow cannot find the castle again after the")
        print("list resorts, and re-equipping by position would equip the wrong castle.")
        print("That rules out the optimisation as designed — say so rather than guessing.")
    else:
        print(f"\n{len(rows)} row window(s) with text — the name label is the repeating one:\n")
        for path, text in rows:
            print(f"  {path:<52} {text}")
    row_count = len({p.split("/")[1] for p in before if p.startswith("FurnitureSpellbookPage/Item_")})
    print(f"\nItem_N rows present on the page: {row_count}")
    print("If that is fewer than the castles this wizard owns, the list pages or scrolls and")
    print("the search has to drive it. Look for scroll/page controls in the full dump above.")

    print("\n--- QUESTION 1: which property means 'equipped'? ---")
    print("\nItem_1/fist, as read in the CURRENT state:")
    # Derived from the constant rather than written out, so a path change cannot leave the
    # report silently looking up a key the dump no longer contains.
    fist_key = "/".join(["FurnitureSpellbookPage", *HOUSING_FIRST_CASTLE_EQUIPPED_ICON_PATH[len(HOUSING_PAGE_PATH) :]])
    fist = before.get(fist_key)
    if fist is None:
        print(f"  {fist_key} does not exist in the tree at all.")
        print("  If this run had a castle EQUIPPED, then HOUSING_FIRST_CASTLE_EQUIPPED_ICON_PATH")
        print("  is wrong and the whole approach needs a different indicator.")
    else:
        for prop in _BOOLEAN_PROPS:
            print(f"  {prop:<18} {fist.get(prop, ABSENT)}")
        print(f"  {'alpha':<18} {fist.get('alpha', ABSENT)}")

    if after is None:
        print("\nRead-only run — no comparison was made.")
        print("Run this again on a wizard in the OPPOSITE state (or pass --toggle) and")
        print("compare the two reports. What matters is whether any property above differs.")
        print("=" * 78)
        return

    changed = diff_states(before, after)
    print(f"\n{len(changed)} propert(ies) changed when Equip_Item was clicked.\n")
    if not changed:
        print("NOTHING changed. Equipped state is not readable from this subtree, so the")
        print("unequip optimisation cannot be built safely — a client with castles but none")
        print("equipped is indistinguishable from one with a castle equipped, and the flow")
        print("would equip a castle for somebody who wants none. Leave handle_potions using")
        print("the two-press HOME path.")
        print("=" * 78)
        return

    for path, prop, b, a in changed:
        marker = "  <-- the hypothesis" if path.endswith("/fist") and prop in _BOOLEAN_PROPS else ""
        print(f"  {path}")
        print(f"    {prop}: {b}  ->  {a}{marker}")

    best = changed[0]
    print("\n" + "-" * 78)
    print("USE THIS:")
    print(f"  path:     {best[0]}")
    print(f"  property: {best[1]}")
    print(f"  equipped state value: {best[2]}   (assuming this run STARTED equipped)")
    if best[1] not in _BOOLEAN_PROPS:
        print("\n  NOTE: not a boolean property, so node_helpers has no ready-made predicate.")
        print("  Prefer a boolean candidate from the list above if there is one.")

    print("\nWhich value means 'equipped' depends on which state this run started in, and the")
    print("probe cannot know that. Confirm against a wizard with NO castle equipped before")
    print("building on it — that is the case the optimisation must not get wrong.")

    if restored is not None:
        drift = diff_states(before, restored)
        if drift:
            print("\n" + "!" * 78)
            print("RESTORE FAILED: the account is NOT in the state this probe found it in.")
            print("Check the housing page by hand and re-equip the intended castle.")
            for path, prop, b, a in drift:
                print(f"  {path}  {prop}: was {b}, now {a}")
            print("!" * 78)
        else:
            print("\nRestore verified: the first castle is back in its original state.")
    print("=" * 78)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client", type=int, default=1, help="1-based client to inspect (default: 1)")
    parser.add_argument(
        "--toggle",
        action="store_true",
        help="equip/unequip the first castle to compare both states, then put it back",
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

    if not 1 <= args.client <= len(clients):
        print(f"--client must be between 1 and {len(clients)}.")
        return 1
    client = clients[args.client - 1]

    try:
        async with client.mouse_handler:
            print("Opening the housing page...")
            if not await _open_housing_page(client):
                print(f"The housing page never appeared at {'/'.join(HOUSING_CASTLES_TAB_PATH)}.")
                print("Either the path is stale or another window would not close.")
                return 1

            print("Selecting the Castles tab...")
            if not await _select_castles_tab(client):
                print(f"Equip_Item never appeared at {'/'.join(HOUSING_EQUIP_BUTTON_PATH)}.")
                return 1

            if not await node_helpers.is_visible_by_path(client, HOUSING_FIRST_CASTLE_PATH):
                print("Item_1 is not present — this wizard appears to own no castles.")
                print("Re-run on a wizard that has at least one; there is nothing to compare here.")
                return 1

            before = await _snapshot(client, "current state")

            after = restored = None
            if args.toggle:
                print("\nSelecting the first castle...")
                if not await _select_first_castle(client):
                    print("Equip_Item never became enabled after clicking Item_1.")
                    print("Nothing was changed.")
                    report(before, None, None)
                    return 1

                print("Clicking Equip_Item (this CHANGES account state)...")
                await node_helpers.click_by_path(client, HOUSING_EQUIP_BUTTON_PATH)
                await asyncio.sleep(_EQUIP_SETTLE)
                after = await _snapshot(client, "after toggle")

                print("Clicking Equip_Item again to restore...")
                await _select_first_castle(client)
                await node_helpers.click_by_path(client, HOUSING_EQUIP_BUTTON_PATH)
                await asyncio.sleep(_EQUIP_SETTLE)
                restored = await _snapshot(client, "after restore")

            await client.send_key(Keycode.ESC, 0.1)

        report(before, after, restored)
        return 0
    finally:
        for c in clients:
            try:
                await c.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
