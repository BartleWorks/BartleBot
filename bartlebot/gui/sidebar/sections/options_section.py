"""Sidebar section: the *declaration* half of a bot's options.

This is the author's surface: what options exist, what type each is, what the
author recommends, and what the tooltip says.  Editing a default here changes the
**recommendation**, not the user's value -- that lives in ``settings.json`` and is
edited in ``setup_section``, which is the other half of the split.

Only hand-added options are editable.  An option a node requires is derived from
its ``NodeSpec`` on every load (see ``bot_options.compose_options``), so renaming
or deleting one here would be undone the moment the file was re-opened; those
rows carry no Edit or Delete button.  Changing a derived option's *default* is
allowed, and materialises the declaration into the file through
``_declaration_for``.

Both fixups an option name needs on a rename are done here, because this is the
only place a name changes: the two transition flag forms, and the override store.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import BotOptionModel
from bartlebot.bot_options import compose_options
from bartlebot.gui.sidebar.sidebar_section import SidebarSection, field_label
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.option_widgets import (
    OPTION_TYPES,
    TYPE_LABELS,
    connect_widget,
    make_option_widget,
    set_widget_value,
    widget_value,
)
from bartlebot.gui.widgets.player_list_widget import PlayerListWidget
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.nodes.registry import NODE_SPECS
from bartlebot.settings_manager import SettingsManager

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

logger = logging.getLogger(__name__)

_PLACEHOLDER = "—"


# ---------------------------------------------------------------------------
# Add / Edit dialog
# ---------------------------------------------------------------------------


class _AddOptionDialog(ThemedMixin, QDialog):
    """Dialog for adding or editing one bot-wide option declaration.

    Pass ``existing_option`` to open in edit mode (pre-populated fields,
    "Save" button, name uniqueness check excludes the original name).

    The type picker is what makes this dialog usable on anything but a checkbox.
    It used to hardcode ``type="bool"``, so pressing Edit on a ``float`` such as
    Safety Delay silently rewrote it as a bool -- a one-click way to break a bot.
    The default control swaps with the type, through the same builder both option
    surfaces use.
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

        self._type_combo = QComboBox()
        for opt_type in OPTION_TYPES:
            self._type_combo.addItem(TYPE_LABELS[opt_type], opt_type)

        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText("Optional description")

        # One page per type, built up front: swapping the visible page is what
        # keeps the value the user already typed for a type they switch back to.
        self._default_stack = QStackedWidget()
        self._default_widgets: dict[str, QWidget] = {}
        for opt_type in OPTION_TYPES:
            widget = make_option_widget(opt_type, None, connected=4, label="True")
            self._default_widgets[opt_type] = widget
            page = QWidget()
            page_layout = QHBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.addWidget(widget)
            self._default_stack.addWidget(page)
        self._type_combo.currentIndexChanged.connect(self._default_stack.setCurrentIndex)

        # A declaration of None is the author saying "I have no opinion", which is
        # what makes the app setting below it reachable at all -- without a way to
        # express it here, every hand-added option would outrank the user's setting
        # forever.  It reads as a checkbox because that is the question it asks.
        self._no_opinion = QCheckBox("No recommendation — use the app setting or the node's own default")
        self._no_opinion.toggled.connect(lambda on: self._default_stack.setDisabled(on))

        form.addRow("Name", self._name_edit)
        form.addRow("Type", self._type_combo)
        form.addRow("Description", self._desc_edit)
        form.addRow("Default", self._default_stack)
        form.addRow("", self._no_opinion)

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
            self._select_type(existing_option.type)
            if existing_option.default is None:
                self._no_opinion.setChecked(True)
            else:
                set_widget_value(self._default_widgets[self._current_type()], existing_option.default)
        else:
            self._select_type("bool")

        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self._err_lbl.setStyleSheet(f"color: {THEME.error}; font-size: 11px;")

    def get_option(self) -> BotOptionModel:
        assert self._result is not None
        return self._result

    def _current_type(self) -> str:
        return self._type_combo.currentData() or "bool"

    def _select_type(self, opt_type: str) -> None:
        index = self._type_combo.findData(opt_type)
        # An option declaring a type this build does not know still has to be
        # editable; falling back to the first entry beats refusing to open.
        self._type_combo.setCurrentIndex(index if index >= 0 else 0)
        self._default_stack.setCurrentIndex(self._type_combo.currentIndex())

    def _on_ok(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            self._show_error("Name is required.")
            return
        if name in self._existing_names:
            self._show_error(f"An option named '{name}' already exists.")
            return
        opt_type = self._current_type()
        self._result = BotOptionModel(
            name=name,
            type=opt_type,
            default=None if self._no_opinion.isChecked() else widget_value(self._default_widgets[opt_type]),
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
    """Collapsible 'Options' section: per-option declaration widgets + CRUD controls."""

    options_changed = pyqtSignal()

    def __init__(self, parent=None, settings: SettingsManager | None = None):
        super().__init__("Options", parent)
        self._config: BotConfig | None = None
        self._settings = settings
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
        """``{option name: default widget}`` for the rows currently shown.

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

    def _sm(self) -> SettingsManager:
        """The settings store, resolved late so a test can inject its own."""
        if self._settings is None:
            self._settings = SettingsManager.instance()
        return self._settings

    def _required_names(self) -> set[str]:
        """Option names locked by at least one node currently in the bot."""
        names: set[str] = set()
        if self._config is None:
            return names
        for state in self._config.states:
            spec = NODE_SPECS.get(state.node_type)
            if spec is None:
                continue
            for req in spec.required_bot_options:
                names.add(req.name)
        return names

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

        required_names = self._required_names()
        connected = len(self._get_clients())

        self._no_options_lbl.hide()
        for opt in options:
            inner, widget, desc_lbl = _build_option_parts(opt, connected)
            self._connect_option_widget(widget, opt)

            # A derived option is re-composed from its NodeSpec on every load, so a
            # rename or a delete here would not survive the round trip.  Its default
            # is still the author's to change.
            if opt.name in required_names:
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
            self._rename_option(name, updated.name)
        idx = next(i for i, o in enumerate(self._config.options) if o.name == name)
        self._config.options[idx] = updated
        self._rebuild_options(self._composed())
        self.options_changed.emit()

    def _on_delete_option(self, name: str) -> None:
        if self._config is None:
            return
        self._config.options = [o for o in self._config.options if o.name != name]
        for trans in self._config.transitions:
            trans.flags = [fc for fc in trans.flags if fc.option != name]
            for group in trans.flag_groups:
                group.flags = [fc for fc in group.flags if fc.option != name]
            trans.flag_groups = [g for g in trans.flag_groups if g.flags]
        self._drop_override(name)
        self._rebuild_options(self._composed())
        self.options_changed.emit()

    def _rename_option(self, old_name: str, new_name: str) -> None:
        """Carry everything keyed on an option's name across the rename.

        Three things are, and all three were missed before: the flat ``flags``
        list, the grouped ``flag_groups`` the edge editor actually writes -- which
        take precedence at run time, so guarding on the stale name silently
        evaluated ``False`` -- and the user's stored value.
        """
        assert self._config is not None
        for trans in self._config.transitions:
            for fc in trans.flags:
                if fc.option == old_name:
                    fc.option = new_name
            for group in trans.flag_groups:
                for fc in group.flags:
                    if fc.option == old_name:
                        fc.option = new_name
        try:
            self._sm().rename_option_override(self._config.id, old_name, new_name)
        except Exception:
            # A lost override is a value the user has to re-enter, not a reason to
            # abandon a rename the graph has already taken.
            logger.exception("Failed to carry your value for option %r across a rename", old_name)

    def _drop_override(self, name: str) -> None:
        assert self._config is not None
        try:
            self._sm().drop_option_override(self._config.id, name)
        except Exception:
            logger.exception("Failed to forget your value for deleted option %r", name)

    def _connect_option_widget(self, widget: QWidget, opt) -> None:
        def _update(value) -> None:
            if self._config is None:
                return
            self._declaration_for(opt).default = value
            self.options_changed.emit()

        connect_widget(widget, _update)


# ---------------------------------------------------------------------------
# Option row builder
# ---------------------------------------------------------------------------


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
    widget = make_option_widget(opt.type, opt.default, connected=connected, tooltip=tooltip, label=opt.name)
    if opt.type != "bool":
        lbl = field_label(opt.name)
        if tooltip:
            lbl.setToolTip(tooltip)
        layout.addWidget(lbl)
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
