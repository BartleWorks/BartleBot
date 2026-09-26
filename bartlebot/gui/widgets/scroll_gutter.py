"""``reserve_scrollbar_gutter`` — hold a scroll area's vertical bar space open, bar or no bar.

The Qt equivalent of CSS ``scrollbar-gutter: stable``. With the default as-needed policy a
vertical bar takes its width out of the viewport the moment the content outgrows it, and
gives it back when the content shrinks, so everything in the viewport narrows and widens
by the bar's width. On a pane whose content grows under the user's hand -- an assignment
card folding open, a section expanding -- that is a sideways jolt on every click, and the
click that caused it lands on a control that has just moved (#112).

HOW
---

The bar is kept in the layout permanently (``ScrollBarAlwaysOn``) and simply not *drawn*
while it has nothing to scroll. The space is therefore constant by construction, and the
bar appears in it exactly when it would have appeared anyway.

The obvious port -- leave the policy as-needed and set a viewport margin the bar's width
while it is hidden -- is the one this avoids. Qt decides whether the bar is needed from
the viewport size *less the margins*, so the decision is taken against a different width
in each state: content that fits at the full width but not at the full width less the bar
shows the bar, drops the margin, fits, hides the bar, restores the margin, and loops.

The gutter also keeps a few pixels clear between the content and the bar
(:data:`GUTTER_GAP`). A bar drawn flush against a card's border reads as part of the card.
That gap is a viewport margin, and a *constant* one is safe where a toggled one is not:
it never changes, so Qt measures both states against the same width.

A bar with no range takes no input worth having: there is no handle to drag and nothing a
click on the groove would scroll, so an unpainted bar is also an inert one.

WHERE
-----

On a scroll area whose content reflows as it narrows and changes height while the user is
working in it: card lists and forms with folding sections. Not on a text editor, a tree or
a table, whose content is text that re-wraps or columns that were never going to line up
with anything outside the view -- there the standard as-needed bar is the right call, and
a permanent gutter only costs width.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QAbstractScrollArea, QScrollBar

#: The clear space between the content and the bar, in px. The same 6px the app's
#: layouts space their items by, so the bar sits beside the content the way a sibling does.
GUTTER_GAP = 6


class _BlankWhenIdle(QObject):
    """Swallows a scroll bar's paint events for as long as its range is empty.

    Swallowing the paint leaves the bar's rectangle to whatever its parent draws beneath,
    which is the scroll area's own background -- the same surface the viewport sits on.
    """

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.Paint and isinstance(obj, QScrollBar) and obj.maximum() <= obj.minimum():
            return True
        return False


def reserve_scrollbar_gutter(area: QAbstractScrollArea) -> None:
    """Keep *area*'s vertical scroll bar space reserved, drawing the bar only when it can scroll.

    Also holds :data:`GUTTER_GAP` clear between the content and the bar. Not for a
    ``QTreeView`` or ``QTableView``: those set their own viewport margins for their headers.

    Idempotent: a second call finds the filter already installed and does nothing more.
    """
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    margins = area.viewportMargins()
    area.setViewportMargins(margins.left(), margins.top(), GUTTER_GAP, margins.bottom())
    bar = area.verticalScrollBar()
    if bar.findChild(_BlankWhenIdle) is not None:
        return
    # Parented to the bar, so the filter lives exactly as long as what it filters.
    bar.installEventFilter(_BlankWhenIdle(bar))
    # A range change is already a repaint for the handle, but not for the bar going from
    # blank to drawn with its geometry unchanged -- ask for one explicitly.
    bar.rangeChanged.connect(lambda _lo, _hi: bar.update())


def is_gutter_reserved(area: QAbstractScrollArea) -> bool:
    """Whether :func:`reserve_scrollbar_gutter` has been applied to *area*."""
    return area.verticalScrollBar().findChild(_BlankWhenIdle) is not None
