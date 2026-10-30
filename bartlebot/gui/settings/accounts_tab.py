"""Settings tab: Game, as BartleBot draws it: core's tab, saying what else reads character selection.

The tab, its two card columns, its launch rows and the card actions every surface that
manages an account or a launch profile calls (``add_``/``edit_``/``delete_account`` and the
rest) are core's ``bartleby/qt/settings/accounts_tab.py``, so BartleBuddy edits the same
accounts with the same editors. What stays is the one thing only BartleBot has to say:
crash recovery relies on pressing Play after login too.
"""

from __future__ import annotations

from bartleby.qt.settings.accounts_tab import AccountsTab as CoreAccountsTab


class AccountsTab(CoreAccountsTab):
    """The Game tab, with crash recovery named on *Automatically select a character*."""

    AUTO_SELECT_DETAILS = (
        "Crash recovery also uses this setting. When off, each game window waits at character select for you to pick."
    )
