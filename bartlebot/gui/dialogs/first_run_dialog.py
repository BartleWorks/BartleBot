"""Setup: Quick or Custom, then a list of pages, each of which writes a real choice (epic #215).

Opened by ``AppController._after_window_shown`` while any page is unanswered, and again
from File › Set up BartleBot…. ``docs/FIRST_RUN.md`` lists the pages, what each writes,
and when each counts as answered.

WHY A QUESTION AND NOT A TOUR
-----------------------------

Tutorials get skipped and forgotten. A setup step that asks for a decision and writes it
to the config gets answered, and stays useful when it can be reached again later: OBS
Studio's auto-configuration wizard runs again from Tools. So each page asks something whose
answer is stored, and the same choice can be made again in Settings (for the combat page,
Settings › Combat).

THE PAGES
---------

A page is a :class:`FirstRunPage`: a heading, its body, and five methods. ``answered()``
says its setting already exists, so a first run passes it over. ``applies(mode)`` says it
belongs in the walk now, under Quick or Custom. ``ready()`` says its answer can be
recorded, and Next waits for it. ``record()`` writes its answer and says whether that
reached disk. ``enter()``, a no-op by default, runs each time the dialog turns to the page,
for a page that draws what an earlier page wrote.

The dialog opens on :class:`ModePage`, Quick or Custom, which is not stored: it only
decides which pages follow. **There is no Skip.** Next records the page shown and moves on
only once that write succeeded; a failed one keeps the user on the page and says so. Back
records nothing. Escape and the close button reject and write nothing more, so the next
launch opens on the first page still unanswered: closing keeps what was answered and asks
the rest next time (Plex's Skip, which left setup to be found again by hand, is the
failure this avoids).

On a first run the walk is the pages unanswered when the dialog opened; a page answered on
the way stays in it, so Back still reaches it. A rerun walks every page that applies, each
on the current value.

What each answer stores lives in a Qt-free module, as :mod:`bartlebot.shared.first_run`
does for the combat page, so BartleBuddy asks the same question and writes the same keys.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from enum import Enum
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from bartlebot.clients import character_roster
from bartlebot.clients.credentials import CredentialStore
from bartlebot.clients.install_location import apply_install
from bartlebot.gui import screen_fit
from bartlebot.gui.dialogs.new_bot_dialog import StartCard
from bartlebot.gui.game_embed.card_columns import CardColumn
from bartlebot.gui.game_embed.launch_cards import AccountCard, CardMode
from bartlebot.gui.settings.accounts_tab import add_account, delete_account, edit_account, reorder_accounts
from bartlebot.gui.settings.profile_editor import mark_unfittable_resolutions
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.widgets.buttons.action_button import ActionButton
from bartlebot.gui.widgets.inputs.game_folder_input import GameFolderInput, folder_status
from bartlebot.gui.widgets.labels.themed_label import ThemedTextLabel
from bartlebot.gui.widgets.layout.dialog_footer import dialog_footer
from bartlebot.settings_manager import VALID_RESOLUTIONS, SettingsManager
from bartlebot.shared.account_setup import accounts_answered, record_accounts_answered
from bartlebot.shared.first_run import RECOMMENDED, BossTactic, combat_question_answered, current_tactic, record_answer
from bartlebot.shared.game_install import SOURCE_NAMES, find_install, is_install, set_install_dir
from bartlebot.shared.launch_profiles import (
    EVERYONE,
    add_profile,
    build_profile,
    free_name,
    launch_profile_answered,
    matching_profile,
    profile_names,
    record_launch_profile_answered,
)
from bartlebot.shared.preferences import PreferenceStore

logger = logging.getLogger(__name__)

TITLE = "Set up BartleBot"
NEXT = "Next"
FINISH = "Finish"
BACK = "Back"
#: Under the heading: where the user is in the walk.
STEP = "Step {n} of {m}"
#: Under the page, when its answer did not reach disk.
SAVE_FAILED = "Couldn't save this answer. Try again, or close setup and it will be asked the next time BartleBot opens."
RECOMMENDED_DETAIL = "Recommended"


class SetupMode(Enum):
    """Quick asks what a first run needs; Custom adds the options Quick sets to their defaults."""

    QUICK = "quick"
    CUSTOM = "custom"


class FirstRunPage(QWidget):
    """One page of setup: a heading, a body, whether it is answered, and a way to store its answer."""

    #: Emitted when the page's answer changes what follows: the walk is recounted.
    walk_changed = pyqtSignal()
    #: Emitted when :meth:`ready` may have changed: Next is enabled or not again.
    ready_changed = pyqtSignal()

    #: The question the page asks, shown above it.
    heading = ""

    def answered(self) -> bool:
        """Whether its setting already exists, so a first run need not ask."""
        return False

    def applies(self, mode: SetupMode) -> bool:
        """Whether the page belongs in the walk now: under *mode*, and with what is set up so far."""
        return True

    def ready(self) -> bool:
        """Whether the answer shown can be recorded. Next is disabled while it can't."""
        return True

    def enter(self) -> None:
        """Called each time the dialog turns to this page, by Next or Back.

        For a page that draws what an earlier page wrote: the Launch profile page lists
        the accounts the Accounts page added.
        """

    def record(self) -> bool:
        """Store this page's answer. Whether it reached disk."""
        raise NotImplementedError


class _CardsPage(FirstRunPage):
    """A page answered by picking one :class:`StartCard`, the recommended one first and selected.

    A card selects itself on taking focus, so the selected one takes it on show: left to
    the focus chain, showing the dialog hands it to the first card, which changes the
    answer. With none selected the page itself takes it, for the same reason.
    """

    def __init__(self, choices: Sequence[tuple[object, str, str, bool]], parent: QWidget | None = None) -> None:
        """*choices* are (value, name, blurb, recommended), in the order shown."""
        super().__init__(parent)
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(12)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self.column.addLayout(grid)

        self._values = [value for value, *_ in choices]
        self._cards: list[StartCard] = []
        for index, (value, name, blurb, recommended) in enumerate(choices):
            card = StartCard(name, blurb, detail=RECOMMENDED_DETAIL if recommended else "")
            card.chosen.connect(lambda v=value: self._choose(v))
            self._cards.append(card)
            grid.addWidget(card, 0, index)
            grid.setColumnStretch(index, 1)
        self._value: object | None = None

    @property
    def cards(self) -> list[StartCard]:
        """The answer cards, in the order shown: the recommended one first."""
        return list(self._cards)

    def card_for(self, value: object) -> StartCard:
        return self._cards[self._values.index(value)]

    def _choose(self, value: object | None) -> None:
        changed = value != self._value
        self._value = value
        for each, card in zip(self._values, self._cards):
            card.set_selected(each == value)
        if changed:
            self.walk_changed.emit()

    def showEvent(self, event) -> None:
        # Here rather than in the constructor, where the page was not yet in the dialog
        # and the focus was lost with the reparenting.
        super().showEvent(event)
        if self._value is None:
            self.setFocus()
        else:
            self.card_for(self._value).setFocus()


# -- Quick or Custom ------------------------------------------------------------

MODE_HEADING = "How would you like to set up BartleBot?"
#: Each mode's card: its name, and what it asks.
MODE_CARDS: dict[SetupMode, tuple[str, str]] = {
    SetupMode.QUICK: (
        "Quick setup",
        "Only the questions BartleBot needs answered before your first run. Everything else starts "
        "on its usual setting, which you can change later in Settings.",
    ),
    SetupMode.CUSTOM: (
        "Custom setup",
        "The same questions, then a page of options Quick setup leaves at their defaults, each shown "
        "with its default selected.",
    ),
}


class ModePage(_CardsPage):
    """The first page: Quick (recommended, selected) or Custom. Not stored; it decides which pages follow."""

    heading = MODE_HEADING

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            [(mode, *MODE_CARDS[mode], mode is SetupMode.QUICK) for mode in SetupMode],
            parent,
        )
        self.column.addStretch()
        self.select(SetupMode.QUICK)

    @property
    def mode(self) -> SetupMode:
        return self._value

    def select(self, mode: SetupMode) -> None:
        self._choose(mode)

    def record(self) -> bool:
        return True


# -- the combat question ------------------------------------------------------------

COMBAT_HEADING = "How should your team take on bosses?"
#: Each answer's card: its name, and what it does in a fight.
TACTIC_CARDS: dict[BossTactic, tuple[str, str]] = {
    BossTactic.ONE_HITTER: (
        "One hitter, the others support",
        "P1 attacks. While a boss is up, the others blade P1 and trap and feint the boss, and P1 "
        "casts last. In fights with no boss, everyone attacks.",
    ),
    BossTactic.EVERYONE_ATTACKS: (
        "Everyone attacks",
        "Every wizard plays its own attacks, in boss fights and every other fight.",
    ),
}
#: Where the defaults are changed later, held together so a wrap never splits it.
SETTINGS_COMBAT = "Settings › Combat"
COMBAT_NOTE = (
    "Fights with one wizard use Default Solo Duel. You can change either default later in "
    f"{SETTINGS_COMBAT}, and tailor any bot's combat on its Combat tab."
)


class CombatQuestionPage(_CardsPage):
    """How the team fights bosses: one card per :class:`BossTactic`, the recommended one first.

    It opens on the answer the stored Team default already is. On a rerun that default can
    be a duel neither answer names, chosen in Settings › Combat; then no card is selected,
    and Next with none selected keeps that duel.
    """

    heading = COMBAT_HEADING

    def __init__(self, prefs: PreferenceStore, parent: QWidget | None = None) -> None:
        tactics = sorted(BossTactic, key=lambda tactic: tactic is not RECOMMENDED)
        super().__init__([(t, *TACTIC_CARDS[t], t is RECOMMENDED) for t in tactics], parent)
        self._prefs = prefs
        self._note = ThemedTextLabel(COMBAT_NOTE, css=f"font-size: {THEME.font_small_px}px;")
        self._note.setWordWrap(True)
        self.column.addWidget(self._note)
        self.column.addStretch()
        self.select(current_tactic(prefs))

    @property
    def tactic(self) -> BossTactic | None:
        """The answer selected now; None keeps a Team default neither answer names."""
        return self._value

    @property
    def note_text(self) -> str:
        return self._note.text()

    def select(self, tactic: BossTactic | None) -> None:
        self._choose(tactic)

    def answered(self) -> bool:
        return combat_question_answered(self._prefs)

    def record(self) -> bool:
        return record_answer(self._prefs, self._value)


# -- the game folder ----------------------------------------------------------------

GAME_FOLDER_HEADING = "Where is Wizard101 installed?"
GAME_FOLDER_INTRO = (
    "BartleBot starts the game from this folder, the one holding its Bin folder. The KingsIsle "
    "launcher installs it in C:\\ProgramData\\KingsIsle Entertainment\\Wizard101, and Steam in "
    "steamapps\\common\\Wizard101 inside a Steam library."
)


class GameFolderPage(FirstRunPage):
    """Where the game is: shown on a first run only when no install is found.

    On a rerun it opens on the folder in use, and Next leaves a detected folder detected:
    only a folder the user changed to is stored, so an install that moves is still found.
    Next is enabled only for a folder holding the client.
    """

    heading = GAME_FOLDER_HEADING

    def __init__(self, prefs: PreferenceStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._prefs = prefs
        self._found = find_install(prefs)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        intro = ThemedTextLabel(GAME_FOLDER_INTRO, css=f"font-size: {THEME.font_small_px}px;")
        intro.setWordWrap(True)
        column.addWidget(intro)
        self._input = GameFolderInput()
        column.addWidget(self._input)
        self._status = ThemedTextLabel("", css=f"font-size: {THEME.font_small_px}px;")
        self._status.setWordWrap(True)
        column.addWidget(self._status)
        column.addStretch()
        if self._found is not None:
            self._input.set_path(self._found.path)
        self._input.changed.connect(self._on_changed)
        self._on_changed()

    @property
    def folder_input(self) -> GameFolderInput:
        return self._input

    @property
    def status_text(self) -> str:
        return self._status.text()

    def _on_changed(self) -> None:
        found_path = self._found.path if self._found is not None else None
        source = SOURCE_NAMES[self._found.source] if self._found is not None else ""
        text, problem = folder_status(self._input.path(), found_path, source)
        self._status.setText(text)
        self._status.set_color("error" if problem else "text_muted")
        self.ready_changed.emit()

    def answered(self) -> bool:
        return self._found is not None

    def ready(self) -> bool:
        return is_install(self._input.path())

    def record(self) -> bool:
        path = Path(self._input.path())
        if self._found is not None and path == self._found.path:
            return True
        if not set_install_dir(self._prefs, path):
            return False
        self._found = apply_install(self._prefs)
        return True


# -- accounts -----------------------------------------------------------------------

ACCOUNTS_HEADING = "Which accounts should BartleBot log in to?"
ACCOUNTS_INTRO = (
    "BartleBot logs these accounts in when it launches the game for you. Passwords are kept in "
    "Windows Credential Manager, never in a file."
)
#: Beneath Add account while there is none: leaving the page empty is an answer too.
ACCOUNTS_EMPTY = (
    "With no account, BartleBot can still hook game windows you start yourself. Accounts can be "
    "added at any time from Launch › Manage Accounts…"
)
ADD_ACCOUNT = "Add account"


class AccountsPage(FirstRunPage):
    """The accounts to log in: the cards Settings › Game draws, on the same editor and the same writes.

    Add, Edit and Remove each write ``accounts.json`` (and a password to Credential
    Manager) as they happen, through ``accounts_tab``'s card actions, so Next has nothing
    left to store but the page answered. It may be left empty, and is passed over on a
    first run once an account exists, from either product.
    """

    heading = ACCOUNTS_HEADING

    def __init__(self, prefs: PreferenceStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._prefs = prefs
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        intro = ThemedTextLabel(ACCOUNTS_INTRO, css=f"font-size: {THEME.font_small_px}px;")
        intro.setWordWrap(True)
        column.addWidget(intro)
        # No heading of its own: the page's is above it.
        self._column = CardColumn("", ACCOUNTS_EMPTY, ADD_ACCOUNT, show_settings_link=False)
        self._column.setMinimumHeight(220)
        self._column.add_requested.connect(self._on_add)
        self._column.reordered.connect(self._on_reordered)
        column.addWidget(self._column, 1)
        self._refresh()

    @property
    def accounts_column(self) -> CardColumn:
        return self._column

    def _refresh(self) -> None:
        accounts = SettingsManager.instance().accounts
        cards: list[QWidget] = []
        for account in accounts:
            username = account.get("username", "")
            card = AccountCard(
                account, character_roster.load(username) if username else [], accounts, mode=CardMode.MANAGE
            )
            card.edit_account_requested.connect(self._on_edit)
            card.delete_account_requested.connect(self._on_delete)
            cards.append(card)
        self._column.set_cards(cards)

    def _changed(self, written: bool) -> None:
        if written:
            self._refresh()
            # How many accounts there are decides whether the Launch profile page follows.
            self.walk_changed.emit()

    def _on_add(self) -> None:
        self._changed(add_account(self))

    def _on_edit(self, username: str) -> None:
        self._changed(bool(username) and edit_account(username, self))

    def _on_delete(self, username: str) -> None:
        self._changed(bool(username) and delete_account(username, self))

    def _on_reordered(self, order: list) -> None:
        self._changed(reorder_accounts(list(order)))

    def answered(self) -> bool:
        return accounts_answered(self._prefs, SettingsManager.instance().account_store)

    def record(self) -> bool:
        return record_accounts_answered(self._prefs)


# -- the launch profile -------------------------------------------------------------

LAUNCH_PROFILE_HEADING = "Which accounts launch together?"
LAUNCH_PROFILE_INTRO = (
    "BartleBot makes a launch profile of the accounts checked here, to start them all at once from "
    "the Launch menu or the launch screen. They launch in this order: the first is Player 1. The "
    "resolution starts at the largest that fits them all on this screen; pick a smaller one to save "
    "your computer some work. More profiles are made in Settings › Game."
)
RESOLUTION_LABEL = "Resolution:"
#: Under the checklist: what Next will do with the accounts checked now.
PROFILE_WILL_MAKE = "Next makes the profile “{name}” with these {n} accounts."
PROFILE_EXISTS = "These accounts already launch together as “{name}”."
PROFILE_TOO_FEW = "Check two or more accounts to make a profile. With fewer, none is made."
#: Under the checklist, after PROFILE_WILL_MAKE, when no resolution fits that many accounts.
PROFILE_NO_FIT = (
    "No resolution fits {n} accounts side by side on this screen, so the profile uses the game "
    "resolution in Settings › Game until you give it one there."
)


class LaunchProfilePage(FirstRunPage):
    """One launch profile from a checklist: every account, checked, in the order of the Accounts page.

    Next makes a profile named Everyone (or the first free name after it) of the accounts
    checked, through the function Settings › Game's Add uses. Fewer than two checked makes
    none, and still records the page. The list is read each time the page is entered, so
    accounts added on the page before are on it.

    The profile gets a resolution of its own, from a combo that starts on the largest that
    fits the accounts checked (``screen_fit.setup_resolution``). Left on the global one, two
    accounts on a 1080p screen would make a profile too wide to launch. A size the user
    picks is kept while it still fits the accounts checked.
    """

    heading = LAUNCH_PROFILE_HEADING

    def __init__(self, prefs: PreferenceStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._prefs = prefs
        #: Accounts the user unchecked, kept unchecked when the list is drawn again.
        self._left_out: set[str] = set()
        self._boxes: list[tuple[str, QCheckBox]] = []
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        intro = ThemedTextLabel(LAUNCH_PROFILE_INTRO, css=f"font-size: {THEME.font_small_px}px;")
        intro.setWordWrap(True)
        column.addWidget(intro)
        self._list = QVBoxLayout()
        self._list.setContentsMargins(0, 4, 0, 4)
        self._list.setSpacing(4)
        column.addLayout(self._list)
        #: The resolution the user picked, kept while it fits; None follows the largest.
        self._picked: tuple[int, int] | None = None
        self._res_combo = QComboBox(self)
        for w, h in VALID_RESOLUTIONS:
            self._res_combo.addItem(f"{w}×{h}", userData=(w, h))
        self._res_combo.activated.connect(self._on_resolution_picked)
        res_form = QFormLayout()
        res_form.setContentsMargins(0, 0, 0, 0)
        res_form.addRow(RESOLUTION_LABEL, self._res_combo)
        column.addLayout(res_form)
        self._status = ThemedTextLabel("", css=f"font-size: {THEME.font_small_px}px;")
        self._status.setWordWrap(True)
        column.addWidget(self._status)
        column.addStretch()
        self.enter()

    @staticmethod
    def _store():
        return SettingsManager.instance().account_store

    @property
    def boxes(self) -> list[QCheckBox]:
        """One check box per account, in player order."""
        return [box for _, box in self._boxes]

    @property
    def checked(self) -> list[str]:
        """The usernames checked, in player order: the profile Next makes."""
        return [username for username, box in self._boxes if box.isChecked()]

    @property
    def status_text(self) -> str:
        return self._status.text()

    @property
    def resolution_combo(self) -> QComboBox:
        return self._res_combo

    @property
    def resolution(self) -> tuple[int, int] | None:
        """The resolution Next gives the profile, or None, to follow the global one, when none fits."""
        resolution = self._res_combo.currentData()
        if resolution is None or not self._fits(tuple(resolution)):
            return None
        return tuple(resolution)

    def _fits(self, resolution: tuple[int, int]) -> bool:
        return screen_fit.fit_problem(resolution, len(self.checked), self, screen_fit.setup_area(self)) is None

    def _on_resolution_picked(self, index: int) -> None:
        self._picked = self._res_combo.itemData(index)
        self._show_status()

    def enter(self) -> None:
        for _, box in self._boxes:
            self._list.removeWidget(box)
            # Hidden first: out of the layout, it would be drawn at the page's corner
            # until the deferred delete runs.
            box.hide()
            box.deleteLater()
        self._boxes = []
        for account in self._store().accounts:
            username = account.get("username", "")
            box = QCheckBox(CredentialStore.display_name(username, account.get("alias") or None), self)
            box.setChecked(username not in self._left_out)
            box.toggled.connect(lambda on, u=username: self._toggled(u, on))
            self._list.addWidget(box)
            self._boxes.append((username, box))
        self._show_status()

    def _toggled(self, username: str, on: bool) -> None:
        if on:
            self._left_out.discard(username)
        else:
            self._left_out.add(username)
        self._show_status()

    def _show_status(self) -> None:
        checked = self.checked
        existing = matching_profile(self._store(), checked)
        makes = len(checked) >= 2 and existing is None
        self._res_combo.setEnabled(makes)
        if len(checked) < 2:
            self._status.setText(PROFILE_TOO_FEW)
        elif existing is not None:
            self._status.setText(PROFILE_EXISTS.format(name=existing))
        else:
            name = free_name(EVERYONE, profile_names(self._store()))
            text = PROFILE_WILL_MAKE.format(name=name, n=len(checked))
            if not self._choose_resolution(len(checked)):
                text = f"{text} {PROFILE_NO_FIT.format(n=len(checked))}"
            self._status.setText(text)

    def _choose_resolution(self, count: int) -> bool:
        """Gray what *count* accounts can't fit, and select the pick or the largest. Whether any fits."""
        mark_unfittable_resolutions(self._res_combo, count, self, screen_fit.setup_area(self))
        target = self._picked if self._picked is not None and self._fits(self._picked) else None
        if target is None:
            target = screen_fit.setup_resolution(count, self)
        if target is None:
            return False
        index = next(i for i in range(self._res_combo.count()) if self._res_combo.itemData(i) == target)
        self._res_combo.setCurrentIndex(index)
        return True

    def applies(self, mode: SetupMode) -> bool:
        return len(self._store().accounts) >= 2

    def answered(self) -> bool:
        return launch_profile_answered(self._prefs, self._store())

    def record(self) -> bool:
        checked = self.checked
        store = self._store()
        if len(checked) >= 2 and matching_profile(store, checked) is None:
            profile = build_profile(free_name(EVERYONE, profile_names(store)), checked, self.resolution)
            if not add_profile(store, profile):
                return False
        return record_launch_profile_answered(self._prefs)


def setup_pages(prefs: PreferenceStore) -> list[FirstRunPage]:
    """Every setup page after Quick or Custom, in the order shown (``docs/FIRST_RUN.md``)."""
    return [GameFolderPage(prefs), AccountsPage(prefs), LaunchProfilePage(prefs), CombatQuestionPage(prefs)]


def needs_asking(pages: Sequence[FirstRunPage]) -> bool:
    """Whether a first run has anything to ask: a page unanswered that applies under some mode.

    A page that applies under neither, such as the Launch profile page with fewer than two
    accounts, opens nothing on its own: the walk would be Quick or Custom and then
    nothing. It is still in the dialog's walk when another page opens it, so accounts
    added on the way bring it in.
    """
    return any(not page.answered() and any(page.applies(mode) for mode in SetupMode) for page in pages)


# -- the dialog ---------------------------------------------------------------------


class FirstRunDialog(QDialog):
    """Quick or Custom, then the pages, one at a time. Accepted means Finish on the last page.

    *pages* are the pages after Quick or Custom. On a first run the dialog walks those not
    yet answered; with *rerun* it walks every page, each on the current value. Either way a
    page is in the walk only while it ``applies`` under the mode chosen.
    """

    def __init__(self, pages: Sequence[FirstRunPage], *, rerun: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not pages:
            raise ValueError("the setup dialog needs at least one page")
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(560)
        self._mode_page = ModePage()
        self._pages = [self._mode_page, *pages]
        # Fixed here, not asked as the user goes: a page answered on the way stays in the walk.
        self._asked = [page for page in pages if rerun or not page.answered()]
        self._rerun = rerun
        #: Whether the first show has placed the dialog; a later show leaves it where it is.
        self._placed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(4)
        self._heading = QLabel()
        self._heading.setFont(THEME.ui_font(THEME.font_heading_px, bold=True))
        self._heading.setWordWrap(True)
        layout.addWidget(self._heading)
        self._step = ThemedTextLabel("", css=f"font-size: {THEME.font_small_px}px;")
        layout.addWidget(self._step)
        layout.addSpacing(8)
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)
        for page in self._pages:
            self._stack.addWidget(page)
            page.walk_changed.connect(self._refresh)
            page.ready_changed.connect(self._refresh)
        self._error = ThemedTextLabel(SAVE_FAILED, color="error", css=f"font-size: {THEME.font_small_px}px;")
        self._error.setWordWrap(True)
        self._error.setVisible(False)
        layout.addWidget(self._error)
        layout.addSpacing(8)

        # Back takes the slot beside the commit that is Cancel elsewhere: there is no
        # Cancel here, and Escape or the close button is how setup is left.
        footer = dialog_footer(
            self,
            commit_text=NEXT,
            commit_icon="arrow_right",
            on_commit=self._advance,
            cancel_text=BACK,
            cancel_icon="arrow_left",
            on_cancel=self._go_back,
        )
        self._commit = footer.commit
        self._back = footer.cancel
        layout.addLayout(footer.layout)
        self._show(self._mode_page)

    @property
    def rerun(self) -> bool:
        return self._rerun

    @property
    def pages(self) -> list[FirstRunPage]:
        """Every page the dialog holds, Quick or Custom first, whether or not it is in the walk."""
        return list(self._pages)

    @property
    def mode_page(self) -> ModePage:
        return self._mode_page

    @property
    def walk(self) -> list[FirstRunPage]:
        """The pages the user steps through now, in order. Recounted as the mode, or a page's answer, changes."""
        mode = self._mode_page.mode
        return [self._mode_page, *(page for page in self._asked if page.applies(mode))]

    @property
    def current(self) -> FirstRunPage:
        return self._stack.currentWidget()

    @property
    def heading_text(self) -> str:
        return self._heading.text()

    @property
    def step_text(self) -> str:
        return self._step.text()

    @property
    def error_shown(self) -> bool:
        return not self._error.isHidden()

    @property
    def commit_button(self) -> ActionButton:
        return self._commit

    @property
    def back_button(self) -> ActionButton:
        return self._back

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._placed:
            self._placed = True
            self._center_on_window()

    def _center_on_window(self) -> None:
        """Center on the main window, or on its screen while the window is maximized.

        Qt centers a dialog over its parent's geometry, but at startup that geometry is
        stale: the window was just shown maximized, and Windows applies the maximize after
        the timer that opens setup has fired. Qt then centers on the window's normal-size
        rectangle, which puts the dialog near the screen's top left. A maximized window
        fills its screen's available area, so that area is the one to center on.
        """
        parent = self.parentWidget()
        window = parent.window() if parent is not None else None
        screen = (window or self).screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        usable = screen.availableGeometry()
        if window is not None and window.isVisible() and not (window.isMaximized() or window.isFullScreen()):
            center = window.frameGeometry().center()
        else:
            center = usable.center()
        frame = self.frameGeometry()
        frame.moveCenter(center)
        # Kept on the screen, for a window mostly dragged off it.
        frame.moveLeft(max(usable.left(), min(frame.left(), usable.right() - frame.width())))
        frame.moveTop(max(usable.top(), min(frame.top(), usable.bottom() - frame.height())))
        self.move(frame.topLeft())

    def _show(self, page: FirstRunPage) -> None:
        page.enter()
        self._stack.setCurrentWidget(page)
        self._heading.setText(page.heading)
        self._error.setVisible(False)
        self._refresh()

    def _refresh(self) -> None:
        """The step count, Back, and Next or Finish, for the page shown and the walk as it stands."""
        walk = self.walk
        index = walk.index(self.current)
        self._step.setText(STEP.format(n=index + 1, m=len(walk)))
        self._back.setVisible(index > 0)
        self._commit.setText(FINISH if index == len(walk) - 1 else NEXT)
        self._commit.setEnabled(self.current.ready())

    def _advance(self) -> None:
        """Next or Finish: record the page shown, and move on only once that reached disk."""
        page = self.current
        if not page.ready():
            return
        if not page.record():
            logger.warning("Could not save the setup answer to %r; the page stays open", page.heading)
            self._error.setVisible(True)
            return
        # Recounted after the write: an answer can change which pages follow.
        walk = self.walk
        index = walk.index(page)
        if index == len(walk) - 1:
            self.accept()
        else:
            self._show(walk[index + 1])

    def _go_back(self) -> None:
        walk = self.walk
        index = walk.index(self.current)
        if index > 0:
            self._show(walk[index - 1])
