"""Bar A's authoring buttons (#81): BartleBot's list on core's action bar, and what each says.

How the bar groups, centers and reports a press is core's (``tests/qt/game_embed/test_game_view_panel.py``).
"""

from __future__ import annotations

from bartleby.qt.theming.icon_utils import ICONS_DIR
from bartleby.qt.widgets.buttons.icon_button import IconTint

from bartlebot.clients.authoring_tools import AuthoringAction
from bartlebot.gui.game_embed.game_view_panel import AUTHORING_GROUPS, AuthoringBar, GameViewPanel


def test_the_groups_hold_every_action_once_in_bar_order():
    """Bar A, the Settings tab and the default F-keys all follow AuthoringAction's order."""
    flat = [action.id for group in AUTHORING_GROUPS for action in group]
    assert flat == [a.value for a in AuthoringAction]


def test_every_action_s_icon_is_in_core_s_set():
    assert all((ICONS_DIR / f"{a.icon}.svg").is_file() for group in AUTHORING_GROUPS for a in group)


def test_the_panel_s_bar_is_the_authoring_bar(qapp):
    panel = GameViewPanel()
    assert isinstance(panel.authoring_bar, AuthoringBar)
    assert panel.authoring_bar is panel.action_bar


def test_a_disabled_button_says_why_and_every_button_has_a_tooltip(qapp):
    bar = AuthoringBar()
    for action in AuthoringAction:
        assert bar.button(action).toolTip()
    # No clients: everything is disabled, and says what is needed.
    assert "Unavailable" in bar.button(AuthoringAction.XYZ_SYNC).toolTip()
    assert "two" in bar.button(AuthoringAction.XYZ_SYNC).toolTip()
    bar.show_state(speed_on=False, combat_on=False, busy=False, run_active=True, clients=2, hotkeys={})
    assert "bot is running" in bar.button(AuthoringAction.TOGGLE_SPEED).toolTip()
    bar.show_state(speed_on=False, combat_on=False, busy=False, run_active=False, clients=2, hotkeys={})
    assert "Unavailable" not in bar.button(AuthoringAction.XYZ_SYNC).toolTip()


def test_a_toggle_that_is_on_is_drawn_in_the_accent_names_its_key_and_stays_pressable(qapp):
    bar = AuthoringBar()
    bar.show_state(
        speed_on=True,
        combat_on=False,
        busy=False,
        run_active=False,
        clients=0,
        hotkeys={AuthoringAction.TOGGLE_SPEED.value: "F3"},
    )
    speed = bar.button(AuthoringAction.TOGGLE_SPEED)
    assert bar.is_checked(AuthoringAction.TOGGLE_SPEED)
    assert speed.tint is IconTint.ACCENT
    assert speed.isEnabled()
    assert speed.toolTip().startswith("Speed (on) [F3] — ")
    assert bar.button(AuthoringAction.TOGGLE_COMBAT).tint is IconTint.DEFAULT
