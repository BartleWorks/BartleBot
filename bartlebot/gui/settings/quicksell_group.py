"""Quicksell's settings card, shown on Settings › Bot and on setup's Quicksell page (#230).

One builder for both, so the page asks exactly what the tab shows, row for row: Enable
Quicksell heading Sell Pets, Sell Socket Wrenches, Sell Skeleton Keys and Always keep.
Both write through :mod:`bartlebot.options.quicksell_setup`. The tab adds its Show
Quicksell explanation row; the page leaves it out, since the page turns the explanation
off itself.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtWidgets import QCheckBox, QLineEdit

from bartlebot.gui.settings.rows import SettingGroup, SettingRow
from bartlebot.gui.widgets.inputs.list_text import format_list_text, parse_list_text
from bartlebot.nodes.utility.quicksell import USER_KEEP_LIST_KEY
from bartlebot.options.quicksell_setup import (
    ENABLED_KEY,
    SELL_PETS_KEY,
    SELL_SKELETON_KEYS_KEY,
    SELL_SOCKET_WRENCHES_KEY,
    SellFlags,
    current_flags,
    current_keep_list,
    write_keep_list,
    write_sell_flags,
)
from bartlebot.settings_manager import SettingsManager, default_for

#: The row titles, for whoever finds a row the way a reader would.
ENABLE_TITLE = "Quicksell"
SELL_PETS_TITLE = "Sell pets"
SELL_SOCKET_WRENCHES_TITLE = "Sell Socket Wrenches"
SELL_SKELETON_KEYS_TITLE = "Sell Skeleton Keys"
KEEP_LIST_TITLE = "Always keep"


def _check_box(checked: bool) -> QCheckBox:
    box = QCheckBox()
    box.setChecked(checked)
    return box


class QuicksellSettings:
    """The quicksell group's rows, on the values *settings* holds now.

    *extra_rows* go after the three sell switches and before Always keep, which stays
    last: tall controls go last in their card. With *foldable* False the group is always
    open and has no chevron.
    """

    def __init__(
        self, settings: SettingsManager, *, extra_rows: Sequence[SettingRow] = (), foldable: bool = True
    ) -> None:
        flags = current_flags(settings)
        self._enabled = _check_box(flags.enabled)
        enable = SettingRow(
            ENABLE_TITLE,
            "Sell drops so backpacks never fill.",
            self._enabled,
            default=default_for("general", ENABLED_KEY),
            details=(
                "When off, a full backpack overflows into the character's bank. Once the bank is full too, "
                "only Crowns items are kept."
            ),
        )
        self._sell_pets = _check_box(flags.sell_pets)
        sell_pets = SettingRow(
            SELL_PETS_TITLE,
            "Turn off to keep pets.",
            self._sell_pets,
            default=default_for("general", SELL_PETS_KEY),
            details="The keep list can't protect a pet.",
        )
        self._sell_socket_wrenches = _check_box(flags.sell_socket_wrenches)
        sell_wrenches = SettingRow(
            SELL_SOCKET_WRENCHES_TITLE,
            'Turn off to keep any item with "Socket Wrench" in its name.',
            self._sell_socket_wrenches,
            default=default_for("general", SELL_SOCKET_WRENCHES_KEY),
        )
        self._sell_skeleton_keys = _check_box(flags.sell_skeleton_keys)
        sell_keys = SettingRow(
            SELL_SKELETON_KEYS_TITLE,
            'Turn off to keep any item with "Skeleton Key" in its name.',
            self._sell_skeleton_keys,
            default=default_for("general", SELL_SKELETON_KEYS_KEY),
        )
        self._keep_list = QLineEdit(format_list_text(current_keep_list(settings)))
        self._keep_list.setPlaceholderText("e.g. Amber, Pearl")
        keep = SettingRow(
            KEEP_LIST_TITLE,
            "Which items every bot keeps, besides its own keep list.",
            self._keep_list,
            default=format_list_text(default_for("general", USER_KEEP_LIST_KEY) or []),
            details="Separate items with commas. Any item whose name contains an entry is kept, except pets.",
            wide=True,
        )
        self._group = SettingGroup(
            "quicksell", enable, [sell_pets, sell_wrenches, sell_keys, *extra_rows, keep], foldable=foldable
        )

    @property
    def group(self) -> SettingGroup:
        return self._group

    @property
    def keep_list_edit(self) -> QLineEdit:
        return self._keep_list

    def flags(self) -> SellFlags:
        """The four switches as the rows show them now."""
        return SellFlags(
            enabled=self._enabled.isChecked(),
            sell_pets=self._sell_pets.isChecked(),
            sell_socket_wrenches=self._sell_socket_wrenches.isChecked(),
            sell_skeleton_keys=self._sell_skeleton_keys.isChecked(),
        )

    def keep_list(self) -> list[str]:
        return parse_list_text(self._keep_list.text())

    def write(self, settings: SettingsManager) -> None:
        """Set what the rows show in memory, as Settings' OK does; the caller saves."""
        write_sell_flags(settings, self.flags())
        write_keep_list(settings, self.keep_list())
