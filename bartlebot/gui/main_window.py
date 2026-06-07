import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QMainWindow, QMenu, QMenuBar, QMessageBox, QSplitter

from bartlebot.credentials import CredentialStore
from bartlebot.gui.canvas.bot_canvas import BotCanvas
from bartlebot.gui.game_embed.game_view_panel import GameViewPanel
from bartlebot.gui.sidebar.left_sidebar import LeftSidebar
from bartlebot.gui.sidebar.right_sidebar import RightSidebar
from bartlebot.settings_manager import SettingsManager


class MainWindow(QMainWindow):
    # Emitted when a recent-file menu entry is clicked (passes the file path)
    open_recent_requested = pyqtSignal(str)
    # Emitted after the Settings dialog is accepted, so AppController can
    # reconfigure timers and other settings-dependent state.
    settings_applied = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BartleBot")

        self._build_menu()
        self._build_central()
        self._apply_minimum_size()
        self._apply_startup_layout()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menu_bar = QMenuBar(self)
        self.setMenuBar(menu_bar)

        file_menu = menu_bar.addMenu("File")

        self.action_new = QAction("New Bot", self)
        self.action_new.setShortcut(QKeySequence("Ctrl+N"))
        file_menu.addAction(self.action_new)

        self.action_open = QAction("Open Bot", self)
        self.action_open.setShortcut(QKeySequence("Ctrl+O"))
        file_menu.addAction(self.action_open)

        self._recent_menu = QMenu("Open Recent", self)
        file_menu.addMenu(self._recent_menu)
        self._update_recent_placeholder()

        self.action_browse_library = QAction("Browse Bot Library…", self)
        file_menu.addAction(self.action_browse_library)

        self.action_close = QAction("Close Bot", self)
        self.action_close.setShortcut(QKeySequence("Ctrl+W"))
        self.action_close.setEnabled(False)
        file_menu.addAction(self.action_close)

        file_menu.addSeparator()

        self.action_save = QAction("Save Bot", self)
        self.action_save.setShortcut(QKeySequence("Ctrl+S"))
        self.action_save.setEnabled(False)
        file_menu.addAction(self.action_save)

        self.action_save_as = QAction("Save Bot As…", self)
        self.action_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.action_save_as.setEnabled(False)
        file_menu.addAction(self.action_save_as)

        file_menu.addSeparator()

        settings_action = QAction("Settings…", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # Edit menu
        edit_menu = menu_bar.addMenu("Edit")

        self.action_undo = QAction("Undo", self)
        self.action_undo.setShortcut(QKeySequence("Ctrl+Z"))
        self.action_undo.setEnabled(False)
        edit_menu.addAction(self.action_undo)

        self.action_redo = QAction("Redo", self)
        self.action_redo.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        self.action_redo.setEnabled(False)
        edit_menu.addAction(self.action_redo)

        # View menu
        view_menu = menu_bar.addMenu("View")
        self.action_toggle_game_view = QAction("Toggle Game View", self)
        self.action_toggle_game_view.setShortcut(QKeySequence("Ctrl+G"))
        view_menu.addAction(self.action_toggle_game_view)

        self.action_maximize_game_view = QAction("Maximize Game View", self)
        self.action_maximize_game_view.setShortcut(QKeySequence("Ctrl+Alt+G"))
        self.action_maximize_game_view.setCheckable(True)
        view_menu.addAction(self.action_maximize_game_view)

        view_menu.addSeparator()

        self.action_toggle_left_sidebar = QAction("Toggle Left Sidebar", self)
        view_menu.addAction(self.action_toggle_left_sidebar)

        self.action_toggle_right_sidebar = QAction("Toggle Right Sidebar", self)
        view_menu.addAction(self.action_toggle_right_sidebar)

        # Run menu
        run_menu = menu_bar.addMenu("Run")

        self.action_run_pause = QAction("Run / Pause", self)
        self.action_run_pause.setShortcut(QKeySequence("F5"))
        self.action_run_pause.setEnabled(False)
        run_menu.addAction(self.action_run_pause)

        self.action_stop_bot = QAction("Stop Bot", self)
        self.action_stop_bot.setShortcut(QKeySequence("Shift+F5"))
        self.action_stop_bot.setEnabled(False)
        run_menu.addAction(self.action_stop_bot)

        run_menu.addSeparator()

        self.action_validate_bot = QAction("Validate Bot", self)
        self.action_validate_bot.setEnabled(False)
        run_menu.addAction(self.action_validate_bot)

        # Launch menu
        self._launch_menu = menu_bar.addMenu("Launch")
        self._rebuild_launch_menu()

    def _build_central(self) -> None:
        # BotCanvas must be created first — it owns param_panel
        self.bot_canvas = BotCanvas()
        self.left_sidebar = LeftSidebar(self.bot_canvas.param_panel)
        self.right_sidebar = RightSidebar()
        self._game_view_panel = GameViewPanel()
        self.game_view = self._game_view_panel.game_view

        # Wire View menu
        self.action_toggle_game_view.triggered.connect(lambda: self.show_game_view(not self._game_view_visible))
        self.action_maximize_game_view.triggered.connect(self._toggle_maximize_game_view)
        self.action_toggle_left_sidebar.triggered.connect(
            lambda: self.left_sidebar.setVisible(not self.left_sidebar.isVisible())
        )
        self.action_toggle_right_sidebar.triggered.connect(
            lambda: self.right_sidebar.setVisible(not self.right_sidebar.isVisible())
        )

        # Wire wiki import from handle_combat node panel → possible drops list
        self.bot_canvas.param_panel.wiki_import_completed.connect(self.left_sidebar.possible_drops.add_drops)
        # Wire Browse Bot Library menu action
        self.action_browse_library.triggered.connect(self.bot_canvas.open_library_dialog)
        # Wire sidebar tab switching from canvas
        self.bot_canvas.tab_switch.connect(self._on_canvas_tab_switch)
        # Wire bot status to game view panel title bar
        self.bot_canvas.bot_status_changed.connect(self._on_bot_status_changed)
        # Wire canvas placeholder quick-start buttons to file menu actions
        self.bot_canvas.new_bot_requested.connect(self.action_new.trigger)
        self.bot_canvas.open_bot_requested.connect(self.action_open.trigger)
        self.bot_canvas.open_recent_requested.connect(self.open_recent_requested)
        # Wire undo/redo
        self.action_undo.triggered.connect(self.bot_canvas._on_undo)
        self.action_redo.triggered.connect(self.bot_canvas._on_redo)
        self.bot_canvas.undo_state_changed.connect(self._on_undo_state_changed)

        # Wire placeholder quick-launch signals
        self._game_view_panel.launch_account_requested.connect(
            lambda u, d: asyncio.ensure_future(self._launch_single_account(u, d))
        )
        self._game_view_panel.launch_profile_requested.connect(lambda n: asyncio.ensure_future(self._launch_profile(n)))
        self._game_view_panel.manage_accounts_requested.connect(lambda: self._open_settings(tab_name="game"))

        # Top row: left sidebar | game view | right sidebar
        self._top_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._top_splitter.setChildrenCollapsible(False)
        self._top_splitter.addWidget(self.left_sidebar)
        self._top_splitter.addWidget(self._game_view_panel)
        self._top_splitter.addWidget(self.right_sidebar)
        self._top_splitter.setStretchFactor(1, 1)
        sm = SettingsManager.instance()
        left_w = sm.get_general("left_sidebar_width", default=300)
        right_w = sm.get_general("right_sidebar_width", default=300)
        bot_h = sm.get_general("bottom_section_height", default=220)
        self._top_splitter.setSizes([left_w, 800, right_w])

        # Outer vertical splitter: top row (game + sidebars) | bot canvas
        self._outer_splitter = QSplitter(Qt.Orientation.Vertical)
        self._outer_splitter.setChildrenCollapsible(False)
        self._outer_splitter.addWidget(self._top_splitter)
        self._outer_splitter.addWidget(self.bot_canvas)
        self._outer_splitter.setStretchFactor(0, 1)
        self._outer_splitter.setSizes([600, bot_h])

        self.setCentralWidget(self._outer_splitter)

        # Show GameViewPanel immediately (placeholder shown when no clients)
        self._game_view_visible: bool = True
        self._game_view_maximized: bool = False
        self._pre_maximize_sizes: dict = {}
        self._game_view_panel.setVisible(True)

        # Wire Run menu
        self.action_run_pause.triggered.connect(lambda: self.bot_canvas._run_pause_btn.click())
        self.action_stop_bot.triggered.connect(lambda: self.bot_canvas.stop_button.click())

    def _apply_minimum_size(self) -> None:
        min_w, min_h = SettingsManager.instance().minimum_window_size()
        self.setMinimumSize(min_w, min_h)

    def _apply_startup_layout(self) -> None:
        sm = SettingsManager.instance()
        min_w, min_h = sm.minimum_window_size()
        if sm.get_general("maximize_on_open", default=False):
            self.showMaximized()
        else:
            w = max(sm.get_general("window_width", default=1400), min_w)
            h = max(sm.get_general("window_height", default=800), min_h)
            self.resize(w, h)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_fsm_state(self, state_id: str) -> None:
        self.bot_canvas.set_state(state_id)

    def set_bot_name(self, name: str, filename: str | None) -> None:
        if filename:
            self.setWindowTitle(f"BartleBot — {filename}")
            self.left_sidebar.bot_info.set_file(filename)
        else:
            self.setWindowTitle("BartleBot")
            self.left_sidebar.bot_info.set_file(None)
        self._game_view_panel.set_bot_name(name or None)

    def set_start_enabled(self, enabled: bool) -> None:
        self.bot_canvas.set_run_enabled(enabled)

    def update_recent_menu(self, paths: list) -> None:
        """Rebuild the Open Recent submenu from a list of file paths."""
        self._recent_menu.clear()
        if not paths:
            self._update_recent_placeholder()
            return
        cwd = Path.cwd()
        for path in paths:
            try:
                label = str(Path(path).relative_to(cwd))
            except ValueError:
                label = path
            action = QAction(label, self)
            action.triggered.connect(lambda _checked, p=path: self.open_recent_requested.emit(p))
            self._recent_menu.addAction(action)

    def set_save_enabled(self, enabled: bool) -> None:
        self.action_save.setEnabled(enabled)
        self.action_save_as.setEnabled(enabled)

    def set_close_enabled(self, enabled: bool) -> None:
        self.action_close.setEnabled(enabled)

    def show_game_view(self, visible: bool) -> None:
        """Show or hide the top game-view area. When hidden, bot canvas fills the window."""
        if visible == self._game_view_visible:
            return
        self._game_view_visible = visible
        self._game_view_panel.setVisible(visible)
        bot_h = self._outer_splitter.sizes()[1]
        if visible:
            self._outer_splitter.setSizes([600, bot_h])
        else:
            self._outer_splitter.setSizes([0, bot_h])

    def refresh_minimum_size(self) -> None:
        """Re-apply minimum size after a settings change."""
        self._apply_minimum_size()

    def _toggle_maximize_game_view(self) -> None:
        if not self._game_view_maximized:
            # Save current state before maximizing
            self._pre_maximize_sizes = {
                "top": self._top_splitter.sizes(),
                "outer": self._outer_splitter.sizes(),
                "game_visible": self._game_view_visible,
                "left_visible": self.left_sidebar.isVisible(),
                "right_visible": self.right_sidebar.isVisible(),
            }
            self._game_view_maximized = True
            self.action_maximize_game_view.setChecked(True)
            # Show game view and hide everything else
            if not self._game_view_visible:
                self._game_view_visible = True
                self._game_view_panel.setVisible(True)
            self.left_sidebar.setVisible(False)
            self.right_sidebar.setVisible(False)
            self.bot_canvas.setVisible(False)
        else:
            self._game_view_maximized = False
            self.action_maximize_game_view.setChecked(False)
            # Restore pre-maximize state
            self.left_sidebar.setVisible(self._pre_maximize_sizes.get("left_visible", True))
            self.right_sidebar.setVisible(self._pre_maximize_sizes.get("right_visible", True))
            self.bot_canvas.setVisible(True)
            if not self._pre_maximize_sizes.get("game_visible", True):
                self._game_view_visible = False
                self._game_view_panel.setVisible(False)
            self._top_splitter.setSizes(self._pre_maximize_sizes.get("top", [300, 800, 300]))
            self._outer_splitter.setSizes(self._pre_maximize_sizes.get("outer", [600, 220]))

    def _on_bot_status_changed(self, color: str, status: str, state_label: str, elapsed: str) -> None:
        self._game_view_panel.set_bot_status(color, status, state_label, elapsed)
        idle = status == "Idle"
        running_or_paused = status in ("Running", "Paused")
        editing = status == "Editing"
        self.action_run_pause.setEnabled(not editing)
        self.action_stop_bot.setEnabled(running_or_paused)
        self.action_validate_bot.setEnabled(idle)

    def _on_undo_state_changed(self, can_undo: bool, can_redo: bool, undo_desc: str, redo_desc: str) -> None:
        self.action_undo.setEnabled(can_undo)
        self.action_undo.setText(f"Undo: {undo_desc}" if undo_desc else "Undo")
        self.action_redo.setEnabled(can_redo)
        self.action_redo.setText(f"Redo: {redo_desc}" if redo_desc else "Redo")

    def _rebuild_launch_menu(self) -> None:
        """Rebuild Launch menu from current accounts and profiles."""
        self._launch_menu.clear()

        # --- Profiles section ---
        profiles_lbl = QAction("─ Profiles ─", self)
        profiles_lbl.setEnabled(False)
        self._launch_menu.addAction(profiles_lbl)

        profiles = SettingsManager.instance().launch_profiles
        if profiles:
            for profile in profiles:
                name = profile.get("name", "Unnamed")
                action = QAction(name, self)
                action.triggered.connect(lambda _checked, n=name: asyncio.ensure_future(self._launch_profile(n)))
                self._launch_menu.addAction(action)
        else:
            empty = QAction("(No profiles)", self)
            empty.setEnabled(False)
            self._launch_menu.addAction(empty)

        self._launch_menu.addSeparator()

        # --- Accounts section ---
        accounts_lbl = QAction("─ Accounts ─", self)
        accounts_lbl.setEnabled(False)
        self._launch_menu.addAction(accounts_lbl)

        accounts = SettingsManager.instance().accounts
        if accounts:
            for acct in accounts:
                username = acct.get("username", "")
                alias = acct.get("alias") or None
                display = CredentialStore.display_name(username, alias)
                action = QAction(display, self)
                action.triggered.connect(
                    lambda _checked, u=username, d=display: asyncio.ensure_future(self._launch_single_account(u, d))
                )
                self._launch_menu.addAction(action)
        else:
            empty2 = QAction("(No accounts)", self)
            empty2.setEnabled(False)
            self._launch_menu.addAction(empty2)

        self._launch_menu.addSeparator()

        manage_action = QAction("Manage Accounts…", self)
        manage_action.triggered.connect(lambda: self._open_settings(tab_name="game"))
        self._launch_menu.addAction(manage_action)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_canvas_tab_switch(self, tab: str) -> None:
        if tab == "log":
            self.left_sidebar.switch_to_log()
        elif tab == "properties":
            self.left_sidebar.switch_to_properties()
        elif tab == "bot":
            self.left_sidebar.switch_to_bot()
        elif tab == "combat":
            self.left_sidebar.switch_to_combat()

    def _open_settings(self, tab_name: str = "general") -> None:
        from bartlebot.gui.settings.settings_dialog import SettingsDialog

        dlg = SettingsDialog(self)
        dlg.open_tab(tab_name)
        if dlg.exec():
            self._rebuild_launch_menu()
            self.refresh_minimum_size()
            self.bot_canvas._apply_canvas_settings()
            self.settings_applied.emit()
        self._game_view_panel.game_view._placeholder.refresh()

    def _open_launcher(self) -> None:
        from bartlebot.gui.launcher_popup import LauncherPopup

        popup = LauncherPopup(self)
        popup.show()

    async def _launch_profile(self, profile_name: str) -> None:
        try:
            from bartlebot.launcher.launch_profile import launch_profile

            await launch_profile(profile_name)
            self._record_recent_launch("profile", profile_name)
        except Exception as e:
            QMessageBox.critical(self, "Launch failed", str(e))
            logger.exception("Profile launch failed")

    async def _launch_single_account(self, username: str, display_name: str) -> None:
        try:
            from bartlebot.launcher.launch_profile import launch_account

            password = CredentialStore.retrieve(username)
            if password is None:
                QMessageBox.critical(
                    self,
                    "Launch failed",
                    f"No stored password for '{username}'.\nPlease add the account in Settings → Accounts.",
                )
                return
            await launch_account(username, password, display_name)
            self._record_recent_launch("account", username)
        except Exception as e:
            QMessageBox.critical(self, "Launch failed", str(e))
            logger.exception("Account launch failed")

    def _record_recent_launch(self, entry_type: str, identifier: str) -> None:
        SettingsManager.instance().add_recent_launch(entry_type, identifier)
        self._game_view_panel.game_view._placeholder.refresh()

    def _update_recent_placeholder(self) -> None:
        action = QAction("(No recent files)", self)
        action.setEnabled(False)
        self._recent_menu.addAction(action)
