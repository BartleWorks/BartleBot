"""Keep Qt from making widgets native that nobody asked to be (#126).

Embedding a game client calls ``ClientTile.winId()``, which gives the tile a native
window. By default Qt then gives every sibling of the tile and of each of its ancestors
one too, which includes the canvas. From there, ``QWidget.setParent`` on a native widget
marks every child of its new parent native, and ``anchor_menu_to_window`` reparents each
popup menu on the way in and on the way out. One right-click on the canvas made its view,
viewport and overlays native.

A native widget has its own HWND, and Windows sends a click to the HWND under the
pointer. ``WA_TransparentForMouseEvents`` only steers Qt's own hit test, so it no longer
applies. The ``ViewFrame`` drawn round the whole viewport in Focus Mode and in a
definition document then took every click meant for the canvas, and the canvas looked
read-only in Edit mode.

``AA_DontCreateNativeWidgetSiblings`` turns that promotion off: a tile and its ancestors
are still native, since the game window has to sit in one, but nothing else becomes
native unless it sets ``WA_NativeWindow`` itself. The one widget that has to is
``GameView``'s detach/re-attach button, which is drawn over the tiles and would otherwise
be painted underneath them.

The native windows that remain need a cursor each (#258); see
``give_native_ancestors_a_cursor``.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget


def keep_siblings_alien() -> None:
    """Stop a native widget from making its siblings native. Call before the ``QApplication`` exists."""
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)


def give_native_ancestors_a_cursor(widget: QWidget) -> None:
    """Give *widget* and each native ancestor an arrow, unless it already has a cursor (#258).

    Qt keeps one cursor per native window, set to that of the widget the pointer last
    entered in it, and a window with none of its own borrows its parent window's. Two
    things leave a native window with none: the pointer over a widget that sets no cursor,
    and the pointer over a disabled widget that does, since Qt clears the cursor for those.
    The borrowed cursor is stale whenever the parent window's was set while the pointer was
    elsewhere -- by a ``setCursor`` call on a widget the pointer had left, which Qt applies
    to that widget's window all the same. With a game embedded, that put a pointing hand on
    the toolbar's empty space, on its disabled buttons and round the game windows, wherever
    a sidebar card or an enabled button had last left one.

    A widget with ``WA_SetCursor`` ends Qt's walk up from the widget under the pointer, so
    giving each native window's own widget a cursor means no window ever borrows one. An
    arrow is what each of them showed before it was native, when the walk ended at the
    top-level window and Qt's default.
    """
    current: QWidget | None = widget
    while current is not None:
        # A native widget is one with a window handle; PyQt6 has no internalWinId().
        if current.windowHandle() is not None and not current.testAttribute(Qt.WidgetAttribute.WA_SetCursor):
            current.setCursor(Qt.CursorShape.ArrowCursor)
        current = current.parentWidget()
