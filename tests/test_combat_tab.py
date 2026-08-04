"""Tests for CombatTab's inline duel-binding UI: _DuelBindingPanel and the
per-state / Default Combat Strategy sections built from it.

Covers the Phase 5c/6 rework: states and the BCS default can now hold an
inline DuelConfig edited directly in the sidebar, or link to a shared library
DuelCombatStrategy — mirroring PlayerAssignment's inline/linked split one
level up. These are real widget tests (QApplication required): the behavior
under test — expand/collapse, link/unlink/promote, live-mutating the bound
model — lives entirely in the Qt wiring.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox

from bartlebot import strategy_store as store_mod
from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DuelCombatStrategy,
    DuelConfig,
    PlayerAssignment,
    StateAssignment,
)
from bartlebot.gui.sidebar.combat_tab import CombatTab, _DefaultBinding, _DuelBindingPanel


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the strategy store's default root at an empty tmp_path for the test."""
    for sub in ("strategies/player", "strategies/duel", "strategies/decks", "bot_strategies"):
        (tmp_path / sub).mkdir(parents=True)
    monkeypatch.setattr(store_mod, "project_root", lambda: tmp_path)
    return tmp_path


def _config(*, label: str = "Boss", use_default: bool = False) -> BotConfig:
    return BotConfig(
        name="Test Bot",
        initial="s0",
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


# ---------------------------------------------------------------------------
# _DuelBindingPanel — display state
# ---------------------------------------------------------------------------


def test_panel_shows_linked_strategy_name_and_meta(qapp):
    dcs = DuelCombatStrategy(
        name="Frenzy AoE", min_players=4, player_assignments=[PlayerAssignment(players=[1], dsl="x")]
    )
    binding = StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id)
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [dcs], [])

    assert panel._chip.text() == "Frenzy AoE"
    assert panel._meta_lbl.isVisible()
    assert not panel._toggle_btn.isEnabled()


def test_panel_shows_missing_strategy_when_linked_id_not_found(qapp):
    binding = StateAssignment(state_ids=["s1"], duel_combat_strategy_id="ghost-id")
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])

    assert "(missing strategy)" in panel._chip.text()


def test_panel_shows_not_configured_when_unlinked_and_empty(qapp):
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])

    assert panel._chip.text() == "BartleBot Default Combat Strategy"
    assert panel._meta_lbl.text() == "Not configured"
    assert panel._toggle_btn.isEnabled()


def test_panel_shows_custom_preview_when_unlinked_and_nonempty(qapp):
    binding = StateAssignment(
        state_ids=["s1"], duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="blade")])
    )
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])

    assert panel._chip.text() == "(custom)"
    assert "P1" in panel._assignments_lbl.text()


def test_panel_hides_itself_when_binding_is_none(qapp):
    panel = _DuelBindingPanel("Default")
    panel.load(StateAssignment(state_ids=["s1"]), [], [])
    assert not panel.isHidden()

    panel._binding = None
    panel._refresh()
    assert panel.isHidden()


# ---------------------------------------------------------------------------
# _DuelBindingPanel — expand / collapse
# ---------------------------------------------------------------------------


def test_expanding_an_unlinked_panel_shows_the_config_editor(qapp):
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])
    assert not panel._config_editor.isVisibleTo(panel)

    panel._toggle_btn.setChecked(True)
    panel._on_toggle()

    assert panel._config_editor.isVisibleTo(panel)


def test_editing_inline_config_while_expanded_mutates_the_binding_live(qapp):
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])
    panel._toggle_btn.setChecked(True)
    panel._on_toggle()

    panel._config_editor._min_spin.setValue(3)

    assert binding.duel_config.min_players == 3


def test_editing_inline_config_emits_changed(qapp):
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])
    panel._toggle_btn.setChecked(True)
    panel._on_toggle()

    seen = []
    panel.changed.connect(lambda: seen.append(True))
    panel._config_editor._min_spin.setValue(2)

    assert seen == [True]


def test_reload_resets_expand_state(qapp):
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])
    panel._toggle_btn.setChecked(True)
    panel._on_toggle()
    assert panel._expanded

    panel.load(StateAssignment(state_ids=["s2"]), [], [])

    assert not panel._expanded
    assert not panel._config_editor.isVisibleTo(panel)


def test_linked_panel_disables_toggle_even_if_previously_expanded(qapp):
    dcs = DuelCombatStrategy(name="Boss DCS")
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [dcs], [])
    panel._toggle_btn.setChecked(True)
    panel._on_toggle()

    linked = StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id)
    panel.load(linked, [dcs], [])

    assert not panel._toggle_btn.isEnabled()
    assert not panel._config_editor.isVisibleTo(panel)


# ---------------------------------------------------------------------------
# _DuelBindingPanel — link / unlink / promote
# ---------------------------------------------------------------------------


def test_pick_link_sets_the_strategy_id_and_emits_changed(qapp):
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [dcs], [])

    seen = []
    panel.changed.connect(lambda: seen.append(True))
    with patch("bartlebot.gui.sidebar.combat_tab.StrategyPickerDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        panel._on_pick_link()

    assert binding.duel_combat_strategy_id == dcs.id
    assert seen == [True]


def test_pick_link_cancel_leaves_the_binding_unchanged(qapp):
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])

    seen = []
    panel.changed.connect(lambda: seen.append(True))
    with patch("bartlebot.gui.sidebar.combat_tab.StrategyPickerDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = False
        panel._on_pick_link()

    assert binding.duel_combat_strategy_id is None
    assert seen == []


def test_pick_link_none_selection_unlinks(qapp):
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    binding = StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id)
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [dcs], [])

    with patch("bartlebot.gui.sidebar.combat_tab.StrategyPickerDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = None
        panel._on_pick_link()

    assert binding.duel_combat_strategy_id is None


def test_unlink_forks_the_linked_strategys_fields_into_the_inline_config(qapp, store):
    dcs = DuelCombatStrategy(
        name="Frenzy AoE",
        min_players=2,
        max_players=3,
        combat_order=[2, 1, 3],
        player_assignments=[PlayerAssignment(players=[1], dsl="frenzy dsl")],
    )
    binding = StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id)
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [dcs], [])

    panel._on_unlink()

    assert binding.duel_combat_strategy_id is None
    assert binding.duel_config.min_players == 2
    assert binding.duel_config.max_players == 3
    assert binding.duel_config.combat_order == [2, 1, 3]
    assert binding.duel_config.player_assignments[0].dsl == "frenzy dsl"


def test_unlink_via_the_default_panel_does_not_crash(qapp):
    """Reproduces the reported crash: unlinking through _DefaultBinding specifically
    (not a raw StateAssignment) used to raise AttributeError — duel_config had no
    setter on that adapter, and _on_unlink assigns a whole new DuelConfig rather
    than mutating the existing one in place.
    """
    dcs = DuelCombatStrategy(
        name="Frenzy AoE",
        player_assignments=[PlayerAssignment(players=[1], dsl="frenzy dsl")],
    )
    bcs = BotCombatStrategy(name="B", bot_id="bot-1", default_strategy_id=dcs.id)
    panel = _DuelBindingPanel("Default")
    panel.load(_DefaultBinding(bcs), [dcs], [])

    panel._on_unlink()  # must not raise

    assert bcs.default_strategy_id is None
    assert bcs.default_duel_config.player_assignments[0].dsl == "frenzy dsl"


def test_unlink_forks_a_deep_copy_not_a_shared_reference(qapp):
    dcs = DuelCombatStrategy(name="Frenzy AoE", player_assignments=[PlayerAssignment(players=[1], dsl="original")])
    binding = StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id)
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [dcs], [])

    panel._on_unlink()
    binding.duel_config.player_assignments[0].dsl = "edited"

    assert dcs.player_assignments[0].dsl == "original"


def test_unlink_with_a_dangling_dcs_reference_produces_an_empty_config(qapp):
    """The linked strategy's file is gone — fork what we can, i.e. nothing."""
    binding = StateAssignment(state_ids=["s1"], duel_combat_strategy_id="ghost-id")
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])

    panel._on_unlink()

    assert binding.duel_combat_strategy_id is None
    assert binding.duel_config.is_empty()


def test_promote_creates_a_library_entry_and_links_to_it(qapp, store):
    binding = StateAssignment(
        state_ids=["s1"], duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="custom")])
    )
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])

    seen_presets_changed = []
    panel.presets_changed.connect(lambda: seen_presets_changed.append(True))
    with patch.object(QInputDialog, "getText", return_value=("My Preset", True)):
        panel._on_promote()

    assert binding.is_linked
    saved = store_mod.load_duel_strategy(binding.duel_combat_strategy_id)
    assert saved.name == "My Preset"
    assert saved.player_assignments[0].dsl == "custom"
    assert seen_presets_changed == [True]


def test_promote_appends_to_the_panels_own_dcs_list(qapp, store):
    """The panel's local all_dcs is updated immediately so its own chip/menu are
    consistent even before the owner refreshes from disk.
    """
    binding = StateAssignment(
        state_ids=["s1"], duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="custom")])
    )
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])

    with patch.object(QInputDialog, "getText", return_value=("My Preset", True)):
        panel._on_promote()

    assert any(d.name == "My Preset" for d in panel._all_dcs)


def test_promote_with_empty_config_is_refused(qapp):
    binding = StateAssignment(state_ids=["s1"])  # empty
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])

    with patch.object(QMessageBox, "information") as mock_info:
        panel._on_promote()

    mock_info.assert_called_once()
    assert binding.duel_combat_strategy_id is None


def test_promote_cancel_does_not_create_anything(qapp, store):
    binding = StateAssignment(
        state_ids=["s1"], duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="custom")])
    )
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])

    with patch.object(QInputDialog, "getText", return_value=("", False)):
        panel._on_promote()

    assert binding.duel_combat_strategy_id is None
    assert store_mod.list_duel_strategies() == []


# ---------------------------------------------------------------------------
# _DuelBindingPanel — menu contents
# ---------------------------------------------------------------------------


def test_menu_offers_save_as_preset_when_unlinked(qapp):
    binding = StateAssignment(
        state_ids=["s1"], duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="x")])
    )
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])
    panel._rebuild_menu()

    texts = [a.text() for a in panel._menu.actions()]
    assert any("Save as preset" in t for t in texts)
    assert not any("Unlink" in t for t in texts)


def test_save_as_preset_is_disabled_when_config_is_empty(qapp):
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])
    panel._rebuild_menu()

    action = next(a for a in panel._menu.actions() if "Save as preset" in a.text())
    assert not action.isEnabled()


def test_menu_offers_unlink_and_edit_when_linked(qapp):
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    binding = StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id)
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [dcs], [])
    panel._rebuild_menu()

    texts = [a.text() for a in panel._menu.actions()]
    assert any("Unlink" in t for t in texts)
    assert any("Edit in library" in t for t in texts)


def test_edit_in_library_emits_the_linked_dcs_id(qapp):
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    binding = StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id)
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [dcs], [])

    seen = []
    panel.edit_in_library_requested.connect(seen.append)
    panel._on_edit_in_library()

    assert seen == [dcs.id]


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
    """Regression: duel_config had a getter but no setter, so _on_unlink — which
    assigns a brand-new DuelConfig rather than mutating in place — crashed with
    AttributeError the moment it ran against the Default panel specifically.
    StateAssignment never caught this because its field is settable for free.
    """
    bcs = BotCombatStrategy(name="B", bot_id="bot-1")
    binding = _DefaultBinding(bcs)
    new_config = DuelConfig(min_players=3)

    binding.duel_config = new_config

    assert bcs.default_duel_config is new_config


def test_default_binding_is_linked_false_when_no_default_strategy_id():
    bcs = BotCombatStrategy(name="B", bot_id="bot-1")
    assert _DefaultBinding(bcs).is_linked is False


# ---------------------------------------------------------------------------
# CombatTab — panel lifecycle
# ---------------------------------------------------------------------------


def test_set_config_creates_one_panel_per_exposed_state(qapp, store):
    tab = CombatTab()
    tab.set_config(_config(label="Howling Chaney"))

    assert list(tab._state_panels.keys()) == ["state_9"]


def test_set_config_with_no_exposed_states_creates_no_panels(qapp, store):
    tab = CombatTab()
    config = BotConfig(name="Bot", initial="s0", states=[StateModel(id="s0", node_type="sleep")], transitions=[])
    tab.set_config(config)

    assert tab._state_panels == {}
    # isVisible() is unreliable for a widget tree that was never shown in a
    # window; isHidden() reflects whether show()/hide() was actually called.
    assert not tab._no_assignments_lbl.isHidden()


def test_untouched_state_binding_is_not_persisted(qapp, store):
    """A state row nobody edited must not bloat the saved file."""
    bcs = BotCombatStrategy(name="BCS", bot_id="bot-1")
    store_mod.save_bot_strategy(bcs)

    tab = CombatTab()
    config = _config()
    config.id = "bot-1"
    from bartlebot.settings_manager import SettingsManager

    SettingsManager.instance().set_active_strategy("bot-1", bcs.id)
    tab.set_config(config)

    tab._save_bcs()

    assert store_mod.load_bot_strategy(bcs.id).state_assignments == []


def test_linked_state_binding_is_persisted_even_with_empty_duel_config(qapp, store):
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    store_mod.save_duel_strategy(dcs)
    bcs = BotCombatStrategy(name="BCS", bot_id="bot-1")
    store_mod.save_bot_strategy(bcs)

    tab = CombatTab()
    config = _config()
    config.id = "bot-1"
    from bartlebot.settings_manager import SettingsManager

    SettingsManager.instance().set_active_strategy("bot-1", bcs.id)
    tab.set_config(config)

    panel = tab._state_panels["state_9"]
    with patch("bartlebot.gui.sidebar.combat_tab.StrategyPickerDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        panel._on_pick_link()
    tab._save_bcs()

    reloaded = store_mod.load_bot_strategy(bcs.id)
    assert len(reloaded.state_assignments) == 1
    assert reloaded.state_assignments[0].duel_combat_strategy_id == dcs.id


def test_inline_state_binding_with_content_is_persisted(qapp, store):
    bcs = BotCombatStrategy(name="BCS", bot_id="bot-1")
    store_mod.save_bot_strategy(bcs)

    tab = CombatTab()
    config = _config()
    config.id = "bot-1"
    from bartlebot.settings_manager import SettingsManager

    SettingsManager.instance().set_active_strategy("bot-1", bcs.id)
    tab.set_config(config)

    panel = tab._state_panels["state_9"]
    panel._toggle_btn.setChecked(True)
    panel._on_toggle()
    panel._config_editor._on_add_assignment()
    tab._save_bcs()

    reloaded = store_mod.load_bot_strategy(bcs.id)
    assert len(reloaded.state_assignments) == 1
    assert reloaded.state_assignments[0].duel_config.player_assignments


def test_refresh_strategy_data_does_not_replace_panel_widgets(qapp, store):
    """Data refreshes must not interrupt an in-progress inline edit elsewhere."""
    bcs = BotCombatStrategy(name="BCS", bot_id="bot-1")
    store_mod.save_bot_strategy(bcs)

    tab = CombatTab()
    config = _config()
    config.id = "bot-1"
    from bartlebot.settings_manager import SettingsManager

    SettingsManager.instance().set_active_strategy("bot-1", bcs.id)
    tab.set_config(config)

    panel_before = tab._state_panels["state_9"]
    default_panel_before = tab._default_panel

    tab.refresh_strategy_data()

    assert tab._state_panels["state_9"] is panel_before
    assert tab._default_panel is default_panel_before


def test_set_editable_does_not_replace_panel_widgets(qapp, store):
    bcs = BotCombatStrategy(name="BCS", bot_id="bot-1")
    store_mod.save_bot_strategy(bcs)

    tab = CombatTab()
    config = _config()
    config.id = "bot-1"
    from bartlebot.settings_manager import SettingsManager

    SettingsManager.instance().set_active_strategy("bot-1", bcs.id)
    tab.set_config(config)

    panel_before = tab._state_panels["state_9"]
    tab.set_editable(False)

    assert tab._state_panels["state_9"] is panel_before
    assert not panel_before._chip.isEnabled()


def test_promote_on_a_state_panel_propagates_to_the_default_panels_library(qapp, store):
    """presets_changed from one panel refreshes every panel's dcs list, including siblings."""
    bcs = BotCombatStrategy(name="BCS", bot_id="bot-1")
    store_mod.save_bot_strategy(bcs)

    tab = CombatTab()
    config = _config()
    config.id = "bot-1"
    from bartlebot.settings_manager import SettingsManager

    SettingsManager.instance().set_active_strategy("bot-1", bcs.id)
    tab.set_config(config)

    state_panel = tab._state_panels["state_9"]
    state_panel._binding.duel_config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="x")])
    with patch.object(QInputDialog, "getText", return_value=("Shared Preset", True)):
        state_panel._on_promote()

    assert any(d.name == "Shared Preset" for d in tab._default_panel._all_dcs)
