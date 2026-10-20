"""The pill icon on a tile is the one part of it that acts on a click (#258).

It is painted rather than a widget, so the pointer and the tooltip a button would bring
are the tile's to give: a pointer over the icon and an arrow everywhere else, and a
tooltip saying what the icon does. ``ClientTile`` needs a real game window, so the
detached and pending tiles stand in for it; all three share ``_PillIconHover``.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QHelpEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QToolTip

from bartlebot.gui.game_embed.client_tile import DetachedTile, PendingTile


def _shown(qtbot, tile):
    """Shown and painted once, since the paint is what places the icon."""
    qtbot.addWidget(tile)
    tile.resize(400, 300)
    tile.show()
    tile.grab()
    return tile


@pytest.fixture
def detached(qtbot):
    return _shown(qtbot, DetachedTile("Player 1"))


def _move(tile, pos: QPoint) -> None:
    tile.mouseMoveEvent(
        QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(pos),
            QPointF(tile.mapToGlobal(pos)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


@pytest.fixture
def tooltip_at(monkeypatch):
    """What a hover at a point would show, read from showText: the suite never shows one."""

    def at(tile, pos: QPoint) -> str:
        shown: list[str] = []
        monkeypatch.setattr(QToolTip, "showText", lambda _pos, text, *args: shown.append(text))
        QApplication.sendEvent(tile, QHelpEvent(QEvent.Type.ToolTip, pos, tile.mapToGlobal(pos)))
        return shown[-1] if shown else ""

    return at


def _icon(tile) -> QPoint:
    return tile._icon_rect.center()


def _body(tile) -> QPoint:
    return QPoint(tile.width() // 2, tile.height() - 20)


def test_the_icon_shows_a_pointer(detached):
    _move(detached, _icon(detached))
    assert detached.cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_the_rest_of_the_tile_shows_an_arrow(detached):
    _move(detached, _icon(detached))
    _move(detached, _body(detached))
    assert detached.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_leaving_from_the_icon_puts_the_arrow_back(detached):
    """Or the pointer is what the tile shows on the way back in, until the first move."""
    _move(detached, _icon(detached))
    detached.leaveEvent(QEvent(QEvent.Type.Leave))
    assert detached.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_the_icon_says_what_it_does(detached, tooltip_at):
    assert tooltip_at(detached, _icon(detached)) == "Re-attach this window"


def test_the_rest_of_the_tile_has_no_tooltip(detached, tooltip_at):
    assert tooltip_at(detached, _body(detached)) == ""


def test_a_running_launch_has_no_icon_and_no_pointer(qtbot):
    tile = _shown(qtbot, PendingTile("Player 1"))
    _move(tile, QPoint(tile.width() // 2, 15))
    assert tile.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_a_failed_launch_offers_its_dismiss_cross(qtbot, tooltip_at):
    tile = PendingTile("Player 1")
    tile.set_status("Launch failed", None, failed=True)
    _shown(qtbot, tile)

    _move(tile, _icon(tile))

    assert tile.cursor().shape() == Qt.CursorShape.PointingHandCursor
    assert tooltip_at(tile, _icon(tile)) == "Dismiss this failed launch"
