"""Left sidebar: tab-based panel with Bot, Setup/Options, Combat, Properties, and Log tabs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QScrollArea, QStackedWidget, QTabWidget, QVBoxLayout, QWidget

from bartlebot.gui.sidebar.combat_tab import CombatTab
from bartlebot.gui.sidebar.log_panel import LogPanel
from bartlebot.gui.sidebar.sections.bot_info_section import BotInfoSection
from bartlebot.gui.sidebar.sections.options_section import OptionsSection
from bartlebot.gui.sidebar.sections.possible_drops_section import PossibleDropsSection
from bartlebot.gui.sidebar.sections.setup_section import SetupSection

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_BOT_TAB = 0
_CONFIG_TAB = 1
# Pages of the Config tab's stack, and the tab label each one wears.
_SETUP_PAGE = 0
_OPTIONS_PAGE = 1
_CONFIG_TAB_TITLES = ("Setup", "Options")
_COMBAT_TAB = 2
_PROPERTIES_TAB = 3
_LOG_TAB = 4


class LeftSidebar(QTabWidget):
    """Left sidebar with five tabs: Bot, Setup/Options, Combat, Properties, Log.

    - Bot: bot metadata (name, version, description, authors, start, initial/recovery state, possible drops)
    - Setup / Options: the two halves of a bot's options, swapped by edit mode --
      Setup edits the user's own values, Options edits the author's declarations
    - Combat: Bot Combat Profiles, Duel Profiles, player deck config
    - Properties: node parameter editor (created externally in BotCanvas and passed in)
    - Log: log viewer
    """

    config_changed = pyqtSignal()

    def __init__(
        self,
        param_panel: QWidget,
        parent=None,
    ):
        super().__init__(parent)
        self.setMinimumWidth(300)
        self._config: BotConfig | None = None

        # ── Bot tab ─────────────────────────────────────────────
        self.bot_info = BotInfoSection()
        self.possible_drops = PossibleDropsSection()

        bot_content = QWidget()
        bot_vbox = QVBoxLayout(bot_content)
        bot_vbox.setContentsMargins(8, 8, 8, 8)
        bot_vbox.setSpacing(4)
        bot_vbox.addWidget(self.bot_info)
        bot_vbox.addWidget(self.possible_drops, 1)  # stretch=1: fills space when expanded
        bot_vbox.addStretch()  # absorbs leftover when possible_drops is collapsed

        # ── Config tab ──────────────────────────────────────────
        # Two surfaces, one tab, swapped by mode: Setup edits the user's values,
        # Options edits the author's declarations.  Every production system that
        # solves this split separates the two by context rather than by a toggle
        # inside one panel, and Edit mode is the context we already have.  Phase 5
        # promotes them to tabs of their own.
        self.setup = SetupSection()
        self.options = OptionsSection()

        self._config_stack = QStackedWidget()
        for section in (self.setup, self.options):
            content = QWidget()
            vbox = QVBoxLayout(content)
            vbox.setContentsMargins(8, 8, 8, 8)
            vbox.setSpacing(4)
            vbox.addWidget(section)
            vbox.addStretch()
            scroll = QScrollArea()
            scroll.setWidget(content)
            scroll.setWidgetResizable(True)
            self._config_stack.addWidget(scroll)

        # ── Combat tab ──────────────────────────────────────────
        self.combat_tab = CombatTab()

        self.log_panel = LogPanel()

        self.addTab(bot_content, "Bot")  # _BOT_TAB        = 0
        self.addTab(self._config_stack, "Setup")  # _CONFIG_TAB     = 1
        self.addTab(self.combat_tab, "Combat")  # _COMBAT_TAB     = 2
        self.addTab(param_panel, "Properties")  # _PROPERTIES_TAB = 3
        self.addTab(self.log_panel, "Log")  # _LOG_TAB        = 4

        # Propagate section-level signals up as the unified config_changed signal.
        self.bot_info.metadata_changed.connect(self.config_changed)
        self.possible_drops.drops_changed.connect(self.config_changed)
        self.options.options_changed.connect(self.config_changed)
        # A changed declaration changes what Setup resolves to, so that surface has
        # to be rebuilt.  The reverse is deliberately *not* wired: an override lives
        # in settings.json, so writing one must never dirty the bot file.
        self.options.options_changed.connect(self._refresh_setup_values)
        self.combat_tab.combat_config_changed.connect(self.config_changed)

        # Refresh the Combat tab's fight profile list whenever it becomes visible,
        # so that handle_combat nodes edited in the Properties tab are reflected.
        self.currentChanged.connect(self._on_tab_changed)

    # ------------------------------------------------------------------
    # Tab switching (called by BotCanvas.on_tab_switch)
    # ------------------------------------------------------------------

    def switch_to_bot(self) -> None:
        self.setCurrentIndex(_BOT_TAB)

    def switch_to_config(self) -> None:
        self.setCurrentIndex(_CONFIG_TAB)

    def switch_to_combat(self) -> None:
        self.setCurrentIndex(_COMBAT_TAB)

    def switch_to_properties(self) -> None:
        self.setCurrentIndex(_PROPERTIES_TAB)

    def switch_to_log(self) -> None:
        self.setCurrentIndex(_LOG_TAB)

    # ------------------------------------------------------------------
    # Convenience forwarders (dispatch to bot sections)
    # ------------------------------------------------------------------

    def _on_tab_changed(self, index: int) -> None:
        if index == _COMBAT_TAB:
            self.combat_tab.refresh_duel_profiles()

    def _refresh_setup_values(self) -> None:
        """Re-resolve the Setup rows after an edit to the declarations behind them."""
        self.setup.set_config(self._config)

    @property
    def config(self) -> BotConfig | None:
        """The BotConfig the sections are currently editing."""
        return self._config

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self.bot_info.set_config(config)
        self.possible_drops.set_config(config)
        self.setup.set_config(config)
        self.options.set_config(config)
        self.combat_tab.set_config(config)

    def refresh_options(self, config: BotConfig | None) -> None:
        """Rebuild both option surfaces alone, without disturbing the other sections.

        A graph edit can add or drop a *derived* option, so the composed list has to
        be recomputed on every config change.  Deliberately not ``set_config``: that
        also re-binds the combat tab, whose rebind can flush a pending save and come
        back round through ``config_changed``.
        """
        self._config = config
        self.setup.set_config(config)
        self.options.set_config(config)

    def set_clients_getter(self, fn) -> None:
        """Pass the sections a callable returning the currently hooked clients."""
        self.setup.set_clients_getter(fn)
        self.options.set_clients_getter(fn)

    def refresh_player_availability(self) -> None:
        """Re-grey the player checkboxes in both option surfaces."""
        self.setup.refresh_player_availability()
        self.options.refresh_player_availability()

    def sync_config(self, config: BotConfig | None, editable: bool) -> None:
        """Rebind the sections if *config* is a different object than they hold.

        Undo/redo hands the canvas a brand-new ``BotConfig`` — ``UndoManager``
        restores from a ``model_dump`` snapshot — while the sections keep editing
        the object they were given at load.  Everything they write from then on
        lands somewhere nothing reads or saves; the initial state is the visible
        one, because its warnings stop moving with it (#53).

        Cheap and idempotent for the common case, where the object is unchanged.
        ``_config`` is assigned before ``set_config`` because that call can flush a
        pending combat save, which comes back around through ``config_changed``;
        the early return is what stops that from recursing.
        """
        if config is self._config:
            return
        self._config = config
        self.set_config(config)
        self.set_editable(editable)

    def set_editable(self, editable: bool) -> None:
        self.bot_info.set_editable(editable)
        self.possible_drops.set_editable(editable)
        self.options.set_editable(editable)
        page = _OPTIONS_PAGE if editable else _SETUP_PAGE
        self._config_stack.setCurrentIndex(page)
        self.setTabText(_CONFIG_TAB, _CONFIG_TAB_TITLES[page])
        # Combat tab is intentionally not gated by edit mode — profiles are user config, not bot structure.

    def set_bot_running(self, running: bool) -> None:
        """Lock the combat tab while the bot is actively running."""
        self.combat_tab.set_editable(not running)
