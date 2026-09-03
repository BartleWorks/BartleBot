"""Sidebar section: the *values* half of a bot's options.

This is the surface a user gets when they are running someone else's bot.  Every
row shows the **effective** value -- what the run will actually use -- and says
where it came from when it did not come from the user.  Editing a row writes an
*override* to ``settings.json`` and never touches the bot file, which is the
whole point of the split: a shared bot stays byte-identical no matter how many
people tune it.

Two markings, both borrowed from the systems that solved this before us:

* an **inherited** caption naming the level that answered -- the safety dialog's
  ``(app setting)`` suffix generalised to all four levels;
* a coloured **left bar** plus a **Reset** button on any row the user has
  overridden, following VS Code's settings gutter and Unity's prefab overrides.

The Reset tooltip names the value a reset would fall back to, which is the free
mitigation for the one accepted cost of storing overrides as bare values: an
author who later improves their declaration is outranked silently, so the
one-way door has to be visibly openable.

Descriptions are folded away behind a ``?`` and shown at the base font when
asked for.  Measured on a shipped bot, node-authored prose was 41% of an
ordinary row's height and the source caption another 17%, leaving the control
itself the minority of its own row -- at 9px, three quarters of the base size,
which is where Windows' subpixel antialiasing starts colouring the strokes.
Every settings surface that shows help inline keeps it to a single line;
the ones whose prose runs this long put it behind an affordance instead.

The declaration editor is the other half -- see ``options_section``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot import bot_options
from bartlebot.bot_options import UNSET, compose_options, is_set, values_agree
from bartlebot.gui.sidebar.sidebar_section import SidebarSection, field_label
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.option_widgets import (
    TALL_TYPES,
    connect_widget,
    make_option_widget,
    set_widget_value,
)
from bartlebot.gui.widgets.player_list_widget import PlayerListWidget
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.settings_manager import SettingsManager

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

logger = logging.getLogger(__name__)

_PLACEHOLDER = "—"
_BAR_WIDTH_PX = 3

# What to print under a row, naming the level that answered.  Every level has an
# entry, including the user's own: a caption that appears and disappears would
# move every row beneath it each time a value was typed.
_SOURCE_CAPTION: dict[str, str] = {
    bot_options.SOURCE_OVERRIDE: "your value",
    bot_options.SOURCE_BOT_FILE: "from the bot",
    bot_options.SOURCE_APP_SETTING: "from the app setting",
    bot_options.SOURCE_NODE_DEFAULT: "from the node default",
}


def _ordered(options: list) -> list:
    """The composed options, with the table-shaped controls moved to the end.

    Order otherwise follows the states that require each option, which is the order
    the bot uses them in.  A 220px drop table sitting between two spin boxes pushes
    everything after it off the bottom of the sidebar, so those go last -- the only
    rearrangement, and it depends on the control rather than on any option's name.
    """
    return [o for o in options if o.type not in TALL_TYPES] + [o for o in options if o.type in TALL_TYPES]


class _OverrideBar(ThemedMixin, QFrame):
    """The gutter marking a row the user has overridden.

    Always present and always the same width: it changes *colour* rather than
    appearing, so a row does not shift sideways the moment a value is typed.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(_BAR_WIDTH_PX)
        self._active = False
        self.refresh_theme_styles()

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        colour = THEME.accent if self._active else "transparent"
        self.setStyleSheet(f"background: {colour}; border: none;")


class _HelpToggle(ThemedMixin, QPushButton):
    """The ``?`` beside an option's name, folding its description in and out.

    Descriptions are node-authored prose -- two or three lines each, and 41% of an
    ordinary row's height with the control itself taking only 42%.  Every settings
    surface that shows help inline keeps it to one line (Chrome, Windows, GitHub);
    the ones whose prose is this long put it behind an affordance instead (Xcode's
    Quick Help, Unreal's property tooltips).  This is that affordance, and it is a
    *button* rather than a hover tooltip because the prose is load-bearing -- the
    only thing that keeps a pet is a sentence in one of these.
    """

    _SIZE_PX = 16

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("?", parent)
        self.setCheckable(True)
        self.setFixedSize(self._SIZE_PX, self._SIZE_PX)
        self.setToolTip("Show what this option does")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        # The node help button in ``param_panel`` is the same affordance one panel
        # over, so it is the same shape and the same accent, only smaller.
        self.setStyleSheet(
            f"QPushButton {{ background: {THEME.surface}; border: 1px solid {THEME.border}; "
            f"border-radius: 3px; color: {THEME.accent}; font-weight: bold; font-size: 10px; padding: 0; }}"
            f"QPushButton:hover, QPushButton:checked {{ background: {THEME.accent}; color: #ffffff; }}"
        )


@dataclass
class _RowChrome:
    """The parts of one row that change when its override state does."""

    widget: QWidget
    bar: _OverrideBar
    reset_btn: QPushButton
    caption: ThemedTextLabel
    description: QWidget | None = None
    help_btn: _HelpToggle | None = None


class SetupSection(SidebarSection):
    """Collapsible 'Setup' section: one row per option, editing the user's value."""

    # Emitted after an override is written or reset.  Deliberately *not* wired to
    # ``config_changed``: an override is not an edit to the bot, and marking the
    # file dirty for one is the confusion this whole split exists to remove.
    values_changed = pyqtSignal()

    def __init__(self, parent=None, settings: SettingsManager | None = None):
        super().__init__("Setup", parent)
        self._config: BotConfig | None = None
        self._settings = settings
        self._rows: list[QWidget] = []
        self._composed: dict[str, object] = {}
        self._chrome: dict[str, _RowChrome] = {}
        self._option_widgets: dict[str, QWidget] = {}
        self._get_clients: Callable[[], list] = lambda: []
        self._show_descriptions = self._read_show_descriptions()

        outer = QVBoxLayout()
        outer.setSpacing(4)

        # One switch for the reader who wants the whole tab as a document, rather
        # than a row at a time.  Off by default, and remembered: it is a property of
        # the person reading, not of the bot.
        self._show_all = QCheckBox("Show descriptions")
        self._show_all.setChecked(self._show_descriptions)
        self._show_all.setToolTip("Show every option's description, instead of one at a time")
        self._show_all.toggled.connect(self._on_show_all_toggled)
        outer.addWidget(self._show_all)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(6)

        self._empty_lbl = QLabel(_PLACEHOLDER)
        self._empty_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._rows_layout.addWidget(self._empty_lbl)

        outer.addLayout(self._rows_layout)
        self.set_content_layout(outer)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self._rebuild()

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
        """Accepted and ignored: this surface has no authoring controls to reveal.

        Editing a *value* is what a user does before pressing Run, so nothing here
        is gated on the canvas being in Edit mode.  The controls that are gated —
        add, edit, delete a declaration — live in ``OptionsSection``.
        """

    @property
    def option_widgets(self) -> dict[str, QWidget]:
        """``{option name: value widget}`` for the rows currently shown."""
        return dict(self._option_widgets)

    @property
    def descriptions_visible(self) -> bool:
        """Whether descriptions are shown for every row by default."""
        return self._show_descriptions

    def set_descriptions_visible(self, shown: bool) -> None:
        """Show or fold every option's description, exactly as the switch would."""
        self._show_all.setChecked(shown)

    def resolution_for(self, name: str) -> bot_options.OptionResolution | None:
        """The effective value of *name* and the level that supplied it."""
        if self._config is None:
            return None
        return bot_options.resolve_for_config(
            self._config,
            name,
            override=self._override(name),
            app_value=self._app_value(name),
        )

    # ------------------------------------------------------------------
    # Settings access
    # ------------------------------------------------------------------

    def _sm(self) -> SettingsManager:
        """The settings store, resolved late so a test can inject its own."""
        if self._settings is None:
            self._settings = SettingsManager.instance()
        return self._settings

    def _override(self, name: str) -> Any:
        if self._config is None:
            return UNSET
        try:
            return self._sm().get_option_override(self._config.id, name)
        except Exception:
            logger.exception("Failed to read the stored value for option %r", name)
            return UNSET

    def _app_value(self, name: str) -> Any:
        key = bot_options.APP_SETTING_KEYS.get(name)
        if key is None:
            return UNSET
        try:
            return self._sm().get("general", key, default=UNSET)
        except Exception:
            logger.exception("Failed to read the app setting behind option %r", name)
            return UNSET

    def _declared_default(self, name: str) -> Any:
        """The bot file's own default for *name*, or ``None`` if it declares none."""
        if self._config is None:
            return None
        for opt in self._config.options:
            if opt.name == name:
                return opt.default
        return None

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------

    def _rebuild(self) -> None:
        """Build the whole list. Only a *different set of options* needs this.

        A value edit refreshes one row in place instead -- rebuilding would destroy
        the control the user is still holding, which for a spin box means the arrow
        they are clicking vanishes from under the pointer on the first click.
        """
        for row in self._rows:
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        self._composed.clear()
        self._option_widgets.clear()
        self._chrome.clear()

        options = _ordered(compose_options(self._config) if self._config is not None else [])
        self._composed = {opt.name: opt for opt in options}
        self._show_all.setVisible(any(opt.description for opt in options))
        if not options:
            self._empty_lbl.show()
            return
        self._empty_lbl.hide()

        connected = len(self._get_clients())
        for opt in options:
            row = self._build_row(opt, connected)
            self._rows_layout.addWidget(row)
            self._rows.append(row)
        self._push_keep_list_context()

    def _build_row(self, opt, connected: int) -> QWidget:
        """One option's row. Every piece of chrome is built once and updated after.

        Nothing here appears or disappears with the override state: the bar keeps
        its width and changes colour, Reset keeps its place and greys out, and the
        caption always names a level. Chrome that comes and goes as you type moves
        everything under it, which on a spin box means the second click of a pair
        lands somewhere else.

        The description is the one exception, and it is not one: it folds on a
        deliberate click of its own ``?``, never on a value edit, and everything it
        moves is below the button that moved it.
        """
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(1)

        resolution = self.resolution_for(opt.name)
        widget = make_option_widget(
            opt.type,
            resolution.value if resolution is not None else opt.default,
            connected=connected,
            tooltip=opt.description or "",
            label=opt.name,
            get_drops=self._drops,
            get_clients=lambda: self._get_clients(),
        )
        tall = opt.type in TALL_TYPES
        is_bool = opt.type == "bool"
        reset_btn = QPushButton("Reset")
        reset_btn.clicked.connect(lambda _checked, name=opt.name: self._on_reset(name))
        caption = ThemedTextLabel("", color="text_disabled", css="font-size: 9px;")

        # The head line carries everything that names the row: what it is called,
        # the affordance for its description, and -- right-aligned -- the level its
        # value came from.  The caption used to have a line of its own beneath the
        # control, which cost every row 12px to say four words.
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(4)
        # A bool has no separate label: its own caption is the name, so the checkbox
        # *is* the head line and the whole row fits on it.
        if is_bool:
            head.addWidget(widget, 0)
        else:
            lbl = field_label(opt.name)
            if opt.description:
                # The hover path stays: the ? is the discoverable affordance, not
                # the only one, and a tooltip costs no height.
                lbl.setToolTip(opt.description)
            head.addWidget(lbl, 0)

        help_btn: _HelpToggle | None = None
        if opt.description:
            help_btn = _HelpToggle()
            help_btn.toggled.connect(lambda shown, name=opt.name: self._set_row_description(name, shown))
            head.addWidget(help_btn, 0)
        head.addStretch(1)
        head.addWidget(caption, 0)
        # Reset joins the head line whenever there is no short control to sit beside:
        # against a 220px table it would otherwise centre on the table's midpoint,
        # floating with nothing to attach it to, and take its width from the last
        # column for that whole height.
        if is_bool or tall:
            head.addWidget(reset_btn, 0)
        body_layout.addLayout(head)

        if tall:
            body_layout.addWidget(widget)
        elif not is_bool:
            value_row = QWidget()
            value_layout = QHBoxLayout(value_row)
            value_layout.setContentsMargins(0, 0, 0, 0)
            value_layout.setSpacing(4)
            value_layout.addWidget(widget, 1)
            value_layout.addWidget(reset_btn, 0)
            body_layout.addWidget(value_row)

        description: QWidget | None = None
        if opt.description:
            # At the base font, not the 9px the captions use: this is the one piece
            # of prose on the surface, and it is only on screen when asked for.
            description = ThemedTextLabel(opt.description)
            description.setWordWrap(True)
            description.setVisible(self._show_descriptions)
            body_layout.addWidget(description)
            if help_btn is not None:
                help_btn.setChecked(self._show_descriptions)

        connect_widget(widget, lambda new_value, name=opt.name: self._on_value_changed(name, new_value))
        self._option_widgets[opt.name] = widget

        # The bar is a sibling of the body, not a border on it: a stylesheet border
        # set on the row would be inherited by every control inside it.
        bar = _OverrideBar()
        outer = QWidget()
        outer_layout = QHBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(5)
        outer_layout.addWidget(bar)
        outer_layout.addWidget(body, 1)

        self._chrome[opt.name] = _RowChrome(
            widget=widget,
            bar=bar,
            reset_btn=reset_btn,
            caption=caption,
            description=description,
            help_btn=help_btn,
        )
        self._refresh_row(opt.name)
        return outer

    def refresh_drops(self) -> None:
        """Re-read the drop table into the keep-list picker, after a Possible Drops edit."""
        for name, widget in self._option_widgets.items():
            if self._composed_type(name) == "drop_keep_list":
                widget.refresh()

    def _drops(self) -> list:
        """This bot's drop table, for the keep-list picker to show."""
        return list(self._config.drops) if self._config is not None else []

    def _push_keep_list_context(self) -> None:
        """Tell the keep-list picker what the sell flags resolved to.

        The picker shows what will *happen* to each drop, and three of the answers
        are not its own: Sell Pets off spares every pet whatever the list says, and
        Sell Socket Wrenches off adds a keyword the user never typed. Those are
        separate rows on this same surface, so flipping one has to reach the table.
        """
        pickers = [w for name, w in self._option_widgets.items() if self._composed_type(name) == "drop_keep_list"]
        if not pickers or self._config is None:
            return
        try:
            from bartlebot.nodes.utility import quicksell  # noqa: PLC0415 - heavy import, only when shown

            context = quicksell.resolve_options_for(self._config, settings=self._settings)
        except Exception:
            logger.exception("Could not resolve the quicksell options behind the keep-list picker")
            return
        for picker in pickers:
            picker.set_context(context)

    def _composed_type(self, name: str) -> str | None:
        opt = self._composed.get(name)
        return getattr(opt, "type", None)

    def _refresh_row(self, name: str) -> None:
        """Re-mark one row from its current resolution, touching no other row."""
        chrome = self._chrome.get(name)
        resolution = self.resolution_for(name)
        if chrome is None or resolution is None:
            return
        overridden = resolution.source == bot_options.SOURCE_OVERRIDE
        chrome.bar.set_active(overridden)
        chrome.reset_btn.setEnabled(overridden)
        chrome.reset_btn.setToolTip(
            self._reset_tooltip(name) if overridden else "Already the value this option resolves to"
        )
        chrome.caption.setText(_SOURCE_CAPTION.get(resolution.source, ""))

    def _fallback_for(self, name: str) -> bot_options.OptionResolution | None:
        """What *name* would resolve to with the user's override taken away.

        Two callers, and they are the same question asked from opposite sides: the
        Reset tooltip naming where the value would land, and a value edit asking
        whether it has landed there already.
        """
        opt = self._composed.get(name)
        if opt is None:
            return None
        return bot_options.resolve_option(
            name,
            override=UNSET,
            declaration=self._declared_default(name),
            declared_type=opt.type,
            app_value=self._app_value(name),
            spec_default=opt.default,
        )

    def _reset_tooltip(self, name: str) -> str:
        """Name the value a reset would fall back to, and the level it comes from."""
        fallback = self._fallback_for(name)
        if fallback is None:
            return "Reset to the value this bot recommends"
        return f"Reset to {fallback.value!r} ({_SOURCE_CAPTION.get(fallback.source, fallback.source)})"

    # ------------------------------------------------------------------
    # Edits
    # ------------------------------------------------------------------

    def _on_value_changed(self, name: str, value: Any) -> None:
        """Store the user's value -- or forget it, when they have typed their way back.

        Setting a control back to what the option already resolves to is the same
        act as pressing Reset, and has to leave the same state behind. A row marked
        as overridden while holding the value it would have had anyway is a lie
        about what the user did, and it would then silently outrank an author who
        later improved the declaration it happens to match. Every settings UI that
        marks overrides this way drops the row instead; so does this one.
        """
        if self._config is None:
            return
        fallback = self._fallback_for(name)
        if fallback is not None and values_agree(fallback.value, value):
            self._on_reset(name)
            return
        if values_agree(self._override(name), value):
            return
        try:
            self._sm().set_option_override(self._config.id, name, value)
        except Exception:
            logger.exception("Failed to store your value for option %r", name)
            return
        self._refresh_row(name)
        self._push_keep_list_context()
        self.values_changed.emit()

    def _read_show_descriptions(self) -> bool:
        try:
            return bool(self._sm().get("general", "show_option_descriptions", default=False))
        except Exception:
            logger.exception("Failed to read whether option descriptions are shown")
            return False

    def _on_show_all_toggled(self, shown: bool) -> None:
        """Set every row at once, and remember the choice.

        The per-row ``?`` still overrides one row afterwards; this is the default
        each row is built with and what a later flip of this switch puts them back
        to. Nothing here touches the bot -- it is a reading preference.
        """
        self._show_descriptions = shown
        try:
            self._sm().set("general", "show_option_descriptions", value=shown)
        except Exception:
            logger.exception("Failed to remember whether option descriptions are shown")
        for chrome in self._chrome.values():
            if chrome.help_btn is not None:
                chrome.help_btn.setChecked(shown)

    def _set_row_description(self, name: str, shown: bool) -> None:
        """Fold one row's description in or out, leaving every other row alone."""
        chrome = self._chrome.get(name)
        if chrome is None or chrome.description is None:
            return
        chrome.description.setVisible(shown)

    def _on_reset(self, name: str) -> None:
        """Forget the user's value for *name* and put the resolved one back on screen."""
        if self._config is None:
            return
        had_override = is_set(self._override(name))
        try:
            self._sm().drop_option_override(self._config.id, name)
        except Exception:
            logger.exception("Failed to forget your value for option %r", name)
            return
        chrome = self._chrome.get(name)
        fallback = self._fallback_for(name)
        if chrome is not None and fallback is not None:
            # Blocked, because writing the value back through the widget would come
            # straight back round as an edit -- and land here again.
            chrome.widget.blockSignals(True)
            set_widget_value(chrome.widget, fallback.value)
            chrome.widget.blockSignals(False)
        self._refresh_row(name)
        self._push_keep_list_context()
        if had_override:
            self.values_changed.emit()
