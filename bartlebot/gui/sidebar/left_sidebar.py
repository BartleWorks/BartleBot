"""Left sidebar: a mode-swapped pair of tab sets.

Run mode shows **Option Values** and **Combat** — the two things a user tunes before
pressing play.  Edit mode shows **Options** and **Properties** — the two things an
author declares.  Nothing is shown in both, and no tab changes meaning when the
mode does.

Both option tabs are *labeled* **Options**: to a user they are one place, showing the
values in Run mode and the declarations in Edit mode.  The code and the docs still need
two names, so the Run-mode surface is **Option Values** (``OptionValuesSection``) and
the Edit-mode one **Options** (``OptionsSection``).  It was labeled Setup until 10.4,
when that word went to the template's setup steps in the right sidebar.

That split is §3.5 of the declarations/overrides plan: every production system
that separates a declaration surface from a values surface does it by *context*
(Unity's Prefab Mode, Unreal's Class Defaults, VS Code's ``package.json`` against
its Settings UI), never a toggle inside one panel — and edit mode is the context
this app already has.  Option Values being the default Run-mode tab is also the whole of
the pre-run configuration story: adjacency to the Run button, not a gate in front
of it.

Two tabs went elsewhere entirely.  **Bot** is gone — its metadata is Bar B above
the graph, its description heads Option Values (read-only) and Options (where it is now
edited), and its Start / Initial / Recovery rows were either unread or already
badges on the nodes.  **Log** moved to
the right sidebar, where it can be read while the left one is showing Option Values.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bartleby.qt.widgets.layout.scroll_gutter import reserve_scrollbar_gutter
from bartleby.runtime.game.game_sizing import SIDEBAR_MIN_HEIGHT
from PyQt6.QtCore import QEvent, pyqtSignal
from PyQt6.QtWidgets import QScrollArea, QTabWidget, QVBoxLayout, QWidget

from bartlebot.gui import screen_fit
from bartlebot.gui.sidebar.combat_tab import CombatTab
from bartlebot.gui.sidebar.sections.description_section import DescriptionSection
from bartlebot.gui.sidebar.sections.option_values_section import OptionValuesSection
from bartlebot.gui.sidebar.sections.options_section import OptionsSection
from bartlebot.gui.sidebar.sections.variable_values_section import VariableValuesSection
from bartlebot.gui.sidebar.sections.variables_section import VariablesSection
from bartlebot.options.bot_options import options_signature

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig


class LeftSidebar(QTabWidget):
    """Two tabs, whose identity depends on the mode.

    - Run:  Option Values (the user's values) and Combat
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
    # Option Values's link to the app-setting level its rows resolve through.  Only the
    # window owns the settings dialog, so the request is passed up rather than
    # answered here.
    app_settings_requested = pyqtSignal()
    # A declaration edit is about to change the config, carrying the undo step's name.
    # Only the canvas holds the undo stack, so the request is passed up for it to snapshot.
    edit_starting = pyqtSignal(str)
    # An option or a variable was added, renamed or deleted: what the condition and
    # assignment rows on the Properties tab list, and the names they hold, may have moved.
    declarations_changed = pyqtSignal()

    def __init__(
        self,
        param_panel: QWidget,
        parent=None,
    ):
        super().__init__(parent)
        # Explicit, and the arithmetic's own term rather than a comfortable width: an
        # explicit minimum is the one thing that wins over a layout's minimum in
        # ``qSmartMinSize``, so this is what stops a fat tab -- the parameter panel hints
        # 562px wide before a bot is even loaded -- becoming the window's floor and
        # pushing it past the screen the launch was sized to fit. Prose still wraps and
        # long names still elide at this width; that is the trade, and only for a sidebar
        # dragged to the stop. What the floor *does* have to hold is the row that can do
        # neither -- see ``screen_fit.left_sidebar_floor`` and "The panel floors" in
        # game_sizing.py. Measured rather than fixed, for the same reason as the right
        # one: that row's width is font and style metrics.
        self._apply_width_floor()
        self._config: BotConfig | None = None
        self._editable = False
        # What the two option surfaces were last built from.  ``None`` is not a
        # signature any config produces -- an empty one is ``()`` -- so it reads as
        # "nothing has been built yet" and never as a match.
        self._built_signature: tuple | None = None
        self._built_config: BotConfig | None = None
        self._built_variables: tuple | None = None

        # ── The two option surfaces ─────────────────────────────────
        # Each gets the bot's full description above it, in the same position, so
        # that the four bots carrying multi-line ``REQUIREMENTS:`` blocks put them
        # in front of the user rather than behind an ellipsis in the header.  The
        # Options copy is the writable one: the description is authorial, so it
        # belongs on the declarations surface and not on the values surface.
        self.option_values = OptionValuesSection()
        self.options = OptionsSection()
        # Under Options on the same tab: both are an author's declarations, and a variable
        # is what the bot learns where an option is what the user chose (docs/VARIABLES.md).
        self.variables = VariablesSection()
        # And their values under Option Values: where each starts, and during a run what it
        # holds now (#188). The pair the two option surfaces are, for variables.
        self.variable_values = VariableValuesSection()
        self._values_desc = DescriptionSection(editable=False)
        self._options_desc = DescriptionSection(editable=True)

        self._values_page = self._wrap(self._values_desc, self.option_values, self.variable_values)
        self._options_page = self._wrap(self._options_desc, self.options, self.variables)

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
        # A changed declaration changes what Option Values resolves to, so that surface has
        # to be rebuilt.  The reverse is deliberately *not* wired: an override lives
        # in settings.json, so writing one must never dirty the bot file.
        self.options.options_changed.connect(self.refresh_option_values)
        # A changed *default* dirties the bot file but cannot change what its
        # options are, so it goes out as ``metadata_changed`` for the same reason
        # the description does: ``config_changed`` would rebuild the Options list
        # from under the control the user is still holding.  Option Values is rebuilt
        # because the declaration it resolves through has moved.
        self.options.defaults_changed.connect(self.metadata_changed)
        self.options.defaults_changed.connect(self.refresh_option_values)
        # Editing the description *is* an edit to the bot file, unlike an override --
        # but it is prose, and no option is composed from it.  So it goes out as
        # ``metadata_changed``, which only marks the bot dirty: routing it through
        # ``config_changed`` rebuilt every option row on every character typed.
        # The Option Values copy is not re-bound by a mode switch, so it is refreshed here
        # rather than left to disagree with the editor above Options.
        self._options_desc.description_changed.connect(self.metadata_changed)
        self._options_desc.description_changed.connect(self._values_desc.refresh)
        self.combat_tab.combat_config_changed.connect(self.config_changed)
        # A variable edit changes what the graph's conditions read and what validation
        # finds, so it is a config change like an option's, and undoable like a panel edit.
        self.variables.edit_starting.connect(self.edit_starting)
        self.variables.variables_changed.connect(self.config_changed)
        self.variables.variables_changed.connect(self.declarations_changed)
        self.options.options_changed.connect(self.declarations_changed)
        self.option_values.app_settings_requested.connect(self.app_settings_requested)

        # Refresh the Combat tab's duel strategy list whenever it becomes visible,
        # so that handle_combat nodes edited in the Properties tab are reflected.
        self.currentChanged.connect(self._on_tab_changed)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def _apply_width_floor(self) -> None:
        self.setMinimumSize(screen_fit.left_sidebar_floor(), SIDEBAR_MIN_HEIGHT)

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Re-measure the floor when the text size under it changes.

        The theme is applied to the whole application after this widget is built, and a
        theme carrying a different font would otherwise leave the panel holding a floor
        measured against the old one -- narrower than the keep-list picker's filter row,
        which is the state this floor exists to prevent.
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self._apply_width_floor()

    @staticmethod
    def _wrap(description: QWidget, *sections: QWidget) -> QScrollArea:
        content = QWidget()
        vbox = QVBoxLayout(content)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(6)
        vbox.addWidget(description)
        for section in sections:
            vbox.addWidget(section)
        vbox.addStretch()
        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        # The option cards fold under the user's hand, and an as-needed bar would come
        # and go with them, narrowing every card by its width on the click that folded
        # one (#112). The space is held instead -- with no extra gap, since the content
        # margin above already keeps the cards off the bar, and this panel's floor comes
        # out of the game view's width.
        reserve_scrollbar_gutter(scroll, gap=0)
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
                self.addTab(self._values_page, "Options")
                self.addTab(self.combat_tab, "Combat")
            self.setCurrentIndex(0)
        finally:
            self.blockSignals(blocked)

    # ------------------------------------------------------------------
    # Tab switching
    # ------------------------------------------------------------------

    # No ``switch_to_setup``: leaving Edit mode rebuilds the tab set and lands on
    # Option Values already, so a method for it would have no caller.

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
            self.combat_tab.refresh_duel_strategies()

    def refresh_option_values(self) -> None:
        """Re-resolve the Option Values rows after an edit to the level beneath them.

        A declaration edit and a change to the app settings are the same event from
        this surface's point of view: a level Option Values resolves *through* has moved.
        """
        self.option_values.set_config(self._config)

    @property
    def config(self) -> BotConfig | None:
        """The BotConfig the sections are currently editing."""
        return self._config

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self._rebuild_option_surfaces(config)
        self.combat_tab.set_config(config)
        self._refresh_description()

    def refresh_options(self, config: BotConfig | None) -> None:
        """Rebuild both option surfaces alone, without disturbing the other sections.

        A graph edit can add or drop a *derived* option, so the composed list has to
        be re*computed* on every config change -- but most graph edits touch no node
        carrying a ``BotOptionSpec``, so the answer is usually the list already on
        screen.  Composing it costs 0.05ms and building the rows from it costs ~55ms
        on a 67-state bot, so the comparison pays for itself on the first edit that
        changes nothing.

        Deliberately not ``set_config``: that also re-binds the combat tab, whose
        rebind can flush a pending save and come back round through
        ``config_changed``.
        """
        self._config = config
        self._rebuild_option_surfaces(config)
        self._refresh_description()

    def _rebuild_option_surfaces(self, config: BotConfig | None) -> None:
        """Re-bind Option Values and Options, unless they are already showing this list.

        Object identity is checked as well as the signature because undo/redo hands
        the canvas a brand-new ``BotConfig`` whose contents are usually identical to
        the one before it: the sections write through the object they hold, so one
        left bound to the replaced config edits something nothing saves.
        """
        signature = options_signature(config)
        declared = tuple(v.model_dump_json() for v in config.variables) if config is not None else ()
        if config is not self._built_config or declared != self._built_variables:
            self.variables.set_config(config)
            self.variable_values.set_config(config)
            self._built_variables = declared
        if config is self._built_config and signature == self._built_signature:
            return
        self.option_values.set_config(config)
        self.options.set_config(config)
        self._built_config = config
        self._built_signature = signature

    def rebind_identity(self) -> None:
        """Forward a change of the config's *id* to the surfaces keyed on it.

        Save As is the only thing that moves an id under a loaded bot.  Options
        and Option Values are re-resolved by ``refresh_options``; this is the combat
        half, kept separate because it must not go through ``set_config``.
        """
        self.combat_tab.rebind_identity()

    @property
    def setup_description(self) -> DescriptionSection:
        """The read-only description heading Option Values."""
        return self._values_desc

    @property
    def options_description(self) -> DescriptionSection:
        """The editable description heading Options — the bot's only editor for it."""
        return self._options_desc

    def _refresh_description(self) -> None:
        """Put the bot's own description above both option surfaces."""
        for section in (self._values_desc, self._options_desc):
            section.set_config(self._config)

    def set_clients_getter(self, fn) -> None:
        """Pass the sections a callable returning the currently hooked clients."""
        self.option_values.set_clients_getter(fn)
        self.options.set_clients_getter(fn)

    def refresh_player_availability(self) -> None:
        """Re-gray the player checkboxes in both option surfaces."""
        self.option_values.refresh_player_availability()
        self.options.refresh_player_availability()

    def refresh_drops(self) -> None:
        """Re-read the bot's drop table into the quicksell keep-list picker."""
        self.option_values.refresh_drops()

    def sync_config(self, config: BotConfig | None, editable: bool) -> None:
        """Rebind the sections if *config* is a different object than they hold.

        Undo/redo hands the canvas a brand-new ``BotConfig`` — ``UndoManager``
        restores from a ``model_dump`` snapshot — while the sections keep editing
        the object they were given at load.  Everything they write from then on
        lands somewhere nothing reads or saves; the initial state is the visible
        one, because its warnings stop moving with it.

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
        self.variables.set_editable(editable)
        if editable == self._editable:
            return
        self._editable = editable
        self._apply_tab_set()
        # Combat is not gated by edit mode — Bot Combat Strategies are user config, not bot
        # structure — it simply is not one of the tabs Edit mode offers.

    def set_bot_running(self, running: bool) -> None:
        """Lock the combat tab and the option values while the bot is actively running, and start or end the run's variable values."""
        self.combat_tab.set_editable(not running)
        self.option_values.set_locked(running)
        if running:
            self.variable_values.run_started()
        else:
            self.variable_values.run_finished()
