"""Pay Qt's first offscreen render at a moment nobody is waiting on it.

THE STALL

The first time a card is dragged after the app starts, it sticks: the pointer moves,
and for a fifth of a second the card does not. Every later drag in the same session is
smooth, on that card and on every other one, so nothing about the gesture is wrong --
something behind it costs 150-200ms exactly once per process, and the first drag is
what happens to pay it.

What triggers it is the lift. ``CardFrame.set_dragging`` attaches a
``QGraphicsDropShadowEffect``, and a widget with an effect on it is no longer painted
straight into the window: Qt renders it into an offscreen surface first. That is
``QWidget::render``'s path, and before it paints anything Qt walks the *whole top-level
window* delivering the move and resize events it has been holding back --
``sendPendingMoveAndResizeEvents``. Qt withholds them from hidden widgets, so they
accumulate: every tab the user has not opened, every menu, every dialog page built at
startup and never shown is sitting on a layout that has never run. The first offscreen
render anywhere in the window runs all of them at once, on the main thread, between two
mouse-move events.

It is measurable and unambiguous: rendering one card into a **1x1** pixmap costs the
same 200ms as rendering it at full size, because the cost is not the painting. Do it
once and the next one is 0ms.

THE FIX

Do it on purpose, early, into a 1x1 pixmap -- which is the whole of this module. The
work itself is not waste and cannot be skipped; those layouts have to run before
anything renders offscreen. What can be chosen is *when*, and a widget that has just
been shown is a far better moment than the first pixel of a gesture.

Per window rather than per process, because the flush is per window: a card dragged in
the Settings dialog flushes the dialog's tree, not the main window's, so warming one
says nothing about the other. The flag lives on the window as a dynamic property, so it
dies with the window and there is no registry of possibly-deleted widgets to keep.

Once per window means once. The flag records that the layouts outstanding *when it
fired* have run -- not that the window can never stall again. Pending move and resize
events accrue for as long as widgets are built hidden, so a dialog page constructed
lazily long after start-up re-arms the debt, and the flag guarantees it will not be
warmed a second time. That is a trade, not an oversight: nearly everything here is built
during start-up, and a warm-up on a repeating timer would pay an unbounded cost to chase
a shrinking one. A stall on some later first gesture is this shape -- not a broken
module.

This is not only about cards. Anything that renders a widget offscreen pays the same
toll on its first go -- ``QWidget.grab()`` for a ``QDrag`` pixmap, as
``combat_order_widget`` and ``favorites_bar`` do, and any other graphics effect. A
surface with one of those in it can call this the way ``DragReorder.watch_for_show``
does. None of them do today, so each still pays on its first gesture unless a card list
in the same window has already paid it. If that starts to matter, warm the *window*
rather than adding a call per widget class -- the debt belongs to the top-level, and
``DragReorder`` is only the volunteer that happens to sit on every surface that drags
cards.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QWidget

#: Dynamic property marking a window whose offscreen render path has been warmed.
WARMED_PROPERTY = "bartlebot_offscreen_warmed"


def warm_offscreen_render(widget: QWidget) -> None:
    """Force the pending-layout flush for *widget*'s window now. Once per window.

    *widget* has to be **visible**, and this returns without marking anything when it is
    not. ``QWidget::render`` takes a different path for a hidden widget -- it unhides it
    behind the caller's back, lays out its hidden ancestors and hides it again -- which
    is a great deal more than the flush this is here for, on a widget the user cannot
    see the result on anyway. A caller that gets it wrong therefore skips the warm-up
    rather than doing something surprising with it, and the next show tries again.
    """
    window = widget.window()
    if window is None or window.property(WARMED_PROPERTY) or not widget.isVisible():
        return
    window.setProperty(WARMED_PROPERTY, True)
    # 1x1 on purpose: the render is a means of reaching Qt's flush, and every pixel past
    # the first is paint nobody will ever look at.
    target = QPixmap(1, 1)
    target.fill(Qt.GlobalColor.transparent)
    widget.render(target)
