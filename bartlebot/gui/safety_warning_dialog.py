"""Dialog shown before a bot run when the bot contains nodes with potentially risky behaviour."""

from __future__ import annotations

import logging
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
    QLineEdit,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from bartlebot.nodes.registry import NODE_SPECS
from bartlebot.nodes.teleportation import teleport_to_friend
from bartlebot.nodes.utility import quicksell

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

logger = logging.getLogger(__name__)


@dataclass
class SafetyWarning:
    """A single warning entry shown in the safety dialog before a bot run."""

    message: str
    """Main warning text displayed next to the warning icon."""

    details: list[tuple[str, str]] = field(default_factory=list)
    """Optional (label, value) rows shown in a compact form below the message."""

    editable_setting: tuple[str, str] | None = None
    """Optional (label, general-settings key) rendered as an editable field.

    For values the user owns rather than the bot — a wrong one is worth fixing here rather
    than cancelling the run, going to Settings, and starting over. Saved on accept only.
    """


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


def _teleport_to_friend_details(states, clients) -> tuple[list[tuple[str, str]], tuple[str, str] | None]:
    """What each teleport state will port to, resolved the same way the run will resolve it.

    The bot file does not show the answer in either mode: a hooked-player target is a slot
    whose name is read from the game, and a friend name lives in app settings. So this
    resolves both, and offers the name for editing only when some state actually uses it —
    in hooked-player mode the name is not the user's to change, and is shown read-only.
    """
    details: list[tuple[str, str]] = []
    editable: tuple[str, str] | None = None
    for state in states:
        prefix = f"{state.label or state.id}: " if len(states) > 1 else ""
        target = teleport_to_friend.resolve_target(list(clients or []), state.params)
        if state.params.get("target_hooked_player", False):
            slot = int(state.params.get("target_player") or 0)
            label = f"{prefix}Target Player"
            details.append((label, f"P{slot} — {target.name}" if target.name else f"P{slot} — {target.error}"))
        else:
            details.append((f"{prefix}Friend Name", target.name or f"({target.error})"))
            if target.source != "the bot file (deprecated)":
                editable = ("Friend Name", teleport_to_friend.APP_SETTING_KEY)
        details.append((f"{prefix}Destination Zone", state.params.get("to_zone", "")))
    details.append(("Required Game Setting", "Options → Gameplay Options 4 → Dblclick Go To Friend: No"))
    return details, editable


_DETAIL_BUILDERS = {
    "quicksell": _quicksell_details,
    "teleport_to_friend": _teleport_to_friend_details,
}

#: Builders that need the live clients as a second argument.
_CLIENT_AWARE_BUILDERS = {"teleport_to_friend"}


def collect_safety_warnings(config: BotConfig, clients: list | None = None) -> list[SafetyWarning]:
    """Return a list of SafetyWarning for every risky node type present in *config*.

    Returns at most one SafetyWarning per risky node type, even when multiple
    states with the same node_type exist. Coverage is driven by the safety_warning
    field on each NodeSpec — adding a warning for a new node type requires only
    setting that field in the node's NODE_SPEC.

    *clients* is passed to detail builders that resolve something against the live
    clients, such as the wizard name behind a teleport target's player slot. It is
    optional so the collection stays callable from tests without any.
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
        details: list[tuple[str, str]] = []
        editable: tuple[str, str] | None = None
        if detail_builder:
            built = detail_builder(states, clients) if node_type in _CLIENT_AWARE_BUILDERS else detail_builder(states)
            details, editable = built if isinstance(built, tuple) else (built, None)
        warnings.append(SafetyWarning(message=spec.safety_warning, details=details, editable_setting=editable))

    return warnings


class SafetyWarningDialog(QDialog):
    """Shows one or more SafetyWarning entries and lets the user proceed or cancel."""

    def __init__(self, warnings: list[SafetyWarning], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Safety Check")
        self.setMinimumWidth(440)
        self._edits: list[tuple[str, QLineEdit]] = []
        self._build(warnings)

    @staticmethod
    def _stored_setting(key: str) -> str:
        from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415 — avoids an import cycle

        try:
            return str(SettingsManager.instance().get_general(key, default="") or "")
        except Exception:
            return ""

    def accept(self) -> None:
        """Save any edited settings, then accept.

        On accept only: cancelling a run must not leave a half-typed name behind. The
        write happens here rather than on every keystroke for the same reason.
        """
        from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415 — avoids an import cycle

        try:
            settings = SettingsManager.instance()
            changed = False
            for key, edit in self._edits:
                value = edit.text().strip()
                if value != self._stored_setting(key):
                    settings.set_general(key, value)
                    settings.add_recent_friend_name(value)
                    changed = True
            if changed:
                settings.save()
        except Exception:
            logger.exception("Could not save an edited safety-dialog setting")
        super().accept()

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

            if warning.editable_setting:
                label_text, setting_key = warning.editable_setting
                edit_container = QWidget()
                edit_form = QFormLayout(edit_container)
                edit_form.setContentsMargins(icon_size + 8, 0, 0, 0)
                edit_form.setSpacing(4)
                edit_form.setHorizontalSpacing(16)
                edit = QLineEdit(self._stored_setting(setting_key))
                edit.setPlaceholderText("Wizard name")
                edit.setToolTip("Applies to every bot on this machine. Saved when you run.")
                edit_form.addRow(f"Change {label_text}:", edit)
                layout.addWidget(edit_container)
                self._edits.append((setting_key, edit))

        buttons = QDialogButtonBox()
        run_btn = buttons.addButton("Run Bot", QDialogButtonBox.ButtonRole.AcceptRole)
        run_btn.setDefault(True)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
