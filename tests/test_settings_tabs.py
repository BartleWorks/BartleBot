"""The settings dialog's tabs: the Bot split, and descriptions that are visible.

Three things Phase 8 of #145 is meant to keep true, none of which the type system
or the older tests can see:

* every row explains itself **on the page**, not only on hover -- the Log, Layout
  and Auto-save rows carried no explanation at all, and the rest carried theirs
  where a user has to already suspect it is there;
* the Safety Delay app setting and the Safety Delay *option* are one value, so
  they must not come to be described two different ways;
* every key in ``_DEFAULTS`` is declared on its section's pydantic model, or it
  is silently never type-checked.
"""

from __future__ import annotations

import sys

import pytest
from PyQt6.QtWidgets import QApplication, QFormLayout

from bartlebot import bot_options
from bartlebot.gui import screen_fit
from bartlebot.gui.settings.bot_tab import BotTab
from bartlebot.gui.settings.canvas_tab import CanvasTab
from bartlebot.gui.settings.general_tab import GeneralTab
from bartlebot.gui.settings.rows import ScrollableTab
from bartlebot.gui.settings.settings_dialog import SettingsDialog
from bartlebot.gui.settings.theme_tab import ThemeTab
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.nodes.registry import NODE_SPECS
from bartlebot.settings_manager import _DEFAULTS, _SECTION_MODELS, SettingsManager


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


# ---------------------------------------------------------------------------
# The split itself
# ---------------------------------------------------------------------------


def test_the_bot_settings_have_a_tab_of_their_own(qapp):
    """``open_tab`` is the only route in, so a tab it cannot name is unreachable."""
    dlg = SettingsDialog()
    titles = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert titles == ["General", "Bot", "Canvas", "Theme", "Game"]

    dlg.open_tab("bot")
    assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == "Bot"


def test_accepting_the_dialog_applies_every_tab(qapp):
    """A tab left out of ``apply_all`` silently discards everything typed into it.

    AccountsTab's own ``apply`` is a documented no-op -- it persists eagerly --
    so every tab in the bar is applied, with no list to keep in step.
    """
    dlg = SettingsDialog()
    tabs = [dlg.tabs.widget(i) for i in range(dlg.tabs.count())]
    called: list[object] = []
    for tab in tabs:
        tab.apply = lambda tab=tab: called.append(tab)

    dlg.apply_all()

    assert called == tabs


def test_the_bot_tab_writes_what_it_was_given(qapp):
    """Round trip through the settings singleton, which is the tab's only output."""
    tab = BotTab()
    _field(tab, "Default Safety Delay:").setValue(7.5)
    _field(tab, "Enable Quicksell:").setChecked(False)
    _field(tab, "Friend teleport target:").setText("  Spare Idler  ")

    tab.apply()

    sm = SettingsManager.instance()
    assert sm.default_safety_delay == 7.5
    assert sm.get_general("default_quicksell_enabled") is False
    # Stripped, and remembered for the autocomplete list.
    assert sm.default_friend_name == "Spare Idler"
    assert "Spare Idler" in sm.recent_friend_names


# ---------------------------------------------------------------------------
# Descriptions
# ---------------------------------------------------------------------------


def _field(tab, label: str):
    """The control on the row captioned *label*, found the way a reader would."""
    for form in tab.findChildren(QFormLayout):
        for i in range(form.rowCount()):
            item = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
            if item is not None and item.widget() is not None and item.widget().text() == label:
                return form.itemAt(i, QFormLayout.ItemRole.FieldRole).widget()
    raise AssertionError(f"no settings row labelled {label!r}")


def _rows(tab) -> list[list]:
    """Every ``QFormLayout`` on *tab*, as a list of field widgets per row.

    A hint is added with the one-argument ``addRow``, which puts it in the *field*
    column with no label, so a row is a hint exactly when its field widget is a
    ``ThemedTextLabel``.
    """
    forms = tab.findChildren(QFormLayout)
    out = []
    for form in forms:
        row = []
        for i in range(form.rowCount()):
            item = form.itemAt(i, QFormLayout.ItemRole.FieldRole)
            if item is None:
                item = form.itemAt(i, QFormLayout.ItemRole.SpanningRole)
            row.append(item.widget() if item is not None else None)
        out.append(row)
    return out


@pytest.mark.parametrize("tab_cls", [GeneralTab, BotTab, CanvasTab, ThemeTab])
def test_every_settings_row_explains_itself_on_the_page(qapp, tab_cls):
    """No row may rely on a tooltip, or on nothing at all.

    Within two rows rather than one, because a pair that reads as a single
    setting -- Maximize on open and the window size under it -- shares one hint.
    """
    tab = tab_cls()
    unexplained = []
    for form in _rows(tab):
        for i, widget in enumerate(form):
            if isinstance(widget, ThemedTextLabel) or widget is None:
                continue
            following = form[i + 1 : i + 3]
            if not any(isinstance(w, ThemedTextLabel) for w in following):
                unexplained.append(f"{tab_cls.__name__} row {i}: {widget!r}")
    assert not unexplained, "settings rows with no visible description: " + ", ".join(unexplained)


def test_the_safety_delay_setting_and_option_read_the_same(qapp):
    """One value, one description.

    The app setting is the third of the four levels the *option* resolves
    through, so a reader who finds the two texts disagreeing has no way to tell
    that they are looking at the same number.
    """
    declared = {
        opt.description
        for spec in NODE_SPECS.values()
        for opt in spec.required_bot_options
        if opt.name == "Safety Delay"
    }
    assert declared == {bot_options.SAFETY_DELAY_DESCRIPTION}

    tab = BotTab()
    hints = [w.text() for form in _rows(tab) for w in form if isinstance(w, ThemedTextLabel)]
    assert bot_options.SAFETY_DELAY_DESCRIPTION in hints


# ---------------------------------------------------------------------------
# Settings schema
# ---------------------------------------------------------------------------


def test_every_default_is_declared_on_its_model():
    """A key in ``_DEFAULTS`` but not on the model is never validated.

    Pydantic ignores an extra key rather than rejecting it, so the section still
    loads -- and a hand-edited ``settings.json`` can then put a string where a
    bool belongs and have it reach the widget untouched. ``streamer_mode`` and
    ``bot_library_path`` were both in that state.
    """
    for section, model in _SECTION_MODELS.items():
        undeclared = set(_DEFAULTS[section]) - set(model.model_fields)
        assert not undeclared, f"{section}: {sorted(undeclared)} in _DEFAULTS but not on {model.__name__}"


# ---------------------------------------------------------------------------
# #175
# ---------------------------------------------------------------------------


def test_no_node_calls_a_player_a_client():
    """The word the user reads for one of their wizards is "player" (#175).

    "Client" is the app's word for the process: a window at character select is a
    client and not a player, which is exactly the distinction ``tile_slots.py``
    exists to keep. Using it for a wizard in node help made three ``player_list``
    params read "…Clients" over check boxes captioned "Player 1…4".

    Param *keys* are excluded: they are stored in every bot file and are never
    shown.
    """
    offenders = []
    for node_type, spec in NODE_SPECS.items():
        texts = [spec.description, spec.usage_notes, spec.safety_warning or ""]
        texts += [t for p in spec.params for t in (p.label, p.help, p.default_hint, p.placeholder)]
        texts += [r.description for r in spec.returns]
        texts += [o.description or "" for o in spec.required_bot_options]
        for text in texts:
            if "client" in text.lower():
                offenders.append(f"{node_type}: {text[:70]}…")
    assert not offenders, "node text saying 'client' where it means 'player': " + "; ".join(offenders)


# ---------------------------------------------------------------------------
# #188 — the dialog has to fit the screen it opens on
# ---------------------------------------------------------------------------


@pytest.fixture
def screen(monkeypatch):
    """Pin the screen the dialog is fitted to, so the result is not the runner's."""

    def _use(size):
        monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: (size, 1.0))

    return _use


def test_the_dialog_opens_inside_a_1080p_screen(qapp, screen):
    """It used to open as tall as its tallest tab, putting OK and Cancel off-screen."""
    screen((1920, 1080))
    dlg = SettingsDialog()

    assert dlg.height() <= 1080
    assert dlg.width() <= 1920
    # And it can never be dragged past the edge either.
    assert dlg.maximumHeight() <= 1080


def test_a_short_screen_shrinks_the_dialog_rather_than_the_other_way_round(qapp, screen):
    """A netbook-height screen: the cap, not the preferred size, decides."""
    screen((1024, 600))
    dlg = SettingsDialog()

    assert dlg.height() <= 600
    # Not shrunk past what the dialog can actually lay out.
    assert dlg.height() >= dlg.minimumHeight()


def test_the_form_tabs_scroll_rather_than_setting_the_dialogs_height(qapp, screen):
    """Every row carries a visible description, which is two rows of height each.

    The Game tab is deliberately not wrapped: its two lists carry the layout's
    stretch, so inside a scroll area they would collapse to their size hint.
    """
    screen((1920, 1080))
    dlg = SettingsDialog()
    wrapped = {dlg.tabs.tabText(i): isinstance(dlg.tabs.widget(i), ScrollableTab) for i in range(dlg.tabs.count())}

    assert wrapped == {"General": True, "Bot": True, "Canvas": True, "Theme": False, "Game": False}
    # The point of wrapping: the tallest tab no longer sets the dialog's height.
    assert dlg.height() < BotTab().sizeHint().height()


def test_a_wrapped_tab_still_applies_what_was_typed_into_it(qapp):
    """A bare QScrollArea in the tab bar would silently discard the whole tab."""
    tab = BotTab()
    wrapper = ScrollableTab(tab)
    _field(tab, "Default Safety Delay:").setValue(4.5)

    wrapper.apply()

    assert SettingsManager.instance().default_safety_delay == 4.5
    assert wrapper.content is tab
