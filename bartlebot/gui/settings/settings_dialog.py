"""SettingsDialog: modal dialog with General, Bot, Combat, Canvas, Authoring, Theme, and Game tabs."""

from __future__ import annotations

from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QDialog,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui import screen_fit
from bartlebot.gui.settings.accounts_tab import AccountsTab
from bartlebot.gui.settings.authoring_tab import AuthoringTab
from bartlebot.gui.settings.bot_tab import BotTab
from bartlebot.gui.settings.canvas_tab import CanvasTab
from bartlebot.gui.settings.combat_defaults_tab import CombatDefaultsTab
from bartlebot.gui.settings.general_tab import GeneralTab
from bartlebot.gui.settings.rows import ScrollableTab, SettingsForm
from bartlebot.gui.settings.theme_tab import ThemeTab
from bartlebot.gui.widgets.layout.dialog_footer import dialog_footer
from bartlebot.settings_manager import SettingsManager

# The size the dialog opens at when the screen has room for it. The height clears the
# General tab whole on a 1080p screen with a taskbar, and is deliberately shorter than the
# tallest tab wants to be: a tab that does not fit scrolls, and a dialog that opens the
# height of its longest page is unreadable long before it is off-screen.
#
# The width is the Game tab's, which is now the widest page: two card columns side by
# side, each of which has to hold a profile's account pills without wrapping them into a
# third row. Every other tab is a form and was never the constraint. Still clamped
# to the screen by _fit_to_screen, so this is a preference and not a floor.
_PREFERRED_SIZE = (900, 860)

# Qt's own ceiling for a widget maximum. An unmeasurable screen reports a size nothing
# can exceed, and handing that straight to ``setMaximumSize`` earns a warning and a
# silent clamp rather than the "no cap" it is meant to say.
_QT_MAX_SIZE = (1 << 24) - 1


class SettingsDialog(QDialog):
    """Modal settings dialog opened via Settings menu or Ctrl+,."""

    def __init__(self, parent=None, live_game_area: tuple[int, int] | None = None) -> None:
        """*live_game_area* is the game area the window behind this dialog currently has.

        Passed in rather than looked up: the Game tab's resolution pickers judge a launch
        exactly as the launch surfaces do, and a splitter drag moves that answer without
        moving any setting. Reaching back up the parent chain from a settings tab to find
        the main window would couple the tab to the window that happens to open it.
        """
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(480, 400)

        self._tabs = QTabWidget()

        # The four form tabs are as tall as their rows make them -- every setting is a
        # row plus a visible description under it -- so each is wrapped in a scroll area.
        # Theme scrolls its own color grid already, and Game is elastic.

        # General tab
        self._general_tab = ScrollableTab(GeneralTab())
        self._tabs.addTab(self._general_tab, "General")

        # Bot tab (how a run behaves, and the app-setting level of every bot option)
        self._bot_tab = ScrollableTab(BotTab())
        self._tabs.addTab(self._bot_tab, "Bot")

        # Combat tab (the Solo and Team Default Duel Strategies, shared with BartleBuddy)
        self._combat_tab = ScrollableTab(CombatDefaultsTab())
        self._tabs.addTab(self._combat_tab, "Combat")

        # Canvas tab
        self._canvas_tab = ScrollableTab(CanvasTab())
        self._tabs.addTab(self._canvas_tab, "Canvas")

        # Authoring tab (the speed multiplier and the authoring utilities' hotkeys)
        self._authoring_tab = ScrollableTab(AuthoringTab())
        self._tabs.addTab(self._authoring_tab, "Authoring")

        # Theme tab
        self._theme_tab = ThemeTab()
        self._tabs.addTab(self._theme_tab, "Theme")

        # Game tab (accounts, launch profiles, game embed resolution)
        self._accounts_tab = AccountsTab(live_game_area=live_game_area)
        self._tabs.addTab(self._accounts_tab, "Game")

        # Show and Hide descriptions are one preference for the whole dialog, so one tab's
        # links reach every form -- including the ones inside Theme and Game.
        self._forms: list[SettingsForm] = []
        for i in range(self._tabs.count()):
            self._forms.extend(_forms_in(self._tabs.widget(i)))
        for form in self._forms:
            form.descriptions_toggled.connect(self._on_descriptions_toggled)

        footer = dialog_footer(self, on_commit=self._on_accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs, 1)
        layout.addLayout(footer.layout)

        self.resize(*_PREFERRED_SIZE)
        self._fit_to_screen()

    def showEvent(self, event) -> None:
        """Refit on the way up: only now is ``screen()`` the screen it opens on.

        Construction happens before the dialog is placed, so on a multi-monitor setup
        the size worked out in ``__init__`` is the primary screen's answer, not the
        one the user is looking at.
        """
        super().showEvent(event)
        self._fit_to_screen()

    def _fit_to_screen(self) -> None:
        """Cap the dialog at the screen's usable area, and pull it back on if it spills.

        The bug this exists for: with no maximum, the dialog was as tall as its tallest
        tab asked to be, which on a 1080p screen put OK and Cancel under the taskbar with
        no way to reach them. The cap is the last line of defense -- the scroll
        areas are what keep it from being needed -- so it is deliberately not conditional
        on which tabs happen to be present.

        ``screen_metrics`` answers for an unmeasurable screen with something nothing can
        exceed, so a headless run is capped at the minimum of nothing rather than at 40
        pixels square.
        """
        available, _dpr = screen_fit.screen_metrics(self)
        minimum = (self.minimumWidth(), self.minimumHeight())

        max_w, max_h = screen_fit.size_within_screen(available, minimum, available)
        self.setMaximumSize(min(max_w, _QT_MAX_SIZE), min(max_h, _QT_MAX_SIZE))

        width, height = screen_fit.size_within_screen((self.width(), self.height()), minimum, available)
        if (width, height) != (self.width(), self.height()):
            self.resize(width, height)

        # Shrinking a window does not move it, so one already opening past the bottom
        # edge stays there at its new size until it is put back on the screen.
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        usable = screen.availableGeometry()
        frame = self.frameGeometry()
        if not usable.contains(frame):
            frame.moveCenter(usable.center())
            self.move(frame.topLeft())

    @property
    def tabs(self) -> QTabWidget:
        """The tab bar itself, for anything that needs to ask what tabs there are."""
        return self._tabs

    def apply_all(self) -> None:
        """Push every tab's form state into the settings singleton, without saving.

        Driven off the tab bar rather than a hand-written list of four: a list is
        one line to forget when a tab is added, and forgetting it discards
        everything the user typed into the new tab with no error anywhere.
        ``AccountsTab`` persists eagerly and its ``apply`` is a documented no-op,
        which is what makes iterating safe.
        """
        for i in range(self._tabs.count()):
            self._tabs.widget(i).apply()

    def open_tab(self, tab_name: str) -> None:
        """Switch to the named tab. Valid names: general, bot, combat, canvas, authoring, theme, game."""
        widgets = {
            "general": self._general_tab,
            "bot": self._bot_tab,
            "combat": self._combat_tab,
            "canvas": self._canvas_tab,
            "authoring": self._authoring_tab,
            "theme": self._theme_tab,
            "game": self._accounts_tab,
        }
        if w := widgets.get(tab_name):
            self._tabs.setCurrentWidget(w)

    @property
    def forms(self) -> list[SettingsForm]:
        """Every column of settings rows in the dialog, tab by tab."""
        return list(self._forms)

    def _on_descriptions_toggled(self, shown: bool) -> None:
        for form in self._forms:
            form.set_descriptions_shown(shown)

    def _on_accept(self) -> None:
        self.apply_all()
        SettingsManager.instance().save()
        self.accept()


def _forms_in(tab: QWidget) -> list[SettingsForm]:
    """*tab*'s settings forms: the tab itself if it is one, and any it holds."""
    content = tab.content if isinstance(tab, ScrollableTab) else tab
    own = [content] if isinstance(content, SettingsForm) else []
    return own + content.findChildren(SettingsForm)
