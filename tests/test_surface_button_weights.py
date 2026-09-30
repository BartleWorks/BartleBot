"""The weights that are load-bearing on the surfaces converted in issue #90, commit 2.

Unlike the inspector rows, these surfaces do have something to say about their buttons,
and each assertion below is a decision someone could undo without noticing: a Delete that
stops being a ``CAUTION``, a second ``PRIMARY`` on a surface that already has one, and one
button in the whole app that must *not* become an ``ActionButton`` at all.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QPushButton

from bartlebot.bot.schema import BotOptionModel
from bartlebot.gui.widgets.action_button import ActionButton, ActionStyle

#: The library toolbar's own two, picked out by label: the canvas also carries the
#: subgraph crumb, which is a separate surface with a PRIMARY of its own.
TOOLBAR_LABELS = {"New Bot", "Open Bot File…"}


def _option_dialog(*, editing: bool):
    from bartlebot.gui.sidebar.sections.options_section import _AddOptionDialog

    option = BotOptionModel(name="repeat", type="int", default=3) if editing else None
    return _AddOptionDialog(["repeat"] if editing else [], existing_option=option)


def test_deleting_an_option_is_a_caution(qapp):
    """It takes the declaration, both transition flag forms and the user's stored value —
    the row gutter was the wrong home for it, and the quiet weight is too."""
    dialog = _option_dialog(editing=True)

    delete = next(b for b in dialog.findChildren(ActionButton) if b.text() == "Delete")

    assert delete.action_style is ActionStyle.CAUTION


def test_the_option_dialog_has_one_primary(qapp):
    dialog = _option_dialog(editing=True)

    primaries = [b.text() for b in dialog.findChildren(ActionButton) if b.action_style is ActionStyle.PRIMARY]

    assert primaries == ["Save"]


def test_adding_an_option_offers_no_delete(qapp):
    """There is nothing to delete yet, and the footer must not invent one."""
    dialog = _option_dialog(editing=False)

    texts = [b.text() for b in dialog.findChildren(ActionButton)]

    assert texts[-2:] == ["Cancel", "Add"], "the footer, after whatever the default editor put above it"
    assert "Delete" not in texts


def test_the_node_browser_commit_takes_the_weight(qapp):
    from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog

    dialog = NodeBrowserDialog(mode="add")

    primaries = [b.text() for b in dialog.findChildren(ActionButton) if b.action_style is ActionStyle.PRIMARY]

    assert primaries == ["Add"]


def test_the_bot_library_toolbar_has_no_primary(qapp):
    """New Bot keeps the accent outline it already had — that is ``SECONDARY`` exactly —
    because what this panel expects is that you open one of the bots it is showing you."""
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    canvas = BotCanvas()

    toolbar = {b.text(): b.action_style for b in canvas.findChildren(ActionButton) if b.text() in TOOLBAR_LABELS}

    assert toolbar == {"New Bot": ActionStyle.SECONDARY, "Open Bot File…": ActionStyle.TERTIARY}


def test_reset_to_system_default_keeps_its_literal_styling(qapp):
    """The one deliberate exception in the rollout. This button is the escape hatch from
    a custom theme that has made the app unreadable, so it is styled in fixed colors that
    no theme can reach — which is exactly what an ``ActionButton`` would undo."""
    from bartlebot.gui.settings.theme_tab import ThemeTab

    tab = ThemeTab()
    reset = tab._reset_btn

    assert isinstance(reset, QPushButton)
    assert not isinstance(reset, ActionButton)
    assert "#f0f0f0" in reset.styleSheet()


@pytest.mark.parametrize("mode", ["add", "browse"])
def test_the_node_browser_closes_on_one_button_per_mode(qapp, mode):
    """Browse mode has no choice to make — its single button is the way out, not a commit
    weighed against a cancel, so it does not go through the footer."""
    from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog

    dialog = NodeBrowserDialog(mode=mode)

    texts = [b.text() for b in dialog.findChildren(ActionButton)]

    assert texts == (["Cancel", "Add"] if mode == "add" else ["Close"])
