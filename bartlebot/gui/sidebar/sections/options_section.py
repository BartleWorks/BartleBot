"""Sidebar section: bot-wide options (add / edit / delete)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import BotOptionModel
from bartlebot.bot_options import compose_options
from bartlebot.gui.sidebar.sidebar_section import SidebarSection, field_label
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.player_list_widget import PlayerListWidget
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.nodes.registry import NODE_SPECS

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_PLACEHOLDER = "—"


# ---------------------------------------------------------------------------
# Add / Edit dialog
# ---------------------------------------------------------------------------


class _AddOptionDialog(ThemedMixin, QDialog):
    """Dialog for adding or editing a bot-wide boolean option.

    Pass ``existing_option`` to open in edit mode (pre-populated fields,
    "Save" button, name uniqueness check excludes the original name).
    """

    def __init__(
        self,
        existing_names: list[str],
        parent=None,
        existing_option: BotOptionModel | None = None,
    ):
        super().__init__(parent)
        edit_mode = existing_option is not None
        self.setWindowTitle("Edit Option" if edit_mode else "Add Option")
        self.setModal(True)
        self.setMinimumWidth(320)
        self._existing_names = [n for n in existing_names if n != existing_option.name] if edit_mode else existing_names
        self._result: BotOptionModel | None = None

        form = QFormLayout()
        form.setSpacing(6)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. repeat")

        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText("Optional description")

        default_widget = QWidget()
        default_layout = QHBoxLayout(default_widget)
        default_layout.setContentsMargins(0, 0, 0, 0)
        default_layout.setSpacing(12)
        self._bool_true = QRadioButton("True")
        self._bool_false = QRadioButton("False")
        self._bool_group = QButtonGroup(self)
        self._bool_group.addButton(self._bool_true)
        self._bool_group.addButton(self._bool_false)
        default_layout.addWidget(self._bool_true)
        default_layout.addWidget(self._bool_false)
        default_layout.addStretch()

        form.addRow("Name", self._name_edit)
        form.addRow("Description", self._desc_edit)
        form.addRow("Default", default_widget)

        self._err_lbl = QLabel()
        self._err_lbl.setWordWrap(True)
        self._err_lbl.setVisible(False)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Save" if edit_mode else "Add")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_ok)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)

        outer = QVBoxLayout(self)
        outer.setSpacing(8)
        outer.addLayout(form)
        outer.addWidget(self._err_lbl)
        outer.addLayout(btn_row)

        if edit_mode:
            self._name_edit.setText(existing_option.name)
            self._desc_edit.setText(existing_option.description or "")
            if bool(existing_option.default):
                self._bool_true.setChecked(True)
            else:
                self._bool_false.setChecked(True)
        else:
            self._bool_false.setChecked(True)

        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self._err_lbl.setStyleSheet(f"color: {THEME.error}; font-size: 11px;")

    def get_option(self) -> BotOptionModel:
        assert self._result is not None
        return self._result

    def _on_ok(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            self._show_error("Name is required.")
            return
        if name in self._existing_names:
            self._show_error(f"An option named '{name}' already exists.")
            return
        self._result = BotOptionModel(
            name=name,
            type="bool",
            default=self._bool_true.isChecked(),
            description=self._desc_edit.text().strip() or None,
        )
        self.accept()

    def _show_error(self, msg: str) -> None:
        self._err_lbl.setText(msg)
        self._err_lbl.setVisible(True)


# ---------------------------------------------------------------------------
# Options section
# ---------------------------------------------------------------------------


class OptionsSection(SidebarSection):
    """Collapsible 'Options' section: per-option value widgets + CRUD controls."""

    options_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Options", parent)
        self._config: BotConfig | None = None
        self._suppress: bool = False
        self._editable: bool = False
        self._option_rows: list[QWidget] = []
        self._option_widgets: dict[str, QWidget] = {}
        self._edit_btns: list[QPushButton] = []
        self._delete_btns: list[QPushButton] = []
        self._get_clients: Callable[[], list] = lambda: []

        outer = QVBoxLayout()
        outer.setSpacing(4)

        self._options_layout = QVBoxLayout()
        self._options_layout.setSpacing(4)

        self._no_options_lbl = QLabel(_PLACEHOLDER)
        self._no_options_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._options_layout.addWidget(self._no_options_lbl)

        self._add_option_btn = QPushButton("+ Add Option")
        self._add_option_btn.setVisible(False)
        self._add_option_btn.clicked.connect(self._on_add_option)

        outer.addLayout(self._options_layout)
        outer.addWidget(self._add_option_btn)

        self.set_content_layout(outer)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self._rebuild_options(self._composed())

    @property
    def option_widgets(self) -> dict[str, QWidget]:
        """``{option name: value widget}`` for the rows currently shown.

        The rows are built from the composed list, so this is the only way to ask
        what a bot's options are once they stopped being in its file.
        """
        return dict(self._option_widgets)

    def _composed(self) -> list:
        """What to show: the file's own options plus the ones the graph derives.

        A derived option is not in the file, so this is the only place its name,
        type and description come from. The rows built from it are transient --
        editing one materialises it into the file through ``_declaration_for``.
        """
        if self._config is None:
            return []
        return compose_options(self._config)

    def _declaration_for(self, opt) -> BotOptionModel:
        """The file's own model for *opt*, adding it to the file if it is derived.

        An edit here is an edit to the *declaration* -- the author's recommended
        value -- and a declaration has to be in the file to exist at all. The
        save-time prune drops it again if it ends up agreeing with the NodeSpec,
        so materialising costs nothing when the user changes their mind back.
        """
        assert self._config is not None
        for existing in self._config.options:
            if existing.name == opt.name:
                return existing
        self._config.options.append(opt)
        return opt

    def set_clients_getter(self, fn: Callable[[], list]) -> None:
        """Pass a callable returning the currently hooked clients."""
        self._get_clients = fn
        self.refresh_player_availability()

    def refresh_player_availability(self) -> None:
        """Re-grey the player checkboxes — call when clients connect or disconnect."""
        connected = len(self._get_clients())
        for w in self.findChildren(PlayerListWidget):
            w.set_available_players(connected)

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._add_option_btn.setVisible(editable)
        for btn in self._edit_btns:
            btn.setVisible(editable)
        for btn in self._delete_btns:
            btn.setVisible(editable)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rebuild_options(self, options: list) -> None:
        for row in self._option_rows:
            self._options_layout.removeWidget(row)
            row.deleteLater()
        self._option_rows.clear()
        self._option_widgets.clear()
        self._edit_btns.clear()
        self._delete_btns.clear()

        if not options:
            self._no_options_lbl.show()
            return

        # Compute which option names are locked by at least one node in the bot.
        required_names: set[str] = set()
        if self._config is not None:
            for state in self._config.states:
                spec = NODE_SPECS.get(state.node_type)
                if spec:
                    for req in spec.required_bot_options:
                        required_names.add(req.name)

        self._no_options_lbl.hide()
        for opt in options:
            inner, widget, desc_lbl = _build_option_parts(opt, len(self._get_clients()))
            self._connect_option_widget(widget, opt)

            is_system = opt.name in required_names

            if is_system:
                if desc_lbl is not None:
                    inner.layout().addWidget(desc_lbl)
                outer = inner
            else:
                edit_btn = QPushButton("Edit")
                edit_btn.setToolTip(f"Edit option '{opt.name}'")
                edit_btn.clicked.connect(lambda _checked, name=opt.name: self._on_edit_option(name))

                del_btn = QPushButton("Delete")
                del_btn.setToolTip(f"Remove option '{opt.name}'")
                del_btn.clicked.connect(lambda _checked, name=opt.name: self._on_delete_option(name))

                # Buttons are injected into the value row so they sit on the same
                # horizontal line as the value widget, not beside the description text.
                # Set visibility AFTER parenting — calling setVisible on a parentless
                # widget makes Qt show it as a top-level window (causes a visible flash).
                outer = _build_option_row_with_buttons(inner, widget, edit_btn, del_btn)
                if desc_lbl is not None:
                    inner.layout().addWidget(desc_lbl)
                edit_btn.setVisible(self._editable)
                del_btn.setVisible(self._editable)
                self._edit_btns.append(edit_btn)
                self._delete_btns.append(del_btn)

            self._options_layout.addWidget(outer)
            self._option_rows.append(outer)
            self._option_widgets[opt.name] = widget

    def _on_add_option(self) -> None:
        if self._config is None:
            return
        existing = [o.name for o in self._composed()]
        dlg = _AddOptionDialog(existing, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._config.options.append(dlg.get_option())
            self._rebuild_options(self._composed())
            self.options_changed.emit()

    def _on_edit_option(self, name: str) -> None:
        if self._config is None:
            return
        original = next((o for o in self._config.options if o.name == name), None)
        if original is None:
            return
        existing = [o.name for o in self._composed()]
        dlg = _AddOptionDialog(existing, parent=self, existing_option=original)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dlg.get_option()
        if updated.name != name:
            for t in self._config.transitions:
                for fc in t.flags:
                    if fc.option == name:
                        fc.option = updated.name
        idx = next(i for i, o in enumerate(self._config.options) if o.name == name)
        self._config.options[idx] = updated
        self._rebuild_options(self._composed())
        self.options_changed.emit()

    def _on_delete_option(self, name: str) -> None:
        if self._config is None:
            return
        self._config.options = [o for o in self._config.options if o.name != name]
        for t in self._config.transitions:
            t.flags = [fc for fc in t.flags if fc.option != name]
        self._rebuild_options(self._composed())
        self.options_changed.emit()

    def _connect_option_widget(self, widget: QWidget, opt) -> None:
        def _update(value) -> None:
            if self._suppress or self._config is None:
                return
            self._declaration_for(opt).default = value
            self.options_changed.emit()

        if isinstance(widget, QCheckBox):
            widget.toggled.connect(_update)
        elif isinstance(widget, QSpinBox):
            widget.valueChanged.connect(_update)
        elif isinstance(widget, QDoubleSpinBox):
            widget.valueChanged.connect(_update)
        elif isinstance(widget, PlayerListWidget):
            widget.changed.connect(lambda: _update(widget.get_value()))
        elif isinstance(widget, QLineEdit):
            widget.editingFinished.connect(lambda: _update(widget.text()))


# ---------------------------------------------------------------------------
# Option row builder
# ---------------------------------------------------------------------------


def _build_option_row(opt, connected: int = 4) -> tuple[QWidget, QWidget]:
    """Return (container_widget, input_widget) for a single BotOptionModel."""
    row, widget, _desc = _build_option_parts(opt, connected)
    return row, widget


def _build_option_parts(opt, connected: int = 4) -> tuple[QWidget, QWidget, QLabel | None]:
    """Return (container_widget, input_widget, description_label) for a BotOptionModel.

    *connected* is how many clients are hooked right now; it only greys the player
    checkboxes that have nobody behind them.

    The container holds the label and value widget only (no description). The
    description label is returned separately so callers can place it after
    injecting buttons into the value row.
    """
    row = QWidget()
    layout = QVBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(1)
    tooltip = opt.description or ""
    # None is a declaration meaning "the author has no opinion" -- there is still
    # a widget to fill, and every type has an empty-handed value to fill it with.
    value = opt.default

    if opt.type == "bool":
        widget = QCheckBox(opt.name)
        widget.setChecked(bool(value))
        if tooltip:
            widget.setToolTip(tooltip)
        layout.addWidget(widget)
    else:
        lbl = field_label(opt.name)
        if tooltip:
            lbl.setToolTip(tooltip)
        layout.addWidget(lbl)

        if opt.type == "int":
            widget = QSpinBox()
            widget.setRange(-2_147_483_648, 2_147_483_647)
            widget.setValue(int(value or 0))
        elif opt.type == "float":
            widget = QDoubleSpinBox()
            widget.setDecimals(4)
            widget.setRange(-1e12, 1e12)
            widget.setValue(float(value or 0.0))
        elif opt.type == "player_list":
            widget = PlayerListWidget()
            widget.set_available_players(connected)
            widget.set_value(value if isinstance(value, list) else [])
        else:  # str
            widget = QLineEdit("" if value is None else str(value))

        if tooltip:
            widget.setToolTip(tooltip)
        layout.addWidget(widget)

    desc_lbl: QLabel | None = None
    if opt.description:
        # Themed, for the same reason field_label is: this is handed back to a
        # caller that has nothing to restyle it with.
        desc_lbl = ThemedTextLabel(opt.description, css="font-size: 9px;")
        desc_lbl.setWordWrap(True)

    return row, widget, desc_lbl


def _build_option_row_with_buttons(
    inner: QWidget,
    value_widget: QWidget,
    edit_btn: QPushButton,
    del_btn: QPushButton,
) -> QWidget:
    """Wrap *inner* so Edit/Delete sit on the same row as the value widget.

    *inner* already contains the label and value widget stacked vertically
    (produced by ``_build_option_parts``).  We pull the value widget out of
    that container, replace it with an HBox (value + buttons), then re-attach
    the description label (if any) below.
    """
    inner_layout = inner.layout()

    # Find and remove the value widget from inner's layout so we can re-host it.
    value_index = inner_layout.indexOf(value_widget)
    inner_layout.removeWidget(value_widget)
    value_widget.setParent(None)  # type: ignore[arg-type]

    # Build the replacement row: value widget + Edit + Delete.
    value_row = QWidget()
    value_row_layout = QHBoxLayout(value_row)
    value_row_layout.setContentsMargins(0, 0, 0, 0)
    value_row_layout.setSpacing(4)
    value_row_layout.addWidget(value_widget, 1)
    value_row_layout.addWidget(edit_btn, 0)
    value_row_layout.addWidget(del_btn, 0)

    inner_layout.insertWidget(value_index, value_row)

    return inner
