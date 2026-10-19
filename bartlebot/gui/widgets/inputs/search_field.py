"""A line edit that looks like what it is: free-text search.

The leading magnifier is the near-universal convention for "type here to narrow
what is below", and it does that job before the placeholder has been read — which
matters most for the fields whose placeholder is just "Filter…".

It exists as a widget rather than a two-line helper because the icon has to be
re-tinted when the theme changes. ``svg_icon`` bakes the tint in at render time, so
an icon added once at construction keeps a dark glyph on a dark field after a switch
to the dark theme. ``changeEvent`` is the hook for that (CLAUDE.md, GUI theming);
putting it in one widget means no call site has to remember.

A field can also carry a **scope**: a quiet pill between the magnifier and the text
naming what the search is confined to, the way Discord's ``in:`` token does (#269).
The bot library's is the case, whose search covers only the folder selected beside it,
which nothing said until testers searched one folder for a bot in another and took
the empty list as proof the bot did not exist. A placeholder could not have said it:
it is gone the moment someone types, which is exactly when the scope matters.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics, QKeyEvent
from PyQt6.QtWidgets import QLineEdit, QPushButton, QSizePolicy, QWidget

from bartlebot.gui.theming.icon_utils import svg_icon
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.theming.themed import set_style_sheet

SEARCH_ICON_SIZE = 14

#: The scope pill's padding either side of its text, and the room left between it and
#: the magnifier before it and the text after it.
_SCOPE_PAD_X = 6
_SCOPE_GAP = 4


class _ScopePill(QPushButton):
    """The scope's pill: outlined in the border color, in muted text, and nothing louder.

    It restates a choice made somewhere the user can already see (the library's folder
    list), so it is there to be noticed when looked for, never to compete with what is
    being typed beside it. No fill for that reason: a gray-filled pill is the cards' own
    shape and reads as content. A removable one ends in a ✕, and hovering it lifts the
    text and border a step, which is the only emphasis it ever takes.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._applying_style = False
        self._removable = True
        # A click lands on the pill, never takes the caret out of the field it sits in.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._restyle()

    @property
    def removable(self) -> bool:
        return self._removable

    def set_scope(self, text: str, *, removable: bool) -> None:
        self._removable = removable
        self.setText(f"{text}  ✕" if removable else text)
        self.setCursor(Qt.CursorShape.PointingHandCursor if removable else Qt.CursorShape.ArrowCursor)
        self._restyle()
        self.refit()

    def refit(self) -> None:
        """Size the pill to its text at the theme's current font size."""
        # Sized from the text, for the reason ActionPill is: a QPushButton's own hint
        # carries the platform's 80px minimum width. The stylesheet's font-size never
        # reaches font(), so the widget is given the same size to measure with.
        font = self.font()
        font.setPixelSize(THEME.font_subtext_px)
        self.setFont(font)
        metrics = QFontMetrics(font)
        self.setFixedSize(QSize(metrics.horizontalAdvance(self.text()) + 2 * _SCOPE_PAD_X + 2, metrics.height() + 2))

    def _restyle(self) -> None:
        if self._applying_style:  # see TagPill._restyle
            return
        self._applying_style = True
        try:
            rule = (
                f"QPushButton {{ background: transparent; color: {THEME.text_muted};"
                f" border: 1px solid {THEME.border}; border-radius: 8px;"
                f" padding: 0px {_SCOPE_PAD_X}px; font-size: {THEME.font_subtext_px}px; }}"
            )
            if self._removable:
                rule += f"QPushButton:hover {{ color: {THEME.text}; border-color: {THEME.text_muted}; }}"
            set_style_sheet(self, rule)
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()


class SearchLineEdit(QLineEdit):
    """``QLineEdit`` with a leading search icon that tracks the theme, and an optional scope.

    Everything else — the clear button, signal wiring, width — is left to the caller,
    so this can replace an existing field without changing its behavior.
    """

    #: The user asked to drop the scope: clicked a removable pill, or pressed Backspace
    #: with the caret right after it. The field does not drop it itself -- the owner
    #: knows what the wider search is, and says so by calling ``set_scope`` again.
    scope_removed = pyqtSignal()

    def __init__(self, placeholder: str = "Search…", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self._icon_action = self.addAction(self._icon(), QLineEdit.ActionPosition.LeadingPosition)
        # Built on the first set_scope: almost every search field has none.
        self._scope_pill: _ScopePill | None = None

    @staticmethod
    def _icon():
        return svg_icon("search", SEARCH_ICON_SIZE, tint=THEME.text_muted)

    # ------------------------------------------------------------------
    # Scope
    # ------------------------------------------------------------------

    def set_scope(self, text: str | None, *, removable: bool = True, tooltip: str = "") -> None:
        """Name what this search is confined to, or ``None`` when it covers everything.

        *removable* gives the pill its ✕ and makes the click and Backspace ask for
        ``scope_removed``; without it the pill only names the scope, for a list that has
        nothing wider to widen to.
        """
        if text is None:
            if self._scope_pill is not None:
                self._scope_pill.hide()
            self._place_scope()
            return
        if self._scope_pill is None:
            self._scope_pill = _ScopePill(self)
            self._scope_pill.clicked.connect(self._on_scope_clicked)
        self._scope_pill.set_scope(text, removable=removable)
        self._scope_pill.setToolTip(tooltip)
        self._scope_pill.show()
        self._place_scope()

    @property
    def scope_text(self) -> str | None:
        """The scope the pill names, without its ✕; ``None`` when there is none."""
        pill = self._scope_pill
        if pill is None or pill.isHidden():
            return None
        return pill.text().removesuffix("  ✕")

    @property
    def scope_removable(self) -> bool:
        return self.scope_text is not None and self._scope_pill.removable

    def _on_scope_clicked(self) -> None:
        if self.scope_removable:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.scope_removed.emit()

    def _place_scope(self) -> None:
        """Put the pill just after the magnifier, and start the text just after the pill.

        The text margin adds to the room Qt keeps for the magnifier rather than replacing
        it, so the margin is the pill's width and the gaps either side of it alone.
        """
        pill = self._scope_pill
        if pill is None or pill.isHidden():
            self.setTextMargins(0, 0, 0, 0)
            return
        pill.move(self._icon_right() + _SCOPE_GAP, (self.height() - pill.height()) // 2)
        self.setTextMargins(pill.width() + 2 * _SCOPE_GAP, 0, 0, 0)

    def _icon_right(self) -> int:
        """Where the magnifier ends: Qt's own button for it, read rather than assumed."""
        for obj in self._icon_action.associatedObjects():
            if obj is not self and isinstance(obj, QWidget):
                return obj.geometry().right()
        return SEARCH_ICON_SIZE + 2 * _SCOPE_GAP

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # Backspace with the caret against the pill takes the pill, as it would a
        # character there: the token behavior Discord's search taught.
        if (
            event.key() == Qt.Key.Key_Backspace
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and self.scope_removable
            and self.cursorPosition() == 0
            and not self.hasSelectedText()
        ):
            event.accept()
            self.scope_removed.emit()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_scope()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._icon_action.setIcon(self._icon())
            # The pill restyles itself, but its size follows the theme's font and the
            # text margin follows its size, so both are redone here, in that order.
            if self._scope_pill is not None:
                self._scope_pill.refit()
            self._place_scope()
