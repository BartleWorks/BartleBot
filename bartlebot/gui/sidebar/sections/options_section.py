"""Sidebar section: the *declaration* half of a bot's options.

This is the author's surface: what options exist, what type each is, what the
author recommends, and what the tooltip says.  Editing a default here changes the
**recommendation**, not the user's value -- that lives in ``settings.json`` and is
edited in ``option_values_section``, which is the other half of the split.

Only hand-added options are editable.  An option a node requires is derived from
its ``NodeSpec`` on every load (see ``bot_options.compose_options``), so renaming
or deleting one here would be undone the moment the file was re-opened; those
rows carry no Edit or Delete button.  Changing a derived option's *default* is
allowed, and materialises the declaration into the file through
``_declaration_for``.

Both fixups an option name needs on a rename are done here, because this is the
only place a name changes: the two transition flag forms, and the override store.

The rows sit in the same folding cards as Option Values's (#138), folded the same way -- the
answer is one, remembered for every bot and shared by both tabs -- so a mode switch
does not move a row to a different place. Quicksell is the user's alone and is
not on this surface at all, so the Quicksell card here has no header switch; a card
with nothing left on it, General usually, is left out. Nothing here is ever the
user's value, so no card's stripe is lit, and a folded card summarizes the
*declarations*.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import BotOptionModel
from bartlebot.gui.sidebar.option_card import CardFolds, OptionCard
from bartlebot.gui.sidebar.option_gutter import (
    GUTTER_BTN_PX,
    DescriptionSwitches,
    HelpToggle,
    OverrideBar,
    build_gutter_row,
    keep_slot_when_hidden,
)
from bartlebot.gui.sidebar.sidebar_section import SidebarSection, field_label
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.theming.themed import ThemedMixin
from bartlebot.gui.widgets.buttons.action_button import ActionButton, ActionStyle
from bartlebot.gui.widgets.buttons.icon_button import IconButton, IconTint
from bartlebot.gui.widgets.inputs.growing_text_edit import GrowingTextEdit
from bartlebot.gui.widgets.inputs.option_widgets import (
    OPTION_TYPES,
    TALL_TYPES,
    TYPE_LABELS,
    block_separate_if_merging,
    connect_widget,
    empty_value,
    make_option_widget,
    set_widget_value,
    widget_value,
)
from bartlebot.gui.widgets.labels.themed_label import ThemedTextLabel
from bartlebot.gui.widgets.layout.dialog_footer import dialog_footer
from bartlebot.gui.widgets.pickers.player_list_widget import PlayerListWidget
from bartlebot.options import option_groups
from bartlebot.options.bot_options import (
    USER_OWNED_OPTIONS,
    compose_options,
    is_user_owned,
    option_specs_for,
    required_option_specs,
)
from bartlebot.settings_manager import SettingsManager

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

logger = logging.getLogger(__name__)

_PLACEHOLDER = "—"

# Measured over the 80 shipped bots.  The 22 hand-added descriptions -- the only
# ones this dialog ever loads, since a derived option has no Edit button -- run to
# a median of 75 characters and a maximum of 164; the node-declared prose the same
# field would have to hold if that ever changed runs to 299.  A one-line QLineEdit
# at the old 320px minimum showed roughly 45.  So not one description in the
# library fitted, and the median was over twice the visible width.
#
# At _DIALOG_MIN_W the field takes about 65 characters a line, which puts the
# worst stored description at three lines and the worst node-declared one at five.
# The floor is the ``description_section`` floor and for the same reason: the empty
# field is where an author starts, and two lines says "prose goes here" where one
# says "a name goes here".
_DIALOG_MIN_W = 420
_DESC_MIN_LINES = 2
_DESC_MAX_LINES = 6


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

    **Delete lives here**, in edit mode only, and it is the reason the row gutter
    can be one column wide.  Option Values's gutter puts the irreversible action furthest
    from the text because reverting has no undo; deleting an option is strictly
    worse -- it takes the declaration, both transition flag forms, and the user's
    stored value -- so a 20px icon-only target a few pixels from the ``?`` was the
    wrong home for it.  Behind Edit it gets a labeled button, a confirmation, and
    a Cancel, and the gutter is left holding one thing.
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
        self.setMinimumWidth(_DIALOG_MIN_W)
        self._existing_names = [n for n in existing_names if n != existing_option.name] if edit_mode else existing_names
        self._result: BotOptionModel | None = None
        # A declaration of None means "the author has no opinion", which is only
        # meaningful under an option that has an app setting or a NodeSpec default
        # beneath it -- and a hand-added option has neither, so a null there
        # resolves to None and `_flag_<name>` silently reads False.  So this dialog
        # never *creates* one; it only declines to destroy one it was handed.
        self._was_null = edit_mode and existing_option.default is None
        self._default_touched = False
        self._delete_btn: ActionButton | None = None
        self._deleted = False

        form = QFormLayout()
        form.setSpacing(6)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. repeat")

        self._type_combo = QComboBox()
        for opt_type in OPTION_TYPES:
            self._type_combo.addItem(TYPE_LABELS[opt_type], opt_type)

        # Prose, not a name: see _DESC_* above for what the shipped library needs.
        self._desc_edit = GrowingTextEdit(min_lines=_DESC_MIN_LINES, max_lines=_DESC_MAX_LINES)
        # On the widget, not in the stylesheet: GrowingTextEdit measures its own line
        # height from ``QWidget.font()``, which a stylesheet ``font-size`` never reaches.
        desc_font = QFont(self._desc_edit.font())
        desc_font.setPixelSize(THEME.font_small_px)
        self._desc_edit.setFont(desc_font)
        self._desc_edit.setPlaceholderText("Optional description")

        # One page per type, built up front: swapping the visible page is what
        # keeps the value the user already typed for a type they switch back to.
        # The bool page states both answers rather than offering a lone tick box,
        # because here the field is captioned "Default" rather than by the option's
        # own name, and "[ ] True" reads as though False were unavailable.
        self._default_stack = QStackedWidget()
        self._default_widgets: dict[str, QWidget] = {}
        for opt_type in OPTION_TYPES:
            widget = make_option_widget(opt_type, None, connected=4, explicit_bool=True)
            connect_widget(widget, lambda _v: setattr(self, "_default_touched", True))
            self._default_widgets[opt_type] = widget
            page = QWidget()
            page_layout = QHBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.addWidget(widget)
            self._default_stack.addWidget(page)
        self._type_combo.currentIndexChanged.connect(self._default_stack.setCurrentIndex)

        form.addRow("Name", self._name_edit)
        form.addRow("Type", self._type_combo)
        form.addRow("Description", self._desc_edit)
        form.addRow("Default", self._default_stack)

        self._err_lbl = QLabel()
        self._err_lbl.setWordWrap(True)
        self._err_lbl.setVisible(False)

        # Left of the stretch, away from Save: the destructive action does not sit
        # next to the one the Return key presses. CAUTION says the same thing in color —
        # deleting an option takes every bot file's value for it with it.
        extras = []
        if edit_mode:
            self._delete_btn = ActionButton("trash", "Delete", style=ActionStyle.CAUTION, fill=False)
            self._delete_btn.setToolTip(f"Remove the option '{existing_option.name}' from this bot")
            self._delete_btn.clicked.connect(self._on_delete)
            extras.append(self._delete_btn)
        btn_row = dialog_footer(
            self,
            commit_text="Save" if edit_mode else "Add",
            on_commit=self._on_ok,
            extras=extras,
        ).layout

        outer = QVBoxLayout(self)
        outer.setSpacing(8)
        outer.addLayout(form)
        outer.addWidget(self._err_lbl)
        outer.addLayout(btn_row)

        if edit_mode:
            self._name_edit.setText(existing_option.name)
            self._desc_edit.setPlainText(existing_option.description or "")
            self._select_type(existing_option.type)
            set_widget_value(
                self._default_widgets[self._current_type()],
                empty_value(existing_option.type) if existing_option.default is None else existing_option.default,
            )
            if self._was_null and existing_option.type == "bool":
                # The radio pair can show "neither", so a null bool is visible as
                # well as preserved.  No other type has a spare state to show it in.
                set_widget_value(self._default_widgets[self._current_type()], None)
        else:
            self._select_type("bool")

        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self._err_lbl.setStyleSheet(f"color: {THEME.error}; font-size: {THEME.font_small_px}px;")
        # The app stylesheet's input rule covers QLineEdit and QComboBox, not
        # QTextEdit, so this field drew Fusion's own derived frame beside two
        # neighbors drawing the real token.  Same treatment description_section
        # gives its editor, and no font-size: that is set on the widget, which is
        # the only place GrowingTextEdit's line-height measurement reads it from.
        self._desc_edit.setStyleSheet(
            f"QTextEdit {{ background: {THEME.surface}; border: 1px solid {THEME.border}; "
            f"border-radius: 4px; color: {THEME.text}; padding: 3px; }}"
        )

    def get_option(self) -> BotOptionModel:
        assert self._result is not None
        return self._result

    @property
    def was_deleted(self) -> bool:
        """Whether the user chose Delete rather than Save.

        The dialog still accepts, so the caller reads this before ``get_option``:
        an accepted dialog means "the user finished", not "the user saved".
        """
        return self._deleted

    def _on_delete(self) -> None:
        """Confirm, then accept with ``was_deleted`` set.

        Deleting takes three things -- the declaration, both transition flag forms
        that guard on its name, and the user's stored value -- and none of them
        comes back, so this is the one action in the dialog that asks first.
        """
        name = self._name_edit.text().strip() or "this option"
        confirm = QMessageBox(self)
        confirm.setWindowTitle("Delete Option")
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setText(f"Delete the option '{name}'?")
        confirm.setInformativeText(
            "Any transition guarding on it will stop taking that edge, and your stored value for it is forgotten. "
            "This cannot be undone."
        )
        confirm.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return
        self._deleted = True
        self.accept()

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
        value = widget_value(self._default_widgets[opt_type])
        if self._was_null and not self._default_touched:
            # Opening Edit to fix a typo in a description must not quietly promote a
            # null declaration to whatever the control had to show in its place.
            value = None
        self._result = BotOptionModel(
            name=name,
            type=opt_type,
            default=value,
            description=self._desc_edit.toPlainText().strip() or None,
        )
        self.accept()

    def _show_error(self, msg: str) -> None:
        self._err_lbl.setText(msg)
        self._err_lbl.setVisible(True)


class _EditButton(IconButton):
    """Opens the declaration dialog for one hand-added option.

    It takes the slot Option Values gives revert, which is what keeps the two surfaces'
    ``?`` columns at the same x.  Delete is *not* beside it: see
    ``_AddOptionDialog``, which owns that action now.

    Icon-only and accented, like revert, and for the same reason -- it is the one
    action a row can take, so its being there at all is the signal.  The tooltip
    names the option, because at 20px the glyph cannot.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "pencil",
            tooltip="Edit this option's declaration",
            size=GUTTER_BTN_PX,
            icon_size=14,
            tint=IconTint.ACCENT_WHEN_ENABLED,
            parent=parent,
        )


@dataclass
class _RowChrome:
    """The parts of one row that a later click has to reach."""

    widget: QWidget
    edit_btn: _EditButton | None
    help_btn: HelpToggle
    description: QWidget | None


# ---------------------------------------------------------------------------
# Options section
# ---------------------------------------------------------------------------


class OptionsSection(SidebarSection):
    """Collapsible 'Options' section: per-option declaration widgets + CRUD controls."""

    #: The option *set* changed -- one was added, deleted or renamed.  Both
    #: surfaces have to be rebuilt from the recomposed list.
    options_changed = pyqtSignal()
    #: An author changed a *default*, and nothing else.  Kept separate because the
    #: handler for ``options_changed`` rebuilds this very list -- which destroys the
    #: check box the click landed on and rebuilds it under the pointer, and (before
    #: the parenting fix in ``_build_row``) flashed a window per row while doing it.
    #: No name can change here, so there is nothing on this surface to rebuild; the
    #: other one still has to re-resolve, since a declaration outranks the app
    #: setting beneath it.
    defaults_changed = pyqtSignal()

    def __init__(self, parent=None, settings: SettingsManager | None = None):
        super().__init__("Options", parent)
        self._config: BotConfig | None = None
        self._settings = settings
        self._editable: bool = False
        self._option_rows: list[QWidget] = []
        self._cards: dict[str, OptionCard] = {}
        self._groups: list[option_groups.OptionGroup] = []
        self._option_widgets: dict[str, QWidget] = {}
        self._chrome: dict[str, _RowChrome] = {}
        self._edit_btns: list[_EditButton] = []
        self._get_clients: Callable[[], list] = lambda: []
        self._show_descriptions = self._read_show_descriptions()

        outer = QVBoxLayout()
        outer.setSpacing(4)

        self._switches = DescriptionSwitches(self._on_show_all_toggled)
        outer.addWidget(self._switches)

        self._options_layout = QVBoxLayout()
        self._options_layout.setSpacing(6)

        self._no_options_lbl = QLabel(_PLACEHOLDER)
        self._no_options_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._options_layout.addWidget(self._no_options_lbl)

        self._add_option_btn = ActionButton("plus", "Add Option")
        self._add_option_btn.setVisible(False)
        self._add_option_btn.clicked.connect(self._on_add_option)

        outer.addLayout(self._options_layout)
        outer.addWidget(self._add_option_btn)

        self.set_content_layout(outer)
        # The same fold state as Option Values's, and the same heading actions.
        self._folds = CardFolds(self, self._sm)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self._rebuild_options(self._composed())

    @property
    def cards(self) -> dict[str, OptionCard]:
        """``{group id: card}`` for the cards this bot has, in drawing order."""
        return dict(self._cards)

    def set_group_collapsed(self, group: str, collapsed: bool) -> None:
        """Fold or unfold one card, and remember it for every bot and both tabs."""
        self._folds.set_group_collapsed(group, collapsed)

    def collapse_all(self) -> None:
        self._folds.collapse_all()

    def expand_all(self) -> None:
        self._folds.expand_all()

    def card_menu(self, group: str):
        """The menu a right-click on *group*'s header opens, built but not shown."""
        return self._folds.card_menu(group)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Pick up folds made on the Option Values tab while this one was not showing."""
        self._folds.reload()
        super().showEvent(event)

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
        """Re-gray the player checkboxes — call when clients connect or disconnect."""
        connected = len(self._get_clients())
        for w in self.findChildren(PlayerListWidget):
            w.set_available_players(connected)

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._add_option_btn.setVisible(editable)
        for btn in self._edit_btns:
            btn.setVisible(editable)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _drops(self) -> list:
        """This bot's drop table, so the keep list's *recommended* value is edited with
        the same picker the user gets rather than as a comma-separated line."""
        return list(self._config.drops) if self._config is not None else []

    def _sm(self) -> SettingsManager:
        """The settings store, resolved late so a test can inject its own."""
        if self._settings is None:
            self._settings = SettingsManager.instance()
        return self._settings

    def _required_names(self) -> set[str]:
        """Option names locked by at least one node currently in the bot.

        The same walk that composes them, so a conditional option -- Challenge Mode,
        required only by a state that says its dungeon offers it -- is locked exactly
        when it is shown.
        """
        if self._config is None:
            return set()
        return set(required_option_specs(self._config))

    def _rebuild_options(self, options: list) -> None:
        # A value that is the user's by definition has no declaration to edit, so it
        # is not on the author's surface at all -- it is a row in Option Values instead.  A
        # disabled row here would only invite the question this answers.
        options = [o for o in options if not is_user_owned(o.name)]
        for card in self._cards.values():
            self._options_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self._option_rows.clear()
        self._option_widgets.clear()
        self._chrome.clear()
        self._edit_btns.clear()

        self._switches.setVisible(bool(options) and any(o.description for o in options))
        specs = option_specs_for(self._config) if self._config is not None else {}
        self._groups = option_groups.group_options(options, specs, is_tall=lambda o: o.type in TALL_TYPES)
        if not options:
            self._no_options_lbl.show()
            self._folds.set_cards({})
            return

        required_names = self._required_names()
        connected = len(self._get_clients())

        self._no_options_lbl.hide()
        for group in self._groups:
            card = OptionCard(group.id, group.title)
            # A header option is a row here: the switch is how Option Values sets a *value*,
            # and this surface edits declarations, in the same controls as any other.
            for opt in group.options:
                row = self._build_row(opt, connected, derived=opt.name in required_names)
                card.body_layout.addWidget(row)
                self._option_rows.append(row)
            self._options_layout.addWidget(card)
            self._cards[group.id] = card
        self._folds.set_cards(self._cards)
        self._refresh_summaries()

    def _refresh_summaries(self) -> None:
        """Say what each folded card declares -- the author's values, not the user's."""
        for group in self._groups:
            card = self._cards.get(group.id)
            if card is None:
                continue
            values = {opt.name: opt.default for opt in group.options}
            pickers = [
                self._option_widgets[o.name]
                for o in group.options
                if o.type == "drop_keep_list" and hasattr(self._option_widgets.get(o.name), "kept_counts")
            ]
            keep_counts = pickers[0].kept_counts[:2] if pickers else None
            card.set_summary(option_groups.group_summary(group, values, keep_counts=keep_counts))

    def _build_row(self, opt, connected: int, *, derived: bool) -> QWidget:
        """One option's row: the gutter, then the head line, control and prose.

        The same anatomy Option Values uses, because the two tabs are two views of one list
        and a row that changes shape between them reads as a different row.  What
        differs is the middle gutter slot -- Option Values reverts a value, this edits a
        declaration -- and that a *derived* option has nothing to edit at all, since
        it is re-composed from its NodeSpec on every load and a rename or a delete
        would not survive the round trip.  Its default is still the author's to
        change, through the control itself.
        """
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(2)

        widget = make_option_widget(
            opt.type,
            opt.default,
            connected=connected,
            tooltip=opt.description or "",
            label=opt.name,
            get_drops=self._drops,
        )
        block_separate_if_merging(widget, self._config)
        is_bool = isinstance(widget, QCheckBox)

        # A bool has no separate label: its own caption is the name, so the checkbox
        # *is* the head line and the whole row fits on it.
        if is_bool:
            body_layout.addWidget(widget)
        else:
            lbl = field_label(opt.name)
            if opt.description:
                # The hover path stays: the ? is the discoverable affordance, not
                # the only one, and a tooltip costs no height.
                lbl.setToolTip(opt.description)
            body_layout.addWidget(lbl)
            body_layout.addWidget(widget)

        description: QWidget | None = None
        if opt.description:
            # At the base font, and folded away until asked for -- the same prose,
            # read the same way, as on the other tab.  Added to the layout *before*
            # its visibility is set: see the note below.
            description = ThemedTextLabel(opt.description)
            description.setWordWrap(True)
            body_layout.addWidget(description)
            description.setVisible(self._show_descriptions)

        self._connect_option_widget(widget, opt)

        # Never painted here: the bar and revert are one affordance in Option Values, and a
        # lone marker with no paired action would be a fact to correlate rather than
        # something to act on.  Its width is what lines the two tabs' ? columns up.
        bar = OverrideBar()

        edit_btn = _EditButton()
        if not derived:
            edit_btn.setToolTip(f"Edit or delete the option '{opt.name}'")
            edit_btn.clicked.connect(lambda _checked, name=opt.name: self._on_edit_option(name))
            self._edit_btns.append(edit_btn)

        help_btn = HelpToggle()
        help_btn.toggled.connect(lambda shown, name=opt.name: self._set_row_description(name, shown))

        self._option_widgets[opt.name] = widget
        self._chrome[opt.name] = _RowChrome(
            widget=widget,
            edit_btn=None if derived else edit_btn,
            help_btn=help_btn,
            description=description,
        )

        row = build_gutter_row(bar, edit_btn, help_btn, body)

        # Visibility is set only now, once ``build_gutter_row`` has parented the
        # chrome.  ``setVisible(True)`` on a widget that has no parent yet makes it
        # a *top-level window*, which Qt maps and unmaps as the row is assembled --
        # a real frame flashing on screen, once per row, on every rebuild.
        if derived:
            keep_slot_when_hidden(edit_btn)
        else:
            edit_btn.setVisible(self._editable)
        if description is None:
            keep_slot_when_hidden(help_btn)
        else:
            help_btn.setChecked(self._show_descriptions)
        return row

    # ------------------------------------------------------------------
    # Descriptions
    # ------------------------------------------------------------------

    @property
    def descriptions_visible(self) -> bool:
        """Whether descriptions are shown for every row by default."""
        return self._show_descriptions

    def set_descriptions_visible(self, shown: bool) -> None:
        """Show or fold every option's description, exactly as the switches would."""
        self._on_show_all_toggled(shown)

    def _read_show_descriptions(self) -> bool:
        try:
            return bool(self._sm().get("general", "show_option_descriptions", default=False))
        except Exception:
            logger.exception("Failed to read whether option descriptions are shown")
            return False

    def _on_show_all_toggled(self, shown: bool) -> None:
        """Set every row at once, and remember the choice.

        The same ``settings.json`` key Option Values writes: folding the prose away is a
        property of the reader, so it should not have to be done twice.
        """
        self._show_descriptions = shown
        try:
            self._sm().set("general", "show_option_descriptions", value=shown)
        except Exception:
            logger.exception("Failed to remember whether option descriptions are shown")
        for chrome in self._chrome.values():
            chrome.help_btn.setChecked(shown)

    def _set_row_description(self, name: str, shown: bool) -> None:
        """Fold one row's description in or out, leaving every other row alone."""
        chrome = self._chrome.get(name)
        if chrome is None or chrome.description is None:
            return
        chrome.description.setVisible(shown)

    def _on_add_option(self) -> None:
        if self._config is None:
            return
        # The user-owned names are reserved as well as taken: a hand-added "Safety
        # Delay" would be a declaration the editor then refuses to show.
        existing = [o.name for o in self._composed()] + sorted(USER_OWNED_OPTIONS)
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
        # An accepted dialog means the user finished, not that they saved: Delete
        # accepts too, so that the row's one button can lead to both outcomes.
        if dlg.was_deleted:
            self._on_delete_option(name)
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
            opt.default = value
            self._refresh_summaries()
            self.defaults_changed.emit()

        connect_widget(widget, _update)
