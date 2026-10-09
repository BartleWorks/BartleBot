"""The export summary: every item going into a share file, the two checkboxes, then Save (#170).

One screen for every kind. It lists what the file will hold in three groups: what is
sent, the Official items that are named rather than sent (whoever opens the file has
them already), and links the sender's own library cannot resolve. Names and
descriptions are the user's own text, going to someone else, so the summary shows them.

The two checkboxes are the only choices: a bot's own Bot Combat Strategy, and that
strategy's decks, both on by default. Ticking one re-gathers the file through the
``build`` callable the caller passes, so the list is always what Save writes.

Gathering is :mod:`bartlebot.content.share_export`; this module only shows a plan and writes it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QStandardPaths, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bartlebot.content.share_export import FILE_TYPES, NOUNS, Disposition, Entry, ExportPlan
from bartlebot.gui.widgets.labels.rule_heading import RuleHeading
from bartlebot.gui.widgets.labels.themed_label import ThemedTextLabel
from bartlebot.gui.widgets.layout.dialog_footer import dialog_footer

logger = logging.getLogger(__name__)

COMMIT_TEXT = "Save…"

#: The group each disposition is listed under, in the order they are shown.
GROUPS: tuple[tuple[str, tuple[Disposition, ...]], ...] = (
    ("Sent in the file", (Disposition.PACKED, Disposition.COPY)),
    ("Official, so not sent: they have it already", (Disposition.NAMED,)),
    ("Not in your library, so not sent", (Disposition.MISSING,)),
)

#: What an item's line says about it, beside its kind.
DISPOSITION_TEXT = {
    Disposition.PACKED: "",
    Disposition.COPY: "your changes to an Official one; they get it as a copy",
    Disposition.NAMED: "",
    Disposition.MISSING: "",
}

NOTHING_TO_SEND = (
    "Nothing here is yours to send: everything it needs is Official, and whoever opens the file has it already."
)


@dataclass(frozen=True)
class Choices:
    """The two checkboxes, as :func:`run_export`'s ``build`` callable receives them."""

    include_combat: bool = True
    include_decks: bool = True


def entry_line(entry: Entry) -> str:
    """An item's second line: its kind, and what happens to it when that needs saying."""
    noun = NOUNS.get(entry.kind, entry.kind)
    extra = DISPOSITION_TEXT[entry.disposition]
    return f"{noun[0].upper()}{noun[1:]}" + (f" · {extra}" if extra else "")


class ExportDialog(QDialog):
    """Shows what an export will write. Accepted means write :attr:`plan`.

    ``combat_name`` is the bot's Bot Combat Strategy, offered as the first checkbox when
    there is one; ``offers_decks`` adds the second, for an export that carries one.
    """

    def __init__(
        self,
        build: Callable[[Choices], ExportPlan],
        *,
        combat_name: str | None = None,
        offers_decks: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._build = build
        self._plan = build(Choices())
        self.setWindowTitle(f"Export {NOUNS[self._plan.kind]}")

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        intro = QLabel(
            f"'{self._plan.name}' goes to one {FILE_TYPES[self._plan.kind]} file "
            f"({self._plan.extension}), with everything of yours it needs. Your option values "
            "and which Bot Combat Strategy is active stay here."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._combat_box: QCheckBox | None = None
        self._decks_box: QCheckBox | None = None
        if combat_name is not None:
            self._combat_box = QCheckBox(f"Include my Bot Combat Strategy, '{combat_name}'")
            self._combat_box.setChecked(True)
            self._combat_box.toggled.connect(self._rebuild)
            layout.addWidget(self._combat_box)
        if offers_decks:
            self._decks_box = QCheckBox("Include its decks")
            self._decks_box.setChecked(True)
            self._decks_box.toggled.connect(self._rebuild)
            layout.addWidget(self._decks_box)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self._scroll, 1)

        self._empty = ThemedTextLabel(NOTHING_TO_SEND)
        self._empty.setWordWrap(True)
        layout.addWidget(self._empty)

        footer = dialog_footer(self, commit_text=COMMIT_TEXT, commit_icon="save_as")
        self._commit = footer.commit
        layout.addLayout(footer.layout)

        self._show_plan()
        self.resize(520, 480)

    # -- reading --------------------------------------------------------

    @property
    def plan(self) -> ExportPlan:
        """What Save writes, as the checkboxes stand."""
        return self._plan

    @property
    def choices(self) -> Choices:
        return Choices(
            include_combat=self._combat_box is None or self._combat_box.isChecked(),
            include_decks=self._decks_box is None or self._decks_box.isChecked(),
        )

    @property
    def combat_box(self) -> QCheckBox | None:
        return self._combat_box

    @property
    def decks_box(self) -> QCheckBox | None:
        return self._decks_box

    @property
    def commit_button(self):
        return self._commit

    def listed(self) -> list[tuple[str, list[str]]]:
        """``[(group heading, [item names]), …]`` as shown. Public so a test can read the summary."""
        return [
            (heading, [e.name for e in self._plan.entries if e.disposition in dispositions])
            for heading, dispositions in GROUPS
            if any(e.disposition in dispositions for e in self._plan.entries)
        ]

    # -- building -------------------------------------------------------

    def _rebuild(self) -> None:
        if self._decks_box is not None and self._combat_box is not None:
            # A bot's decks belong to its Bot Combat Strategy: with that left out, so are they.
            self._decks_box.setEnabled(self._combat_box.isChecked())
        self._plan = self._build(self.choices)
        self._show_plan()

    def _show_plan(self) -> None:
        body = QWidget()
        rows = QVBoxLayout(body)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(4)
        for heading, dispositions in GROUPS:
            entries = [e for e in self._plan.entries if e.disposition in dispositions]
            if not entries:
                continue
            rows.addWidget(RuleHeading(heading))
            for entry in entries:
                rows.addWidget(self._row(entry))
        rows.addStretch(1)
        self._scroll.setWidget(body)
        self._empty.setVisible(not self._plan.sends_anything)
        self._commit.setEnabled(self._plan.sends_anything)

    @staticmethod
    def _row(entry: Entry) -> QWidget:
        row = QWidget()
        box = QVBoxLayout(row)
        box.setContentsMargins(8, 2, 0, 4)
        box.setSpacing(0)
        name = QLabel(entry.name)
        font = name.font()
        font.setBold(True)
        name.setFont(font)
        name.setTextFormat(Qt.TextFormat.PlainText)
        box.addWidget(name)
        line = ThemedTextLabel(entry_line(entry))
        box.addWidget(line)
        if entry.description:
            description = QLabel(entry.description)
            description.setWordWrap(True)
            description.setTextFormat(Qt.TextFormat.PlainText)
            box.addWidget(description)
        return row


def _start_folder() -> Path:
    found = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(found) if found else Path.home()


def run_export(
    parent: QWidget | None,
    build: Callable[[Choices], ExportPlan],
    *,
    combat_name: str | None = None,
    offers_decks: bool = False,
) -> Path | None:
    """Show the summary, ask where to save, and write the file. The path written, or None.

    A ``build`` that raises (an item that no longer loads) is reported and writes nothing.
    """
    try:
        dialog = ExportDialog(build, combat_name=combat_name, offers_decks=offers_decks, parent=parent)
    except Exception as e:
        logger.exception("Could not gather an export")
        QMessageBox.critical(parent, "Export", f"Could not gather what to export:\n\n{e}")
        return None
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    plan = dialog.plan
    title = f"Export {NOUNS[plan.kind]}"
    path, _ = QFileDialog.getSaveFileName(
        parent,
        title,
        str(_start_folder() / plan.suggested_file_name()),
        f"{FILE_TYPES[plan.kind]} (*{plan.extension})",
    )
    if not path:
        return None
    target = Path(path)
    if target.suffix.lower() != plan.extension:
        target = target.with_name(target.name + plan.extension)
    try:
        plan.write(target)
    except Exception as e:
        logger.exception("Could not write %s", target)
        QMessageBox.critical(parent, title, f"Could not write the file:\n\n{e}")
        return None
    logger.info("Exported the %s '%s' to %s", NOUNS[plan.kind], plan.name, target)
    return target
