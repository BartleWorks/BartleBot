"""Tests for the Combat tab: the three strategy cards, the deck cards, and the tab
that groups fights across them.

Rewritten for the card rework. The tab used to hold an inline ``DuelConfigEditor``
behind a disclosure arrow on every binding, and most of what was tested here was that
editor's expand/collapse/unlink/keystroke-debounce behaviour. All of it is gone: the
tab picks and reads, editing goes through the Strategy Library, and the widgets are
``CardFrame`` subclasses whose verbs are data. So these tests read verb lists and card
lines rather than chips, rails and disclosure rows.

Real widget tests (QApplication required): what is under test — which verbs a card
offers, which card a fight resolves to, what a pick does to the grouping — lives
entirely in the Qt wiring.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QInputDialog, QMessageBox, QScrollArea

from bartlebot import strategy_store as store_mod
from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DeckData,
    DuelCombatStrategy,
    DuelConfig,
    PlayerAssignment,
    StateAssignment,
)
from bartlebot.combat_warnings import exposed_combat_states
from bartlebot.gui.sidebar.combat_tab import (
    _APPLY_DECK,
    _CHANGE_BCS,
    _CLEAR_DECK,
    _DELETE_BCS,
    _DUPLICATE_BCS,
    _IMPORT_DECK,
    _NEW_BCS,
    _NO_DECK,
    _NO_STRATEGY_LABEL,
    _RENAME_BCS,
    CombatTab,
    _BotStrategyCard,
    _DefaultBinding,
    _PlayerDecksSection,
)
from bartlebot.gui.sidebar.strategy_panel import (
    APPLIES_TO,
    BUILT_IN_LABEL,
    CHANGE,
    CUSTOM_LABEL,
    DEFAULT_PILL,
    DEFAULT_SCOPE_META,
    EDIT_STRATEGY,
    LOCKED,
    MISSING_LABEL,
    NOT_CONFIGURED_LABEL,
    SAVE_AS_PRESET,
    STATE_INHERIT_LABEL,
    STATE_INHERIT_META,
    StrategyPanel,
)
from bartlebot.gui.widgets.picker_popup import PickerRow
from bartlebot.gui.widgets.tag_pill import AccentPill, ActionPill, NeutralPill


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the strategy store's default root at an empty tmp_path for the test."""
    for sub in ("strategies/player", "strategies/duel", "strategies/decks", "bot_strategies"):
        (tmp_path / sub).mkdir(parents=True)
    monkeypatch.setattr(store_mod, "project_root", lambda: tmp_path)
    return tmp_path


def _config(*, label: str = "Boss", use_default: bool = False) -> BotConfig:
    return BotConfig(
        id="test-bot",
        name="Test Bot",
        initial="s0",
        recovery_state="s0",
        states=[
            StateModel(id="s0", node_type="sleep"),
            StateModel(
                id="state_9",
                node_type="handle_combat",
                label=label,
                params={"use_default_strategy": use_default},
            ),
        ],
        transitions=[],
    )


def _verb_labels(card) -> list[str]:
    """Every verb the card offers right now — the right-click menu, in order."""
    return [verb.label for verb in card._verbs()]


def _visible_lines(panel: StrategyPanel) -> list[str]:
    return [lbl.full_text() for lbl in panel._assignment_lbls if lbl.isVisibleTo(panel)]


def _default_fights(tab: CombatTab) -> list[str]:
    """The fights on the tab's default card, by label, in order."""
    return [label for _state_id, label in tab._default_panel.fights()]


def _configured_panel(name: str = "Shared") -> StrategyPanel:
    """A fight card holding a strategy — the state in which it has pills at all.

    A card with no strategy shows no pill row: its fights are pills on the tab's default
    card instead, which is the wrong subject for a membership test.
    """
    dcs = DuelCombatStrategy(name=name)
    panel = StrategyPanel(STATE_INHERIT_LABEL, STATE_INHERIT_META)
    panel.load(StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id), [dcs], [])
    return panel


# ---------------------------------------------------------------------------
# StrategyPanel — what a card says at rest
# ---------------------------------------------------------------------------


def test_panel_shows_linked_strategy_name_and_facets(qapp):
    dcs = DuelCombatStrategy(
        name="Frenzy AoE", min_players=4, player_assignments=[PlayerAssignment(players=[1], dsl="x")]
    )
    binding = StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id)
    panel = StrategyPanel()
    panel.load(binding, [dcs], [])

    assert panel._name.full_text() == "Frenzy AoE"
    assert panel._meta.isVisibleTo(panel)


def test_panel_shows_missing_strategy_when_linked_id_not_found(qapp):
    binding = StateAssignment(state_ids=["s1"], duel_combat_strategy_id="ghost-id")
    panel = StrategyPanel()
    panel.load(binding, [], [])

    assert panel._name.full_text() == MISSING_LABEL


def test_panel_shows_not_configured_when_unlinked_and_empty(qapp):
    binding = StateAssignment(state_ids=["s1"])
    panel = StrategyPanel()
    panel.load(binding, [], [])

    assert panel._name.full_text() == BUILT_IN_LABEL
    assert panel._meta.full_text() == NOT_CONFIGURED_LABEL


def test_panel_shows_custom_when_unlinked_and_nonempty(qapp):
    binding = StateAssignment(
        state_ids=["s1"], duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="blade")])
    )
    panel = StrategyPanel()
    panel.load(binding, [], [])

    assert panel._name.full_text() == CUSTOM_LABEL
    assert _visible_lines(panel) == ["P1: blade"]


def test_every_assignment_gets_its_own_line(qapp):
    """The point of the rework: four short lines, not one elided line of four.

    The old panel joined every assignment with ``·`` into a single elided label, which
    at a 300px sidebar showed about one and a half of them.
    """
    binding = StateAssignment(
        state_ids=["s1"],
        duel_config=DuelConfig(
            player_assignments=[
                PlayerAssignment(players=[1], dsl="one"),
                PlayerAssignment(players=[2], dsl="two"),
                PlayerAssignment(players=[3, 4], dsl="three"),
            ]
        ),
    )
    panel = StrategyPanel()
    panel.load(binding, [], [])

    assert len(_visible_lines(panel)) == 3


def test_surplus_assignment_lines_are_hidden_not_destroyed(qapp):
    """The binding is replaced under the card on every pick; rebuilding children
    mid-refresh is how the old per-state rebuild interrupted edits in progress."""
    panel = StrategyPanel()
    panel.load(
        StateAssignment(
            state_ids=["s1"],
            duel_config=DuelConfig(
                player_assignments=[PlayerAssignment(players=[i], dsl="x") for i in (1, 2, 3)],
            ),
        ),
        [],
        [],
    )
    first = panel._assignment_lbls[0]

    panel.load(StateAssignment(state_ids=["s1"]), [], [])

    assert panel._assignment_lbls[0] is first
    assert _visible_lines(panel) == []


def test_an_unparseable_strategy_is_marked_on_its_name(qapp):
    """The same ⚠ and the same reason StrategyCard shows in the picker."""
    dcs = DuelCombatStrategy(name="Broken", player_assignments=[PlayerAssignment(players=[1], dsl="{{{ nope")])
    panel = StrategyPanel()
    panel.load(StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id), [dcs], [])

    assert "⚠" in panel._name.full_text()
    assert "Does not parse" in panel._name.toolTip()


def test_panel_hides_itself_when_binding_is_none(qapp):
    panel = StrategyPanel()
    panel.load(StateAssignment(state_ids=["s1"]), [], [])
    assert not panel.isHidden()

    panel.load(None, [], [])

    assert panel.isHidden()


# ---------------------------------------------------------------------------
# StrategyPanel — verbs
# ---------------------------------------------------------------------------


def test_change_is_the_body_verb(qapp):
    """The card is the active configuration and the card is how you replace it."""
    panel = StrategyPanel()
    panel.load(StateAssignment(state_ids=["s1"]), [], [])
    seen: list[bool] = []
    panel.pick_requested.connect(lambda: seen.append(True))

    panel.activated.emit()

    assert seen == [True]
    assert CHANGE in _verb_labels(panel)


def test_edit_in_library_is_offered_only_for_a_linked_binding(qapp):
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    panel = StrategyPanel()

    panel.load(StateAssignment(state_ids=["s1"]), [dcs], [])
    assert EDIT_STRATEGY not in _verb_labels(panel)

    panel.load(StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id), [dcs], [])
    assert EDIT_STRATEGY in _verb_labels(panel)


def test_the_edit_button_exists_from_the_start_and_is_shown_when_it_applies(qapp):
    """CardFrame builds face buttons once, before any binding is loaded.

    A conditional face verb would therefore never get a button at all, and could never
    appear when the binding later became linked — which is why the two face buttons are
    built from ``_face_verbs`` and this one is shown and hidden instead.
    """
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    panel = StrategyPanel()
    button = panel.action_button(EDIT_STRATEGY)
    assert button is not None

    panel.load(StateAssignment(state_ids=["s1"]), [dcs], [])
    assert not button.isVisibleTo(panel)

    panel.load(StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id), [dcs], [])
    assert button.isVisibleTo(panel)


def test_edit_in_library_emits_the_linked_dcs_id(qapp):
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    panel = StrategyPanel()
    panel.load(StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id), [dcs], [])

    seen: list[str] = []
    panel.edit_in_library_requested.connect(seen.append)
    panel._emit_edit_in_library()

    assert seen == [dcs.id]


def test_save_as_preset_is_offered_only_for_an_inline_config(qapp):
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    panel = StrategyPanel()

    panel.load(StateAssignment(state_ids=["s1"]), [], [])
    assert SAVE_AS_PRESET not in _verb_labels(panel)  # nothing in it to save

    panel.load(
        StateAssignment(
            state_ids=["s1"],
            duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="x")]),
        ),
        [],
        [],
    )
    assert SAVE_AS_PRESET in _verb_labels(panel)

    panel.load(StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id), [dcs], [])
    assert SAVE_AS_PRESET not in _verb_labels(panel)  # already there


def test_a_locked_card_refuses_its_body_click(qapp):
    """There is no QPushButton here whose disabled state would swallow it."""
    panel = StrategyPanel()
    panel.load(StateAssignment(state_ids=["s1"]), [], [])

    panel.set_editable(False)

    assert panel._refuses_activation()
    assert panel.toolTip() == LOCKED
    assert all(not button.isEnabled() for button in panel.action_buttons)


# ---------------------------------------------------------------------------
# StrategyPanel — "Applies to"
# ---------------------------------------------------------------------------


def test_a_state_panel_offers_applies_to(qapp):
    dcs = DuelCombatStrategy(name="Shared")
    panel = StrategyPanel(STATE_INHERIT_LABEL, "")
    panel.load(StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id), [dcs], [])

    assert APPLIES_TO in _verb_labels(panel)


def test_the_default_panel_offers_no_applies_to(qapp):
    """The Default Duel Combat Strategy has no state_ids — it is the fallback for
    every fight no group claims."""
    bcs = BotCombatStrategy(name="B", bot_id="bot-1")
    panel = StrategyPanel()
    panel.load(_DefaultBinding(bcs), [], [])

    assert APPLIES_TO not in _verb_labels(panel)


def test_a_panel_holding_no_strategy_offers_no_applies_to(qapp):
    """A group of fights holding no config is what the grouping pulls back apart on the
    next rebuild, so offering it would look like an edit that quietly undid itself."""
    panel = StrategyPanel(STATE_INHERIT_LABEL, "")
    panel.load(StateAssignment(state_ids=["s1"]), [], [])

    assert APPLIES_TO not in _verb_labels(panel)
    assert panel._pill_row.isHidden()


def test_the_pill_row_appears_once_a_fight_has_an_inline_config(qapp):
    panel = StrategyPanel(STATE_INHERIT_LABEL, "")
    panel.load(
        StateAssignment(
            state_ids=["s1"],
            duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="blade")]),
        ),
        [],
        [],
    )

    assert not panel._pill_row.isHidden()
    assert APPLIES_TO in _verb_labels(panel)


def test_the_menu_verb_opens_the_same_popup_as_the_button(qapp):
    """The menu is the keyboard route to a verb, not a second way of performing it."""
    dcs = DuelCombatStrategy(name="Shared")
    panel = StrategyPanel(STATE_INHERIT_LABEL, "")
    panel.load(StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id), [dcs], [])

    verb = next(v for v in panel._verbs() if v.label == APPLIES_TO)

    assert verb.run == panel._fights_btn.click


def test_membership_is_reported_when_the_popup_closes_not_per_toggle(qapp):
    """Acting per toggle would rebuild the section holding the card whose popup is
    still open on top of it."""
    dcs = DuelCombatStrategy(name="Shared")
    panel = StrategyPanel(STATE_INHERIT_LABEL, "")
    panel.load(StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id), [dcs], [])
    panel.set_fight_options([("", [PickerRow(key="s1", label="A"), PickerRow(key="s2", label="B")])], ["s1"])
    seen: list[list[str]] = []
    panel.membership_changed.connect(seen.append)

    panel._fights_btn.selection_changed.emit(["s1", "s2"])
    assert seen == []

    panel._fights_btn.committed.emit(["s1", "s2"])
    assert seen == [["s1", "s2"]]


# ---------------------------------------------------------------------------
# StrategyPanel — fight titles
# ---------------------------------------------------------------------------


def _pills(panel: StrategyPanel) -> list[str]:
    return [p.text() for p in panel._pills if not p.isHidden()]


def test_every_fight_in_a_group_gets_a_pill(qapp):
    panel = _configured_panel()

    panel.set_fights(
        [("Tse-Tse", "Tse-Tse"), ("Spectral", "Spectral"), ("Goliath", "Goliath"), ("Starburst", "Starburst")]
    )

    assert _pills(panel) == ["Tse-Tse", "Spectral", "Goliath", "Starburst"]


def test_shrinking_a_group_hides_the_surplus_pills(qapp):
    panel = _configured_panel()
    panel.set_fights([("A", "A"), ("B", "B"), ("C", "C")])

    panel.set_fights([("A", "A")])

    assert panel.titles() == ["A"]
    assert _pills(panel) == ["A"]


def test_growing_a_group_reuses_existing_pills(qapp):
    panel = _configured_panel()
    panel.set_fights([("A", "A")])
    first = panel._pills[0]

    panel.set_fights([("A", "A"), ("B", "B")])

    assert panel._pills[0] is first


def test_the_fights_button_stays_last_as_pills_come_and_go(qapp):
    """A control that walks along the row is one you have to find again after every
    change, which is why FlowLayout grew an insertWidget."""
    panel = _configured_panel()

    panel.set_fights([("A", "A"), ("B", "B"), ("C", "C")])

    layout = panel._pill_layout
    assert layout.itemAt(layout.count() - 1).widget() is panel._fights_btn


def test_the_pill_row_is_hidden_where_membership_is_not_supported(qapp):
    panel = StrategyPanel()
    panel.set_fights([("A", "A"), ("B", "B")])

    assert panel._pill_row.isHidden()


# ---------------------------------------------------------------------------
# _BotStrategyCard
# ---------------------------------------------------------------------------


def test_bot_card_names_the_strategy_and_summarises_it(qapp):
    bcs = BotCombatStrategy(name="Four wizards", bot_id="bot-1")
    card = _BotStrategyCard()

    card.set_strategy(bcs)

    assert card._name.full_text() == "Four wizards"
    assert card.strategy is bcs


def test_bot_card_with_no_strategy_says_so_and_refuses_its_body(qapp):
    card = _BotStrategyCard()

    card.set_strategy(None)

    assert card._name.full_text() == _NO_STRATEGY_LABEL
    assert card._refuses_activation()


def test_new_stays_live_with_no_strategy_loaded(qapp):
    """That is exactly the state a user gets out of by making one."""
    card = _BotStrategyCard()
    card.set_strategy(None)

    assert card.action_button(_NEW_BCS).isEnabled()
    assert not card.action_button(_RENAME_BCS).isEnabled()
    assert not card.action_button(_CHANGE_BCS).isEnabled()


def test_destructive_bot_verbs_stay_off_the_face(qapp):
    """A trash 22px from a body click that changes the live configuration."""
    card = _BotStrategyCard()
    card.set_strategy(BotCombatStrategy(name="B", bot_id="bot-1"))

    assert {_DUPLICATE_BCS, _DELETE_BCS} <= set(_verb_labels(card))
    assert card.action_button(_DELETE_BCS) is None
    assert card.action_button(_DUPLICATE_BCS) is None


def test_bot_card_body_click_asks_for_a_different_strategy(qapp):
    card = _BotStrategyCard()
    card.set_strategy(BotCombatStrategy(name="B", bot_id="bot-1"))
    seen: list[bool] = []
    card.change_requested.connect(lambda: seen.append(True))

    card.activated.emit()

    assert seen == [True]


# ---------------------------------------------------------------------------
# _DefaultBinding adapter
# ---------------------------------------------------------------------------


def test_default_binding_reads_bcs_default_fields():
    bcs = BotCombatStrategy(
        name="B",
        bot_id="bot-1",
        default_strategy_id="dcs-1",
        default_duel_config=DuelConfig(min_players=2),
    )
    binding = _DefaultBinding(bcs)

    assert binding.duel_combat_strategy_id == "dcs-1"
    assert binding.duel_config.min_players == 2
    assert binding.is_linked


def test_default_binding_setter_writes_back_to_the_bcs():
    bcs = BotCombatStrategy(name="B", bot_id="bot-1")
    binding = _DefaultBinding(bcs)

    binding.duel_combat_strategy_id = "dcs-2"

    assert bcs.default_strategy_id == "dcs-2"


def test_default_binding_duel_config_setter_writes_back_to_the_bcs():
    """The adapter has to be writable in both fields, or anything that assigns a whole
    new DuelConfig crashes against the Default panel specifically — StateAssignment
    never catches it, its field being settable for free."""
    bcs = BotCombatStrategy(name="B", bot_id="bot-1")
    binding = _DefaultBinding(bcs)
    new_config = DuelConfig(min_players=3)

    binding.duel_config = new_config

    assert bcs.default_duel_config is new_config


def test_default_binding_is_linked_false_when_no_default_strategy_id():
    bcs = BotCombatStrategy(name="B", bot_id="bot-1")
    assert _DefaultBinding(bcs).is_linked is False


# ---------------------------------------------------------------------------
# CombatTab — sections and card lifecycle
# ---------------------------------------------------------------------------


def _active(bot_id: str, bcs: BotCombatStrategy) -> None:
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415

    SettingsManager.instance().set_active_strategy(bot_id, bcs.id)


def _tab_for(bcs: BotCombatStrategy, config: BotConfig) -> CombatTab:
    config.id = bcs.bot_id
    store_mod.save_bot_strategy(bcs)
    _active(config.id, bcs)
    tab = CombatTab()
    tab.set_config(config)
    return tab


def test_an_exposed_state_with_no_strategy_is_a_pill_not_a_panel(qapp, store):
    """Nothing is configured on a fresh Bot Combat Strategy, so nothing gets a card of
    its own — every exposed fight is a pill on the default card."""
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config(label="Howling Chaney"))

    assert tab._state_panels == {}
    assert tab._default_panel.fights() == [("state_9", "Howling Chaney")]
    assert not tab._default_panel.isHidden()


def test_the_section_is_absent_until_a_bot_combat_strategy_exists(qapp, store):
    """There is no default strategy to name, so the card that names it has nothing to
    say — and it is the card the section is built around."""
    tab = CombatTab()
    tab.set_config(_config(label="Howling Chaney"))

    assert tab._strategies_section.isHidden()


def test_the_specialized_section_is_absent_when_no_fight_opts_out(qapp, store):
    """63 of the 81 shipped bots expose none. A permanently empty section headed by a
    phrase you have to learn the schema to parse is worse than no section."""
    tab = CombatTab()
    config = BotConfig(
        id="test-bot",
        name="Bot",
        initial="s0",
        recovery_state="s0",
        states=[StateModel(id="s0", node_type="sleep")],
        transitions=[],
    )
    tab.set_config(config)

    assert tab._state_panels == {}
    # isVisible() is unreliable for a widget tree that was never shown in a window;
    # isHidden() reflects whether show()/hide() was actually called.
    assert tab._strategies_section.isHidden()


def test_the_section_stands_on_the_default_card_alone(qapp, store):
    """It is not hidden merely because no fight opted out. 49 of the 68 shipped bots with
    combat expose none, and on those the default card is still what names the strategy
    every one of their fights will use -- only the add-card has nothing to act on."""
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config(use_default=True))

    assert not tab._strategies_section.isHidden()
    assert not tab._default_panel.isHidden()
    assert _default_fights(tab) == []
    assert tab._add_card.isHidden()


def test_the_add_card_appears_once_a_fight_opts_out(qapp, store):
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config())

    assert not tab._strategies_section.isHidden()
    assert not tab._add_card.isHidden()


def test_untouched_state_binding_is_not_persisted(qapp, store):
    """A state row nobody edited must not bloat the saved file."""
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config())

    tab._save_bcs()

    assert store_mod.load_bot_strategy(tab._bcs.id).state_assignments == []


def test_linked_state_binding_is_persisted_even_with_empty_duel_config(qapp, store):
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    store_mod.save_duel_strategy(dcs)
    bcs = BotCombatStrategy(name="BCS", bot_id="bot-1")
    tab = _tab_for(bcs, _config())

    with patch("bartlebot.gui.sidebar.combat_tab.CombatStrategyDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        tab._on_pick_for_fight("state_9")

    reloaded = store_mod.load_bot_strategy(bcs.id)
    assert len(reloaded.state_assignments) == 1
    assert reloaded.state_assignments[0].duel_combat_strategy_id == dcs.id


def test_inline_state_binding_with_content_is_persisted(qapp, store):
    """Nothing on this tab can author one any more, but a bot file can carry one and
    it has to survive a save rather than being pruned as empty."""
    bcs = BotCombatStrategy(
        name="BCS",
        bot_id="bot-1",
        state_assignments=[
            StateAssignment(
                state_ids=["state_9"],
                duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="blade")]),
            )
        ],
    )
    tab = _tab_for(bcs, _config())

    tab._save_bcs()

    reloaded = store_mod.load_bot_strategy(bcs.id)
    assert reloaded.state_assignments[0].duel_config.player_assignments[0].dsl == "blade"


def test_refresh_strategy_data_does_not_replace_card_widgets(qapp, store):
    """A rename in the library must reach the cards without destroying them."""
    dcs = _saved_dcs("Frenzy AoE")
    bcs = BotCombatStrategy(
        name="BCS",
        bot_id="bot-1",
        state_assignments=[StateAssignment(state_ids=["state_9"], duel_combat_strategy_id=dcs.id)],
    )
    tab = _tab_for(bcs, _config())
    panel_before = tab._state_panels["state_9"]
    default_before = tab._default_panel

    tab.refresh_strategy_data()

    assert tab._state_panels["state_9"] is panel_before
    assert tab._default_panel is default_before


def test_set_editable_locks_every_card_without_replacing_it(qapp, store):
    dcs = _saved_dcs()
    bcs = BotCombatStrategy(
        name="BCS",
        bot_id="bot-1",
        state_assignments=[StateAssignment(state_ids=["state_9"], duel_combat_strategy_id=dcs.id)],
    )
    tab = _tab_for(bcs, _config())
    panel_before = tab._state_panels["state_9"]

    tab.set_editable(False)

    assert tab._state_panels["state_9"] is panel_before
    assert panel_before._refuses_activation()
    assert not tab._bcs_card.action_button(_CHANGE_BCS).isEnabled()


def test_the_default_cards_pills_are_locked_with_the_rest(qapp, store):
    """They are that card's only verb, so a running bot has to reach them."""
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config())
    card = tab._default_panel

    tab.set_editable(False)

    assert card._pills[0].isEnabled() is False
    assert card.toolTip() == LOCKED


def test_an_unconfigured_fight_is_a_pill_on_the_default_card(qapp, store):
    """It used to draw a full card -- frame, name, meta sentence, Applies-to row -- to
    say it had nothing to say, once per fight; then a muted row per fight under a bare
    caption. Now they are pills on the card holding the strategy they all fall through
    to, which is the shape the rest of the section already had."""
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config(label="Howling Chaney"))
    card = tab._default_panel

    assert _default_fights(tab) == ["Howling Chaney"]
    assert not card.isHidden()
    assert card._name.full_text() == BUILT_IN_LABEL


def test_the_default_card_is_badged_rather_than_titled(qapp, store):
    """Once a strategy is picked for it, it is titled with that strategy's name like
    every other card in the section, so the badge is what says which one is the
    fallback."""
    dcs = _saved_dcs()
    bcs = BotCombatStrategy(name="BCS", bot_id="bot-1", default_strategy_id=dcs.id)
    tab = _tab_for(bcs, _config())

    assert tab._default_panel._name.full_text() == dcs.name
    assert tab._default_panel._default_pill.text() == DEFAULT_PILL


def test_the_default_cards_badge_is_not_one_of_its_fights(qapp, store):
    """It qualifies the card; the pills beside it are its contents. Two kinds of word on
    one row, so they are two kinds of pill."""
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config(label="Howling Chaney"))
    card = tab._default_panel

    assert isinstance(card._default_pill, AccentPill)
    assert _default_fights(tab) == ["Howling Chaney"]
    assert not isinstance(card._default_pill, ActionPill)


def test_the_default_card_says_what_else_it_governs(qapp, store):
    """Its pills are not the whole truth: only 30 of the shipped library's 133
    handle_combat states are exposed, and the other 103 run this strategy without
    appearing here at all."""
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config(label="Howling Chaney"))
    card = tab._default_panel

    assert card._scope.full_text() == DEFAULT_SCOPE_META
    assert card._scope.isVisibleTo(card)
    assert DEFAULT_SCOPE_META.lower() in card._pills[0].toolTip()


def test_the_default_card_offers_no_fights_button(qapp, store):
    """Its membership is a residue, not a choice: unchecking a fight here would be an
    offer to send it nowhere."""
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config())

    assert tab._default_panel._fights_btn is None
    assert APPLIES_TO not in [v.label for v in tab._default_panel._verbs()]


def test_the_default_card_stays_when_every_fight_has_a_strategy(qapp, store):
    """It still holds the strategy for every combat state the sidebar never lists, and
    its badge keeps the row from reading as a strategy that lost its fights."""
    dcs = _saved_dcs()
    bcs = BotCombatStrategy(
        name="BCS",
        bot_id="bot-1",
        state_assignments=[StateAssignment(state_ids=["state_9"], duel_combat_strategy_id=dcs.id)],
    )
    tab = _tab_for(bcs, _config())

    assert tab._default_panel.fights() == []
    assert not tab._default_panel.isHidden()
    assert tab._default_panel._pill_row.isVisibleTo(tab._default_panel)


def test_the_two_levels_fall_through_to_different_things(qapp, store):
    """resolve_combat() reaches for bcs.default_strategy_id / default_duel_config before
    the built-in strategy, and the pre-run warning says so too. Telling a fight it falls
    through to "BartleBot Default Combat Strategy" claimed the bot default was skipped."""
    dcs = _saved_dcs()
    bcs = BotCombatStrategy(
        name="BCS",
        bot_id="bot-1",
        state_assignments=[StateAssignment(state_ids=["state_9"], duel_combat_strategy_id=dcs.id)],
    )
    tab = _tab_for(bcs, _config())

    assert tab._state_panels["state_9"].empty_label == STATE_INHERIT_LABEL
    assert tab._default_panel.empty_label == BUILT_IN_LABEL
    assert tab._default_panel._name.full_text() == BUILT_IN_LABEL


def test_a_fight_leaves_the_pills_for_a_card_of_its_own(qapp, store):
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    store_mod.save_duel_strategy(dcs)
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config(label="Howling Chaney"))

    with patch("bartlebot.gui.sidebar.combat_tab.CombatStrategyDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        tab._on_pick_for_fight("state_9")

    panel = tab._state_panels["state_9"]
    assert panel._name.full_text() == "Frenzy AoE"
    assert not panel._pill_row.isHidden()
    assert _pills(panel) == ["Howling Chaney"]
    assert _default_fights(tab) == []


def test_a_pill_click_picks_for_that_fight_alone(qapp, store):
    """The click the condensed row used to take, on the fight's own name."""
    dcs = _saved_dcs("Frenzy AoE")
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _multi_config("A", "B"))

    with patch("bartlebot.gui.sidebar.combat_tab.CombatStrategyDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        tab._default_panel._pills[1].click()

    assert tab._state_bindings["state_2"].duel_combat_strategy_id == dcs.id
    assert tab._state_bindings["state_1"].duel_combat_strategy_id is None
    assert _default_fights(tab) == ["A"]


def test_a_cancelled_pill_pick_changes_nothing(qapp, store):
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _multi_config("A", "B"))

    with patch("bartlebot.gui.sidebar.combat_tab.CombatStrategyDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = False
        tab._on_pick_for_fight("state_2")

    assert tab._state_bindings["state_2"].duel_combat_strategy_id is None
    assert _default_fights(tab) == ["A", "B"]


# ---------------------------------------------------------------------------
# ActionPill — the clickable pill the bot-default card is built from
# ---------------------------------------------------------------------------


def test_a_pill_reports_its_key_rather_than_its_label(qapp):
    """Two fights can share a label; nothing but the state id identifies one."""
    pill = ActionPill("Howling Chaney", "state_9")
    seen: list[str] = []
    pill.activated.connect(seen.append)

    pill.click()

    assert seen == ["state_9"]


def test_a_reused_pill_reports_the_fight_it_now_names(qapp):
    """Pills are re-pointed rather than rebuilt, so a stale key would fire a pick against
    a fight that left the card."""
    pill = ActionPill("A", "state_1")
    seen: list[str] = []
    pill.activated.connect(seen.append)

    pill.set_pill("B", "state_2")
    pill.click()

    assert pill.text() == "B"
    assert seen == ["state_2"]


def test_a_pill_is_as_wide_as_its_text(qapp):
    """A QPushButton's own size hint carries the platform's 80px minimum button width,
    which would make four pills wider than the sidebar they wrap inside."""
    short = ActionPill("A", "state_1")
    long = ActionPill("Defeat the second Lost Soul", "state_2")

    assert short.sizeHint().width() < 30
    assert short.sizeHint().width() < long.sizeHint().width()
    # The same shape as the inert pills on the cards above it -- that is the whole reason
    # this is a pill rather than a NeutralChip.
    assert short.sizeHint().height() == NeutralPill("A").sizeHint().height()


def test_the_badge_pill_shares_the_row_without_joining_it(qapp):
    """Same height, so it sits on the row rather than above or below it; outlined rather
    than filled, so it reads as a label on the pills and not as the loudest of them."""
    badge = AccentPill(DEFAULT_PILL)

    assert badge.sizeHint().height() == NeutralPill("A").sizeHint().height()
    assert "transparent" in badge.styleSheet()
    assert "border: 1px solid" in badge.styleSheet()


# ---------------------------------------------------------------------------
# CombatTab — picking a Duel Combat Strategy
# ---------------------------------------------------------------------------


def test_pick_sets_the_strategy_id_and_saves(qapp, store):
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    store_mod.save_duel_strategy(dcs)
    bcs = BotCombatStrategy(name="BCS", bot_id="bot-1")
    tab = _tab_for(bcs, _config())

    with patch("bartlebot.gui.sidebar.combat_tab.CombatStrategyDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        tab._on_pick_duel(tab._default_panel)

    assert tab._bcs.default_strategy_id == dcs.id
    assert store_mod.load_bot_strategy(bcs.id).default_strategy_id == dcs.id


def test_pick_cancel_leaves_the_binding_unchanged(qapp, store):
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config())

    with patch("bartlebot.gui.sidebar.combat_tab.CombatStrategyDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = False
        tab._on_pick_duel(tab._default_panel)

    assert tab._bcs.default_strategy_id is None


def test_pick_none_unlinks(qapp, store):
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    store_mod.save_duel_strategy(dcs)
    bcs = BotCombatStrategy(name="BCS", bot_id="bot-1", default_strategy_id=dcs.id)
    tab = _tab_for(bcs, _config())

    with patch("bartlebot.gui.sidebar.combat_tab.CombatStrategyDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = None
        tab._on_pick_duel(tab._default_panel)

    assert tab._bcs.default_strategy_id is None
    assert tab._default_panel._name.full_text() == BUILT_IN_LABEL


def test_the_picker_none_row_is_labelled_with_what_the_card_falls_through_to(qapp, store):
    """Choosing nothing in the dialog and reading the card afterwards say the same
    words — and the two levels say different words."""
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config())

    with patch("bartlebot.gui.sidebar.combat_tab.CombatStrategyDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = False
        tab._on_pick_duel(tab._default_panel)
        default_label = MockDlg.call_args.kwargs["none_label"]
        tab._on_pick_for_fight("state_9")
        state_label = MockDlg.call_args.kwargs["none_label"]

    assert default_label == BUILT_IN_LABEL
    assert state_label == STATE_INHERIT_LABEL


# ---------------------------------------------------------------------------
# CombatTab — saving an inline config into the library
# ---------------------------------------------------------------------------


def _inline_bcs(dsl: str = "custom") -> BotCombatStrategy:
    return BotCombatStrategy(
        name="BCS",
        bot_id="bot-1",
        default_duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl=dsl)]),
    )


def test_promote_creates_a_library_entry_and_links_to_it(qapp, store):
    tab = _tab_for(_inline_bcs(), _config())

    with patch.object(QInputDialog, "getText", return_value=("My Preset", True)):
        tab._on_promote(tab._default_panel)

    saved = store_mod.load_duel_strategy(tab._bcs.default_strategy_id)
    assert saved.name == "My Preset"
    assert saved.player_assignments[0].dsl == "custom"


def test_promote_carries_every_field_of_the_config(qapp, store):
    """Regression: hand-listing fields silently drops any field added later."""
    bcs = BotCombatStrategy(
        name="BCS",
        bot_id="bot-1",
        default_duel_config=DuelConfig(
            description="Farm loop, no adds",
            player_assignments=[
                PlayerAssignment(players=[1], dsl="custom", school="Life", note="Heals through round 2"),
            ],
        ),
    )
    tab = _tab_for(bcs, _config())

    with patch.object(QInputDialog, "getText", return_value=("My Preset", True)):
        tab._on_promote(tab._default_panel)

    saved = store_mod.load_duel_strategy(tab._bcs.default_strategy_id)
    assert saved.description == "Farm loop, no adds"
    assert saved.player_assignments[0].school == "Life"
    assert saved.player_assignments[0].note == "Heals through round 2"


def test_promote_with_an_empty_config_is_refused(qapp, store):
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config())

    with patch.object(QMessageBox, "information") as mock_info:
        tab._on_promote(tab._default_panel)

    mock_info.assert_called_once()
    assert tab._bcs.default_strategy_id is None


def test_promote_cancel_does_not_create_anything(qapp, store):
    tab = _tab_for(_inline_bcs(), _config())

    with patch.object(QInputDialog, "getText", return_value=("", False)):
        tab._on_promote(tab._default_panel)

    assert tab._bcs.default_strategy_id is None
    assert store_mod.list_duel_strategies() == []


def test_promote_reaches_every_cards_library(qapp, store):
    """The new entry has to be pickable from the sibling cards immediately."""
    bcs = _inline_bcs()
    bcs.state_assignments = [
        StateAssignment(
            state_ids=["state_9"],
            duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="blade")]),
        )
    ]
    tab = _tab_for(bcs, _config())

    with patch.object(QInputDialog, "getText", return_value=("Shared Preset", True)):
        tab._on_promote(tab._default_panel)

    assert any(d.name == "Shared Preset" for d in tab._state_panels["state_9"]._all_dcs)


# ---------------------------------------------------------------------------
# rebind_identity — Save As gives the loaded bot a new id
# ---------------------------------------------------------------------------


def _tab_with_active_bcs(bot_id: str = "bot-1"):
    """A tab showing ``bot_id``'s active strategy, with the config it is bound to."""
    bcs = BotCombatStrategy(name="BCS", bot_id=bot_id)
    store_mod.save_bot_strategy(bcs)
    tab = CombatTab()
    config = _config()
    config.id = bot_id
    _active(bot_id, bcs)
    tab.set_config(config)
    return tab, config, bcs


def test_rebind_identity_drops_the_originals_strategy(qapp, store):
    """A fork inherits no strategy — the card must stop naming the original's."""
    tab, config, bcs = _tab_with_active_bcs("drops-orig")
    assert tab._bcs is not None and tab._bcs.id == bcs.id

    config.id = "drops-fork"
    tab.rebind_identity()

    assert tab._bcs is None
    assert tab._all_bcs == []
    assert tab._bcs_card._name.full_text() == _NO_STRATEGY_LABEL


def test_rebind_identity_does_not_bind_the_fork_to_the_originals_strategy(qapp, store):
    """The bug this exists for: a combat edit after Save As wrote the *original's*
    strategy under the *new* id, which is what minting an id was meant to prevent."""
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415

    tab, config, _bcs = _tab_with_active_bcs("no-bind-orig")

    config.id = "no-bind-fork"
    tab.rebind_identity()
    tab._save_bcs()

    assert SettingsManager.instance().get_active_strategy("no-bind-fork") is None


def test_rebind_identity_finds_the_forks_own_strategy(qapp, store):
    """Not a blanket clear: whatever the new id does resolve to is what shows."""
    tab, config, _bcs = _tab_with_active_bcs("own-orig")
    fork_bcs = BotCombatStrategy(name="Fork BCS", bot_id="own-fork")
    store_mod.save_bot_strategy(fork_bcs)
    _active("own-fork", fork_bcs)

    config.id = "own-fork"
    tab.rebind_identity()

    assert tab._bcs is not None and tab._bcs.id == fork_bcs.id
    assert tab._bcs_card._name.full_text() == "Fork BCS"


def test_rebind_identity_is_silent(qapp, store):
    """combat_config_changed reaches the controller as an unsaved edit — against a bot
    Save As has just written."""
    tab, config, _bcs = _tab_with_active_bcs("silent-orig")
    seen: list[int] = []
    tab.combat_config_changed.connect(lambda: seen.append(1))

    config.id = "silent-fork"
    tab.rebind_identity()

    assert seen == []


def test_rebind_identity_redraws_the_state_cards(qapp, store):
    """The rows carry the original's links; leaving them is the same lie as the card."""
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    store_mod.save_duel_strategy(dcs)
    tab, config, bcs = _tab_with_active_bcs("redraw-orig")
    bcs.state_assignments = [StateAssignment(state_ids=["state_9"], duel_combat_strategy_id=dcs.id)]
    store_mod.save_bot_strategy(bcs)
    tab.set_config(config)
    assert tab._state_bindings["state_9"].duel_combat_strategy_id == dcs.id

    config.id = "redraw-fork"
    tab.rebind_identity()

    assert tab._state_bindings["state_9"].duel_combat_strategy_id is None
    # Back to a pill on the bot-default card, the strategy it named being gone.
    assert tab._state_panels == {}
    assert _default_fights(tab) == ["Boss"]


# ---------------------------------------------------------------------------
# Orphan pruning — assignments for deleted states must not linger
# ---------------------------------------------------------------------------


def test_save_prunes_assignments_for_states_the_bot_no_longer_has(qapp, store):
    bcs = BotCombatStrategy(
        name="BCS",
        bot_id="bot-1",
        state_assignments=[
            StateAssignment(
                state_ids=["state_9"],
                duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="live")]),
            ),
            StateAssignment(
                state_ids=["state_404"],  # node was deleted from the bot
                duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="orphan")]),
            ),
        ],
    )
    tab = _tab_for(bcs, _config())

    tab._save_bcs()

    kept = {sid for a in tab._bcs.state_assignments for sid in a.state_ids}
    assert kept == {"state_9"}


def test_save_prunes_only_the_dead_ids_from_a_shared_assignment(qapp, store):
    bcs = BotCombatStrategy(
        name="BCS",
        bot_id="bot-1",
        state_assignments=[
            StateAssignment(
                state_ids=["state_9", "state_404"],
                duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="x")]),
            )
        ],
    )
    tab = _tab_for(bcs, _config())

    tab._save_bcs()

    assert [a.state_ids for a in tab._bcs.state_assignments] == [["state_9"]]


def test_prune_is_skipped_when_no_config_is_loaded(qapp, store):
    """With no bot to compare against, every assignment would look orphaned."""
    tab = CombatTab()
    assignments = [StateAssignment(state_ids=["state_9"])]

    assert tab._prune_orphans(assignments) == assignments


# ---------------------------------------------------------------------------
# Sidebar width — long DSL summaries must not force a horizontal scrollbar
# ---------------------------------------------------------------------------


def test_long_assignment_summaries_do_not_widen_the_tab(qapp, store):
    """Regression: three duels of inline DSL demanded ~1173 px in a 220 px sidebar."""
    long_dsl = "{1} shatter@boss {2} empower {3} monster@boss {4} feint[potent]@boss"
    bcs = BotCombatStrategy(
        name="A rather long bot combat strategy name that goes on",
        bot_id="bot-1",
        state_assignments=[
            StateAssignment(
                state_ids=["state_9"],
                duel_config=DuelConfig(
                    player_assignments=[
                        PlayerAssignment(players=[1], dsl=long_dsl),
                        PlayerAssignment(players=[2, 3], dsl=long_dsl),
                        PlayerAssignment(players=[4], dsl=long_dsl),
                    ]
                ),
            )
        ],
    )
    tab = _tab_for(bcs, _config())

    scroll_content = tab.findChild(QScrollArea).widget()
    assert scroll_content.minimumSizeHint().width() < 400


# ---------------------------------------------------------------------------
# CombatTab — grouping
# ---------------------------------------------------------------------------
#
# One Duel Strategy, one card, however many fights use it. The old arrangement drew one
# panel per fight and backed several of them with the same object, so editing one
# silently edited all of them while the rest showed stale summaries.


def _multi_config(*labels: str) -> BotConfig:
    """A bot with one exposed combat state per label, ids state_1..state_N."""
    states = [StateModel(id="s0", node_type="sleep")]
    states += [
        StateModel(
            id=f"state_{i}",
            node_type="handle_combat",
            label=label,
            params={"use_default_strategy": False},
        )
        for i, label in enumerate(labels, start=1)
    ]
    return BotConfig(id="test-bot", name="Multi Bot", initial="s0", recovery_state="s0", states=states, transitions=[])


def _linked_bcs(dcs_id: str, *groups: list[str]) -> BotCombatStrategy:
    return BotCombatStrategy(
        name="BCS",
        bot_id="bot-1",
        state_assignments=[StateAssignment(state_ids=list(g), duel_combat_strategy_id=dcs_id) for g in groups],
    )


def _saved_dcs(name: str = "Shared") -> DuelCombatStrategy:
    dcs = DuelCombatStrategy(name=name)
    store_mod.save_duel_strategy(dcs)
    return dcs


def test_fights_sharing_a_strategy_draw_one_card(qapp, store):
    """Mirror Lake: four fights, one Scald Incindiate, one card."""
    dcs = _saved_dcs("Scald Incindiate")
    bcs = _linked_bcs(dcs.id, ["state_1", "state_2", "state_3", "state_4"])
    tab = _tab_for(bcs, _multi_config("Tse-Tse", "Spectral", "Goliath", "Starburst"))

    assert len(tab._group_panels()) == 1
    assert tab._group_panels()[0].titles() == ["Tse-Tse", "Spectral", "Goliath", "Starburst"]


def test_every_fight_in_a_group_resolves_to_the_same_card(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_1", "state_2"]), _multi_config("A", "B"))

    assert tab._state_panels["state_1"] is tab._state_panels["state_2"]


def test_separate_assignments_naming_one_strategy_are_drawn_once(qapp, store):
    dcs = _saved_dcs("AoE spam")
    bcs = _linked_bcs(dcs.id, ["state_1"], ["state_2"])
    tab = _tab_for(bcs, _multi_config("Chief Whip", "Noxious Gas Golem"))

    assert len(tab._group_panels()) == 1
    assert tab._group_panels()[0].titles() == ["Chief Whip", "Noxious Gas Golem"]


def test_unassigned_fights_share_one_card(qapp, store):
    """The noise this replaced: a fresh Bot Combat Strategy drew a row per fight, all of
    them saying the same nothing."""
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _multi_config("A", "B", "C"))

    assert tab._group_panels() == []
    assert _default_fights(tab) == ["A", "B", "C"]


def test_giving_one_fight_a_strategy_leaves_the_others_together(qapp, store):
    """Each pill is its own binding, so a pick moves one fight and not the card."""
    dcs = _saved_dcs()
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _multi_config("A", "B", "C"))

    with patch("bartlebot.gui.sidebar.combat_tab.CombatStrategyDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        tab._on_pick_for_fight("state_2")

    assert [p.titles() for p in tab._group_panels()] == [["B"]]
    assert _default_fights(tab) == ["A", "C"]


def test_the_default_card_is_drawn_above_the_cards_that_except_it(qapp, store):
    """First, and the add-card last. It is the only card guaranteed to be there and the
    one every other card is an exception to; a card that migrated from the foot of the
    list to the head of it as overrides appeared would be worse than either place."""
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_2"]), _multi_config("A", "B", "C"))

    layout = tab._strategies_layout
    order = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert order == [tab._default_panel, tab._group_panels()[0], tab._add_card]
    assert _default_fights(tab) == ["A", "C"]


# ---------------------------------------------------------------------------
# The add-card: giving several fights a strategy at once
# ---------------------------------------------------------------------------


def _add_strategy(tab: CombatTab, dcs_id: str | None) -> None:
    """Run the add-card's first dialog, stubbed, and stop before its popup opens.

    ``open_fights_popup`` is patched out rather than allowed to fire: the real one is
    deferred a turn by a QTimer, so an event loop turn in some later test would open a
    live popup window belonging to a tab that test never made.
    """
    with (
        patch("bartlebot.gui.sidebar.combat_tab.CombatStrategyDialog") as MockDlg,
        patch.object(StrategyPanel, "open_fights_popup"),
    ):
        MockDlg.return_value.exec.return_value = dcs_id is not None
        MockDlg.return_value.selected_id.return_value = dcs_id
        tab._on_add_strategy()


def test_adding_a_strategy_asks_for_its_fights_straight_away(qapp, store):
    """The two dialogs are chained. A card with no fights forms no group, so it would
    draw nothing on the next rebuild and be pruned on the next save -- it cannot be left
    on screen for the user to fill in later, because nothing would bring it back."""
    dcs = _saved_dcs()
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _multi_config("A", "B"))

    with (
        patch("bartlebot.gui.sidebar.combat_tab.CombatStrategyDialog") as MockDlg,
        patch.object(StrategyPanel, "open_fights_popup") as mock_popup,
    ):
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        tab._on_add_strategy()
        # The popup is deferred a turn: it positions itself against the button's place on
        # screen, which the layout has not given the card just inserted above it.
        qapp.processEvents()

    assert tab._pending_panel is not None
    assert mock_popup.called


def test_a_cancelled_add_makes_no_card(qapp, store):
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _multi_config("A", "B"))

    _add_strategy(tab, None)

    assert tab._pending_panel is None
    assert tab._group_panels() == []
    assert _default_fights(tab) == ["A", "B"]


def test_a_strategy_added_with_no_fights_leaves_no_card(qapp, store):
    """Closing the fights popup without choosing any undoes the add outright, rather
    than leaving a card that governs nothing and would vanish on the next save anyway."""
    dcs = _saved_dcs()
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _multi_config("A", "B"))
    _add_strategy(tab, dcs.id)
    panel = tab._pending_panel

    panel.membership_changed.emit([])

    assert tab._pending_panel is None
    assert tab._group_panels() == []
    assert _default_fights(tab) == ["A", "B"]
    assert store_mod.load_bot_strategy(tab._bcs.id).state_assignments == []


def test_a_strategy_added_with_fights_takes_them_off_the_default_card(qapp, store):
    """The exclusivity rule the whole section runs on: a fight belongs to exactly one
    card, so the ones chosen here leave the default card's pills."""
    dcs = _saved_dcs()
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _multi_config("A", "B", "C"))
    _add_strategy(tab, dcs.id)

    tab._pending_panel.membership_changed.emit(["state_1", "state_3"])

    assert [p.titles() for p in tab._group_panels()] == [["A", "C"]]
    assert _default_fights(tab) == ["B"]
    assert tab._pending_panel is None


def test_adding_a_strategy_the_bot_already_uses_lands_on_that_card(qapp, store):
    """One strategy is one card however many fights use it. Adding the strategy another
    card already names has to join it rather than draw a second card beside it."""
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_1"]), _multi_config("A", "B", "C"))
    _add_strategy(tab, dcs.id)

    tab._pending_panel.membership_changed.emit(["state_3"])

    assert [p.titles() for p in tab._group_panels()] == [["A", "C"]]
    assert _default_fights(tab) == ["B"]


def test_the_add_flow_saves_what_it_committed(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _multi_config("A", "B"))
    _add_strategy(tab, dcs.id)

    tab._pending_panel.membership_changed.emit(["state_1"])

    saved = store_mod.load_bot_strategy(tab._bcs.id)
    assert [(a.state_ids, a.duel_combat_strategy_id) for a in saved.state_assignments] == [(["state_1"], dcs.id)]


def test_the_default_card_survives_a_rebuild(qapp, store):
    """It is persistent for the reason the panels are re-keyed rather than rebuilt: its
    pills are live targets, and a click travelling to one must not land on a corpse."""
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_1"]), _multi_config("A", "B"))
    card = tab._default_panel

    tab._rebuild_state_assignments()

    assert tab._default_panel is card
    assert not card.isHidden()
    assert _default_fights(tab) == ["B"]


# ---------------------------------------------------------------------------
# CombatTab — regrouping when a link changes
# ---------------------------------------------------------------------------


def test_linking_a_fight_to_an_in_use_strategy_merges_the_rows(qapp, store):
    """The whole point: pick a strategy another card uses and they become one."""
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_1"]), _multi_config("A", "B"))
    assert len(tab._group_panels()) == 1

    with patch("bartlebot.gui.sidebar.combat_tab.CombatStrategyDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        tab._on_pick_for_fight("state_2")

    assert len(tab._group_panels()) == 1
    assert tab._group_panels()[0].titles() == ["A", "B"]
    assert tab._state_panels["state_1"] is tab._state_panels["state_2"]


def test_a_merged_group_is_saved_as_one_assignment(qapp, store):
    dcs = _saved_dcs()
    bcs = _linked_bcs(dcs.id, ["state_1"])
    tab = _tab_for(bcs, _multi_config("A", "B"))

    with patch("bartlebot.gui.sidebar.combat_tab.CombatStrategyDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        tab._on_pick_for_fight("state_2")

    saved = store_mod.load_bot_strategy(bcs.id)
    assert len(saved.state_assignments) == 1
    assert saved.state_assignments[0].state_ids == ["state_1", "state_2"]


def test_clearing_a_link_moves_the_whole_group_onto_the_bot_default_card(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_1"], ["state_2"]), _multi_config("A", "B"))

    with patch("bartlebot.gui.sidebar.combat_tab.CombatStrategyDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = None
        tab._on_pick_duel(tab._group_panels()[0])

    # Both fights unlinked together — they were one group.
    assert tab._group_panels() == []
    assert _default_fights(tab) == ["A", "B"]


def test_picking_on_the_default_card_does_not_rebuild_the_group_cards(qapp, store):
    """The bot default has no group of its own — nothing below it has moved."""
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_1"]), _multi_config("A", "B"))
    before = tab._state_panels["state_1"]

    with patch("bartlebot.gui.sidebar.combat_tab.CombatStrategyDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        tab._on_pick_duel(tab._default_panel)

    assert tab._state_panels["state_1"] is before
    assert tab._default_panel._name.full_text() == "Shared"


# ---------------------------------------------------------------------------
# CombatTab — Applies to...
# ---------------------------------------------------------------------------


def test_checking_a_fight_adds_it_to_the_group(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_1"]), _multi_config("A", "B"))
    assignment = tab._state_bindings["state_1"]

    tab._on_membership_changed(assignment, ["state_1", "state_2"])

    assert tab._group_panels()[0].titles() == ["A", "B"]
    assert tab._state_bindings["state_2"] is assignment


def test_committing_an_unchanged_selection_does_nothing(qapp, store):
    """The popup commits on every close, including the ones where nothing was touched;
    rebuilding the section for those would drop the scroll position for no reason."""
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_1"]), _multi_config("A", "B"))
    assignment = tab._state_bindings["state_1"]
    panel_before = tab._state_panels["state_1"]

    tab._on_membership_changed(assignment, ["state_1"])

    assert assignment.state_ids == ["state_1"]
    assert tab._state_panels["state_1"] is panel_before


def test_unchecking_the_last_fight_leaves_the_strategy_unused(qapp, store):
    """A legitimate act -- "stop using this here" -- and one click to undo, so the
    popup does not refuse it. The card goes and both fights fall back to the pills."""
    dcs = _saved_dcs()
    bcs = _linked_bcs(dcs.id, ["state_1"])
    tab = _tab_for(bcs, _multi_config("A", "B"))

    tab._on_membership_changed(tab._state_bindings["state_1"], [])

    assert tab._group_panels() == []
    assert _default_fights(tab) == ["A", "B"]
    assert store_mod.load_bot_strategy(bcs.id).state_assignments == []


def test_removing_a_fight_from_an_inline_group_keeps_its_config(qapp, store):
    """Decision: the departing fight deep-copies the DSL so it goes on working."""
    bcs = BotCombatStrategy(
        name="BCS",
        bot_id="bot-1",
        state_assignments=[
            StateAssignment(
                state_ids=["state_1", "state_2"],
                duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="blade")]),
            )
        ],
    )
    tab = _tab_for(bcs, _multi_config("A", "B"))
    assignment = tab._state_bindings["state_1"]

    tab._on_membership_changed(assignment, ["state_1"])

    detached = tab._state_bindings["state_2"]
    assert detached is not assignment
    assert detached.duel_config.player_assignments[0].dsl == "blade"
    assert [p.titles() for p in tab._group_panels()] == [["A"], ["B"]]


def test_membership_edits_are_saved_immediately(qapp, store):
    dcs = _saved_dcs()
    bcs = _linked_bcs(dcs.id, ["state_1"])
    tab = _tab_for(bcs, _multi_config("A", "B"))

    tab._on_membership_changed(tab._state_bindings["state_1"], ["state_1", "state_2"])

    saved = store_mod.load_bot_strategy(bcs.id)
    assert saved.state_assignments[0].state_ids == ["state_1", "state_2"]


def test_the_popup_says_what_every_fight_uses_today(qapp, store):
    """Checking a fight moves it out of whatever group holds it, and the right-hand
    slot is what makes that visible before the click rather than after it."""
    dcs = _saved_dcs("Scald Incindiate")
    tab = _tab_for(_linked_bcs(dcs.id, ["state_1"]), _multi_config("A", "B"))

    sections = tab._fight_sections(exposed_combat_states(tab._config))

    assert [(r.label, r.meta) for _, rows in sections for r in rows] == [
        ("A", "Scald Incindiate"),
        ("B", STATE_INHERIT_LABEL),
    ]


def test_the_popup_preselects_the_fights_the_card_already_covers(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_1", "state_2"]), _multi_config("A", "B", "C"))

    assert tab._group_panels()[0]._fights_btn.selection() == ["state_1", "state_2"]


def test_binding_descriptions_match_the_card_wording(qapp, store):
    """The dialog has to read as the same thing the card does, so both take their
    words from the same constants."""
    dcs = _saved_dcs("Scald Incindiate")
    bcs = BotCombatStrategy(
        name="BCS",
        bot_id="bot-1",
        state_assignments=[
            StateAssignment(state_ids=["state_1"], duel_combat_strategy_id=dcs.id),
            StateAssignment(
                state_ids=["state_2"],
                duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="x")]),
            ),
        ],
    )
    tab = _tab_for(bcs, _multi_config("A", "B", "C"))

    assert tab._describe_state_binding("state_1") == "Scald Incindiate"
    assert tab._describe_state_binding("state_2") == CUSTOM_LABEL
    assert tab._describe_state_binding("state_3") == STATE_INHERIT_LABEL


# ---------------------------------------------------------------------------
# Deck cards
# ---------------------------------------------------------------------------


def _content_texts(card) -> list[str]:
    layout = card._content_layout
    return [layout.itemAt(i).widget().text() for i in range(layout.count())]


# The section is never shown in these tests (no top-level window under it), so
# isVisible() would read false regardless of what this widget's own code did —
# isHidden() tracks the explicit show()/hide() state these tests exercise.


def test_a_slot_with_no_deck_says_so_and_refuses_its_body(qapp):
    section = _PlayerDecksSection()
    section.refresh({})

    card = section.card(1)
    assert card._summary.full_text() == _NO_DECK
    assert card._refuses_activation()

    card.activated.emit()
    assert not card.is_expanded


def test_a_body_click_unfolds_the_card_list(qapp):
    section = _PlayerDecksSection()
    section.refresh({1: DeckData(normal={"Test Card": 4})})
    card = section.card(1)

    card.activated.emit()

    assert card.is_expanded
    assert not card._content.isHidden()
    assert "Test Card × 4" in _content_texts(card)


def test_a_second_body_click_folds_it_again(qapp):
    section = _PlayerDecksSection()
    section.refresh({1: DeckData(normal={"Test Card": 4})})
    card = section.card(1)

    card.activated.emit()
    card.activated.emit()

    assert not card.is_expanded
    assert card._content.isHidden()


def test_refresh_does_not_fold_an_unfolded_card(qapp):
    """refresh() runs after every import/apply/save/load/clear — far more often than
    anyone folds a card, or the disclosure would read as broken rather than persistent."""
    section = _PlayerDecksSection()
    section.refresh({1: DeckData(normal={"Test Card": 4})})
    section.card(1).activated.emit()

    section.refresh({1: DeckData(normal={"Test Card": 4}), 2: DeckData(normal={"Other Card": 2})})

    assert section.card(1).is_expanded
    assert not section.card(1)._content.isHidden()


def test_refresh_updates_an_unfolded_cards_list(qapp):
    section = _PlayerDecksSection()
    section.refresh({1: DeckData(normal={"Test Card": 4})})
    section.card(1).activated.emit()

    section.refresh({1: DeckData(normal={"Test Card": 4, "Second Card": 1})})

    assert "Second Card × 1" in _content_texts(section.card(1))


def test_refresh_folds_a_card_whose_deck_became_empty(qapp):
    section = _PlayerDecksSection()
    section.refresh({1: DeckData(normal={"Test Card": 4})})
    section.card(1).activated.emit()

    section.refresh({1: DeckData()})

    assert not section.card(1).is_expanded
    assert section.card(1)._content.isHidden()


def test_clear_is_offered_only_where_there_is_a_deck_to_clear(qapp):
    section = _PlayerDecksSection()

    section.refresh({})
    assert _CLEAR_DECK not in _verb_labels(section.card(1))

    section.refresh({1: DeckData(normal={"Test Card": 4})})
    assert _CLEAR_DECK in _verb_labels(section.card(1))
    # Off the face: a trash one icon from a body click that unfolds a list.
    assert section.card(1).action_button(_CLEAR_DECK) is None


def test_the_archmastery_override_is_named_in_the_card(qapp):
    """Which of the three is in force is a fact about the deck, so it belongs where the
    rest of them are rather than behind two hovers of a submenu."""
    section = _PlayerDecksSection()

    section.refresh({1: DeckData(normal={"Card": 1}, archmastery_school="Fire", apply_archmastery=True)})

    card = section.card(1)
    assert not card._archmastery.isHidden()
    assert "Fire" in card._archmastery.full_text()
    assert "always set it" in card._archmastery.full_text()


def test_no_archmastery_verbs_without_a_school_to_set(qapp):
    """A deck with no saved school has nothing to set, so the override would be a
    choice with no effect either way."""
    section = _PlayerDecksSection()
    section.refresh({1: DeckData(normal={"Card": 1})})

    assert not any("Archmastery" in label for label in _verb_labels(section.card(1)))
    assert section.card(1)._archmastery.isHidden()


def test_an_archmastery_verb_emits_the_slot_and_the_value(qapp):
    section = _PlayerDecksSection()
    section.refresh({1: DeckData(normal={"Card": 1}, archmastery_school="Fire")})
    seen: list[tuple[int, object]] = []
    section.archmastery_mode_changed.connect(lambda slot, mode: seen.append((slot, mode)))

    verb = next(v for v in section.card(1)._verbs() if "never" in v.label)
    verb.run()

    assert seen == [(1, False)]


def test_deck_verbs_reach_the_tab_with_their_slot(qapp):
    section = _PlayerDecksSection()
    seen: list[int] = []
    section.import_requested.connect(seen.append)

    section.card(3).import_requested.emit()

    assert seen == [3]


# ---------------------------------------------------------------------------
# Disabled controls explain themselves
# ---------------------------------------------------------------------------


def test_apply_deck_tooltip_names_the_missing_client(qapp):
    tab = CombatTab()
    tab.set_client_count(0)

    button = tab._decks_section.card(1).action_button(_APPLY_DECK)
    assert not button.isEnabled()
    assert "No game client connected for P1" in button.toolTip()


def test_apply_deck_tooltip_names_the_missing_deck_when_a_client_is_present(qapp):
    tab = CombatTab()
    tab.set_client_count(4)

    button = tab._decks_section.card(1).action_button(_APPLY_DECK)
    assert not button.isEnabled()
    assert "No deck saved for P1" in button.toolTip()


def test_deck_tooltips_name_the_run_lock(qapp):
    tab = CombatTab()
    tab.set_client_count(4)

    tab.set_editable(False)

    card = tab._decks_section.card(1)
    assert card.action_button(_IMPORT_DECK).toolTip() == LOCKED
    assert card.action_button(_APPLY_DECK).toolTip() == LOCKED
