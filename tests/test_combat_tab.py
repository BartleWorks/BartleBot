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
    EDIT_IN_LIBRARY,
    LOCKED,
    MISSING_LABEL,
    NOT_CONFIGURED_LABEL,
    SAVE_AS_PRESET,
    STATE_INHERIT_LABEL,
    StrategyPanel,
)


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
    assert EDIT_IN_LIBRARY not in _verb_labels(panel)

    panel.load(StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id), [dcs], [])
    assert EDIT_IN_LIBRARY in _verb_labels(panel)


def test_the_edit_button_exists_from_the_start_and_is_shown_when_it_applies(qapp):
    """CardFrame builds face buttons once, before any binding is loaded.

    A conditional face verb would therefore never get a button at all, and could never
    appear when the binding later became linked — which is why the two face buttons are
    built from ``_face_verbs`` and this one is shown and hidden instead.
    """
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    panel = StrategyPanel()
    button = panel.action_button(EDIT_IN_LIBRARY)
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
    panel = StrategyPanel(STATE_INHERIT_LABEL, "", supports_membership=True)
    panel.load(StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id), [dcs], [])

    assert APPLIES_TO in _verb_labels(panel)


def test_the_default_panel_offers_no_applies_to(qapp):
    """The Default Duel Combat Strategy has no state_ids — it is the fallback for
    every fight no group claims."""
    bcs = BotCombatStrategy(name="B", bot_id="bot-1")
    panel = StrategyPanel()
    panel.load(_DefaultBinding(bcs), [], [])

    assert APPLIES_TO not in _verb_labels(panel)


def test_applies_to_is_withheld_on_an_unassigned_fight(qapp):
    """A group of fights holding no config is what the grouping pulls back apart on the
    next rebuild, so offering it would look like an edit that quietly undid itself."""
    panel = StrategyPanel(STATE_INHERIT_LABEL, "", supports_membership=True)
    panel.load(StateAssignment(state_ids=["s1"]), [], [])

    assert APPLIES_TO not in _verb_labels(panel)
    assert not panel._applies_btn.isEnabled()


def test_applies_to_is_enabled_once_a_fight_has_an_inline_config(qapp):
    panel = StrategyPanel(STATE_INHERIT_LABEL, "", supports_membership=True)
    panel.load(
        StateAssignment(
            state_ids=["s1"],
            duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="blade")]),
        ),
        [],
        [],
    )

    assert panel._applies_btn.isEnabled()
    assert APPLIES_TO in _verb_labels(panel)


def test_the_applies_to_row_emits_membership_requested(qapp):
    """The label naming which fights a card governs and the control that edits them
    are one thing."""
    dcs = DuelCombatStrategy(name="Shared")
    panel = StrategyPanel(STATE_INHERIT_LABEL, "", supports_membership=True)
    panel.load(StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id), [dcs], [])
    seen: list[bool] = []
    panel.membership_requested.connect(lambda: seen.append(True))

    panel._applies_btn.click()

    assert seen == [True]


# ---------------------------------------------------------------------------
# StrategyPanel — fight titles
# ---------------------------------------------------------------------------


def test_every_fight_in_a_group_gets_its_own_line(qapp):
    panel = StrategyPanel(supports_membership=True)

    panel.set_titles(["Tse-Tse", "Spectral", "Goliath", "Starburst"])

    visible = [lbl.full_text() for lbl in panel._title_lbls if lbl.isVisibleTo(panel)]
    assert visible == ["Tse-Tse", "Spectral", "Goliath", "Starburst"]


def test_shrinking_a_group_hides_the_surplus_lines(qapp):
    panel = StrategyPanel(supports_membership=True)
    panel.set_titles(["A", "B", "C"])

    panel.set_titles(["A"])

    assert panel.titles() == ["A"]
    assert [lbl.isVisibleTo(panel) for lbl in panel._title_lbls] == [True, False, False]


def test_growing_a_group_reuses_existing_labels(qapp):
    panel = StrategyPanel(supports_membership=True)
    panel.set_titles(["A"])
    first = panel._title_lbls[0]

    panel.set_titles(["A", "B"])

    assert panel._title_lbls[0] is first


def test_the_applies_to_row_is_hidden_where_membership_is_not_supported(qapp):
    panel = StrategyPanel()
    panel.set_titles(["A", "B"])

    assert panel._applies_row.isHidden()


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


def test_set_config_creates_one_panel_per_exposed_state(qapp, store):
    tab = CombatTab()
    tab.set_config(_config(label="Howling Chaney"))

    assert list(tab._state_panels.keys()) == ["state_9"]


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
    assert tab._assignments_section.isHidden()


def test_the_specialized_section_appears_once_a_fight_opts_out(qapp, store):
    tab = CombatTab()
    tab.set_config(_config())

    assert not tab._assignments_section.isHidden()


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

    panel = tab._state_panels["state_9"]
    with patch("bartlebot.gui.sidebar.combat_tab.StrategyPickerDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        tab._on_pick_duel(panel)

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
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config())
    panel_before = tab._state_panels["state_9"]
    default_before = tab._default_panel

    tab.refresh_strategy_data()

    assert tab._state_panels["state_9"] is panel_before
    assert tab._default_panel is default_before


def test_set_editable_locks_every_card_without_replacing_it(qapp, store):
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config())
    panel_before = tab._state_panels["state_9"]

    tab.set_editable(False)

    assert tab._state_panels["state_9"] is panel_before
    assert panel_before._refuses_activation()
    assert not tab._bcs_card.action_button(_CHANGE_BCS).isEnabled()


def test_state_panel_says_it_inherits_the_bot_default_not_the_builtin(qapp, store):
    """resolve_combat() reaches for bcs.default_strategy_id / default_duel_config before
    the built-in strategy, and the pre-run warning says so too. Labelling the state card
    "BartleBot Default Combat Strategy" claimed the bot default would be skipped."""
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config())

    state_name = tab._state_panels["state_9"]._name.full_text()
    default_name = tab._default_panel._name.full_text()

    assert state_name == STATE_INHERIT_LABEL
    assert default_name == BUILT_IN_LABEL
    assert state_name != default_name


# ---------------------------------------------------------------------------
# CombatTab — picking a Duel Combat Strategy
# ---------------------------------------------------------------------------


def test_pick_sets_the_strategy_id_and_saves(qapp, store):
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    store_mod.save_duel_strategy(dcs)
    bcs = BotCombatStrategy(name="BCS", bot_id="bot-1")
    tab = _tab_for(bcs, _config())

    with patch("bartlebot.gui.sidebar.combat_tab.StrategyPickerDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        tab._on_pick_duel(tab._default_panel)

    assert tab._bcs.default_strategy_id == dcs.id
    assert store_mod.load_bot_strategy(bcs.id).default_strategy_id == dcs.id


def test_pick_cancel_leaves_the_binding_unchanged(qapp, store):
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config())

    with patch("bartlebot.gui.sidebar.combat_tab.StrategyPickerDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = False
        tab._on_pick_duel(tab._default_panel)

    assert tab._bcs.default_strategy_id is None


def test_pick_none_unlinks(qapp, store):
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    store_mod.save_duel_strategy(dcs)
    bcs = BotCombatStrategy(name="BCS", bot_id="bot-1", default_strategy_id=dcs.id)
    tab = _tab_for(bcs, _config())

    with patch("bartlebot.gui.sidebar.combat_tab.StrategyPickerDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = None
        tab._on_pick_duel(tab._default_panel)

    assert tab._bcs.default_strategy_id is None
    assert tab._default_panel._name.full_text() == BUILT_IN_LABEL


def test_the_picker_none_row_is_labelled_with_what_the_card_falls_through_to(qapp, store):
    """Choosing nothing in the dialog and reading the card afterwards say the same
    words — and the two levels say different words."""
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _config())

    with patch("bartlebot.gui.sidebar.combat_tab.StrategyPickerDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = False
        tab._on_pick_duel(tab._default_panel)
        default_label = MockDlg.call_args.kwargs["none_label"]
        tab._on_pick_duel(tab._state_panels["state_9"])
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
    tab = _tab_for(_inline_bcs(), _config())

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
    assert tab._state_panels["state_9"]._name.full_text() == STATE_INHERIT_LABEL


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


def test_unassigned_fights_stay_on_their_own_rows(qapp, store):
    """Not collapsed: giving one fight a strategy must stay a single click."""
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _multi_config("A", "B", "C"))

    assert [p.titles() for p in tab._group_panels()] == [["A"], ["B"], ["C"]]


def test_configured_groups_are_drawn_above_unassigned_fights(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_2"]), _multi_config("A", "B", "C"))

    assert [p.titles() for p in tab._group_panels()] == [["B"], ["A"], ["C"]]


def test_the_divider_appears_only_with_fights_on_both_sides(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_1"]), _multi_config("A", "B"))

    assert not tab._group_divider.isHidden()


def test_no_divider_when_every_fight_is_unassigned(qapp, store):
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _multi_config("A", "B"))

    assert tab._group_divider.isHidden()


def test_no_divider_when_every_fight_is_configured(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_1", "state_2"]), _multi_config("A", "B"))

    assert tab._group_divider.isHidden()


def test_the_divider_survives_a_rebuild(qapp, store):
    """It is persistent so its themed stylesheet is not rebuilt away."""
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_1"]), _multi_config("A", "B"))
    divider = tab._group_divider

    tab._rebuild_state_assignments()

    assert tab._group_divider is divider
    assert not divider.isHidden()


# ---------------------------------------------------------------------------
# CombatTab — regrouping when a link changes
# ---------------------------------------------------------------------------


def test_linking_a_fight_to_an_in_use_strategy_merges_the_rows(qapp, store):
    """The whole point: pick a strategy another card uses and they become one."""
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_1"]), _multi_config("A", "B"))
    assert len(tab._group_panels()) == 2

    with patch("bartlebot.gui.sidebar.combat_tab.StrategyPickerDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        tab._on_pick_duel(tab._state_panels["state_2"])

    assert len(tab._group_panels()) == 1
    assert tab._group_panels()[0].titles() == ["A", "B"]
    assert tab._state_panels["state_1"] is tab._state_panels["state_2"]


def test_a_merged_group_is_saved_as_one_assignment(qapp, store):
    dcs = _saved_dcs()
    bcs = _linked_bcs(dcs.id, ["state_1"])
    tab = _tab_for(bcs, _multi_config("A", "B"))

    with patch("bartlebot.gui.sidebar.combat_tab.StrategyPickerDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        tab._on_pick_duel(tab._state_panels["state_2"])

    saved = store_mod.load_bot_strategy(bcs.id)
    assert len(saved.state_assignments) == 1
    assert saved.state_assignments[0].state_ids == ["state_1", "state_2"]


def test_clearing_a_link_moves_the_whole_group_below_the_divider(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_1"], ["state_2"]), _multi_config("A", "B"))

    with patch("bartlebot.gui.sidebar.combat_tab.StrategyPickerDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = None
        tab._on_pick_duel(tab._group_panels()[0])

    # Both fights unlinked together — they were one group.
    assert [p.titles() for p in tab._group_panels()] == [["A"], ["B"]]
    assert tab._group_divider.isHidden()


def test_picking_on_the_default_card_does_not_rebuild_the_group_cards(qapp, store):
    """The bot default has no group of its own — nothing below it has moved."""
    dcs = _saved_dcs()
    tab = _tab_for(BotCombatStrategy(name="BCS", bot_id="bot-1"), _multi_config("A", "B"))
    before = tab._state_panels["state_1"]

    with patch("bartlebot.gui.sidebar.combat_tab.StrategyPickerDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        tab._on_pick_duel(tab._default_panel)

    assert tab._state_panels["state_1"] is before
    assert tab._default_panel._name.full_text() == "Shared"


# ---------------------------------------------------------------------------
# CombatTab — Applies to...
# ---------------------------------------------------------------------------


def test_applies_to_adds_a_fight_to_the_group(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_1"]), _multi_config("A", "B"))
    assignment = tab._state_bindings["state_1"]

    with patch("bartlebot.gui.sidebar.applies_to_dialog.AppliesToDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_ids.return_value = ["state_1", "state_2"]
        tab._on_edit_membership(assignment)

    assert tab._group_panels()[0].titles() == ["A", "B"]
    assert tab._state_bindings["state_2"] is assignment


def test_applies_to_cancel_changes_nothing(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_for(_linked_bcs(dcs.id, ["state_1"]), _multi_config("A", "B"))
    assignment = tab._state_bindings["state_1"]

    with patch("bartlebot.gui.sidebar.applies_to_dialog.AppliesToDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = False
        MockDlg.return_value.selected_ids.return_value = ["state_1", "state_2"]
        tab._on_edit_membership(assignment)

    assert assignment.state_ids == ["state_1"]


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

    with patch("bartlebot.gui.sidebar.applies_to_dialog.AppliesToDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_ids.return_value = ["state_1"]
        tab._on_edit_membership(assignment)

    detached = tab._state_bindings["state_2"]
    assert detached is not assignment
    assert detached.duel_config.player_assignments[0].dsl == "blade"
    assert [p.titles() for p in tab._group_panels()] == [["A"], ["B"]]


def test_membership_edits_are_saved_immediately(qapp, store):
    dcs = _saved_dcs()
    bcs = _linked_bcs(dcs.id, ["state_1"])
    tab = _tab_for(bcs, _multi_config("A", "B"))

    with patch("bartlebot.gui.sidebar.applies_to_dialog.AppliesToDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_ids.return_value = ["state_1", "state_2"]
        tab._on_edit_membership(tab._state_bindings["state_1"])

    saved = store_mod.load_bot_strategy(bcs.id)
    assert saved.state_assignments[0].state_ids == ["state_1", "state_2"]


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
