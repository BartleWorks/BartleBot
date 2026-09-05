"""GameView: tiled display of embedded Wizard101 game windows."""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes
import logging

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot.game_sizing import FitProblem, grid_for, required_game_area
from bartlebot.gui import screen_fit
from bartlebot.gui.game_embed.client_tile import ClientTile, DetachedTile, PendingTile
from bartlebot.gui.game_embed.launch_cards import AccountCard, ProfileCard
from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.settings_manager import SettingsManager
from bartlebot.tile_slots import TileKey, TileSlot, plan_tile_changes

logger = logging.getLogger(__name__)

_user32 = ctypes.windll.user32
_VK_LBUTTON = 0x01

_OVERLAY_BTN_SIZE = 34
_OVERLAY_BTN_MARGIN = 4

_LOGO_SIZE = 160  # diameter of the circular logo widget


def _size(pair: tuple[int, int]) -> str:
    return f"{pair[0]}×{pair[1]}"


def account_banner_text(problem: FitProblem | None) -> str:
    """What the Accounts column says when a single client will not fit, or "".

    Every card in that column asks the identical question -- one client, the *default*
    resolution, this screen -- so this sentence is the whole refusal for all of them and
    the cards carry none of it.

    It names the default resolution specifically, because that is the setting to change:
    an account card never launches at a pinned one. And it offers two ways out rather than
    the three a profile gets. "Launch fewer clients" is advice that cannot be taken here
    -- one account is one client -- and a remedy the user cannot act on reads as the app
    not knowing what it is refusing.
    """
    if problem is None:
        return ""
    return (
        f"Your default game resolution ({_size(problem.resolution)}) needs "
        f"{_size(problem.need)}, but only {_size(problem.available)} is available. "
        f"Either lower the default game resolution or increase the size of the game view panel."
    )


def profile_banner_text(problems: list[FitProblem], total: int) -> str:
    """What the Launch Profiles column says about *problems* out of *total*, or "".

    Sized against the *panel*, not a resolution, which is the one number every profile in
    the column shares: profiles may each pin their own resolution, so naming one would
    point at a setting that is not the problem for the cards beside it.

    Whether some or all of them fail changes what the user needs to be told. All of them
    is a fact about the panel and comes with the ways out. Some of them is a fact about
    those profiles -- the column still works, and each greyed card carries its own numbers
    saying what it wanted -- so the banner says which state the list is in and leaves the
    per-profile detail on the per-profile cards.
    """
    if not problems:
        return ""
    where = f"in the game view panel at its current size ({_size(problems[0].available)})"
    if len(problems) < total:
        return f"Some launch profiles do not fit {where} and have been disabled."
    if total == 1:
        return f"This launch profile does not fit {where}."
    return (
        f"No launch profiles fit {where}. Lower the resolution they launch at, "
        f"increase the size of the game view panel, or launch fewer clients."
    )


class _FitBanner(ThemedMixin, QWidget):
    """One column's "these will not fit" notice, and the way out of it.

    A fit refusal has a half that belongs to the entry -- how much room this profile
    wanted -- and a half that belongs to the column, because the ways out are properties
    of the layout rather than of any one profile. Saying the second half once here is what
    stops the account column printing the identical wrapped paragraph under every account
    name, which is what it did when each card carried the whole sentence.

    The two columns word it differently and deliberately: they are refusing different
    things, they point at different settings, and one of the three standard remedies does
    not apply to a single account at all. ``account_banner_text`` and
    ``profile_banner_text`` hold the wording, out here where they can be read and tested
    without a QApplication.

    The link goes to the same place as the screen's own "Game settings" -- the tab holding
    the resolution -- because advice the user then has to go hunting to follow is half an
    answer.
    """

    settings_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(2)
        # Height-for-width, for the same reason as a card's quiet line: a wrapping label
        # whose layout does not know it wraps reports a sizeHint height measured at some
        # other width, and the column inherits it.
        self._text = ThemedTextLabel("", color="warning")
        self._text.setWordWrap(True)
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self._text.setSizePolicy(policy)
        self._text.setMinimumWidth(1)
        layout.addWidget(self._text)

        self._link = QPushButton("Change resolution")
        self._link.setFlat(True)
        self._link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._link.clicked.connect(self.settings_requested)
        layout.addWidget(self._link, alignment=Qt.AlignmentFlag.AlignLeft)

        self.setVisible(False)
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self._link.setStyleSheet(
            f"color: {THEME.accent}; text-decoration: underline; border: none; padding: 0px; text-align: left;"
        )

    @property
    def text(self) -> str:
        """What the banner currently says -- empty while it is hidden."""
        return self._text.text()

    def set_text(self, text: str) -> None:
        """Show *text*, or hide the banner entirely when it is empty.

        Hidden rather than blank: an empty wrapping label still holds a line of the
        column's height, and that is scroll range over nothing once the cards below it
        fill the viewport.
        """
        self._text.setText(text)
        self.setVisible(bool(text))


class _GamePlaceholder(ThemedMixin, QWidget):
    """Placeholder shown in GameView when no clients are embedded.

    Three-column layout:
        [Accounts list] | [Logo + settings links] | [Launch Profiles list]
    """

    launch_account_requested = pyqtSignal(str, str)  # (username, display_name)
    launch_profile_requested = pyqtSignal(str)  # (profile_name)
    edit_profile_requested = pyqtSignal(str)  # (profile_name)
    manage_accounts_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Set before _build_ui: the constructor's own refresh() reads it.
        self._roster_source = None
        # The live cards, held so a resize can re-judge them without rebuilding. Rebuilding
        # on a splitter drag would destroy and recreate every widget several times a
        # second; re-judging is a few integer comparisons that usually change nothing.
        self._launch_cards: list = []
        self._build_ui()
        self.refresh_theme_styles()
        self.refresh()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        outer.setSpacing(12)
        outer.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        # Title
        title = ThemedTextLabel("Quick Launch")
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 2)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(title)

        # Three-column body
        body = QHBoxLayout()
        body.setSpacing(16)
        outer.addLayout(body, stretch=1)

        # ── Left: Accounts ────────────────────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(6)
        accounts_hdr = ThemedTextLabel("Accounts")
        accounts_hdr_font = accounts_hdr.font()
        accounts_hdr_font.setBold(True)
        accounts_hdr.setFont(accounts_hdr_font)
        accounts_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(accounts_hdr)
        sep_l = QFrame()
        sep_l.setFrameShape(QFrame.Shape.HLine)
        sep_l.setFrameShadow(QFrame.Shadow.Sunken)
        left.addWidget(sep_l)
        self._accounts_banner = _FitBanner()
        self._accounts_banner.settings_requested.connect(self.manage_accounts_requested)
        left.addWidget(self._accounts_banner)
        self._accounts_area = self._make_scroll_area()
        left.addWidget(self._accounts_area, stretch=1)
        body.addLayout(left, stretch=1)

        # ── Center: Logo + links ──────────────────────────────────────
        center = QVBoxLayout()
        center.setSpacing(8)
        center.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        center.addStretch(1)

        self._logo = logo = QFrame()
        logo.setFixedSize(_LOGO_SIZE, _LOGO_SIZE)
        logo_wrapper = QHBoxLayout()
        logo_wrapper.setContentsMargins(0, 0, 0, 0)
        logo_wrapper.addStretch()
        logo_wrapper.addWidget(logo)
        logo_wrapper.addStretch()
        center.addLayout(logo_wrapper)

        center.addStretch(1)

        self._manage_btn = QPushButton("Game settings")
        self._manage_btn.setFlat(True)
        self._manage_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._manage_btn.clicked.connect(self.manage_accounts_requested)
        center.addWidget(self._manage_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        body.addLayout(center, stretch=1)

        # ── Right: Launch Profiles ────────────────────────────────────
        right = QVBoxLayout()
        right.setSpacing(6)
        profiles_hdr = ThemedTextLabel("Launch Profiles")
        profiles_hdr_font = profiles_hdr.font()
        profiles_hdr_font.setBold(True)
        profiles_hdr.setFont(profiles_hdr_font)
        profiles_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right.addWidget(profiles_hdr)
        sep_r = QFrame()
        sep_r.setFrameShape(QFrame.Shape.HLine)
        sep_r.setFrameShadow(QFrame.Shadow.Sunken)
        right.addWidget(sep_r)
        self._profiles_banner = _FitBanner()
        self._profiles_banner.settings_requested.connect(self.manage_accounts_requested)
        right.addWidget(self._profiles_banner)
        self._profiles_area = self._make_scroll_area()
        right.addWidget(self._profiles_area, stretch=1)
        body.addLayout(right, stretch=1)

    @staticmethod
    def _make_scroll_area() -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addStretch()
        area.setWidget(container)
        return area

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply_theme_styles(self) -> None:
        """The two pieces that are not labels — the rest own their own colour."""
        self._logo.setStyleSheet(f"QFrame {{ border: 2px solid {THEME.border}; border-radius: {_LOGO_SIZE // 2}px; }}")
        self._manage_btn.setStyleSheet(
            f"color: {THEME.text_muted}; text-decoration: underline; border: none; padding: 2px;"
        )

    def set_roster_source(self, source) -> None:
        """Supply the callable that answers ``characters_for(username)``.

        Injected rather than imported because the rosters are cached on ClientManager,
        which this widget does not own and must not construct. Without it the account
        cards simply show no characters, which is also the correct display for an
        install that has never launched anything.
        """
        self._roster_source = source
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the account and profile cards from current settings.

        Each card is asked whether what it would launch fits this screen, and greyed out
        with the reason if it does not — see bartlebot/gui/screen_fit.py for why an
        unfittable launch is refused rather than warned about. The screen is only known
        once the widget is on one, so a card built before the window is shown is measured
        against the primary screen and re-measured on the way back in, which showEvent
        already triggers.
        """
        sm = SettingsManager.instance()
        accounts = sm.accounts

        account_cards: list[QWidget] = []
        for account in accounts:
            card = AccountCard(account, self._characters_for(account.get("username", "")), accounts)
            card.launch_account_requested.connect(self.launch_account_requested)
            account_cards.append(card)
        self._rebuild_list(self._accounts_area, account_cards, empty_hint="(No accounts)")

        profile_cards: list[QWidget] = []
        for profile in sm.launch_profiles:
            card = ProfileCard(profile, accounts)
            card.launch_profile_requested.connect(self.launch_profile_requested)
            card.edit_profile_requested.connect(self.edit_profile_requested)
            profile_cards.append(card)
        self._rebuild_list(self._profiles_area, profile_cards, empty_hint="(No profiles)")

        # Assigned only after _rebuild_list has deleteLater'd the previous cards, so
        # nothing here can be a reference to a widget that is on its way out.
        self._launch_cards = [*account_cards, *profile_cards]
        self.refresh_fit()

    def refresh_fit(self) -> None:
        """Re-judge every card against the game area as it is now.

        Cheap by construction: ``set_unavailable`` returns immediately when the reason has
        not changed, so a drag costs a few comparisons per card per event and restyles
        only on the frame where a verdict actually flips.

        Each column's banner is then given what its refusals have in common. The account
        column's answer is one answer by construction — every card asks the same question
        — so the whole sentence, numbers included, belongs there and not on each card. A
        profile column's cards keep their own numbers; see _FitBanner.
        """
        live = self.game_area_size
        resolution = SettingsManager.instance().game_resolution
        # This screen is only up when nothing is embedded, so an account card always
        # launches the first client. The +1 case — adding a client to a running fleet —
        # is the Launch menu's, which is reachable while tiles are on screen.
        account_problem = screen_fit.fit_details(resolution, 1, self, live)
        accounts = 0
        profile_problems: list[FitProblem] = []
        profiles = 0
        for card in self._launch_cards:
            if isinstance(card, ProfileCard):
                profiles += 1
                problem = screen_fit.profile_fit_details(card.profile, self, live)
                card.set_unavailable(problem)
                if problem is not None:
                    profile_problems.append(problem)
            else:
                accounts += 1
                card.set_unavailable(account_problem)
        self._accounts_banner.set_text(account_banner_text(account_problem) if accounts else "")
        self._profiles_banner.set_text(profile_banner_text(profile_problems, profiles))

    @property
    def game_area_size(self) -> tuple[int, int]:
        """The space the tiles would have: this widget fills the GameView exactly.

        ``GameView._recompute_geometry`` sets the placeholder to the view's full rect, so
        measuring self and measuring the game area are the same measurement.
        """
        return self.width(), self.height()

    def resizeEvent(self, event) -> None:
        """Re-judge on a splitter drag, which is what makes the answer track the layout.

        Sidebar widths and the canvas height are startup seeds written only by the
        settings dialog — nothing persists a drag — so without this the cards go on
        describing a layout the user has already moved away from.
        """
        super().resizeEvent(event)
        self.refresh_fit()

    def showEvent(self, event) -> None:
        """Rebuild on the way back in.

        The launch screen is hidden for the whole life of an embedded client, which is
        exactly when the things it describes change: characters are learned as they are
        played, and the roster is what the account cards are for. Closing the last game
        window used to reveal cards built before the session that taught them anything.
        """
        super().showEvent(event)
        self.refresh()

    def _characters_for(self, username: str) -> list[dict]:
        """The known characters for *username*, or none if no source has been supplied."""
        if self._roster_source is None or not username:
            return []
        try:
            return self._roster_source(username)
        except Exception:
            logger.debug("Could not read the character roster for the launch screen", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _rebuild_list(area: QScrollArea, cards: list[QWidget], empty_hint: str) -> None:
        container = area.widget()
        layout = container.layout()

        # Remove all existing widgets
        while layout.count() > 0:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not cards:
            # ThemedTextLabel because _rebuild_list is static and the hints it makes
            # are replaced on every refresh — there is nothing to hold a reference on.
            hint = ThemedTextLabel(empty_hint, css="font-size: 11px;")
            hint.setWordWrap(True)
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(hint)
        else:
            # Expanding across, so a card fills the column; height-for-width down, so the
            # column asks each card how tall it is *at the width it got*. Fixed vertically
            # is what this used to be, and it pinned both the card's minimum and its
            # maximum to a sizeHint that a wrapped refusal line computes at some unrelated
            # width -- with setWidgetResizable the scroll area then could not be shorter
            # than that sum, which is the scroll bar over empty space in #191.
            for card in cards:
                policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                policy.setHeightForWidth(True)
                card.setSizePolicy(policy)
                layout.addWidget(card)

        layout.addStretch()


class GameView(ThemedMixin, QWidget):
    """Container that shows N ClientTile widgets in a grid layout.

    Tiles are positioned manually (not via a QLayout) so that we can recompute
    geometry precisely on resize without Qt interfering with the Win32-embedded
    child windows.
    """

    embed_state_changed = pyqtSignal(bool)  # True = ≥1 client embedded
    launch_account_requested = pyqtSignal(str, str)
    launch_profile_requested = pyqtSignal(str)
    edit_profile_requested = pyqtSignal(str)
    manage_accounts_requested = pyqtSignal()
    game_embed_settings_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tiles: dict[int, ClientTile] = {}
        self._detached_tiles: dict[int, DetachedTile] = {}
        # Slots holding a launch that has not produced a window yet. A third tile kind
        # rather than a flag on the other two: it has no hwnd, so almost nothing the
        # other dicts are asked for has an answer here. See PendingTile.
        self._pending_tiles: dict[int, PendingTile] = {}
        # Account per pending slot. Kept beside the widgets because it is the pending
        # tile's identity in plan_tile_changes -- a PendingTile itself only knows the
        # label it is drawn with, which is a display name and not a key.
        self._pending_accounts: dict[int, str] = {}
        # Last known wizard name per client index. Kept so a tile created later — a new
        # client, or a detach/reattach — gets its name immediately instead of waiting for
        # the name to change again, which for a stable character never happens.
        self._wizard_names: list = []
        self._detached_hwnds: dict[int, int] = {}  # slot → hwnd
        # Player index per slot, as of the last sync. The tiles are keyed by slot and the
        # wizard names arrive keyed by player, so this is the join between them; a slot
        # holding a window that is not in game maps to None. See bartlebot.tile_slots.
        self._tile_players: dict[int, int | None] = {}
        # Last announced embed state, so sync_tiles can stay silent on a tick that changed
        # nothing rather than re-emitting several times a second.
        self._embed_state = False

        # Placeholder shown when no clients are embedded or detached
        self._placeholder = _GamePlaceholder(self)
        self._placeholder.launch_account_requested.connect(self.launch_account_requested)
        self._placeholder.launch_profile_requested.connect(self.launch_profile_requested)
        self._placeholder.edit_profile_requested.connect(self.edit_profile_requested)
        self._placeholder.manage_accounts_requested.connect(self.manage_accounts_requested)
        self._placeholder.show()

        # Overlay button: detach-all (when embedded) / re-attach-all (when detached)
        self._overlay_btn = QToolButton(self)
        self._overlay_btn.setAutoRaise(True)
        self._overlay_btn.setFixedSize(_OVERLAY_BTN_SIZE, _OVERLAY_BTN_SIZE)
        self._overlay_btn.clicked.connect(self._on_overlay_clicked)
        self._overlay_btn.setVisible(False)
        self._overlay_btn.raise_()

        # Poll every 50 ms: grant focus to the game HWND on click, update tile borders
        self._focus_timer = QTimer(self)
        self._focus_timer.setInterval(50)
        self._focus_timer.timeout.connect(self._poll_focus)
        self._focus_timer.start()

        QApplication.instance().installEventFilter(self)

        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        """Re-tint the overlay button.

        svg_icon bakes THEME.icon_tint in at render time, and which of the two icons
        belongs there depends on whether the tiles are embedded or detached —
        _update_overlay already answers that, so the restyle defers to it rather than
        duplicating the rule.
        """
        self._update_overlay()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync_tiles(self, tiles: list[TileSlot]) -> None:
        """Make the grid show exactly *tiles*, one entry per window that belongs on screen.

        The single entry point for everything that changes the grid: a launch, a crash, a
        recovery, a player reorder. ClientManager calls it on every detection tick and it
        is a no-op when nothing moved, which is what lets a window appearing at character
        select — an event that changes no client list, because it is not a client — still
        reach the screen.

        Slot and player are different numbers here; see bartlebot.tile_slots for why. A
        tile sits at its ``slot`` and is captioned from its ``player``, so a window with no
        player index is drawn under its account name rather than a number it does not have.
        """
        current: dict[int, TileKey] = {slot: ("hwnd", tile.hwnd) for slot, tile in self._tiles.items()}
        current.update({slot: ("hwnd", hwnd) for slot, hwnd in self._detached_hwnds.items()})
        current.update({slot: ("pending", name) for slot, name in self._pending_accounts.items()})
        changes = plan_tile_changes(current, tiles)

        if not changes.is_empty:
            for slot in changes.removed:
                self._destroy_tile(slot)
            self._apply_moves(changes.moved)
            for tile in changes.added:
                self._create_tile(tile)

        # Captions and names are refreshed even when nothing moved: a client hooking gives
        # a tile that is already on screen its player number for the first time.
        self._tile_players = {t.slot: t.player for t in tiles}
        for t in tiles:
            widget = self._widget_at(t.slot)
            if widget is not None:
                widget.set_label(t.label)
                widget.set_wizard_name(self._wizard_name_for(t.player))
            # Pushed on every sync alongside the captions, and for the same reason: a
            # launch changing stage moves no tile, so the reconciler has nothing to report
            # and the only thing that changed is what the tile says.
            pending = self._pending_tiles.get(t.slot)
            if pending is not None:
                pending.set_status(t.status, t.status_since)

        if changes.is_empty:
            return
        self._placeholder.setVisible(not self._occupied_slots())
        self._recompute_geometry()
        self._update_minimum_size()
        self._set_embed_state(bool(self._tiles))
        self._update_overlay()

    def _create_tile(self, spec: TileSlot) -> None:
        """Build the tile for a slot that does not have one yet, embedded or pending."""
        if spec.is_pending:
            self._create_pending_tile(spec)
            return
        tile = ClientTile(spec.hwnd, spec.label, parent=self)
        tile.set_wizard_name(self._wizard_name_for(spec.player))
        self._tiles[spec.slot] = tile
        tile.detach_requested.connect(lambda slot=spec.slot: self._detach_client(slot))
        tile.show()
        self._remove_detached_tile(spec.slot)
        self._remove_pending_tile(spec.slot)

    def _create_pending_tile(self, spec: TileSlot) -> None:
        """Put a busy tile in a slot whose account is launching but has no window yet."""
        self._remove_pending_tile(spec.slot)
        tile = PendingTile(spec.label, parent=self)
        tile.set_status(spec.status, spec.status_since)
        self._pending_tiles[spec.slot] = tile
        self._pending_accounts[spec.slot] = spec.username or ""
        tile.show()

    def _remove_pending_tile(self, slot: int) -> None:
        """Tear down the pending tile in *slot*, if there is one."""
        self._pending_accounts.pop(slot, None)
        tile = self._pending_tiles.pop(slot, None)
        if tile is None:
            return
        tile.hide()
        tile.setParent(None)  # type: ignore[arg-type]
        tile.deleteLater()

    def _destroy_tile(self, slot: int) -> None:
        """Tear down whatever occupies *slot*, embedded, detached or pending."""
        self._detached_hwnds.pop(slot, None)
        self._remove_detached_tile(slot)
        self._remove_pending_tile(slot)
        tile = self._tiles.pop(slot, None)
        if tile is None:
            return
        tile.unembed()
        tile.hide()
        tile.setParent(None)  # type: ignore[arg-type]
        tile.deleteLater()

    def _apply_moves(self, moves: tuple[tuple[int, int], ...]) -> None:
        """Re-key tiles from one slot to another without touching the game windows.

        Every vacated slot is cleared before any is filled, and always against the
        *original* mapping. Doing it move by move loses a tile on a swap: clearing slot 1
        for the tile arriving from slot 0 throws away the tile that had already been
        written into slot 1 by the move out of it. Re-keying rather than rebuilding is the
        whole point — rebuilding a tile un-parents the game window and parents it again,
        which flashes.
        """
        if not moves:
            return
        vacated = {old for old, _new in moves}
        tiles = {slot: tile for slot, tile in self._tiles.items() if slot not in vacated}
        detached_tiles = {slot: tile for slot, tile in self._detached_tiles.items() if slot not in vacated}
        detached_hwnds = {slot: hwnd for slot, hwnd in self._detached_hwnds.items() if slot not in vacated}
        pending_tiles = {slot: tile for slot, tile in self._pending_tiles.items() if slot not in vacated}
        pending_accounts = {slot: name for slot, name in self._pending_accounts.items() if slot not in vacated}
        for old, new in moves:
            if old in self._tiles:
                tiles[new] = self._tiles[old]
            if old in self._detached_tiles:
                detached_tiles[new] = self._detached_tiles[old]
            if old in self._detached_hwnds:
                detached_hwnds[new] = self._detached_hwnds[old]
            if old in self._pending_tiles:
                pending_tiles[new] = self._pending_tiles[old]
            if old in self._pending_accounts:
                pending_accounts[new] = self._pending_accounts[old]
        self._tiles = tiles
        self._detached_tiles = detached_tiles
        self._detached_hwnds = detached_hwnds
        self._pending_tiles = pending_tiles
        self._pending_accounts = pending_accounts
        for slot, tile in self._tiles.items():
            self._reconnect_detach(tile, slot)
        for slot, dtile in self._detached_tiles.items():
            self._reconnect_reattach(dtile, slot)

    def _reconnect_detach(self, tile: ClientTile, slot: int) -> None:
        """Point a tile's detach button at the slot it now occupies."""
        with contextlib.suppress(TypeError):
            tile.detach_requested.disconnect()
        tile.detach_requested.connect(lambda s=slot: self._detach_client(s))

    def _reconnect_reattach(self, dtile: DetachedTile, slot: int) -> None:
        """Point a placeholder's attach button at the slot it now occupies."""
        with contextlib.suppress(TypeError):
            dtile.reattach_requested.disconnect()
        dtile.reattach_requested.connect(lambda s=slot: self.reattach_client(s))

    def _set_embed_state(self, embedded: bool) -> None:
        """Announce the embedded/not-embedded state, but only when it actually changed.

        sync_tiles runs on every detection tick; a signal per tick would have the main
        window relaying a layout change several times a second.
        """
        if embedded != self._embed_state:
            self._embed_state = embedded
            self.embed_state_changed.emit(embedded)

    def unembed_client(self, client_idx: int) -> None:
        """Unembed and remove the tile for *client_idx* (client closed, not detached)."""
        if client_idx not in self._tiles:
            return
        self._destroy_tile(client_idx)
        self._placeholder.setVisible(not self._occupied_slots())
        self._recompute_geometry()
        self._update_minimum_size()
        self._set_embed_state(bool(self._tiles))
        self._update_overlay()

    def unembed_all(self) -> None:
        """Unembed every client."""
        for idx in list(self._tiles.keys()):
            self.unembed_client(idx)

    def detach_all(self) -> None:
        """Save current HWNDs and detach all clients (windows float free, slots show placeholders)."""
        for idx, tile in list(self._tiles.items()):
            self._detached_hwnds[idx] = tile.hwnd
            self._create_detached_tile(idx, tile.label)
        # Unembed the game windows without touching _detached_tiles
        for idx in list(self._tiles.keys()):
            tile = self._tiles.pop(idx)
            tile.unembed()
            tile.hide()
            tile.setParent(None)  # type: ignore[arg-type]
            tile.deleteLater()
        self._placeholder.setVisible(False)
        self._recompute_geometry()
        self._update_minimum_size()
        self._set_embed_state(False)
        self._update_overlay()

    def reattach_all(self) -> None:
        """Re-embed previously detached windows."""
        for idx in sorted(self._detached_hwnds):
            self.reattach_client(idx)

    def reattach_client(self, client_idx: int) -> None:
        """Re-embed a single previously detached window."""
        hwnd = self._detached_hwnds.pop(client_idx, None)
        if hwnd is None:
            return
        if client_idx in self._tiles:
            return
        label = self._detached_tiles[client_idx].label if client_idx in self._detached_tiles else ""
        self._create_tile(TileSlot(slot=client_idx, hwnd=hwnd, label=label, player=self._player_for(client_idx)))
        self._placeholder.setVisible(False)
        self._recompute_geometry()
        self._update_minimum_size()
        self._set_embed_state(True)
        self._update_overlay()

    @property
    def has_detached(self) -> bool:
        return bool(self._detached_hwnds)

    @property
    def pending_slots(self) -> dict[int, str]:
        """Slot → account, for every launch drawn here that has no window yet.

        Public because "what is this view still waiting for" is a question about the
        launch rather than about the widgets, and the only alternative is reaching into
        the tile dicts from outside.
        """
        return dict(self._pending_accounts)

    def pending_status(self, slot: int) -> str | None:
        """What the pending tile in *slot* currently says, or None if it is not pending.

        Public for the same reason as ``pending_slots``: the caption is a fact about the
        launch, and reading it is otherwise a reach into the tile widgets.
        """
        tile = self._pending_tiles.get(slot)
        return None if tile is None else tile.status_text

    def tile_geometry(self, slot: int):
        """The rectangle the tile in *slot* occupies, or None if that slot is empty.

        The grid is laid out by hand rather than by a QLayout — see _recompute_geometry —
        so where a tile ended up is not readable from anything Qt exposes about this
        widget, and a tile left out of the layout silently overlaps the one in slot 0.
        """
        widget = self._widget_at(slot)
        return None if widget is None else widget.geometry()

    @property
    def game_area_size(self) -> tuple[int, int]:
        """The logical pixels the tile grid actually has, right now.

        Named rather than left as ``width()``/``height()`` because this is the live
        budget a launch is judged against (see gui/screen_fit.budget_for), and "the size
        of this widget" does not say that. It is exact where the configured layout is an
        estimate: the sidebars, the bot canvas, the menu and status bars and the game
        panel's own header are already subtracted, because they are simply not in it.
        """
        return self.width(), self.height()

    @property
    def largest_game_size(self) -> tuple[int, int] | None:
        """The widest and tallest game window on screen, or None if there is none.

        Componentwise, not one tile's size: mixed-resolution fleets are supported —
        ``_group_by_resolution`` launches a batch per resolution — so the grid has to be
        measured against the largest of each axis rather than against whichever tile a
        dict happened to yield first, which understated it whenever they differed.

        Detached windows are not counted: they are not parented here, so their size
        cannot be read without going back to Win32, and a re-attach re-measures anyway.
        """
        sizes = [tile.game_size for tile in self._tiles.values()]
        if not sizes:
            return None
        return max(size[0] for size in sizes), max(size[1] for size in sizes)

    @property
    def client_count(self) -> int:
        """How many windows this view is holding a place for, embedded or detached.

        A detached window still owns its slot and comes back into the same grid, so it
        counts: the question this answers is how many tiles the layout has to fit, not
        how many are currently parented here. So does a launching one, for the same
        reason and one step earlier -- its window is coming, and the grid it lands in
        should be the grid that was already drawn for it rather than a resize on arrival.
        """
        return len(self._occupied_slots())

    def set_wizard_names(self, names: list) -> None:
        """Show each client's wizard name on its tile pill.

        *names* is positionally aligned with the live client list — the **player** index,
        not the slot the tile sits in. The two are only the same number while every
        account is in game (see bartlebot.tile_slots), so each tile is looked up by the
        player index it was last synced with rather than by its own key.

        A None entry clears the name rather than leaving a stale one on screen — that is
        what a character switch looks like before the new name has been read.
        """
        self._wizard_names = list(names)
        for slot, player in self._tile_players.items():
            tile = self._widget_at(slot)
            if tile is not None:
                tile.set_wizard_name(self._wizard_name_for(player))

    def refresh_launch_lists(self) -> None:
        """Rebuild the Quick Launch account and profile lists from settings.

        Public because the placeholder is GameView's own widget: a caller that has just
        added an account or recorded a launch needs the lists rebuilt without reaching
        through to it.
        """
        self._placeholder.refresh()

    def refresh_launch_fit(self) -> None:
        """Re-judge which Quick Launch entries can launch, without rebuilding them.

        The cheap half of ``refresh_launch_lists``: what a card *says* has not changed,
        only whether the space is there for it. Called on every geometry change, so it
        must stay cheap — see the placeholder's ``refresh_fit``.
        """
        self._placeholder.refresh_fit()

    def set_roster_source(self, source) -> None:
        """Supply what the account cards ask for their characters — see the placeholder."""
        self._placeholder.set_roster_source(source)

    def repaint_tile_labels(self) -> None:
        """Repaint every tile's caption without changing what it is showing.

        Toggling Streamer Mode changes how ``pill_text`` renders a name it already has,
        and nothing else would prompt a repaint: a tile only updates when its name or
        label *changes*, and neither did. Called from the settings-applied signal.
        """
        for tile in (*self._tiles.values(), *self._detached_tiles.values(), *self._pending_tiles.values()):
            tile.update()

    def _wizard_name_for(self, player: int | None) -> str | None:
        """The last known wizard name for a **player** index, or None if there is not one.

        None in, None out: a window that is not in game has no player index and therefore
        no wizard name — nothing has read one, because nothing can.
        """
        if player is not None and 0 <= player < len(self._wizard_names):
            return self._wizard_names[player]
        return None

    def _player_for(self, slot: int) -> int | None:
        """The player index last synced for *slot*, or None if it has none."""
        return self._tile_players.get(slot)

    def _occupied_slots(self) -> list[int]:
        """Every slot holding a tile of any kind, in grid order.

        One question with one answer, because the three dicts have to be consulted
        together everywhere it is asked -- the grid layout, the minimum size, the tile
        count and whether the launch screen shows. Each of those read two of the three
        before pending tiles existed, and a pending tile missing from any one of them is a
        tile drawn on top of another.
        """
        return sorted(set(self._tiles) | set(self._detached_tiles) | set(self._pending_tiles))

    def _widget_at(self, slot: int):
        """Whatever tile occupies *slot*, of any kind, or None."""
        return self._tiles.get(slot) or self._detached_tiles.get(slot) or self._pending_tiles.get(slot)

    def toggle(self) -> None:
        """Show or hide the GameView (used by the toggle_embed keybinding)."""
        visible = not self.isVisible()
        self.setVisible(visible)
        parent = self.parent()
        while parent is not None:
            if hasattr(parent, "show_game_view"):
                parent.show_game_view(visible)
                return
            parent = parent.parent()

    # ------------------------------------------------------------------
    # Qt event overrides
    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._recompute_geometry()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._recompute_geometry()

    # ------------------------------------------------------------------
    # Internal geometry management
    # ------------------------------------------------------------------

    def _recompute_geometry(self) -> None:
        """Lay out tiles in the grid and update placeholder/overlay position."""
        self._placeholder.setGeometry(0, 0, self.width(), self.height())
        # Re-judged here rather than left to the placeholder's own resizeEvent: Qt defers
        # a resize event to a hidden widget until it is shown, and the launch screen is
        # hidden for the whole life of an embedded client. Its own handler stays as well,
        # for a resize that reaches it directly.
        self._placeholder.refresh_fit()

        self._overlay_btn.move(
            self.width() - _OVERLAY_BTN_SIZE - _OVERLAY_BTN_MARGIN,
            _OVERLAY_BTN_MARGIN,
        )

        all_slots = self._occupied_slots()
        n = len(all_slots)
        if n == 0:
            return

        rows, cols = grid_for(n)
        tile_w = self.width() // cols
        tile_h = self.height() // rows

        for pos, idx in enumerate(all_slots):
            row = pos // cols
            col = pos % cols
            geo = (col * tile_w, row * tile_h, tile_w, tile_h)
            widget = self._widget_at(idx)
            if widget is not None:
                widget.setGeometry(*geo)

    def _update_minimum_size(self) -> None:
        """Set GameView minimum size to fit the current client grid plus padding.

        This is what stops the window and the splitters being dragged to where the game
        windows get clipped: a tile shows its client at 1:1 and cannot scale, so anything
        narrower than the grid needs simply cuts the game off. Resolutions are capped to
        the screen before launch (see bartlebot/game_sizing.py), so the minimum this
        derives is always satisfiable rather than a size the window cannot reach.

        The grid is measured against the **largest** client on screen, not an arbitrary
        one. Mixed-resolution fleets are supported — ``_group_by_resolution`` launches a
        batch per resolution — so reading whichever tile the dict happened to yield first
        understated the minimum whenever they differed, and the biggest client was the one
        that got clipped.
        """
        n = len(self._occupied_slots())
        if n == 0:
            self.setMinimumSize(0, 0)
            return
        # Everything detached leaves no window to measure; the setting is what those
        # windows were launched at, and what a re-attach brings back.
        largest = self.largest_game_size or SettingsManager.instance().game_resolution
        self.setMinimumSize(*required_game_area(largest, n, self.devicePixelRatio() or 1.0))

    def _detach_client(self, idx: int) -> None:
        """Save hwnd, create a DetachedTile, and unembed the ClientTile."""
        if idx not in self._tiles:
            return
        hwnd = self._tiles[idx].hwnd
        label = self._tiles[idx].label
        self._detached_hwnds[idx] = hwnd
        self._create_detached_tile(idx, label)
        # Unembed the ClientTile without clearing the DetachedTile
        tile = self._tiles.pop(idx)
        tile.unembed()
        tile.hide()
        tile.setParent(None)  # type: ignore[arg-type]
        tile.deleteLater()
        self._recompute_geometry()
        self._update_minimum_size()
        self._set_embed_state(bool(self._tiles))
        self._update_overlay()

    def _create_detached_tile(self, idx: int, label: str) -> None:
        dtile = DetachedTile(label, parent=self)
        dtile.set_wizard_name(self._wizard_name_for(self._player_for(idx)))
        dtile.reattach_requested.connect(lambda i=idx: self.reattach_client(i))
        dtile.show()
        self._detached_tiles[idx] = dtile

    def _remove_detached_tile(self, idx: int) -> None:
        dtile = self._detached_tiles.pop(idx, None)
        if dtile is not None:
            dtile.hide()
            dtile.setParent(None)  # type: ignore[arg-type]
            dtile.deleteLater()

    def _update_overlay(self) -> None:
        if self._tiles:
            self._overlay_btn.setIcon(svg_icon("detach", 22))
            self._overlay_btn.setToolTip("Detach all windows")
            self._overlay_btn.setVisible(True)
            self._overlay_btn.raise_()
        elif self._detached_tiles:
            self._overlay_btn.setIcon(svg_icon("attach", 22))
            self._overlay_btn.setToolTip("Re-attach all windows")
            self._overlay_btn.setVisible(True)
            self._overlay_btn.raise_()
        else:
            self._overlay_btn.setVisible(False)

    def _on_overlay_clicked(self) -> None:
        if self._detached_hwnds:
            self.reattach_all()
        else:
            self.detach_all()

    # ------------------------------------------------------------------
    # Focus polling
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            widget = obj if isinstance(obj, QWidget) else None
            while widget is not None:
                if isinstance(widget, ClientTile):
                    return False  # click is on or inside a game tile; focus polling handles it
                widget = widget.parent() if isinstance(widget, QWidget) else None
            # Click landed on a non-game Qt widget; revoke game focus if any tile has it.
            for tile in self._tiles.values():
                if tile.is_game_focused():
                    _user32.SetFocus(int(self.window().winId()))
                    break
        return False

    def _poll_focus(self) -> None:
        if _user32.GetAsyncKeyState(_VK_LBUTTON) & 0x0001:
            # Skip focus grant when a non-tile Qt widget (dialog, popup, overlay button)
            # is on top of the game area at the cursor position.
            top_widget = QApplication.widgetAt(QCursor.pos())
            if top_widget is None or isinstance(top_widget, ClientTile):
                pt = ctypes.wintypes.POINT()
                _user32.GetCursorPos(ctypes.byref(pt))
                for tile in self._tiles.values():
                    rect = ctypes.wintypes.RECT()
                    _user32.GetWindowRect(tile.hwnd, ctypes.byref(rect))
                    if rect.left <= pt.x < rect.right and rect.top <= pt.y < rect.bottom:
                        tile.focus_game()
                        break

        for tile in self._tiles.values():
            tile.set_focused(tile.is_game_focused())
