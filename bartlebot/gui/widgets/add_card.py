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

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from bartlebot.gui.icon_utils import svg_pixmap
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.card_frame import GRIP_GUTTER

# Border width of the Add card's dashed outline. Two, not one, because Qt scales a dash
# pattern by the pen width: a 1px dashed border draws as a row of 1px dots, which at the
# subtle border colour is a line you have to look for -- on the top and left edges of a
# dark-theme card it was not visible at all. At 2px the dashes are dashes, and the card
# reads at about the weight of the 1px solid border on the launch cards above it, because
# the gaps give back what the extra pixel takes.
ADD_CARD_BORDER_WIDTH = 2


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
    columns' fit pass unable to grey it: ``refresh_fit`` walks the launch cards and calls
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
        self.setStyleSheet(
            f"#AddCard {{ background: {bg}; border: {ADD_CARD_BORDER_WIDTH}px dashed {border}; border-radius: 4px; }}"
        )
        self._name_lbl.setStyleSheet(f"color: {color}; background: transparent;")
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
