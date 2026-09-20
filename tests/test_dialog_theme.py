"""The four standalone dialogs, which are the ones we chose not to exempt.

None of them can be caught stale in the app as it stands: the settings dialog is
modal and applies the theme only after it closes, so no other dialog is alive when a
switch happens. That is a fact about modality rather than about these widgets — make
settings non-modal, or add the live preview a theme tab invites, and all four break
at once. They were fixed rather than allowlisted for that reason.
"""

import pytest
from PyQt6.QtCore import QEvent

import bartlebot.gui.theme as theme_mod
from bartlebot.gui.combat_strategies.bot_strategy_picker import BotStrategyPicker
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.gui.wiki_import_dialog import WikiImportDialog


@pytest.fixture
def theme():
    before = theme_mod.active_theme()
    yield theme_mod.set_theme
    theme_mod.set_theme_instance(before)


def _switch(qapp, widget, theme, mode: str) -> None:
    theme(mode)
    qapp.sendEvent(widget, QEvent(QEvent.Type.StyleChange))


def _switch_app(qapp, theme, mode: str) -> None:
    theme(mode)
    qapp.setStyleSheet(THEME.global_stylesheet())


# ---------------------------------------------------------------------------
# WikiImportDialog — the only one with state-dependent colour
# ---------------------------------------------------------------------------


def test_an_error_status_stays_an_error_colour_after_a_switch(qapp, theme):
    """The status colour says whether the last message was an error. Restyling from
    the stylesheet already on the label would have to parse it back out; the flag is
    what the restyle re-derives from instead."""
    theme("light")
    dlg = WikiImportDialog()
    dlg._set_status("Something went wrong", error=True)
    assert THEME.error in dlg._status_lbl.styleSheet()

    _switch(qapp, dlg, theme, "dark")

    assert THEME.error in dlg._status_lbl.styleSheet()
    assert THEME.text_muted not in dlg._status_lbl.styleSheet()
    dlg.deleteLater()


def test_a_normal_status_stays_muted_after_a_switch(qapp, theme):
    theme("light")
    dlg = WikiImportDialog()
    dlg._set_status("Fetching…")
    light_muted = THEME.text_muted

    _switch(qapp, dlg, theme, "dark")

    assert THEME.text_muted in dlg._status_lbl.styleSheet()
    assert light_muted not in dlg._status_lbl.styleSheet()
    dlg.deleteLater()


def test_the_select_buttons_recolour(qapp, theme):
    """They are ``ActionButton``s now (#90), so they restyle *themselves* rather than
    being restyled by the dialog. That is a broadcast to every widget rather than one
    event delivered to this dialog, which is what an app-wide switch actually is."""
    theme("light")
    dlg = WikiImportDialog()
    light_surface = THEME.surface

    _switch_app(qapp, theme, "dark")

    for btn in (dlg._btn_all, dlg._btn_none):
        assert THEME.surface in btn.styleSheet()
        assert light_surface not in btn.styleSheet()
    dlg.deleteLater()


def test_setting_a_status_does_not_lose_the_message(qapp, theme):
    """_set_status now routes through the restyle, so the text has to survive it."""
    theme("light")
    dlg = WikiImportDialog()

    dlg._set_status("Fetched 12 items")

    assert dlg._status_lbl.text() == "Fetched 12 items"
    dlg.deleteLater()


# ---------------------------------------------------------------------------
# BotStrategyPicker — the deck picker's list style, on the one picker that outlived it
# ---------------------------------------------------------------------------


def test_the_strategy_list_recolours(qapp, theme):
    """Checked on the fill rather than the border: the list is borderless now — the cards
    in it draw their own edges, and a box around a column of boxes was one line too many.
    The fill is the one THEME-derived value left, so it is what proves the re-derive."""
    theme("light")
    dlg = BotStrategyPicker([], current_id=None)
    light_bg = THEME.bg

    _switch(qapp, dlg, theme, "dark")

    assert THEME.bg in dlg._list.styleSheet()
    assert light_bg not in dlg._list.styleSheet()
    dlg.deleteLater()


# ---------------------------------------------------------------------------
# The two browsers, whose status labels became ThemedTextLabels
# ---------------------------------------------------------------------------


def test_the_entity_browser_status_follows_the_theme(qapp, theme):
    from bartlebot.gui.entity_browser import EntityBrowserDialog

    theme("light")
    dlg = EntityBrowserDialog(lambda: [])
    assert isinstance(dlg._status, ThemedTextLabel)
    before = dlg._status.styleSheet()

    _switch_app(qapp, theme, "dark")

    assert dlg._status.styleSheet() != before
    dlg.deleteLater()


# ---------------------------------------------------------------------------
# Tab pane chrome — the strip's line, and nothing else
# ---------------------------------------------------------------------------


def test_the_tab_pane_draws_only_the_line_under_the_strip(qapp, theme):
    """That line anchors the tabs to the page and the selected tab's fill matches the
    pane, so the two read as continuous. The other three sides drew a box around a page
    whose own contents already say where they stop."""
    theme("dark")
    pane = THEME.global_stylesheet().split("QTabWidget::pane {")[1].split("}")[0]

    assert "border: none;" in pane
    assert f"border-top: 1px solid {THEME.border};" in pane
