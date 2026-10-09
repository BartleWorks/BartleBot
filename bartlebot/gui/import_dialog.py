"""The import summary: one line and one button, or the rows that need a choice (#171).

When nothing in the file needs deciding -- every item is new, already here, or one that
can only come in as a copy -- the summary is one line and Import: "Import 'Boss X', with
2 subgraphs and a Bot Combat Strategy?". An update or a conflict opens the rows that need
a choice instead, each with its default already set, as Node-RED's import dialog does,
and the rest of the file listed under them. Changing a choice re-matches the file
through the ``build`` callable, since a copy rewrites the links of everything after it.

Nothing here asks for an id, or for any text at all: every choice is a pick from a list.

Matching and writing are :mod:`bartlebot.content.share_import`; this module only shows a plan.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from PyQt6.QtCore import QStandardPaths, Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bartlebot.content.share_export import EXTENSIONS, FILE_TYPES, NOUNS
from bartlebot.content.share_import import (
    ACTION_TEXT,
    OUTCOME_TEXT,
    Action,
    ImportPlan,
    Outcome,
    Row,
    affected_text,
    headline,
)
from bartlebot.gui.widgets.dialog_footer import dialog_footer
from bartlebot.gui.widgets.rule_heading import RuleHeading
from bartlebot.gui.widgets.themed_label import ThemedTextLabel

logger = logging.getLogger(__name__)

TITLE = "Import"
COMMIT_TEXT = "Import"
CHOICES_HEADING = "Needs your choice"
REST_HEADING = "Also in the file"
COPIES_NOTE = "Changes to Official items come in as copies, so your Official ones are never replaced: "

Choices = Mapping[tuple[str, str], Action]


def _kind_line(row: Row) -> str:
    noun = NOUNS.get(row.kind, row.kind)
    return f"{noun[0].upper()}{noun[1:]} · {OUTCOME_TEXT[row.outcome]}"


class ImportDialog(QDialog):
    """Shows what an import will do. Accepted means write :attr:`plan`."""

    def __init__(self, build: Callable[[Choices], ImportPlan], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build = build
        self._plan = build({})
        # Whether the rows are shown: decided once, by whether anything needed a choice when
        # it opened, so a choice that settles every other row never hides the one just made.
        self._detailed = self._plan.asks
        self._combos: dict[tuple[str, str], QComboBox] = {}
        self.setWindowTitle(TITLE)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        self._headline = QLabel()
        self._headline.setWordWrap(True)
        self._headline.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._headline)

        self._copies = ThemedTextLabel("")
        self._copies.setWordWrap(True)
        layout.addWidget(self._copies)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        # The rows wrap to the width they are given, so the choice beside each stays in view.
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self._scroll, 1)

        footer = dialog_footer(self, commit_text=COMMIT_TEXT, commit_icon="download")
        self._commit = footer.commit
        layout.addLayout(footer.layout)

        self._show_plan()
        if self._detailed:
            self.resize(560, 480)
        else:
            self._scroll.hide()
            # As tall as the wrapped headline needs at this width, and no taller.
            self.resize(460, max(layout.totalHeightForWidth(460), self.minimumSizeHint().height()))

    # -- reading --------------------------------------------------------

    @property
    def plan(self) -> ImportPlan:
        """What Import writes, as the choices stand."""
        return self._plan

    @property
    def commit_button(self):
        return self._commit

    @property
    def headline_text(self) -> str:
        return self._headline.text()

    @property
    def copies_text(self) -> str:
        return self._copies.text() if not self._copies.isHidden() else ""

    def combo(self, kind: str, item_id: str) -> QComboBox | None:
        """The choice for one row, or None for a row with nothing to choose. Public so a test can pick."""
        return self._combos.get((kind, item_id))

    def listed(self) -> list[tuple[str, list[str]]]:
        """``[(group heading, [item names]), …]`` as shown, when the rows are. Public so a test can read it."""
        if not self._detailed:
            return []
        groups = [
            (CHOICES_HEADING, [r.name for r in self._plan.rows if r.asks]),
            (REST_HEADING, [r.name for r in self._plan.rows if not r.asks]),
        ]
        return [(heading, names) for heading, names in groups if names]

    # -- building -------------------------------------------------------

    def _choices(self) -> dict[tuple[str, str], Action]:
        return {key: combo.currentData() for key, combo in self._combos.items()}

    def _rebuild(self) -> None:
        self._plan = self._build(self._choices())
        self._show_plan()

    def _show_plan(self) -> None:
        self._headline.setText(headline(self._plan))
        copies = [r.name for r in self._plan.rows if r.outcome is Outcome.OFFICIAL]
        self._copies.setText(COPIES_NOTE + ", ".join(f"'{n}'" for n in copies) + "." if copies else "")
        self._copies.setVisible(bool(copies))
        self._commit.setEnabled(self._plan.writes_anything)
        if not self._detailed:
            return
        body = QWidget()
        rows = QVBoxLayout(body)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(4)
        self._combos = {}
        asking = [r for r in self._plan.rows if r.asks]
        rest = [r for r in self._plan.rows if not r.asks]
        if asking:
            rows.addWidget(RuleHeading(CHOICES_HEADING))
        for row in asking:
            rows.addWidget(self._choice_row(row))
        if rest:
            rows.addWidget(RuleHeading(REST_HEADING))
            for row in rest:
                rows.addWidget(self._row(row))
        rows.addStretch(1)
        # Taken out and deleted later, never replaced in place: setWidget deletes the old
        # body at once, and a rebuild runs inside the old combo's own signal.
        old = self._scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self._scroll.setWidget(body)

    @staticmethod
    def _name_label(row: Row) -> QLabel:
        name = QLabel(row.name)
        font = name.font()
        font.setBold(True)
        name.setFont(font)
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setWordWrap(True)
        return name

    def _row(self, row: Row) -> QWidget:
        widget = QWidget()
        box = QVBoxLayout(widget)
        box.setContentsMargins(8, 2, 0, 4)
        box.setSpacing(0)
        box.addWidget(self._name_label(row))
        for text in (_kind_line(row), affected_text(row)):
            if text:
                line = ThemedTextLabel(text)
                line.setWordWrap(True)
                box.addWidget(line)
        return widget

    def _choice_row(self, row: Row) -> QWidget:
        widget = QWidget()
        line = QHBoxLayout(widget)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(self._row(row), 1)
        combo = QComboBox()
        for action in row.choices:
            combo.addItem(ACTION_TEXT[action], action)
        combo.setCurrentIndex(row.choices.index(row.action))
        combo.currentIndexChanged.connect(lambda _i: self._rebuild())
        self._combos[(row.kind, row.id)] = combo
        line.addWidget(combo, 0, Qt.AlignmentFlag.AlignTop)
        return widget


def file_filter() -> str:
    """The open dialog's filter: every kind of share file at once, then each on its own."""
    every = " ".join(f"*{ext}" for ext in EXTENSIONS.values())
    each = ";;".join(f"{FILE_TYPES[kind]} (*{ext})" for kind, ext in EXTENSIONS.items())
    return f"Shared bots and strategies ({every});;{each}"


def ask_for_file(parent: QWidget | None) -> str | None:
    """File › Import…'s open dialog, starting in Documents. The path chosen, or None."""
    start = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    path, _ = QFileDialog.getOpenFileName(parent, TITLE, start, file_filter())
    return path or None


def confirm_import(parent: QWidget | None, build: Callable[[Choices], ImportPlan]) -> ImportPlan | None:
    """Show the summary, and the plan to write once the user says Import; None when there is nothing to do.

    A file holding nothing new says so in a message and writes nothing: importing the
    same file twice changes nothing the second time.
    """
    plan = build({})
    if not plan.writes_anything:
        QMessageBox.information(parent, TITLE, headline(plan))
        return None
    dialog = ImportDialog(build, parent=parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.plan
