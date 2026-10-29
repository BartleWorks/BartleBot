"""The New Bot dialog and the controller path behind it (10.3).

Real widget tests (QApplication required). The dialog is never ``exec``'d: its pages are
driven through its own methods, and the controller tests swap it for a stand-in holding
the answers.
"""

from __future__ import annotations

import pytest
from bartleby.qt.theming.theme import THEME
from PyQt6.QtCore import QEvent, QPointF
from PyQt6.QtGui import QEnterEvent, QIcon
from PyQt6.QtWidgets import QDialog, QLabel, QMessageBox

from bartlebot import app_controller
from bartlebot.bot.schema import BotConfig, StateModel, TemplateMeta, TemplateRef
from bartlebot.content.new_bot import BotDetails, starting_points
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary
from bartlebot.content.templates.template_store import release, save_draft
from bartlebot.gui.canvas.bot_canvas import BotMode
from bartlebot.gui.dialogs import new_bot_dialog
from bartlebot.gui.dialogs.new_bot_dialog import NewBotDialog
from bartlebot.gui.widgets.pills.tag_pill import TagChip
from bartlebot.settings_manager import SettingsManager
from tests.content.subgraphs.test_subgraph_integration import ControllerHarness

TEMPLATE_ID = "tpl-boss"


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "store"
    draft = BotConfig(
        id=TEMPLATE_ID,
        name="Boss Farmer",
        initial="t_1",
        recovery_state="t_1",
        tags=["Gear", "Boss", "Team-Up"],
        states=[StateModel(id="t_1", node_type="quicksell", position={"x": 0.0, "y": 0.0})],
        transitions=[],
        template_meta=TemplateMeta(blurb="Farms one boss.\n\nThe long version."),
    )
    save_draft(draft, root)
    release(TEMPLATE_ID, "First release", root=root)
    return root


@pytest.fixture
def bots_dir(tmp_path):
    base = tmp_path / "bots"
    for name in ("02_krokotopia", "13_aquila"):
        (base / name).mkdir(parents=True)
    return base


@pytest.fixture
def dialog(qapp, store, bots_dir):
    return NewBotDialog(starting_points(store), bots_dir)


# ---------------------------------------------------------------------------
# Page one
# ---------------------------------------------------------------------------


def test_the_gallery_is_the_blank_bot_then_every_template(dialog):
    assert [c.name for c in dialog.cards] == ["Blank bot", "Boss Farmer"]
    assert [c.selected for c in dialog.cards] == [True, False]
    assert dialog.cards[1].toolTip() == "Farms one boss.\n\nThe long version."


def test_the_gallery_is_two_columns_wide(dialog):
    grid = dialog.cards[0].parentWidget().layout().itemAt(0).layout()

    assert [grid.getItemPosition(grid.indexOf(card))[:2] for card in dialog.cards] == [(0, 0), (0, 1)]


def test_the_blank_bot_wears_a_glyph_and_art_is_optional(qapp, store, bots_dir, monkeypatch):
    monkeypatch.setattr(new_bot_dialog, "tag_icon", lambda tag: None)

    blank, boss = NewBotDialog(starting_points(store), bots_dir).cards

    assert blank.has_icon
    assert not boss.has_icon


def test_a_template_card_wears_its_first_tags_medallion(qapp, store, bots_dir, monkeypatch):
    asked = []
    monkeypatch.setattr(new_bot_dialog, "tag_icon", lambda tag: asked.append(tag) or QIcon())

    _blank, boss = NewBotDialog(starting_points(store), bots_dir).cards

    assert asked == ["Gear"]
    assert boss.has_icon


def test_building_the_dialog_never_shows_a_parentless_widget(qapp, store, bots_dir, parentless_shows):
    """Each card's blurb was shown before its layout had a parent: one window flashed per card."""
    dialog = NewBotDialog(starting_points(store), bots_dir)
    dialog.select_start(1)
    dialog.advance()

    assert parentless_shows == []


def test_hovering_a_card_outlines_it_without_selecting_it(dialog):
    blank, boss = dialog.cards
    plain = boss.styleSheet()

    boss.enterEvent(QEnterEvent(QPointF(), QPointF(), QPointF()))

    assert boss.hovered and boss.outlined and not boss.selected
    assert THEME.accent in boss.styleSheet() and THEME.node_fill_selected not in boss.styleSheet()
    assert blank.selected and dialog.start is None

    boss.leaveEvent(QEvent(QEvent.Type.Leave))

    assert not boss.outlined
    assert boss.styleSheet() == plain


def test_hovering_the_selected_card_changes_nothing(dialog):
    blank = dialog.cards[0]
    before = blank.styleSheet()

    blank.enterEvent(QEnterEvent(QPointF(), QPointF(), QPointF()))

    assert blank.styleSheet() == before


def test_picking_a_card_picks_that_starting_point(dialog):
    dialog.select_start(1)

    assert dialog.start.template_id == TEMPLATE_ID
    assert [c.selected for c in dialog.cards] == [False, True]


def test_next_goes_to_the_details_and_create_waits_for_a_name(dialog):
    assert dialog.commit_button.text() == "Next" and dialog.commit_button.isEnabled()

    dialog.advance()

    assert dialog.page == 1
    assert dialog.commit_button.text() == "Create" and not dialog.commit_button.isEnabled()
    dialog.advance()
    assert dialog.result() != QDialog.DialogCode.Accepted

    dialog.name_edit.setText("Lamia")
    assert dialog.commit_button.isEnabled()
    dialog.advance()
    assert dialog.result() == QDialog.DialogCode.Accepted


# ---------------------------------------------------------------------------
# Page two
# ---------------------------------------------------------------------------


def test_a_template_fills_in_every_tag_it_carries(dialog):
    dialog.select_start(1)
    dialog.advance()

    assert dialog.tags_field.tags() == ["Gear", "Boss", "Team-Up"]
    assert dialog.details().tags == ["Gear", "Boss", "Team-Up"]


def test_a_blank_bot_starts_with_no_tags(dialog):
    dialog.advance()

    assert dialog.details().tags == []


def test_the_tags_are_chips_that_can_be_taken_off(dialog):
    dialog.select_start(1)
    dialog.advance()
    chips = dialog.tags_field.findChildren(TagChip)
    assert [c.tag for c in chips] == ["Gear", "Boss", "Team-Up"]

    chips[0].removed.emit("Gear")

    assert dialog.details().tags == ["Boss", "Team-Up"]


def test_the_details_are_what_was_typed(dialog):
    dialog.select_start(1)
    dialog.advance()
    dialog.name_edit.setText("  Lamia  ")
    dialog.description_edit.setPlainText("Farms Lamia.")
    dialog.tags_field.set_tags(["Jewels", "Boss"])

    assert dialog.details() == BotDetails(name="Lamia", description="Farms Lamia.", tags=["Jewels", "Boss"])


def test_no_field_carries_hint_text(dialog):
    assert dialog.name_edit.placeholderText() == ""
    assert dialog.description_edit.placeholderText() == ""
    assert dialog.authors_edit.placeholderText() == ""
    assert dialog.file_edit.placeholderText() == ""


def test_the_file_name_follows_the_display_name(dialog, bots_dir):
    dialog.advance()
    dialog.name_edit.setText("Lamia's Lair")

    assert dialog.file_edit.text() == "lamia-s-lair"
    assert dialog.target_path() == bots_dir / "02_krokotopia" / "lamia-s-lair.json"
    assert dialog.file_note == "Saved as 02_krokotopia/lamia-s-lair.json"


def test_a_clash_is_numbered_and_the_field_shows_the_name_used(dialog, bots_dir):
    (bots_dir / "13_aquila" / "lamia.json").write_text("{}", encoding="utf-8")
    dialog.advance()
    dialog.name_edit.setText("Lamia")
    assert dialog.file_edit.text() == "lamia"

    dialog.folder_picker.set_folder("13_aquila")
    dialog.folder_picker.selection_changed.emit(["13_aquila"])

    assert dialog.file_edit.text() == "lamia-2"
    assert dialog.target_path() == bots_dir / "13_aquila" / "lamia-2.json"


def test_a_file_name_the_user_typed_stops_following_the_display_name(dialog, bots_dir):
    (bots_dir / "02_krokotopia" / "Mine.json").write_text("{}", encoding="utf-8")
    dialog.advance()
    dialog.name_edit.setText("Lamia")
    dialog.edit_file_name("Mine")
    dialog.name_edit.setText("Lamia the Second")

    assert dialog.file_edit.text() == "Mine"
    assert dialog.target_path() == bots_dir / "02_krokotopia" / "Mine-2.json"
    assert "Mine-2.json" in dialog.file_note


def test_the_file_name_is_required(dialog):
    dialog.advance()
    dialog.name_edit.setText("Lamia")
    assert dialog.commit_button.isEnabled()

    dialog.edit_file_name("")

    assert not dialog.commit_button.isEnabled()
    assert dialog.file_note == ""
    dialog.advance()
    assert dialog.result() != QDialog.DialogCode.Accepted

    dialog.edit_file_name("lamia")
    assert dialog.commit_button.isEnabled()


def test_the_two_required_fields_are_marked(dialog):
    marked = [label.text() for label in dialog.findChildren(QLabel) if "*</span>" in label.text()]

    assert [text.split(" <span")[0] for text in marked] == ["Display name", "File name"]


def test_the_folders_are_the_librarys_and_open_on_the_first(dialog):
    assert dialog.folder_picker.folders == ["02_krokotopia", "13_aquila"]
    assert dialog.folder_picker.folder() == "02_krokotopia"
    assert dialog.folder_picker.text() == "Krokotopia"


def test_the_folder_opens_on_the_one_being_looked_at(qapp, store, bots_dir):
    dialog = NewBotDialog(starting_points(store), bots_dir, initial_folder="13_aquila")

    assert dialog.folder_picker.folder() == "13_aquila"


def test_a_folder_the_library_does_not_have_falls_back_to_the_first(qapp, store, bots_dir):
    dialog = NewBotDialog(starting_points(store), bots_dir, initial_folder="99_nowhere")

    assert dialog.folder_picker.folder() == "02_krokotopia"


def test_a_library_with_no_folders_saves_into_the_library_itself(qapp, tmp_path):
    dialog = NewBotDialog([], tmp_path / "bots")
    dialog.advance()
    dialog.name_edit.setText("Lamia")

    assert dialog.target_path() == tmp_path / "bots" / "lamia.json"
    assert not dialog.folder_picker.isEnabled()


def test_going_back_and_forward_keeps_an_edited_tag_selection(dialog):
    dialog.select_start(1)
    dialog.advance()
    dialog.tags_field.set_tags(["Boss"])

    dialog.select_start(1)
    dialog.advance()

    assert dialog.tags_field.tags() == ["Boss"]


# ---------------------------------------------------------------------------
# The controller
# ---------------------------------------------------------------------------


class _Answers:
    """Stands in for the dialog: Create, with these answers."""

    def __init__(self, start, details, path, accepted=True) -> None:
        self.start = start
        self._details = details
        self._path = path
        self._accepted = accepted

    def exec(self):
        return QDialog.DialogCode.Accepted if self._accepted else QDialog.DialogCode.Rejected

    def details(self):
        return self._details

    def target_path(self):
        return self._path


@pytest.fixture
def harness(qapp, store, tmp_path, monkeypatch):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.No)
    library = SubgraphLibrary(tmp_path / "library")
    canvas = BotCanvas()
    canvas.set_subgraph_library(library)
    h = ControllerHarness(canvas, library)
    h._templates_root = store
    yield h
    canvas.prepare_for_shutdown()


def _answer(monkeypatch, *args, **kwargs) -> None:
    monkeypatch.setattr(app_controller, "NewBotDialog", lambda *a, **k: _Answers(*args, **kwargs))


def test_create_saves_the_bot_at_once_and_opens_it_for_editing(harness, store, tmp_path, monkeypatch):
    path = tmp_path / "bots" / "13_aquila" / "lamia.json"
    _answer(monkeypatch, starting_points(store)[0], BotDetails(name="Lamia", tags=["Boss"]), path)

    assert harness._new_bot()

    on_disk = BotConfig.load(path)
    assert on_disk.template == TemplateRef(id=TEMPLATE_ID, version=1)
    assert on_disk.template_meta is None and on_disk.id != TEMPLATE_ID
    assert (on_disk.name, on_disk.tags) == ("Lamia", ["Boss"])
    assert harness._loaded_config.id == on_disk.id
    assert harness._last_save_path == str(path)
    assert not harness._has_unsaved_changes
    assert harness._window.bot_canvas.mode is BotMode.EDIT


def test_saving_a_bot_just_created_changes_nothing_in_its_file(harness, store, tmp_path, monkeypatch):
    path = tmp_path / "bots" / "lamia.json"
    _answer(monkeypatch, starting_points(store)[0], BotDetails(name="Lamia"), path)
    harness._new_bot()
    written = path.read_bytes()

    assert harness._save_bot()

    assert path.read_bytes() == written


def test_a_blank_bot_is_saved_with_no_template(harness, tmp_path, monkeypatch):
    path = tmp_path / "bots" / "scratch.json"
    _answer(monkeypatch, None, BotDetails(name="Scratch"), path)

    assert harness._new_bot()

    on_disk = BotConfig.load(path)
    assert on_disk.template is None
    assert on_disk.states == [], "a blank bot is an empty canvas"
    assert on_disk.id


def test_cancelling_creates_nothing(harness, tmp_path, monkeypatch):
    path = tmp_path / "bots" / "lamia.json"
    _answer(monkeypatch, None, BotDetails(name="Lamia"), path, accepted=False)

    assert not harness._new_bot()

    assert not path.exists()
    assert harness._loaded_config is None


def test_a_bot_that_cannot_be_written_is_not_opened(harness, tmp_path, monkeypatch):
    blocker = tmp_path / "bots"
    blocker.write_text("a file where the library folder should be", encoding="utf-8")
    _answer(monkeypatch, None, BotDetails(name="Lamia"), blocker / "lamia.json")

    assert not harness._new_bot()

    assert harness._loaded_config is None


def test_new_bot_opens_on_the_open_bots_folder(harness, tmp_path, monkeypatch):
    bots_dir = tmp_path / "bots"
    SettingsManager.instance().set("canvas", "bot_library_path", value=str(bots_dir))
    path = bots_dir / "13_aquila" / "lamia.json"
    _answer(monkeypatch, None, BotDetails(name="Lamia"), path)
    harness._new_bot()

    assert harness._new_bot_folder() == "13_aquila"


def test_a_bot_outside_the_library_gives_new_bot_no_folder(harness, tmp_path, monkeypatch):
    SettingsManager.instance().set("canvas", "bot_library_path", value=str(tmp_path / "bots"))
    _answer(monkeypatch, None, BotDetails(name="Lamia"), tmp_path / "elsewhere" / "13_aquila" / "lamia.json")
    harness._new_bot()

    assert harness._new_bot_folder() is None


def test_new_bot_opens_on_no_folder_with_nothing_to_go_by(harness):
    assert harness._new_bot_folder() is None


def test_the_library_panel_names_the_folder_it_is_showing(qapp):
    from bartlebot.gui.canvas.bot_canvas import _BotLibraryPanel  # noqa: PLC0415

    panel = _BotLibraryPanel()
    panel.add_category_row("\0all", "All")
    panel.add_category_row("13_aquila", "Aquila")

    assert panel.current_folder() is None
    panel._cat_list.setCurrentRow(0)
    assert panel.current_folder() is None
    panel._cat_list.setCurrentRow(1)
    assert panel.current_folder() == "13_aquila"


def test_authors_start_blank_whatever_the_starting_point_and_are_what_was_typed(dialog):
    dialog.select_start(1)
    dialog.advance()
    assert dialog.authors_edit.text() == "" and dialog.details().authors == ""

    dialog.authors_edit.setText("  Luke SkullHunter ")

    assert dialog.details().authors == "Luke SkullHunter"
    assert dialog.is_complete() is False, "authors are optional, and fill nothing that is required"


def test_an_emptied_file_name_stays_empty_until_the_display_name_changes(dialog):
    """So it can be retyped; then it follows the name again, as it did before it was touched."""
    dialog.select_start(0)
    dialog.advance()
    dialog.name_edit.setText("Lamia")
    assert dialog.file_edit.text() == "lamia"

    dialog.edit_file_name("")

    assert dialog.file_edit.text() == "" and not dialog.is_complete()

    dialog.name_edit.setText("Lamia's Lair")

    assert dialog.file_edit.text() == "lamia-s-lair" and dialog.is_complete()
    dialog.name_edit.setText("Medusa")
    assert dialog.file_edit.text() == "medusa", "and goes on following"


def test_a_typed_file_name_is_never_replaced_by_the_display_name(dialog):
    dialog.select_start(0)
    dialog.advance()
    dialog.name_edit.setText("Lamia")
    dialog.edit_file_name("my-boss")

    dialog.name_edit.setText("Medusa")

    assert dialog.file_edit.text() == "my-boss"


def test_a_dialog_opened_after_an_extraction_wears_the_new_art(qapp, store, bots_dir):
    """Built afresh on each open, so it needs no art_changed connection (#239)."""
    from tests.extracted_art import write_extracted_art  # noqa: PLC0415

    assert not NewBotDialog(starting_points(store), bots_dir).cards[1].has_icon

    write_extracted_art(icons=["tag_gear"])

    assert NewBotDialog(starting_points(store), bots_dir).cards[1].has_icon
