"""The cards on the Quick Launch screen: one per account, one per launch profile.

Launching is the only thing this app does before a bot is open, and the screen that does
it used to say less about each entry than the bot library says about a bot file -- a bare
``QPushButton`` carrying a name and nothing else. A launch profile was the worst of it:
its name says nothing about which accounts it brings, which is the one thing you need to
know before pressing it.

``_BotCard`` in gui/canvas/bot_canvas.py is the shape these follow, down to the hover
handling: a styled ``QWidget`` with an ``objectName``-scoped stylesheet, a bold name row,
pills beside it and quieter secondary lines below.

CLICK MODEL

The whole card launches. That is the primary verb here and it keeps the one-click launch
the buttons had; the alternative -- select, then press Launch -- would put a second click
in front of the app's most common action to match a library screen whose primary verb is
"open", not "run".

Configuration is a hover-revealed button that opens the existing editor, which is what
Chrome's profile picker, Parallels' VM tiles and the Steam library all converge on for a
tile whose primary action is "launch". Nothing on the card is a form control: a combo box
on a surface that launches on click is a hole in the hit target, and one that would have
to swallow its own clicks to be safe.

WHAT THE PILLS MEAN

A launch profile's pills are its accounts, in launch order. That ordering is real -- it
decides which window becomes Player 1 -- so the pills are laid out in it rather than
sorted for looks.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from bartlebot import streamer_mode
from bartlebot.credentials import CredentialStore
from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.tag_pill import NeutralPill

# Characters listed in full on an account card before the rest become "+N more", used
# only when nothing has told the card how wide it is -- during construction, and in the
# tests that build the line without a QApplication. A live card measures instead: a fixed
# count truncates a card with room for two more names, which is what the width-aware
# path in ``fit_character_summary`` exists to avoid.
MAX_CHARACTERS_SHOWN = 4

# Rows of names a live card will grow to before the rest become "+N more". The account
# column has vertical space to spare -- five accounts do not fill it -- so the limit that
# matters is width, and a card is allowed to use three lines of it.
MAX_CHARACTER_ROWS = 3

NO_CHARACTERS_HINT = "No characters known yet — launch one to learn them"

# What separates two names, and what follows the last one when some were dropped.
_SEP = " · "
_MORE_GAP = "  "

_EDIT_BTN_SIZE = 22


# ---------------------------------------------------------------------------
# Pure text-building. Separated from the widgets so it can be tested without a
# QApplication, as CLAUDE.md requires of anything inside a QWidget subclass.
# ---------------------------------------------------------------------------


def character_names(roster: list[dict]) -> list[str]:
    """The names to show for *roster*, honouring Streamer Mode.

    Positions come from the roster's own order, so a character reads the same way every
    time the card is rebuilt -- which is the only thing that makes the placeholder usable
    for telling two of them apart.
    """
    return [streamer_mode.display_name(entry.get("name"), index) for index, entry in enumerate(roster)]


def character_summary(roster: list[dict]) -> str:
    """One line describing an account's characters, or a hint when none are known.

    The empty case says *why* it is empty rather than just that it is: nothing can be
    known about an account until a character on it has been played through BartleBot,
    and a bare "(none)" reads like a fault.
    """
    if not roster:
        return NO_CHARACTERS_HINT
    names = character_names(roster)
    if len(names) <= MAX_CHARACTERS_SHOWN:
        return _SEP.join(names)
    return _more_line(names, MAX_CHARACTERS_SHOWN)


def _more_line(names: list[str], shown: int) -> str:
    return _SEP.join(names[:shown]) + f"{_MORE_GAP}+{len(names) - shown} more"


def _pack_rows(pieces: list[tuple[str, str]], available: int, measure: Callable[[str], int]) -> list[str]:
    """Greedily lay (separator, text) *pieces* into rows no wider than *available*.

    The rows are built here rather than left to ``QLabel``'s own word wrap because Qt
    wraps at every space -- including the ones inside a two-word wizard name and inside
    the separator -- so a name would be split across two lines. Breaking only between
    names is the whole point of the line.

    A piece wider than the row on its own still gets one: the label elides it, which
    reads better than an empty card.
    """
    rows: list[str] = []
    current = ""
    for separator, text in pieces:
        if not current:
            current = text
            continue
        candidate = current + separator + text
        if measure(candidate) <= available:
            current = candidate
        else:
            rows.append(current)
            current = text
    if current:
        rows.append(current)
    return rows


def _pieces(names: list[str], shown: int) -> list[tuple[str, str]]:
    """The pieces of a line showing the first *shown* of *names*, with a tail if it drops any."""
    pieces = [(_SEP, name) for name in names[:shown]]
    if shown < len(names):
        pieces.append((_MORE_GAP, f"+{len(names) - shown} more"))
    return pieces


def fit_character_summary(
    names: list[str],
    available: int,
    measure: Callable[[str], int],
    max_rows: int = MAX_CHARACTER_ROWS,
) -> str:
    """As many of *names* as fit in *max_rows* rows of *available* pixels, then "+N more".

    Measured rather than counted because what a card can show is a property of its width
    and the names in it, not a constant: four short names and one long one are not the
    same line. *measure* is passed in so this stays testable without a QApplication --
    the card hands it a ``QFontMetrics.horizontalAdvance``.

    Several rows because the account column has vertical room to spare -- five accounts
    do not fill it -- and horizontal room is what it is short of. Three is where that
    stops: a card taller than that stops being a card, and the count pill beside the name
    already says how many characters there are.

    The tail is laid out as part of each candidate rather than measured on its own, since
    adding it is what can push a line that just fitted onto one more row.

    One name is always shown. A card too narrow for even that has the label elide it,
    which says more than "+3 more" on its own does.
    """
    if not names:
        return NO_CHARACTERS_HINT
    if available <= 0:
        return _SEP.join(names)
    for shown in range(len(names), 0, -1):
        rows = _pack_rows(_pieces(names, shown), available, measure)
        if len(rows) <= max_rows:
            return "\n".join(rows)
    # Not even one name and its tail fit the rows allowed. The tail is what gets dropped
    # by the row limit, so it is put back on the last row and left to overflow: an elided
    # line still says there are more characters, whereas a name alone claims there are not.
    rows = _pack_rows(_pieces(names, 1), available, measure)[:max_rows]
    rows[-1] += f"{_MORE_GAP}+{len(names) - 1} more"
    return "\n".join(rows)


def account_display_names(usernames: list[str], accounts: list[dict]) -> list[str]:
    """Display names for *usernames*, in the order given.

    Order is preserved because a profile's account order decides which window becomes
    Player 1 — sorting these for looks would misreport the launch.

    A username the accounts list no longer holds still gets a pill, built from the raw
    username: dropping it would make a profile referring to a deleted account look
    complete, and that profile will fail to launch.
    """
    meta = {a.get("username"): a for a in accounts}
    out: list[str] = []
    for username in usernames:
        entry = meta.get(username)
        if entry is None:
            out.append(CredentialStore.display_name(username, None))
        else:
            out.append(CredentialStore.display_name(username, entry.get("alias") or None))
    return out


def resolution_label(profile: dict) -> str | None:
    """``"1280×720"`` for a profile that pins a resolution, else None.

    None means the profile follows the app setting, which the card shows nothing for: a
    pill on every card would be noise, and the interesting case is the profile that
    differs.
    """
    resolution = profile.get("resolution")
    if not resolution or len(resolution) != 2:
        return None
    return f"{resolution[0]}×{resolution[1]}"


def profile_summary(profile: dict) -> str:
    """The quiet line under a profile's pills: how many clients it launches."""
    count = len(profile.get("accounts") or [])
    if count == 0:
        return "No accounts — this profile cannot launch"
    return f"{count} client{'s' if count != 1 else ''}"


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class _LaunchCard(ThemedMixin, QWidget):
    """Shared frame, hover behaviour and click-to-launch for both card types.

    Hover is state rather than a QSS pseudo-class for the same reason as ``_BotCard``:
    the frame and the labels are restyled by one pass, and ``_hovered`` picks which
    colours that pass uses. A pseudo-class would restyle the frame and leave every label
    in the old palette after a theme switch.
    """

    launch_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("LaunchCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hovered = False
        # Every plain label is held so apply_theme_styles can re-derive its colour.
        # The pills are not: NeutralPill owns its own.
        self._name_lbl: QLabel | None = None
        self._detail_lbl: QLabel | None = None
        self._quiet_lbl: QLabel | None = None

    def apply_theme_styles(self) -> None:
        bg = THEME.node_fill if self._hovered else THEME.surface
        border = THEME.accent if self._hovered else THEME.border
        self.setStyleSheet(f"#LaunchCard {{ background: {bg}; border: 1px solid {border}; border-radius: 4px; }}")
        if self._name_lbl is not None:
            self._name_lbl.setStyleSheet(f"color: {THEME.text}; background: transparent;")
        for lbl, color in ((self._detail_lbl, THEME.text_muted), (self._quiet_lbl, THEME.text_subtle)):
            if lbl is not None:
                lbl.setStyleSheet(f"color: {color}; font-size: 10px; background: transparent;")

    def _make_name_label(self, text: str) -> QLabel:
        label = QLabel(text)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        self._name_lbl = label
        return label

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.refresh_theme_styles()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.refresh_theme_styles()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.launch_requested.emit()
        super().mousePressEvent(event)


class AccountCard(_LaunchCard):
    """One account: its display name, and the characters known to live on it.

    The character line is why this card exists. ``wizard_names`` only knows a character
    while its client is hooked, which is never true of an account on this screen, so the
    names come from ``character_roster`` — learned as clients connect and persisted per
    account.
    """

    launch_account_requested = pyqtSignal(str, str)  # (username, display_name)

    def __init__(self, account: dict, roster: list[dict], parent=None) -> None:
        super().__init__(parent)
        self._username = account.get("username", "")
        self._display_name = CredentialStore.display_name(self._username, account.get("alias") or None)
        self._roster = roster
        self._build()
        self.refresh_theme_styles()
        self.launch_requested.connect(lambda: self.launch_account_requested.emit(self._username, self._display_name))

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        header.addWidget(self._make_name_label(self._display_name), 0)
        if self._roster:
            header.addWidget(NeutralPill(str(len(self._roster))), 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch()
        layout.addLayout(header)

        self._detail_lbl = QLabel(character_summary(self._roster))
        # Ignored, with a minimum of one pixel: the line is fitted to whatever width the
        # card ends up with, so its own text must never be what decides that width.
        # Left as Preferred, a long roster would push the card's minimum width out and
        # take the launch screen's minimum size with it.
        self._detail_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._detail_lbl.setMinimumWidth(1)
        # Word wrap stays off: the rows are built by fit_character_summary, which breaks
        # between names. Qt's own wrap breaks at any space, splitting a two-word wizard
        # name across two lines.
        self._detail_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._detail_lbl)

    def resizeEvent(self, event) -> None:
        """Re-fit the character line: how many names fit is a function of the width.

        The card is only ever as wide as the column it sits in, and that is not known
        until it is laid out -- so the constructor's count-based line is a placeholder
        that the first resize replaces.
        """
        super().resizeEvent(event)
        self._refit_characters()

    def _refit_characters(self) -> None:
        if self._detail_lbl is None or not self._roster:
            return
        margins = self.layout().contentsMargins()
        available = self.width() - margins.left() - margins.right()
        metrics = QFontMetrics(self._detail_lbl.font())
        self._detail_lbl.setText(
            fit_character_summary(character_names(self._roster), available, metrics.horizontalAdvance)
        )


class ProfileCard(_LaunchCard):
    """One launch profile: which accounts it brings, and any resolution it pins.

    The account pills are the point. A profile's name says nothing about its membership,
    which is the single hardest thing to see on the old screen — you had to open Settings
    to find out what a profile would do.
    """

    launch_profile_requested = pyqtSignal(str)  # (profile_name)
    edit_profile_requested = pyqtSignal(str)  # (profile_name)

    def __init__(self, profile: dict, accounts: list[dict], parent=None) -> None:
        super().__init__(parent)
        self._profile = profile
        self._name = profile.get("name", "")
        self._accounts = accounts
        # Declared before _build: the edit button is a real child widget so that it
        # consumes its own clicks rather than the card launching underneath it, and
        # apply_theme_styles can be reached before _build has made it.
        self._edit_btn: QToolButton | None = None
        self._build()
        self.refresh_theme_styles()
        self.launch_requested.connect(lambda: self.launch_profile_requested.emit(self._name))

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        header.addWidget(self._make_name_label(self._name), 0)
        if (resolution := resolution_label(self._profile)) is not None:
            header.addWidget(NeutralPill(resolution), 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch()

        # Revealed on hover: it is secondary to launching, and a permanently visible
        # button on every card competes with the name for attention.
        self._edit_btn = QToolButton(self)
        self._edit_btn.setFixedSize(_EDIT_BTN_SIZE, _EDIT_BTN_SIZE)
        # The button's space is held while it is hidden, or revealing it on hover would
        # resize the card under the pointer -- which can move the card out from under it,
        # hiding the button again, resizing back: the whole card jitters as long as the
        # pointer sits near its edge.
        policy = self._edit_btn.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self._edit_btn.setSizePolicy(policy)
        self._edit_btn.setAutoRaise(True)
        self._edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_btn.setToolTip(f"Edit the '{self._name}' profile")
        self._edit_btn.setVisible(False)
        self._edit_btn.clicked.connect(lambda: self.edit_profile_requested.emit(self._name))
        header.addWidget(self._edit_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        pills = QHBoxLayout()
        pills.setContentsMargins(0, 0, 0, 0)
        pills.setSpacing(4)
        for display in account_display_names(self._profile.get("accounts") or [], self._accounts):
            pills.addWidget(NeutralPill(display), 0, Qt.AlignmentFlag.AlignVCenter)
        pills.addStretch()
        layout.addLayout(pills)

        self._quiet_lbl = QLabel(profile_summary(self._profile))
        layout.addWidget(self._quiet_lbl)

    def apply_theme_styles(self) -> None:
        super().apply_theme_styles()
        # svg_icon bakes THEME.icon_tint in at render time, so the icon has to be rebuilt
        # here rather than set once in _build — an icon set once goes stale on a theme
        # switch with no stylesheet anywhere to grep for.
        if self._edit_btn is not None:
            self._edit_btn.setIcon(svg_icon("gear", size=14))

    def enterEvent(self, event) -> None:
        self._edit_btn.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._edit_btn.setVisible(False)
        super().leaveEvent(event)
