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
from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox, QScrollArea

from bartlebot import strategy_store as store_mod
from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DuelCombatStrategy,
    DuelConfig,
    PlayerAssignment,
    StateAssignment,
)
from bartlebot.gui.sidebar.combat_tab import (
    _BUILT_IN_LABEL,
    _NO_STRATEGY_LABEL,
    _NOT_CONFIGURED_LABEL,
    _STATE_INHERIT_LABEL,
    CombatTab,
    _DefaultBinding,
    _DuelBindingPanel,
)


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

    assert panel._chip.strategy_name() == "Frenzy AoE"
    assert panel._meta_lbl.isVisible()
    # Nothing to expand: the affordance is withdrawn, not shown greyed out.
    assert not panel._disclosure._interactive


def test_panel_shows_missing_strategy_when_linked_id_not_found(qapp):
    binding = StateAssignment(state_ids=["s1"], duel_combat_strategy_id="ghost-id")
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])

    assert "(missing strategy)" in panel._chip.strategy_name()


def test_panel_shows_not_configured_when_unlinked_and_empty(qapp):
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])

    assert panel._chip.strategy_name() == "BartleBot Default Combat Strategy"
    assert panel._meta_lbl.text() == _NOT_CONFIGURED_LABEL
    assert panel._disclosure._interactive


def test_panel_shows_custom_preview_when_unlinked_and_nonempty(qapp):
    binding = StateAssignment(
        state_ids=["s1"], duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="blade")])
    )
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])

    assert panel._chip.strategy_name() == "(custom)"
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

    panel.set_expanded(True)

    assert panel._config_editor.isVisibleTo(panel)


def test_editing_inline_config_while_expanded_mutates_the_binding_live(qapp):
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])
    panel.set_expanded(True)

    panel._config_editor._min_spin.setValue(3)

    assert binding.duel_config.min_players == 3


def test_editing_inline_config_emits_changed(qapp):
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])
    panel.set_expanded(True)

    seen = []
    panel.changed.connect(lambda: seen.append(True))
    panel._config_editor._min_spin.setValue(2)

    assert seen == [True]


def test_reload_resets_expand_state(qapp):
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])
    panel.set_expanded(True)
    assert panel.is_expanded

    panel.load(StateAssignment(state_ids=["s2"]), [], [])

    assert not panel.is_expanded
    assert not panel._config_editor.isVisibleTo(panel)


def test_linked_panel_withdraws_expand_affordance_even_if_previously_expanded(qapp):
    dcs = DuelCombatStrategy(name="Boss DCS")
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [dcs], [])
    panel.set_expanded(True)

    linked = StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id)
    panel.load(linked, [dcs], [])

    assert not panel._disclosure._interactive
    assert not panel.is_expanded
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
    panel.set_expanded(True)
    panel._config_editor._on_add_assignment()
    tab._save_bcs()

    reloaded = store_mod.load_bot_strategy(bcs.id)
    assert len(reloaded.state_assignments) == 1
    assert reloaded.state_assignments[0].duel_config.player_assignments


# ---------------------------------------------------------------------------
# rebind_identity — Save As gives the loaded bot a new id (#179)
# ---------------------------------------------------------------------------


def _tab_with_active_bcs(bot_id: str = "bot-1"):
    """A tab showing ``bot_id``'s active strategy, with the config it is bound to."""
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415

    bcs = BotCombatStrategy(name="BCS", bot_id=bot_id)
    store_mod.save_bot_strategy(bcs)
    tab = CombatTab()
    config = _config()
    config.id = bot_id
    SettingsManager.instance().set_active_strategy(bot_id, bcs.id)
    tab.set_config(config)
    return tab, config, bcs


def test_rebind_identity_drops_the_originals_strategy(qapp, store):
    """A fork inherits no strategy — the chip must stop naming the original's."""
    tab, config, bcs = _tab_with_active_bcs("drops-orig")
    assert tab._bcs is not None and tab._bcs.id == bcs.id

    config.id = "drops-fork"
    tab.rebind_identity()

    assert tab._bcs is None
    assert tab._all_bcs == []
    assert tab._bcs_chip.strategy_name() == _NO_STRATEGY_LABEL


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
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415

    tab, config, _bcs = _tab_with_active_bcs("own-orig")
    fork_bcs = BotCombatStrategy(name="Fork BCS", bot_id="own-fork")
    store_mod.save_bot_strategy(fork_bcs)
    SettingsManager.instance().set_active_strategy("own-fork", fork_bcs.id)

    config.id = "own-fork"
    tab.rebind_identity()

    assert tab._bcs is not None and tab._bcs.id == fork_bcs.id
    assert tab._bcs_chip.strategy_name() == "Fork BCS"


def test_rebind_identity_commits_a_pending_edit_to_the_original(qapp, store):
    """The debounced edit belongs to the strategy that was on screen when it was
    made. Replacing _bcs without committing it would drop it on the floor."""
    tab, config, bcs = _tab_with_active_bcs("pending-orig")
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    store_mod.save_duel_strategy(dcs)
    tab._state_bindings["state_9"].duel_combat_strategy_id = dcs.id
    tab._schedule_save()
    assert tab._save_timer.isActive()

    config.id = "pending-fork"
    tab.rebind_identity()

    assert not tab._save_timer.isActive()
    reloaded = store_mod.load_bot_strategy(bcs.id)
    assert [a.duel_combat_strategy_id for a in reloaded.state_assignments] == [dcs.id]


def test_rebind_identity_commits_that_edit_silently(qapp, store):
    """combat_config_changed reaches the controller as an unsaved edit — against a
    bot Save As has just written."""
    tab, config, _bcs = _tab_with_active_bcs("silent-orig")
    tab._state_bindings["state_9"].duel_combat_strategy_id = "some-dcs"
    tab._schedule_save()
    seen: list[int] = []
    tab.combat_config_changed.connect(lambda: seen.append(1))

    config.id = "silent-fork"
    tab.rebind_identity()

    assert seen == []


def test_rebind_identity_redraws_the_state_panels(qapp, store):
    """The rows carry the original's links; leaving them is the same lie as the chip."""
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
    assert tab._state_panels["state_9"]._chip.strategy_name() == _STATE_INHERIT_LABEL


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


# ---------------------------------------------------------------------------
# Fallback labelling — state overrides inherit the bot default, not the built-in
# ---------------------------------------------------------------------------


def test_state_panel_says_it_inherits_the_bot_default_not_the_builtin(qapp, store):
    """An empty state override falls through to the BCS default first.

    resolve_combat() reaches for bcs.default_strategy_id / default_duel_config
    before the built-in strategy, and the pre-run warning says so too. Labelling
    the state chip "BartleBot Default Combat Strategy" claimed the bot default
    would be skipped.
    """
    bcs = BotCombatStrategy(name="BCS", bot_id="bot-1")
    store_mod.save_bot_strategy(bcs)

    tab = CombatTab()
    config = _config()
    config.id = "bot-1"
    from bartlebot.settings_manager import SettingsManager

    SettingsManager.instance().set_active_strategy("bot-1", bcs.id)
    tab.set_config(config)

    state_chip = tab._state_panels["state_9"]._chip.strategy_name()
    default_chip = tab._default_panel._chip.strategy_name()

    assert state_chip == _STATE_INHERIT_LABEL
    assert default_chip == _BUILT_IN_LABEL
    assert state_chip != default_chip


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
    store_mod.save_bot_strategy(bcs)

    tab = CombatTab()
    config = _config()
    config.id = "bot-1"
    from bartlebot.settings_manager import SettingsManager

    SettingsManager.instance().set_active_strategy("bot-1", bcs.id)
    tab.set_config(config)
    tab._save_bcs()

    kept = {sid for a in tab._bcs.state_assignments for sid in a.state_ids}
    assert kept == {"state_9"}


def test_save_prunes_only_the_dead_ids_from_a_shared_assignment(qapp, store):
    """A multi-state assignment keeps its live states and loses only the dead one."""
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
    store_mod.save_bot_strategy(bcs)

    tab = CombatTab()
    config = _config()
    config.id = "bot-1"
    from bartlebot.settings_manager import SettingsManager

    SettingsManager.instance().set_active_strategy("bot-1", bcs.id)
    tab.set_config(config)
    tab._save_bcs()

    assert [a.state_ids for a in tab._bcs.state_assignments] == [["state_9"]]


def test_prune_is_skipped_when_no_config_is_loaded(qapp, store):
    """With no bot to compare against, every assignment would look orphaned."""
    tab = CombatTab()
    assignments = [StateAssignment(state_ids=["state_9"])]

    assert tab._prune_orphans(assignments) == assignments


# ---------------------------------------------------------------------------
# Edit lock reaches the inline editor
# ---------------------------------------------------------------------------


def test_set_editable_false_locks_an_expanded_inline_editor(qapp):
    """Disabling only the chip/menu/disclosure left the expanded editor writable mid-run."""
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])
    panel.set_expanded(True)
    panel._config_editor._on_add_assignment()

    panel.set_editable(False)

    editor = panel._config_editor
    assert not editor._min_spin.isEnabled()
    assert not editor._max_spin.isEnabled()
    assert not editor._add_btn.isEnabled()
    assert editor._rows[0]._dsl_edit.isReadOnly()
    assert not editor._rows[0]._player_widget.isEnabled()


def test_set_editable_true_restores_an_inline_row(qapp):
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])
    panel.set_expanded(True)
    panel._config_editor._on_add_assignment()

    panel.set_editable(False)
    panel.set_editable(True)

    assert not panel._config_editor._rows[0]._dsl_edit.isReadOnly()
    assert panel._config_editor._min_spin.isEnabled()


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
    store_mod.save_bot_strategy(bcs)

    tab = CombatTab()
    config = _config()
    config.id = "bot-1"
    from bartlebot.settings_manager import SettingsManager

    SettingsManager.instance().set_active_strategy("bot-1", bcs.id)
    tab.set_config(config)

    scroll_content = tab.findChild(QScrollArea).widget()
    assert scroll_content.minimumSizeHint().width() < 400


# ---------------------------------------------------------------------------
# Disabled controls explain themselves
# ---------------------------------------------------------------------------


def test_apply_deck_tooltip_names_the_missing_client(qapp):
    tab = CombatTab()
    tab.set_client_count(0)

    apply_btn = tab._decks_section._apply_btns[0]
    assert not apply_btn.isEnabled()
    assert "No game client connected for P1" in apply_btn.toolTip()


def test_apply_deck_tooltip_names_the_missing_deck_when_a_client_is_present(qapp):
    tab = CombatTab()
    tab.set_client_count(4)

    apply_btn = tab._decks_section._apply_btns[0]
    assert not apply_btn.isEnabled()
    assert "No deck saved for P1" in apply_btn.toolTip()


def test_deck_tooltips_name_the_run_lock(qapp):
    tab = CombatTab()
    tab.set_client_count(4)

    tab.set_editable(False)

    assert "Locked while the bot is running" in tab._decks_section._import_btns[0].toolTip()
    assert "Locked while the bot is running" in tab._decks_section._apply_btns[0].toolTip()


def test_linked_panel_disclosure_tooltip_says_why_it_cannot_expand(qapp):
    dcs = DuelCombatStrategy(name="Boss DCS")
    binding = StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id)
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [dcs], [])

    assert "unlink" in panel._disclosure.toolTip().lower()


def test_locked_panel_disclosure_tooltip_says_the_bot_is_running(qapp):
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])

    panel.set_editable(False)

    assert "Locked while the bot is running" in panel._disclosure.toolTip()


# ---------------------------------------------------------------------------
# Disclosure affordance is consistent across nesting levels
# ---------------------------------------------------------------------------


def test_nested_disclosure_uses_the_same_glyph_size_as_a_section_header(qapp):
    """A 16 px triangle in a QToolButton beside a 10 px one on a QLabel read as
    two unrelated controls rather than two levels of the same one."""
    from bartlebot.gui.sidebar.sidebar_section import DISCLOSURE_PX

    panel = _DuelBindingPanel("Boss")
    panel.load(StateAssignment(state_ids=["s1"]), [], [])

    # The label is a full line tall so the glyph lines up with the first fight
    # in a multi-fight group; the glyph itself is what must not vary by depth.
    glyph = panel._disclosure._arrow.pixmap()
    assert glyph.width() == DISCLOSURE_PX
    assert glyph.height() == DISCLOSURE_PX
    assert panel._disclosure._arrow.width() == DISCLOSURE_PX


def test_nested_panel_is_indented_below_its_section(qapp):
    """Depth is shown by indentation, since the glyph itself no longer varies."""
    from bartlebot.gui.sidebar.sidebar_section import NEST_INDENT_PX

    panel = _DuelBindingPanel("Boss")

    assert panel.layout().contentsMargins().left() == NEST_INDENT_PX


def test_clicking_the_disclosure_row_toggles_expansion(qapp):
    binding = StateAssignment(state_ids=["s1"])
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [], [])

    panel._disclosure.clicked.emit()
    assert panel.is_expanded

    panel._disclosure.clicked.emit()
    assert not panel.is_expanded


def test_a_linked_panel_ignores_expand_requests(qapp):
    dcs = DuelCombatStrategy(name="Boss DCS")
    binding = StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id)
    panel = _DuelBindingPanel("Boss")
    panel.load(binding, [dcs], [])

    panel.set_expanded(True)

    assert not panel.is_expanded
    assert not panel._config_editor.isVisibleTo(panel)


# ---------------------------------------------------------------------------
# Group titles: one line per fight
# ---------------------------------------------------------------------------
#
# A panel now covers a group of fights rather than exactly one. The membership
# is listed vertically instead of joined and elided, because a truncated list
# hides which fights an edit is about to reach.


def test_a_single_fight_panel_shows_one_title_line(qapp):
    """The common case must look exactly as it did before grouping."""
    panel = _DuelBindingPanel("Defeat Balaur")

    assert panel.titles() == ["Defeat Balaur"]
    assert panel._disclosure.title() == "Defeat Balaur"


def test_every_fight_in_a_group_gets_its_own_line(qapp):
    panel = _DuelBindingPanel("Boss")

    panel.set_titles(["Tse-Tse", "Spectral", "Goliath", "Starburst"])

    row = panel._disclosure
    visible = [lbl.full_text() for lbl in row._title_lbls if lbl.isVisibleTo(row)]
    assert visible == ["Tse-Tse", "Spectral", "Goliath", "Starburst"]


def test_no_fight_is_hidden_by_a_long_neighbour(qapp):
    """Eliding happens per line, so each name keeps its own tooltip."""
    panel = _DuelBindingPanel("Boss")
    long_name = "Defeat " + "Extremely Verbose Boss Name " * 5

    panel.set_titles([long_name, "Defeat Balaur"])

    assert panel.titles() == [long_name, "Defeat Balaur"]
    assert panel._disclosure._title_lbls[1].toolTip() == "Defeat Balaur"


def test_shrinking_a_group_hides_the_surplus_lines(qapp):
    panel = _DuelBindingPanel("Boss")
    panel.set_titles(["A", "B", "C"])

    panel.set_titles(["A"])

    row = panel._disclosure
    assert panel.titles() == ["A"]
    assert [lbl.isVisibleTo(row) for lbl in row._title_lbls] == [True, False, False]


def test_growing_a_group_reuses_existing_labels(qapp):
    """Membership changes while the panel is on screen; destroying widgets
    mid-refresh is what interrupted in-progress edits before."""
    panel = _DuelBindingPanel("Boss")
    first = panel._disclosure._title_lbls[0]

    panel.set_titles(["A", "B"])

    assert panel._disclosure._title_lbls[0] is first


def test_title_returns_the_first_fight(qapp):
    panel = _DuelBindingPanel("Boss")
    panel.set_titles(["Tse-Tse", "Spectral"])

    assert panel._disclosure.title() == "Tse-Tse"


# ---------------------------------------------------------------------------
# Membership action
# ---------------------------------------------------------------------------


def test_a_state_panel_offers_applies_to(qapp):
    panel = _DuelBindingPanel("Boss", supports_membership=True)
    panel.load(StateAssignment(state_ids=["s1"]), [], [])

    panel._rebuild_menu()

    assert any("Applies to" in a.text() for a in panel._menu.actions())


def test_the_default_panel_offers_no_applies_to(qapp):
    """The Default Combat Strategy has no state_ids — it is the fallback for
    every fight no group claims."""
    bcs = BotCombatStrategy(name="B", bot_id="bot-1")
    panel = _DuelBindingPanel("Default")
    panel.load(_DefaultBinding(bcs), [], [])

    panel._rebuild_menu()

    assert not any("Applies to" in a.text() for a in panel._menu.actions())


def test_applies_to_is_offered_for_a_linked_group_too(qapp):
    dcs = DuelCombatStrategy(name="Shared")
    panel = _DuelBindingPanel("Boss", supports_membership=True)
    panel.load(StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id), [dcs], [])

    panel._rebuild_menu()

    texts = [a.text() for a in panel._menu.actions()]
    assert any("Applies to" in t for t in texts)
    assert any("Unlink" in t for t in texts)


def test_applies_to_emits_membership_requested(qapp):
    dcs = DuelCombatStrategy(name="Shared")
    panel = _DuelBindingPanel("Boss", supports_membership=True)
    # Configured, so the action is enabled — an unassigned fight has nothing to share.
    panel.load(StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id), [dcs], [])
    panel._rebuild_menu()
    seen = []
    panel.membership_requested.connect(lambda: seen.append(True))

    next(a for a in panel._menu.actions() if "Applies to" in a.text()).trigger()

    assert seen == [True]


# ---------------------------------------------------------------------------
# CombatTab — grouping
# ---------------------------------------------------------------------------
#
# One Duel Strategy, one row, however many fights use it. The old arrangement
# drew one panel per fight and backed several of them with the same object, so
# editing one silently edited all of them while the rest showed stale summaries.


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


def _tab_with(bcs: BotCombatStrategy, config: BotConfig) -> CombatTab:
    from bartlebot.settings_manager import SettingsManager

    config.id = bcs.bot_id
    store_mod.save_bot_strategy(bcs)
    SettingsManager.instance().set_active_strategy(config.id, bcs.id)
    tab = CombatTab()
    tab.set_config(config)
    return tab


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


def test_fights_sharing_a_strategy_draw_one_panel(qapp, store):
    """Mirror Lake: four fights, one Scald Incindiate, one row."""
    dcs = _saved_dcs("Scald Incindiate")
    bcs = _linked_bcs(dcs.id, ["state_1", "state_2", "state_3", "state_4"])
    tab = _tab_with(bcs, _multi_config("Tse-Tse", "Spectral", "Goliath", "Starburst"))

    assert len(tab._group_panels()) == 1
    assert tab._group_panels()[0].titles() == ["Tse-Tse", "Spectral", "Goliath", "Starburst"]


def test_every_fight_in_a_group_resolves_to_the_same_panel(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_with(_linked_bcs(dcs.id, ["state_1", "state_2"]), _multi_config("A", "B"))

    assert tab._state_panels["state_1"] is tab._state_panels["state_2"]


def test_separate_assignments_naming_one_strategy_are_drawn_once(qapp, store):
    """The shape bot_strategies/f77a6a91 is in: two rows before, one now."""
    dcs = _saved_dcs("AoE spam")
    bcs = _linked_bcs(dcs.id, ["state_1"], ["state_2"])
    tab = _tab_with(bcs, _multi_config("Chief Whip", "Noxious Gas Golem"))

    assert len(tab._group_panels()) == 1
    assert tab._group_panels()[0].titles() == ["Chief Whip", "Noxious Gas Golem"]


def test_unassigned_fights_stay_on_their_own_rows(qapp, store):
    """Not collapsed: giving one fight a strategy must stay a single click."""
    tab = _tab_with(BotCombatStrategy(name="BCS", bot_id="bot-1"), _multi_config("A", "B", "C"))

    assert [p.titles() for p in tab._group_panels()] == [["A"], ["B"], ["C"]]


def test_configured_groups_are_drawn_above_unassigned_fights(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_with(_linked_bcs(dcs.id, ["state_2"]), _multi_config("A", "B", "C"))

    assert [p.titles() for p in tab._group_panels()] == [["B"], ["A"], ["C"]]


def test_the_divider_appears_only_with_fights_on_both_sides(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_with(_linked_bcs(dcs.id, ["state_1"]), _multi_config("A", "B"))

    assert not tab._group_divider.isHidden()


def test_no_divider_when_every_fight_is_unassigned(qapp, store):
    tab = _tab_with(BotCombatStrategy(name="BCS", bot_id="bot-1"), _multi_config("A", "B"))

    assert tab._group_divider.isHidden()


def test_no_divider_when_every_fight_is_configured(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_with(_linked_bcs(dcs.id, ["state_1", "state_2"]), _multi_config("A", "B"))

    assert tab._group_divider.isHidden()


def test_the_divider_survives_a_rebuild(qapp, store):
    """It is persistent so its themed stylesheet is not rebuilt away."""
    dcs = _saved_dcs()
    tab = _tab_with(_linked_bcs(dcs.id, ["state_1"]), _multi_config("A", "B"))
    divider = tab._group_divider

    tab._rebuild_state_assignments()

    assert tab._group_divider is divider
    assert not divider.isHidden()


# ---------------------------------------------------------------------------
# CombatTab — regrouping when a link changes
# ---------------------------------------------------------------------------


def test_linking_a_fight_to_an_in_use_strategy_merges_the_rows(qapp, store):
    """The whole point: pick a strategy another row uses and they become one."""
    dcs = _saved_dcs()
    tab = _tab_with(_linked_bcs(dcs.id, ["state_1"]), _multi_config("A", "B"))
    assert len(tab._group_panels()) == 2

    loose = tab._state_panels["state_2"]
    with patch("bartlebot.gui.sidebar.combat_tab.StrategyPickerDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        loose._on_pick_link()

    assert len(tab._group_panels()) == 1
    assert tab._group_panels()[0].titles() == ["A", "B"]
    assert tab._state_panels["state_1"] is tab._state_panels["state_2"]


def test_a_merged_group_is_saved_as_one_assignment(qapp, store):
    dcs = _saved_dcs()
    bcs = _linked_bcs(dcs.id, ["state_1"])
    tab = _tab_with(bcs, _multi_config("A", "B"))

    with patch("bartlebot.gui.sidebar.combat_tab.StrategyPickerDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = dcs.id
        tab._state_panels["state_2"]._on_pick_link()

    saved = store_mod.load_bot_strategy(bcs.id)
    assert len(saved.state_assignments) == 1
    assert saved.state_assignments[0].state_ids == ["state_1", "state_2"]


def test_clearing_a_link_moves_the_whole_group_below_the_divider(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_with(_linked_bcs(dcs.id, ["state_1"], ["state_2"]), _multi_config("A", "B"))

    with patch("bartlebot.gui.sidebar.combat_tab.StrategyPickerDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_id.return_value = None
        tab._group_panels()[0]._on_pick_link()

    # Both fights unlinked together — they were one group.
    assert [p.titles() for p in tab._group_panels()] == [["A"], ["B"]]
    assert tab._group_divider.isHidden()


def test_typing_in_the_inline_editor_does_not_rebuild_the_panels(qapp, store):
    """changed fires per keystroke; a rebuild there would destroy the editor."""
    tab = _tab_with(BotCombatStrategy(name="BCS", bot_id="bot-1"), _multi_config("A", "B"))
    panel = tab._state_panels["state_1"]
    panel.set_expanded(True)

    panel._config_editor._min_spin.setValue(3)

    assert tab._state_panels["state_1"] is panel
    assert panel.is_expanded


# ---------------------------------------------------------------------------
# CombatTab — Applies to...
# ---------------------------------------------------------------------------


def test_applies_to_adds_a_fight_to_the_group(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_with(_linked_bcs(dcs.id, ["state_1"]), _multi_config("A", "B"))
    assignment = tab._state_bindings["state_1"]

    with patch("bartlebot.gui.sidebar.applies_to_dialog.AppliesToDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_ids.return_value = ["state_1", "state_2"]
        tab._on_edit_membership(assignment)

    assert tab._group_panels()[0].titles() == ["A", "B"]
    assert tab._state_bindings["state_2"] is assignment


def test_applies_to_cancel_changes_nothing(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_with(_linked_bcs(dcs.id, ["state_1"]), _multi_config("A", "B"))
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
    tab = _tab_with(bcs, _multi_config("A", "B"))
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
    """Structural, so it bypasses the debounce timer."""
    dcs = _saved_dcs()
    bcs = _linked_bcs(dcs.id, ["state_1"])
    tab = _tab_with(bcs, _multi_config("A", "B"))

    with patch("bartlebot.gui.sidebar.applies_to_dialog.AppliesToDialog") as MockDlg:
        MockDlg.return_value.exec.return_value = True
        MockDlg.return_value.selected_ids.return_value = ["state_1", "state_2"]
        tab._on_edit_membership(tab._state_bindings["state_1"])

    saved = store_mod.load_bot_strategy(bcs.id)
    assert saved.state_assignments[0].state_ids == ["state_1", "state_2"]


def test_binding_descriptions_match_the_chip_wording(qapp, store):
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
    tab = _tab_with(bcs, _multi_config("A", "B", "C"))

    assert tab._describe_state_binding("state_1") == "Scald Incindiate"
    assert tab._describe_state_binding("state_2") == "(custom)"
    assert tab._describe_state_binding("state_3") == _STATE_INHERIT_LABEL


def _applies_to(panel):
    panel._rebuild_menu()
    return next(a for a in panel._menu.actions() if "Applies to" in a.text())


def test_applies_to_is_disabled_on_an_unassigned_fight(qapp):
    """A group of fights holding no config is what split_unconfigured undoes,
    so offering it would look like an edit that quietly reverted itself."""
    panel = _DuelBindingPanel("Boss", supports_membership=True)
    panel.load(StateAssignment(state_ids=["s1"]), [], [])

    action = _applies_to(panel)

    assert not action.isEnabled()
    assert "nothing to share" in action.toolTip()


def test_applies_to_is_enabled_once_a_fight_has_an_inline_config(qapp):
    binding = StateAssignment(
        state_ids=["s1"],
        duel_config=DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="blade")]),
    )
    panel = _DuelBindingPanel("Boss", supports_membership=True)
    panel.load(binding, [], [])

    assert _applies_to(panel).isEnabled()


def test_applies_to_is_enabled_on_a_linked_group(qapp):
    dcs = DuelCombatStrategy(name="Shared")
    panel = _DuelBindingPanel("Boss", supports_membership=True)
    panel.load(StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id), [dcs], [])

    assert _applies_to(panel).isEnabled()


# ---------------------------------------------------------------------------
# Grouping rail
# ---------------------------------------------------------------------------
#
# Stacked fight names read as a list, but not necessarily as a list that means
# "all of these use the strategy on the right". The rail is what says so.


def test_no_rail_is_drawn_beside_a_single_fight(qapp):
    """It would claim a group that does not exist."""
    panel = _DuelBindingPanel("Defeat Balaur")

    assert not panel._rail.is_active()


def test_a_rail_is_drawn_once_fights_are_grouped(qapp):
    panel = _DuelBindingPanel("Boss")

    panel.set_titles(["Defeat Izft", "Defeat Apep", "Defeat Ammit"])

    assert panel._rail.is_active()


def test_the_rail_goes_away_when_a_group_drops_to_one_fight(qapp):
    panel = _DuelBindingPanel("Boss")
    panel.set_titles(["A", "B"])

    panel.set_titles(["A"])

    assert not panel._rail.is_active()


def test_the_rail_column_is_reserved_whether_or_not_it_is_drawn(qapp):
    """Nothing shifts between a one-fight row and a four-fight row — the same
    reason the withdrawn disclosure arrow keeps its slot."""
    single = _DuelBindingPanel("Only")
    grouped = _DuelBindingPanel("Boss")
    grouped.set_titles(["A", "B"])

    assert single._rail.width() == grouped._rail.width()


def test_the_default_panel_never_shows_a_rail(qapp):
    bcs = BotCombatStrategy(name="B", bot_id="bot-1")
    panel = _DuelBindingPanel("Default")
    panel.load(_DefaultBinding(bcs), [], [])

    assert not panel._rail.is_active()


def test_the_rail_brackets_the_whole_panel_not_just_the_names(qapp):
    """It spans the summary and the inline editor too, so it says "this block is
    one group" rather than "these names belong together"."""
    binding = StateAssignment(state_ids=["s1", "s2"])
    panel = _DuelBindingPanel("Boss")
    panel.set_titles(["A", "B"])
    panel.load(binding, [], [])
    panel.set_expanded(True)
    panel.resize(panel.sizeHint())

    assert panel._rail.height() >= panel._disclosure.height()


def test_a_grouped_panel_in_the_tab_gets_a_rail(qapp, store):
    dcs = _saved_dcs()
    tab = _tab_with(_linked_bcs(dcs.id, ["state_1", "state_2"]), _multi_config("A", "B"))

    assert tab._group_panels()[0]._rail.is_active()


def test_unassigned_rows_get_no_rail(qapp, store):
    tab = _tab_with(BotCombatStrategy(name="BCS", bot_id="bot-1"), _multi_config("A", "B"))

    assert [p._rail.is_active() for p in tab._group_panels()] == [False, False]
