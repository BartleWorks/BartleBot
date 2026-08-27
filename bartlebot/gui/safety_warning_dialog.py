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

from bartlebot.nodes.registry import NODE_SPECS
from bartlebot.nodes.utility import quicksell

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig


@dataclass
class SafetyWarning:
    """A single warning entry shown in the safety dialog before a bot run."""

    message: str
    """Main warning text displayed next to the warning icon."""

    details: list[tuple[str, str]] = field(default_factory=list)
    """Optional (label, value) rows shown in a compact form below the message."""


#: The quicksell sell_* params, in the order the dialog lists them.
_QUICKSELL_FLAG_LABELS: dict[str, str] = {
    "sell_pets": "Sell Pets",
    "sell_socket_wrenches": "Sell Socket Wrenches",
    "sell_skeleton_keys": "Sell Skeleton Keys",
}


def _quicksell_details(states) -> list[tuple[str, str]]:
    """What each quicksell state will actually sell, and which level decided it.

    The sell_* params are three-state, so the stored value is not the answer: an
    unset one follows the app setting. This dialog is the last thing between the
    user and an irreversible sale, so it shows the resolved value — and says when
    it came from the app setting, because that is the one the bot file does not
    show.
    """
    details: list[tuple[str, str]] = []
    for state in states:
        prefix = f"{state.label or state.id}: " if len(states) > 1 else ""
        inherited = set(quicksell.inherited_flags(state.params))
        resolved = quicksell.resolve_app_defaults(state.params)
        details += [
            (
                f"{prefix}{label}",
                ("Yes" if resolved[key] else "No") + (" (app setting)" if key in inherited else ""),
            )
            for key, label in _QUICKSELL_FLAG_LABELS.items()
        ]
    return details


def _teleport_to_friend_details(states) -> list[tuple[str, str]]:
    details: list[tuple[str, str]] = []
    for state in states:
        prefix = f"{state.label or state.id}: " if len(states) > 1 else ""
        p = state.params
        details += [
            (f"{prefix}Friend Name", p.get("friend_name", "")),
            (f"{prefix}Destination Zone", p.get("to_zone", "")),
        ]
    details.append(("Required Game Setting", "Options → Gameplay Options 4 → Dblclick Go To Friend: No"))
    return details


_DETAIL_BUILDERS = {
    "quicksell": _quicksell_details,
    "teleport_to_friend": _teleport_to_friend_details,
}


def collect_safety_warnings(config: BotConfig) -> list[SafetyWarning]:
    """Return a list of SafetyWarning for every risky node type present in *config*.

    Returns at most one SafetyWarning per risky node type, even when multiple
    states with the same node_type exist. Coverage is driven by the safety_warning
    field on each NodeSpec — adding a warning for a new node type requires only
    setting that field in the node's NODE_SPEC.
    """
    states_by_type: dict[str, list] = {}
    for state in config.states:
        states_by_type.setdefault(state.node_type, []).append(state)

    warnings: list[SafetyWarning] = []
    for node_type, spec in NODE_SPECS.items():
        if not spec.safety_warning:
            continue
        states = states_by_type.get(node_type, [])
        if not states:
            continue
        detail_builder = _DETAIL_BUILDERS.get(node_type)
        details = detail_builder(states) if detail_builder else []
        warnings.append(SafetyWarning(message=spec.safety_warning, details=details))

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
