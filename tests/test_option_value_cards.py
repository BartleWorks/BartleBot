"""The Option Values tab's folding cards (#138).

A bot's options are drawn as *This dungeon*, *Team*, *Quicksell* and *General* cards
rather than one flat list. These cover what the cards add on top of the rows
``test_setup_quicksell`` already covers: which card is which, the Quicksell switch,
the remembered fold state, the heading's Collapse all and Expand all, the card menu,
and the header's stripe, summary and warning.

Real widget tests; the settings store is injected, so nothing here touches the real
``settings.json``.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication

from bartlebot import settings_manager as sm_mod
from bartlebot.bot.schema import BotConfig, BotOptionModel, DropModel, StateModel
from bartlebot.gui.sidebar.option_card import COLLAPSE_ALL, COLLAPSE_OTHERS, EXPAND_ALL
from bartlebot.gui.sidebar.sections.option_values_section import OptionValuesSection
from bartlebot.gui.widgets.toggle_switch import ToggleSwitch
from bartlebot.nodes.utility import quicksell as qs
from bartlebot.options import option_groups as og
from bartlebot.options.bot_options import SOURCE_APP_SETTING, SOURCE_OVERRIDE
from bartlebot.settings_manager import SettingsManager


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(sm_mod, "_appdata_dir", lambda: tmp_path)
    return SettingsManager()


_DROPS = [DropModel(name="Amber Ring", category="Rings"), DropModel(name="Fierce Hound", category="Pets")]


def _config(options=None) -> BotConfig:
    """A dungeon entrance, a quicksell, and an option of the author's own."""
    return BotConfig(
        id="cards-bot",
        name="Cards Bot",
        initial="a",
        recovery_state="a",
        states=[
            StateModel(id="a", node_type="enter_dungeon", params={}),
            StateModel(id="b", node_type="quicksell", params={}),
        ],
        transitions=[],
        options=options if options is not None else [BotOptionModel(name="Skip mobs", type="bool", default=False)],
        drops=list(_DROPS),
    )


def _section(settings, config=None) -> OptionValuesSection:
    section = OptionValuesSection(settings=settings)
    section.set_config(config if config is not None else _config())
    return section


def _switch(section) -> ToggleSwitch:
    return section.option_widgets[qs.OPTION_ENABLED]


# ---------------------------------------------------------------------------
# The cards
# ---------------------------------------------------------------------------


def test_the_options_are_drawn_as_cards_in_the_fixed_order(qapp, settings):
    section = _section(settings)

    assert list(section.cards) == [og.GROUP_DUNGEON, og.GROUP_TEAM, og.GROUP_QUICKSELL, og.GROUP_GENERAL]
    assert [c.title for c in section.cards.values()] == ["This dungeon", "Team", "Quicksell", "General"]


def test_every_bot_gets_cards_however_few_options_it_has(qapp, settings):
    """The same layout on a small bot is what teaches where things are on a big one.

    Player Realms and Safety Delay alone, the most common pair in the library: two
    one-row cards rather than a flat list.
    """
    config = BotConfig(
        id="small",
        name="Small",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="enter_zone", params={})],
        transitions=[],
    )

    section = _section(settings, config)

    assert list(section.cards) == [og.GROUP_TEAM, og.GROUP_GENERAL]


def test_the_authors_own_option_is_on_this_dungeon(qapp, settings):
    section = _section(settings)

    assert section._group_of["Skip mobs"] == og.GROUP_DUNGEON


def test_quicksell_and_general_start_collapsed(qapp, settings):
    section = _section(settings)

    assert {g: c.is_collapsed for g, c in section.cards.items()} == {
        og.GROUP_DUNGEON: False,
        og.GROUP_TEAM: False,
        og.GROUP_QUICKSELL: True,
        og.GROUP_GENERAL: True,
    }


# ---------------------------------------------------------------------------
# The Quicksell switch
# ---------------------------------------------------------------------------


def test_enable_quicksell_is_the_quicksell_cards_header_switch(qapp, settings):
    section = _section(settings)
    card = section.cards[og.GROUP_QUICKSELL]

    assert isinstance(_switch(section), ToggleSwitch)
    assert card.header.isAncestorOf(_switch(section))
    assert qs.OPTION_ENABLED not in section._chrome  # not a row: no revert, no ?


def test_the_switch_names_the_level_it_came_from(qapp, settings):
    section = _section(settings)
    caption = section._headers[qs.OPTION_ENABLED].caption

    assert caption.text() == SOURCE_APP_SETTING
    _switch(section).setChecked(False)
    assert caption.text() == SOURCE_OVERRIDE


def test_the_switch_works_while_its_card_is_collapsed(qapp, settings):
    """It sits on the band, which is still on screen."""
    config = _config()
    section = _section(settings, config)
    assert section.cards[og.GROUP_QUICKSELL].is_collapsed

    _switch(section).click()

    assert settings.get_option_override(config.id, qs.OPTION_ENABLED) is False
    assert section.cards[og.GROUP_QUICKSELL].is_collapsed


def test_the_switch_tooltip_says_its_state_and_what_it_does(qapp, settings):
    section = _section(settings)

    tip = _switch(section).toolTip()

    assert tip.startswith(f"{qs.OPTION_ENABLED}: on")
    assert "shared bank" in tip
    _switch(section).setChecked(False)
    assert _switch(section).toolTip().startswith(f"{qs.OPTION_ENABLED}: off")


def test_the_switchs_description_heads_the_card_while_descriptions_are_shown(qapp, settings):
    section = _section(settings)
    section.expand_all()
    section.show()
    QApplication.processEvents()
    try:
        description = section._headers[qs.OPTION_ENABLED].description
        assert not description.isVisible()

        section.set_descriptions_visible(True)

        assert description.isVisible()
    finally:
        section.hide()


# ---------------------------------------------------------------------------
# Folding, and remembering it
# ---------------------------------------------------------------------------


def test_a_click_on_a_band_folds_its_card_and_is_remembered(qapp, settings):
    section = _section(settings)

    section.cards[og.GROUP_TEAM].fold_requested.emit(False)

    assert section.cards[og.GROUP_TEAM].is_collapsed
    assert settings.get("general", og.COLLAPSED_SETTING)[og.GROUP_TEAM] is True


def test_the_fold_state_is_the_readers_not_the_bots(qapp, settings):
    """One answer for every bot: a card folded on one is folded on the next."""
    first = _section(settings)
    first.set_group_collapsed(og.GROUP_QUICKSELL, False)

    second = _section(settings, _config(options=[]))

    assert not second.cards[og.GROUP_QUICKSELL].is_collapsed


def test_ctrl_click_keeps_one_card_open_and_collapses_the_rest(qapp, settings):
    section = _section(settings)

    section.cards[og.GROUP_QUICKSELL].fold_requested.emit(True)

    assert {g: c.is_collapsed for g, c in section.cards.items()} == {
        og.GROUP_DUNGEON: True,
        og.GROUP_TEAM: True,
        og.GROUP_QUICKSELL: False,
        og.GROUP_GENERAL: True,
    }


def test_a_fold_made_on_the_options_tab_is_picked_up_on_the_way_back(qapp, settings):
    """Only one of the two tabs is on screen at a time, and they share the answer."""
    section = _section(settings)
    settings.set("general", og.COLLAPSED_SETTING, value={og.GROUP_TEAM: True})

    section.show()
    QApplication.processEvents()
    try:
        assert section.cards[og.GROUP_TEAM].is_collapsed
    finally:
        section.hide()


# ---------------------------------------------------------------------------
# Collapse all and Expand all
# ---------------------------------------------------------------------------


def test_collapse_all_and_expand_all_act_on_every_card(qapp, settings):
    section = _section(settings)

    section.collapse_all_button.click()
    assert all(c.is_collapsed for c in section.cards.values())

    section.expand_all_button.click()
    assert not any(c.is_collapsed for c in section.cards.values())


def test_each_heading_action_is_enabled_only_while_it_has_something_to_do(qapp, settings):
    """Bar B's rule for its own pair."""
    section = _section(settings)
    assert section.collapse_all_button.isEnabled() and section.expand_all_button.isEnabled()

    section.collapse_all()
    assert not section.collapse_all_button.isEnabled()
    assert section.expand_all_button.isEnabled()

    section.expand_all()
    assert section.collapse_all_button.isEnabled()
    assert not section.expand_all_button.isEnabled()


def test_the_heading_actions_name_what_they_act_on(qapp, settings):
    section = _section(settings)

    assert section.collapse_all_button.toolTip() == COLLAPSE_ALL
    assert section.expand_all_button.toolTip() == EXPAND_ALL
    assert section.header_actions == [section.collapse_all_button, section.expand_all_button]


def test_the_heading_actions_are_drawn_only_while_the_heading_is_hovered(qapp, settings):
    section = _section(settings)
    heading = section.collapse_all_button.parentWidget()
    assert not section.collapse_all_button.revealed

    QApplication.sendEvent(heading, QEvent(QEvent.Type.Enter))
    assert section.collapse_all_button.revealed and section.expand_all_button.revealed

    QApplication.sendEvent(heading, QEvent(QEvent.Type.Leave))
    assert not section.collapse_all_button.revealed


def test_the_heading_actions_stay_reachable_from_the_keyboard(qapp, settings):
    """Undrawn, not hidden: a hidden widget is skipped by the Tab order."""
    section = _section(settings)
    section.show()
    QApplication.processEvents()
    try:
        assert section.collapse_all_button.isVisible()
        assert section.collapse_all_button.focusPolicy() & section.collapse_all_button.focusPolicy().TabFocus
    finally:
        section.hide()


def test_the_heading_actions_hide_while_the_section_is_folded(qapp, settings):
    """There is nothing under the heading for them to act on."""
    section = _section(settings)
    section.show()
    QApplication.processEvents()
    try:
        section.set_expanded(False)

        assert not section.collapse_all_button.isVisible()
        section.set_expanded(True)
        assert section.collapse_all_button.isVisible()
    finally:
        section.hide()


def test_clicking_a_heading_action_does_not_fold_the_section(qapp, settings):
    section = _section(settings)

    section.collapse_all_button.click()

    assert section.expanded


# ---------------------------------------------------------------------------
# The card menu
# ---------------------------------------------------------------------------


def _menu_entries(section, group):
    menu = section.card_menu(group)
    return [(a.text(), a.isEnabled()) for a in menu.actions() if not a.isSeparator()]


def test_the_card_menu_offers_every_fold_verb(qapp, settings):
    section = _section(settings)

    entries = _menu_entries(section, og.GROUP_TEAM)

    assert [text for text, _enabled in entries] == [
        "Collapse Team",
        f"{COLLAPSE_OTHERS}\tCtrl+click",
        COLLAPSE_ALL,
        EXPAND_ALL,
    ]


def test_the_card_menu_names_what_a_press_does_next(qapp, settings):
    section = _section(settings)

    assert _menu_entries(section, og.GROUP_QUICKSELL)[0][0] == "Expand Quicksell"


def test_the_card_menus_bulk_entries_follow_the_heading_actions(qapp, settings):
    section = _section(settings)
    section.collapse_all()

    entries = dict(_menu_entries(section, og.GROUP_TEAM))

    assert entries[COLLAPSE_ALL] is False
    assert entries[EXPAND_ALL] is True


def test_collapse_others_from_the_menu(qapp, settings):
    section = _section(settings)
    menu = section.card_menu(og.GROUP_GENERAL)

    next(a for a in menu.actions() if a.text().startswith(COLLAPSE_OTHERS)).trigger()

    assert [g for g, c in section.cards.items() if not c.is_collapsed] == [og.GROUP_GENERAL]


# ---------------------------------------------------------------------------
# The header: stripe, summary and warning
# ---------------------------------------------------------------------------


def test_the_stripe_marks_a_card_holding_one_of_your_values(qapp, settings):
    section = _section(settings)
    team, quicksell = section.cards[og.GROUP_TEAM], section.cards[og.GROUP_QUICKSELL]
    assert not team.is_marked and not quicksell.is_marked

    section.option_widgets[qs.SELL_FLAG_OPTIONS["sell_pets"]].setChecked(False)

    assert quicksell.is_marked
    assert not team.is_marked


def test_the_switch_alone_marks_its_card(qapp, settings):
    section = _section(settings)

    _switch(section).setChecked(False)

    assert section.cards[og.GROUP_QUICKSELL].is_marked


def test_reverting_clears_the_stripe(qapp, settings):
    section = _section(settings)
    flag = qs.SELL_FLAG_OPTIONS["sell_pets"]
    section.option_widgets[flag].setChecked(False)

    section._chrome[flag].revert_btn.click()

    assert not section.cards[og.GROUP_QUICKSELL].is_marked


def test_the_quicksell_summary_counts_what_the_keep_list_counts(qapp, settings):
    settings.set_general("default_quicksell_sell_pets", False)
    section = _section(settings)
    kept, sellable, _never = section.option_widgets[qs.OPTION_KEEP_LIST].kept_counts

    summary = og.summary_text(section.cards[og.GROUP_QUICKSELL].summary_parts)

    assert summary == f"pets kept · keeping {kept} of {sellable}"


def test_turning_quicksell_off_says_so_on_the_folded_card(qapp, settings):
    section = _section(settings)

    _switch(section).setChecked(False)

    assert og.summary_text(section.cards[og.GROUP_QUICKSELL].summary_parts) == "Off: nothing is sold"


def test_pets_sold_anyway_raise_a_warning_on_the_folded_card(qapp, settings):
    """A fold never hides a warning: the keep list's own line is out of sight."""
    settings.set_general("default_quicksell_sell_pets", True)
    section = _section(settings)
    card = section.cards[og.GROUP_QUICKSELL]

    assert card.is_collapsed
    assert card.warning is not None and card.warning.startswith("1 Pets:")
    assert card.summary_parts[0] == og.SummaryPart("1 pet will be sold", warning=True)


def test_the_warning_goes_when_sell_pets_is_turned_off(qapp, settings):
    settings.set_general("default_quicksell_sell_pets", True)
    section = _section(settings)

    section.option_widgets[qs.SELL_FLAG_OPTIONS["sell_pets"]].setChecked(False)

    assert section.cards[og.GROUP_QUICKSELL].warning is None


def test_nothing_is_lost_while_quicksell_is_off_so_nothing_warns(qapp, settings):
    settings.set_general("default_quicksell_sell_pets", True)
    section = _section(settings)

    _switch(section).setChecked(False)

    assert section.cards[og.GROUP_QUICKSELL].warning is None


def test_the_general_summary_reads_the_resolved_values(qapp, settings):
    section = _section(settings)

    assert og.summary_text(section.cards[og.GROUP_GENERAL].summary_parts) == "Delay 3.00 s"


def test_a_bot_with_no_options_has_nothing_to_collapse(qapp, settings):
    config = BotConfig(
        id="empty",
        name="Empty",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="sleep", params={})],
        transitions=[],
    )

    section = _section(settings, config)

    assert section.cards == {}
    assert not section.collapse_all_button.isEnabled()
    assert not section.expand_all_button.isEnabled()
