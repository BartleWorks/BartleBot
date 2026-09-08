"""Popup menus that keep a top-level window as their transient parent.

Qt gives a popup the ``QWindow`` of its parent's *nearest native ancestor* as
transient parent.  Embedding a game client makes its tile a native window, and
Qt then promotes the tile's siblings — the left sidebar, the canvas — to native
windows of their own.  A menu parented to a button inside one of those resolves
to a ``QWidgetWindow`` that is not top level, which Qt refuses:

    QWidgetWindow(0x..., name="LeftSidebarClassWindow") must be a top level window.

The popup is still shown, but with no transient parent at all — it is no longer
tied to the window it belongs to.  See the top-level-parent rule.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QMenu, QWidget


def anchor_menu_to_window(menu: QMenu) -> QMenu:
    """Park *menu* on its top-level window for as long as it is shown.

    The swap happens on ``aboutToShow`` rather than at construction because a
    menu built in a widget's ``__init__`` has no window yet — ``window()`` would
    return the widget itself.  ``aboutToHide`` hands the menu back, so ownership
    (and anything the caller does with ``exec()``'s return value) is unchanged
    once the popup closes.

    Returns *menu*, so it can wrap a constructor call.
    """
    owner = menu.parentWidget()
    if owner is None:
        return menu

    def _to_window() -> None:
        window = owner.window()
        if window is not menu.parentWidget():
            menu.setParent(window, menu.windowFlags())

    def _to_owner() -> None:
        if menu.parentWidget() is not owner:
            menu.setParent(owner, menu.windowFlags())

    menu.aboutToShow.connect(_to_window)
    menu.aboutToHide.connect(_to_owner)
    return menu


def popup_menu(anchor: QWidget) -> QMenu:
    """A ``QMenu`` owned by *anchor* and anchored to *anchor*'s window."""
    return anchor_menu_to_window(QMenu(anchor))
