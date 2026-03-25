"""Shared async helpers for node implementations."""

import asyncio

from bartlebot.nodes.paths import (  # noqa: F401 — re-exported for callers
    ADVANCE_DIALOG_PATH,
    DUNGEON_ENTRANCE_PATH,
    DUNGEON_WARNING_PATH,
)
from bartlebot.nodes.timing import POLL_INTERVAL  # noqa: F401 — re-exported for callers


async def all_in_npc_range(clients: list) -> bool:
    """Return True when every client has the interact prompt showing."""
    results = await asyncio.gather(*[c.is_in_npc_range() for c in clients])
    return all(results)


async def none_in_npc_range(clients: list) -> bool:
    """Return True when no client has an interact prompt showing."""
    results = await asyncio.gather(*[c.is_in_npc_range() for c in clients])
    return not any(results)


async def all_at_dungeon_entrance(clients: list) -> bool:
    """Return True when every client has the dungeon entrance prompt (TeamUpButton) showing."""
    results = await asyncio.gather(*[is_visible_by_path(c, DUNGEON_ENTRANCE_PATH) for c in clients])
    return all(results)


async def none_at_dungeon_entrance(clients: list) -> bool:
    """Return True when no client has the dungeon entrance prompt (TeamUpButton) showing."""
    results = await asyncio.gather(*[is_visible_by_path(c, DUNGEON_ENTRANCE_PATH) for c in clients])
    return not any(results)


async def dismiss_ambient_dialog_once(client) -> bool:
    """If client has an advanceable dialog open, click the advance button to dismiss it. Returns True if dismissed."""
    if await is_visible_by_path(client, ADVANCE_DIALOG_PATH):
        return await click_by_path(client, ADVANCE_DIALOG_PATH)
    return False


async def confirm_dungeon_warning_dialog(client, delay: float = 3.0) -> None:
    """Poll for the dungeon warning dialog; confirm it immediately when it appears.

    Keeps polling for at least `delay` seconds before concluding the dialog will
    not appear. If the dialog appears before `delay` has elapsed, the confirm button
    is clicked immediately and the function returns — no further waiting.
    """
    loop_start = asyncio.get_event_loop().time()
    while True:
        if await click_by_path(client, DUNGEON_WARNING_PATH):
            return
        if asyncio.get_event_loop().time() - loop_start >= delay:
            return
        await asyncio.sleep(POLL_INTERVAL)


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


async def all_left_zone(clients: list, zone: str) -> bool:
    """Return True when every client's zone_name differs from zone."""
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    return all(z != zone for z in zones)


async def all_in_zone(clients: list, zone: str) -> bool:
    """Return True when every client's zone_name matches zone."""
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    return all(z == zone for z in zones)


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


async def click_by_path(client, path: list[str]) -> bool:
    """Click the UI window at path. Returns True if found and clicked, False otherwise."""
    window = await _get_window_from_path(client.root_window, path)
    if window is None:
        return False
    async with client.mouse_handler:
        await client.mouse_handler.click_window(window)
    return True
