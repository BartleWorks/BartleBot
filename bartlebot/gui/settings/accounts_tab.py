"""Settings tab: Game, as BartleBot draws it: core's tab with character selection under *On launch*.

The tab, its two card columns and the card actions every surface that manages an account
or a launch profile calls (``add_``/``edit_``/``delete_account`` and the rest) moved to
core's ``bartleby/qt/settings/accounts_tab.py`` in Stage 11 of #167, so BartleBuddy edits
the same accounts with the same editors. What stays is BartleBot's one launch option of
its own: pressing Play on a character after login, which its client manager does and
crash recovery relies on.
"""

from __future__ import annotations

from bartleby.qt.settings.accounts_tab import AccountsTab as CoreAccountsTab
from bartleby.qt.settings.rows import SettingRow
from PyQt6.QtWidgets import QCheckBox

from bartlebot.settings_manager import SettingsManager, default_for


class AccountsTab(CoreAccountsTab):
    """The Game tab, with *Automatically select a character* between the resolution and quick friend teleport."""

    def product_launch_rows(self) -> list[SettingRow]:
        # Done to a client on its way up, which is why it is on this tab and not the Bot
        # one: it is not a level of any bot option's resolution, and a bot that never
        # starts still gets it.
        self._auto_select_cb = QCheckBox()
        self._auto_select_cb.setChecked(SettingsManager.instance().auto_select_character)
        return [
            SettingRow(
                "Automatically select a character",
                "Press Play on the most recently played character after login.",
                self._auto_select_cb,
                default=default_for("general", "auto_select_character"),
                details=(
                    "Crash recovery also uses this setting. When off, each game window waits at character "
                    "select for you to pick."
                ),
            )
        ]

    def apply(self) -> None:
        """Write the launch options, character selection among them; see core's ``apply``."""
        super().apply()
        SettingsManager.instance().auto_select_character = self._auto_select_cb.isChecked()
