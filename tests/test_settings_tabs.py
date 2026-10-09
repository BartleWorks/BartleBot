"""The settings dialog's tabs: the Bot split, and descriptions that are visible.

Three things Phase 8 of the bot-options split is meant to keep true, none of which the type system
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

import pytest
from PyQt6.QtWidgets import QFormLayout, QLabel

from bartlebot.gui import screen_fit
from bartlebot.gui.settings.accounts_tab import AccountsTab
from bartlebot.gui.settings.authoring_tab import AuthoringTab
from bartlebot.gui.settings.bot_tab import BotTab
from bartlebot.gui.settings.canvas_tab import CanvasTab
from bartlebot.gui.settings.general_tab import GeneralTab
from bartlebot.gui.settings.rows import ScrollableTab, SettingRow
from bartlebot.gui.settings.settings_dialog import SettingsDialog
from bartlebot.gui.settings.theme_tab import ThemeTab
from bartlebot.gui.widgets.rule_heading import RuleHeading
from bartlebot.nodes.registry import NODE_SPECS
from bartlebot.options import bot_options
from bartlebot.settings_manager import _DEFAULTS, _SECTION_MODELS, SettingsManager

# ---------------------------------------------------------------------------
# The split itself
# ---------------------------------------------------------------------------


def test_the_bot_settings_have_a_tab_of_their_own(qapp):
    """``open_tab`` is the only route in, so a tab it cannot name is unreachable."""
    dlg = SettingsDialog()
    titles = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert titles == ["General", "Bot", "Canvas", "Authoring", "Theme", "Game"]

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
    _field(tab, "Default Safety Delay").setValue(7.5)
    _field(tab, "Enable Quicksell").setChecked(False)
    _field(tab, "Friend teleport target").setText("  Spare Idler  ")

    tab.apply()

    sm = SettingsManager.instance()
    assert sm.default_safety_delay == 7.5
    assert sm.get_general("default_quicksell_enabled") is False
    # Stripped, and remembered for the autocomplete list.
    assert sm.default_friend_name == "Spare Idler"
    assert "Spare Idler" in sm.recent_friend_names


def test_the_users_own_keep_list_round_trips_a_name_with_a_comma_in_it(qapp):
    """#34. ``No Pain, No Gain Onyx`` is a real item, and a naive split makes it two keywords."""
    sm = SettingsManager.instance()
    sm.set_general("quicksell_keep_list", ["Amber", "No Pain, No Gain Onyx"])
    tab = BotTab()

    tab.apply()

    assert sm.get_general("quicksell_keep_list") == ["Amber", "No Pain, No Gain Onyx"]


def test_the_users_own_keep_list_is_written_from_what_was_typed(qapp):
    tab = BotTab()
    _field(tab, "Always keep").setText("Amber,  Pearl , ")

    tab.apply()

    assert SettingsManager.instance().get_general("quicksell_keep_list") == ["Amber", "Pearl"]


_SHOW_EXPLANATION = "Show Quicksell explanation before each run"


def test_the_quicksell_explanation_is_shown_on_a_fresh_install(qapp):
    assert _field(BotTab(), _SHOW_EXPLANATION).isChecked()


def test_the_quicksell_explanation_can_be_turned_back_on(qapp):
    """#145. The safety check's check box turns it off, so this is the way back."""
    sm = SettingsManager.instance()
    sm.show_quicksell_explanation = False
    tab = BotTab()
    box = _field(tab, _SHOW_EXPLANATION)
    assert not box.isChecked()

    box.setChecked(True)
    tab.apply()

    assert sm.show_quicksell_explanation is True


def test_the_quicksell_explanation_can_be_turned_off_here_too(qapp):
    tab = BotTab()
    _field(tab, _SHOW_EXPLANATION).setChecked(False)

    tab.apply()

    assert SettingsManager.instance().show_quicksell_explanation is False


def test_the_launch_options_are_on_the_game_tab(qapp):
    """A setting the app applies while a client starts is not a Bot tab row.

    Neither of these is a level of any bot option's resolution, and both happen
    to every launched client whether or not a bot is ever run -- which is what
    the Bot tab is for. The recovery message that tells a user where to turn
    auto-select on names the tab, so a move that misses it sends them nowhere.
    """
    tab = AccountsTab()
    assert _field(tab, "Automatically select a character") is not None
    assert _field(tab, "Disable quick friend teleport") is not None

    with pytest.raises(AssertionError):
        _field(BotTab(), "Automatically select a character")
    with pytest.raises(AssertionError):
        _field(BotTab(), "Disable quick friend teleport")


def test_the_game_tab_applies_its_launch_options(qapp):
    """The rest of the tab persists eagerly; these two wait for OK like any row.

    ``apply`` was a documented no-op for as long as the tab held nothing but
    cards, so a form row added to it without touching ``apply`` would be typed
    into and silently discarded.
    """
    sm = SettingsManager.instance()
    sm.auto_select_character = True
    sm.disable_quick_friend_teleport = True

    tab = AccountsTab()
    _field(tab, "Automatically select a character").setChecked(False)
    _field(tab, "Disable quick friend teleport").setChecked(False)

    tab.apply()

    assert sm.auto_select_character is False
    assert sm.disable_quick_friend_teleport is False


# ---------------------------------------------------------------------------
# Descriptions
# ---------------------------------------------------------------------------


def _field(tab, label: str):
    """The control on the row titled *label*, found the way a reader would."""
    for row in tab.findChildren(SettingRow):
        if row.title == label:
            return row.control
    raise AssertionError(f"no settings row titled {label!r}")


_ALL_TABS = [GeneralTab, BotTab, CanvasTab, AuthoringTab, ThemeTab, AccountsTab]


@pytest.mark.parametrize("tab_cls", _ALL_TABS)
def test_every_settings_row_explains_itself_on_the_page(qapp, tab_cls):
    """No row may rely on a tooltip, or on nothing at all.

    Its one line is always on screen; only the longer prose folds behind the ``?``.
    And no row is the old shape -- a ``QFormLayout`` label beside a control, with the
    description as a second row under it.
    """
    tab = tab_cls()
    rows = tab.findChildren(SettingRow)
    assert rows, f"{tab_cls.__name__} draws no setting rows"
    unexplained = [row.title for row in rows if not row.summary.text().strip() or row.summary.isHidden()]
    assert not unexplained, "settings rows with no visible description: " + ", ".join(unexplained)
    assert not [f for f in tab.findChildren(QFormLayout) if f.rowCount()], "a settings row built the old way"


# Not the Game tab: its two card columns carry CardColumn's own centered heading.
@pytest.mark.parametrize("tab_cls", [GeneralTab, BotTab, CanvasTab, AuthoringTab, ThemeTab])
def test_every_settings_section_is_a_rule_heading(qapp, tab_cls):
    """A section is a heading with a rule, not a bold line of text.

    Every row carries a visible description under it, which makes each tab long
    enough that a bold label alone does not stop the eye -- the Bot tab's five
    sections read as one unbroken column of rows. A bare bold ``QLabel`` at the
    left margin of the tab's own layout is what that looked like, so finding one
    means a section was added the old way.
    """
    tab = tab_cls()
    assert tab.findChildren(RuleHeading), f"{tab_cls.__name__} draws no section headings"

    bold_labels = [
        lbl.text()
        for lbl in tab.findChildren(QLabel)
        if lbl.font().bold() and not isinstance(lbl.parent(), RuleHeading)
    ]
    assert not bold_labels, f"{tab_cls.__name__} section headers not using RuleHeading: {bold_labels}"


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

    row = BotTab().row("Default Safety Delay")
    assert row.details is not None
    assert row.details.text() == bot_options.SAFETY_DELAY_DESCRIPTION


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
# The description-row rework
# ---------------------------------------------------------------------------


def test_no_node_calls_a_player_a_client():
    """The word the user reads for one of their wizards is "player".

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
# The dialog has to fit the screen it opens on
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

    assert wrapped == {
        "General": True,
        "Bot": True,
        "Canvas": True,
        "Authoring": True,
        "Theme": False,
        "Game": False,
    }
    # The point of wrapping: the tallest tab no longer sets the dialog's height. Read at
    # its tallest -- every description shown and every group open.
    tallest = BotTab()
    tallest.set_descriptions_shown(True)
    for group in tallest.groups:
        group.set_collapsed(False)
    assert dlg.height() < tallest.sizeHint().height()


def test_a_wrapped_tab_still_applies_what_was_typed_into_it(qapp):
    """A bare QScrollArea in the tab bar would silently discard the whole tab."""
    tab = BotTab()
    wrapper = ScrollableTab(tab)
    _field(tab, "Default Safety Delay").setValue(4.5)

    wrapper.apply()

    assert SettingsManager.instance().default_safety_delay == 4.5
    assert wrapper.content is tab


@pytest.mark.parametrize(
    ("tab_cls", "title"),
    [(GeneralTab, "Maximize on open"), (CanvasTab, "Snap nodes to grid")],
)
def test_a_fresh_install_starts_with_these_on(qapp, tab_cls, title):
    """Both default to on, and on a fresh install the row is at its default, unmarked."""
    row = tab_cls().row(title)

    assert row.control.isChecked()
    assert not row.is_changed
