"""Chat-based drop detection utilities.

Reads the in-game system chat window after combat to detect item drops.
Adapted from Deimos (https://github.com/Deimos-Wizard101/Deimos-Wizard101).
"""

import asyncio
import re
import time

# Window tree path to the chat log text element
_CHAT_PATH = [
    "WorldView",
    "WizardChatBox",
    "chatContainer",
    "chatLogContainer",
    "chatLogInnerContainer",
    "chatLog",
]

# System-recognised drop categories (from Deimos drop_logger.py)
_DROP_TYPES = {
    "PetSnack",
    "Reagent",
    "Housing",
    "Pet",
    "Shoes",
    "Seed",
    "Jewel",
    "Robe",
    "Hat",
    "Athame",
    "Weapon",
    "Deck",
    "Ring",
    "Amulet",
}


async def snapshot_chat(client) -> str:
    """Return the current chat log text for a client.

    Returns an empty string if the chat window is not visible or not found.
    """
    root = client.root_window
    window = await _get_window_from_path(root, _CHAT_PATH)
    if window is None:
        return ""
    if not await window.is_visible():
        return ""
    return await window.maybe_text() or ""


async def chat_available(client) -> bool:
    """Return True if the chat log window exists and is currently visible.

    Drop detection has nothing to read when the chat log is hidden, so callers
    that would otherwise poll for a message check this once up front and skip
    detection entirely rather than waiting out a timeout per action.
    """
    window = await _get_window_from_path(client.root_window, _CHAT_PATH)
    return window is not None and await window.is_visible()


async def poll_new_drops(client, before: str, timeout: float, poll_interval: float) -> tuple[list[str], str]:
    """Poll the chat log until new drops appear, or `timeout` seconds elapse.

    Drop announcements are sent by the game server, so they land some time after
    the local action that earned them has visibly finished. A single snapshot
    taken the moment an interaction completes normally reads the chat exactly as
    it was before the drop.

    Returns `(drops, baseline)`. When drops are found, `baseline` is the snapshot
    they were read from, and the caller passes it back as `before` next time.
    When nothing is found, `baseline` is `before` unchanged — so a message that
    arrives after the timeout is still caught by the following call instead of
    being silently absorbed into a fresh baseline.
    """
    deadline = time.monotonic() + timeout
    while True:
        after = await snapshot_chat(client)
        drops = extract_new_drops(before, after)
        if drops:
            return drops, after
        if time.monotonic() >= deadline:
            return [], before
        await asyncio.sleep(poll_interval)


def extract_new_drops(before: str, after: str) -> list[str]:
    """Return item names that appeared in the chat between two snapshots.

    Compares the before/after chat text and parses any new system messages
    that match known drop categories.
    """
    new_text = _find_new_stuff(before, after)
    if not new_text:
        return []
    return _filter_drops(new_text.split("\n"))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_window_from_path(window, path: list[str]):
    """Traverse the UI window tree by a sequence of child names.

    Returns the matching Window object, or None if the path cannot be followed.
    """
    if not path:
        return window
    for child in await window.children():
        if await child.name() == path[0]:
            result = await _get_window_from_path(child, path[1:])
            if result is not None:
                return result
    return None


def _find_new_stuff(old: str, new: str) -> str:
    """Return the portion of `new` that does not appear in `old`.

    Slides the old string backward until it finds itself as a substring of new,
    then returns everything that comes after it. If old is not found at all,
    the entire `new` string is considered new.
    """
    while old:
        idx = new.find(old)
        if idx >= 0:
            return new[idx + len(old) :]
        old = old[1:]
    return new


def _filter_drops(lines: list[str]) -> list[str]:
    """Parse raw chat lines and return item names that are recognised drops.

    System messages are identified by the 'Art_Chat_System.dds' marker.
    The item type (Reagent, Weapon, etc.) and name are extracted via regex,
    matching the format Wizard101 uses for drop announcements.
    """
    drops: list[str] = []
    for raw in lines:
        if "Art_Chat_System.dds" not in raw:
            continue

        # Match everything after "> <" (the user-facing message body)
        after_bracket = re.findall(r"(?<=> <).*|$", raw)[0]

        if after_bracket:
            drop_type = ""
            if ";" in after_bracket:
                # The drop category is between ; and >
                drop_type = re.findall(r"(?<=;).*?[^>]*|$", after_bracket)[0]

            if drop_type in _DROP_TYPES:
                # Item name is between > and <
                raw_drop = re.findall(r">.*?<|$", after_bracket)[0]
                name = re.findall(r"[^>]+[^<]+|$", raw_drop)[0]
                drops.append(name.lstrip())

        elif ":" in raw.lower():
            # Fallback: match everything after : (catches gold / simpler messages)
            name = re.findall(r"(?<=:).*?[^<]*|$", raw)[0]
            drops.append(name.lstrip())

    return drops
