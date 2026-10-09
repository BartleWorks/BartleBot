"""``AddCard``: the dashed card that ends a collection and adds one more to it.

Lived in ``game_embed/launch_cards.py`` while the launch columns were its only caller.
The Combat tab's Duel Combat Strategies section is the second, and a sidebar importing
a widget out of the game-embedding package to draw a terminator is the wrong dependency
-- the card knows nothing about launching, so it belongs beside the frame whose gutter
it borrows.

``ADD_CARD_BORDER_WIDTH`` comes with it: the dash width is the one thing about this card
a test asserts, and it is a property of the card rather than of either caller.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QWidget

from bartlebot.gui.menu_utils import popup_menu
from bartlebot.gui.theming.icon_utils import svg_icon, svg_pixmap
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.theming.themed import ThemedMixin, set_style_sheet
from bartlebot.gui.widgets.cards.card_frame import GRIP_GUTTER

# Border width of the Add card's dashed outline. Two, not one, because Qt scales a dash
# pattern by the pen width: a 1px dashed border draws as a row of 1px dots, which at the
# subtle border color is a line you have to look for -- on the top and left edges of a
# dark-theme card it was not visible at all. At 2px the dashes are dashes, and the card
# reads at about the weight of the 1px solid border on the launch cards above it, because
# the gaps give back what the extra pixel takes.
ADD_CARD_BORDER_WIDTH = 2

#: Gap between the dashed outline and a menu opened under it, in px.
MENU_GAP = 2


class AddCard(ThemedMixin, QWidget):
    """The card at the foot of a collection: "Add account", "Add profile".

    A fresh install opened on two columns reading "(No accounts)" and "(No profiles)",
    and nothing on either said what to do about that -- the only way forward was to
    notice a small underlined link between them, open a dialog and find a button under a
    list. The answer to "how do I get another one of these" belongs where the ones
    you have are, which is the convention every card collection whose members are
    launched has settled on: Chrome's profile picker, the Figma file grid, Docker
    Desktop. Always last, and always a card.

    Deliberately *not* a ``CardFrame`` subclass. It launches nothing and has no
    resolution to judge, so keeping it outside that hierarchy is what makes the launch
    columns' fit pass unable to gray it: ``refresh_fit`` walks the launch cards and calls
    ``set_unavailable`` on each, and this class does not have that method to call. It is
    also not a member of any ordered list -- nothing is dragged onto it and nothing is
    dropped after it. It is the collection's terminator.

    Dashed rather than solid, which is the whole visual argument: same width, same
    radius, same hover as the cards above it, so it reads as one of them, but an outline
    that says it is a slot rather than a thing.
    """

    add_requested = pyqtSignal()

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        # Framed like a launch card, and for the same reason: the dashed outline is drawn
        # on an inner frame so that this card can carry the drag gutter's indent without
        # its border reaching back across it. A terminator whose frame starts twenty-two
        # pixels left of every frame above it does not read as the last card in the
        # column, it reads as a misplaced one.
        self._body = QFrame(self)
        self._body.setObjectName("AddCard")
        self._body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hovered = False
        self._text = text
        self._build()
        self.refresh_theme_styles()

    @property
    def text(self) -> str:
        """What the card reads. Public so a test can name the collection it belongs to."""
        return self._text

    def menu_position(self) -> QPoint:
        """Where a menu opened from this card goes, in global coordinates.

        Under the dashed outline's left edge, not the widget's: the widget starts at the
        drag gutter, so a menu anchored to it opened twenty-two pixels left of the card
        the user clicked.
        """
        return self._body.mapToGlobal(QPoint(0, self._body.height() + MENU_GAP))

    def open_menu(self, items: list[tuple[str, str, Callable[[], None]]]) -> None:
        """Offer a choice of ways to add, for a collection that has more than one.

        *items* are ``(icon, label, run)``. Built on every click rather than once, so the
        icons take whichever theme is current when it opens.
        """
        self.build_menu(items).exec(self.menu_position())

    def build_menu(self, items: list[tuple[str, str, Callable[[], None]]]) -> QMenu:
        """The menu :meth:`open_menu` shows, unshown. Public so a test can measure it.

        At least as wide as the dashed outline, so it reads as dropping out of the card
        rather than as a short list parked under its corner. A minimum, not a fixed
        width: a label longer than a narrow sidebar's card still gets its room.
        """
        menu = popup_menu(self)
        menu.setMinimumWidth(self._body.width())
        for icon, label, run in items:
            menu.addAction(svg_icon(icon, size=14), label).triggered.connect(lambda _checked=False, run=run: run())
        return menu

    def _build(self) -> None:
        row = QHBoxLayout(self)
        row.setContentsMargins(GRIP_GUTTER, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._body)

        layout = QHBoxLayout(self._body)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        layout.addStretch()

        self._icon_lbl = QLabel(self)
        self._icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self._icon_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self._name_lbl = QLabel(self._text, self)
        self._name_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self._name_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addStretch()

    def apply_theme_styles(self) -> None:
        border = THEME.accent if self._hovered else THEME.border
        color = THEME.text if self._hovered else THEME.text_muted
        # Transparent rather than THEME.surface: an outline the same fill as a real card
        # reads as one, and the point of the dashes is that this is the empty slot after
        # them. The hover is where it takes a fill, because that is a hit target
        # confirming itself.
        bg = THEME.node_fill if self._hovered else "transparent"
        set_style_sheet(
            self,
            f"#AddCard {{ background: {bg}; border: {ADD_CARD_BORDER_WIDTH}px dashed {border}; border-radius: 4px; }}",
        )
        set_style_sheet(self._name_lbl, f"color: {color}; background: transparent;")
        # svg_icon bakes the tint in at render time, so the pixmap is rebuilt on every
        # pass rather than set once in _build -- see ProfileCard.apply_theme_styles.
        self._icon_lbl.setPixmap(svg_pixmap("plus", 12, tint=color))

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.refresh_theme_styles()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.refresh_theme_styles()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.add_requested.emit()
        super().mousePressEvent(event)
