"""Make my own copy: what switching an Official bot to Edit mode asks (#149).

An Official bot is read-only, because every app update replaces it. Running it needs no
copy: option values and combat setup live in settings, which the user changes in Run
mode as for any bot. Changing the graph does, and this asks for it, with the copy's
name prefilled from the Official bot's and editable.

The copy itself is :mod:`bartlebot.content.bot_fork`; the controller makes it and opens it.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from bartlebot.gui.widgets.layout.dialog_footer import dialog_footer

TITLE = "Make My Own Copy"
COMMIT_TEXT = "Make my own copy"


def explanation(name: str) -> str:
    """What the dialog says above the name field, for the Official bot *name*."""
    return (
        f"'{name}' is an Official bot: it ships with BartleBot, has been tested in game, and is "
        "updated with the app, so it can't be changed.\n\n"
        "To edit it, make your own copy. The copy keeps your option values and Bot Combat "
        "Strategies for this bot, and the Official one stays as it is."
    )


class MakeCopyDialog(QDialog):
    """Asks for the copy's name. Accepted means make it: read :attr:`name`."""

    def __init__(self, official_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        text = QLabel(explanation(official_name))
        text.setWordWrap(True)
        self._name = QLineEdit(official_name)
        self._name.selectAll()
        form = QFormLayout()
        form.addRow("Name", self._name)
        footer = dialog_footer(self, commit_text=COMMIT_TEXT, commit_icon="copy")
        self._commit = footer.commit
        self._name.textChanged.connect(lambda t: self._commit.setEnabled(bool(t.strip())))
        self._commit.setEnabled(bool(official_name.strip()))
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addLayout(form)
        layout.addLayout(footer.layout)
        self.resize(440, self.sizeHint().height())

    @property
    def name(self) -> str:
        return self._name.text().strip()

    @property
    def name_edit(self) -> QLineEdit:
        return self._name

    @property
    def commit_button(self):
        return self._commit
