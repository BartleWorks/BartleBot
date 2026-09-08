"""The two row-building helpers every settings tab uses.

Both were written three times over -- ``_section_label`` in ``general_tab`` and
``theme_tab``, ``_hint`` in ``canvas_tab`` -- and the copies had already drifted
apart in their margins, so two tabs open beside each other set their headers at
different heights.

The rule the hint exists to state, because it is the one that is easy to get
wrong: **a hint is a** ``ThemedTextLabel``, **not a styled** ``QLabel``. A factory
hands its label to a caller with nothing to re-apply on a theme switch, so a plain
one keeps whichever palette was live when the tab was built -- see the "GUI
theming" section of CLAUDE.md.

Descriptions are *visible*, not tooltips. Before the description-row rework the Log, Layout and
Auto-save rows carried no explanation at all and the rest carried theirs only on
hover, which is a description nobody discovers. A row's hint says what the
setting does in a line or two; a tooltip is kept only where there is a longer
*why* worth having, and it never carries the whole explanation on its own.

That decision is what :class:`ScrollableTab` exists to pay for: a visible hint
under every row is two rows of height per setting, and four such tabs stacked
the dialog past the bottom of a 1080p screen. The height is the cost of
the descriptions, so the tab scrolls rather than the descriptions going away.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QScrollArea, QWidget

from bartlebot.gui.widgets.themed_label import ThemedTextLabel


def hint(text: str) -> QLabel:
    """The dimmed explanation printed under a settings row."""
    lbl = ThemedTextLabel(text, css="font-size: 11px; padding-bottom: 6px;")
    lbl.setWordWrap(True)
    return lbl


def section_label(title: str, *, top: int = 8, bottom: int = 4) -> QLabel:
    """A bold section header, matching ``_SectionHeader`` in ``sidebar_section.py``."""
    lbl = QLabel(title)
    font = lbl.font()
    font.setBold(True)
    font.setPointSize(font.pointSize() + 1)
    lbl.setFont(font)
    lbl.setContentsMargins(0, top, 0, bottom)
    return lbl


class ScrollableTab(QScrollArea):
    """A settings tab that scrolls, and still answers ``apply()`` for the tab inside.

    ``SettingsDialog.apply_all`` walks the tab bar and calls ``apply`` on whatever it
    finds there, precisely so a tab added later cannot be forgotten. Wrapping a tab in
    a plain ``QScrollArea`` would put a widget with no ``apply`` in that position and
    throw away everything the user typed, so the wrapper forwards instead of the dialog
    learning to unwrap.

    Only the tabs whose height is fixed by their content are wrapped. A tab built to
    fill the space it is given -- the Game tab, whose two lists carry the stretch --
    shrinks on its own, and inside a scroll area those lists would collapse to a couple
    of visible rows instead.
    """

    def __init__(self, content: QWidget) -> None:
        super().__init__()
        self._content = content
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setWidget(content)

    @property
    def content(self) -> QWidget:
        """The tab itself, for anything that needs the real widget."""
        return self._content

    def apply(self) -> None:
        self._content.apply()
