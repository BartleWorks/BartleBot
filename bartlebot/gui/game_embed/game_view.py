"""GameView: tiled display of embedded Wizard101 game windows."""

from __future__ import annotations

import contextlib
import ctypes
import logging

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot.game_sizing import grid_for, required_game_area
from bartlebot.gui.account_accent import account_color_keys
from bartlebot.gui.game_embed.card_columns import (
    EMPTY_ACCOUNTS_HINT,
    EMPTY_PROFILES_HINT,
    CardColumn,
    refresh_column_fit,
)
from bartlebot.gui.game_embed.client_tile import ClientTile, DetachedTile, PendingTile
from bartlebot.gui.game_embed.launch_cards import AccountCard, ProfileCard
from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.link_button import LinkButton
from bartlebot.gui.widgets.logo_disc import LogoDisc
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.settings_manager import SettingsManager
from bartlebot.tile_slots import TileKey, TileSlot, plan_tile_changes

logger = logging.getLogger(__name__)

_user32 = ctypes.windll.user32

_OVERLAY_BTN_SIZE = 34
_OVERLAY_BTN_MARGIN = 4

_LOGO_SIZE = 160  # diameter of the logo disc


class _GamePlaceholder(QWidget):
    """Placeholder shown in GameView when no clients are embedded.

    Three-column layout:
        [Accounts column] | [Logo + settings link] | [Launch Profiles column]

    The two outer columns are ``CardColumn``s -- the same widget the Game settings tab is
    built from, so the surface that launches an account and the surface that edits one
    draw it identically. Everything a column does, the drag that reorders it
    included, lives there; this class supplies the cards and decides what a click on one
    means.
    """

    launch_account_requested = pyqtSignal(str, str)
    launch_profile_requested = pyqtSignal(str)  # (profile_name)
    # The card actions and the column reorders. Forwarded rather than acted
    # on: what an edit, a delete or a reorder has to refresh afterwards -- the Launch
    # menu, the window minimum, these cards -- is the main window's business and not this
    # widget's.
    edit_account_requested = pyqtSignal(str)  # (username)
    delete_account_requested = pyqtSignal(str)  # (username)
    edit_profile_requested = pyqtSignal(str)  # (profile_name)
    duplicate_profile_requested = pyqtSignal(str)  # (profile_name)
    delete_profile_requested = pyqtSignal(str)  # (profile_name)
    add_account_requested = pyqtSignal()
    add_profile_requested = pyqtSignal()
    reorder_accounts_requested = pyqtSignal(list)  # (old indices, in their new order)
    reorder_profiles_requested = pyqtSignal(list)  # (old indices, in their new order)
    manage_accounts_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Set before _build_ui: the constructor's own refresh() reads it.
        self._roster_source = None
        self._build_ui()
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
        title_font.setPixelSize(THEME.font_heading_px)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(title)

        # Three-column body
        body = QHBoxLayout()
        body.setSpacing(16)
        outer.addLayout(body, stretch=1)

        # -- Left: Accounts --------------------------------------------
        self._accounts_col = CardColumn("Accounts", EMPTY_ACCOUNTS_HINT, "Add account")
        self._accounts_col.add_requested.connect(self.add_account_requested)
        self._accounts_col.reordered.connect(self.reorder_accounts_requested)
        self._accounts_col.settings_requested.connect(self.manage_accounts_requested)
        body.addWidget(self._accounts_col, stretch=1)

        # -- Center: Logo + links --------------------------------------
        center = QVBoxLayout()
        center.setSpacing(8)
        center.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        center.addStretch(1)

        logo = LogoDisc(_LOGO_SIZE)
        logo_wrapper = QHBoxLayout()
        logo_wrapper.setContentsMargins(0, 0, 0, 0)
        logo_wrapper.addStretch()
        logo_wrapper.addWidget(logo)
        logo_wrapper.addStretch()
        center.addLayout(logo_wrapper)

        center.addStretch(1)

        self._manage_btn = LinkButton("Game settings")
        self._manage_btn.clicked.connect(self.manage_accounts_requested)
        center.addWidget(self._manage_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        body.addLayout(center, stretch=1)

        # -- Right: Launch Profiles ------------------------------------
        self._profiles_col = CardColumn("Launch Profiles", EMPTY_PROFILES_HINT, "Add launch profile")
        self._profiles_col.add_requested.connect(self.add_profile_requested)
        self._profiles_col.reordered.connect(self.reorder_profiles_requested)
        self._profiles_col.settings_requested.connect(self.manage_accounts_requested)
        body.addWidget(self._profiles_col, stretch=1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def accounts_column(self) -> CardColumn:
        """The Accounts column, for the fit pass and for anyone measuring what it drew."""
        return self._accounts_col

    @property
    def profiles_column(self) -> CardColumn:
        """The Launch Profiles column."""
        return self._profiles_col

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

        Each card is asked whether what it would launch fits this screen, and grayed out
        with the reason if it does not -- see bartlebot/gui/screen_fit.py for why an
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
            card.edit_account_requested.connect(self.edit_account_requested)
            card.delete_account_requested.connect(self.delete_account_requested)
            account_cards.append(card)
        self._accounts_col.set_cards(account_cards)

        profile_cards: list[QWidget] = []
        for profile in sm.launch_profiles:
            card = ProfileCard(profile, accounts)
            card.launch_profile_requested.connect(self.launch_profile_requested)
            card.edit_profile_requested.connect(self.edit_profile_requested)
            card.duplicate_profile_requested.connect(self.duplicate_profile_requested)
            card.delete_profile_requested.connect(self.delete_profile_requested)
            profile_cards.append(card)
        self._profiles_col.set_cards(profile_cards)

        self.refresh_fit()

    def refresh_fit(self) -> None:
        """Re-judge every card against the game area as it is now -- see refresh_column_fit."""
        refresh_column_fit(self._accounts_col, self._profiles_col, self, self.game_area_size)

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
        settings dialog -- nothing persists a drag -- so without this the cards go on
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


class GameView(ThemedMixin, QWidget):
    """Container that shows N ClientTile widgets in a grid layout.

    Tiles are positioned manually (not via a QLayout) so that we can recompute
    geometry precisely on resize without Qt interfering with the Win32-embedded
    child windows.
    """

    embed_state_changed = pyqtSignal(bool)  # True = ≥1 client embedded
    launch_account_requested = pyqtSignal(str, str)
    launch_profile_requested = pyqtSignal(str)
    edit_account_requested = pyqtSignal(str)
    delete_account_requested = pyqtSignal(str)
    edit_profile_requested = pyqtSignal(str)
    duplicate_profile_requested = pyqtSignal(str)
    delete_profile_requested = pyqtSignal(str)
    add_account_requested = pyqtSignal()
    add_profile_requested = pyqtSignal()
    reorder_accounts_requested = pyqtSignal(list)
    reorder_profiles_requested = pyqtSignal(list)
    manage_accounts_requested = pyqtSignal()
    game_embed_settings_requested = pyqtSignal()
    # A failed launch's tile was clicked away. Carries the account, not the slot: a pending
    # tile's slot can still move under it, and the launch is keyed by account throughout.
    dismiss_launch_requested = pyqtSignal(str)
    # The game window holding the keyboard right now (an hwnd), or None when BartleBot's
    # own widgets or another application have it. What arms the authoring hotkeys: they
    # are system-wide keys, and armed anywhere else they would eat F1 in every other app.
    game_focus_changed = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tiles: dict[int, ClientTile] = {}
        # See game_focus_changed and last_focused_hwnd.
        self._focused_hwnd: int | None = None
        self._last_focused_hwnd: int | None = None
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
        self._placeholder.edit_account_requested.connect(self.edit_account_requested)
        self._placeholder.delete_account_requested.connect(self.delete_account_requested)
        self._placeholder.edit_profile_requested.connect(self.edit_profile_requested)
        self._placeholder.duplicate_profile_requested.connect(self.duplicate_profile_requested)
        self._placeholder.delete_profile_requested.connect(self.delete_profile_requested)
        self._placeholder.add_account_requested.connect(self.add_account_requested)
        self._placeholder.add_profile_requested.connect(self.add_profile_requested)
        self._placeholder.reorder_accounts_requested.connect(self.reorder_accounts_requested)
        self._placeholder.reorder_profiles_requested.connect(self.reorder_profiles_requested)
        self._placeholder.manage_accounts_requested.connect(self.manage_accounts_requested)
        self._placeholder.show()

        # Overlay button: detach-all (when embedded) / re-attach-all (when detached)
        self._overlay_btn = QToolButton(self)
        self._overlay_btn.setAutoRaise(True)
        self._overlay_btn.setFixedSize(_OVERLAY_BTN_SIZE, _OVERLAY_BTN_SIZE)
        self._overlay_btn.clicked.connect(self._on_overlay_clicked)
        # Drawn over the tiles, which are native: an alien widget would be painted under
        # them. Native by request, since siblings are no longer promoted (native_widgets).
        self._overlay_btn.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self._overlay_btn.setVisible(False)
        self._overlay_btn.raise_()

        # Poll every 50 ms to keep each tile's focus border in step with its game window
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
        # Account colors ride the same per-tick push, because a window's account is learned
        # from its title after the tile exists and nothing announces it. Resolved over the
        # whole account list, for the reason account_stripe_keys gives: a color is only
        # distinct with respect to the others. A tile with no account gets no color.
        color_keys = account_color_keys(SettingsManager.instance().accounts)
        for t in tiles:
            widget = self._widget_at(t.slot)
            if widget is not None:
                widget.set_label(t.label)
                widget.set_wizard_name(self._wizard_name_for(t.player))
                widget.set_color_key(color_keys.get(t.username) if t.username else None)
            # Pushed on every sync alongside the captions, and for the same reason: a
            # launch changing stage moves no tile, so the reconciler has nothing to report
            # and the only thing that changed is what the tile says.
            pending = self._pending_tiles.get(t.slot)
            if pending is not None:
                pending.set_status(t.status, t.status_since, t.failed)

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
        tile.set_status(spec.status, spec.status_since, spec.failed)
        self._pending_tiles[spec.slot] = tile
        self._pending_accounts[spec.slot] = spec.username or ""
        # Connected to the account rather than to the slot, unlike the detach and reattach
        # buttons: a pending tile's slot can still move under it, and the thing being
        # dismissed is the launch, which is keyed by account all the way down.
        username = spec.username or ""
        tile.dismiss_requested.connect(lambda u=username: self.dismiss_launch_requested.emit(u))
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
    def overlay_button(self) -> QToolButton:
        """The detach-all / re-attach-all button drawn over the tiles."""
        return self._overlay_btn

    @property
    def pending_slots(self) -> dict[int, str]:
        """Slot → account, for every launch drawn here that has no window yet.

        Public because "what is this view still waiting for" is a question about the
        launch rather than about the widgets, and the only alternative is reaching into
        the tile dicts from outside.
        """
        return dict(self._pending_accounts)

    def pending_failed(self, slot: int) -> bool:
        """Whether the pending tile in *slot* is showing a launch that gave up."""
        tile = self._pending_tiles.get(slot)
        return tile is not None and tile.failed

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
        # Carried across rather than waiting for the next sync, so the pill does not
        # flash gray for a tick on the way out.
        self._detached_tiles[idx].set_color_key(self._tiles[idx].color_key)
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
    # Focus
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            widget = obj if isinstance(obj, QWidget) else None
            while widget is not None:
                if isinstance(widget, ClientTile):
                    return False  # click is on or inside a game tile; the tile grants focus itself
                widget = widget.parent() if isinstance(widget, QWidget) else None
            # Click landed on a non-game Qt widget; revoke game focus if any tile has it.
            for tile in self._tiles.values():
                if tile.is_game_focused():
                    _user32.SetFocus(int(self.window().winId()))
                    break
        return False

    def _poll_focus(self) -> None:
        # Borders only. Granting focus is the tile's job, on the click itself (see
        # ClientTile.nativeEvent): a poll grants it too late to survive BartleBot being
        # brought to the front by that click, and matched clicks through windows stacked
        # over a tile (#93).
        focused = None
        for tile in self._tiles.values():
            has_focus = tile.is_game_focused()
            tile.set_focused(has_focus)
            if has_focus:
                focused = tile.hwnd
        # A game thread keeps its focus when the user switches to another application, so
        # an embedded tile only counts while BartleBot itself is in front. A detached game
        # window is a top-level window of its own, and is in front or not by itself.
        foreground = _user32.GetForegroundWindow()
        if focused is not None and foreground != int(self.window().winId()):
            focused = None
        if foreground and foreground in self._detached_hwnds.values():
            focused = foreground
        if focused is not None:
            self._last_focused_hwnd = focused
        if focused != self._focused_hwnd:
            self._focused_hwnd = focused
            self.game_focus_changed.emit(focused)

    @property
    def focused_hwnd(self) -> int | None:
        """The game window holding the keyboard right now, or None. See game_focus_changed."""
        return self._focused_hwnd

    @property
    def last_focused_hwnd(self) -> int | None:
        """The game window that most recently held the keyboard, or None if none ever has.

        The source for XYZ sync and TP to player. *Last*, not current: pressing their button
        is a click on BartleBot, which takes the focus away from the game first.
        """
        return self._last_focused_hwnd
