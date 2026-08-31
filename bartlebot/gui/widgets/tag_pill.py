"""The two shapes a bot tag takes on screen, and the one palette behind both.

A tag is coloured by its group everywhere it appears — the dot in the picker, the
pill on a library card, the chip in a filter bar, the chip in Bot Info. Someone who
filters by Rewards and scans the results should be matching a colour rather than
re-reading every pill, and that only holds if one module owns the mapping.

Two shapes, because they do different jobs:

* :func:`tag_pill` — descriptive. Small, inert, several per row. It says what a bot
  is; there is nothing to click.
* :class:`TagChip` — interactive. Larger, with a ✕ and a real hit target, because
  removing the filter or the tag it stands for is the whole point of it being there.

Both take their colour from the same call, so the sizes differ and the hues never do.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget

from bartlebot.bot_tags import tag_group
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.tag_picker import group_accent


def pill_style(bg: str, fg: str, *, font_size: int = 9, radius: int = 7, pad: str = "1px 6px") -> str:
    """The shared pill look, so a caller varies only what it means to vary."""
    return (
        f"background: {bg}; color: {fg}; font-size: {font_size}px;"
        f" font-weight: bold; border-radius: {radius}px; padding: {pad};"
    )


class TagPill(QLabel):
    """A small, inert, group-coloured pill naming one tag."""

    def __init__(self, tag: str, parent: QWidget | None = None) -> None:
        super().__init__(tag, parent)
        self._tag = tag
        self._applying_style = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(14)
        self._restyle()

    @property
    def tag(self) -> str:
        return self._tag

    def _restyle(self) -> None:
        # Guard is load-bearing: setStyleSheet delivers StyleChange straight back to
        # changeEvent, and the app dies in window construction with no traceback.
        # Same shape as PickerButton._apply_style.
        if self._applying_style:
            return
        self._applying_style = True
        try:
            self.setStyleSheet(pill_style(group_accent(tag_group(self._tag)), THEME.pill_text))
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()


class TagChip(QPushButton):
    """A group-coloured chip that removes its tag when clicked."""

    removed = pyqtSignal(str)

    def __init__(self, tag: str, *, tooltip: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(f"{tag}  ✕", parent)
        self._tag = tag
        self._applying_style = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip or f"Remove the {tag} tag")
        self._restyle()
        self.clicked.connect(lambda: self.removed.emit(self._tag))

    @property
    def tag(self) -> str:
        return self._tag

    def _restyle(self) -> None:
        if self._applying_style:  # see TagPill._restyle
            return
        self._applying_style = True
        try:
            base = pill_style(
                group_accent(tag_group(self._tag)), THEME.pill_text, font_size=10, radius=9, pad="2px 9px"
            )
            self.setStyleSheet(
                f"QPushButton {{ border: none; {base} }}QPushButton:hover {{ background: {THEME.edge_selected}; }}"
            )
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()
