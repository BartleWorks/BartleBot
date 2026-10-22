"""The Default Safety Delay and Favorite realm rows, on Settings › Bot and setup's realm page (#231).

One builder for both, so the page asks exactly what the tab shows. The tab puts each row
under its own heading, Timing and Realms; the page shows the two together. Both write
through ``SettingsManager``'s ``default_safety_delay`` and ``favorite_realm``, the page by
way of :func:`bartlebot.options.realm_delay_setup.record_realm_delay`.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QDoubleSpinBox

from bartlebot.gui.settings.rows import SettingRow
from bartlebot.gui.widgets.inputs.realm_combo import RealmCombo
from bartlebot.options.bot_options import FAVORITE_REALM_DESCRIPTION, SAFETY_DELAY_DESCRIPTION
from bartlebot.settings_manager import SettingsManager, default_for

SAFETY_DELAY_TITLE = "Default Safety Delay"
FAVORITE_REALM_TITLE = "Favorite realm"


def safety_delay_row(settings: SettingsManager) -> SettingRow:
    """Seconds, 0 to 30 by 0.5, on the stored value. The row's control is the spin box."""
    box = QDoubleSpinBox()
    box.setRange(0.0, 30.0)
    box.setSingleStep(0.5)
    box.setDecimals(1)
    box.setSuffix(" sec")
    box.setValue(settings.default_safety_delay)
    return SettingRow(
        SAFETY_DELAY_TITLE,
        "How long the game server gets before an action counts as failed.",
        box,
        default=default_for("general", "default_safety_delay"),
        # The option's own text: one value, described once (see settings/CLAUDE.md).
        details=SAFETY_DELAY_DESCRIPTION,
    )


def favorite_realm_row(settings: SettingsManager, realm: str | None = None) -> SettingRow:
    """The realm picker, on *realm*, or the stored realm when None. The row's control is the combo."""
    combo = RealmCombo()
    combo.set_value(settings.favorite_realm if realm is None else realm)
    return SettingRow(
        FAVORITE_REALM_TITLE,
        "Where Change Realm and Sync in Zone send players.",
        combo,
        default=default_for("general", "favorite_realm"),
        details=FAVORITE_REALM_DESCRIPTION
        + (
            " "
            "Launched players also get the game's Remember Realm option, so a relogged player returns "
            "to the realm it left."
        ),
    )
