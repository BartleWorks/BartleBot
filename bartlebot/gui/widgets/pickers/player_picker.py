"""Choosing which connected player a button acts on: Fill, Record, and anything like them.

One player needs no choice, so the button acts at once. With two or more it opens a
"<verb> from Player N" menu under itself, and its label carries a ``▾`` to say so.
Every such button in the node editor used to write this out for itself.
"""

from __future__ import annotations

from collections.abc import Callable

from bartleby.qt.menu_utils import popup_menu
from PyQt6.QtWidgets import QWidget

#: What a label gains when a click opens the player menu instead of acting.
MENU_MARK = " ▾"


def picker_label(text: str, players: int) -> str:
    """*text*, marked when a click will ask which player: two or more are connected."""
    return f"{text}{MENU_MARK}" if players >= 2 else text


def pick_player(
    anchor: QWidget, players: int, verb: str, on_pick: Callable[[int], None], *, preposition: str = "from"
) -> None:
    """Call *on_pick* with the chosen player's index, asking only when there is a choice.

    *preposition* is for a verb that goes the other way: "Apply to Player 2".

    Nothing happens with no player connected; the caller says why, if it needs to.
    """
    if players <= 0:
        return
    if players == 1:
        on_pick(0)
        return
    menu = popup_menu(anchor)
    for i in range(players):
        action = menu.addAction(f"{verb} {preposition} Player {i + 1}")
        action.triggered.connect(lambda _checked=False, idx=i: on_pick(idx))
    menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
