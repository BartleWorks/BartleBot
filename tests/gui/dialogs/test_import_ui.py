"""Import's ways in, the import summary, and the controller behind them (#171).

What an import matches and writes is :mod:`tests.content.test_share_import`; this is how a user
gets a file in. Nothing here is a real character.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import QDialog, QLineEdit, QMessageBox

from bartlebot.combat.strategy_store import StrategyKind
from bartlebot.content import bot_roots, share_export, share_import
from bartlebot.content.share_export import BOT, SUBGRAPH
from bartlebot.content.share_import import Action
from bartlebot.content.subgraphs.subgraph_store import load_subgraph
from bartlebot.gui.dialogs import import_dialog
from bartlebot.gui.dialogs.import_dialog import CHOICES_HEADING, COPIES_NOTE, REST_HEADING, ImportDialog, confirm_import
from bartlebot.gui.share_drop import ShareFileDrops, is_share_file, share_paths
from bartlebot.settings_manager import SettingsManager
from bartlebot.shared.strategy_models import PlayerCombatStrategy
from tests.content.subgraphs.test_subgraph_integration import ControllerHarness
from tests.content.subgraphs.test_subgraph_store import _defn
from tests.content.test_share_import import WORLD, Library, _bot, _combat, _export, _send_bot, _sender_setup


@pytest.fixture
def sender(tmp_path) -> Library:
    return Library(tmp_path / "sender")


@pytest.fixture
def receiver(tmp_path) -> Library:
    return Library(tmp_path / "receiver")


def _build(receiver: Library, path: Path):
    share = share_import.read(path)
    sources = receiver.sources()
    incoming = share_import.check(share, sources)
    return lambda choices: share_import.plan_import(share, sources, choices, incoming=incoming)


def _conflicting_file(sender: Library, receiver: Library, tmp_path: Path) -> Path:
    sender.subgraph(_defn("sg", "Their Loop"))
    receiver.subgraph(_defn("sg", "My Loop"))
    bot = _bot("bot-1", "Boss Farmer", "sg")
    sender.bot(bot)
    return _send_bot(sender, tmp_path, bot)


# ---------------------------------------------------------------------------
# The summary
# ---------------------------------------------------------------------------


def test_with_nothing_to_decide_the_summary_is_one_line_and_one_button(qapp, sender, receiver, tmp_path):
    bot, bcs = _sender_setup(sender)
    dialog = ImportDialog(_build(receiver, _send_bot(sender, tmp_path, bot, bcs)))

    assert dialog.headline_text.startswith("Import 'Boss Farmer', with ")
    assert dialog.listed() == [], "no rows"
    assert dialog.commit_button.text() == "Import" and dialog.commit_button.isEnabled()
    assert dialog.findChildren(QLineEdit) == [], "nothing is typed: no id, no name"


def test_a_conflict_opens_the_rows_that_need_a_choice_with_their_defaults_set(qapp, sender, receiver, tmp_path):
    dialog = ImportDialog(_build(receiver, _conflicting_file(sender, receiver, tmp_path)))

    assert dialog.listed() == [(CHOICES_HEADING, ["Their Loop"]), (REST_HEADING, ["Boss Farmer"])]
    combo = dialog.combo(SUBGRAPH, "sg")
    assert [combo.itemText(i) for i in range(combo.count())] == ["Import as copy", "Replace mine", "Keep mine"]
    assert combo.currentData() is Action.COPY
    assert dialog.combo(BOT, "bot-1") is None, "a new bot has nothing to choose"
    assert dialog.findChildren(QLineEdit) == []


def test_changing_a_choice_rematches_the_file(qapp, sender, receiver, tmp_path):
    dialog = ImportDialog(_build(receiver, _conflicting_file(sender, receiver, tmp_path)))
    copy = dialog.plan.row(SUBGRAPH, "sg").target_id
    assert dialog.plan.row(BOT, "bot-1").item.regions[0].source.subgraph_id == copy

    dialog.combo(SUBGRAPH, "sg").setCurrentIndex(1)  # Replace mine

    assert dialog.plan.row(SUBGRAPH, "sg").action is Action.REPLACE
    assert dialog.plan.row(BOT, "bot-1").item.regions[0].source.subgraph_id == "sg"
    assert dialog.combo(SUBGRAPH, "sg").currentData() is Action.REPLACE, "the choice survives the rebuild"


def test_changes_to_official_items_are_said_to_come_in_as_copies(qapp, sender, receiver, tmp_path):
    receiver.ship_subgraph(_defn("sg", "Sync Players"))
    sender.subgraph(_defn("sg", "Sync Players, theirs"))
    bot = _bot("bot-1", "Boss Farmer", "sg")
    sender.bot(bot)

    dialog = ImportDialog(_build(receiver, _send_bot(sender, tmp_path, bot)))

    assert dialog.listed() == []
    assert dialog.copies_text == COPIES_NOTE + "'Sync Players, theirs'."


def test_a_file_with_nothing_new_says_so_and_writes_nothing(qapp, sender, receiver, tmp_path, monkeypatch):
    bot, _ = _sender_setup(sender)
    path = _send_bot(sender, tmp_path, bot)
    share_import.apply_import(_build(receiver, path)({}), receiver.sources())
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda _p, _t, text: told.append(text))
    monkeypatch.setattr(ImportDialog, "exec", lambda self: pytest.fail("no summary for nothing to do"))

    assert confirm_import(None, _build(receiver, path)) is None
    assert told == ["You already have 'Boss Farmer' and everything that comes with it."]


def test_cancel_returns_no_plan(qapp, sender, receiver, tmp_path, monkeypatch):
    bot, _ = _sender_setup(sender)
    monkeypatch.setattr(ImportDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert confirm_import(None, _build(receiver, _send_bot(sender, tmp_path, bot))) is None


def test_the_open_dialog_offers_every_kind_of_share_file():
    text = import_dialog.file_filter()
    for ext in share_export.EXTENSIONS.values():
        assert f"*{ext}" in text.split(";;")[0]


# ---------------------------------------------------------------------------
# Dropping a file
# ---------------------------------------------------------------------------


def _mime(*paths: str) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    return mime


def test_only_share_files_are_taken_from_a_drag(tmp_path):
    bot, other = str(tmp_path / "farm.bbot"), str(tmp_path / "farm.json")
    assert [Path(p) for p in share_paths(_mime(bot, other))] == [Path(bot)]
    assert share_paths(_mime(other)) == []
    assert share_paths(QMimeData()) == []
    assert is_share_file("x.BBDECK") and not is_share_file("x.json")


def test_a_share_file_dropped_anywhere_is_taken_and_any_other_drag_goes_on(qapp, tmp_path):
    from PyQt6.QtWidgets import QWidget  # noqa: PLC0415

    target = QWidget()
    drops = ShareFileDrops()
    dropped: list[list[str]] = []
    drops.files_dropped.connect(dropped.append)
    path = str(tmp_path / "farm.bbot")

    def event(cls, mime):
        pos = QPoint(1, 1) if cls is QDragEnterEvent else QPointF(1, 1)
        return cls(pos, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

    assert drops.eventFilter(target, event(QDragEnterEvent, _mime(path))) is True
    assert drops.eventFilter(target, event(QDropEvent, _mime(path))) is True
    assert dropped == [], "the Drop returns before anything is imported (#178)"
    qapp.processEvents()
    assert [[Path(p) for p in paths] for paths in dropped] == [[Path(path)]]
    assert drops.eventFilter(target, event(QDropEvent, _mime(str(tmp_path / "farm.json")))) is False
    assert drops.eventFilter(target, QEvent(QEvent.Type.Show)) is False


def test_the_file_menu_has_import_and_the_window_takes_drops(qapp):
    from bartlebot.gui.main_window import MainWindow  # noqa: PLC0415

    window = MainWindow()
    try:
        assert window.action_import.text() == "Import…"
        assert window.action_import.isEnabled(), "whatever is open, or nothing"
        assert window.acceptDrops()
    finally:
        window.bot_canvas.prepare_for_shutdown()


# ---------------------------------------------------------------------------
# The controller
# ---------------------------------------------------------------------------

_IMPORT_METHODS = (
    "_import_from_arguments",
    "_import_dropped",
    "_import_share_file",
    "_import_blocked_by_run",
    "_open_document_ids",
    "_clear_for_import",
    "_after_import",
    "_select_imported",
)


@pytest.fixture
def harness(qapp, tmp_path, receiver, monkeypatch):
    from unittest.mock import MagicMock  # noqa: PLC0415

    from bartlebot.app_controller import AppController  # noqa: PLC0415
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    monkeypatch.setattr(bot_roots, "shipped_bots_dir", lambda: receiver.shipped / "bots")
    monkeypatch.setattr(bot_roots, "user_bots_dir", lambda configured=None: receiver.user / "bots")
    sources = receiver.sources()
    canvas = BotCanvas()
    canvas.set_subgraph_library(sources.subgraphs)
    h = ControllerHarness(canvas, sources.subgraphs)
    for name in _IMPORT_METHODS:
        setattr(h, name, getattr(AppController, name).__get__(h))
    h._export_sources = lambda: receiver.sources()
    h._app = MagicMock()
    # Revealing an item with a document open opens the modal library dialog: recorded instead.
    h.library_dialogs = []
    monkeypatch.setattr(canvas, "open_library_dialog", lambda **kw: h.library_dialogs.append(kw))
    h.warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, text: h.warnings.append(text))
    monkeypatch.setattr(QMessageBox, "information", lambda _p, _t, text: h.warnings.append(text))
    # The summary is accepted as it opens: what it would show is tested above.
    monkeypatch.setattr("bartlebot.app_controller.confirm_import", lambda _parent, build: build({}))
    yield h
    canvas.prepare_for_shutdown()


def test_an_imported_bot_is_written_selected_in_the_library_and_its_strategy_picked(harness, sender, tmp_path):
    bot, bcs = _sender_setup(sender)

    result = harness._import_share_file(str(_send_bot(sender, tmp_path, bot, bcs)))

    path = result.bot_paths["bot-1"]
    assert path.parent.name == WORLD
    card = harness._window.bot_canvas.library_panel.selected_card()
    assert card is not None and card.selected, "the library selects the main item"
    assert SettingsManager.instance().get_active_strategy("bot-1") == "bcs-1"


def test_a_refused_file_says_why_and_writes_nothing(harness, receiver, tmp_path):
    path = tmp_path / "broken.bbot"
    path.write_text("not json", encoding="utf-8")
    before = receiver.files()

    assert harness._import_share_file(str(path)) is None

    assert harness.warnings == [share_import.NOT_A_SHARE_FILE]
    assert receiver.files() == before


def test_a_subgraph_import_selects_it_in_the_subgraphs_folder(harness, sender, receiver, tmp_path):
    defn = _defn("sg", "Loop")
    sender.subgraph(defn)

    harness._import_share_file(str(_export(share_export.export_subgraph(defn, sender.sources()), tmp_path)))

    assert load_subgraph("sg", receiver.shipped, receiver.user).name == "Loop"
    card = harness._window.bot_canvas.library_panel.selected_card()
    assert card is not None and card.source_text == "Custom"


def test_a_strategy_import_opens_the_strategy_library_on_it(harness, sender, tmp_path):
    pcs = PlayerCombatStrategy(id="p1", name="Hitter")
    path = _export(share_export.export_player(pcs, sender.sources()), tmp_path)

    harness._import_share_file(str(path))

    harness._window.left_sidebar.combat_tab.open_combat_dialog.assert_called_once()
    args, kwargs = harness._window.left_sidebar.combat_tab.open_combat_dialog.call_args
    assert args[0] is StrategyKind.PLAYER and kwargs == {"current_id": "p1"}


def test_an_import_replacing_the_open_bot_with_unsaved_changes_writes_nothing(harness, sender, receiver, tmp_path):
    mine = receiver.bot(_bot("bot-1", "Boss Farmer"))
    harness._open_bot(str(mine))
    harness._has_unsaved_changes = True
    harness._clean_snapshot = None
    sender.bot(_bot("bot-1", "Boss Farmer, theirs"))
    path = _send_bot(sender, tmp_path, _bot("bot-1", "Boss Farmer, theirs"))
    before = mine.read_bytes()
    plan = share_import.plan_import(share_import.read(path), receiver.sources(), {(BOT, "bot-1"): Action.REPLACE})

    assert harness._clear_for_import(plan) is False
    assert "unsaved changes" in harness.warnings[-1]
    assert mine.read_bytes() == before


def test_an_import_replacing_the_open_bot_reopens_it(harness, sender, receiver, tmp_path, monkeypatch):
    mine = receiver.bot(_bot("bot-1", "Boss Farmer"))
    harness._open_bot(str(mine))
    theirs = _bot("bot-1", "Boss Farmer, theirs")
    sender.bot(theirs)
    monkeypatch.setattr(
        "bartlebot.app_controller.confirm_import", lambda _parent, build: build({(BOT, "bot-1"): Action.REPLACE})
    )

    harness._import_share_file(str(_send_bot(sender, tmp_path, theirs)))

    assert harness._loaded_config.name == "Boss Farmer, theirs"
    assert Path(harness._last_save_path) == mine
    assert harness.library_dialogs == [], "the bot is in front of the user already"


def test_a_bot_imported_with_another_open_opens_the_library_on_it(harness, sender, receiver, tmp_path):
    harness._open_bot(str(receiver.bot(_bot("bot-mine", "Mine"), "mine")))
    bot, _ = _sender_setup(sender)

    result = harness._import_share_file(str(_send_bot(sender, tmp_path, bot)))

    assert harness.library_dialogs == [{"reveal": (result.bot_paths["bot-1"], None)}]


def test_files_named_at_launch_are_imported_and_nothing_else(harness, sender, tmp_path):
    bot, _ = _sender_setup(sender)
    path = _send_bot(sender, tmp_path, bot)
    harness._app.arguments.return_value = ["bartlebot.py", str(tmp_path / "missing.bbot"), "--flag", str(path)]
    imported: list[str] = []
    harness._import_share_file = imported.append

    harness._import_from_arguments()

    assert imported == [str(path)]


def test_a_combat_strategy_for_the_open_bot_rebinds_the_combat_tab(harness, sender, receiver, tmp_path):
    bot = _bot()
    receiver_path = receiver.bot(bot)
    harness._open_bot(str(receiver_path))
    bcs = _combat()
    bcs.state_assignments = []
    sender.bot(bot)
    plan = share_export.export_bot_strategy(bcs, sender.sources())

    harness._import_share_file(str(_export(plan, tmp_path)))

    harness._window.left_sidebar.rebind_identity.assert_called()
    assert SettingsManager.instance().get_active_strategy("bot-1") == "bcs-1"


def test_no_way_in_imports_while_a_bot_runs(harness, sender, receiver, tmp_path, monkeypatch):
    bot, bcs = _sender_setup(sender)
    path = str(_send_bot(sender, tmp_path, bot, bcs))
    harness._current_machine = object()
    monkeypatch.setattr(
        "bartlebot.app_controller.ask_for_file", lambda _w: pytest.fail("File › Import… must not ask for a file")
    )
    monkeypatch.setattr(
        "bartlebot.app_controller.confirm_import", lambda _p, _b: pytest.fail("no summary during a run")
    )
    before = receiver.files()

    assert harness._import_share_file(path) is None
    assert harness._import_share_file() is None
    harness._import_dropped([path, path])
    harness._app.arguments.return_value = ["bartlebot", path]
    harness._import_from_arguments()

    assert len(harness.warnings) == 4
    assert all("Stop the running bot" in w for w in harness.warnings)
    assert receiver.files() == before
