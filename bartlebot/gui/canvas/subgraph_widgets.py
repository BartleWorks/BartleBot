"""The small widgets subgraph authoring adds to the canvas.

``DefinitionCrumb`` is the overlay shown while a definition is open as a document: the
path back to where it was opened from, and the verbs that end the visit.
``SubgraphMetaDialog`` asks for a name and description when a region becomes a
definition. ``PromoteInputDialog`` binds one member param to an existing input of a
compatible type, or to a new one. All three are plain Qt; the policy they drive lives
in ``BotCanvas`` and the controller's document stack.
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
from bartlebot.gui.themed import ThemedMixin, set_style_sheet
from bartlebot.gui.widgets.action_button import ActionButton, ActionStyle
from bartlebot.gui.widgets.dialog_footer import dialog_footer
from bartlebot.gui.widgets.icon_button import IconButton
from bartlebot.nodes.param_schema import narrowest_type

#: The combo entry that means "declare a new input" rather than bind to one.
NEW_INPUT = "— New input —"


class DefinitionCrumb(ThemedMixin, QWidget):
    """``<bot> › <definition>`` with *Save to library* and a way back, pinned over the view.

    Save is the one thing this surface expects (``PRIMARY``). The path's first segment
    is the document the definition was opened from, and clicking it goes back, as the
    close button does -- both ask Save / Discard / Cancel first when there are unsaved
    changes. The frame round the view is the double line a call node's side bars
    echo, so "double means shared" holds on both.
    """

    save_requested = pyqtSignal()
    back_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._parent_lbl = QLabel()
        self._parent_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._parent_lbl.mousePressEvent = lambda _ev: self.back_requested.emit()  # type: ignore[method-assign]
        self._sep = QLabel("›")
        self._name_lbl = QLabel()
        self._hint_lbl = QLabel()
        self._save_btn = ActionButton("upload", "Save to library", style=ActionStyle.PRIMARY, fill=False)
        self._close_btn = IconButton("dismiss", tooltip="Close the definition and go back", size=22, icon_size=12)
        self._save_btn.clicked.connect(self.save_requested)
        self._close_btn.clicked.connect(self.back_requested)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 5, 6, 5)
        row.setSpacing(8)
        row.addWidget(self._parent_lbl)
        row.addWidget(self._sep)
        row.addWidget(self._name_lbl)
        row.addWidget(self._hint_lbl)
        row.addWidget(self._save_btn)
        row.addWidget(self._close_btn)
        self.refresh_theme_styles()

    def set_path(self, parent: str | None, name: str, hint: str = "") -> None:
        """``parent`` is the document the definition was opened from, or None when it was opened on its own."""
        self._parent_lbl.setText(parent or "")
        self._parent_lbl.setVisible(bool(parent))
        self._parent_lbl.setToolTip(f"Back to {parent}" if parent else "")
        self._sep.setVisible(bool(parent))
        self._name_lbl.setText(name)
        self._hint_lbl.setText(hint)
        self._hint_lbl.setVisible(bool(hint))
        self.adjustSize()

    @property
    def text(self) -> str:
        return " › ".join(p for p in (self._parent_lbl.text(), self._name_lbl.text()) if p)

    @property
    def save_button(self) -> ActionButton:
        return self._save_btn

    def apply_theme_styles(self) -> None:
        set_style_sheet(
            self,
            f"DefinitionCrumb {{ background-color: {THEME.accent}; border: 1px solid {THEME.accent}; border-radius: 6px; }}",
        )
        text = f"border: none; color: {THEME.pill_text}; font-size: {THEME.font_body_px}px; padding: 0;"
        set_style_sheet(self._name_lbl, text + " font-weight: 600;")
        set_style_sheet(self._parent_lbl, text + " text-decoration: underline;")
        set_style_sheet(self._sep, text)
        set_style_sheet(self._hint_lbl, f"border: none; color: {THEME.pill_text}; font-size: {THEME.font_small_px}px;")


class SubgraphMetaDialog(QDialog):
    """Name and description for a definition about to be created from a region."""

    def __init__(
        self, parent: QWidget | None = None, *, name: str = "", description: str = "", title: str | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title or "Save Region as Reusable Subgraph")
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


def compatible_inputs(inputs: list[SubgraphInput], param_type: str) -> list[SubgraphInput]:
    """The inputs a param of ``param_type`` can share a value with: one type narrows the other."""
    return [i for i in inputs if narrowest_type([i.type, param_type]) is not None]


class PromoteInputDialog(QDialog):
    """Promote one member param to an input of the definition being edited.

    Offers the existing inputs whose type is compatible with the param's (one narrows
    the other, so a ``zone`` and a ``nav_zone`` can share), plus *New input*, which
    asks for a name, label and help; the param's current value becomes the new
    input's default. The dialog only collects the choice -- ``choice`` names an
    existing input, or ``new_input()`` builds the declared one -- and the canvas
    applies it, so the same code path serves a test that never opens a dialog.
    """

    def __init__(
        self,
        inputs: list[SubgraphInput],
        *,
        param_key: str,
        param_type: str,
        param_label: str,
        current_value: object,
        shared: list[tuple[str, str, str]] | None = None,
        groups: list[tuple[str, str]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Promote to Subgraph Input")
        self._inputs = inputs
        self._key = param_key
        self._type = param_type
        self._value = current_value
        self._compatible = compatible_inputs(inputs, param_type)

        self._combo = QComboBox()
        for inp in self._compatible:
            self._combo.addItem(f"{inp.label or inp.name} ({inp.type})", ("input", inp.name))
        # An exposed node's params can be shared too: (label, panel id, key), already
        # filtered to the compatible ones by the caller.
        for label, panel_id, key in shared or []:
            self._combo.addItem(f"{label} (exposed)", ("shared", panel_id, key))
        self._combo.addItem(NEW_INPUT, None)
        self._name = QLineEdit(param_key)
        self._label = QLineEdit(param_label)
        self._help = QLineEdit()
        self._help.setPlaceholderText("Shown as the input's tooltip (optional)")
        # (group id, label) of the input groups a new input can be listed under.
        self._group = QComboBox()
        self._group.addItem("None", None)
        for gid, glabel in groups or []:
            self._group.addItem(glabel, gid)

        form = QFormLayout()
        form.addRow(QLabel(f"Set <b>{param_label}</b> ({param_type}) from:"))
        form.addRow("Input", self._combo)
        form.addRow("Name", self._name)
        form.addRow("Label", self._label)
        form.addRow("Help", self._help)
        form.addRow("Input group", self._group)
        footer = dialog_footer(self, on_commit=self.accept)
        self._ok = footer.commit
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(footer.layout)
        self._combo.currentIndexChanged.connect(self._on_choice_changed)
        self._name.textChanged.connect(self._validate)
        self._combo.setCurrentIndex(0)
        self._on_choice_changed(self._combo.currentIndex())

    @property
    def choice(self) -> str | None:
        """The existing input's name, or None for a new one or a shared exposed param."""
        data = self._combo.currentData()
        return data[1] if data and data[0] == "input" else None

    @property
    def shared_choice(self) -> tuple[str, str] | None:
        """``(panel id, key)`` of the exposed param chosen to share, or None."""
        data = self._combo.currentData()
        return (data[1], data[2]) if data and data[0] == "shared" else None

    def select(self, text_prefix: str) -> None:
        """Pick the first entry whose text starts with *text_prefix* -- for tests."""
        for i in range(self._combo.count()):
            if self._combo.itemText(i).startswith(text_prefix):
                self._combo.setCurrentIndex(i)
                return

    def select_new(self) -> None:
        self._combo.setCurrentIndex(self._combo.count() - 1)

    def set_new_input(self, name: str, label: str = "", help_text: str = "") -> None:
        self.select_new()
        self._name.setText(name)
        self._label.setText(label)
        self._help.setText(help_text)

    def _on_choice_changed(self, _index: int) -> None:
        new = self._combo.currentData() is None
        for w in (self._name, self._label, self._help, self._group):
            w.setEnabled(new)
        self._validate()

    def _validate(self, *_args) -> None:
        if self._combo.currentData() is not None:
            self._ok.setEnabled(True)
            return
        name = self._name.text().strip()
        taken = {i.name for i in self._inputs}
        self._ok.setEnabled(bool(name) and name not in taken and "." not in name)

    def new_input(self) -> SubgraphInput:
        """The input the *New input* fields describe, unbound; the param's value is its default."""
        return SubgraphInput(
            name=self._name.text().strip(),
            label=self._label.text().strip() or None,
            type=self._type,
            help=self._help.text().strip() or None,
            default=self._value,
            panel=self._group.currentData(),
        )

    def set_group(self, group_id: str | None) -> None:
        """Pick the new input's group by id -- for tests."""
        idx = self._group.findData(group_id)
        self._group.setCurrentIndex(idx if idx >= 0 else 0)
