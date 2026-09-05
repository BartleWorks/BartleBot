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
from bartlebot.gui.strategy_library.deck_preset_picker_dialog import DeckPresetPickerDialog
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
    theme("light")
    dlg = WikiImportDialog()
    light_accent = THEME.accent

    _switch(qapp, dlg, theme, "dark")

    for btn in (dlg._btn_all, dlg._btn_none):
        assert THEME.accent in btn.styleSheet()
        assert light_accent not in btn.styleSheet()
    dlg.deleteLater()


def test_setting_a_status_does_not_lose_the_message(qapp, theme):
    """_set_status now routes through the restyle, so the text has to survive it."""
    theme("light")
    dlg = WikiImportDialog()

    dlg._set_status("Fetched 12 items")

    assert dlg._status_lbl.text() == "Fetched 12 items"
    dlg.deleteLater()


# ---------------------------------------------------------------------------
# DeckPresetPickerDialog
# ---------------------------------------------------------------------------


def test_the_preset_list_recolours(qapp, theme):
    theme("light")
    dlg = DeckPresetPickerDialog([])
    light_border = THEME.border

    _switch(qapp, dlg, theme, "dark")

    assert THEME.border in dlg._list.styleSheet()
    assert light_border not in dlg._list.styleSheet()
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
