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


# How much of the chat log a snapshot keeps. Snapshots are carried between fights as
# diff baselines, and the game's log holds thousands of lines after a long session, so
# an uncapped snapshot makes every later diff and every stored baseline grow with
# session length. Keeping the *tail* is what the diff needs: new announcements
# arrive at the end, and a trim only ever removes lines from the front, so a capped
# baseline survives a trim strictly longer than a full one does. The cap only has to
# exceed what can arrive between two consecutive snapshots — a poll interval or a
# single fight, on the order of tens of lines.
_SNAPSHOT_MAX_LINES = 500


async def snapshot_chat(client) -> str:
    """Return the tail of the current chat log text for a client.

    At most ``_SNAPSHOT_MAX_LINES`` lines are kept; see that constant for why.

    Returns an empty string if the chat window is not visible or not found.
    """
    root = client.root_window
    window = await _get_window_from_path(root, _CHAT_PATH)
    if window is None:
        return ""
    if not await window.is_visible():
        return ""
    return _tail(await window.maybe_text() or "")


def _tail(text: str) -> str:
    """Return the last ``_SNAPSHOT_MAX_LINES`` lines of `text`."""
    lines = text.split("\n")
    if len(lines) <= _SNAPSHOT_MAX_LINES:
        return text
    return "\n".join(lines[-_SNAPSHOT_MAX_LINES:])


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

    The common case is append-only: `old` is still a substring of `new`, and everything
    after it is new. That is one ``str.find``.

    The hard case is a *trim*. The game's chat log has a finite scrollback, so once it
    fills, new announcements push the oldest lines off the front and `old` stops being a
    substring of anything. What survives is an overlap — some suffix of `old` is a prefix
    of `new` — and the longest such overlap is the split point. It is found on line
    boundaries in O(n) via a KMP failure function. The previous implementation slid
    `old` forward one character at a time, re-scanning `new` on every step: O(len(old) x
    len(new)), measured at 127 ms on a 250 KB log and 856 ms when it missed entirely, paid
    on the event loop thread at end of fight — and it misfired, reporting the whole visible
    log as new drops (163 in one batch) whenever it ran out of `old` before matching.

    With no overlap at all the entire `new` string is considered new, as before.
    """
    if not old:
        return new

    idx = new.find(old)
    if idx >= 0:
        return new[idx + len(old) :]

    new_lines = new.split("\n")
    overlap = _suffix_prefix_overlap(old.split("\n"), new_lines)
    if overlap == 0:
        return new
    return "\n".join(new_lines[overlap:])


def _suffix_prefix_overlap(left: list[str], right: list[str]) -> int:
    """Return the length of the longest suffix of `left` that is also a prefix of `right`.

    The standard KMP failure-function trick: build ``right + <sentinel> + left`` and take
    the final failure value, which is by construction the longest prefix of `right` that is
    also a suffix of `left`. The sentinel is a fresh object, so it compares equal to nothing
    and the match cannot run across the join.
    """
    sentinel = object()
    combined: list[object] = [*right, sentinel, *left]

    failure = [0] * len(combined)
    k = 0
    for i in range(1, len(combined)):
        while k and combined[i] != combined[k]:
            k = failure[k - 1]
        if combined[i] == combined[k]:
            k += 1
        failure[i] = k
    return failure[-1]


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
