"""GameView: tiled display of embedded Wizard101 game windows."""

from __future__ import annotations

import ctypes
import ctypes.wintypes

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

from bartlebot.credentials import CredentialStore
from bartlebot.gui.game_embed.client_tile import FOCUS_BORDER, LABEL_RESERVE, ClientTile, DetachedTile
from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.settings_manager import SettingsManager

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
    manage_accounts_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
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

    def refresh(self) -> None:
        """Rebuild account and profile lists from current settings."""
        sm = SettingsManager.instance()

        self._rebuild_list(
            self._accounts_area,
            items=[
                (
                    CredentialStore.display_name(a["username"], a.get("alias") or None),
                    lambda *_, u=a["username"], alias=a.get("alias") or None: self.launch_account_requested.emit(
                        u, CredentialStore.display_name(u, alias)
                    ),
                )
                for a in sm.accounts
            ],
            empty_hint="(No accounts)",
        )

        self._rebuild_list(
            self._profiles_area,
            items=[
                (p["name"], lambda *_, n=p["name"]: self.launch_profile_requested.emit(n)) for p in sm.launch_profiles
            ],
            empty_hint="(No profiles)",
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _rebuild_list(
        area: QScrollArea,
        items: list[tuple[str, object]],
        empty_hint: str,
    ) -> None:
        container = area.widget()
        layout = container.layout()

        # Remove all existing widgets
        while layout.count() > 0:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not items:
            # ThemedTextLabel because _rebuild_list is static and the hints it makes
            # are replaced on every refresh — there is nothing to hold a reference on.
            hint = ThemedTextLabel(empty_hint, css="font-size: 11px;")
            hint.setWordWrap(True)
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(hint)
        else:
            for label, callback in items:
                btn = QPushButton(f"{label}")
                btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                btn.clicked.connect(callback)
                layout.addWidget(btn)

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
        self._detached_hwnds: dict[int, int] = {}  # client_idx → hwnd

        # Placeholder shown when no clients are embedded or detached
        self._placeholder = _GamePlaceholder(self)
        self._placeholder.launch_account_requested.connect(self.launch_account_requested)
        self._placeholder.launch_profile_requested.connect(self.launch_profile_requested)
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

    def embed_client(self, hwnd: int, client_idx: int) -> None:
        """Embed the game window identified by *hwnd* as client *client_idx*."""
        if client_idx in self._tiles:
            return  # already embedded
        label = f"Player {client_idx + 1}"
        tile = ClientTile(hwnd, label, parent=self)
        tile.set_wizard_name(self._wizard_name_for(client_idx))
        self._tiles[client_idx] = tile
        tile.detach_requested.connect(lambda idx=client_idx: self._detach_client(idx))
        tile.show()
        # Remove any DetachedTile occupying this slot
        self._remove_detached_tile(client_idx)
        self._placeholder.setVisible(False)
        self._recompute_geometry()
        self._update_minimum_size()
        self.embed_state_changed.emit(True)
        self._update_overlay()

    def unembed_client(self, client_idx: int) -> None:
        """Unembed and remove the tile for *client_idx* (client closed, not detached)."""
        tile = self._tiles.pop(client_idx, None)
        if tile is None:
            return
        tile.unembed()
        tile.hide()
        tile.setParent(None)  # type: ignore[arg-type]
        tile.deleteLater()
        if not self._tiles and not self._detached_tiles:
            self._placeholder.setVisible(True)
        self._recompute_geometry()
        self._update_minimum_size()
        self.embed_state_changed.emit(bool(self._tiles))
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
        self.embed_state_changed.emit(False)
        self._update_overlay()

    def reattach_all(self) -> None:
        """Re-embed previously detached windows."""
        for idx, hwnd in sorted(self._detached_hwnds.items()):
            self.embed_client(hwnd, idx)
        self._detached_hwnds.clear()

    def reattach_client(self, client_idx: int) -> None:
        """Re-embed a single previously detached window."""
        hwnd = self._detached_hwnds.pop(client_idx, None)
        if hwnd is None:
            return
        self.embed_client(hwnd, client_idx)

    @property
    def has_detached(self) -> bool:
        return bool(self._detached_hwnds)

    def set_wizard_names(self, names: list) -> None:
        """Show each client's wizard name on its tile pill.

        `names` is positionally aligned with the live client list, matching the tile keys.
        A None entry clears the name rather than leaving a stale one on screen — that is
        what a character switch looks like before the new name has been read.
        """
        self._wizard_names = list(names)
        for idx, name in enumerate(names):
            tile = self._tiles.get(idx) or self._detached_tiles.get(idx)
            if tile is not None:
                tile.set_wizard_name(name)

    def refresh_launch_lists(self) -> None:
        """Rebuild the Quick Launch account and profile lists from settings.

        Public because the placeholder is GameView's own widget: a caller that has just
        added an account or recorded a launch needs the lists rebuilt without reaching
        through to it.
        """
        self._placeholder.refresh()

    def repaint_tile_labels(self) -> None:
        """Repaint every tile's caption without changing what it is showing.

        Toggling Streamer Mode changes how ``pill_text`` renders a name it already has,
        and nothing else would prompt a repaint: a tile only updates when its name or
        label *changes*, and neither did. Called from the settings-applied signal.
        """
        for tile in (*self._tiles.values(), *self._detached_tiles.values()):
            tile.update()

    def _wizard_name_for(self, client_idx: int) -> str | None:
        """The last known wizard name for a slot, or None if there is not one yet."""
        if 0 <= client_idx < len(self._wizard_names):
            return self._wizard_names[client_idx]
        return None

    def renumber_tiles(self, clients: list) -> None:
        """Re-key _tiles and update labels to match current list positions."""
        hwnd_to_old_idx = {tile.hwnd: old_idx for old_idx, tile in self._tiles.items()}
        new_tiles: dict[int, ClientTile] = {}
        for new_idx, client in enumerate(clients):
            old_idx = hwnd_to_old_idx.get(client.window_handle)
            if old_idx is not None and old_idx in self._tiles:
                tile = self._tiles[old_idx]
                tile.set_label(f"Player {new_idx + 1}")
                # Reconnect detach_requested with the updated index so the detach
                # button targets the correct slot after a client reorder.
                tile.detach_requested.disconnect()
                tile.detach_requested.connect(lambda idx=new_idx: self._detach_client(idx))
                new_tiles[new_idx] = tile
        self._tiles = new_tiles
        # A tile carries its own wizard name and follows its client through a reorder, so
        # the tiles stay correct on their own -- but the by-index list here would not, and
        # it is what a newly created tile reads from. Rebuild it from the tiles.
        self._wizard_names = [
            new_tiles[i].wizard_name if i in new_tiles else self._wizard_name_for(i) for i in range(len(clients))
        ]
        self._recompute_geometry()
        self._update_minimum_size()

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
        self.embed_state_changed.emit(bool(self._tiles))
        self._update_overlay()

    def _create_detached_tile(self, idx: int, label: str) -> None:
        dtile = DetachedTile(label, parent=self)
        dtile.set_wizard_name(self._wizard_name_for(idx))
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
