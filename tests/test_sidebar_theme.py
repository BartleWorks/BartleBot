"""The sidebar's themed widgets, and the factory fix behind most of them.

Three of the five classes in this batch — ClientWidget, SidebarField, OptionsSection
— had no THEME reference of their own. They were stale because they called a factory
that built a styled label and handed it back, leaving nothing able to restyle the
instance. Fixing the factory fixed all three without touching them, which is why the
tests here are mostly about ThemedTextLabel and the two factories that return it.
"""

import sys

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QSizePolicy

import bartlebot.gui.theme as theme_mod
from bartlebot.bot.schema import BotOptionModel
from bartlebot.gui.sidebar.sections.options_section import _AddOptionDialog, _build_option_parts
from bartlebot.gui.sidebar.sidebar_section import SidebarField, field_label
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.themed_label import ThemedTextLabel


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def theme():
    before = theme_mod.active_theme()
    yield theme_mod.set_theme
    theme_mod.set_theme_instance(before)


def _switch(qapp, widget, theme, mode: str) -> None:
    """Deliver StyleChange to one widget."""
    theme(mode)
    qapp.sendEvent(widget, QEvent(QEvent.Type.StyleChange))


def _switch_app(qapp, theme, mode: str) -> None:
    """What AppController._apply_theme does: broadcast to every widget.

    Needed wherever the styling is owned by a *child* — sendEvent reaches one widget,
    while setStyleSheet on the QApplication reaches the whole tree, which is how a
    ThemedTextLabel nested in a plain parent gets restyled in the real app.
    """
    theme(mode)
    qapp.setStyleSheet(THEME.global_stylesheet())


# ---------------------------------------------------------------------------
# ThemedTextLabel
# ---------------------------------------------------------------------------


def test_it_recolours(qapp, theme):
    theme("light")
    lbl = ThemedTextLabel("hi")
    assert THEME.text_muted in lbl.styleSheet()
    light = THEME.text_muted

    _switch(qapp, lbl, theme, "dark")

    assert THEME.text_muted in lbl.styleSheet()
    assert light not in lbl.styleSheet()


def test_it_keeps_its_extra_css(qapp, theme):
    """The size and weight live in the same string as the colour, so a restyle that
    rebuilt only the colour would silently drop them."""
    theme("light")
    lbl = ThemedTextLabel("hi", css="font-size: 9px;")

    _switch(qapp, lbl, theme, "dark")

    assert "font-size: 9px" in lbl.styleSheet()


def test_the_colour_token_is_resolved_at_apply_time(qapp, theme):
    """Storing the token name rather than the colour is the whole mechanism: a
    resolved value would be the snapshot this exists to avoid."""
    theme("light")
    lbl = ThemedTextLabel("hi", color="error")
    assert THEME.error in lbl.styleSheet()

    _switch(qapp, lbl, theme, "dark")

    assert THEME.error in lbl.styleSheet()


# ---------------------------------------------------------------------------
# The two factories
# ---------------------------------------------------------------------------


def test_field_label_returns_something_that_follows_the_theme(qapp, theme):
    theme("light")
    lbl = field_label("Zone")
    light = THEME.text_muted

    _switch(qapp, lbl, theme, "dark")

    assert light not in lbl.styleSheet()
    assert "font-weight: 600" in lbl.styleSheet()


def test_field_label_still_reads_as_a_caption(qapp, theme):
    """The colon and the fixed width are the caption, not decoration.

    Every caller passes a bare word, so the colon has to come from here; and the
    fixed horizontal policy is what holds the label column steady in ClientWidget's
    rows, whose value labels are added with stretch. Both were dropped when this
    factory stopped styling a bare QLabel, and nothing asserted either.
    """
    theme("light")
    lbl = field_label("Zone")

    assert lbl.text() == "Zone:"
    assert lbl.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed
    assert lbl.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Preferred


def test_a_sidebar_field_recolours_its_caption(qapp, theme):
    """SidebarField never referenced THEME — it was stale purely through field_label."""
    theme("light")
    field = SidebarField("Zone", ThemedTextLabel("value"))
    caption = field.findChild(ThemedTextLabel)
    assert caption is not None
    before = caption.styleSheet()

    _switch_app(qapp, theme, "dark")

    assert caption.styleSheet() != before


def test_an_option_description_recolours(qapp, theme):
    theme("light")
    opt = BotOptionModel(name="Farm", type="bool", description="Keep farming", default=True)
    _row, _widget, desc = _build_option_parts(opt)
    assert desc is not None
    light = THEME.text_muted

    _switch(qapp, desc, theme, "dark")

    assert light not in desc.styleSheet()
    assert "font-size: 9px" in desc.styleSheet()


# ---------------------------------------------------------------------------
# _AddOptionDialog
# ---------------------------------------------------------------------------


def test_the_add_option_error_label_recolours(qapp, theme):
    theme("light")
    dlg = _AddOptionDialog([])
    light = THEME.error

    _switch(qapp, dlg, theme, "dark")

    assert THEME.error in dlg._err_lbl.styleSheet()
    assert light not in dlg._err_lbl.styleSheet()
    dlg.deleteLater()


def test_the_add_option_dialog_still_builds_completely(qapp):
    """Its restyle was briefly spliced into the middle of __init__, which left the
    dialog half-constructed. The button row and defaults come after that point."""
    dlg = _AddOptionDialog([])

    assert dlg._name_edit is not None
    assert dlg._current_type() == "bool"
    assert dlg._default_widgets["bool"].value() is False
    assert dlg.layout() is not None
    dlg.deleteLater()
