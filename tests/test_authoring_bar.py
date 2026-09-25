"""Bar A's authoring buttons (#81) stay centred whatever the captions beside them say."""

from __future__ import annotations

import pytest

from bartlebot.gui.game_embed.game_view_panel import GameViewPanel


def _bar_centre_offset(panel: GameViewPanel) -> int:
    """How far the buttons' centre sits from the panel's, in pixels."""
    panel.layout().activate()
    panel.authoring_bar.parentWidget().layout().activate()
    bar = panel.authoring_bar
    centre = bar.mapTo(panel, bar.rect().center()).x()
    return centre - panel.width() // 2


@pytest.mark.parametrize(
    "node_title",
    ["", "Fight", "A node title long enough to be wider than its half of the bar, and then some more"],
)
def test_the_buttons_are_centred_with_or_without_a_node_title(qapp, node_title):
    panel = GameViewPanel()
    panel.resize(900, 400)
    panel.show()
    panel.set_client_summary(4, "Four Wizards")
    panel.set_active_node(node_title)
    qapp.processEvents()

    assert abs(_bar_centre_offset(panel)) <= 2
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
