"""Quicksell's settings on the Setup surface.

Quicksell used to carry five node params.  Four of them described the *user* --
what they are willing to lose -- and one described the *dungeon*: which of its
drops are worth keeping.  Params could express neither, so all five shipped in
the bot file, which is the epic's own bug at node scale.

They are bot options now, and this covers the half of that which only shows up
once a real surface renders them:

* the keep-list picker is reached through the shared option-type mapping, not a
  branch of its own, and lands last so a 220px table does not push the short rows
  off the bottom of the sidebar;
* the three sell flags are separate rows on this same surface, and each of them
  changes what the picker says will happen -- so flipping one has to reach the
  table;
* editing any of it writes an override and leaves the bot file alone.

Real widget tests (QApplication required).  The settings store is injected, so
nothing here touches the real ``settings.json``.
"""

from __future__ import annotations

import sys

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from bartlebot import settings_manager as sm_mod
from bartlebot.bot.schema import BotConfig, BotOptionModel, DropModel, StateModel
from bartlebot.gui.canvas.drop_keep_list import _COL_ITEM, _COL_KEEP, DropKeepListWidget
from bartlebot.gui.sidebar.sections.setup_section import SetupSection
from bartlebot.gui.widgets.link_button import LinkButton
from bartlebot.nodes.utility import quicksell as qs
from bartlebot.settings_manager import SettingsManager


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(sm_mod, "_appdata_dir", lambda: tmp_path)
    return SettingsManager()


_DROPS = [
    DropModel(name="Amber Ring", category="Rings"),
    DropModel(name="Onyx Ring", category="Rings"),
    DropModel(name="Fierce Hound", category="Pets"),
    DropModel(name="Socket Wrench"),
]


def _config(options=None, drops=None) -> BotConfig:
    return BotConfig(
        id="quicksell-bot",
        name="Quicksell Bot",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="quicksell", params={})],
        transitions=[],
        options=options if options is not None else [],
        drops=list(_DROPS if drops is None else drops),
    )


def _section(settings, config) -> SetupSection:
    section = SetupSection(settings=settings)
    section.set_config(config)
    return section


def _picker(section) -> DropKeepListWidget:
    return section.option_widgets[qs.OPTION_KEEP_LIST]


def _row(widget, name):
    return next(r for r in widget._rows if r.text(_COL_ITEM) == name)


# ---------------------------------------------------------------------------
# The rows
# ---------------------------------------------------------------------------


def test_every_quicksell_option_gets_a_row(qapp, settings):
    section = _section(settings, _config())

    names = set(section.option_widgets)

    assert names >= {qs.OPTION_ENABLED, qs.OPTION_KEEP_LIST, *qs.SELL_FLAG_OPTIONS.values()}


def test_the_keep_list_is_the_picker_and_it_comes_last(qapp, settings):
    """Reached through the shared option-type mapping — the picker is what
    ``drop_keep_list`` means — and put last because it is a table, not a field."""
    section = _section(settings, _config())

    assert isinstance(_picker(section), DropKeepListWidget)
    assert list(section.option_widgets)[-1] == qs.OPTION_KEEP_LIST


def test_the_picker_lists_this_bots_drops(qapp, settings):
    section = _section(settings, _config())

    assert [r.text(_COL_ITEM) for r in _picker(section)._rows] == [d.name for d in _DROPS]


def test_refresh_drops_picks_up_a_drop_added_in_possible_drops(qapp, settings):
    """Possible Drops lives in the other sidebar, so the edit arrives as a signal."""
    config = _config()
    section = _section(settings, config)
    config.drops.append(DropModel(name="Onyx", category="Jewels"))

    section.refresh_drops()

    assert "Onyx" in [r.text(_COL_ITEM) for r in _picker(section)._rows]


# ---------------------------------------------------------------------------
# The sell flags reach the picker
# ---------------------------------------------------------------------------


def test_the_picker_shows_what_the_sell_flags_will_actually_do(qapp, settings):
    """Sell Pets off spares every pet whatever the keep list says, so the pet row is
    checked and locked rather than showing as sold."""
    settings.set_general("default_quicksell_sell_pets", False)
    section = _section(settings, _config())

    row = _row(_picker(section), "Fierce Hound")

    assert row.checkState(_COL_KEEP) == Qt.CheckState.Checked
    assert not row.flags() & Qt.ItemFlag.ItemIsEnabled


def test_flipping_a_sell_flag_reaches_the_picker(qapp, settings):
    """The flag is a row on this same surface, so the table under it has to move."""
    section = _section(settings, _config())
    assert _row(_picker(section), "Fierce Hound").checkState(_COL_KEEP) == Qt.CheckState.Unchecked

    section.option_widgets[qs.SELL_FLAG_OPTIONS["sell_pets"]].setChecked(False)

    assert _row(_picker(section), "Fierce Hound").checkState(_COL_KEEP) == Qt.CheckState.Checked


def test_a_sell_flag_off_shows_its_implied_keyword_in_the_picker(qapp, settings):
    """Sell Socket Wrenches off is a keep-list keyword the user never typed. The union
    of the two mechanisms happens in one place, and this is that place rendering it.

    Started from *on* — the shipped answer is off, so a test that only turned it off
    would assert what was already true before it touched anything."""
    settings.set_general("default_quicksell_sell_socket_wrenches", True)
    section = _section(settings, _config())
    assert _row(_picker(section), "Socket Wrench").checkState(_COL_KEEP) == Qt.CheckState.Unchecked

    section.option_widgets[qs.SELL_FLAG_OPTIONS["sell_socket_wrenches"]].setChecked(False)

    row = _row(_picker(section), "Socket Wrench")
    assert row.checkState(_COL_KEEP) == Qt.CheckState.Checked
    assert not row.flags() & Qt.ItemFlag.ItemIsEnabled


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


def test_ticking_a_drop_writes_an_override_and_not_the_bot_file(qapp, settings):
    """The keep list is the one authorial thing here, and a user's list *replaces* the
    author's — the picker renders the resolved value and writes its whole state, so a
    tick yields the author's list plus the item without any seeding step."""
    config = _config([BotOptionModel(name=qs.OPTION_KEEP_LIST, type="drop_keep_list", default=["Amber Ring"])])
    section = _section(settings, config)

    _row(_picker(section), "Onyx Ring").setCheckState(_COL_KEEP, Qt.CheckState.Checked)

    assert settings.get_option_override(config.id, qs.OPTION_KEEP_LIST) == ["Amber Ring", "Onyx Ring"]
    assert config.options[0].default == ["Amber Ring"]


def test_the_users_keep_list_is_what_the_picker_shows(qapp, settings):
    config = _config([BotOptionModel(name=qs.OPTION_KEEP_LIST, type="drop_keep_list", default=["Amber Ring"])])
    settings.set_option_override(config.id, qs.OPTION_KEEP_LIST, ["Onyx Ring"])

    section = _section(settings, config)

    assert _picker(section).get_value() == ["Onyx Ring"]


def test_resetting_the_keep_list_puts_the_authors_back(qapp, settings):
    config = _config([BotOptionModel(name=qs.OPTION_KEEP_LIST, type="drop_keep_list", default=["Amber Ring"])])
    settings.set_option_override(config.id, qs.OPTION_KEEP_LIST, ["Onyx Ring"])
    section = _section(settings, config)

    section._on_reset(qs.OPTION_KEEP_LIST)

    assert _picker(section).get_value() == ["Amber Ring"]
    assert not settings.get("bot_option_overrides", default={}).get(config.id, {})


def test_turning_quicksell_off_writes_an_override(qapp, settings):
    config = _config()
    section = _section(settings, config)

    section.option_widgets[qs.OPTION_ENABLED].setChecked(False)

    assert settings.get_option_override(config.id, qs.OPTION_ENABLED) is False
    assert config.options == []


# ---------------------------------------------------------------------------
# The gutter
# ---------------------------------------------------------------------------


def _revert_button(section, name):
    return section._chrome[name].revert_btn


def _x(section, widget) -> int:
    return widget.mapTo(section, widget.rect().topLeft()).x()


def test_the_gutter_buttons_line_up_across_every_shape_of_row(qapp, settings):
    """The property the gutter exists for, and the one a later refactor would
    quietly break. Reset used to live in two places -- on the head line for a bool
    or a table, beside the control otherwise -- and the ``?`` followed the name, so
    both staircased down the panel."""
    section = _section(settings, _config())
    section.resize(400, 900)
    section.show()
    qapp.processEvents()
    try:
        rows = [qs.OPTION_ENABLED, "Safety Delay", qs.OPTION_KEEP_LIST]  # bool, float, tall
        revert_x = {_x(section, _revert_button(section, name)) for name in rows}
        help_x = {_x(section, section._chrome[name].help_btn) for name in rows}

        assert len(revert_x) == 1
        assert len(help_x) == 1
        # Revert is against the bar it agrees with; the destructive one is furthest
        # from the text being read.
        assert revert_x.pop() < help_x.pop()
    finally:
        section.hide()


def test_the_gutter_keeps_its_help_slot_on_a_row_with_nothing_to_explain(qapp, settings):
    """A gutter that closes up on undescribed options is not a column any more."""
    options = [BotOptionModel(name="Passes", type="int", default=1)]  # no description
    section = _section(settings, _config(options=options))
    section.resize(400, 900)
    section.show()
    qapp.processEvents()
    try:
        help_btn = section._chrome["Passes"].help_btn

        assert not help_btn.isVisible()
        assert _x(section, section._chrome["Passes"].revert_btn) == _x(section, _revert_button(section, "Safety Delay"))
        assert _x(section, help_btn) == _x(section, section._chrome["Safety Delay"].help_btn)
    finally:
        section.hide()


def test_the_table_keeps_the_whole_width_of_its_row(qapp, settings):
    """Nothing shares the value row with a 220px table any more, which is what the
    trailing Reset used to do -- taking its width from the last column for the whole
    height of the table."""
    section = _section(settings, _config())
    section.resize(400, 900)
    section.show()
    qapp.processEvents()
    try:
        picker = _picker(section)

        assert picker.width() == picker.parentWidget().width()
    finally:
        section.hide()


def test_reverting_still_drops_the_override(qapp, settings):
    config = _config()
    section = _section(settings, config)

    section.option_widgets[qs.OPTION_ENABLED].setChecked(False)
    assert settings.get_option_override(config.id, qs.OPTION_ENABLED) is False

    _revert_button(section, qs.OPTION_ENABLED).click()

    assert not settings.get("bot_option_overrides", default={}).get(config.id, {})
    assert section.option_widgets[qs.OPTION_ENABLED].isChecked() is True


# ---------------------------------------------------------------------------
# Descriptions
# ---------------------------------------------------------------------------


def _description(section, name):
    return section._chrome[name].description


def _help_button(section, name):
    return section._chrome[name].help_btn


def test_descriptions_are_folded_away_until_asked_for(qapp, settings):
    """41% of an ordinary row was node-authored prose, at 9px, on every row at once."""
    section = _section(settings, _config())
    section.show()
    qapp.processEvents()
    try:
        assert not any(_description(section, n).isVisible() for n in section.option_widgets)
    finally:
        section.hide()


def test_the_help_button_unfolds_one_row_and_only_that_row(qapp, settings):
    section = _section(settings, _config())
    section.show()
    qapp.processEvents()
    try:
        _help_button(section, qs.OPTION_ENABLED).setChecked(True)

        assert _description(section, qs.OPTION_ENABLED).isVisible()
        assert not _description(section, "Sell Pets").isVisible()
    finally:
        section.hide()


def test_a_description_is_read_at_the_base_font(qapp, settings):
    """The 9px the captions use is 75% of base and fringes under ClearType. A
    description is the one piece of prose here, and it is only on screen when asked
    for, so it has no reason to be the smallest text on the surface."""
    section = _section(settings, _config())

    assert "font-size" not in _description(section, qs.OPTION_ENABLED).styleSheet()


def test_the_switch_shows_every_description_and_is_remembered(qapp, settings):
    section = _section(settings, _config())
    section.show()
    qapp.processEvents()
    try:
        section.set_descriptions_visible(True)

        assert all(_description(section, n).isVisible() for n in section.option_widgets)
        assert settings.get("general", "show_option_descriptions") is True
    finally:
        section.hide()


def test_both_switches_are_offered_at_once(qapp, settings):
    """A single toggle names whichever half it last set, not what the rows are doing."""
    section = _section(settings, _config())

    labels = [b.text() for b in section.findChildren(LinkButton)]

    assert labels == ["Show descriptions", "Hide descriptions", "Open app settings"]


def test_the_app_settings_link_asks_rather_than_opening_the_dialog(qapp, settings):
    """The dialog is modal, so only a synchronous slot in the window may ``exec()`` it.

    The link also rides the switches row rather than each hint that names the app
    setting, so the section carries exactly one of it however many rows resolve there.
    """
    section = _section(settings, _config())
    seen = []
    section.app_settings_requested.connect(lambda: seen.append(True))

    links = [b for b in section.findChildren(LinkButton) if b.text() == "Open app settings"]
    assert len(links) == 1
    links[0].click()

    assert seen == [True]


def test_a_switch_still_acts_on_rows_the_help_button_moved(qapp, settings):
    """The per-row ``?`` leaves the surface mixed; an unconditional command has to
    reach the rows that already agree with the remembered default."""
    section = _section(settings, _config())
    section.show()
    qapp.processEvents()
    try:
        _help_button(section, qs.OPTION_ENABLED).setChecked(True)

        section.set_descriptions_visible(False)

        assert not any(_description(section, n).isVisible() for n in section.option_widgets)

        _help_button(section, qs.OPTION_ENABLED).setChecked(False)
        section.set_descriptions_visible(True)

        assert all(_description(section, n).isVisible() for n in section.option_widgets)
    finally:
        section.hide()


def test_a_remembered_switch_builds_the_rows_already_unfolded(qapp, settings):
    settings.set("general", "show_option_descriptions", value=True)

    section = _section(settings, _config())
    section.show()
    qapp.processEvents()
    try:
        assert all(_description(section, n).isVisible() for n in section.option_widgets)
        assert all(_help_button(section, n).isChecked() for n in section.option_widgets)
    finally:
        section.hide()


def test_the_source_caption_shares_the_name_line(qapp, settings):
    """It said four words and cost every row a line of its own."""
    section = _section(settings, _config())
    section.resize(400, 900)
    section.show()
    qapp.processEvents()
    try:
        caption = section._chrome["Safety Delay"].caption
        widget = section.option_widgets["Safety Delay"]

        assert caption.text() == "App settings"
        assert (
            caption.mapTo(section, caption.rect().bottomLeft()).y()
            <= widget.mapTo(section, widget.rect().topLeft()).y()
        )
    finally:
        section.hide()
