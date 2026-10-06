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
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication


def keep_siblings_alien() -> None:
    """Stop a native widget from making its siblings native. Call before the ``QApplication`` exists."""
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)
