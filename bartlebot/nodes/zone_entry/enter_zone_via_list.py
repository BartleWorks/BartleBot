"""Use a zone-selection list object -- a Streamportal, a Nanavator, a World Gate -- and teleport.

Not a node in the library: it is how a planned ``list`` or ``gate`` step is run
(``nodes/zone_navigation.step_call``), the World Gate included. Callers
hand over a row already resolved -- the text to match in every locale it might read, and where
to stand -- because only they know what the row is: ``zone_lists.resolve`` for a zone list,
``worlds.display_names`` for a gate, whose rows are worlds rather than zones.

Sync In Zone is what a bot author reaches for instead. A list object only ever stands in a zone
the navigation map declares, so there is nothing this could be pointed at that Sync In Zone
cannot already plan a route to -- which is why it was retired as a node (#36).
"""

import asyncio
import difflib
import logging
from collections.abc import Callable

from wizwalker import XYZ

from bartlebot.clients import game_keys
from bartlebot.game import dialog_captions
from bartlebot.game.key_bindings import GameAction
from bartlebot.nodes import node_helpers
from bartlebot.nodes.paths import (
    UNIVERSE_CANCEL_BUTTON,
    UNIVERSE_NEXT_PAGE,
    UNIVERSE_TELEPORT_BUTTON,
    universe_zone_option_path,
)
from bartlebot.nodes.timing import MAX_RETRIES, POLL_INTERVAL, ZONE_CHANGE_TIMEOUT

_MAX_PAGES = 20  # Safety cap — must exceed the total page count of any zone list
_FUZZY_THRESHOLD = 0.75  # Minimum SequenceMatcher ratio for a fuzzy match


def _fuzzy_match(text: str, queries_lower: list[str]) -> bool:
    """Return True if text is a close enough match for any of queries_lower (already lowercased).

    Checks substring containment first (fast path), then falls back to
    SequenceMatcher ratio. This handles display names that may differ slightly
    from the underlying zone name used to derive the query.

    Several queries rather than one because the same entry has a different display text
    per game locale, and the node cannot tell which locale a client is running — so every
    known spelling is tried and any hit wins.
    """
    t = text.strip().lower()
    if not t:
        return False
    for query_lower in queries_lower:
        if not query_lower:
            continue
        if query_lower in t:
            return True
        if difflib.SequenceMatcher(None, t, query_lower).ratio() >= _FUZZY_THRESHOLD:
            return True
    return False


def _queries(entry: str, aliases: list[str] | None) -> list[str]:
    """The lowercased texts to match a list row against, deduped, primary entry first."""
    candidates = [entry, *(aliases or [])]
    return list(dict.fromkeys(q for c in candidates if (q := (c or "").strip().lower())))


async def _search_pages(client, safety_delay: float, match: Callable[[str], bool]) -> bool:
    """Walk pages from wherever the list currently sits, clicking the first entry `match` accepts.

    Returns True if an entry was found and clicked, False if all pages were exhausted —
    in which case the list is left on the page it started on, not wherever the walk ended.

    Page navigation uses a visited-signature set to detect when the list has wrapped
    around (all pages seen), since the prev/next buttons never become disabled — they
    always cycle through the list circularly.
    """
    visited: set[tuple[str, ...]] = set()

    async def read_texts() -> list[str]:
        return [await node_helpers.get_text_by_path(client, universe_zone_option_path(i)) for i in range(4)]

    async def page_changed(s: list[str]) -> bool:
        return await read_texts() != s

    for _ in range(_MAX_PAGES):
        texts = await read_texts()
        sig = tuple(texts)

        # If we've seen this page before the list has wrapped — all pages exhausted.
        if sig in visited:
            return False
        visited.add(sig)

        for i, text in enumerate(texts):
            if match(text):
                await node_helpers.click_by_path(client, universe_zone_option_path(i))
                return True

        snapshot = texts
        await node_helpers.click_by_path(client, UNIVERSE_NEXT_PAGE)
        await node_helpers.poll_until(lambda s=snapshot: page_changed(s), safety_delay, POLL_INTERVAL)

    return False


def _substring_match(text: str, queries_lower: list[str]) -> bool:
    """Case-insensitive containment only — no ratio fallback. See `_select_zone`."""
    t = text.strip().lower()
    return bool(t) and any(query_lower and query_lower in t for query_lower in queries_lower)


async def _select_zone(client, entry: str, safety_delay: float, aliases: list[str] | None = None) -> bool:
    """Navigate the zone list UI on a single client to find and click the matching entry.

    Returns True if the entry was found and clicked, False if all pages were exhausted.
    Assumes the zone list UI is already open.

    Two passes, substring first and fuzzy second, rather than one pass trying both: a
    fuzzy ratio match on an early page must never pre-empt a real substring match sitting
    on a later one. Paired qualifiers collide often enough for this to matter — "Inner
    Athanor" against "Outer Athanor" scores 0.77 on `_fuzzy_match`'s own 0.75 threshold in
    English, 0.94 in Greek, so no threshold separates a genuine near-duplicate spelling
    from a real, different zone that merely shares most of its letters. The first pass
    alone already resolves any list where the correct entry appears verbatim (or as an
    exact alias) anywhere on it, which is every list this node has been pointed at so far;
    the second pass exists only for a display name closer than a raw substring check
    catches — a stripped accent, a differently quoted apostrophe.
    """
    queries = _queries(entry, aliases)
    if await _search_pages(client, safety_delay, lambda text: _substring_match(text, queries)):
        return True

    def fuzzy(text: str) -> bool:
        logging.debug(f"No exact match; fuzzy-searching for any of {queries!r} in {text!r} (client={client})")
        return _fuzzy_match(text, queries)

    return await _search_pages(client, safety_delay, fuzzy)


async def _confirm_arrival(machine, clients: list, to_zone: str) -> str:
    """Wait for every client to reach to_zone. The tail both modes share."""
    actual = await node_helpers.wait_for_zone_with_recovery(machine, clients, to_zone, ZONE_CHANGE_TIMEOUT)
    if actual is not None:
        machine.log(
            f"FAILURE: zone change timed out (expected={to_zone!r}, actual={actual})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"enter_zone_via_list: all clients in {to_zone!r} — SUCCESS")
    return "SUCCESS"


async def _use_list_sequentially(
    machine,
    clients: list,
    interact,
    reset,
    zone_list_entry: str,
    safety_delay: float,
    aliases: list[str],
    object_use_delay: float | None,
) -> str | None:
    """Let each client use the list object alone. Returns "FAILURE", or None to carry on.

    A client's turn runs from its keypress to its Teleport click: the list stays open on
    screen for the whole of the page search, so unlike the other interaction nodes there is
    no earlier moment at which the object is free. Answering the instance prompt is part of
    the turn too — until it is answered the teleport never fires, so a client released
    before that has not actually left.
    """
    machine.log("Sequential interaction: clients take turns at the list object", logging.DEBUG)
    refusals: list[str] = []

    async def _turn(client) -> bool:
        await game_keys.press(client, GameAction.INTERACT, 0.1)

        if not await node_helpers.poll_until(
            lambda: node_helpers.is_visible_by_path(client, UNIVERSE_TELEPORT_BUTTON), safety_delay, POLL_INTERVAL
        ):
            return False

        if not await _select_zone(client, zone_list_entry, safety_delay, aliases):
            # Leave the object as we found it, or the next client's turn starts with
            # somebody else's list still on screen.
            await node_helpers.click_by_path(client, UNIVERSE_CANCEL_BUTTON)
            return False

        await node_helpers.click_by_path(client, UNIVERSE_TELEPORT_BUTTON)
        if refusal := await node_helpers.answer_instance_prompts([client], safety_delay, POLL_INTERVAL):
            refusals.append(refusal)
            return False
        return True

    numbers = list(range(1, len(clients) + 1))
    results = await node_helpers.take_turns(
        clients,
        interact,
        reset,
        safety_delay,
        _turn,
        numbers=numbers,
        poll_interval=POLL_INTERVAL,
        object_use_delay=object_use_delay,
        # The teleport takes the client out of the zone itself; parking it would aim a
        # teleport at a client that is already leaving.
        park_after_turn=False,
        log=machine.log,
    )
    unprompted, unfinished = node_helpers.summarize_turns(results, numbers)

    # One player's failed turn is not the group's: whoever teleported is already on its way,
    # and the arrival check is what decides what happens to the rest.
    behind = sorted(set(unprompted) | set(unfinished))
    if behind and len(behind) < len(clients):
        machine.log(
            f"Client(s) {behind} did not use the list; continuing to the arrival check",
            logging.INFO,
        )
        return None

    if refusals:
        machine.log(
            f"FAILURE: the game refused the teleport — {dialog_captions.describe(refusals[0])}",
            logging.WARNING,
        )
        return "FAILURE"
    if unprompted:
        machine.log(
            f"FAILURE: interact prompt never appeared for client(s) {unprompted} after {MAX_RETRIES} attempts",
            logging.WARNING,
        )
        return "FAILURE"
    if unfinished:
        machine.log(
            f"FAILURE: client(s) {unfinished} could not open the zone list or select {zone_list_entry!r}",
            logging.WARNING,
        )
        return "FAILURE"
    return None


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.is_in_npc_range(),
          game_keys.press(client, GameAction.INTERACT), node_helpers.is_visible_by_path(),
          node_helpers.get_text_by_path(), node_helpers.click_by_path()

    Params are whatever the caller resolved: the two zones, the object's coordinates, and
    zone_list_entry plus zone_list_aliases -- every text the row might read, the first of them
    tried first.

    Flow:
      1. Verify all clients are in zone. Return FAILURE if not.
      2s. If sequential, _use_list_sequentially runs one client's whole turn — open the list,
         find the entry, click Teleport, answer the prompt — before the next client starts,
         replacing steps 2–7b. Step 8 runs either way.
      2. Clear any pre-existing interact prompt by teleporting to reset_xyz.
      3. Retry loop (MAX_RETRIES): teleport to interact_xyz, wait for the interact
         prompt. If it does not appear, teleport to reset_xyz and retry.
         Return FAILURE if the prompt never appeared.
      4. Press X on all clients to open the zone list.
      5. Wait for the zone list UI to open (UNIVERSE_TELEPORT_BUTTON visible on all
         clients). Return FAILURE if it never appears within Safety Delay.
      6. For each client in parallel: navigate pages searching for zone_list_entry, a
         substring pass over every page before a fuzzy pass is ever allowed to (see
         _select_zone). After each failed page, click Next and wait for the page to
         change. A visited-signature set detects when all pages have been cycled through
         (the list wraps circularly — Next/Prev are never disabled). If not found on
         any page, click cancel on all clients and return FAILURE.
      7. Click the teleport button on all clients, then confirm the dungeon exit warning
         if the list was used from inside a dungeon.
      8. Wait for all clients to reach to_zone within ZONE_CHANGE_TIMEOUT.
         Return FAILURE if timed out.
      9. Return SUCCESS.

    FAILURE: clients not in zone; interact prompt never appeared after MAX_RETRIES; zone list
    UI never opened; the row not found on any page; zone change timed out.
    """
    zone: str = params["zone"]
    to_zone: str = params["to_zone"]
    interact = XYZ(**params["interact_xyz"])
    reset = XYZ(**params["reset_xyz"])
    zone_list_entry: str = params["zone_list_entry"]
    aliases: list[str] = params.get("zone_list_aliases") or []
    safety_delay: float = machine.get_option("Safety Delay", 3.0)

    machine.log(
        f"enter_zone_via_list: {zone!r} → {to_zone!r}, entry={zone_list_entry!r}"
        + (f" (or any of {aliases!r})" if aliases else "")
    )

    # 1. Zone check.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if not all(node_helpers.zone_matches(z, zone) for z in zones):
        wrong = [z for z in zones if not node_helpers.zone_matches(z, zone)]
        machine.log(
            f"FAILURE: clients not in expected zone (expected={zone!r}, unexpected={wrong})",
            logging.WARNING,
        )
        return "FAILURE"

    # Who shares whose realm, for straggler recovery in a Separate bot.
    await node_helpers.note_realm_peers(machine, clients)

    # 2s. Sequential mode replaces steps 2–7b; step 8 runs either way.
    if params.get("sequential", False):
        failure = await _use_list_sequentially(
            machine, clients, interact, reset, zone_list_entry, safety_delay, aliases, params.get("object_use_delay")
        )
        if failure is not None:
            return failure
        return await _confirm_arrival(machine, clients, to_zone)

    # 2–3. Clear a stale prompt, then teleport to interact_xyz until the prompt shows.
    if not await node_helpers.reach_prompt(
        machine, clients, interact, reset, node_helpers.INTERACT_PROMPT, safety_delay, POLL_INTERVAL
    ):
        return "FAILURE"

    # 4. Press X to open the zone list.
    machine.log("Pressing X to open zone list", logging.DEBUG)
    await asyncio.gather(*[game_keys.press(c, GameAction.INTERACT, 0.1) for c in clients])

    # 5. Wait for zone list UI to open.
    async def _list_open() -> bool:
        results = await asyncio.gather(*[node_helpers.is_visible_by_path(c, UNIVERSE_TELEPORT_BUTTON) for c in clients])
        return all(results)

    if not await node_helpers.poll_until(_list_open, safety_delay, POLL_INTERVAL):
        machine.log("FAILURE: zone list UI never opened after pressing X", logging.WARNING)
        return "FAILURE"

    machine.log(f"Zone list open; searching for {zone_list_entry!r}", logging.DEBUG)

    # 6. Each client navigates to and selects the matching entry.
    found = await asyncio.gather(*[_select_zone(c, zone_list_entry, safety_delay, aliases) for c in clients])
    if not all(found):
        machine.log(
            f"FAILURE: zone list entry {zone_list_entry!r} not found on any page",
            logging.WARNING,
        )
        await asyncio.gather(*[node_helpers.click_by_path(c, UNIVERSE_CANCEL_BUTTON) for c in clients])
        return "FAILURE"

    machine.log(f"Entry {zone_list_entry!r} selected; clicking teleport button", logging.DEBUG)

    # 7. Click the teleport button.
    await asyncio.gather(*[node_helpers.click_by_path(c, UNIVERSE_TELEPORT_BUTTON) for c in clients])

    # 7b. Confirm the dungeon exit warning if the list was used from inside a dungeon —
    #     until it is answered the teleport never fires and step 8 times out. Blocking
    #     here is safe because what follows waits on the arrival zone, which is sticky.
    machine.log("Checking for an instance prompt…", logging.DEBUG)
    if refusal := await node_helpers.answer_instance_prompts(clients, safety_delay, POLL_INTERVAL):
        machine.log(
            f"FAILURE: the game refused the teleport — {dialog_captions.describe(refusal)}",
            logging.WARNING,
        )
        return "FAILURE"

    # 8. Wait for zone change.
    return await _confirm_arrival(machine, clients, to_zone)
