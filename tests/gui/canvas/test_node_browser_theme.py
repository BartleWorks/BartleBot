"""The node browser's two halves follow a theme switch by different means.

``NodeBrowserDialog`` restyles four long-lived widgets in place. ``_DetailPanel``
cannot: ``show_node`` throws its whole container away and builds a new one on every
selection, so there is nothing stable to hold — it replays the last call instead.
That distinction is the thing worth pinning, along with the scroll position it has
to preserve to be usable while reading.
"""

import bartleby.qt.theming.theme as theme_mod
import pytest
from bartleby.qt.theming.theme import THEME
from PyQt6.QtCore import QEvent

from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog, _DetailPanel
from bartlebot.nodes.registry import NODE_SPECS


@pytest.fixture
def theme():
    before = theme_mod.active_theme()
    yield theme_mod.set_theme
    theme_mod.set_theme_instance(before)


def _switch(qapp, widget, theme, mode: str) -> None:
    theme(mode)
    qapp.sendEvent(widget, QEvent(QEvent.Type.StyleChange))
    # _DetailPanel rebuilds a turn later — it deletes widgets, which a StyleChange
    # handler must not do while the app is iterating over them.
    for _ in range(5):
        qapp.processEvents()


def _switch_app(qapp, theme, mode: str) -> None:
    """The app-wide broadcast, which is what a real theme switch is.

    An ``ActionButton`` restyles itself off its own ``StyleChange``, so an event sent to
    its dialog alone never reaches it — see gui/widgets/buttons/action_button.py.
    """
    theme(mode)
    qapp.setStyleSheet(THEME.global_stylesheet())


def _a_node() -> tuple[str, object]:
    node_type = next(iter(NODE_SPECS))
    return node_type, NODE_SPECS[node_type]


def test_the_detail_panel_rebuilds_on_a_theme_switch(qapp, theme):
    theme("light")
    panel = _DetailPanel()
    node_type, spec = _a_node()
    panel.show_node(node_type, spec)
    before = panel.widget()

    _switch(qapp, panel, theme, "dark")

    assert panel.widget() is not before, "the container should have been replaced"
    assert panel._node_type == node_type


def test_the_rebuild_does_not_happen_inside_the_style_change_handler(qapp, theme):
    """setWidget destroys the old container outright, and the switch arrives while
    QApplication.setStyleSheet is iterating over every widget — freeing a subtree
    there aborts the process with no traceback. So the rebuild waits a turn."""
    theme("light")
    panel = _DetailPanel()
    node_type, spec = _a_node()
    panel.show_node(node_type, spec)
    before = panel.widget()

    theme("dark")
    qapp.sendEvent(panel, QEvent(QEvent.Type.StyleChange))

    assert panel.widget() is before, "the container was replaced synchronously"
    for _ in range(5):
        qapp.processEvents()
    assert panel.widget() is not before


def test_the_placeholder_recolors_before_any_node_is_shown(qapp, theme):
    """A panel opened and left alone is the state the dialog starts in."""
    theme("light")
    panel = _DetailPanel()
    before = panel.widget().styleSheet()

    _switch(qapp, panel, theme, "dark")

    assert panel.widget().styleSheet() != before
    assert THEME.text_muted in panel.widget().styleSheet()


def test_a_theme_switch_keeps_the_scroll_position(qapp, theme):
    """show_node scrolls to top because the node changed. A theme switch did not
    change the node, and dumping the reader back to the top would be a regression
    the rebuild introduced."""
    theme("light")
    panel = _DetailPanel()
    # show() before resize(), and several event cycles: without a laid-out viewport
    # the container is sized to fit and the scrollbar range is 0, which makes every
    # assertion about scrolling trivially true.
    panel.show()
    panel.resize(300, 60)
    panel.show_node("click_path", NODE_SPECS["click_path"])
    for _ in range(5):
        qapp.processEvents()
    assert panel.verticalScrollBar().maximum() > 0, "no scroll range — the test would prove nothing"
    panel.verticalScrollBar().setValue(40)

    _switch(qapp, panel, theme, "dark")
    for _ in range(5):
        qapp.processEvents()

    assert panel.verticalScrollBar().value() == 40
    panel.close()


def test_the_dialog_chrome_recolors(qapp, theme):
    theme("light")
    dlg = NodeBrowserDialog(mode="add")
    before = (dlg._search.styleSheet(), dlg._list.styleSheet(), dlg._add_btn.styleSheet())

    _switch_app(qapp, theme, "dark")

    after = (dlg._search.styleSheet(), dlg._list.styleSheet(), dlg._add_btn.styleSheet())
    assert all(a != b for a, b in zip(before, after))
    dlg.deleteLater()


def test_browse_mode_has_no_add_button_to_restyle(qapp, theme):
    """_add_btn exists only in add mode; restyling must tolerate its absence."""
    theme("light")
    dlg = NodeBrowserDialog(mode="browse")
    assert dlg._add_btn is None

    _switch(qapp, dlg, theme, "dark")  # must not raise

    assert THEME.border in dlg._search.styleSheet()
    dlg.deleteLater()


def test_switching_node_cancels_a_pending_scroll_restore(qapp, theme):
    """The restore waits for a range that a short doc never gets, so it can still be
    pending when the user picks a different node. Applying it then would scroll the
    new node's page to the old one's offset."""
    theme("light")
    panel = _DetailPanel()
    panel.show()
    panel.resize(300, 60)
    panel.show_node("click_path", NODE_SPECS["click_path"])
    for _ in range(5):
        qapp.processEvents()
    panel.verticalScrollBar().setValue(40)

    _switch(qapp, panel, theme, "dark")  # arms a restore
    other = next(k for k in NODE_SPECS if k != "click_path")
    panel.show_node(other, NODE_SPECS[other])
    for _ in range(5):
        qapp.processEvents()

    assert panel._pending_scroll is None
    assert panel.verticalScrollBar().value() == 0
    panel.close()
