"""The pieces the node page and the subgraph node page share (#142).

Both pages are the same shape: the name once, as an editable title; a gray line saying
what the node is; a row with Show / Hide descriptions and Collapse all / Expand all;
then ``OptionCard``\\s. These are the parts that are not already somebody else's --
the card is #138's, the gutter and the description switches are the option surfaces'.
What each card *says* is ``bartlebot.options.param_cards``, which needs no Qt.
"""

from __future__ import annotations

from collections.abc import Callable

from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin, set_style_sheet
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QWidget

from bartlebot.gui.sidebar.option_card import CardHeadingActions
from bartlebot.gui.sidebar.option_gutter import DescriptionSwitches
from bartlebot.options.param_cards import to_fill_text


class TitleEdit(ThemedMixin, QLineEdit):
    """A node's label as the page's title: heading-sized, and outlined like every other field.

    The name used to appear three times on the subgraph node's page -- the title, the
    line under it and a Label row -- and twice on a node's. It is once now, and it is
    the field. The outline used to show only on hover and focus, so an unlabeled node's
    page had nothing to click (#156); it is always drawn now, as the page is an inspector
    and every other field on it is outlined too. The page sets the placeholder: the name
    the node started with, so an emptied field still says what goes in it.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setToolTip("The label: what the canvas calls this node")
        font = THEME.ui_font(THEME.font_heading_px, bold=True)
        self.setFont(font)
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self.setFont(THEME.ui_font(THEME.font_heading_px, bold=True))
        set_style_sheet(
            self,
            # A stylesheet ``color`` overrides the palette's placeholder color with the
            # text color at half alpha, so the muted one is restated here.
            f"QLineEdit {{ background: {THEME.bg}; color: {THEME.text}; placeholder-text-color: {THEME.text_muted};"
            f" border: 1px solid {THEME.border}; border-radius: 3px; padding: 1px 3px; }}"
            f"QLineEdit:focus {{ border-color: {THEME.accent}; }}",
        )


class ToFillLabel(ThemedTextLabel):
    """A card band's "2 to fill": muted, or the error color once a field it counts is shown red.

    On the band whether the card is folded or not, so a fold never hides what is left
    to fill in -- the old header's "required input empty" mark, said as a count and
    said quietly until the user has been in the field.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", color="text_muted", css=f"font-size: {THEME.font_subtext_px}px;", parent=parent)
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    @property
    def red(self) -> bool:
        return self._color_token == "error"

    def set_count(self, count: int, *, red: bool = False) -> None:
        self._count = count
        self.setText(to_fill_text(count))
        self.set_color("error" if red and count else "text_muted")


class PageFoldBar(QWidget):
    """Show / Hide descriptions on the left, Collapse all / Expand all on the right.

    The option surfaces' ``DescriptionSwitches``, with ``CardFolds``' two heading actions
    where a section heading would have them. Hand :attr:`heading_actions` to ``CardFolds``.
    """

    def __init__(self, on_descriptions: Callable[[bool], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.switches = DescriptionSwitches(on_descriptions)
        row.addWidget(self.switches, 1)
        self.heading_actions = CardHeadingActions()
        row.addWidget(self.heading_actions, 0)


def record_line() -> ThemedTextLabel:
    """The line at the top of a recordable node's card: what Record fills, or what it is doing."""
    line = ThemedTextLabel("", color="text_muted", css=f"font-size: {THEME.font_small_px}px;")
    line.setWordWrap(True)
    return line
