"""BartleBot's setup: core's frame and shared pages, with BartleBot's own pages after them (epic #215).

Opened by ``AppController._after_window_shown`` while any page is unanswered, and again
from File › Set up BartleBot…. ``docs/FIRST_RUN.md`` lists the pages, what each writes,
and when each counts as answered.

The dialog, :class:`~bartleby.qt.dialogs.first_run_dialog.FirstRunPage` and the pages every
product asks (the game folder, the accounts, the launch profile and the combat question)
moved to core's ``bartleby/qt/dialogs/first_run_dialog.py`` in Stage 11 of #167, so
BartleBuddy asks them too, and a page answered in one product is not asked in the other.
Core's docstring has why setup is a list of questions and not a tour. What stays here is
BartleBot's: the Quicksell, Realm and Safety Delay, Options and Game art pages, its texts
for the Accounts page, and :func:`setup_pages`, its list.
"""

from __future__ import annotations

import logging

from bartleby.qt.dialogs import first_run_dialog as core
from bartleby.qt.dialogs.first_run_dialog import (
    AccountsPage,
    CombatQuestionPage,
    FirstRunPage,
    GameFolderPage,
    LaunchProfilePage,
    SetupMode,
)
from bartleby.qt.settings.rows import SettingsForm
from bartleby.qt.theming.theme import THEME
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from bartleby.shared.preferences import PreferenceStore
from PyQt6.QtWidgets import QCheckBox, QDoubleSpinBox, QVBoxLayout, QWidget

from bartlebot.game import card_art
from bartlebot.gui import window_panes
from bartlebot.gui.settings.game_art_row import ArtExtraction, extract_art_row
from bartlebot.gui.settings.players_rows import auto_group_row, refill_potions_row
from bartlebot.gui.settings.quicksell_group import QuicksellSettings
from bartlebot.gui.settings.realm_delay_rows import favorite_realm_row, safety_delay_row
from bartlebot.gui.widgets.inputs.realm_combo import RealmCombo
from bartlebot.options.custom_setup import (
    art_answered,
    extract_art_chosen,
    options_answered,
    record_art_answered,
    record_options,
)
from bartlebot.options.quicksell_setup import quicksell_answered, record_quicksell
from bartlebot.options.realm_delay_setup import realm_delay_answered, record_realm_delay, setup_realm
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)


# -- accounts -----------------------------------------------------------------------

#: Above the Accounts page's cards: core's text, and what a run does with the logins.
ACCOUNTS_INTRO = (
    "BartleBot uses your logins to launch the game, and to bring it back after a crash or disconnect. "
    "Passwords are stored in Windows Credential Manager. The game opens inside BartleBot's window, where "
    "you can play it too. While a bot runs, your clicks and keys reach the game and can throw the bot off."
)
#: Beneath the Add button while there is none: leaving the page empty is an answer too.
ACCOUNTS_EMPTY = (
    "Without an account, you start the game yourself, and bots can't recover from a crash. To add one "
    "later, use Launch › Manage Wizard101 Accounts…"
)


# -- quicksell ----------------------------------------------------------------------

QUICKSELL_HEADING = "What should bots sell?"
#: Above the rows: that quicksell starts off (#262), what turning it on sells, which is
#: what the explanation before each run used to say and this page turns off, and where
#: the same rows are later.
QUICKSELL_INTRO = (
    "When a backpack fills, quicksell sells every drop these settings don't keep, and never an item "
    "you've locked in the game. To lock items, turn on Backpack Item Lock in the game's Options › "
    "Gameplay Options 2. Change these later in Settings › Bot, or for one bot in its Options tab."
)


class QuicksellPage(FirstRunPage):
    """What bots may sell: Settings › Bot's quicksell rows, on the stored values.

    The rows are :class:`~bartlebot.gui.settings.quicksell_group.QuicksellSettings`, the
    builder the Bot tab uses, without its Quicksell reminder row: Next writes
    through :func:`~bartlebot.options.quicksell_setup.record_quicksell`, which turns the
    explanation off, since this page has given it.
    """

    heading = QUICKSELL_HEADING

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = SettingsManager.instance()
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        intro = ThemedTextLabel(QUICKSELL_INTRO, css=f"font-size: {THEME.font_small_px}px;")
        intro.setWordWrap(True)
        column.addWidget(intro)
        self._quicksell = QuicksellSettings(self._settings, foldable=False)
        # Every description shown: on a first run each is part of the question.
        form = SettingsForm(margin=0, always_describe=True)
        form.add_group(self._quicksell.group)
        form.finish(stretch=False)
        column.addWidget(form)
        column.addStretch()

    @property
    def quicksell(self) -> QuicksellSettings:
        return self._quicksell

    @classmethod
    def asks_first_run(cls, prefs: PreferenceStore) -> bool:
        return not quicksell_answered(SettingsManager.instance())

    def answered(self) -> bool:
        return quicksell_answered(self._settings)

    def record(self) -> bool:
        return record_quicksell(self._settings, self._quicksell.flags(), self._quicksell.keep_list())


# -- realm and Safety Delay ---------------------------------------------------------

REALM_DELAY_HEADING = "Set your favorite realm and Safety Delay"
#: Above the rows: what each is for, said once here so the rows can keep the tab's text.
REALM_DELAY_INTRO = (
    "Bots with a Change Realm node move every player to your favorite realm. The farther you are from "
    "the game servers, the longer Safety Delay should be. Change both later in Settings › Bot, or for "
    "one bot in its Options tab."
)


class RealmAndDelayPage(FirstRunPage):
    """Favorite Realm and Safety Delay: Settings › Bot's two rows, on the stored values.

    On a first run the realm opens on :data:`~bartlebot.options.realm_delay_setup.SETUP_REALM`
    (Wraith), and the delay on the stored one, 3.0 seconds on a fresh install. Wraith is
    only the page's selection: the setting's own default stays unset. Next writes both
    through :func:`~bartlebot.options.realm_delay_setup.record_realm_delay`.
    """

    heading = REALM_DELAY_HEADING

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = SettingsManager.instance()
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        intro = ThemedTextLabel(REALM_DELAY_INTRO, css=f"font-size: {THEME.font_small_px}px;")
        intro.setWordWrap(True)
        column.addWidget(intro)
        # Every description shown: on a first run each is part of the question.
        form = SettingsForm(margin=0, always_describe=True)
        self._realm = form.add_row(favorite_realm_row(self._settings, setup_realm(self._settings))).control
        self._delay = form.add_row(safety_delay_row(self._settings)).control
        form.finish(stretch=False)
        column.addWidget(form)
        column.addStretch()

    @property
    def realm_combo(self) -> RealmCombo:
        return self._realm

    @property
    def delay_box(self) -> QDoubleSpinBox:
        return self._delay

    @classmethod
    def asks_first_run(cls, prefs: PreferenceStore) -> bool:
        return not realm_delay_answered(SettingsManager.instance())

    def answered(self) -> bool:
        return realm_delay_answered(self._settings)

    def record(self) -> bool:
        return record_realm_delay(self._settings, self._realm.get_value(), self._delay.value())


# -- Custom's options ---------------------------------------------------------------

OPTIONS_HEADING = "Choose a few more options"
OPTIONS_INTRO = "Change these later in Settings › General and Settings › Bot."


class OptionsPage(FirstRunPage):
    """Custom setup only: Extract game art, Automatically group players and Refill potions, all on.

    The last two are Settings › Bot's rows, from the same builder. Next writes all three
    through :func:`~bartlebot.options.custom_setup.record_options`. Under Quick the page is
    not shown and nothing of it is written: grouping and refills stay as they are, and the
    art is extracted.
    """

    heading = OPTIONS_HEADING

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = SettingsManager.instance()
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        intro = ThemedTextLabel(OPTIONS_INTRO, css=f"font-size: {THEME.font_small_px}px;")
        intro.setWordWrap(True)
        column.addWidget(intro)
        # Every description shown: each says what its choice costs.
        form = SettingsForm(margin=0, always_describe=True)
        self._art = form.add_row(extract_art_row(extract_art_chosen(self._settings))).control
        self._group = form.add_row(auto_group_row(self._settings)).control
        self._refill = form.add_row(refill_potions_row(self._settings)).control
        form.finish(stretch=False)
        column.addWidget(form)
        column.addStretch()
        # Whether the Game art page follows.
        self._art.toggled.connect(lambda _on: self.walk_changed.emit())

    @property
    def art_box(self) -> QCheckBox:
        return self._art

    @property
    def group_box(self) -> QCheckBox:
        return self._group

    @property
    def refill_box(self) -> QCheckBox:
        return self._refill

    @property
    def extract_art(self) -> bool:
        return self._art.isChecked()

    @classmethod
    def asks_first_run(cls, prefs: PreferenceStore) -> bool:
        # Custom only, so never: see needs_asking.
        return False

    def applies(self, mode: SetupMode) -> bool:
        return mode is SetupMode.CUSTOM

    def answered(self) -> bool:
        return options_answered(self._settings)

    def record(self) -> bool:
        return record_options(
            self._settings,
            extract_art=self._art.isChecked(),
            auto_group=self._group.isChecked(),
            refill=self._refill.isChecked(),
        )


# -- game art -----------------------------------------------------------------------

GAME_ART_HEADING = "Extracting game art…"
GAME_ART_INTRO = (
    "BartleBot is copying spell card pictures and icons from your game files. Back and Finish wait until "
    "it's done, and closing setup stops it."
)


class GameArtPage(FirstRunPage):
    """The last page whenever game art is on, as it is under Quick, and none is extracted yet.

    Entered, it starts :func:`~bartlebot.game.art_extraction.extract_art` off the GUI
    thread, in an :class:`~bartlebot.gui.settings.game_art_row.ArtExtraction`. Finish waits
    for the run to end, worked or not: a failure says why, is logged, and setup ends as
    usual, since art is optional everywhere. Closing setup stops the run, which leaves
    nothing half written, and leaves the page unanswered.

    Art already extracted keeps the page out of the walk, a rerun's too: extracting it
    again is Settings › General › Game art's job, after a game patch. Whether there was
    art is read once, when the page is built, so the page's own run finishing never takes
    it out of the walk while it is shown. A user who turned the art off before has none,
    so a rerun still offers it to them.
    """

    heading = GAME_ART_HEADING

    def __init__(self, options: OptionsPage, prefs: PreferenceStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = SettingsManager.instance()
        self._options = options
        self._had_art = card_art.is_available()
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)
        intro = ThemedTextLabel(GAME_ART_INTRO, css=f"font-size: {THEME.font_small_px}px;")
        intro.setWordWrap(True)
        column.addWidget(intro)
        self._extraction = ArtExtraction(prefs)
        self._extraction.finished.connect(lambda _ok: self.ready_changed.emit())
        column.addWidget(self._extraction)
        column.addStretch()

    @property
    def extraction(self) -> ArtExtraction:
        return self._extraction

    @classmethod
    def asks_first_run(cls, prefs: PreferenceStore) -> bool:
        # Art on disk answers the page as well as taking it out of the walk.
        return not art_answered(SettingsManager.instance())

    def applies(self, mode: SetupMode) -> bool:
        if self._had_art:
            return False
        return mode is SetupMode.QUICK or self._options.extract_art

    def answered(self) -> bool:
        return art_answered(self._settings)

    def ready(self) -> bool:
        return self._extraction.ended

    def busy(self) -> bool:
        # Held here while it runs: Back to the Options page could turn the art off under a
        # run that would install it anyway. Ended is set before ``finished`` is emitted,
        # while the task is still finishing.
        return self._extraction.running and not self._extraction.ended

    def enter(self) -> None:
        self._extraction.start()

    def stop(self) -> None:
        self._extraction.stop()

    def record(self) -> bool:
        return record_art_answered(self._settings)


#: Every setup page after Quick or Custom, in the order shown: what :func:`setup_pages` builds.
SETUP_PAGE_TYPES: tuple[type[FirstRunPage], ...] = (
    GameFolderPage,
    AccountsPage,
    LaunchProfilePage,
    CombatQuestionPage,
    QuicksellPage,
    RealmAndDelayPage,
    OptionsPage,
    GameArtPage,
)


def accounts_page(prefs: PreferenceStore) -> AccountsPage:
    """Core's Accounts page, saying what BartleBot does with the logins."""
    return AccountsPage(prefs, intro=ACCOUNTS_INTRO, empty=ACCOUNTS_EMPTY)


def launch_profile_page(prefs: PreferenceStore) -> LaunchProfilePage:
    """Core's Launch profile page, judging a resolution at BartleBot's panes as setup leaves them."""
    return LaunchProfilePage(prefs, setup_area=window_panes.setup_area)


def setup_pages(prefs: PreferenceStore, *, rerun: bool = False) -> list[FirstRunPage]:
    """Every setup page after Quick or Custom, in the order shown (``docs/FIRST_RUN.md``).

    *rerun* for File › Set up BartleBot…, where the Game folder page lets a user with no
    game found pass it.
    """
    options = OptionsPage()
    return [
        GameFolderPage(prefs, rerun=rerun),
        accounts_page(prefs),
        launch_profile_page(prefs),
        CombatQuestionPage(prefs),
        QuicksellPage(),
        RealmAndDelayPage(),
        options,
        GameArtPage(options, prefs),
    ]


def needs_asking(prefs: PreferenceStore) -> bool:
    """Whether a first run has anything to ask: core's :func:`~bartleby.qt.dialogs.first_run_dialog.needs_asking` over BartleBot's pages."""
    return core.needs_asking(prefs, SETUP_PAGE_TYPES)
