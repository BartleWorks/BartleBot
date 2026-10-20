"""Whether setup's Accounts page needs asking, for every product by this vendor (epic #215, Stage 3).

The page adds accounts through the same editor Settings › Game uses, and each Add writes
``accounts.json`` there and then, so the page itself has nothing to store but the fact that
it was answered. That fact matters only for a user who chose no account: one with an
account, added by either product, is never asked.

Left empty is a real answer. Clients started by hand can still be hooked, and accounts can
be added later, so :data:`ANSWERED_KEY` keeps a user who chose none from being asked again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bartlebot.shared.account_store import AccountStore
    from bartlebot.shared.preferences import PreferenceStore

#: Set once the Accounts page has been answered, with or without an account, by either product.
ANSWERED_KEY = "setup.accounts_answered"


def accounts_answered(prefs: PreferenceStore, store: AccountStore) -> bool:
    """Whether the Accounts page needs no asking: an account exists, or the page was answered with none."""
    return bool(store.accounts) or bool(prefs.get(ANSWERED_KEY))


def has_team(store: AccountStore) -> bool:
    """Whether the user has two or more accounts: what a question about a team needs to mean anything (#264)."""
    return len(store.accounts) >= 2


def record_accounts_answered(prefs: PreferenceStore) -> bool:
    """Record the Accounts page answered. Whether it reached disk."""
    return prefs.set(ANSWERED_KEY, True)
