"""Left sidebar: a mode-swapped pair of tab sets.

Run mode shows **Setup** and **Combat** — the two things a user tunes before
pressing play.  Edit mode shows **Options** and **Properties** — the two things an
author declares.  Nothing is shown in both, and no tab changes meaning when the
mode does.

That split is §3.5 of the declarations/overrides plan: every production system
that separates a declaration surface from a values surface does it by *context*
(Unity's Prefab Mode, Unreal's Class Defaults, VS Code's ``package.json`` against
its Settings UI), never a toggle inside one panel — and edit mode is the context
this app already has.  Setup being the default Run-mode tab is also the whole of
the pre-run configuration story: adjacency to the Run button, not a gate in front
of it.

Two tabs went elsewhere entirely.  **Bot** is gone — its metadata is Bar B above
the graph, its description heads Setup (read-only) and Options (where it is now
edited), and its Start / Initial / Recovery rows were either unread or already
badges on the nodes.  **Log** moved to
the right sidebar, where it can be read while the left one is showing Setup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QScrollArea, QTabWidget, QVBoxLayout, QWidget

from bartlebot.gui.sidebar.combat_tab import CombatTab
from bartlebot.gui.sidebar.sections.description_section import DescriptionSection
from bartlebot.gui.sidebar.sections.options_section import OptionsSection
from bartlebot.gui.sidebar.sections.setup_section import SetupSection

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig


class LeftSidebar(QTabWidget):
    """Two tabs, whose identity depends on the mode.

    - Run:  Setup (the user's values) and Combat
    - Edit: Options (the author's declarations) and Properties

    ``Properties`` is the node parameter editor, built by ``BotCanvas`` and passed
    in; everything else is built here.
    """

    config_changed = pyqtSignal()
    # An edit that dirties the bot file but cannot change what its options *are*.
    # Kept separate from ``config_changed`` because that signal's handler rebuilds
    # both option surfaces, which is ~80ms on a large bot -- affordable once per
    # graph edit, and not affordable once per keystroke.
    metadata_changed = pyqtSignal()
    # Setup's link to the app-setting level its rows resolve through.  Only the
    # window owns the settings dialog, so the request is passed up rather than
    # answered here.
    app_settings_requested = pyqtSignal()

    def __init__(
        self,
        param_panel: QWidget,
        parent=None,
    ):
        super().__init__(parent)
        self.setMinimumWidth(300)
        self._config: BotConfig | None = None
        self._editable = False

        # ── The two option surfaces ─────────────────────────────────
        # Each gets the bot's full description above it, in the same position, so
        # that the four bots carrying multi-line ``REQUIREMENTS:`` blocks put them
        # in front of the user rather than behind an ellipsis in the header.  The
        # Options copy is the writable one: the description is authorial, so it
        # belongs on the declarations surface and not on the values surface (#180).
        self.setup = SetupSection()
        self.options = OptionsSection()
        self._setup_desc = DescriptionSection(editable=False)
        self._options_desc = DescriptionSection(editable=True)

        self._setup_page = self._wrap(self._setup_desc, self.setup)
        self._options_page = self._wrap(self._options_desc, self.options)

        # ── Combat and Properties ───────────────────────────────────
        self.combat_tab = CombatTab()
        self._param_panel = param_panel

        # Where a tab the current mode does not offer waits.  ``removeTab`` leaves
        # the widget parentless, which makes it a hidden top-level window; parking
        # it on a hidden child keeps it inside this widget's ownership instead.
        self._parked = QWidget(self)
        self._parked.setVisible(False)

        self._apply_tab_set()

        # Propagate section-level signals up as the unified config_changed signal.
        self.options.options_changed.connect(self.config_changed)
        # A changed declaration changes what Setup resolves to, so that surface has
        # to be rebuilt.  The reverse is deliberately *not* wired: an override lives
        # in settings.json, so writing one must never dirty the bot file.
        self.options.options_changed.connect(self.refresh_setup_values)
        # A changed *default* dirties the bot file but cannot change what its
        # options are, so it goes out as ``metadata_changed`` for the same reason
        # the description does: ``config_changed`` would rebuild the Options list
        # from under the control the user is still holding.  Setup is rebuilt
        # because the declaration it resolves through has moved.
        self.options.defaults_changed.connect(self.metadata_changed)
        self.options.defaults_changed.connect(self.refresh_setup_values)
        # Editing the description *is* an edit to the bot file, unlike an override --
        # but it is prose, and no option is composed from it.  So it goes out as
        # ``metadata_changed``, which only marks the bot dirty: routing it through
        # ``config_changed`` rebuilt every option row on every character typed.
        # The Setup copy is not re-bound by a mode switch, so it is refreshed here
        # rather than left to disagree with the editor above Options.
        self._options_desc.description_changed.connect(self.metadata_changed)
        self._options_desc.description_changed.connect(self._setup_desc.refresh)
        self.combat_tab.combat_config_changed.connect(self.config_changed)
        self.setup.app_settings_requested.connect(self.app_settings_requested)

        # Refresh the Combat tab's fight profile list whenever it becomes visible,
        # so that handle_combat nodes edited in the Properties tab are reflected.
        self.currentChanged.connect(self._on_tab_changed)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _wrap(description: QWidget, section: QWidget) -> QScrollArea:
        content = QWidget()
        vbox = QVBoxLayout(content)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(6)
        vbox.addWidget(description)
        vbox.addWidget(section)
        vbox.addStretch()
        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        return scroll

    def _apply_tab_set(self) -> None:
        """Put the tabs the current mode calls for in place, landing on the first.

        Rebuilding rather than hiding: a ``QTabWidget`` cannot hide one tab, and the
        four widgets are long-lived members, so removing them costs nothing beyond
        the re-add.  Signals are blocked across the swap because ``clear()`` moves
        the current index through every intermediate value on the way.
        """
        blocked = self.signalsBlocked()
        self.blockSignals(True)
        try:
            while self.count():
                page = self.widget(0)
                self.removeTab(0)
                page.setParent(self._parked)
            if self._editable:
                self.addTab(self._options_page, "Options")
                self.addTab(self._param_panel, "Properties")
            else:
                self.addTab(self._setup_page, "Setup")
                self.addTab(self.combat_tab, "Combat")
            self.setCurrentIndex(0)
        finally:
            self.blockSignals(blocked)

    # ------------------------------------------------------------------
    # Tab switching
    # ------------------------------------------------------------------

    # No ``switch_to_setup``: leaving Edit mode rebuilds the tab set and lands on
    # Setup already, so a method for it would have no caller.

    def switch_to_combat(self) -> None:
        self._switch_to(self.combat_tab)

    def switch_to_properties(self) -> None:
        self._switch_to(self._param_panel)

    def _switch_to(self, widget: QWidget) -> None:
        index = self.indexOf(widget)
        if index >= 0:
            self.setCurrentIndex(index)

    # ------------------------------------------------------------------
    # Convenience forwarders (dispatch to bot sections)
    # ------------------------------------------------------------------

    def _on_tab_changed(self, index: int) -> None:
        if self.widget(index) is self.combat_tab:
            self.combat_tab.refresh_duel_profiles()

    def refresh_setup_values(self) -> None:
        """Re-resolve the Setup rows after an edit to the level beneath them.

        A declaration edit and a change to the app settings are the same event from
        this surface's point of view: a level Setup resolves *through* has moved.
        """
        self.setup.set_config(self._config)

    @property
    def config(self) -> BotConfig | None:
        """The BotConfig the sections are currently editing."""
        return self._config

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self.setup.set_config(config)
        self.options.set_config(config)
        self.combat_tab.set_config(config)
        self._refresh_description()

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
        self._refresh_description()

    @property
    def setup_description(self) -> DescriptionSection:
        """The read-only description heading Setup."""
        return self._setup_desc

    @property
    def options_description(self) -> DescriptionSection:
        """The editable description heading Options — the bot's only editor for it."""
        return self._options_desc

    def _refresh_description(self) -> None:
        """Put the bot's own description above both option surfaces."""
        for section in (self._setup_desc, self._options_desc):
            section.set_config(self._config)

    def set_clients_getter(self, fn) -> None:
        """Pass the sections a callable returning the currently hooked clients."""
        self.setup.set_clients_getter(fn)
        self.options.set_clients_getter(fn)

    def refresh_player_availability(self) -> None:
        """Re-grey the player checkboxes in both option surfaces."""
        self.setup.refresh_player_availability()
        self.options.refresh_player_availability()

    def refresh_drops(self) -> None:
        """Re-read the bot's drop table into the quicksell keep-list picker."""
        self.setup.refresh_drops()

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
        self.options.set_editable(editable)
        if editable == self._editable:
            return
        self._editable = editable
        self._apply_tab_set()
        # Combat is not gated by edit mode — profiles are user config, not bot
        # structure — it simply is not one of the tabs Edit mode offers.

    def set_bot_running(self, running: bool) -> None:
        """Lock the combat tab while the bot is actively running."""
        self.combat_tab.set_editable(not running)
