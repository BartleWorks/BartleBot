"""The Default Safety Delay and Favorite realm rows, built once for Settings › Bot and setup (#231)."""

from __future__ import annotations

from bartlebot.gui.settings.realm_delay_rows import (
    FAVORITE_REALM_TITLE,
    SAFETY_DELAY_TITLE,
    favorite_realm_row,
    safety_delay_row,
)
from bartlebot.options.bot_options import SAFETY_DELAY_DESCRIPTION
from bartlebot.settings_manager import SettingsManager


def test_the_delay_row_is_zero_to_thirty_seconds_by_half_a_second_on_the_stored_value(qapp):
    sm = SettingsManager.instance()
    sm.default_safety_delay = 4.5
    row = safety_delay_row(sm)
    box = row.control

    assert row.title == SAFETY_DELAY_TITLE
    assert (box.minimum(), box.maximum(), box.singleStep(), box.value()) == (0.0, 30.0, 0.5, 4.5)
    assert row.details.text() == SAFETY_DELAY_DESCRIPTION


def test_the_realm_row_opens_on_the_stored_realm_or_the_one_given(qapp):
    sm = SettingsManager.instance()
    sm.favorite_realm = "Balestrom"

    assert favorite_realm_row(sm).title == FAVORITE_REALM_TITLE
    assert favorite_realm_row(sm).control.get_value() == "Balestrom"
    assert favorite_realm_row(sm, "Wraith").control.get_value() == "Wraith"
