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

The declaration editor is the other half -- see ``options_section``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot import bot_options
from bartlebot.bot_options import UNSET, compose_options, is_set
from bartlebot.gui.sidebar.sidebar_section import SidebarSection, field_label
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.option_widgets import connect_widget, make_option_widget
from bartlebot.gui.widgets.player_list_widget import PlayerListWidget
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.settings_manager import SettingsManager

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

logger = logging.getLogger(__name__)

_PLACEHOLDER = "—"
_BAR_WIDTH_PX = 3

# What to print under a row whose value the user did not choose.  The override
# source is deliberately absent: an overridden row already carries the bar and
# the Reset button, and captioning it as well says the same thing three times.
_INHERITED_CAPTION: dict[str, str] = {
    bot_options.SOURCE_BOT_FILE: "from the bot",
    bot_options.SOURCE_APP_SETTING: "from the app setting",
    bot_options.SOURCE_NODE_DEFAULT: "from the node default",
}


class _OverrideBar(ThemedMixin, QFrame):
    """The coloured gutter marking a row the user has overridden."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(_BAR_WIDTH_PX)
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self.setStyleSheet(f"background: {THEME.accent}; border: none;")


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
        self._option_widgets: dict[str, QWidget] = {}
        self._get_clients: Callable[[], list] = lambda: []

        outer = QVBoxLayout()
        outer.setSpacing(4)

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
        for row in self._rows:
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        self._option_widgets.clear()

        options = compose_options(self._config) if self._config is not None else []
        if not options:
            self._empty_lbl.show()
            return
        self._empty_lbl.hide()

        connected = len(self._get_clients())
        for opt in options:
            row = self._build_row(opt, connected)
            self._rows_layout.addWidget(row)
            self._rows.append(row)

    def _build_row(self, opt, connected: int) -> QWidget:
        resolution = self.resolution_for(opt.name)
        overridden = resolution is not None and resolution.source == bot_options.SOURCE_OVERRIDE
        value = resolution.value if resolution is not None else opt.default

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(1)

        widget = make_option_widget(
            opt.type,
            value,
            connected=connected,
            tooltip=opt.description or "",
            label=opt.name,
        )
        if opt.type != "bool":
            lbl = field_label(opt.name)
            if opt.description:
                lbl.setToolTip(opt.description)
            body_layout.addWidget(lbl)

        value_row = QWidget()
        value_layout = QHBoxLayout(value_row)
        value_layout.setContentsMargins(0, 0, 0, 0)
        value_layout.setSpacing(4)
        value_layout.addWidget(widget, 1)
        if overridden:
            reset_btn = QPushButton("Reset")
            reset_btn.setToolTip(self._reset_tooltip(opt))
            reset_btn.clicked.connect(lambda _checked, name=opt.name: self._on_reset(name))
            value_layout.addWidget(reset_btn, 0)
        body_layout.addWidget(value_row)

        if opt.description:
            desc = ThemedTextLabel(opt.description, css="font-size: 9px;")
            desc.setWordWrap(True)
            body_layout.addWidget(desc)

        caption = _INHERITED_CAPTION.get(resolution.source) if resolution is not None else None
        if caption is not None:
            body_layout.addWidget(ThemedTextLabel(caption, color="text_disabled", css="font-size: 9px;"))

        connect_widget(widget, lambda new_value, name=opt.name: self._on_value_changed(name, new_value))
        self._option_widgets[opt.name] = widget

        # The bar is a sibling of the body, not a border on it: a stylesheet border
        # set on the row would be inherited by every control inside it.  The unset
        # rows carry an equally wide spacer so nothing shifts sideways when a value
        # gains or loses its override.
        outer = QWidget()
        outer_layout = QHBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(5)
        if overridden:
            outer_layout.addWidget(_OverrideBar())
        else:
            spacer = QWidget()
            spacer.setFixedWidth(_BAR_WIDTH_PX)
            outer_layout.addWidget(spacer)
        outer_layout.addWidget(body, 1)
        return outer

    def _reset_tooltip(self, opt) -> str:
        """Name the value a reset would fall back to, and the level it comes from."""
        fallback = bot_options.resolve_option(
            opt.name,
            override=UNSET,
            declaration=self._declared_default(opt.name),
            declared_type=opt.type,
            app_value=self._app_value(opt.name),
            spec_default=opt.default,
        )
        where = _INHERITED_CAPTION.get(fallback.source, fallback.source)
        return f"Reset to {fallback.value!r} ({where})"

    # ------------------------------------------------------------------
    # Edits
    # ------------------------------------------------------------------

    def _on_value_changed(self, name: str, value: Any) -> None:
        if self._config is None:
            return
        current = self._override(name)
        if is_set(current) and current == value and isinstance(current, bool) == isinstance(value, bool):
            return
        try:
            self._sm().set_option_override(self._config.id, name, value)
        except Exception:
            logger.exception("Failed to store your value for option %r", name)
            return
        self._rebuild()
        self.values_changed.emit()

    def _on_reset(self, name: str) -> None:
        if self._config is None:
            return
        try:
            self._sm().drop_option_override(self._config.id, name)
        except Exception:
            logger.exception("Failed to forget your value for option %r", name)
            return
        self._rebuild()
        self.values_changed.emit()
