"""Take a widget off the screen for good, without it opening as a window on the way out."""

from __future__ import annotations

from PyQt6.QtWidgets import QLayout, QWidget


def discard_widget(widget: QWidget, layout: QLayout | None = None) -> None:
    """Hide *widget*, unparent it and schedule its deletion, in that order.

    Unparented, not merely taken out of *layout*: a widget removed from a layout stays
    a child and keeps painting at its last geometry until the deferred delete runs,
    which shows up as a ghost overlapping whatever replaced it.

    Hidden first, because unparenting a widget makes it a top-level window, and one
    shown earlier in the same event-loop turn -- by a rebuild the caller is now
    replacing -- still has that show to come. It arrives after the unparent and maps
    the widget as a window of its own. Import all rebuilt every deck card four times
    in one turn and flashed a window per art tile, per rebuild (#120). A hidden widget
    stays hidden through the reparent, so the show never lands.
    """
    widget.hide()
    if layout is not None:
        layout.removeWidget(widget)
    widget.setParent(None)
    widget.deleteLater()
