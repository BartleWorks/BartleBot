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
third means both apps are running. This store does not yet solve concurrent writers --
it loads once and replaces the whole file, so a second writer's updates are lost -- but
it is shaped so that it *can be* solved here and nowhere else. Every write goes through
:meth:`AccountStore.set_accounts` or :meth:`AccountStore.set_launch_profiles`, never
through a caller pulling a list, mutating a dict inside it and assigning it back. With
the writes funnelled into two methods, atomic replace and reload-before-write are a
later change in one file rather than an audit of every call site.

The writes are already atomic in the crash sense: the file is written to a temporary
beside it and renamed over, so an interrupted save leaves the previous file whole rather
than a half-written one that reads as "no accounts".
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from bartlebot.shared.vendor import vendor_dir

logger = logging.getLogger(__name__)

#: Schema version of the stored record. Bumped when the shape changes in a way a reader
#: has to know about.
SCHEMA_VERSION = 1

#: The filename beneath the vendor directory. Not a product name: this is the shared one.
FILENAME = "accounts.json"


class AccountStore:
    """The vendor-level accounts file, loaded once and written whole.

    Constructed with its directory rather than finding one, so a test -- or a second
    product with its own layout -- can point it anywhere without patching a module
    global.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._path = (directory or vendor_dir()) / FILENAME
        self._accounts: list[dict] = []
        self._launch_profiles: list[dict] = []
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
        if save:
            self.save()

    def set_launch_profiles(self, profiles: list[dict], save: bool = True) -> None:
        """Replace the launch profiles, writing them out unless *save* is False."""
        self._launch_profiles = list(profiles)
        if save:
            self.save()

    def save(self) -> None:
        """Write the record, atomically. Never raises: a failed write is logged.

        Called from GUI slots and from the client loop, where an exception escaping
        looks like the launch screen breaking. The data is still in memory either way,
        so the next successful save writes it.
        """
        record = {
            "version": SCHEMA_VERSION,
            "accounts": self._accounts,
            "launch_profiles": self._launch_profiles,
        }
        tmp = self._path.with_suffix(".json.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(record, fh, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            logger.exception("Could not write the shared accounts file at %s", self._path)

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
        self._accounts = _as_dict_list(record.get("accounts"))
        self._launch_profiles = _as_dict_list(record.get("launch_profiles"))

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
        self.save()
        logger.info(
            "Moved %d account(s) and %d launch profile(s) into the shared store at %s",
            len(accounts),
            len(launch_profiles),
            self._path,
        )
        return True


def _as_dict_list(value: Any) -> list[dict]:
    """*value* as a list of dicts, dropping anything else.

    The file is user-editable JSON, so a string where a list belongs must degrade to
    "nothing stored" rather than reaching the GUI as an account with no username.
    """
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, dict)]
