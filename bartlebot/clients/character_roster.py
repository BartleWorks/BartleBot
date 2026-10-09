"""Which characters live on which account, learned as clients connect.

The launch screen shows accounts and profiles, and until now it could say nothing about
either beyond a name. Characters are the missing half: a launch profile that brings four
accounts is really a choice of four *characters*, and nothing on screen said which.

``wizard_names.cached_name()`` cannot answer it. That cache is keyed by window handle and
filled by ``ClientManager``'s loop, so it only knows a character while a client is hooked
and playing it -- which is never true of an account sitting on the launch screen, which is
every account on that screen. So the roster has to be *persisted*, and learned at the one
moment both halves are known: a live client whose account has been identified.

STORAGE: KEYRING, NOT THE ACCOUNTS FILE

A wizard name is real player data. The shared accounts file (``shared.account_store``) is
not committed, so nothing forces this
choice -- but ``account_identity`` deliberately keeps *nothing* identifying in that file
and reasons explicitly about a leaked copy of it, storing an HMAC fingerprint rather than
the user id it stands for. A plaintext roster of wizard names beside those fingerprints
would undo that in one step, and unlike a user id a name cannot be fingerprinted: the
whole purpose of holding it is to display it.

So the roster goes to Windows Credential Manager, one JSON blob per account, under a
service of its own. The cost is that clearing credentials loses it -- which degrades to
"no characters known", the state before this existed, and repairs itself the next time
each character is played. That is the same failure mode ``account_identity`` accepts for
its key, for the same reason.

Streamer Mode is the other half of the exposure and lives in ``bartlebot.streamer_mode``:
this module stores names, that one decides whether they reach a label.

WHAT AN ENTRY HOLDS

    {"gid": int, "name": str}

``gid`` is ``ClientObject.m_characterId`` and is the identity: names are *not* unique --
plenty of accounts carry several wizards sharing a first name -- so a name can never be
the key. Entry order is the order characters were first seen, which is stable enough to
number them for display and is not claimed to match the game's own classroom order.
"""

from __future__ import annotations

import json
import logging

import keyring

from bartlebot.shared.vendor import keyring_service

logger = logging.getLogger(__name__)

_SERVICE = keyring_service("roster")
# A namespace of its own, not CredentialStore's: that service is keyed by account
# username and holds passwords, so a shared namespace would put a roster where a
# password is expected. Both take the vendor prefix from `shared.vendor`.

# The keys one roster entry carries. Anything else on a stored entry is dropped on the
# next write rather than preserved -- see `upsert`.
_FIELDS = ("gid", "name")


def entries(roster: list[dict] | None) -> list[dict]:
    """*roster* with anything malformed dropped, in stored order.

    Storage is a keyring blob that a user can edit or corrupt, so every read goes through
    here rather than trusting the shape. An entry without a usable gid is not an entry:
    the gid is the identity, and one without it can never be matched or updated.
    """
    out: list[dict] = []
    for entry in roster or []:
        if not isinstance(entry, dict):
            continue
        gid = entry.get("gid")
        if not isinstance(gid, int) or gid <= 0:
            continue
        out.append({"gid": gid, "name": str(entry.get("name") or "")})
    return out


def find(roster: list[dict] | None, gid: int) -> dict | None:
    """The entry for *gid*, or None."""
    for entry in entries(roster):
        if entry["gid"] == gid:
            return entry
    return None


def position(roster: list[dict] | None, gid: int) -> int:
    """The index of *gid* within the roster, or -1.

    Streamer Mode numbers characters by this, so it has to come from the roster's own
    order rather than from wherever the caller happens to be iterating.
    """
    for index, entry in enumerate(entries(roster)):
        if entry["gid"] == gid:
            return index
    return -1


def upsert(roster: list[dict] | None, gid: int, name: str) -> list[dict]:
    """*roster* with *gid* recorded, returning a new list.

    Appends a character not seen before, and updates the name of one that was -- a rename
    is a real thing the game allows, and the gid outlives it. Returns the input unchanged
    (normalized) when nothing would move, so a caller can compare and skip the write:
    this runs on the client loop, and keyring writes are not free.
    """
    current = entries(roster)
    if gid <= 0:
        return current
    for index, entry in enumerate(current):
        if entry["gid"] == gid:
            if entry["name"] == name or not name:
                return current
            updated = list(current)
            updated[index] = {"gid": gid, "name": name}
            return updated
    return [*current, {"gid": gid, "name": name}]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def load(username: str) -> list[dict]:
    """The stored roster for *username*, or an empty list.

    Never raises. A missing entry, a locked credential store and a corrupted blob are all
    "nothing known yet", which is a state the cards already have to render.
    """
    try:
        blob = keyring.get_password(_SERVICE, username)
    except Exception:
        logger.debug("Could not read the character roster for an account", exc_info=True)
        return []
    if not blob:
        return []
    try:
        return entries(json.loads(blob))
    except (ValueError, TypeError):
        logger.warning("The stored character roster for an account is not readable; ignoring it")
        return []


def save(username: str, roster: list[dict]) -> bool:
    """Store *roster* for *username*. Returns whether it was written.

    Never raises, for the same reason ``_learn_account_identities`` never does: this runs
    inside the client loop, where an exception escaping looks like every client vanishing
    at once.
    """
    try:
        keyring.set_password(_SERVICE, username, json.dumps(entries(roster)))
    except Exception:
        logger.debug("Could not store the character roster for an account", exc_info=True)
        return False
    return True


def forget(username: str) -> None:
    """Drop the stored roster for *username*, if any.

    Called when an account is deleted from settings: leaving a roster of wizard names
    behind for an account the user has removed is exactly the data this module is
    careful about.
    """
    try:
        keyring.delete_password(_SERVICE, username)
    except Exception:
        logger.debug("Could not delete the character roster for an account", exc_info=True)
