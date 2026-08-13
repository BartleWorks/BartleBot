"""Per-client utility panel shown in the sidebar."""

import asyncio
import json
import time

import pyperclip
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from wizwalker import XYZ

from bartlebot import wizard_names
from bartlebot.gui.sidebar.sidebar_section import field_label
from bartlebot.gui.widgets.elided_label import ElidedLabel

_MOB_INTERVAL = 5.0  # seconds between closest-mob refreshes
_PLACEHOLDER = "—"
_COPY_BTN_WIDTH = 48


class ClientWidget(QWidget):
    """Displays live zone/XYZ info and freecam controls for a single client."""

    move_up = pyqtSignal(int)  # emits this widget's current index
    move_down = pyqtSignal(int)
    unhook_requested = pyqtSignal(int)  # emits index (for active clients)
    rehook_requested = pyqtSignal(int)  # emits HWND (for unhooked clients)

    def __init__(self, client, index: int, total: int, parent=None):
        super().__init__(parent)
        self._client = client
        self._index = index
        self._is_unhooked = False
        self._in_freecam = False
        self._last_wizard = _PLACEHOLDER
        self._last_zone = _PLACEHOLDER
        self._last_xyz = _PLACEHOLDER
        self._last_mob = _PLACEHOLDER
        self._last_mob_time: float = 0.0
        self._build_ui(index, total)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    @staticmethod
    def _row() -> QHBoxLayout:
        """A card row: no margins of its own, since the card's outer layout insets it."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        return row

    @staticmethod
    def _copy_button(tooltip: str, on_click) -> QPushButton:
        """Narrow copy button. The inline field prefix beside it says what it copies."""
        btn = QPushButton("Copy")
        btn.setFixedWidth(_COPY_BTN_WIDTH)
        btn.setToolTip(tooltip)
        btn.clicked.connect(on_click)
        return btn

    def _build_ui(self, index: int, total: int) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(4)

        # Title row with move up/down buttons
        title_text = f"Player {index + 1}" if index >= 0 else "Player (unhooked)"
        title = QLabel(title_text)
        font = QFont()
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        btn_up = QPushButton("▲")
        btn_up.setFixedSize(22, 22)
        btn_up.setToolTip("Move player up")
        btn_up.setEnabled(index > 0)
        btn_up.clicked.connect(lambda: self.move_up.emit(index))

        btn_down = QPushButton("▼")
        btn_down.setFixedSize(22, 22)
        btn_down.setToolTip("Move player down")
        btn_down.setEnabled(index < total - 1)
        btn_down.clicked.connect(lambda: self.move_down.emit(index))

        title_row = self._row()
        title_row.addWidget(btn_up)
        title_row.addWidget(title, 1)
        title_row.addWidget(btn_down)
        layout.addLayout(title_row)

        # Wizard row. Read from the name cache rather than detected here: detection can
        # cost keypresses, and a sidebar label must never move the game UI.
        self._wizard_label = ElidedLabel(_PLACEHOLDER)
        wizard_row = self._row()
        wizard_row.addWidget(field_label("Wizard"))
        wizard_row.addWidget(self._wizard_label, 1)
        wizard_row.addWidget(self._copy_button("Copy wizard name", self._copy_wizard))
        layout.addLayout(wizard_row)

        # Zone row — elided from the LEFT, because a zone path's tail
        # (…/WC_Streets/Interior) is what distinguishes it; the prefix is shared.
        self._zone_label = ElidedLabel(_PLACEHOLDER, elide_mode=Qt.TextElideMode.ElideLeft)
        zone_row = self._row()
        zone_row.addWidget(field_label("Zone"))
        zone_row.addWidget(self._zone_label, 1)
        zone_row.addWidget(self._copy_button("Copy zone name", self._copy_zone))
        layout.addLayout(zone_row)

        # XYZ row. An ElidedLabel, not a word-wrapped QLabel: a wrapped QLabel
        # reports a 26 px sizeHint (and heightForWidth of 40) for a single 12 px
        # line, which made this row visibly taller than its neighbours and pushed
        # the value below its prefix. All three data rows use the same widget so
        # they share one line height and centre alike.
        self._xyz_label = ElidedLabel(f"X={_PLACEHOLDER}  Y={_PLACEHOLDER}  Z={_PLACEHOLDER}")
        xyz_row = self._row()
        xyz_row.addWidget(field_label("XYZ"))
        xyz_row.addWidget(self._xyz_label, 1)
        xyz_row.addWidget(self._copy_button("Copy XYZ as JSON", self._copy_xyz))
        layout.addLayout(xyz_row)

        # Closest enemy row
        self._mob_label = ElidedLabel(_PLACEHOLDER)
        mob_row = self._row()
        mob_row.addWidget(field_label("Enemy"))
        mob_row.addWidget(self._mob_label, 1)
        mob_row.addWidget(self._copy_button("Copy closest enemy name", self._copy_mob))
        layout.addLayout(mob_row)

        # Freecam controls — both buttons share one row, below the data rows so
        # the three read as a block rather than being split by a pair of buttons.
        self._freecam_btn = QPushButton("Enter Freecam")
        self._freecam_btn.clicked.connect(lambda: asyncio.ensure_future(self._toggle_freecam()))

        self._tp_btn = QPushButton("TP to Freecam")
        self._tp_btn.setEnabled(False)
        self._tp_btn.clicked.connect(lambda: asyncio.ensure_future(self._tp_to_freecam()))

        freecam_row = self._row()
        freecam_row.addWidget(self._freecam_btn, 1)
        freecam_row.addWidget(self._tp_btn, 1)
        layout.addLayout(freecam_row)

        # Tool buttons. A QPushButton's minimum width is its label width and it
        # clips rather than elides, so these three labels set the floor for how
        # narrow the whole card can go — "Entity Browser"/"UI Tree Browser" alone
        # demanded 376 px inside a 300 px panel. The tooltips carry the full name.
        tool_row = self._row()

        entity_btn = QPushButton("Entities")
        entity_btn.setToolTip("Entity Browser — inspect nearby entities for this player")
        entity_btn.clicked.connect(self._open_entity_browser)
        tool_row.addWidget(entity_btn)

        ui_tree_btn = QPushButton("UI Tree")
        ui_tree_btn.setToolTip("UI Tree Browser — inspect the live WizWalker UI window tree for this player")
        ui_tree_btn.clicked.connect(self._open_ui_tree)
        tool_row.addWidget(ui_tree_btn)

        btn_hook = QPushButton("Unhook")
        btn_hook.setToolTip("Unhook this client (leaves game window open)")
        btn_hook.clicked.connect(self._on_hook_btn_clicked)
        self._hook_btn = btn_hook
        tool_row.addWidget(btn_hook)

        layout.addLayout(tool_row)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def hwnd(self) -> int:
        return self._client.window_handle

    @property
    def is_unhooked(self) -> bool:
        return self._is_unhooked

    @property
    def last_zone(self) -> str:
        """Most recent zone name read by refresh(), or the placeholder if unknown."""
        return self._last_zone

    def set_unhooked(self, unhooked: bool) -> None:
        self._is_unhooked = unhooked
        self._hook_btn.setText("Rehook" if unhooked else "Unhook")
        self._hook_btn.setToolTip(
            "Re-hook this client into BartleBot" if unhooked else "Unhook this client (leaves game window open)"
        )

    def set_bot_running(self, running: bool) -> None:
        if not self._is_unhooked:
            self._hook_btn.setEnabled(not running)

    def _on_hook_btn_clicked(self) -> None:
        if self._is_unhooked:
            self.rehook_requested.emit(self._client.window_handle)
        else:
            self.unhook_requested.emit(self._index)

    # ------------------------------------------------------------------
    # Copy helpers
    # ------------------------------------------------------------------

    def _copy_wizard(self) -> None:
        pyperclip.copy(self._last_wizard)

    def _copy_zone(self) -> None:
        pyperclip.copy(self._last_zone)

    def _copy_xyz(self) -> None:
        pyperclip.copy(self._last_xyz)

    def _copy_mob(self) -> None:
        pyperclip.copy(self._last_mob)

    # ------------------------------------------------------------------
    # Tool button actions
    # ------------------------------------------------------------------

    def _open_entity_browser(self) -> None:
        from bartlebot.gui.entity_browser import EntityBrowserDialog

        dlg = EntityBrowserDialog(self._client, parent=self)
        dlg.show()

    def _open_ui_tree(self) -> None:
        from bartlebot.gui.ui_tree_browser import UITreeBrowserDialog

        dlg = UITreeBrowserDialog(self._client, parent=self)
        dlg.show()

    # ------------------------------------------------------------------
    # Async actions
    # ------------------------------------------------------------------

    async def _toggle_freecam(self) -> None:
        try:
            if self._in_freecam:
                await self._client.camera_elastic()
                self._in_freecam = False
                self._freecam_btn.setText("Enter Freecam")
                self._tp_btn.setEnabled(False)
            else:
                # Pre-position free camera at player location + 200 Z before
                # entering freecam — without this the world freezes on entry.
                pos = await self._client.body.position()
                yaw = await self._client.body.yaw()
                camera = await self._client.game_client.free_camera_controller()
                await camera.write_position(XYZ(pos.x, pos.y, pos.z + 200))
                await camera.write_yaw(yaw)
                await self._client.camera_freecam()
                self._in_freecam = True
                self._freecam_btn.setText("Exit Freecam")
                self._tp_btn.setEnabled(True)
        except Exception:
            pass  # client may have disconnected

    async def _tp_to_freecam(self) -> None:
        """Teleport client to current freecam position, then exit freecam."""
        try:
            free = await self._client.game_client.free_camera_controller()
            pos = await free.position()
            # Exit freecam before teleporting — matches Deimos behaviour.
            await self._client.camera_elastic()
            self._in_freecam = False
            self._freecam_btn.setText("Enter Freecam")
            self._tp_btn.setEnabled(False)
            await self._client.teleport(pos, wait_on_inuse=True, purge_on_after_unuser_fixer=True)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Periodic refresh (called by ClientSection._refresh_loop)
    # ------------------------------------------------------------------

    async def refresh(self) -> None:
        """Read zone and XYZ from the client and update labels."""
        try:
            await self._refresh()
        except RuntimeError:
            return  # Widget was deleted while awaiting

    async def _refresh(self) -> None:
        # Skip all reads during zone transitions — pointers are stale and reads
        # are slow or produce garbage, which is what causes the sidebar to freeze.
        try:
            if await self._client.is_loading():
                return
        except Exception:
            pass

        # Wizard name — a dict lookup in the shared cache, no memory read of its own.
        # ClientManager's loop is what fills it, so the card shows whatever is known and
        # never triggers detection on its own.
        self._last_wizard = wizard_names.cached_name(self._client) or _PLACEHOLDER
        self._wizard_label.setText(self._last_wizard)

        # Zone
        try:
            zone = await self._client.zone_name() or _PLACEHOLDER
        except Exception:
            zone = _PLACEHOLDER
        self._last_zone = zone
        self._zone_label.setText(zone)

        # XYZ — show freecam position when in freecam mode
        try:
            if self._in_freecam:
                free = await self._client.game_client.free_camera_controller()
                pos = await free.position()
            else:
                pos = await self._client.body.position()
            # Copy format is JSON at 2dp; the display format is 1dp and space-separated.
            xyz_dict = {"x": round(pos.x, 2), "y": round(pos.y, 2), "z": round(pos.z, 2)}
            self._last_xyz = json.dumps(xyz_dict)
            self._xyz_label.setText(f"X={pos.x:.1f}  Y={pos.y:.1f}  Z={pos.z:.1f}")
        except Exception:
            self._last_xyz = _PLACEHOLDER
            self._xyz_label.setText(f"X={_PLACEHOLDER}  Y={_PLACEHOLDER}  Z={_PLACEHOLDER}")

        # Closest mob — throttled to once every _MOB_INTERVAL seconds
        now = time.monotonic()
        if now - self._last_mob_time >= _MOB_INTERVAL:
            self._last_mob_time = now
            try:
                mob = await self._client.find_closest_mob()
                name = await mob.object_name() if mob else _PLACEHOLDER
                self._last_mob = name or _PLACEHOLDER
            except Exception:
                self._last_mob = _PLACEHOLDER
            self._mob_label.setText(self._last_mob)
