"""Sidebar section: the bot's variable declarations, on the Edit-mode Options tab (#187).

A variable is what the bot learns during a run, an option what the user chose before it
(``docs/VARIABLES.md``), so the two get separate sections; this one sits under Options on
the same tab, since both are declarations an author makes. Each row is a variable's
name, then its type and where it starts, behind the same ``[bar][edit][?]`` gutter the
option rows use, so the columns line up down the tab.

Add and Edit open one dialog, :class:`VariableDialog`, which lists an enum's values.
Three things it refuses, because each would change routing without saying so:

- **Delete, while anything uses the variable.** Deleting an option strips its conditions
  in silence; a variable's conditions *are* the routing, so the dialog names each edge
  and node that uses it instead, and the author takes them off first.
- **A change of type, while anything uses it**, for the same reason: every row reading
  it would compare against a value the new type refuses.
- **Removing an enum value a row compares with or sets.**

A rename is the one change that carries its references with it: every condition and
assignment that names the variable is rewritten (``variables.rename``).

Every change is undoable: :attr:`edit_starting` goes out before the config is touched,
and the canvas takes its snapshot then, as it does for a panel edit.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bartleby.qt.sidebar.sidebar_section import SidebarSection
from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin
from bartleby.qt.widgets.buttons.action_button import ActionButton, ActionStyle
from bartleby.qt.widgets.buttons.icon_button import IconButton, IconTint
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from bartleby.qt.widgets.layout.dialog_footer import dialog_footer
from bartleby.qt.widgets.layout.discard import discard_widget
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot import variables
from bartlebot.bot.schema import VariableModel
from bartlebot.gui.sidebar.option_gutter import (
    GUTTER_BTN_PX,
    HelpToggle,
    OverrideBar,
    build_gutter_row,
    keep_slot_when_hidden,
)
from bartlebot.gui.widgets.inputs.growing_text_edit import GrowingTextEdit
from bartlebot.gui.widgets.inputs.option_widgets import TYPE_LABELS
from bartlebot.gui.widgets.inputs.variable_widgets import VariableValueField

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

logger = logging.getLogger(__name__)

#: What each type is called where an author picks it: the option dialog's words for the
#: two types they share, so one bot's two lists of declarations speak one language.
VARIABLE_TYPE_LABELS: dict[str, str] = {
    "bool": TYPE_LABELS["bool"],
    "int": TYPE_LABELS["int"],
    "enum": "One of a list",
}

_PLACEHOLDER = "No variables. A variable is something the bot learns during a run, for an edge to read."

_DIALOG_MIN_W = 420


def describe_declaration(decl: VariableModel) -> str:
    """A row's caption: the type, then where it starts, ``Whole number · starts at 0``."""
    kind = VARIABLE_TYPE_LABELS.get(decl.type, decl.type)
    start = decl.initial_value()
    return f"{kind} · starts at {variables.format_value(start)}" if start is not None else kind


def _uses_text(found: list[variables.Use]) -> str:
    return "\n".join(f"• {u.text}" for u in found)


# ---------------------------------------------------------------------------
# Add / Edit dialog
# ---------------------------------------------------------------------------


class VariableDialog(ThemedMixin, QDialog):
    """Add or edit one variable declaration: name, type, an enum's values, where it starts, and why.

    Pass ``existing`` to edit (Save, and a Delete beside the footer). ``config`` is the
    bot, for the names already taken and for what uses the variable. Accepting means the
    author finished, not that they saved: read :attr:`was_deleted` before :meth:`result_model`,
    as with the option dialog.
    """

    def __init__(self, config: BotConfig, parent: QWidget | None = None, existing: VariableModel | None = None):
        super().__init__(parent)
        editing = existing is not None
        self.setWindowTitle("Edit Variable" if editing else "Add Variable")
        self.setModal(True)
        self.setMinimumWidth(_DIALOG_MIN_W)
        self._config = config
        self._existing = existing
        self._result: VariableModel | None = None
        self._deleted = False
        self._delete_btn: ActionButton | None = None

        form = QFormLayout()
        form.setSpacing(6)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. swords")

        self._type_combo = QComboBox()
        for var_type, label in VARIABLE_TYPE_LABELS.items():
            self._type_combo.addItem(label, var_type)

        # One value a line: an enum's order is meaningful (the first is where it starts
        # unless another is picked), and a plain list is the easiest thing to reorder.
        self._values_edit = QPlainTextEdit()
        self._values_edit.setPlaceholderText("One value a line, e.g.\nSun Chamber\nMoon Chamber")
        self._values_edit.setFixedHeight(96)
        self._values_lbl = QLabel("Values")

        self._initial = VariableValueField()

        self._desc_edit = GrowingTextEdit(min_lines=2, max_lines=6)
        desc_font = QFont(self._desc_edit.font())
        desc_font.setPixelSize(THEME.font_small_px)
        self._desc_edit.setFont(desc_font)
        self._desc_edit.setPlaceholderText("Optional: what it remembers, and what reads it")

        form.addRow("Name", self._name_edit)
        form.addRow("Type", self._type_combo)
        form.addRow(self._values_lbl, self._values_edit)
        form.addRow("Starts at", self._initial)
        form.addRow("Description", self._desc_edit)

        self._err_lbl = QLabel()
        self._err_lbl.setWordWrap(True)
        self._err_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        extras = []
        if editing:
            self._delete_btn = ActionButton("trash", "Delete", style=ActionStyle.CAUTION, fill=False)
            self._delete_btn.setToolTip(f"Remove the variable '{existing.name}' from this bot")
            self._delete_btn.clicked.connect(self._on_delete)
            extras.append(self._delete_btn)
        footer = dialog_footer(self, commit_text="Save" if editing else "Add", on_commit=self._on_ok, extras=extras)

        outer = QVBoxLayout(self)
        outer.setSpacing(8)
        outer.addLayout(form)
        outer.addWidget(self._err_lbl)
        outer.addLayout(footer.layout)
        self._err_lbl.setVisible(False)

        if editing:
            self._name_edit.setText(existing.name)
            self._type_combo.setCurrentIndex(max(0, self._type_combo.findData(existing.type)))
            self._values_edit.setPlainText("\n".join(existing.values or []))
            self._desc_edit.setPlainText(existing.description or "")
        self._sync_type(existing.initial_value() if editing else None)
        self._type_combo.currentIndexChanged.connect(lambda _i: self._sync_type(self._initial.value()))
        self._values_edit.textChanged.connect(lambda: self._sync_type(self._initial.value()))
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self._err_lbl.setStyleSheet(f"color: {THEME.error}; font-size: {THEME.font_small_px}px;")
        self._desc_edit.setStyleSheet(
            f"QTextEdit {{ background: {THEME.surface}; border: 1px solid {THEME.border}; "
            f"border-radius: 4px; color: {THEME.text}; padding: 3px; }}"
        )

    # ------------------------------------------------------------------

    @property
    def was_deleted(self) -> bool:
        """Whether the author chose Delete, which accepts the dialog with no result."""
        return self._deleted

    def result_model(self) -> VariableModel:
        assert self._result is not None
        return self._result

    @property
    def initial_field(self) -> VariableValueField:
        """The Starts at control, for a test to drive."""
        return self._initial

    def _type(self) -> str:
        return self._type_combo.currentData() or "bool"

    def _values(self) -> list[str]:
        return [line.strip() for line in self._values_edit.toPlainText().splitlines() if line.strip()]

    def _draft(self, initial=None) -> VariableModel:
        var_type = self._type()
        return VariableModel(
            name=self._name_edit.text().strip() or "variable",
            type=var_type,
            values=self._values() if var_type == "enum" else None,
            initial=initial,
        )

    def _sync_type(self, keep) -> None:
        """Rebuild Starts at for the type and values now in the form, keeping the value shown if it still fits."""
        is_enum = self._type() == "enum"
        self._values_lbl.setVisible(is_enum)
        self._values_edit.setVisible(is_enum)
        draft = self._draft()
        start = keep if keep is not None and variables.value_fits(draft, keep) else draft.initial_value()
        self._initial.set_declaration(draft, start)

    def _on_ok(self) -> None:
        old = self._existing
        name = self._name_edit.text().strip()
        problem = variables.name_problem(self._config, name, current=old.name if old else None)
        if problem:
            self._show_error(problem)
            return
        var_type = self._type()
        values = self._values() if var_type == "enum" else None
        if var_type == "enum":
            if not values:
                self._show_error("List at least one value, one a line.")
                return
            if len(set(values)) != len(values):
                self._show_error("Each value can be listed once.")
                return
        if old is not None:
            if var_type != old.type and (found := variables.uses(self._config, old.name)):
                self._show_error(
                    f"'{old.name}' is used, so its type cannot change. Take it off these first:\n{_uses_text(found)}"
                )
                return
            removed = [v for v in old.values or [] if v not in (values or [])] if old.type == "enum" else []
            if removed and (found := variables.uses(self._config, old.name, values=removed)):
                gone = ", ".join(removed)
                self._show_error(f"These still compare with or set {gone}:\n{_uses_text(found)}")
                return
        draft = VariableModel(name=name, type=var_type, values=values)
        start = self._initial.value()
        if not variables.value_fits(draft, start):
            start = draft.initial_value()
        self._result = VariableModel(
            name=name,
            type=var_type,
            values=values,
            # Left out while it is where the type starts anyway, so the file says only what the author chose.
            initial=None if start == draft.initial_value() and type(start) is type(draft.initial_value()) else start,
            description=self._desc_edit.toPlainText().strip() or None,
        )
        self.accept()

    def _on_delete(self) -> None:
        name = self._existing.name if self._existing else ""
        found = variables.uses(self._config, name)
        if found:
            refuse = QMessageBox(self)
            refuse.setWindowTitle("Variable in use")
            refuse.setIcon(QMessageBox.Icon.Information)
            refuse.setText(f"'{name}' is used, so it cannot be deleted yet.")
            refuse.setInformativeText(
                "Deleting it would change which way the bot goes without saying so. Take it off these first:\n\n"
                + _uses_text(found)
            )
            refuse.setStandardButtons(QMessageBox.StandardButton.Ok)
            refuse.exec()
            return
        confirm = QMessageBox(self)
        confirm.setWindowTitle("Delete Variable")
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setText(f"Delete the variable '{name}'?")
        confirm.setInformativeText("Nothing uses it.")
        confirm.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return
        self._deleted = True
        self.accept()

    def _show_error(self, text: str) -> None:
        self._err_lbl.setText(text)
        self._err_lbl.setVisible(True)


# ---------------------------------------------------------------------------
# The section
# ---------------------------------------------------------------------------


class _EditButton(IconButton):
    """Opens a variable's dialog: the gutter slot Options gives its own Edit button."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(
            "pencil",
            tooltip=f"Edit or delete the variable '{name}'",
            size=GUTTER_BTN_PX,
            icon_size=14,
            tint=IconTint.ACCENT_WHEN_ENABLED,
            parent=parent,
        )


class VariablesSection(SidebarSection):
    """The variable declarations, one row each, with Add Variable under them."""

    #: Goes out before the config is touched, carrying the undo step's name.
    edit_starting = pyqtSignal(str)
    #: The declarations, or a reference renamed with one, changed.
    variables_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Variables", parent)
        self._config: BotConfig | None = None
        self._editable = False
        self._rows: dict[str, QWidget] = {}
        self._edit_btns: dict[str, _EditButton] = {}
        self._descriptions: dict[str, QWidget] = {}

        outer = QVBoxLayout()
        outer.setSpacing(4)
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(6)
        outer.addLayout(self._rows_layout)
        self._empty_lbl = ThemedTextLabel(_PLACEHOLDER, color="text_muted")
        self._empty_lbl.setWordWrap(True)
        outer.addWidget(self._empty_lbl)
        self._add_btn = ActionButton("plus", "Add Variable")
        self._add_btn.clicked.connect(self.add_variable)
        outer.addWidget(self._add_btn)
        self.set_content_layout(outer)
        self._add_btn.setVisible(False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self._rebuild()

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._add_btn.setVisible(editable)
        for btn in self._edit_btns.values():
            btn.setVisible(editable)

    @property
    def row_names(self) -> list[str]:
        """The variables shown, in the file's order."""
        return list(self._rows)

    def row(self, name: str) -> QWidget | None:
        return self._rows.get(name)

    def edit_button(self, name: str) -> QWidget | None:
        return self._edit_btns.get(name)

    def add_variable(self) -> None:
        """Open the dialog for a new variable, and add it if the author finishes."""
        if self._config is None:
            return
        dlg = VariableDialog(self._config, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        decl = dlg.result_model()
        self.edit_starting.emit(f"Add variable {decl.name}")
        self._config.variables.append(decl)
        self._changed()

    def edit_variable(self, name: str) -> None:
        """Open ``name``'s dialog, and apply a save, rename or delete."""
        if self._config is None:
            return
        index = next((i for i, v in enumerate(self._config.variables) if v.name == name), None)
        if index is None:
            return
        dlg = VariableDialog(self._config, parent=self, existing=self._config.variables[index])
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if dlg.was_deleted:
            self.edit_starting.emit(f"Delete variable {name}")
            del self._config.variables[index]
            self._changed()
            return
        decl = dlg.result_model()
        self.edit_starting.emit(f"Rename variable {name}" if decl.name != name else f"Edit variable {name}")
        if decl.name != name:
            variables.rename(self._config, name, decl.name)
        self._config.variables[index] = decl
        self._changed()

    # ------------------------------------------------------------------

    def _changed(self) -> None:
        self._rebuild()
        self.variables_changed.emit()

    def _rebuild(self) -> None:
        for row in self._rows.values():
            discard_widget(row, self._rows_layout)
        self._rows.clear()
        self._edit_btns.clear()
        self._descriptions.clear()
        decls = list(self._config.variables) if self._config is not None else []
        self._empty_lbl.setVisible(not decls)
        for decl in decls:
            if decl.name in self._rows:
                continue  # a second declaration of one name: validation reports it, and Edit reaches the first
            self._rows[decl.name] = self._build_row(decl)

    def _build_row(self, decl: VariableModel) -> QWidget:
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(2)
        head = QHBoxLayout()
        body_layout.addLayout(head)
        name_lbl = QLabel(decl.name)
        name_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        name_font = QFont(name_lbl.font())
        name_font.setBold(True)
        name_lbl.setFont(name_font)
        caption = ThemedTextLabel(describe_declaration(decl), color="text_muted")
        caption.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(name_lbl, 1)
        head.addWidget(caption)
        if decl.type == "enum" and decl.values:
            values = ThemedTextLabel(", ".join(decl.values), color="text_muted")
            values.setWordWrap(True)
            body_layout.addWidget(values)

        description: QWidget | None = None
        if decl.description:
            description = ThemedTextLabel(decl.description)
            description.setWordWrap(True)
            body_layout.addWidget(description)
            self._descriptions[decl.name] = description

        edit_btn = _EditButton(decl.name)
        edit_btn.clicked.connect(lambda _checked, n=decl.name: self.edit_variable(n))
        self._edit_btns[decl.name] = edit_btn
        help_btn = HelpToggle()
        row = build_gutter_row(OverrideBar(), edit_btn, help_btn, body)
        self._rows_layout.addWidget(row)

        # Visibility only now that the row is parented: see gui/CLAUDE.md.
        edit_btn.setVisible(self._editable)
        if description is None:
            keep_slot_when_hidden(help_btn)
        else:
            description.setVisible(False)
            help_btn.toggled.connect(description.setVisible)
        return row
