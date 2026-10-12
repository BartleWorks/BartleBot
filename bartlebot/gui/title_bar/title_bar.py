"""The title bar: the logo, the menus, the window's title, the layout toggles and the window
buttons, on the one row where Windows would otherwise draw its caption above a menu bar.

It is an ordinary widget, handed to ``QMainWindow.setMenuWidget``. What makes it a title bar
is ``native_frame``: that is what moves the client area up over the caption and tells
Windows which of these pixels drag the window. The bar's part of that is
:meth:`TitleBar.is_control_at` -- a button or the menu bar takes a click, and everything
else (the logo, the title, the space between) is caption.

The menu bar is the window's own ``QMenuBar``, moved here rather than rebuilt, so every
action and its Alt mnemonic work as they did. Each layout toggle is bound to the View menu
action that does the same thing, so the two can never disagree about what is showing.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QPainter
from PyQt6.QtWidgets import QAbstractButton, QHBoxLayout, QMenuBar, QSizePolicy, QWidget

from bartlebot.game.game_sizing import TITLE_BAR_PX
from bartlebot.gui.logo import logo_icon
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.theming.themed import ThemedMixin, set_style_sheet
from bartlebot.gui.title_bar.caption_button import CaptionButton, CaptionKind
from bartlebot.gui.widgets.buttons.icon_button import IconButton
from bartlebot.gui.widgets.labels.elided_label import ElidedLabel

#: The logo's side, and a layout toggle's, in logical pixels.
_LOGO_PX = 20
_TOGGLE_PX = 28
_TOGGLE_ICON_PX = 18


class _Logo(QWidget):
    """The app's tree, painted from the window icon so it picks the size Windows was given."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon = logo_icon()
        self.setFixedSize(_LOGO_PX, _LOGO_PX)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        self._icon.paint(painter, QRect(0, 0, _LOGO_PX, _LOGO_PX))
        painter.end()


class TitleBar(ThemedMixin, QWidget):
    """The window's one row of chrome above the panes. See the module docstring."""

    minimize_requested = pyqtSignal()
    maximize_requested = pyqtSignal()
    close_requested = pyqtSignal()

    def __init__(self, menu_bar: QMenuBar, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedHeight(TITLE_BAR_PX)
        self._menu_bar = menu_bar
        self._toggles: list[IconButton] = []

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(_Logo(self), alignment=Qt.AlignmentFlag.AlignVCenter)
        row.addSpacing(6)
        menu_bar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row.addWidget(menu_bar, alignment=Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self._title = ElidedLabel("", self)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._title, stretch=2)
        row.addStretch(1)
        self._toggle_row = QHBoxLayout()
        self._toggle_row.setSpacing(2)
        row.addLayout(self._toggle_row)
        row.addSpacing(10)

        self._minimize = CaptionButton(CaptionKind.MINIMIZE, TITLE_BAR_PX, self)
        self._maximize = CaptionButton(CaptionKind.MAXIMIZE, TITLE_BAR_PX, self)
        self._close = CaptionButton(CaptionKind.CLOSE, TITLE_BAR_PX, self)
        for button in (self._minimize, self._maximize, self._close):
            row.addWidget(button)
        self._minimize.clicked.connect(self.minimize_requested)
        self._maximize.clicked.connect(self.maximize_requested)
        self._close.clicked.connect(self.close_requested)

        self.refresh_theme_styles()

    # -- building -------------------------------------------------------------------

    def add_toggle(self, action: QAction, *, shown_icon: str, hidden_icon: str) -> IconButton:
        """A layout toggle bound to *action*, a checkable View menu action.

        The button shows *shown_icon* while the action is checked. A click triggers the
        action rather than flipping anything itself, and the button then follows the
        action's state -- so a refusal, or a change made from the menu or a shortcut, is
        reflected rather than contradicted.
        """
        shortcut = action.shortcut().toString()
        tooltip = action.text() + (f" ({shortcut})" if shortcut else "")
        button = IconButton(
            hidden_icon,
            tooltip=tooltip,
            size=_TOGGLE_PX,
            icon_size=_TOGGLE_ICON_PX,
            checked_icon_name=shown_icon,
            parent=self,
        )
        button.setChecked(action.isChecked())
        button.clicked.connect(lambda _checked=False: self._trigger(action, button))
        action.toggled.connect(button.setChecked)
        self._toggle_row.addWidget(button, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._toggles.append(button)
        return button

    @staticmethod
    def _trigger(action: QAction, button: IconButton) -> None:
        # The click has already flipped the button; the action decides what is true.
        action.trigger()
        button.setChecked(action.isChecked())

    # -- state ------------------------------------------------------------------------

    @property
    def toggles(self) -> list[IconButton]:
        return list(self._toggles)

    @property
    def caption_buttons(self) -> tuple[CaptionButton, CaptionButton, CaptionButton]:
        """Minimize, maximize and close, in that order."""
        return self._minimize, self._maximize, self._close

    @property
    def title(self) -> str:
        return self._title.text()

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_maximized(self, maximized: bool) -> None:
        """Swap the maximize glyph for the restore one while the window is maximized."""
        self._maximize.set_maximized(maximized)

    def is_control_at(self, pos: QPoint) -> bool:
        """Whether a click at *pos* (this widget's coordinates) belongs to a control.

        A button or the menu bar takes it. Anything else under the point is caption, and
        drags the window.
        """
        child = self.childAt(pos)
        while child is not None and child is not self:
            if isinstance(child, (QAbstractButton, QMenuBar)):
                return True
            child = child.parentWidget()
        return False

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(super().sizeHint().width(), TITLE_BAR_PX)

    def apply_theme_styles(self) -> None:
        set_style_sheet(
            self,
            f"#titleBar {{ background: {THEME.surface}; border-bottom: 1px solid {THEME.border}; }}",
        )
        set_style_sheet(self._title, f"color: {THEME.text_muted};")
