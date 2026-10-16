"""One app-wide signal that the extracted game art changed, and how a surface follows it (#239).

Art can be extracted while the app is open: setup's Game art page runs straight after an
install, and Settings › General › Game art runs again after a game patch. When a run
ends, :func:`bartlebot.game.art_extraction.extract_art` calls ``card_art.forget_cache()``,
so the next lookup reads the new files. Nothing already on screen looks again by itself,
though, so the bot library kept its plain captions until a relaunch.

So :class:`~bartlebot.gui.settings.game_art_row.ArtExtraction` calls
:func:`announce_art_changed` once when a run produces art, and every surface that draws
art follows the signal with :func:`follow_art`. ``card_art`` stays free of Qt: the signal
lives here, beside the widgets that need it.

**The widget that chooses between art and text is the one that follows.** A deck card
shows its strip of tiles or its summary line, and an ``ArchmasteryBadge`` or its
sentence, depending on what resolved, so the card re-runs its own fill method rather than
the strip or badge redrawing beneath it. A widget built afresh each time it opens (a
picker's popup, the New Bot dialog) looks the art up then, and needs no connection.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


class ArtSignal(QObject):
    """Carries :attr:`art_changed`. One per app, from :func:`art_signal`."""

    #: An extraction finished and wrote new art: every lookup may now answer differently.
    art_changed = pyqtSignal()


_signal: ArtSignal | None = None


def art_signal() -> ArtSignal:
    """The app's one :class:`ArtSignal`, made on first use."""
    global _signal
    if _signal is None:
        _signal = ArtSignal()
    return _signal


def announce_art_changed() -> None:
    """Tell every following surface to redraw. Called once per extraction that wrote art."""
    art_signal().art_changed.emit()


class _ArtFollower(QObject):
    """A child of the following widget, so the connection ends when the widget is destroyed.

    PyQt drops a connection to a bound method of a ``QObject`` when that object is
    destroyed, and this one is destroyed with its parent. A lambda connected straight to
    the signal would outlive the widget and redraw a deleted one.
    """

    def __init__(self, owner: QObject, redraw: Callable[[], None]) -> None:
        super().__init__(owner)
        self._redraw = redraw
        art_signal().art_changed.connect(self._on_art_changed)

    def _on_art_changed(self) -> None:
        # A slot that raises aborts the process under PyQt6, and a redraw is cosmetic.
        try:
            self._redraw()
        except Exception:
            logger.exception("redrawing %s after the game art changed raised", type(self.parent()).__name__)


def follow_art(owner: QObject, redraw: Callable[[], None]) -> None:
    """Call *redraw* whenever the art changes, for as long as *owner* exists."""
    _ArtFollower(owner, redraw)
