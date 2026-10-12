"""Accounts and launch profiles, in a file every product by this publisher can read.

``%APPDATA%/Bartleby/BartleBot/settings.json`` used to hold two kinds of thing that are
not the same kind of thing:

* **About the account** -- ``accounts`` (username, alias, the pinned ``color`` slot, the
  identity fingerprint) and ``launch_profiles`` (membership, order, resolution override).
* **About this app** -- canvas layout, sidebar widths, Safety Delay, bot options, the bot
  library path, Streamer Mode.

A live-play companion needs the first group and none of the second: it wants quick
launching, launch profiles and account tracking, and has no bot canvas, no FSM and no
node timings. Reaching the first group meant reading another product's settings file and
parsing around everything else in it, and any write clobbered whatever that product had
in memory.

So the account half moves one level up, to ``%APPDATA%/Bartleby/accounts.json`` -- beside
the products rather than inside one -- as a versioned record::

    {"version": 1, "accounts": [...], "launch_profiles": [...]}

``SettingsManager.accounts`` and ``.launch_profiles`` are a facade over it, so no call
site changed and the split is invisible to the GUI.

WHAT DOES *NOT* COME WITH THE RECORD

A launch profile's resolution override travels; the verdict on whether it fits does not.
``screen_fit`` and ``game_sizing`` judge a resolution against *this* app's game area --
sidebars, the bot canvas, ``live_game_area`` -- so the same profile fits differently in a
tool with no bot canvas. Moving them alongside the record is the obvious wrong turn.

TWO PROCESSES AT ONCE

Not an edge case, it is the premise: farming on two accounts while playing live on a
third means both apps are running. Every write goes through :meth:`AccountStore.set_accounts`
or :meth:`AccountStore.set_launch_profiles`, and each reaches disk through
:func:`~bartlebot.shared.json_update.update_json`: the file is read as it is *now*, only
the key that call changed is replaced, and the result is renamed over the file. So an
account change from one product and a launch-profile change from another both survive, and
so does any key a newer version added. What the store holds in memory is a copy of the file
as of the last read or write, refreshed by every write; it is not the authority.

Within a key the write is per entry, not per list. Callers hand back a whole list built from
the copy in memory, which may be hours old: the client loop learning a fingerprint writes
every account it holds, including one the other product deleted since, and none it added.
So :meth:`AccountStore.save` merges three ways, by an account's ``username`` and a launch
profile's ``name``: what this store last read or wrote (the base), what it holds now, and
what is on disk now. Only the entries this store changed against its base are applied:

* an entry it added or edited is written as it holds it;
* an entry it removed is removed;
* every other entry is the file's, so the other product's adds, edits and removes stand;
* the order is this store's when it reordered the entries, and the file's otherwise.

An entry both products edited is this store's, the later write. One the other product
removed stays removed, even if this store edited it: a delete is the more deliberate act,
and a fingerprint learned for an account the user just deleted must not bring it back. A
list whose entries are not all keyed and distinct can't be merged, and is written whole,
as it was before.

The same two methods are why a caller never mutates a dict inside the list it read and
leaves it there: the store would have no way to know it changed.

A write is atomic in the crash sense too: an interrupted save leaves the previous file
whole rather than a half-written one that reads as "no accounts".
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

from bartlebot.shared.json_update import update_json
from bartlebot.shared.vendor import vendor_dir

logger = logging.getLogger(__name__)

#: Schema version of the stored record. Bumped when the shape changes in a way a reader
#: has to know about.
SCHEMA_VERSION = 1

#: The filename beneath the vendor directory. Not a product name: this is the shared one.
FILENAME = "accounts.json"

_ACCOUNTS = "accounts"
_LAUNCH_PROFILES = "launch_profiles"

#: What names an entry across two writers, for each list.
_ENTRY_KEYS = {_ACCOUNTS: "username", _LAUNCH_PROFILES: "name"}


class AccountStore:
    """The vendor-level accounts file: read at start, written a key at a time.

    Constructed with its directory rather than finding one, so a test -- or a second
    product with its own layout -- can point it anywhere without patching a module
    global.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._path = (directory or vendor_dir()) / FILENAME
        self._accounts: list[dict] = []
        self._launch_profiles: list[dict] = []
        #: Keys changed in memory and not yet written; what :meth:`save` writes.
        self._dirty: set[str] = set()
        #: Each list as this store last read or wrote it: what a change is measured against.
        self._base: dict[str, list[dict]] = {_ACCOUNTS: [], _LAUNCH_PROFILES: []}
        self._load()

    @property
    def path(self) -> Path:
        """Where this store reads and writes. Public so a caller can say so in a log."""
        return self._path

    @property
    def exists(self) -> bool:
        """Whether a stored file is already on disk.

        The migration's one question: an absent file means the settings file may still
        be carrying accounts that belong here.
        """
        return self._path.exists()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    @property
    def accounts(self) -> list[dict]:
        """The stored account records: ``{username, alias?, color?, ...}``.

        A shallow copy of the list, which is what ``SettingsManager`` returned before the
        split -- callers iterate it, and the ones that edit an entry read, mutate and hand
        the whole list back through :meth:`set_accounts`.
        """
        return list(self._accounts)

    @property
    def launch_profiles(self) -> list[dict]:
        """The stored profiles: ``{name, accounts: [username, ...], resolution?}``."""
        return list(self._launch_profiles)

    # ------------------------------------------------------------------
    # Writes -- the only two, deliberately
    # ------------------------------------------------------------------

    def set_accounts(self, accounts: list[dict], save: bool = True) -> None:
        """Replace the account list, writing it out unless *save* is False.

        *save* exists for the one caller that changes both lists -- deleting an account
        also strips it from every profile that names it -- so the pair reaches disk in a
        single write rather than leaving a window where the file names an account that
        the same operation has already removed.
        """
        self._accounts = list(accounts)
        self._dirty.add(_ACCOUNTS)
        if save:
            self.save()

    def set_launch_profiles(self, profiles: list[dict], save: bool = True) -> None:
        """Replace the launch profiles, writing them out unless *save* is False."""
        self._launch_profiles = list(profiles)
        self._dirty.add(_LAUNCH_PROFILES)
        if save:
            self.save()

    def save(self) -> None:
        """Write the entries changed since the last write, atomically. Never raises: a failure is logged.

        Only those: the rest of the file is whatever is on disk now, which may be another
        product's newer write (see the module docstring for the merge). Called from GUI
        slots and from the client loop, where an exception escaping looks like the launch
        screen breaking. The data is still in memory either way, and its key stays
        pending, so the next successful save writes it.
        """
        pending = {key: (self._accounts if key == _ACCOUNTS else self._launch_profiles) for key in sorted(self._dirty)}
        base = {key: self._base[key] for key in pending}

        def mutate(record: dict[str, Any]) -> None:
            # Never lowered: a newer product's version stays, or it would read its own
            # record as one still to migrate. PreferenceStore does the same.
            record.setdefault("version", SCHEMA_VERSION)
            for key, mine in pending.items():
                theirs = _as_dict_list(record.get(key))
                record[key] = _merge_entries(base[key], mine, theirs, _ENTRY_KEYS[key])
            record.setdefault(_ACCOUNTS, [])
            record.setdefault(_LAUNCH_PROFILES, [])

        try:
            written = update_json(self._path, mutate)
        except OSError:
            logger.exception("Could not write the shared accounts file at %s", self._path)
            return
        self._dirty.clear()
        self._take(written)

    # ------------------------------------------------------------------
    # Load and migration
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Read the stored record, or start empty. A corrupt file is not overwritten here.

        Every failure lands on "nothing stored yet", which is a state the launch screen
        already draws -- but the file is left where it is, because the next ``save`` is
        driven by a user action and not by having loaded.
        """
        if not self._path.exists():
            return
        try:
            with open(self._path, encoding="utf-8") as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            logger.exception("The shared accounts file at %s is not readable", self._path)
            return
        if not isinstance(record, dict):
            logger.warning("The shared accounts file at %s is not a record; ignoring it", self._path)
            return
        self._take(record)

    def _take(self, record: dict[str, Any]) -> None:
        """Hold both lists as *record* has them, and measure the next change against that."""
        self._accounts = _as_dict_list(record.get(_ACCOUNTS))
        self._launch_profiles = _as_dict_list(record.get(_LAUNCH_PROFILES))
        # Deep, because the lists handed out are shallow copies: a caller that edits a
        # profile's account list in place would otherwise edit the base with it, and the
        # change would read as no change.
        self._base = {_ACCOUNTS: copy.deepcopy(self._accounts), _LAUNCH_PROFILES: copy.deepcopy(self._launch_profiles)}

    def adopt(self, accounts: Any, launch_profiles: Any) -> bool:
        """Take over accounts still held in a product's own settings file. Whether it wrote.

        The one-time migration out of ``settings.json``, and it is free only while it
        stays one-time: it runs when there is no shared file yet, so the moment a user
        has one, the settings file's copy is dead weight that the caller drops. Doing
        this before 1.0.0 is what keeps it from becoming migration code with no end date.

        Refuses when a shared file already exists, so a stale ``accounts`` key left in a
        hand-edited settings file cannot overwrite the real store on the next start.
        """
        if self.exists:
            return False
        accounts = _as_dict_list(accounts)
        launch_profiles = _as_dict_list(launch_profiles)
        if not accounts and not launch_profiles:
            return False
        self._accounts = accounts
        self._launch_profiles = launch_profiles
        self._dirty.update((_ACCOUNTS, _LAUNCH_PROFILES))
        self.save()
        logger.info(
            "Moved %d account(s) and %d launch profile(s) into the shared store at %s",
            len(accounts),
            len(launch_profiles),
            self._path,
        )
        return True


def _keyed(entries: list[dict], key: str) -> dict[str, dict] | None:
    """*entries* by their *key* field, in order, or None when one lacks it or two share it."""
    keyed: dict[str, dict] = {}
    for entry in entries:
        name = entry.get(key)
        if not isinstance(name, str) or name in keyed:
            return None
        keyed[name] = entry
    return keyed


def _merge_entries(base: list[dict], mine: list[dict], theirs: list[dict], key: str) -> list[dict]:
    """*theirs* with the entries *mine* changed against *base* applied. See the module docstring.

    Falls back to *mine* whole when any of the three can't be keyed, which is what every
    write did before the merge.
    """
    b, m, t = _keyed(base, key), _keyed(mine, key), _keyed(theirs, key)
    if b is None or m is None or t is None:
        return list(mine)
    merged: dict[str, dict] = {}
    for name, entry in t.items():
        if name in b and name not in m:
            continue  # removed here
        merged[name] = m[name] if name in m and m[name] != b.get(name) else entry
    for name, entry in m.items():
        if name not in b and name not in t:
            merged[name] = entry  # added here
    # Shared entries keep this store's order if it reordered them, else the file's.
    shared = [name for name in m if name in b]
    reordered = shared != [name for name in b if name in m]
    lead = list(m) if reordered else list(t)
    order = [name for name in lead if name in merged]
    order += [name for name in m if name in merged and name not in order]
    order += [name for name in merged if name not in order]
    return [merged[name] for name in order]


def _as_dict_list(value: Any) -> list[dict]:
    """*value* as a list of dicts, dropping anything else.

    The file is user-editable JSON, so a string where a list belongs must degrade to
    "nothing stored" rather than reaching the GUI as an account with no username.
    """
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, dict)]
