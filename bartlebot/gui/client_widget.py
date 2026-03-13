"""Per-client utility panel shown in the sidebar."""

import asyncio
import json

import pyperclip
from wizwalker import XYZ
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont


class ClientWidget(QWidget):
    """Displays live zone/XYZ info and freecam controls for a single client."""

    def __init__(self, client, index: int, parent=None):
        super().__init__(parent)
        self._client = client
        self._in_freecam = False
        self._last_zone = "—"
        self._last_xyz = "—"
        self._build_ui(index)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self, index: int) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        # Title
        title = QLabel(f"Client {index + 1}")
        font = QFont()
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Zone row
        self._zone_label = QLabel("—")
        self._zone_label.setWordWrap(True)
        self._zone_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        copy_zone_btn = QPushButton("Copy Zone")
        copy_zone_btn.setFixedWidth(80)
        copy_zone_btn.clicked.connect(self._copy_zone)
        zone_row = QHBoxLayout()
        zone_row.addWidget(self._zone_label, 1)
        zone_row.addWidget(copy_zone_btn)
        layout.addLayout(zone_row)

        # XYZ row
        self._xyz_label = QLabel("—")
        self._xyz_label.setWordWrap(True)
        self._xyz_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        copy_xyz_btn = QPushButton("Copy XYZ")
        copy_xyz_btn.setFixedWidth(80)
        copy_xyz_btn.clicked.connect(self._copy_xyz)
        xyz_row = QHBoxLayout()
        xyz_row.addWidget(self._xyz_label, 1)
        xyz_row.addWidget(copy_xyz_btn)
        layout.addLayout(xyz_row)

        # Freecam toggle button
        self._freecam_btn = QPushButton("Enter Freecam")
        self._freecam_btn.clicked.connect(
            lambda: asyncio.ensure_future(self._toggle_freecam())
        )
        layout.addWidget(self._freecam_btn)

        # TP to Freecam button
        self._tp_btn = QPushButton("TP to Freecam")
        self._tp_btn.setEnabled(False)
        self._tp_btn.clicked.connect(
            lambda: asyncio.ensure_future(self._tp_to_freecam())
        )
        layout.addWidget(self._tp_btn)

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
            self._xyz_label.setText(
                f"x={pos.x:.1f}\ny={pos.y:.1f}\nz={pos.z:.1f}"
            )
        except Exception:
            self._last_xyz = "—"
            self._xyz_label.setText("—")
