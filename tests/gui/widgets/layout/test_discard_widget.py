"""#120: a widget shown and discarded in the same event-loop turn must not open as a window.

Import all rebuilt every deck card's art strip once per slot inside one turn. Each
rebuild showed its tiles and the next one unparented them before that show landed, so
Qt mapped every tile as a top-level window of its own -- a burst of empty windows in the
corner of the screen. Invisible to a test that only inspects the finished tree, which is
why the spy below counts ``Show`` events as they are delivered. ``discard_widget`` is core's
since Stage 6 of #167, with its own tests; this is the case that found the bug.
"""

import pytest
from bartleby.shared.strategy_models import DeckData
from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from bartlebot.combat.deck_content import deck_groups
from bartlebot.gui.widgets.cards import card_art_tile
from bartlebot.gui.widgets.cards.card_art_tile import CardArtStrip


class _WindowShowSpy(QObject):
    """Counts every plain widget shown while it has no parent -- a window nobody meant."""

    def __init__(self) -> None:
        super().__init__()
        self.shown: list[str] = []

    def eventFilter(self, obj, event):  # noqa: N802 - Qt's name
        if (
            event.type() == QEvent.Type.Show
            and isinstance(obj, QWidget)
            and obj.parent() is None
            and obj.windowType() == Qt.WindowType.Window
        ):
            self.shown.append(type(obj).__name__)
        return False


@pytest.fixture
def spy(qapp):
    watcher = _WindowShowSpy()
    qapp.installEventFilter(watcher)
    yield watcher
    qapp.removeEventFilter(watcher)


@pytest.fixture
def shown_host(qapp):
    host = QWidget()
    QVBoxLayout(host)
    host.show()
    qapp.processEvents()
    yield host
    host.close()


def test_rebuilding_an_art_strip_repeatedly_opens_no_window(qapp, shown_host, spy, monkeypatch):
    """The exact shape of the bug: one visible strip, rebuilt four times in one turn."""
    pixmap = QPixmap(8, 8)
    pixmap.fill(Qt.GlobalColor.red)
    monkeypatch.setattr(card_art_tile, "card_icon", lambda _template: QIcon(pixmap))
    strip = CardArtStrip(parent=shown_host)
    shown_host.layout().addWidget(strip)
    groups = deck_groups(DeckData(normal={"Tempest": 1, "Frenzy": 1, "Epic": 1}))
    strip.set_groups(groups)
    qapp.processEvents()
    spy.shown.clear()

    for _ in range(4):
        strip.set_groups(groups)
    qapp.processEvents()

    assert len(strip.tiles) == 3
    assert spy.shown == []
