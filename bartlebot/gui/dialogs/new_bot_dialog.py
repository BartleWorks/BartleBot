"""New Bot: pick a starting point, then name the bot (#115).

Two pages. The first is a gallery: a Blank bot card, then one card per template, built
from its latest released version. The second asks for what the bot is called and where
its file goes. Create hands both back to the controller, which builds the bot, saves it
at once and opens it in Edit mode.

Everything this decides is in :mod:`bartlebot.content.new_bot`, which has no Qt in it: what a
starting point is, the bot it makes, and the file name rule. The dialog only collects
the answers, so its tests are about the form and that module's are about the result.

The fields carry no hint text: they are a name, a description, authors, tags, a folder
and a file name, and the two that must be filled say so with the red asterisk the
Properties panel puts on a required param. Authors starts blank whatever the starting
point is, since a template's authors are not the new bot's.

The file name follows the display name as it is typed, until the user edits it; from
then on it is theirs. Emptying the field hands it back: it stays empty, so a name can be
retyped, and follows again the next time the display name changes. Either way the line
under it says the name that will be written, which differs from the field when a file of
that name already exists.
"""

from __future__ import annotations

from pathlib import Path

from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin
from bartleby.qt.widgets.buttons.action_button import ActionButton
from bartleby.qt.widgets.cards.start_card import StartCard
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from bartleby.qt.widgets.layout.dialog_footer import dialog_footer
from bartleby.qt.widgets.pickers.picker_popup import PickerButton, PickerRow
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from bartlebot.content.bot_folders import folder_badge, folder_caption
from bartlebot.content.new_bot import (
    BOT_FILE_SUFFIX,
    BotDetails,
    StartingPoint,
    clean_stem,
    file_stem,
    library_folders,
    unique_file_name,
)
from bartlebot.game import card_art
from bartlebot.gui.widgets.cards.card_art_tile import art_icon
from bartlebot.gui.widgets.inputs.growing_text_edit import GrowingTextEdit
from bartlebot.gui.widgets.pickers.tag_picker import tag_icon
from bartlebot.gui.widgets.pills.source_pill import source_pill
from bartlebot.gui.widgets.pills.tags_field import TagsField

_BLANK_NAME = "Blank bot"
_BLANK_BLURB = "An empty canvas. Build the graph yourself."
_BLANK_GLYPH = "plus"

_PAGE_START = 0
_PAGE_DETAILS = 1

#: The gallery is a grid this wide, so seven cards are four rows and fit without scrolling.
_GALLERY_COLUMNS = 2


class _RequiredLabel(ThemedMixin, QLabel):
    """A form label ending in the red asterisk a required field carries.

    The asterisk's color is in the label's own text, so a theme switch rewrites the text.
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self.setText(f"{self._text} <span style='color:{THEME.error}'>*</span>")


class FolderPicker(PickerButton):
    """Which library folder the bot is saved in: the app's own picker, one choice at a time.

    Not a combo box: the library has thirty-five folders, and the platform style opens
    a combo's list at its full height. This one scrolls, filters as you type, and wears
    each folder's badge, as the library's own category list does.
    """

    def __init__(self, folders: list[str], parent: QWidget | None = None) -> None:
        super().__init__(
            "Library folder",
            placeholder="Choose a folder…",
            search_placeholder="Filter folders…",
            single_select=True,
            parent=parent,
        )
        self._folders = list(folders)
        rows = [PickerRow(key=name, label=folder_caption(name), icon=_badge_icon(name)) for name in folders]
        self.set_sections([("Folders", rows)])
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.selection_changed.connect(lambda _keys: self._show_badge())

    @property
    def folders(self) -> list[str]:
        return list(self._folders)

    def folder(self) -> str:
        """The folder's name on disk, or an empty string when none is chosen."""
        return next(iter(self.selection()), "")

    def set_folder(self, name: str) -> None:
        self.set_selection([name] if name in self._folders else [])
        self._show_badge()

    def _show_badge(self) -> None:
        """Wear the chosen folder's badge beside its caption, when there is art for one."""
        self.setIcon(_badge_icon(self.folder()) or QIcon())


def _badge_icon(folder: str) -> QIcon | None:
    badge = folder_badge(folder)
    path = card_art.badge_icon_path(badge) if badge is not None else None
    return art_icon(path) if path is not None else None


class NewBotDialog(QDialog):
    """The New Bot dialog. Accepted means Create: read :attr:`start`, :meth:`details` and :meth:`target_path`.

    ``initial_folder`` is the library folder to open on, by its name on disk: the one
    the user is looking at. Anything the library does not have falls back to the first.
    """

    def __init__(
        self,
        points: list[StartingPoint],
        bots_dir: str | Path,
        parent: QWidget | None = None,
        *,
        initial_folder: str | None = None,
        folder_sources: tuple[str | Path, ...] = (),
    ) -> None:
        """``bots_dir`` is where the bot is written: the user's own folder. ``folder_sources``
        are other folders whose world folders are offered too, the shipped one's above all."""
        super().__init__(parent)
        self.setWindowTitle("New Bot")
        self.setMinimumSize(600, 460)
        # Wide enough that a card's blurb is two or three lines, so the gallery fits.
        self.resize(720, 520)
        self._bots_dir = Path(bots_dir)
        self._folder_sources = tuple(Path(d) for d in folder_sources)
        #: Parallel to the cards: ``None`` is the blank bot.
        self._points: list[StartingPoint | None] = [None, *points]
        self._start_index = 0
        #: The card whose tags the field was last filled from, so Back and Next keep an edit.
        self._tags_from: int | None = None
        #: Whether the file name still follows the display name.
        self._file_follows_name = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self._heading = QLabel()
        self._heading.setFont(THEME.ui_font(THEME.font_heading_px, bold=True))
        layout.addWidget(self._heading)
        self._pages = QStackedWidget()
        self._pages.addWidget(self._build_start_page(points))
        self._pages.addWidget(self._build_details_page(initial_folder))
        layout.addWidget(self._pages, 1)

        self._back = ActionButton("arrow_left", "Back", fill=False)
        self._back.clicked.connect(lambda: self._show_page(_PAGE_START))
        footer = dialog_footer(
            self, commit_text="Next", commit_icon="arrow_right", on_commit=self._advance, extras=[self._back]
        )
        self._commit = footer.commit
        layout.addLayout(footer.layout)

        self._select(0)
        self._show_page(_PAGE_START)

    # ------------------------------------------------------------------
    # What Create hands back
    # ------------------------------------------------------------------

    @property
    def start(self) -> StartingPoint | None:
        """The starting point picked: ``None`` for a blank bot."""
        return self._points[self._start_index]

    def details(self) -> BotDetails:
        return BotDetails(
            name=self._name.text().strip(),
            description=self._description.toPlainText().strip(),
            tags=self._tags.tags(),
            authors=self._authors.text().strip(),
        )

    def folder(self) -> Path:
        """The folder the bot is saved in: the library's own when it has no folders."""
        name = self._folder.folder()
        return self._bots_dir / name if name else self._bots_dir

    def file_name(self) -> str:
        """The name the file will actually get, past any already in the folder."""
        stem = file_stem(self._name.text()) if self._file_follows_name else clean_stem(self._file.text())
        return unique_file_name(stem, self.folder())

    def target_path(self) -> Path:
        return self.folder() / self.file_name()

    def is_complete(self) -> bool:
        """Whether both required fields are filled: the display name and the file name."""
        return bool(self._name.text().strip()) and bool(self._file.text().strip())

    # ------------------------------------------------------------------
    # For the tests, and for nothing else to need a private attribute
    # ------------------------------------------------------------------

    @property
    def cards(self) -> list[StartCard]:
        return list(self._cards)

    @property
    def page(self) -> int:
        return self._pages.currentIndex()

    @property
    def commit_button(self) -> ActionButton:
        return self._commit

    @property
    def name_edit(self) -> QLineEdit:
        return self._name

    @property
    def description_edit(self) -> GrowingTextEdit:
        return self._description

    @property
    def authors_edit(self) -> QLineEdit:
        return self._authors

    @property
    def tags_field(self) -> TagsField:
        return self._tags

    @property
    def file_edit(self) -> QLineEdit:
        return self._file

    @property
    def folder_picker(self) -> FolderPicker:
        return self._folder

    @property
    def file_note(self) -> str:
        return self._file_note.text()

    def select_start(self, index: int) -> None:
        """Pick the card at ``index``: 0 is the blank bot."""
        self._select(index)

    def advance(self) -> None:
        """Next on page one, Create on page two."""
        self._advance()

    def edit_file_name(self, text: str) -> None:
        """Type ``text`` into the file name field, as the user would."""
        self._file.setText(text)
        self._on_file_edited(text)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_start_page(self, points: list[StartingPoint]) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self._cards: list[StartCard] = [StartCard(_BLANK_NAME, _BLANK_BLURB, glyph=_BLANK_GLYPH)]
        for point in points:
            full = point.blurb.strip()
            icon = tag_icon(point.icon_tag) if point.icon_tag else None
            self._cards.append(
                StartCard(
                    point.name,
                    point.summary,
                    tooltip=full if full != point.summary else "",
                    icon=icon,
                    pill=source_pill(point.origin),
                )
            )
        for index, card in enumerate(self._cards):
            card.chosen.connect(lambda i=index: self._select(i))
            card.activated.connect(self._advance)
            grid.addWidget(card, index // _GALLERY_COLUMNS, index % _GALLERY_COLUMNS)
        for column in range(_GALLERY_COLUMNS):
            grid.setColumnStretch(column, 1)
        outer.addLayout(grid)
        outer.addStretch()
        scroll.setWidget(host)
        return scroll

    def _build_details_page(self, initial_folder: str | None) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._name = QLineEdit()
        self._name.textChanged.connect(self._on_name_changed)
        form.addRow(_RequiredLabel("Display name"), self._name)

        self._description = GrowingTextEdit(min_lines=2, max_lines=5)
        self._description.setTabChangesFocus(True)
        form.addRow("Description", self._description)

        # Blank whatever the starting point: a template's authors wrote the template.
        self._authors = QLineEdit()
        form.addRow("Authors", self._authors)

        self._tags = TagsField()
        self._tags.set_editable(True)
        form.addRow("Tags", self._tags)

        folders = library_folders(self._bots_dir, *self._folder_sources)
        self._folder = FolderPicker(folders)
        if folders:
            self._folder.set_folder(initial_folder if initial_folder in folders else folders[0])
        else:
            # Nothing to choose between: the bot goes in the library's own directory.
            self._folder.setText(self._bots_dir.name)
            self._folder.setEnabled(False)
        self._folder.selection_changed.connect(lambda _keys: self._refresh_file())
        form.addRow("Library folder", self._folder)

        file_row = QHBoxLayout()
        file_row.setSpacing(4)
        self._file = QLineEdit()
        self._file.textEdited.connect(self._on_file_edited)
        file_row.addWidget(self._file, 1)
        file_row.addWidget(ThemedTextLabel(BOT_FILE_SUFFIX))
        form.addRow(_RequiredLabel("File name"), file_row)
        self._file_note = ThemedTextLabel(css=f"font-size: {THEME.font_small_px}px;")
        form.addRow("", self._file_note)
        return page

    # ------------------------------------------------------------------
    # Behavior
    # ------------------------------------------------------------------

    def _select(self, index: int) -> None:
        self._start_index = index
        for i, card in enumerate(self._cards):
            card.set_selected(i == index)

    def _show_page(self, page: int) -> None:
        self._pages.setCurrentIndex(page)
        on_details = page == _PAGE_DETAILS
        self._back.setVisible(on_details)
        self._commit.setText("Create" if on_details else "Next")
        self._commit.set_icon_name("plus" if on_details else "arrow_right")
        if on_details:
            start = self.start
            self._heading.setText("New blank bot" if start is None else f"New bot from {start.name}")
            self._name.setFocus()
        else:
            self._heading.setText("Start from")
        self._refresh_commit()

    def _advance(self) -> None:
        if self._pages.currentIndex() == _PAGE_START:
            self._apply_start_tags()
            self._show_page(_PAGE_DETAILS)
        elif self.is_complete():
            self.accept()

    def _apply_start_tags(self) -> None:
        """Fill in the starting point's tags, unless this card already did and they were edited since."""
        if self._tags_from == self._start_index:
            return
        self._tags_from = self._start_index
        self._tags.set_tags(list(self.start.config.tags) if self.start is not None else [])

    def _on_name_changed(self, text: str) -> None:
        if not self._file.text().strip():
            # Emptied by hand, and left empty until now so it could be retyped. A change
            # to the display name is when it is filled in again; a typed name never is.
            self._file_follows_name = True
        if self._file_follows_name:
            self._file.setText(file_stem(text) if text.strip() else "")
        self._refresh_file()
        self._refresh_commit()

    def _on_file_edited(self, _text: str) -> None:
        # From the first keystroke the name is the user's, an empty one included:
        # the field is required, so emptying it holds Create back.
        self._file_follows_name = False
        self._refresh_file()
        self._refresh_commit()

    def _refresh_file(self) -> None:
        """Show the generated name in the field, and say under it the name that will be written."""
        if not self._file.text().strip() and not (self._file_follows_name and self._name.text().strip()):
            self._file_note.setText("")
            return
        actual = self.file_name()
        if self._file_follows_name:
            # The field shows what will be used, suffix and all: nobody typed what is in it.
            self._file.setText(actual[: -len(BOT_FILE_SUFFIX)])
        self._file_note.setText(f"Saved as {self._relative(actual)}")

    def _relative(self, file_name: str) -> str:
        folder = self._folder.folder()
        return f"{folder}/{file_name}" if folder else file_name

    def _refresh_commit(self) -> None:
        self._commit.setEnabled(self._pages.currentIndex() == _PAGE_START or self.is_complete())
