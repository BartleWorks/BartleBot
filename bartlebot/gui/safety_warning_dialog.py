"""Dialog shown before a bot run when the bot contains nodes with potentially risky behaviour."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QStyle,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig


@dataclass
class SafetyWarning:
    """A single warning entry shown in the safety dialog before a bot run."""

    message: str
    """Main warning text displayed next to the warning icon."""

    details: list[tuple[str, str]] = field(default_factory=list)
    """Optional (label, value) rows shown in a compact form below the message."""


def collect_safety_warnings(config: BotConfig) -> list[SafetyWarning]:
    """Return a list of SafetyWarning for every risky node type present in *config*.

    Returns at most one SafetyWarning per risky node type, even when multiple
    states with the same node_type exist. Adding coverage for a new node type
    requires only adding an additional block here.
    """
    warnings: list[SafetyWarning] = []

    quicksell_states = [s for s in config.states if s.node_type == "quicksell"]
    if quicksell_states:
        details: list[tuple[str, str]] = []
        for state in quicksell_states:
            prefix = f"{state.label or state.id}: " if len(quicksell_states) > 1 else ""
            p = state.params
            details += [
                (f"{prefix}Sell Crowns Items", "Yes" if p.get("sell_crowns", True) else "No"),
                (f"{prefix}Sell Pets", "Yes" if p.get("sell_pets", True) else "No"),
                (f"{prefix}Sell Socket Wrenches", "Yes" if p.get("sell_socket_wrenches", False) else "No"),
                (f"{prefix}Sell Skeleton Keys", "Yes" if p.get("sell_skeleton_keys", False) else "No"),
            ]
        warnings.append(
            SafetyWarning(
                message=(
                    "This bot quicksells items when your backpack is full. "
                    "Ensure that you have locked any valuable items or have added them "
                    "to the keep list in the quicksell node parameters."
                ),
                details=details,
            )
        )

    if any(s.node_type == "collect_reagent" for s in config.states):
        warnings.append(
            SafetyWarning(
                message=(
                    "This bot teleports to collect reagents. "
                    "Ensure that you are not in a busy realm when you run this bot."
                ),
            )
        )

    return warnings


class SafetyWarningDialog(QDialog):
    """Shows one or more SafetyWarning entries and lets the user proceed or cancel."""

    def __init__(self, warnings: list[SafetyWarning], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Safety Check")
        self.setMinimumWidth(440)
        self._build(warnings)

    def _build(self, warnings: list[SafetyWarning]) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QLabel("Review before running")
        font = header.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        header.setFont(font)
        layout.addWidget(header)

        warn_icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        icon_size = 20

        for i, warning in enumerate(warnings):
            if i > 0:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setFrameShadow(QFrame.Shadow.Sunken)
                layout.addWidget(sep)

            # Icon + message row
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            row_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

            icon_label = QLabel()
            icon_label.setPixmap(warn_icon.pixmap(icon_size, icon_size))
            icon_label.setFixedSize(icon_size, icon_size)
            row_layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

            msg_label = QLabel(warning.message)
            msg_label.setWordWrap(True)
            row_layout.addWidget(msg_label, 1)

            layout.addWidget(row)

            if warning.details:
                form_container = QWidget()
                form_container.setContentsMargins(icon_size + 8, 0, 0, 0)
                form = QFormLayout(form_container)
                form.setContentsMargins(icon_size + 8, 0, 0, 0)
                form.setSpacing(4)
                form.setHorizontalSpacing(16)
                for label_text, value_text in warning.details:
                    value_label = QLabel(value_text)
                    form.addRow(label_text + ":", value_label)
                layout.addWidget(form_container)

        buttons = QDialogButtonBox()
        run_btn = buttons.addButton("Run Bot", QDialogButtonBox.ButtonRole.AcceptRole)
        run_btn.setDefault(True)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
