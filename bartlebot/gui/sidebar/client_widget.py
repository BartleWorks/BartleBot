"""Per-client utility panel shown in the sidebar."""

import asyncio
import json

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

from bartlebot.gui.sidebar.sidebar_section import field_label


class ClientWidget(QWidget):
    """Displays live zone/XYZ info and freecam controls for a single client."""

    move_up = pyqtSignal(int)  # emits this widget's current index
    move_down = pyqtSignal(int)

    def __init__(self, client, index: int, total: int, parent=None):
        super().__init__(parent)
        self._client = client
        self._in_freecam = False
        self._last_zone = "—"
        self._last_xyz = "—"
        self._last_mob = "—"
        self._mob_tick = 0
        self._build_ui(index, total)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self, index: int, total: int) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        # Title row with move up/down buttons
        title = QLabel(f"Client {index + 1}")
        font = QFont()
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        btn_up = QPushButton("▲")
        btn_up.setFixedSize(22, 22)
        btn_up.setToolTip("Move client up")
        btn_up.setEnabled(index > 0)
        btn_up.clicked.connect(lambda: self.move_up.emit(index))

        btn_down = QPushButton("▼")
        btn_down.setFixedSize(22, 22)
        btn_down.setToolTip("Move client down")
        btn_down.setEnabled(index < total - 1)
        btn_down.clicked.connect(lambda: self.move_down.emit(index))

        title_row = QHBoxLayout()
        title_row.addWidget(btn_up)
        title_row.addWidget(title, 1)
        title_row.addWidget(btn_down)
        layout.addLayout(title_row)

        # Zone row
        layout.addWidget(field_label("Current Zone"))
        self._zone_label = QLabel("—")
        self._zone_label.setWordWrap(True)
        self._zone_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        copy_zone_btn = QPushButton("Copy Zone")
        copy_zone_btn.setFixedWidth(80)
        copy_zone_btn.clicked.connect(self._copy_zone)
        zone_row = QHBoxLayout()
        zone_row.addWidget(self._zone_label, 1)
        zone_row.addWidget(copy_zone_btn)
        layout.addLayout(zone_row)

        # XYZ row — three independent labels so each has its own layout height
        layout.addWidget(field_label("Current XYZ"))

        def _xyz_val_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            return lbl

        self._x_label = _xyz_val_label("X=—")
        self._y_label = _xyz_val_label("Y=—")
        self._z_label = _xyz_val_label("Z=—")

        xyz_labels = QVBoxLayout()
        xyz_labels.setSpacing(0)
        xyz_labels.addWidget(self._x_label)
        xyz_labels.addWidget(self._y_label)
        xyz_labels.addWidget(self._z_label)

        copy_xyz_btn = QPushButton("Copy XYZ")
        copy_xyz_btn.setFixedWidth(80)
        copy_xyz_btn.clicked.connect(self._copy_xyz)
        xyz_row = QHBoxLayout()
        xyz_row.addLayout(xyz_labels, 1)
        xyz_row.addWidget(copy_xyz_btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(xyz_row)

        # Freecam controls
        layout.addWidget(field_label("Freecam Controls"))
        self._freecam_btn = QPushButton("Enter Freecam")
        self._freecam_btn.clicked.connect(lambda: asyncio.ensure_future(self._toggle_freecam()))
        layout.addWidget(self._freecam_btn)

        self._tp_btn = QPushButton("TP to Freecam")
        self._tp_btn.setEnabled(False)
        self._tp_btn.clicked.connect(lambda: asyncio.ensure_future(self._tp_to_freecam()))
        layout.addWidget(self._tp_btn)

        # Closest enemy row
        layout.addWidget(field_label("Closest Enemy"))
        self._mob_label = QLabel("—")
        self._mob_label.setWordWrap(True)
        self._mob_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        copy_mob_btn = QPushButton("Copy Mob")
        copy_mob_btn.setFixedWidth(80)
        copy_mob_btn.clicked.connect(self._copy_mob)
        mob_row = QHBoxLayout()
        mob_row.addWidget(self._mob_label, 1)
        mob_row.addWidget(copy_mob_btn)
        layout.addLayout(mob_row)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

    # ------------------------------------------------------------------
    # Copy helpers
    # ------------------------------------------------------------------

    def _copy_zone(self) -> None:
        pyperclip.copy(self._last_zone)

    def _copy_xyz(self) -> None:
        pyperclip.copy(self._last_xyz)

    def _copy_mob(self) -> None:
        pyperclip.copy(self._last_mob)

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
    # Periodic refresh (called by sidebar timer)
    # ------------------------------------------------------------------

    async def refresh(self) -> None:
        """Read zone and XYZ from the client and update labels."""
        try:
            await self._refresh()
        except RuntimeError:
            return  # Widget was deleted while awaiting

    async def _refresh(self) -> None:
        # Zone
        try:
            zone = await self._client.zone_name() or "—"
        except Exception:
            zone = "—"
        self._last_zone = zone
        self._zone_label.setText(zone)

        # XYZ — show freecam position when in freecam mode
        try:
            if self._in_freecam:
                free = await self._client.game_client.free_camera_controller()
                pos = await free.position()
            else:
                pos = await self._client.body.position()
            xyz_dict = {"x": round(pos.x, 2), "y": round(pos.y, 2), "z": round(pos.z, 2)}
            self._last_xyz = json.dumps(xyz_dict)
            self._x_label.setText(f"X={pos.x:.1f}")
            self._y_label.setText(f"Y={pos.y:.1f}")
            self._z_label.setText(f"Z={pos.z:.1f}")
        except Exception:
            self._last_xyz = "—"
            self._x_label.setText("X=—")
            self._y_label.setText("Y=—")
            self._z_label.setText("Z=—")

        # Closest mob — updated every 20 ticks (~2 s)
        self._mob_tick += 1
        if self._mob_tick >= 20:
            self._mob_tick = 0
            try:
                mob = await self._client.find_closest_mob()
                name = await mob.object_name() if mob else "—"
                self._last_mob = name or "—"
            except Exception:
                self._last_mob = "—"
            self._mob_label.setText(self._last_mob)
