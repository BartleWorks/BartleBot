"""Export… on every kind's own menu, the export summary, and the controller behind them (#170).

What a share file holds is :mod:`tests.test_share_export`; this is how a user gets one.
Nothing here is a real character.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QDialog, QMessageBox

from bartlebot.bot.schema import BotConfig, StateModel, TemplateMeta
from bartlebot.combat.profile_models import BotCombatStrategy
from bartlebot.combat.strategy_store import (
    StrategyKind,
    save_bot_strategy,
    save_deck_preset,
    save_duel_strategy,
    save_player_strategy,
)
from bartlebot.combat.strategy_workset import StrategyWorkingSet
from bartlebot.content import bot_roots, share_export, store_roots
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary
from bartlebot.gui import export_dialog
from bartlebot.gui.canvas.bot_canvas import EXPORT_TEXT, _BotLibraryPanel
from bartlebot.gui.combat_strategies.dialog import EXPORT_UNSAVED_TIP, CombatStrategyDialog
from bartlebot.gui.export_dialog import NOTHING_TO_SEND, Choices, ExportDialog, run_export
from bartlebot.gui.sidebar.combat_tab import _EXPORT_BCS
from bartlebot.settings_manager import SettingsManager
from bartlebot.shared import share_file
from bartlebot.shared.strategy_models import DeckData, DeckPreset, DuelCombatStrategy, PlayerCombatStrategy
from tests.test_combat_tab import _config, _tab_for, store  # noqa: F401  (store is a fixture)
from tests.test_subgraph_integration import ControllerHarness
from tests.test_subgraph_store import _defn


def _bot(bot_id: str = "bot-1", name: str = "Boss Farmer") -> BotConfig:
    return BotConfig(
        id=bot_id,
        name=name,
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="relog")],
        transitions=[],
    )


def _action(menu, text: str):
    return next(a for a in menu.actions() if a.text() == text)


class _Captured:
    """Stands in for ``run_export``: keeps what it was asked to export, and writes nothing."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, parent, build, **kwargs):
        self.calls.append((build, kwargs))
        return None

    @property
    def plan(self) -> share_export.ExportPlan:
        build, _kwargs = self.calls[-1]
        return build(Choices())


# ---------------------------------------------------------------------------
# Each kind's own menu
# ---------------------------------------------------------------------------


def test_a_bot_card_offers_export(qapp):
    panel = _BotLibraryPanel()
    asked: list[str] = []
    panel.export_bot_requested.connect(asked.append)

    _action(panel.bot_menu(Path("bots/farm.json")), EXPORT_TEXT).trigger()

    assert asked == [str(Path("bots/farm.json"))]


def test_a_subgraph_card_offers_export(qapp, tmp_path):
    panel = _BotLibraryPanel()
    library = SubgraphLibrary(tmp_path)
    library.save(_defn("sg", "Loop"))
    panel.set_subgraph_library(library)
    asked: list[str] = []
    panel.export_subgraph_requested.connect(asked.append)

    _action(panel.subgraph_menu("sg"), EXPORT_TEXT).trigger()

    assert asked == ["sg"]


@pytest.fixture
def strategy_roots(tmp_path, monkeypatch):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    monkeypatch.setattr(store_roots, "project_root", lambda: shipped)
    monkeypatch.setattr(store_roots, "product_dir", lambda: user)
    return shipped, user


_LIBRARY_ITEMS = [
    (StrategyKind.DUEL, share_export.DUEL_STRATEGY, DuelCombatStrategy(id="d1", name="Boss"), save_duel_strategy),
    (
        StrategyKind.PLAYER,
        share_export.PLAYER_STRATEGY,
        PlayerCombatStrategy(id="p1", name="Hitter"),
        save_player_strategy,
    ),
    (
        StrategyKind.DECK,
        share_export.DECK,
        DeckPreset(id="k1", name="Storm", deck=DeckData(normal={"Storm Lord": 1})),
        save_deck_preset,
    ),
]


@pytest.mark.parametrize(("kind", "share_kind", "item", "save"), _LIBRARY_ITEMS, ids=lambda v: getattr(v, "value", ""))
def test_each_strategy_dialog_tab_exports_from_its_cards_menu(
    qapp, strategy_roots, monkeypatch, kind, share_kind, item, save
):
    shipped, user = strategy_roots
    save(item, shipped, user)
    dialog = CombatStrategyDialog(StrategyWorkingSet.from_store(root=shipped, user_root=user), kind=kind)
    captured = _Captured()
    monkeypatch.setattr("bartlebot.gui.combat_strategies.dialog.run_export", captured)

    _action(dialog.page(kind).card_menu(item.id), "Export…").trigger()

    plan = captured.plan
    assert plan.share.main == share_file.ItemRef(kind=share_kind, id=item.id)
    assert [i.id for i in plan.share.items] == [item.id]


def test_an_entry_with_unsaved_edits_says_to_save_before_exporting(qapp, strategy_roots):
    shipped, user = strategy_roots
    save_player_strategy(PlayerCombatStrategy(id="p1", name="Hitter"), shipped, user)
    ws = StrategyWorkingSet.from_store(root=shipped, user_root=user)
    dialog = CombatStrategyDialog(ws, kind=StrategyKind.PLAYER)
    ws.get(StrategyKind.PLAYER, "p1").dsl = "pass"

    action = _action(dialog.page(StrategyKind.PLAYER).card_menu("p1"), "Export…")

    assert not action.isEnabled()
    assert action.toolTip() == EXPORT_UNSAVED_TIP


def test_the_bot_combat_strategy_banner_exports_the_strategy_it_names(qapp, store):  # noqa: F811
    bcs = BotCombatStrategy(name="Solo", bot_id="bot-1")
    tab = _tab_for(bcs, _config())
    asked: list = []
    tab.export_bot_strategy_requested.connect(asked.append)

    verb = next(v for v in tab._bcs_banner._verbs() if v.label == _EXPORT_BCS)
    verb.run()

    assert [b.id for b in asked] == [bcs.id]
    assert not verb.on_face, "a menu verb, beside Duplicate"


def test_the_file_menu_exports_whatever_document_is_open(qapp):
    from bartlebot.gui.main_window import MainWindow  # noqa: PLC0415

    window = MainWindow()
    try:
        assert window.action_export.text() == "Export…"
        assert not window.action_export.isEnabled()
        window.set_close_enabled(True)
        assert window.action_export.isEnabled()
        window.set_close_enabled(False)
        assert not window.action_export.isEnabled()
    finally:
        window.bot_canvas.prepare_for_shutdown()


# ---------------------------------------------------------------------------
# The export summary
# ---------------------------------------------------------------------------


def _bot_build(roots_dir: Path, combat: BotCombatStrategy | None):
    sources = share_export.Sources(
        subgraph_library=SubgraphLibrary(roots_dir), strategy_root=roots_dir, strategy_user_root=roots_dir
    )

    def build(choices: Choices):
        return share_export.export_bot(
            _bot(),
            sources,
            official=False,
            combat=combat if choices.include_combat else None,
            include_decks=choices.include_decks,
        )

    return build


def test_the_summary_lists_what_is_sent_and_the_checkboxes_change_it(qapp, tmp_path):
    combat = BotCombatStrategy(
        id="b1", name="Solo", bot_id="bot-1", player_decks={1: DeckData(normal={"Storm Lord": 2})}
    )
    dialog = ExportDialog(_bot_build(tmp_path, combat), combat_name="Solo", offers_decks=True)

    assert dialog.listed() == [("Sent in the file", ["Boss Farmer", "Solo"])]
    assert dialog.commit_button.isEnabled()
    assert dialog.plan.share.items[1].content["player_decks"], "decks on by default"

    dialog.decks_box.setChecked(False)
    assert "player_decks" not in dialog.plan.share.items[1].content

    dialog.combat_box.setChecked(False)
    assert dialog.listed() == [("Sent in the file", ["Boss Farmer"])]
    assert not dialog.decks_box.isEnabled(), "a bot's decks go with its Bot Combat Strategy"


def test_the_summary_says_when_there_is_nothing_to_send(qapp, tmp_path):
    sources = share_export.Sources(subgraph_library=SubgraphLibrary(tmp_path))
    dialog = ExportDialog(lambda _c: share_export.export_bot(_bot(), sources, official=True))

    assert dialog.listed() == [("Official, so not sent: they have it already", ["Boss Farmer"])]
    assert not dialog.commit_button.isEnabled()
    assert not dialog._empty.isHidden()
    assert dialog._empty.text() == NOTHING_TO_SEND


def test_run_export_writes_the_file_under_its_kinds_extension(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ExportDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        export_dialog.QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "for-a-friend"), "")
    )

    written = run_export(None, _bot_build(tmp_path, None))

    assert written == tmp_path / "for-a-friend.bbot"
    assert share_file.read(written).main == share_file.ItemRef(kind=share_export.BOT, id="bot-1")


def test_run_export_writes_nothing_when_the_summary_is_cancelled(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ExportDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    asked: list = []
    monkeypatch.setattr(export_dialog.QFileDialog, "getSaveFileName", lambda *a, **k: asked.append(a) or ("", ""))

    assert run_export(None, _bot_build(tmp_path, None)) is None
    assert asked == [], "no file dialog after Cancel"


# ---------------------------------------------------------------------------
# The controller
# ---------------------------------------------------------------------------

_EXPORT_METHODS = (
    "_export_sources",
    "_export_open_document",
    "_saved_for_export",
    "_active_bot_strategy",
    "_export_bot",
    "_export_subgraph",
    "_export_bot_strategy",
)


@pytest.fixture
def harness(qapp, tmp_path, store, monkeypatch):  # noqa: F811
    from bartlebot.app_controller import AppController  # noqa: PLC0415
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    monkeypatch.setattr(bot_roots, "shipped_bots_dir", lambda: tmp_path / "bots")
    library = SubgraphLibrary(tmp_path / "library")
    canvas = BotCanvas()
    canvas.set_subgraph_library(library)
    h = ControllerHarness(canvas, library)
    for name in _EXPORT_METHODS:
        setattr(h, name, getattr(AppController, name).__get__(h))
    yield h
    canvas.prepare_for_shutdown()


@pytest.fixture
def captured(monkeypatch) -> _Captured:
    fake = _Captured()
    monkeypatch.setattr("bartlebot.app_controller.run_export", fake)
    return fake


def test_a_bots_export_offers_its_active_bot_combat_strategy(harness, captured, tmp_path):
    path = tmp_path / "user-bots" / "farm.json"
    _bot().save(path)
    bcs = BotCombatStrategy(name="Solo", bot_id="bot-1")
    save_bot_strategy(bcs)
    SettingsManager.instance().set_active_strategy("bot-1", bcs.id)

    harness._export_bot(str(path))

    build, kwargs = captured.calls[0]
    assert kwargs == {"combat_name": "Solo", "offers_decks": True}
    assert {i.kind for i in build(Choices()).share.items} == {share_export.BOT, share_export.BOT_COMBAT_STRATEGY}
    assert {i.kind for i in build(Choices(include_combat=False)).share.items} == {share_export.BOT}


def test_a_bot_with_no_active_strategy_offers_no_checkboxes(harness, captured, tmp_path):
    path = tmp_path / "farm.json"
    _bot().save(path)

    harness._export_bot(str(path))

    assert captured.calls[0][1] == {"combat_name": None, "offers_decks": False}


def test_file_export_on_an_open_bot_exports_it_as_saved(harness, captured, tmp_path):
    path = tmp_path / "farm.json"
    _bot().save(path)
    harness._open_bot(str(path))

    harness._export_open_document()

    assert captured.plan.share.main == share_file.ItemRef(kind=share_export.BOT, id="bot-1")


def test_file_export_with_unsaved_changes_asks_to_save_first(harness, captured, tmp_path, monkeypatch):
    path = tmp_path / "farm.json"
    _bot().save(path)
    harness._open_bot(str(path))
    harness._loaded_config.name = "Renamed"
    harness._has_unsaved_changes = True
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)

    assert harness._export_open_document() is None
    assert captured.calls == []

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save)
    harness._export_open_document()
    assert captured.plan.name == "Renamed", "saved first, then exported"


def test_file_export_on_an_open_subgraph_exports_the_definition(harness, captured):
    harness._subgraph_library.save(_defn("sg", "Loop"))

    harness._export_subgraph("sg")

    assert captured.plan.share.main == share_file.ItemRef(kind=share_export.SUBGRAPH, id="sg")


def test_a_template_is_not_exported(harness, captured, tmp_path, monkeypatch):
    path = tmp_path / "draft.json"
    BotConfig.model_validate({**_bot().model_dump(), "template_meta": TemplateMeta()}).save(path)
    told: list = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a))

    assert harness._export_bot(str(path)) is None
    assert captured.calls == [] and told


def test_the_combat_tabs_export_rereads_the_strategy_and_offers_its_decks(harness, captured):
    bcs = BotCombatStrategy(name="Solo", bot_id="bot-1")
    save_bot_strategy(bcs)
    stale = bcs.model_copy(update={"name": "Stale"})

    harness._export_bot_strategy(stale)

    assert captured.calls[0][1] == {"offers_decks": True}
    assert captured.plan.name == "Solo"
    assert captured.plan.share.main == share_file.ItemRef(kind=share_export.BOT_COMBAT_STRATEGY, id=bcs.id)
