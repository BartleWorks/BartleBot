"""The window's bottom rule: when it shows, and what color the window's edge is (#271).

Whether it lands on screen when maximized is asked of a real window in
``test_native_frame.py``, which already builds one.
"""

from __future__ import annotations

import pytest

from bartlebot.game.game_sizing import BOTTOM_EDGE_PX
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.title_bar.bottom_edge import BottomEdge, bottom_rule_shown, edge_color


@pytest.mark.parametrize(
    ("maximized", "full_screen", "border_drawn", "shown"),
    [
        (False, False, True, False),  # restored on Windows 11: Windows' border is the edge
        (False, False, False, True),  # restored with no border color to set: Windows 10
        (True, False, True, True),  # maximized: no border at all
        (False, True, True, True),  # full screen: no border either
    ],
)
def test_the_rule_shows_wherever_windows_draws_no_border(maximized, full_screen, border_drawn, shown):
    assert bottom_rule_shown(maximized=maximized, full_screen=full_screen, border_drawn=border_drawn) is shown


def test_the_edge_wears_the_accent_while_the_window_is_active():
    assert edge_color(True) == THEME.qcolor("accent")
    assert edge_color(False) == THEME.qcolor("border")


def test_the_rule_is_as_tall_as_the_floor_arithmetic_counts(qapp):
    assert BottomEdge().height() == BOTTOM_EDGE_PX
