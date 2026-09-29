"""Identify which in-game modal is on screen from its caption text.

THE PROBLEM
    Every Wizard101 modal is the same window tree -- MessageBoxModalWindow, with left /
    centre / right buttons -- so "a modal is up" says nothing about which one. Nodes used
    to infer identity from which buttons were showing, which works only while no two
    dialogs a node can hit share a button layout. teleport_to_friend broke that: the
    dungeon exit warning and the friend-busy dialog both show a right button, so the
    busy check matched every dungeon exit and reported an unreachable friend.

    The caption is the real discriminator, and CaptionText is readable at
    MESSAGE_BOX_CAPTION_PATH.

WHY A SNAPSHOT, AND WHY EVERY LOCALE
    Memory holds the *resolved* display text, never the lang key -- so matching English
    substrings silently fails on a client running in any of the game's other six locales,
    and fails by finding nothing, which reads as "no dialog". ``data/dialog_captions.json``
    therefore holds every locale's text for each key, extracted from Root.wad by
    ``tools/dump_dialog_captions.py``. The app never reads Root.wad at runtime: that would
    mean a 172k-entry archive read per lookup plus a hard dependency on the game being
    installed at a guessable path.

MATCHING
    Captions are templates, not literals. They carry markup (``<br>``, ``<center>``) and
    substitution slots (``$ZONE$``, ``#1:{their[!n]}``) that are already filled in by the
    time the text reaches memory, so equality never holds. Each caption is reduced to its
    longest run of literal text instead, and a caption matches when that run appears in
    what the client is showing. The longest run is used because short fragments collide:
    "Are you sure you want to" opens three unrelated dialogs.

    An unrecognised caption returns None rather than a guess. Callers treat that as
    "some other dialog" and fall back to whatever they did before, so a caption the
    snapshot has not seen degrades to the old behaviour instead of hanging.
"""

from __future__ import annotations

import json
import logging
import re
from functools import cache
from pathlib import Path

logger = logging.getLogger(__name__)

CAPTIONS_PATH = Path(__file__).resolve().parent / "data" / "dialog_captions.json"

# Lang keys the app knows how to answer. Each is a module-level constant so call sites
# name the dialog rather than repeating a string, and so a key that disappears from the
# game surfaces as a load-time warning instead of a silent never-match.
INSTANCE_EXIT = "InstanceExit"
"""Leaving a dungeon by any means. Confirm to proceed."""

DUNGEON_ENTRY = "WizardInstancePromptText:00000000"
"""Stepping on a dungeon sigil. Confirm to enter."""

DUNGEON_LOGOUT = "DungeonLogout"
"""Logging out from inside a dungeon loses its progress. Confirm to proceed."""

DUNGEON_RECALL_LOGOUT = "GUI:00007243"
"""Logging out closes Dungeon Recall. Confirm to proceed."""

FRIEND_BUSY = "GUI:00006574"
"""'Your friend is busy right now.' Teleport refused -- dismiss and fail."""

FRIEND_CROSS_REALM = "GoToFriendZoneClearBossMark"
"""Friend is in another realm; porting resets the current dungeon. Confirm to proceed."""

REALM_RESETS_DUNGEON = "OnGoToRealmBossMarkRemovalWarningText"
"""Realm change will reset the current dungeon. Confirm to proceed."""

REALM_NOT_ALLOWED_HERE = "Error_DynamicZoneError"
"""Realm change refused from this zone. Dismiss and fail -- retrying cannot help."""

RECALL_DUNGEON_EXPIRED = "ERROR_FailedInstanceRecall"
"""Recall target's dungeon timer ended. Dismiss and fail."""

GAUNTLET_NO_ZONE_CHANGE = "Error_GauntletError"
"""Zone change refused while in a gauntlet. Dismiss and fail."""

ALREADY_IN_GROUP = "GUI:00001085"
"""Invited player is already grouped. Dismiss."""

DUNGEON_FULL = "NoSubsOpen"
"""'That dungeon is full.' Dismiss and fail."""

REALM_DOES_NOT_EXIST = "Error_RealmDoesNotExist"
"""Chosen realm is gone. Dismiss and fail."""

FRIEND_NOT_ON_LIST = "GoToFriendFail"
"""'You failed to teleport to this friend. Perhaps he or she is not your friend anymore.'"""

# The client has fallen out of the game. Nothing to click -- ClientHealthMonitor kills the
# process and lets the relaunch/re-hook path recover it.
DISCONNECTED_IDLE = "ConnectionAFK"
"""'You have been disconnected due to inactivity.'"""

DISCONNECTED_LOST = "ConnectionLost"
"""'Connection Lost!'"""

DISCONNECTED_BY_ADMIN = "CSRDisconnect"
"""Disconnected from the server by an Administrator."""

DISCONNECTED_ELSEWHERE = "ForceDisconnect"
"""Someone else has logged in with this account."""

DISCONNECTED_MAINTENANCE = "MaintenanceDisconnect"
"""Disconnected for maintenance on this character."""

CONNECT_FAILED = "FailedConnectToServer"
"""'Failed to connect to server!'"""

LOGIN_LOCKED = "LockFailed"
"""'Your character is unable to enter the game. Please quit and try again.'"""

# Every key the snapshot is expected to carry. tools/dump_dialog_captions.py reads this
# list, so adding a dialog here and re-running the tool is the whole workflow.
#
# A key is "<file stem>:<key>" when the lang key is a bare number, which is not unique
# across files, and the plain key otherwise.
KNOWN_KEYS: tuple[str, ...] = (
    INSTANCE_EXIT,
    DUNGEON_ENTRY,
    DUNGEON_LOGOUT,
    DUNGEON_RECALL_LOGOUT,
    FRIEND_BUSY,
    FRIEND_CROSS_REALM,
    REALM_RESETS_DUNGEON,
    REALM_NOT_ALLOWED_HERE,
    RECALL_DUNGEON_EXPIRED,
    GAUNTLET_NO_ZONE_CHANGE,
    ALREADY_IN_GROUP,
    DUNGEON_FULL,
    REALM_DOES_NOT_EXIST,
    FRIEND_NOT_ON_LIST,
    DISCONNECTED_IDLE,
    DISCONNECTED_LOST,
    DISCONNECTED_BY_ADMIN,
    DISCONNECTED_ELSEWHERE,
    DISCONNECTED_MAINTENANCE,
    CONNECT_FAILED,
    LOGIN_LOCKED,
)

# ---------------------------------------------------------------------------
# What to do about each dialog
#
# Every modal falls into one of two kinds, and the distinction is what a node needs:
# a question it should answer yes to and carry on, or a refusal that no amount of
# retrying will get past. Confusing the two is expensive in both directions -- confirming
# a refusal makes the node spin until its timeout and then report the wrong cause, while
# failing on a question aborts a run that would have worked.
# ---------------------------------------------------------------------------

CONFIRM_TO_PROCEED = frozenset(
    {
        INSTANCE_EXIT,
        REALM_RESETS_DUNGEON,
        FRIEND_CROSS_REALM,
        DUNGEON_ENTRY,
        DUNGEON_LOGOUT,
        DUNGEON_RECALL_LOGOUT,
    }
)
"""Questions. Click the centre (confirm) button and the action goes ahead."""

REFUSALS = frozenset(
    {
        FRIEND_BUSY,
        REALM_NOT_ALLOWED_HERE,
        RECALL_DUNGEON_EXPIRED,
        GAUNTLET_NO_ZONE_CHANGE,
        DUNGEON_FULL,
        REALM_DOES_NOT_EXIST,
        FRIEND_NOT_ON_LIST,
    }
)
"""Refusals. Dismiss and fail the node -- the game will not do the thing."""

DISCONNECTS = frozenset(
    {
        DISCONNECTED_IDLE,
        DISCONNECTED_LOST,
        DISCONNECTED_BY_ADMIN,
        DISCONNECTED_ELSEWHERE,
        DISCONNECTED_MAINTENANCE,
        CONNECT_FAILED,
        LOGIN_LOCKED,
    }
)
"""The client is out of the game. Not a refusal and not a question: there is nothing a node
can usefully click, so resolve_modal leaves these alone and ClientHealthMonitor kills the
process instead, letting the relaunch and re-hook path recover it."""

UNRECOGNISED_REFUSAL = "<unrecognised>"
"""Not a lang key: a dismiss-only dialog whose caption the snapshot does not know.

Returned by node_helpers.resolve_modal() when caption matching finds nothing but the
button layout says refusal -- a right button with no centre one, meaning the dialog offers
no way to proceed. It keeps a node failing fast on a dialog reworded by a game patch,
instead of silently waiting out its timeout, and the log line says plainly that the bot
could not identify it.
"""

# Plain-language reasons for the log, so a FAILURE line says what the player would have
# read rather than a lang key. Kept short: these are appended to a node's own message.
REASONS: dict[str, str] = {
    FRIEND_BUSY: "the friend is not accepting teleports right now (this is not about combat — porting to a friend who is fighting is allowed)",
    REALM_NOT_ALLOWED_HERE: "realms cannot be switched from inside a dungeon; leave it first",
    RECALL_DUNGEON_EXPIRED: "the marked dungeon's timer ended and the dungeon has been reset",
    GAUNTLET_NO_ZONE_CHANGE: "zones cannot be changed from inside a gauntlet",
    DUNGEON_FULL: "that dungeon is full",
    REALM_DOES_NOT_EXIST: "the chosen realm no longer exists",
    FRIEND_NOT_ON_LIST: "that wizard is no longer on the friends list",
    ALREADY_IN_GROUP: "that player is already in a group",
    UNRECOGNISED_REFUSAL: (
        "the game refused with a dialog this build does not recognise — if this is "
        "reproducible, add its lang key to KNOWN_KEYS and re-run tools/dump_dialog_captions.py"
    ),
}


def describe(key: str | None) -> str:
    """Plain-language reason for a dialog, for logging. Falls back to the key itself."""
    if key is None:
        return "no dialog"
    return REASONS.get(key, key)


# Markup the game embeds in caption text, and the substitution slots it fills in before
# display. Both are stripped so what remains is text a player would actually read.
_MARKUP = re.compile(r"<[^>]*>")
_SLOTS = re.compile(r"\$[A-Za-z_]+\$|#\d+:\{[^}]*\}|#\d+:|&[A-Za-z_]+&")
_WHITESPACE = re.compile(r"\s+")

# A literal run shorter than this is not trusted to identify a dialog on its own -- short
# fragments collide ("Are you sure you want to" opens at least three unrelated dialogs).
# It cannot be raised much further: the shortest real caption, Error_RealmDoesNotExist,
# is "Desired realm does not exist" at 28 characters, and its translations are shorter
# still. The genuine guard against collisions is the --verify pass in
# tools/dump_dialog_captions.py, which fails when two keys share a fingerprint or when one
# key's fingerprint appears inside another's text.
MIN_LITERAL_LEN = 16


def normalize(text: str) -> str:
    """Reduce caption text to comparable form: markup and slots gone, whitespace collapsed."""
    text = _MARKUP.sub(" ", text)
    text = _SLOTS.sub("\x00", text)  # slot boundaries survive as separators
    text = text.replace("\\n", " ").replace("\r", " ").replace("\n", " ")
    return _WHITESPACE.sub(" ", text).strip().lower()


def literal_runs(text: str) -> list[str]:
    """Return the stretches of a caption template that survive substitution, longest first."""
    runs = [_WHITESPACE.sub(" ", part).strip() for part in normalize(text).split("\x00")]
    return sorted((r for r in runs if r), key=len, reverse=True)


def _fingerprint(text: str) -> str | None:
    """The one literal run used to recognise this caption, or None if none is long enough."""
    for run in literal_runs(text):
        if len(run) >= MIN_LITERAL_LEN:
            return run
    return None


@cache
def _fingerprints() -> tuple[tuple[str, str], ...]:
    """(fingerprint, lang key) for every caption in the snapshot, longest fingerprint first.

    Longest first so that a caption whose fingerprint is a substring of another's cannot
    shadow it -- the more specific match is tested before the more general one.
    """
    try:
        raw = json.loads(CAPTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("Could not read dialog captions from %s; modals cannot be identified", CAPTIONS_PATH)
        return ()

    captions: dict[str, dict[str, str]] = raw.get("captions", {})
    missing = [key for key in KNOWN_KEYS if key not in captions]
    if missing:
        logger.warning(
            "dialog_captions.json is missing %d known key(s): %s. Regenerate with tools/dump_dialog_captions.py",
            len(missing),
            ", ".join(missing),
        )

    entries: list[tuple[str, str]] = []
    for key, by_locale in captions.items():
        for text in by_locale.values():
            if fingerprint := _fingerprint(text):
                entries.append((fingerprint, key))
    entries.sort(key=lambda pair: len(pair[0]), reverse=True)
    return tuple(dict.fromkeys(entries))


def identify_caption(text: str | None) -> str | None:
    """Return the lang key of the dialog showing `text`, or None if it is not a known one.

    `text` is the raw CaptionText read from the client, in whatever locale it is running.
    """
    if not text:
        return None
    haystack = normalize(text).replace("\x00", " ")
    haystack = _WHITESPACE.sub(" ", haystack)
    for fingerprint, key in _fingerprints():
        if fingerprint in haystack:
            return key
    return None
