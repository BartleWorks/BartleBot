"""Bar A's authoring buttons (#81): grouped by what they do, and centered whatever the captions say."""

from __future__ import annotations

import pytest

from bartlebot.clients.authoring_tools import AuthoringAction
from bartlebot.gui.game_embed.game_view_panel import AUTHORING_GROUPS, AuthoringBar, GameViewPanel


def _bar_center_offset(panel: GameViewPanel) -> int:
    """How far the buttons' center sits from the panel's, in pixels."""
    panel.layout().activate()
    panel.authoring_bar.parentWidget().layout().activate()
    bar = panel.authoring_bar
    center = bar.mapTo(panel, bar.rect().center()).x()
    return center - panel.width() // 2


@pytest.mark.parametrize(
    "node_title",
    ["", "Fight", "A node title long enough to be wider than its half of the bar, and then some more"],
)
def test_the_buttons_are_centered_with_or_without_a_node_title(qapp, node_title):
    panel = GameViewPanel()
    panel.resize(900, 400)
    panel.show()
    panel.set_client_summary(4, "Four Wizards")
    panel.set_active_node(node_title)
    qapp.processEvents()

    assert abs(_bar_center_offset(panel)) <= 2
    panel.close()


def test_a_run_starting_does_not_move_the_buttons(qapp):
    panel = GameViewPanel()
    panel.resize(900, 400)
    panel.show()
    qapp.processEvents()
    idle = panel.authoring_bar.mapTo(panel, panel.authoring_bar.rect().topLeft()).x()

    panel.set_active_node("Go to zone")
    qapp.processEvents()
    panel.layout().activate()
    running = panel.authoring_bar.mapTo(panel, panel.authoring_bar.rect().topLeft()).x()

    assert running == idle
    panel.close()


def test_the_groups_hold_every_action_once_in_bar_order():
    """Bar A, the Settings tab and the default F-keys all follow AuthoringAction's order."""
    flat = [action for group in AUTHORING_GROUPS for action in group]
    assert flat == list(AuthoringAction)


def test_a_divider_sits_between_each_group_and_nowhere_else(qapp):
    bar = AuthoringBar()
    bar.resize(bar.sizeHint())
    bar.layout().activate()

    def left(w):
        return w.geometry().left()

    def right(w):
        return w.geometry().right()

    groups = [[bar.button(a) for a in group] for group in AUTHORING_GROUPS]
    dividers = bar.dividers
    assert len(dividers) == len(AUTHORING_GROUPS) - 1
    for before, divider, after in zip(groups, dividers, groups[1:]):
        assert right(before[-1]) < left(divider) and right(divider) < left(after[0])

    within = left(groups[1][1]) - right(groups[1][0])
    across = left(groups[1][0]) - right(groups[0][-1])
    assert across > within
