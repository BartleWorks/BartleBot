"""The Automatically group players and Refill potions rows, on Settings › Bot and setup's Options page (#232).

One builder for both, so the page asks exactly what the tab shows, with the cost each
carries in its description: grouping needs every account on the others' friends lists,
and a refill spends gold. Both write through
:func:`bartlebot.options.custom_setup.record_options` on the page and the tab's OK.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QCheckBox

from bartlebot.gui.settings.rows import SettingRow
from bartlebot.options.custom_setup import refill_potions
from bartlebot.settings_manager import SettingsManager, default_for

AUTO_GROUP_TITLE = "Automatically group players"
REFILL_POTIONS_TITLE = "Refill potions"


def _check_box(checked: bool) -> QCheckBox:
    box = QCheckBox()
    box.setChecked(checked)
    return box


def auto_group_row(settings: SettingsManager) -> SettingRow:
    """On the stored value. The row's control is the check box."""
    return SettingRow(
        AUTO_GROUP_TITLE,
        "Put every player in one in-game group when a run starts.",
        _check_box(settings.auto_group_players),
        default=default_for("general", "auto_group_players"),
        details=(
            "And again after one crashes or is closed. Group members get wisps after combat "
            "and take the first turn in battle, and crashing drops a player out of the group. "
            "Requires every account to be on the others' friends lists in-game."
        ),
    )


def refill_potions_row(settings: SettingsManager) -> SettingRow:
    """On the stored value. The row's control is the check box."""
    return SettingRow(
        REFILL_POTIONS_TITLE,
        "Buy potions from Hilda Brewer when a player runs out.",
        _check_box(refill_potions(settings)),
        default=default_for("general", "default_refill_potions"),
        details=(
            "Players go to the Commons, buy, then recall to where they were. Buying costs "
            "gold. Off, they keep farming without potions."
        ),
    )
