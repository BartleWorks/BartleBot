"""Filtering a card list in the combat strategy dialog must not disturb the editor beside it.

``QListWidget.clear()`` emits ``currentItemChanged(None, previous)``, and
repopulating then emits it again for the reselect. Both reached the selection
slot, so a single keystroke in a filter box hid and reloaded the editor, flushed
it to disk twice, and put the DSL caret back to position 0.

The flush half of that is gone with the autosave — nothing below
``CombatStrategyDialog``'s Save button writes a file any more — but the caret and
the reload are still real, and the defect is still entirely in the signal/slot
wiring, which is why these are widget tests. They cover the same three lists the
Strategy Library window's versions did; the window itself was retired by #4.
"""

import pytest
from PyQt6.QtCore import Qt

from bartlebot import strategy_store as store_mod
from bartlebot.bot.strategy_models import DeckPreset, DuelCombatStrategy, PlayerCombatStrategy
from bartlebot.gui.combat_strategies.dialog import (
    CombatStrategyDialog,
    DialogMode,
    matches_filter,
    matches_query,
)
from bartlebot.strategy_store import StrategyKind
from bartlebot.strategy_summary import StrategySummary
from bartlebot.strategy_workset import StrategyWorkingSet


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the strategy store's default root at an empty tmp_path for the test."""
    (tmp_path / "strategies" / "player").mkdir(parents=True)
    (tmp_path / "strategies" / "duel").mkdir(parents=True)
    (tmp_path / "strategies" / "decks").mkdir(parents=True)
    (tmp_path / "bot_strategies").mkdir(parents=True)
    monkeypatch.setattr(store_mod, "project_root", lambda: tmp_path)
    monkeypatch.setattr(store_mod, "product_dir", lambda: tmp_path)
    return tmp_path


def _dialog(tmp_path, players=(), duels=(), decks=(), **kw) -> CombatStrategyDialog:
    ws = StrategyWorkingSet(
        players=list(players), duels=list(duels), decks=list(decks), root=tmp_path, user_root=tmp_path
    )
    return CombatStrategyDialog(ws, parent=None, **kw)


def _count_calls(monkeypatch, obj, name):
    """Replace ``obj.name`` with a counting wrapper; return the call list."""
    calls = []
    original = getattr(obj, name)

    def wrapper(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(obj, name, wrapper)
    return calls


# ---------------------------------------------------------------------------
# The rule itself, Qt-free
# ---------------------------------------------------------------------------


def test_the_filter_matches_the_description_not_just_the_name():
    summary = StrategySummary(name="A", description="tank and spank")

    assert matches_filter(summary, "tank") is not None
    assert matches_filter(summary, "heal") is None


def test_an_empty_filter_matches_everything():
    assert matches_filter(StrategySummary(name="A", description=""), "   ") is not None


def test_the_filter_ignores_case():
    assert matches_filter(StrategySummary(name="Frenzy AoE", description=""), "FRENZY") is not None


def test_matches_query_agrees_with_matches_filter():
    """The cached-haystack path the list uses must not drift from the summary rule."""
    summary = StrategySummary(name="Frenzy AoE", description="tank and spank")
    haystack = matches_filter(summary, "")

    for query in ("FRENZY", "tank", "heal", "", "   "):
        assert matches_query(haystack, query) is (matches_filter(summary, query) is not None)


# ---------------------------------------------------------------------------
# Filtering must not re-summarize (#43)
# ---------------------------------------------------------------------------


def _visible(page):
    return [i for i, _c in page._card_map if not i.isHidden()]


def test_filtering_hides_rows_instead_of_rebuilding_them(qapp, store):
    """Building a card summarizes its entry, and summarizing parses every DSL it
    references — seconds of lag per keystroke. Filtering must touch no widget."""
    a = PlayerCombatStrategy(name="Alpha", dsl="any<blade>[sharp]@ally(2)")
    b = PlayerCombatStrategy(name="Beta", dsl="any<blade>[sharp]@ally(2)")
    dlg = _dialog(store, players=[a, b], kind=StrategyKind.PLAYER, current_id=a.id)
    page = dlg._pages[StrategyKind.PLAYER]
    before = list(page._card_map)

    page._filter.setText("Alp")

    assert page._card_map == before
    assert [i.data(Qt.ItemDataRole.UserRole) for i in _visible(page)] == [a.id]


def test_clearing_the_filter_shows_every_row_again(qapp, store):
    a = PlayerCombatStrategy(name="Alpha")
    b = PlayerCombatStrategy(name="Beta")
    dlg = _dialog(store, players=[a, b], kind=StrategyKind.PLAYER, current_id=a.id)
    page = dlg._pages[StrategyKind.PLAYER]
    page._filter.setText("zzz")
    assert _visible(page) == []

    page._filter.setText("")

    assert len(_visible(page)) == 2


def test_filtering_does_not_re_summarize_any_entry(qapp, store, monkeypatch):
    """The direct measurement of the bug: one keystroke used to summarize the
    whole list, and each summary parses a DSL."""
    players = [PlayerCombatStrategy(name=f"P{i}", dsl="any<blade>[sharp]@ally(2)") for i in range(4)]
    dlg = _dialog(store, players=players, kind=StrategyKind.PLAYER, current_id=players[0].id)
    page = dlg._pages[StrategyKind.PLAYER]
    calls = _count_calls(monkeypatch, page, "summarize")

    for text in ("a", "an", "any"):
        page._filter.setText(text)

    assert calls == []


def test_a_new_entry_still_appears_in_the_list(qapp, store):
    """The other half of the split: a structural change does rebuild."""
    dlg = _dialog(store, players=[PlayerCombatStrategy(name="Alpha")], kind=StrategyKind.PLAYER)
    page = dlg._pages[StrategyKind.PLAYER]

    page._on_new()

    assert len(_visible(page)) == 2
    assert page.selected_id() is not None


# ---------------------------------------------------------------------------
# Player Strategies
# ---------------------------------------------------------------------------


def test_typing_in_the_pcs_filter_leaves_the_dsl_caret_alone(qapp, store):
    """The repro from the filter-reload fix: one keystroke used to send the caret back to 0."""
    a = PlayerCombatStrategy(name="Alpha", dsl="any<damage> @ enemy")
    dlg = _dialog(store, players=[a, PlayerCombatStrategy(name="Beta")], kind=StrategyKind.PLAYER, current_id=a.id)
    page = dlg._pages[StrategyKind.PLAYER]
    editor = page._editor
    editor._dsl_edit.setFocus()
    cursor = editor._dsl_edit.textCursor()
    cursor.setPosition(5)
    editor._dsl_edit.setTextCursor(cursor)

    page._filter.setText("Alp")

    assert editor._dsl_edit.textCursor().position() == 5


def test_typing_in_the_pcs_filter_does_not_reload_the_editor(qapp, store, monkeypatch):
    a = PlayerCombatStrategy(name="Alpha", dsl="blade")
    dlg = _dialog(store, players=[a], kind=StrategyKind.PLAYER, current_id=a.id)
    page = dlg._pages[StrategyKind.PLAYER]
    loads = _count_calls(monkeypatch, page._editor, "load")

    page._filter.setText("Alp")

    assert loads == []


def test_filtering_the_selected_pcs_out_of_view_keeps_it_in_the_editor(qapp, store):
    a = PlayerCombatStrategy(name="Alpha", dsl="blade")
    dlg = _dialog(store, players=[a, PlayerCombatStrategy(name="Beta")], kind=StrategyKind.PLAYER, current_id=a.id)
    page = dlg._pages[StrategyKind.PLAYER]

    page._filter.setText("Beta")

    assert page.selected_id() == a.id
    assert page._editor.strategy() is a or page._editor.strategy().id == a.id


def test_clearing_the_pcs_filter_reselects_the_edited_row(qapp, store):
    a = PlayerCombatStrategy(name="Alpha")
    dlg = _dialog(store, players=[a, PlayerCombatStrategy(name="Beta")], kind=StrategyKind.PLAYER, current_id=a.id)
    page = dlg._pages[StrategyKind.PLAYER]
    page._filter.setText("Beta")

    page._filter.setText("")

    assert page.selected_id() == a.id
    assert page._list.currentItem() is not None


def test_edits_made_while_the_pcs_row_is_filtered_out_still_land(qapp, store):
    a = PlayerCombatStrategy(name="Alpha", dsl="blade")
    dlg = _dialog(store, players=[a, PlayerCombatStrategy(name="Beta")], kind=StrategyKind.PLAYER, current_id=a.id)
    page = dlg._pages[StrategyKind.PLAYER]
    page._filter.setText("Beta")

    page._editor._dsl_edit.setPlainText("pass")

    assert dlg._ws.get(StrategyKind.PLAYER, a.id).dsl == "pass"


def test_selecting_another_pcs_still_loads_it(qapp, store):
    a = PlayerCombatStrategy(name="Alpha")
    b = PlayerCombatStrategy(name="Beta")
    dlg = _dialog(store, players=[a, b], kind=StrategyKind.PLAYER, current_id=a.id)
    page = dlg._pages[StrategyKind.PLAYER]

    page.select(b.id)

    assert page._editor.strategy().id == b.id


# ---------------------------------------------------------------------------
# Duel Strategies
# ---------------------------------------------------------------------------


def test_typing_in_the_dcs_filter_does_not_reload_the_editor(qapp, store, monkeypatch):
    d = DuelCombatStrategy(name="Frenzy AoE")
    dlg = _dialog(store, duels=[d], kind=StrategyKind.DUEL, current_id=d.id)
    page = dlg._pages[StrategyKind.DUEL]
    loads = _count_calls(monkeypatch, page._editor, "load")

    page._filter.setText("Fren")

    assert loads == []


def test_filtering_the_selected_dcs_out_of_view_leaves_it_pickable(qapp, store):
    """Use returns what was selected, not what happens to be listed."""
    d = DuelCombatStrategy(name="Frenzy AoE")
    dlg = _dialog(
        store,
        duels=[d, DuelCombatStrategy(name="Other")],
        kind=StrategyKind.DUEL,
        mode=DialogMode.PICK,
        current_id=d.id,
    )
    page = dlg._pages[StrategyKind.DUEL]

    page._filter.setText("Other")

    assert page.selected_id() == d.id


def test_a_deleted_dcs_detaches_the_editor(qapp, store):
    """The one case where the pane really should empty."""
    d = DuelCombatStrategy(name="Frenzy AoE")
    dlg = _dialog(store, duels=[d], kind=StrategyKind.DUEL, current_id=d.id)
    page = dlg._pages[StrategyKind.DUEL]

    dlg._ws.remove(StrategyKind.DUEL, d.id)
    page.rebuild_list()

    assert page.selected_id() is None
    assert not page._empty_lbl.isHidden()


# ---------------------------------------------------------------------------
# Deck Presets
# ---------------------------------------------------------------------------


def test_typing_in_the_decks_filter_does_not_reload_the_detail_panel(qapp, store, monkeypatch):
    p = DeckPreset(name="Storm 60")
    dlg = _dialog(store, decks=[p], kind=StrategyKind.DECK, current_id=p.id)
    page = dlg._pages[StrategyKind.DECK]
    loads = _count_calls(monkeypatch, page._editor, "load")

    page._filter.setText("Storm")

    assert loads == []


def test_renaming_a_deck_relabels_the_row_without_reloading_the_panel(qapp, store, monkeypatch):
    p = DeckPreset(name="Storm 60")
    dlg = _dialog(store, decks=[p], kind=StrategyKind.DECK, current_id=p.id)
    page = dlg._pages[StrategyKind.DECK]
    loads = _count_calls(monkeypatch, page._editor, "load")

    page._editor._name_edit.setText("Storm 60 v2")

    assert loads == []
    assert dlg._ws.get(StrategyKind.DECK, p.id).name == "Storm 60 v2"
    assert page._card_map[0][1]._name_lbl.text() == "Storm 60 v2"
