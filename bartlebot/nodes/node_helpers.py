"""Shared async helpers for node implementations."""

import asyncio
import fnmatch
import logging
import re

from wizwalker import Keycode, MemoryReadError

logger = logging.getLogger(__name__)

from bartlebot.nodes.paths import (  # noqa: F401 — re-exported for callers
    ADVANCE_DIALOG_PATH,
    DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH,
    FRIENDS_LIST_WINDOW_NAME,
    FRIENDS_LIST_WINDOW_NAMES,
    GAUNTLET_ENTRANCE_PATH,
    LARGE_FRIENDS_LIST_WINDOW_NAME,
    MESSAGE_BOX_CENTER,
)
from bartlebot.nodes.timing import (  # noqa: F401 — re-exported for callers
    DIALOG_FAST_POLL_INTERVAL,
    DIALOG_TIMEOUT,
    POLL_INTERVAL,
    WINDOW_CLOSE_RETRY_INTERVAL,
    WINDOW_CLOSE_TIMEOUT,
)


async def all_in_npc_range(clients: list) -> bool:
    """Return True when every client has the interact prompt showing."""
    results = await asyncio.gather(*[c.is_in_npc_range() for c in clients])
    return all(results)


async def none_in_npc_range(clients: list) -> bool:
    """Return True when no client has an interact prompt showing."""
    results = await asyncio.gather(*[c.is_in_npc_range() for c in clients])
    return not any(results)


async def all_at_dungeon_entrance(clients: list, path: list[str] = DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH) -> bool:
    """Return True when every client has the dungeon entrance prompt showing.

    Pass GAUNTLET_ENTRANCE_PATH for gauntlets, which lack a TeamUpButton.
    """
    results = await asyncio.gather(*[is_visible_by_path(c, path) for c in clients])
    return all(results)


async def none_at_dungeon_entrance(clients: list, path: list[str] = DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH) -> bool:
    """Return True when no client has the dungeon entrance prompt showing.

    Pass GAUNTLET_ENTRANCE_PATH for gauntlets, which lack a TeamUpButton.
    """
    results = await asyncio.gather(*[is_visible_by_path(c, path) for c in clients])
    return not any(results)


async def dismiss_ambient_dialog_once(client) -> bool:
    """If client has an advanceable dialog open, press SPACEBAR to dismiss it. Returns True if dismissed."""
    if await is_visible_by_path(client, ADVANCE_DIALOG_PATH):
        await client.send_key(Keycode.SPACEBAR, 0.1)
        return True
    return False


async def advance_dialogue_until_clear(
    clients: list,
    safety_delay: float,
    timeout: float | None,
) -> bool:
    """Advance dialogue on all clients until all are dialogue-free for safety_delay seconds.

    Presses SPACEBAR on any client that has an open dialogue banner, then resets the quiet
    timer. Polls at DIALOG_FAST_POLL_INTERVAL after a dismissal and POLL_INTERVAL during the
    quiet phase (no dialogue detected). Returns True when all clients have been dialogue-free
    for safety_delay seconds. Returns False if timeout expires while clients are still in
    dialogue.
    """
    loop = asyncio.get_event_loop()
    overall_start = loop.time()
    quiet_since = overall_start

    while True:
        now = loop.time()
        dialogs = await asyncio.gather(*[is_visible_by_path(c, ADVANCE_DIALOG_PATH) for c in clients])

        if not any(dialogs) and (now - quiet_since) >= safety_delay:
            return True

        if timeout is not None and (now - overall_start) >= timeout:
            return False

        if any(dialogs):
            await asyncio.gather(*[c.send_key(Keycode.SPACEBAR, 0.1) for c, d in zip(clients, dialogs) if d])
            quiet_since = loop.time()
            await asyncio.sleep(DIALOG_FAST_POLL_INTERVAL)
        else:
            await asyncio.sleep(POLL_INTERVAL)


async def confirm_dungeon_warning_dialog(client, delay: float = 3.0, poll_interval: float = POLL_INTERVAL) -> None:
    """Poll for the dungeon warning dialog; confirm it immediately when it appears.

    Keeps polling for at least `delay` seconds before concluding the dialog will
    not appear. If the dialog appears before `delay` has elapsed, the confirm button
    is clicked immediately and the function returns — no further waiting.
    """
    loop_start = asyncio.get_event_loop().time()
    while True:
        if await click_by_path(client, MESSAGE_BOX_CENTER):
            return
        if asyncio.get_event_loop().time() - loop_start >= delay:
            return
        await asyncio.sleep(poll_interval)


async def diagnose_path(client, path: list[str]) -> str:
    """Walk path step-by-step and return a diagnostic string.

    If a segment is missing, reports the name that was expected, the names of the
    children that actually exist at that level, and how far traversal got.
    If the full path exists, reports whether is_visible() is True or False.
    Intended for DEBUG logging when is_visible_by_path unexpectedly returns False.
    """
    node = client.root_window
    for i, segment in enumerate(path):
        children = await node.children()
        names = [await c.name() for c in children]
        match = next((c for c, n in zip(children, names) if n == segment), None)
        if match is None:
            prefix = "→".join(path[:i]) if i > 0 else "(root)"
            return f"missing {segment!r} under {prefix!r}; actual children: {names}"
        node = match
    visible = await node.is_visible()
    return f"path found, is_visible()={visible}"


async def is_visible_by_path(client, path: list[str]) -> bool:
    """Return True if the UI window at path exists and is currently visible.

    IMPORTANT: a window's is_visible() flag is its OWN flag — it does not account for its
    parents. A button inside a hidden window commonly still reports visible=True, so
    reading a child to infer whether its container is open gives a constant answer.
    Observed directly: wndFriendsList reads visible=False while its btnFriendListClose
    reads visible=True, before and after the friends list is ever opened
    (tools/probe_friends_list.py).

    So: to ask "is this panel open?", read the panel's own path, not a control inside it.
    """
    window = await _get_window_from_path(client.root_window, path)
    if window is None:
        return False
    return await window.is_visible()


async def is_named_window_visible(client, name: str) -> bool:
    """Return True if any window with this name is visible.

    For windows that are located by name rather than by a fixed path — the friends list
    is opened and read as "NewFriendsListWindow", whose parent chain is not recorded.
    """
    try:
        windows = await client.root_window.get_windows_with_name(name)
    except Exception:
        logger.debug("Could not search for window %r", name, exc_info=True)
        return False
    for window in windows:
        if await window.is_visible():
            return True
    return False


async def is_enabled_by_path(client, path: list[str]) -> bool:
    """Return True if the UI window at path exists and is not grayed out (i.e., enabled)."""
    window = await _get_window_from_path(client.root_window, path)
    if window is None:
        return False
    return not await window.is_control_grayed()


async def _get_window_from_path(parent, path: list[str]):
    """Traverse the UI window tree by a sequence of child names.

    Returns the matching Window object, or None if the path cannot be followed.

    Backtracks across same-named siblings: if two children share a name and only the
    second one leads to the rest of the path, the second is returned. This is what makes
    ambiguous names like Wizard101's duplicate `ButtonLayout` windows safe to path
    through, as long as one branch alone completes the path.
    """
    if not path:
        return parent
    for child in await parent.children():
        if await child.name() == path[0]:
            result = await _get_window_from_path(child, path[1:])
            if result is not None:
                return result
    return None


async def poll_until(condition_coro, timeout: float | None, poll_interval: float = 0.25) -> bool:
    """Poll condition_coro every poll_interval until it returns True or timeout expires.

    Always evaluates condition_coro at least once before the timeout clock starts, so a
    condition that is already True never races against a tight deadline.
    """
    if await condition_coro():
        return True

    async def _poll():
        while not await condition_coro():
            await asyncio.sleep(poll_interval)

    try:
        await asyncio.wait_for(_poll(), timeout=timeout)
        return True
    except TimeoutError:
        return False


async def poll_each_until_seen(
    clients: list,
    check_fn,
    action_fn,
    timeout: float,
    poll_interval: float = POLL_INTERVAL,
) -> set[int]:
    """Poll until check_fn has returned True for every client at least once.

    Re-calls action_fn for each client not yet seen on every poll cycle.
    Returns the set of client indices that triggered. If all clients triggered,
    len(result) == len(clients); on timeout, len(result) < len(clients).
    """
    seen: set[int] = set()
    loop_start = asyncio.get_event_loop().time()
    while True:
        results = await asyncio.gather(*[check_fn(c) for c in clients])
        for i, triggered in enumerate(results):
            if triggered:
                seen.add(i)
        if len(seen) == len(clients):
            return seen
        if asyncio.get_event_loop().time() - loop_start > timeout:
            return seen
        stragglers = [c for i, c in enumerate(clients) if i not in seen]
        await asyncio.gather(*[action_fn(c) for c in stragglers])
        await asyncio.sleep(poll_interval)


def has_glob(pattern: str) -> bool:
    """Return True when pattern contains glob metacharacters (* ? [)."""
    return "*" in pattern or "?" in pattern or "[" in pattern


def entity_name_matches(actual: str, pattern: str) -> bool:
    """Return True when an entity's template name matches pattern.

    Two modes, chosen by the pattern itself, mirroring zone_matches():

      * No metacharacters — case-insensitive *substring* match, which is what
        WizSprinter's get_base_entities_with_vague_name() does. "Cyclops" finds
        "Cyclops/CyclopsMinion". Every existing bot file relies on this.
      * With metacharacters — case-insensitive glob against the *whole* name, so
        "*Bandit*" matches anywhere but "Bandit*" only matches names that start
        with it. Anchoring is the point: substring matching alone cannot express
        "starts with", and a pattern that silently meant the same thing either
        way would make the wildcard decorative.

    A malformed glob does not raise: fnmatch treats an unbalanced bracket as a
    literal character, so "[unclosed" simply matches names containing that text
    (anchored, like any other pattern). No fallback is needed.
    """
    if actual is None:
        return False
    if has_glob(pattern):
        return fnmatch.fnmatch(actual.lower(), pattern.lower())
    return pattern.lower() in actual.lower()


async def find_closest_entity_by_name(client, pattern: str):
    """Return the closest entity whose template name matches pattern, or None.

    Plain names delegate to WizSprinter's find_closest_by_vague_name() so the
    long-standing path is untouched; only a pattern carrying metacharacters takes
    the predicate route, which is the same search with our matcher in place of
    WizSprinter's hardcoded substring test.
    """
    if not has_glob(pattern):
        return await client.find_closest_by_vague_name(pattern)

    async def _matches(entity) -> bool:
        # An entity can despawn mid-scan; WizSprinter's own vague-name predicate
        # would propagate that and abandon the whole search, so skip it instead.
        try:
            template = await entity.object_template()
            if template is None:
                return False
            return entity_name_matches(await template.object_name(), pattern)
        except (ValueError, MemoryReadError):
            return False

    return await client.find_closest_by_predicate(_matches)


def zone_matches(actual: str, pattern: str) -> bool:
    """Return True when actual matches pattern.

    If pattern contains glob metacharacters (* ? [), fnmatch is used so callers
    can write patterns like "WizardCity/*" to match any WizardCity sub-zone.
    Plain strings (no metacharacters) fall through to exact equality, preserving
    the previous behaviour for all existing bot files.

    Malformed glob patterns (e.g. "[unclosed") log a warning and fall back to
    exact equality rather than raising.
    """
    if actual is None:
        return False
    if has_glob(pattern):
        try:
            matched = fnmatch.fnmatchcase(actual, pattern)
        except re.error:
            logger.warning("zone_matches: invalid glob pattern %r — falling back to exact match", pattern)
            return actual == pattern
        if matched:
            logger.debug("zone_matches: pattern %r matched actual zone %r (wildcard)", pattern, actual)
        return matched
    return actual == pattern


async def all_left_zone(clients: list, zone: str) -> bool:
    """Return True when every client's zone_name does not match zone.

    zone may be a glob pattern (e.g. "WizardCity/*"); see zone_matches().
    Note: if the pattern is broad enough to encompass the destination zone,
    this helper will never return True until clients leave that zone family too.
    """
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    return all(not zone_matches(z, zone) for z in zones)


async def all_in_zone(clients: list, zone: str) -> bool:
    """Return True when every client's zone_name matches zone.

    zone may be a glob pattern (e.g. "WizardCity/*"); see zone_matches().
    """
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    return all(zone_matches(z, zone) for z in zones)


async def wait_for_zone(
    clients: list, zone: str, timeout: float, poll_interval: float = POLL_INTERVAL
) -> list[str] | None:
    """Poll until all clients are in zone.

    Returns None on success, or the list of actual zone names on timeout.
    """
    if await poll_until(lambda: all_in_zone(clients, zone), timeout, poll_interval):
        return None
    return list(await asyncio.gather(*[c.zone_name() for c in clients]))


async def none_loading(clients: list) -> bool:
    """Return True when no client is on a loading screen."""
    loading = await asyncio.gather(*[c.is_loading() for c in clients])
    return not any(loading)


async def get_text_by_path(client, path: list[str]) -> str:
    """Return the displayed text of the UI window at path, or '' if not found."""
    window = await _get_window_from_path(client.root_window, path)
    if window is None:
        return ""
    return await window.maybe_text() or ""


async def click_until_gone(
    client,
    path: list[str],
    is_open=None,
    timeout: float = WINDOW_CLOSE_TIMEOUT,
    poll_interval: float = WINDOW_CLOSE_RETRY_INTERVAL,
) -> bool:
    """Click the button at `path` until the thing it closes is gone. Returns True once it is.

    A single click is not reliable: another window can sit on top of the button and swallow
    the click, and the game can drop clicks sent during an animation. Retrying until the
    target is actually gone turns "we clicked it" into "it actually closed".

    `is_open` is an async predicate for "the thing is still open". It MUST test the panel
    itself, not the close button, because a control inside a hidden window still reports
    visible=True — polling the button gives a constant answer and guarantees a timeout.
    See is_visible_by_path() for the observed evidence.

    It defaults to the button's own visibility, which is only correct for controls that
    genuinely disappear with their window; pass an explicit predicate otherwise.

    The predicate is evaluated before each click, so an already-closed panel costs one read
    and no clicks — important because these buttons often sit on toggles, where a stray
    click would reopen what was just closed.

    Returns False if the panel is still open when the timeout expires.
    """
    if is_open is None:

        async def is_open():
            return await is_visible_by_path(client, path)

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        if not await is_open():
            return True
        await click_by_path(client, path)
        if loop.time() >= deadline:
            break
        await asyncio.sleep(poll_interval)

    still_open = await is_open()
    if still_open:
        logger.warning("Window closed via %s is still open after %.0fs of retries", "/".join(path), timeout)
    return not still_open


async def click_by_path(client, path: list[str]) -> bool:
    """Click the UI window at path if it exists and is enabled (not grayed out).
    Returns True if found, enabled, and clicked. Returns False if not found or grayed."""
    window = await _get_window_from_path(client.root_window, path)
    if window is None:
        return False
    if await window.is_control_grayed():
        return False
    async with client.mouse_handler:
        await client.mouse_handler.click_window(window)
    return True


# ---------------------------------------------------------------------------
# Friends list
#
# The friends list has no per-row window — rows are one block of markup text in
# `listFriends`, clicked by computing a pixel offset. WizWalker already solves this, so
# these helpers wrap its (private) routines rather than re-deriving the row geometry.
# Depending on underscore-prefixed names across a library boundary is normally a bad
# trade, but both libraries are pinned by exact commit in pyproject.toml, so they cannot
# shift without a deliberate pin bump.
#
# Callers must hold `async with client.mouse_handler` around every function here.
# ---------------------------------------------------------------------------


async def open_friends_list(client):
    """Open the compact friends list if it is not already open, and return its window.

    Opens NewFriendsListWindow specifically. The game's other friends list, wndFriendsList,
    is a separate window reached from this one — see paths.FRIENDS_LIST_WINDOW_NAMES. The
    name/profile code reads the compact list, so this deliberately opens only that one.

    Raises ValueError if the window cannot be found even after clicking the button.
    """
    from wizwalker.extensions.scripting.utils import _maybe_get_named_window  # noqa: PLC0415

    # The large list has to be cleared, not clicked past. Visibility is judged below from
    # NewFriendsListWindow alone, so from the large list this reads "closed" and clicks
    # btnFriends anyway — and what the game does with that click was never established
    # (issue #96). Closing first replaces an unknown with the known-good closed state, at
    # the cost of one extra toggle in a case that only arises when a list was left open by
    # hand.
    if await is_named_window_visible(client, LARGE_FRIENDS_LIST_WINDOW_NAME):
        logger.debug("Large friends list is open; closing it before opening the compact one")
        await close_friends_list(client)

    try:
        friends_window = await _maybe_get_named_window(client.root_window, FRIENDS_LIST_WINDOW_NAME)
        if not await friends_window.is_visible():
            btn = await _maybe_get_named_window(client.root_window, "btnFriends")
            await client.mouse_handler.click_window(btn)
        return friends_window
    except ValueError:
        # The window does not exist at all until the list has been opened once.
        btn = await _maybe_get_named_window(client.root_window, "btnFriends")
        await client.mouse_handler.click_window(btn)
        return await _maybe_get_named_window(client.root_window, FRIENDS_LIST_WINDOW_NAME)


async def press_key_until_gone(
    client,
    key: Keycode,
    is_open,
    timeout: float = WINDOW_CLOSE_TIMEOUT,
    poll_interval: float = WINDOW_CLOSE_RETRY_INTERVAL,
) -> bool:
    """Press `key` until `is_open()` reports the panel has closed. Returns True once gone.

    The keyboard counterpart to click_until_gone, and preferable for panels that have a
    keyboard toggle: a keypress goes to the client as a whole, so unlike a click it cannot
    land on the wrong window — which is the failure mode every close bug here has had.

    `is_open` must test the panel itself; see click_until_gone for why a close button's own
    visibility is useless for this.

    The predicate is checked before each press, because these keys are toggles: pressing
    one on an already-closed panel reopens it.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        if not await is_open():
            return True
        await client.send_key(key, 0.1)
        if loop.time() >= deadline:
            break
        await asyncio.sleep(poll_interval)

    still_open = await is_open()
    if still_open:
        logger.warning("Panel toggled by %s is still open after %.0fs of retries", key, timeout)
    return not still_open


async def is_friends_list_open(client) -> bool:
    """Return True if EITHER friends-list window is showing.

    There are two independent friends-list windows (see paths.FRIENDS_LIST_WINDOW_NAMES):
    the compact NewFriendsListWindow and the larger wndFriendsList. Only one is on screen
    at a time, and F cycles between them, so a check against a single name will report the
    list closed while the other one is still up.
    """
    for name in FRIENDS_LIST_WINDOW_NAMES:
        if await is_named_window_visible(client, name):
            return True
    return False


async def close_friends_list(client) -> bool:
    """Close the friends list, retrying until it is gone. Never raises.

    Closed with the F toggle rather than a close button. F cycles
    closed -> NewFriendsListWindow -> wndFriendsList -> closed, so pressing it until
    neither window is visible handles whichever list the client happens to be showing.

    A close button cannot do this job: btnFriendListClose belongs to wndFriendsList and
    does not close NewFriendsListWindow. It appeared to work earlier only because the two
    windows put their close buttons in the same place, so the click sometimes landed on
    whichever list was actually up.
    """
    try:
        return await press_key_until_gone(
            client,
            Keycode.F,
            is_open=lambda: is_friends_list_open(client),
        )
    except Exception:
        logger.debug("Could not close the friends list", exc_info=True)
        return False


async def resolve_friend_name(client, friend_name_lower: str) -> str:
    """Return the exact-case friend name matching the lowercased search term.

    Opens the online friends list (if not already open) and scans the full list text with a
    case-insensitive comparison. The list window's text contains every online friend
    regardless of the current scroll position, so pagination is not a concern here.

    WizWalker's friend lookups compare names case-sensitively, so callers must resolve the
    exact casing through this function before handing a name to them.

    Raises ValueError if no friends are online or no entry matches the search term.
    """
    from wizwalker.extensions.scripting.utils import (  # noqa: PLC0415
        _cycle_to_online_friends,
        _friend_list_entry,
        _maybe_get_named_window,
    )

    friends_window = await open_friends_list(client)
    await _cycle_to_online_friends(client, friends_window)

    friends_list = await _maybe_get_named_window(friends_window, "listFriends")
    list_text = await friends_list.maybe_text()

    if not list_text:
        raise ValueError("No friends online")

    for entry in _friend_list_entry.finditer(list_text):
        if entry.group("name").lower() == friend_name_lower:
            return entry.group("name")

    raise ValueError(f"Could not find friend matching {friend_name_lower!r} in online friends list")


async def open_friend_profile(client, name: str):
    """Open a friend's profile popup from the friends list and return the wndCharacter window.

    This is the first half of WizWalker's teleport_to_friend_from_list — it locates the
    friend, paginates to their row and clicks it, but stops at the profile popup instead of
    going on to teleport. That popup carries the per-player action buttons (go to friend,
    add/remove friend, add to group).

    `name` is matched case-insensitively. WizWalker's own lookup compares names with ==,
    so the exact casing is resolved here first, from the list text this function already
    reads — no extra UI work. Without that, a name whose casing differs even slightly from
    the friends list (e.g. one detected from the character page) silently fails to match.

    Raises ValueError if no friends are online or the name is not in the list.
    """
    from wizwalker.extensions.scripting.utils import (  # noqa: PLC0415
        _click_on_friend,
        _cycle_friends_list,
        _cycle_to_online_friends,
        _friend_list_entry,
        _maybe_get_named_window,
    )

    friends_window = await open_friends_list(client)
    await _cycle_to_online_friends(client, friends_window)

    friends_list = await _maybe_get_named_window(friends_window, "listFriends")
    list_text = await friends_list.maybe_text()
    if not list_text:
        raise ValueError("No friends online")

    wanted = name.lower()
    exact_name = next(
        (e.group("name") for e in _friend_list_entry.finditer(list_text) if e.group("name").lower() == wanted),
        name,
    )
    if exact_name != name:
        logger.debug("Resolved friend name %r to %r from the friends list", name, exact_name)

    right_button = await _maybe_get_named_window(friends_window, "btnArrowDown")
    page_number = await _maybe_get_named_window(friends_window, "PageNumber")
    page_text = await page_number.maybe_text()
    current_page, _ = map(
        int,
        page_text.replace("<center>", "").replace("</center>", "").replace(" ", "").split("/"),
    )

    friend, friend_index = await _cycle_friends_list(
        client, right_button, friends_list, None, None, exact_name, current_page
    )
    if friend is None:
        raise ValueError(f"Could not find friend {exact_name!r} in online friends list")

    await _click_on_friend(client, friends_list, friend_index)
    return await _maybe_get_named_window(client.root_window, "wndCharacter")
