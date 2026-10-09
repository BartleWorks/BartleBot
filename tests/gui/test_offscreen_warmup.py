"""The first offscreen render is paid on a show, not on the first drag.

A card being lifted gets a ``QGraphicsDropShadowEffect``, and a widget with an effect on
it is rendered through an offscreen surface rather than straight into the window. Qt
spends 150-200ms on the *first* one in a window, flushing the move and resize events it
withholds from hidden widgets -- every unopened tab, menu and dialog page laid out at
once. Measured in the real app, the first drag of a session cost 336ms against a 144ms
baseline, and every drag after it was free.

``warm_offscreen_render`` moves that toll onto a moment nobody is waiting on, and
``CardColumn`` calls it a turn after it is first shown. What is asserted here is that the
warm-up happens, that it happens once, and that it does not happen on a widget it would
do something other than a flush on -- not the timing, which is a property of the machine.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QVBoxLayout, QWidget

from bartlebot.gui.game_embed.card_columns import CardColumn
from bartlebot.gui.game_embed.launch_cards import AccountCard
from bartlebot.gui.offscreen_warmup import WARMED_PROPERTY, warm_offscreen_render

ACCOUNTS = [
    {"username": "one@example.com", "alias": "One"},
    {"username": "two@example.com", "alias": "Two"},
]


class _CountingWidget(QWidget):
    """A widget that counts the offscreen renders asked of it."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.renders = 0

    def render(self, *args, **kwargs) -> None:
        self.renders += 1
        super().render(*args, **kwargs)


def _shown(qapp) -> tuple[QWidget, _CountingWidget]:
    """A shown host window and the counting child in it."""
    host = QWidget()
    child = _CountingWidget()
    QVBoxLayout(host).addWidget(child)
    host.show()
    qapp.processEvents()
    return host, child


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------


def test_warming_renders_the_widget_and_marks_its_window(qapp):
    host, child = _shown(qapp)

    warm_offscreen_render(child)

    assert child.renders == 1
    assert host.property(WARMED_PROPERTY) is True


def test_a_second_warm_up_in_the_same_window_does_nothing(qapp):
    host, child = _shown(qapp)
    warm_offscreen_render(child)

    warm_offscreen_render(child)

    assert child.renders == 1, "the flush is per window and has already been paid"
    assert host


def test_a_sibling_in_a_warmed_window_does_not_warm_again(qapp):
    host, child = _shown(qapp)
    other = _CountingWidget()
    host.layout().addWidget(other)
    other.show()
    warm_offscreen_render(child)

    warm_offscreen_render(other)

    assert other.renders == 0
    assert host


def test_each_window_is_warmed_on_its_own(qapp):
    host, child = _shown(qapp)
    dialog, dialog_child = _shown(qapp)
    warm_offscreen_render(child)

    warm_offscreen_render(dialog_child)

    assert dialog_child.renders == 1, "a dialog flushes its own tree, not the main window's"
    assert host and dialog


def test_a_hidden_widget_is_skipped_and_left_unwarmed(qapp):
    host, child = _shown(qapp)
    child.hide()
    qapp.processEvents()

    warm_offscreen_render(child)

    # Qt's render() unhides a hidden widget behind the caller's back to lay out its hidden
    # ancestors, which is a great deal more than the flush this is for.
    assert child.renders == 0
    assert host.property(WARMED_PROPERTY) is None, "the next show gets to try again"


# ---------------------------------------------------------------------------
# The column that asks for it
# ---------------------------------------------------------------------------


def test_showing_a_card_column_warms_its_window(qapp):
    column = CardColumn("Accounts", "hint", "Add account")
    column.set_cards([AccountCard(a, [], ACCOUNTS) for a in ACCOUNTS])
    host = QWidget()
    QVBoxLayout(host).addWidget(column)

    host.show()
    for _ in range(3):
        qapp.processEvents()

    assert host.property(WARMED_PROPERTY) is True, (
        "a drag would otherwise pay Qt's first offscreen render between two mouse moves"
    )
