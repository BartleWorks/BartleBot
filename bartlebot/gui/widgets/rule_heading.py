"""``RuleHeading``: a bold label with a rule running out to the right of it.

The heading a *detail pane* wants, as opposed to the two the app already had. A
``SidebarSection`` header is a control — it carries a disclosure triangle and folds what
is under it — and a ``CardColumn``'s heading is centered over a column of cards. Neither
is right for naming the regions of a form: a triangle promises a fold nobody asked for,
and a centered heading over left-aligned fields reads as belonging to something else.

So: the label at the left margin where the fields below it start, and a hairline filling
whatever width is left. The rule is what makes it a *section* rather than a bold line of
text — it draws the boundary the eye needs to stop scanning the previous region — and it
costs nothing to the left edge everything else is aligned to.

The rule is a plain ``QFrame`` given the theme's border color explicitly rather than
Qt's sunken shadow. A sunken HLine is drawn from the palette's Mid/Light pair, which the
dark theme leaves nearly invisible against ``surface``; one flat pixel of ``THEME.border``
is the same weight in both themes and matches every card border on the same screen.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin

#: Gap between the label and the start of the rule, and how far the rule sits below the
#: label's baseline. Small: the rule belongs to the heading rather than dividing it from
#: what follows, and a wide gap reads as two unrelated marks.
_RULE_GAP = 8
_RULE_HEIGHT = 1

#: What the heading keeps clear above and below itself. See the layout in ``__init__``
#: for why the two differ.
_SPACE_ABOVE = 10
_SPACE_BELOW = 2


class RuleHeading(ThemedMixin, QWidget):
    """A section heading: bold text at the left, a hairline filling the rest of the row."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        # The space above belongs to the heading rather than to whatever precedes it: a
        # heading's job is to *open* a region, so it carries its own leading gap and every
        # surface using one gets the same rhythm without setting a spacer of its own.
        # Asymmetric, and deliberately -- a rule sitting closer to what it introduces than
        # to what it follows is what makes it read as a heading rather than as a divider.
        row.setContentsMargins(0, _SPACE_ABOVE, 0, _SPACE_BELOW)
        row.setSpacing(_RULE_GAP)

        self._label = QLabel(text, self)
        font = self._label.font()
        font.setBold(True)
        self._label.setFont(font)
        row.addWidget(self._label, 0)

        self._rule = QFrame(self)
        self._rule.setFrameShape(QFrame.Shape.NoFrame)
        self._rule.setFixedHeight(_RULE_HEIGHT)
        self._rule.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row.addWidget(self._rule, 1)

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.refresh_theme_styles()

    @property
    def text(self) -> str:
        """What the heading reads. Public so a test can name the section it found."""
        return self._label.text()

    def set_text(self, text: str) -> None:
        self._label.setText(text)

    def apply_theme_styles(self) -> None:
        self._label.setStyleSheet(f"color: {THEME.text}; background: transparent;")
        self._rule.setStyleSheet(f"background: {THEME.border}; border: none;")
