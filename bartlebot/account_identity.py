"""Recognise which account a running client belongs to, without keeping the id.

Recovery has to get from a dead window to a login:

    window → user id → fingerprint → configured account → stored password → relaunch

Only the third link has to be taught. A client reports an account-level user id in
memory (``game_client.user_id()``), but nothing in that number says which of the
configured accounts it is — that association can only be learned at the one moment
BartleBot knows the answer for certain, which is a launch it drove itself: we typed
those credentials, so whatever id the client reports belongs to that account.

Learned once, it holds forever, including for a window launched outside BartleBot and
adopted later. That is the case this exists for: an adopted window's title is a bare
"Wizard101", so the title-matching path in ``ClientManager._refresh_account_map`` can
never place it, and until now such a client could not be recovered at all (#79).

**Known limitation.** An account BartleBot has *never* launched has no fingerprint on
file, so an adopted window belonging to it stays unidentified — and one that isn't in
Settings → Accounts at all has no stored password either, so it could not be relaunched
even if it were identified. Neither is handled deliberately: launching an account
through BartleBot once fixes the first permanently, and the pre-run warning names any
client that is at risk. Assigning an account to a client by hand was considered and
rejected as not worth the control.

Storing the id itself
---------------------

We never do. The only question ever asked of it is "which account is this?", which is
equality, so what goes to disk is an HMAC-SHA256 fingerprint and nothing else.

The key is a *pepper*, not a salt, and that is the point. A per-record salt written
beside the digest would stop precomputed tables but not someone holding settings.json:
a user id is a bounded integer, dense within its range, so candidates can simply be
ground against a salt that is sitting right there. Keeping the key in Windows
Credential Manager instead leaves a leaked settings.json with nothing to grind — the
attacker needs DPAPI access as this user, and at that point they have the account
passwords anyway.

That also makes a slow KDF (scrypt, argon2) pointless here: those buy resistance when
the secret must live next to the hash, and there is nothing to slow down once it does
not. One HMAC per observed id, stdlib only.

Losing the key — cleared credentials, a new machine — makes every stored fingerprint
unmatchable. That degrades to "adopted windows are unidentified", which is where we
started, and it repairs itself the next time each account is launched from here.
Copying settings.json to another machine yields fingerprints that match nothing, which
is the intended outcome rather than a shortcoming.
"""

from __future__ import annotations

import base64
import hmac
import logging
import secrets
from hashlib import sha256

import keyring

logger = logging.getLogger(__name__)

SCHEME = 1
# Version of the fingerprinting scheme, stored alongside each fingerprint so a future
# change (different construction, longer key) can migrate rather than guess.

_SERVICE = "BartleBot-identity"
# Deliberately not CredentialStore's "BartleBot": that service is keyed by account
# username, and a shared namespace would collide with an account named like our key.

_KEY_NAME = "account-id-key"

_KEY_BYTES = 32

_key_cache: bytes | None = None


def account_key() -> bytes:
    """The HMAC key for this install, creating and storing one on first use.

    Cached in memory: this is read once per client identification, and a Credential
    Manager round trip per lookup would be pure waste.
    """
    global _key_cache
    if _key_cache is not None:
        return _key_cache

    stored = keyring.get_password(_SERVICE, _KEY_NAME)
    if stored:
        try:
            _key_cache = base64.b64decode(stored)
            return _key_cache
        except Exception:
            logger.warning("Stored account-id key is unreadable — generating a new one")

    key = secrets.token_bytes(_KEY_BYTES)
    keyring.set_password(_SERVICE, _KEY_NAME, base64.b64encode(key).decode("ascii"))
    logger.info("Generated a new account-id key; accounts will be re-fingerprinted as they launch")
    _key_cache = key
    return key


def reset_key_cache() -> None:
    """Drop the cached key so the next call re-reads it. For tests and key rotation."""
    global _key_cache
    _key_cache = None


def fingerprint(user_id: int, key: bytes) -> str:
    """The stored form of *user_id* under *key* — HMAC-SHA256, hex.

    Takes the key rather than fetching it so the construction stays a pure function:
    the same id and key always give the same string, on any machine, with no keyring
    and no global state involved.
    """
    return hmac.new(key, str(user_id).encode("ascii"), sha256).hexdigest()


def mask(user_id: int) -> str:
    """A log-safe rendering of *user_id* — enough to correlate two lines, not to identify.

    Logs are pasted into issues, so the full id must never reach one.
    """
    text = str(user_id)
    return "****" if len(text) <= 4 else "*" * (len(text) - 4) + text[-4:]


def match_account(accounts: list[dict], fp: str) -> str | None:
    """The username whose stored fingerprint is *fp*, or None.

    Entries written under an older scheme are ignored rather than compared: a
    fingerprint means nothing without knowing how it was built.
    """
    for account in accounts:
        if account.get("uid_scheme") != SCHEME:
            continue
        if account.get("uid_fingerprint") == fp:
            return account.get("username")
    return None


def stored_fingerprint(accounts: list[dict], username: str) -> str | None:
    """The fingerprint currently on file for *username*, or None if it has none."""
    for account in accounts:
        if account.get("username") == username and account.get("uid_scheme") == SCHEME:
            return account.get("uid_fingerprint")
    return None


def with_fingerprint(accounts: list[dict], username: str, fp: str) -> list[dict]:
    """*accounts* with *username*'s fingerprint set to *fp*.

    Returns a new list of new dicts — the caller hands this straight to the settings
    setter, and mutating the list it just read would make the write look like a no-op
    to anything comparing before and after.
    """
    updated = []
    for account in accounts:
        entry = dict(account)
        if entry.get("username") == username:
            entry["uid_fingerprint"] = fp
            entry["uid_scheme"] = SCHEME
        updated.append(entry)
    return updated
