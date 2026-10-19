"""The rule along the window's bottom, for when Windows draws no border there.

A tester took the window for one cut off (#271): the bot library's last card ran into the
bottom edge, and below it sat a taskbar of the same near-black. Nothing said where the
window stopped. ``NativeFrame.set_border_color`` gives a restored window a border that
does; this is the same line for the cases it cannot reach, so the window always has
exactly one bottom edge and never two.

A maximized or full-screen window has no border at all, and Windows 10 has no border
color to set. A rule rather than a status bar, because vertical space is what this app
is shortest of: one pixel says where the window ends as well as twenty-two would.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QSizePolicy, QWidget

from bartlebot.game.game_sizing import BOTTOM_EDGE_PX
from bartlebot.gui.theming.theme import THEME


def bottom_rule_shown(*, maximized: bool, full_screen: bool, border_drawn: bool) -> bool:
    """Whether the window needs its own bottom rule: when Windows is not drawing a border."""
    return maximized or full_screen or not border_drawn


def edge_color(active: bool) -> QColor:
    """The window's edge, whoever draws it: the accent while active, ``border`` while not.

    Windows' own "accent color on borders" does the same. Plain ``border`` was tried for
    both and lost: against the near-black taskbar it read no better than Windows' gray.
    """
    return THEME.qcolor("accent" if active else "border")


class BottomEdge(QWidget):
    """A full-width line in :func:`edge_color`, read at paint time so a theme switch needs nothing.

    The window repaints it when it is activated or deactivated (``MainWindow._sync_window_edge``).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(BOTTOM_EDGE_PX)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.fillRect(self.rect(), edge_color(self.isActiveWindow()))
        painter.end()
