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

from bartlebot.gui.game_embed.client_tile import FOCUS_BORDER, LABEL_RESERVE, ClientTile, DetachedTile
from bartlebot.gui.game_embed.launch_cards import AccountCard, ProfileCard
from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.settings_manager import SettingsManager
from bartlebot.tile_slots import TileSlot, plan_tile_changes

logger = logging.getLogger(__name__)

_user32 = ctypes.windll.user32
_VK_LBUTTON = 0x01

# Grid layouts: number of clients → (rows, cols)
_GRID: dict[int, tuple[int, int]] = {
    1: (1, 1),
    2: (1, 2),
    3: (2, 2),
    4: (2, 2),
}

_OVERLAY_BTN_SIZE = 34
_OVERLAY_BTN_MARGIN = 4

_LOGO_SIZE = 160  # diameter of the circular logo widget


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
        """Rebuild the account and profile cards from current settings."""
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
            for card in cards:
                card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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
        current = {slot: tile.hwnd for slot, tile in self._tiles.items()}
        current.update(self._detached_hwnds)
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
            widget = self._tiles.get(t.slot) or self._detached_tiles.get(t.slot)
            if widget is not None:
                widget.set_label(t.label)
                widget.set_wizard_name(self._wizard_name_for(t.player))

        if changes.is_empty:
            return
        self._placeholder.setVisible(not self._tiles and not self._detached_tiles)
        self._recompute_geometry()
        self._update_minimum_size()
        self._set_embed_state(bool(self._tiles))
        self._update_overlay()

    def _create_tile(self, spec: TileSlot) -> None:
        """Build and embed a tile for a window that does not have one yet."""
        tile = ClientTile(spec.hwnd, spec.label, parent=self)
        tile.set_wizard_name(self._wizard_name_for(spec.player))
        self._tiles[spec.slot] = tile
        tile.detach_requested.connect(lambda slot=spec.slot: self._detach_client(slot))
        tile.show()
        self._remove_detached_tile(spec.slot)

    def _destroy_tile(self, slot: int) -> None:
        """Tear down whatever occupies *slot*, embedded or detached."""
        self._detached_hwnds.pop(slot, None)
        self._remove_detached_tile(slot)
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
        for old, new in moves:
            if old in self._tiles:
                tiles[new] = self._tiles[old]
            if old in self._detached_tiles:
                detached_tiles[new] = self._detached_tiles[old]
            if old in self._detached_hwnds:
                detached_hwnds[new] = self._detached_hwnds[old]
        self._tiles = tiles
        self._detached_tiles = detached_tiles
        self._detached_hwnds = detached_hwnds
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
        self._placeholder.setVisible(not self._tiles and not self._detached_tiles)
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
            tile = self._tiles.get(slot) or self._detached_tiles.get(slot)
            if tile is not None:
                tile.set_wizard_name(self._wizard_name_for(player))

    def refresh_launch_lists(self) -> None:
        """Rebuild the Quick Launch account and profile lists from settings.

        Public because the placeholder is GameView's own widget: a caller that has just
        added an account or recorded a launch needs the lists rebuilt without reaching
        through to it.
        """
        self._placeholder.refresh()

    def set_roster_source(self, source) -> None:
        """Supply what the account cards ask for their characters — see the placeholder."""
        self._placeholder.set_roster_source(source)

    def repaint_tile_labels(self) -> None:
        """Repaint every tile's caption without changing what it is showing.

        Toggling Streamer Mode changes how ``pill_text`` renders a name it already has,
        and nothing else would prompt a repaint: a tile only updates when its name or
        label *changes*, and neither did. Called from the settings-applied signal.
        """
        for tile in (*self._tiles.values(), *self._detached_tiles.values()):
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

        self._overlay_btn.move(
            self.width() - _OVERLAY_BTN_SIZE - _OVERLAY_BTN_MARGIN,
            _OVERLAY_BTN_MARGIN,
        )

        all_slots = sorted(set(self._tiles) | set(self._detached_tiles))
        n = len(all_slots)
        if n == 0:
            return

        rows, cols = _GRID.get(n, (2, 2))
        tile_w = self.width() // cols
        tile_h = self.height() // rows

        for pos, idx in enumerate(all_slots):
            row = pos // cols
            col = pos % cols
            geo = (col * tile_w, row * tile_h, tile_w, tile_h)
            if idx in self._tiles:
                self._tiles[idx].setGeometry(*geo)
            elif idx in self._detached_tiles:
                self._detached_tiles[idx].setGeometry(*geo)

    def _update_minimum_size(self) -> None:
        """Set GameView minimum size to fit the current client grid plus padding."""
        n = len(self._tiles) + len(self._detached_tiles)
        if n == 0:
            self.setMinimumSize(0, 0)
            return
        rows, cols = _GRID.get(n, (2, 2))
        if self._tiles:
            tile = next(iter(self._tiles.values()))
            dpr = self.devicePixelRatio() or 1.0
            game_w, game_h = tile.game_size
        else:
            dpr = self.devicePixelRatio() or 1.0
            game_w, game_h = SettingsManager.instance().game_resolution
        self.setMinimumSize(
            int(cols * game_w / dpr) + (cols * 2 * FOCUS_BORDER),
            int(rows * game_h / dpr) + (rows * LABEL_RESERVE) + (rows * 2 * FOCUS_BORDER),
        )

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
