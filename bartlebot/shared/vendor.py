"""The publisher's name, defined once, wherever it reaches the user's machine.

Two names, and they answer different questions:

============  ==========================================  =================
name          where it appears                            value
============  ==========================================  =================
organization  the GitHub org that owns every repo         ``BartleWorks``
vendor        ``%APPDATA%/<vendor>/``, Credential Manager  ``Bartleby``
============  ==========================================  =================

They have no reason to match. The organization name is developer-facing and lives in
URLs; the vendor string is user-facing and lives on the user's disk and in their
credential store, where changing it is a data migration rather than a search and
replace. Keeping them apart is what confines the blast radius of a rename: repo URLs,
pinned dependency revisions and issue links carry none of it.

WHY THIS IS A MODULE AND NOT THREE LITERALS

Every value below used to be spelled out at its point of use — three keyring services
named after one product, and one ``%APPDATA%`` directory each for the settings, the
shared accounts and the strategy root. Three products under one publisher cannot each
pick their own: the user then sees a Credential Manager listing entries under a product
name while two products use them, and uninstalling the one whose name is on the entry
looks like it should take them. Defined here and imported, a future product cannot
drift by copying a literal.

There is no migration anywhere for the rename, and that is the point of doing it before
1.0.0: no user has data under the old names yet. Afterwards every install would need
read-old/write-new code that has to live in the tree indefinitely, because Credential
Manager carries no version marker to say when it is safe to drop.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The publisher, as it appears on the user's disk and in their credential store.
VENDOR = "Bartleby"

#: This product, as it appears beneath the vendor directory. A sibling product sets its
#: own; nothing here is BartleBot-specific except this one string.
PRODUCT = "BartleBot"


def vendor_dir() -> Path:
    """``%APPDATA%/Bartleby`` — the root every product by this publisher writes under.

    Data *shared* between products sits directly here (``accounts.json``); data belonging
    to one product goes under :func:`product_dir`. Falls back to the home directory where
    ``APPDATA`` is unset, which is every non-Windows machine the test suite runs on.
    """
    base = os.environ.get("APPDATA") or Path.home()
    return Path(base) / VENDOR


def local_vendor_dir() -> Path:
    """``%LocalAppData%/Bartleby`` — the vendor's root for what belongs to this machine alone.

    Caches and downloads: whatever describes this machine, or is too large to roam, and
    can be rebuilt. The user's own data goes under :func:`vendor_dir`. Falls back to the
    home directory where ``LOCALAPPDATA`` is unset, as :func:`vendor_dir` does.
    """
    base = os.environ.get("LOCALAPPDATA") or Path.home()
    return Path(base) / VENDOR


def product_dir(product: str = PRODUCT) -> Path:
    """``%APPDATA%/Bartleby/<product>`` — one product's own data directory."""
    return vendor_dir() / product


def keyring_service(suffix: str = "") -> str:
    """The Credential Manager service name for a *suffix* of the vendor namespace.

    ``keyring_service()`` is the account passwords, keyed by username;
    ``keyring_service("roster")`` and ``keyring_service("identity")`` are namespaces of
    their own. Separate services rather than one, because the password service's keys are
    usernames chosen by the user: a shared namespace would let an account named like our
    own key collide with it.
    """
    return f"{VENDOR}-{suffix}" if suffix else VENDOR
