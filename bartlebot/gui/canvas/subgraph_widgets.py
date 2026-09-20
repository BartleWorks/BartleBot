"""The small widgets subgraph authoring adds to the canvas.

``EditSubgraphCrumb`` is the overlay pill shown while a linked region is being
edited in place, with the two verbs that end the edit. ``SubgraphMetaDialog``
asks for a name and description when a region becomes a definition.
``ExposeInputDialog`` binds one member param to an existing input of the same
type, or to a new one. All three are plain Qt; the policy they drive lives in
``BotCanvas`` (see docs/SUBGRAPHS.md, *Authoring is edit in place, then push*).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import SubgraphInput
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.action_button import ActionButton, ActionStyle
from bartlebot.gui.widgets.dialog_footer import dialog_footer

#: The combo entry that means "declare a new input" rather than bind to one.
NEW_INPUT = "— New input —"


class EditSubgraphCrumb(ThemedMixin, QWidget):
    """``Editing subgraph <name>`` with Save to library and Discard, pinned over the view.

    Save is the one thing this surface expects (``PRIMARY``); Discard is the quiet
    alternative. Built like the focus crumb, but themed through the mixin because
    the canvas cannot re-derive a child it built inline.
    """

    save_requested = pyqtSignal()
    discard_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._label = QLabel("Editing subgraph")
        self._save_btn = ActionButton("upload", "Save to library", style=ActionStyle.PRIMARY, fill=False)
        self._discard_btn = ActionButton("dismiss", "Discard", fill=False)
        self._save_btn.clicked.connect(self.save_requested)
        self._discard_btn.clicked.connect(self.discard_requested)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 6, 6)
        row.setSpacing(8)
        row.addWidget(self._label)
        row.addWidget(self._save_btn)
        row.addWidget(self._discard_btn)
        self.refresh_theme_styles()

    def set_subgraph_name(self, name: str) -> None:
        self._label.setText(f"Editing subgraph — {name}")
        self.adjustSize()

    @property
    def text(self) -> str:
        return self._label.text()

    def apply_theme_styles(self) -> None:
        self.setStyleSheet(
            f"EditSubgraphCrumb {{ background-color: {THEME.surface}; border: 1px solid {THEME.accent}; "
            f"border-radius: 6px; }}"
        )
        self._label.setStyleSheet(
            f"border: none; color: {THEME.text}; font-size: {THEME.font_body_px}px; font-weight: 600; padding: 0;"
        )


class SubgraphMetaDialog(QDialog):
    """Name and description for a definition about to be created from a region."""

    def __init__(self, parent: QWidget | None = None, *, name: str = "", description: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Save Region as Reusable Subgraph")
        self._name = QLineEdit(name)
        self._name.setPlaceholderText("What this subgraph does, in a few words")
        self._description = QPlainTextEdit(description)
        self._description.setPlaceholderText("When to use it, and what it expects of the bot around it (optional)")
        self._description.setFixedHeight(80)
        form = QFormLayout()
        form.addRow("Name", self._name)
        form.addRow("Description", self._description)
        footer = dialog_footer(self, commit_text="Save")
        self._ok = footer.commit
        self._name.textChanged.connect(lambda t: self._ok.setEnabled(bool(t.strip())))
        self._ok.setEnabled(bool(name.strip()))
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(footer.layout)
        self.resize(420, self.sizeHint().height())

    @property
    def name(self) -> str:
        return self._name.text().strip()

    @property
    def description(self) -> str | None:
        text = self._description.toPlainText().strip()
        return text or None


class ExposeInputDialog(QDialog):
    """Bind one member param to an input of the definition being edited.

    Offers the existing inputs whose ``type`` matches the param's, plus *New input*,
    which asks for a name, label and help; the param's current value becomes the
    new input's default. The dialog only collects the choice -- ``choice`` names an
    existing input, or ``new_input()`` builds the declared one -- and the canvas
    applies it, so the same code path serves a test that never opens a dialog.
    """

    def __init__(
        self,
        inputs: list[SubgraphInput],
        *,
        state_local_id: str,
        param_key: str,
        param_type: str,
        param_label: str,
        current_value: object,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Expose as Input")
        self._inputs = inputs
        self._state = state_local_id
        self._key = param_key
        self._type = param_type
        self._value = current_value
        self._compatible = [i for i in inputs if i.type == param_type]

        self._combo = QComboBox()
        for inp in self._compatible:
            self._combo.addItem(f"{inp.label or inp.name} ({inp.name})", inp.name)
        self._combo.addItem(NEW_INPUT, None)
        self._name = QLineEdit(param_key)
        self._label = QLineEdit(param_label)
        self._help = QLineEdit()
        self._help.setPlaceholderText("Shown as the input's tooltip (optional)")

        form = QFormLayout()
        form.addRow(QLabel(f"Bind <b>{param_label}</b> ({param_type}) to:"))
        form.addRow("Input", self._combo)
        form.addRow("Name", self._name)
        form.addRow("Label", self._label)
        form.addRow("Help", self._help)
        footer = dialog_footer(self, on_commit=self._on_accept)
        self._ok = footer.commit
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(footer.layout)
        self._combo.currentIndexChanged.connect(self._on_choice_changed)
        self._name.textChanged.connect(self._validate)
        self._combo.setCurrentIndex(self._combo.count() - 1 if not self._compatible else 0)
        self._on_choice_changed(self._combo.currentIndex())

    @property
    def choice(self) -> str | None:
        """The existing input's name, or None for a new one."""
        return self._combo.currentData()

    def select_new(self) -> None:
        self._combo.setCurrentIndex(self._combo.count() - 1)

    def set_new_input(self, name: str, label: str = "", help_text: str = "") -> None:
        self.select_new()
        self._name.setText(name)
        self._label.setText(label)
        self._help.setText(help_text)

    def _on_choice_changed(self, _index: int) -> None:
        new = self.choice is None
        for w in (self._name, self._label, self._help):
            w.setEnabled(new)
        self._validate()

    def _validate(self, *_args) -> None:
        if self.choice is not None:
            self._ok.setEnabled(True)
            return
        name = self._name.text().strip()
        taken = {i.name for i in self._inputs}
        self._ok.setEnabled(bool(name) and name not in taken)

    def new_input(self) -> SubgraphInput:
        """The input the *New input* fields describe, unbound; the param's value is its default."""
        return SubgraphInput(
            name=self._name.text().strip(),
            label=self._label.text().strip() or None,
            type=self._type,
            help=self._help.text().strip() or None,
            default=self._value,
        )

    def _on_accept(self) -> None:
        self.accept()
