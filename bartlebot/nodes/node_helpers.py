"""Shared async helpers for node implementations."""

import asyncio

from wizwalker import Keycode

from bartlebot.nodes.paths import DUNGEON_WARNING_PATH  # noqa: F401 — re-exported for callers

POLL_INTERVAL = 0.25


async def dismiss_ambient_dialog_once(client) -> bool:
    """If client has an open dialog, press SPACEBAR to dismiss it. Returns True if dismissed."""
    if await client.is_in_dialog():
        await client.send_key(Keycode.SPACEBAR, 0.1)
        return True
    return False


async def confirm_dialog_if_visible(client, delay: float = 3.0) -> None:
    """Poll for the dungeon warning dialog; confirm it immediately when it appears.

    Keeps polling for at least `delay` seconds before concluding the dialog will
    not appear. If the dialog appears before `delay` has elapsed, ENTER is pressed
    immediately and the function returns — no further waiting.
    """
    loop_start = asyncio.get_event_loop().time()
    while True:
        if await is_visible_by_path(client, DUNGEON_WARNING_PATH):
            await client.send_key(Keycode.ENTER, 0.1)
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


async def wait_until(condition_coro, timeout: float | None, poll_interval: float = 0.25) -> bool:
    """Poll condition_coro every poll_interval until it returns True or timeout expires."""
    async def _poll():
        while not await condition_coro():
            await asyncio.sleep(poll_interval)

    try:
        await asyncio.wait_for(_poll(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def all_left_zone(clients: list, zone: str) -> bool:
    """Return True when every client's zone_name differs from zone."""
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    return all(z != zone for z in zones)


async def none_loading(clients: list) -> bool:
    """Return True when no client is on a loading screen."""
    loading = await asyncio.gather(*[c.is_loading() for c in clients])
    return not any(loading)


async def click_by_path(client, path: list[str]) -> bool:
    """Click the UI window at path. Returns True if found and clicked, False otherwise."""
    window = await _get_window_from_path(client.root_window, path)
    if window is None:
        return False
    await window.click()
    return True
