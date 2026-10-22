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
REFILL_POTIONS_DETAILS = "Each refill costs gold and a trip to the Commons, and players come back to where they were."


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
            "Every account must be on the others' friends lists. Players regroup after a crash, and "
            "group members share wisps and take the first turn in battle."
        ),
    )


def refill_potions_row(settings: SettingsManager) -> SettingRow:
    """On the stored value. The row's control is the check box."""
    return SettingRow(
        REFILL_POTIONS_TITLE,
        "Buy potions from Hilda Brewer when a player runs out.",
        _check_box(refill_potions(settings)),
        default=default_for("general", "default_refill_potions"),
        details=REFILL_POTIONS_DETAILS,
    )
