"""Shared async helpers for node implementations."""

import asyncio
import fnmatch
import logging
import re

from wizwalker import Keycode

logger = logging.getLogger(__name__)

from bartlebot.nodes.paths import (  # noqa: F401 — re-exported for callers
    ADVANCE_DIALOG_PATH,
    DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH,
    GAUNTLET_ENTRANCE_PATH,
    MESSAGE_BOX_CENTER,
)
from bartlebot.nodes.timing import (  # noqa: F401 — re-exported for callers
    DIALOG_FAST_POLL_INTERVAL,
    DIALOG_TIMEOUT,
    POLL_INTERVAL,
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
    """Return True if the UI window at path exists and is currently visible."""
    window = await _get_window_from_path(client.root_window, path)
    if window is None:
        return False
    return await window.is_visible()


async def is_enabled_by_path(client, path: list[str]) -> bool:
    """Return True if the UI window at path exists and is not grayed out (i.e., enabled)."""
    window = await _get_window_from_path(client.root_window, path)
    if window is None:
        return False
    return not await window.is_control_grayed()


async def _get_window_from_path(parent, path: list[str]):
    """Traverse the UI window tree by a sequence of child names.

    Returns the matching Window object, or None if the path cannot be followed.
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
    """Poll condition_coro every poll_interval until it returns True or timeout expires."""

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
    if "*" in pattern or "?" in pattern or "[" in pattern:
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
