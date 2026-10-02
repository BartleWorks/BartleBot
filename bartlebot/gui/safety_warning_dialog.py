"""Dialog shown before a bot run when the bot contains nodes with potentially risky behavior."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from bartlebot import bot_options, option_values
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.action_button import ActionStyle
from bartlebot.gui.widgets.dialog_footer import dialog_footer
from bartlebot.nodes.registry import NODE_SPECS
from bartlebot.nodes.teleportation import teleport_to_friend
from bartlebot.nodes.utility import handle_level_scaling, quicksell
from bartlebot.nodes.zone_entry import enter_dungeon

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


def _quicksell_enabled(config) -> bool:
    """Whether this bot's quicksell nodes will sell anything at all.

    Resolved the way ``run()`` resolves it, through ``resolve_options_for`` rather
    than by reading the option here: the option's node default is null, so its
    resolved *value* is ``None`` when no level answers, and only the node's own
    coercion knows that means on. Two readings of "off" would eventually disagree,
    and this one decides whether the user is warned about a sale.
    """
    return bool(quicksell.resolve_options_for(config)["enabled"])


def _quicksell_message(config, states, clients, message: str) -> str | None:
    """The warning quicksell earns on *this* run, or None to drop it.

    A bot with quicksell nodes is not necessarily a bot that sells: ``Enable
    Quicksell`` can resolve false, and then the node returns SUCCESS without opening
    anything. Making the user dismiss an irreversible-sale warning before a run with
    no sale in it is how they learn to click the dialog away — the one habit this
    dialog cannot afford to teach.

    Who turned it off decides which way that goes. An override or the app setting is
    the user's own doing and they need no telling, so the warning goes entirely. A
    bot file that declares it off is not something the user chose or can see from
    here, so the one sentence that is still true — the backpack fills, then the bank
    — is shown on its own instead of being lost.
    """
    if _quicksell_enabled(config):
        return message
    resolution = option_values.option_resolution(config, quicksell.OPTION_ENABLED)
    if resolution is not None and resolution.source in (bot_options.SOURCE_OVERRIDE, bot_options.SOURCE_APP_SETTING):
        return None
    return quicksell.SAFETY_WARNING_DISABLED


def _quicksell_details(config, states, clients) -> list[tuple[str, str]]:
    """What quicksell will actually sell, and which level decided each answer.

    Quicksell's settings are bot options now, so they are the bot's rather than any
    one state's — every quicksell node in a bot gets the same answers, and the rows
    are listed once rather than once per state.

    Nothing here reads a stored value directly. Each option goes through the same
    four levels the run will use, and the caption names the one that answered:
    this dialog is the last thing between the user and an irreversible sale, and
    what the bot file says is not the answer.

    When nothing is being sold the table is the switch alone. What the keep list
    holds and whether pets are spared are answers to a question that is not being
    asked, and a row per option would bury the one row that explains the rest.
    """
    details: list[tuple[str, str]] = []
    if not _quicksell_enabled(config):
        resolution = option_values.option_resolution(config, quicksell.OPTION_ENABLED)
        if resolution is None:
            return []
        return [(quicksell.OPTION_ENABLED, f"No ({resolution.source})")]

    for name in (quicksell.OPTION_ENABLED, *quicksell.SELL_FLAG_OPTIONS.values()):
        resolution = option_values.option_resolution(config, name)
        if resolution is None:
            continue
        details.append((name, f"{'Yes' if resolution.value else 'No'} ({resolution.source})"))

    keep_list = option_values.option_resolution(config, quicksell.OPTION_KEEP_LIST)
    if keep_list is not None:
        shown = ", ".join(str(k) for k in keep_list.value) if keep_list.value else "(nothing)"
        details.append((quicksell.OPTION_KEEP_LIST, f"{shown} ({keep_list.source})"))
    return details


def _teleport_to_friend_details(config, states, clients) -> tuple[list[tuple[str, str]], tuple[str, str] | None]:
    """What each teleport state will port to, resolved the same way the run will resolve it.

    The bot file does not show the answer in either mode: a hooked-player target is a slot
    whose name is read from the game, and a friend name is a bot option resolved against
    this machine. So this resolves both, and offers the name for editing only when some
    state actually uses it — in hooked-player mode the name is not the user's to change,
    and is shown read-only.

    The editable field writes the *app setting*, which is the shared level under the
    option. Offering it while a per-bot override sits above it would be a field whose
    edits the run ignores, so it is withheld then — the same reason it is withheld while
    a deprecated literal in the bot file is winning.
    """
    details: list[tuple[str, str]] = []
    editable: tuple[str, str] | None = None
    resolution = option_values.option_resolution(config, teleport_to_friend.OPTION_FRIEND_NAME)
    configured = "" if resolution is None else str(resolution.value or "")
    outranks_app_setting = resolution is not None and resolution.source == bot_options.SOURCE_OVERRIDE
    for state in states:
        prefix = f"{state.label or state.id}: " if len(states) > 1 else ""
        target = teleport_to_friend.resolve_target(list(clients or []), state.params, configured)
        if state.params.get("target_hooked_player", False):
            slot = int(state.params.get("target_player") or 0)
            label = f"{prefix}Target Player"
            details.append((label, f"P{slot} — {target.name}" if target.name else f"P{slot} — {target.error}"))
        else:
            details.append((f"{prefix}Friend Name", target.name or f"({target.error})"))
            if target.source == "the Friend Name option" and not outranks_app_setting:
                editable = ("Friend Name", teleport_to_friend.APP_SETTING_KEY)
        details.append((f"{prefix}Destination Zone", state.params.get("to_zone", "")))
    details.append(("Required Game Setting", "Options → Gameplay Options 4 → Dblclick Go To Friend: No"))
    return details, editable


def _enter_dungeon_message(config, states, clients, message: str) -> str | None:
    """The Team-Up warning, but only for the run that can actually be split by it.

    ``enter_dungeon`` is in most of the library, so a static warning on it would be one
    the user learns to click away. What is being warned about is narrower than the node:
    each client joins the queue on its own, and ``zone_name()`` matches the zone rather
    than the instance, so two clients matched into *different* copies of the dungeon
    would both read as arrived and the node would return SUCCESS on a split party.

    That needs all three of: an author who ticked Offers Team-Up, a user who turned the
    option on, and more than one client hooked. One client cannot be split from itself,
    and a normal entrance takes everyone through the same sigil.

    Not a refusal. The node cannot tell whether the clients landed together, which is a
    gap in what can be read rather than something the user got wrong -- and if an
    instance identity ever becomes readable this turns into a real check instead.
    """
    if not any(state.params.get(enter_dungeon.PARAM_OFFERS_TEAM_UP) for state in states):
        return None
    if len(clients or []) < 2:
        return None
    resolution = option_values.option_resolution(config, enter_dungeon.OPTION_TEAM_UP)
    if resolution is None or not resolution.value:
        return None
    return message


def _enter_dungeon_details(config, states, clients) -> list[tuple[str, str]]:
    """What the queue was asked for, and how many players are about to ask for it."""
    details: list[tuple[str, str]] = [("Players hooked", str(len(clients or [])))]
    resolution = option_values.option_resolution(config, enter_dungeon.OPTION_MINIMUM_TEAM_SIZE)
    if resolution is not None:
        details.append((enter_dungeon.OPTION_MINIMUM_TEAM_SIZE, f"{resolution.value} ({resolution.source})"))
    return details


#: node type -> ``(config, states, clients) -> details`` (or ``(details, editable)``).
#: Every builder takes all three: what a node will do is now mostly a question about
#: the bot's options rather than about one state's params, so a builder that could not
#: see the config could not answer it.
_DETAIL_BUILDERS = {
    "enter_dungeon": _enter_dungeon_details,
    "quicksell": _quicksell_details,
    "teleport_to_friend": _teleport_to_friend_details,
}

#: node type -> ``(config, states, clients, message) -> message or None``, where the
#: message passed in is the ``NodeSpec.safety_warning``. Returning it unchanged is the
#: default for a node with no entry here; returning None drops the warning for this run.
#:
#: This is the answer to "the node is in the bot, but will it do anything?", which a
#: NodeSpec field cannot answer because it is a question about the bot's options -- and
#: for ``enter_dungeon``, about how many clients are hooked. Quicksell has an option that
#: switches the node off wholesale, and ``enter_dungeon`` warns about one of its two ways
#: in rather than about itself; ``collect_reagent`` and ``teleport_to_friend`` both act
#: whenever they are reached, so a warning naming one is always earned.
#:
#: ``relog`` has no warning at all (#98): a warning is for something the user can act on
#: before the run, and there is nothing to decide about a relog but whether to run the bot.
_MESSAGE_BUILDERS = {
    "enter_dungeon": _enter_dungeon_message,
    "quicksell": _quicksell_message,
}


def level_scaling_players(config: BotConfig, client_count: int) -> list[int]:
    """The 1-based players this run will level scale, or [] for a bot with no level scaling node.

    Who the run-start elixir check reads. Resolved as the nodes resolve it, through the
    bot's options, so a user's override of Level Scaling Players is what counts.
    """
    if not any(state.node_type in handle_level_scaling.LEVEL_SCALING_NODE_TYPES for state in config.states):
        return []
    resolution = option_values.option_resolution(config, handle_level_scaling.LEVEL_SCALING_PLAYERS_OPTION.name)
    if resolution is None:
        return []
    return handle_level_scaling.selected_numbers(resolution.value, client_count)


def level_scaling_warning(elixir_players: Sequence[int]) -> SafetyWarning | None:
    """The warning for a run that starts with an elixir already active on a level scaling player.

    The game draws every elixir the same way, and telling them apart would mean opening each
    one on every check, so the nodes take any elixir for the level scaling one (#141). That is
    wrong in two ways the user can fix before the run and the bot cannot fix during it: the
    elixir may be another kind, and a level scaling one taken earlier runs out before the
    others'. The bot never cancels an elixir itself.
    """
    if not elixir_players:
        return None
    who = ", ".join(f"P{n}" for n in elixir_players)
    return SafetyWarning(
        message=(
            "A level scaling player already has an elixir active. The bot cannot tell one elixir from "
            "another, so it treats this player as level scaled and will not visit the Scales of Knowledge "
            "for them until the elixir runs out. If it is a different elixir, they run unscaled. If it is "
            "a level scaling elixir taken earlier, it runs out before the other players' and costs an "
            "extra trip. To start everyone together, cancel the elixir in game before running."
        ),
        details=[("Elixir already active", who)],
    )


def collect_safety_warnings(
    config: BotConfig, clients: list | None = None, elixir_players: Sequence[int] = ()
) -> list[SafetyWarning]:
    """Return a list of SafetyWarning for every risky node type present in *config*.

    Returns at most one SafetyWarning per risky node type, even when multiple
    states with the same node_type exist. Coverage is driven by the safety_warning
    field on each NodeSpec — adding a warning for a new node type requires only
    setting that field in the node's NODE_SPEC.

    A node type in ``_MESSAGE_BUILDERS`` gets the last word on its own entry: it may
    reword the warning for what this run will actually do, or drop it entirely when
    the node is present but inert.

    *clients* is passed to detail builders that resolve something against the live
    clients, such as the wizard name behind a teleport target's player slot. It is
    optional so the collection stays callable from tests without any.

    *elixir_players* is who among ``level_scaling_players`` showed an elixir when the run
    was requested. Reading that is async, so the caller reads it and hands it in.
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
        message_builder = _MESSAGE_BUILDERS.get(node_type)
        message = spec.safety_warning
        if message_builder:
            message = message_builder(config, states, clients, message)
            if message is None:
                continue
        detail_builder = _DETAIL_BUILDERS.get(node_type)
        details: list[tuple[str, str]] = []
        editable: tuple[str, str] | None = None
        if detail_builder:
            built = detail_builder(config, states, clients)
            details, editable = built if isinstance(built, tuple) else (built, None)
        warnings.append(SafetyWarning(message=message, details=details, editable_setting=editable))

    if elixir_warning := level_scaling_warning(elixir_players):
        warnings.append(elixir_warning)
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
        font.setPixelSize(THEME.font_body_px + 1)
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

        # The one place in the app where the commit deliberately does not take the accent.
        # This dialog exists to make the user stop and read before a run; filling the
        # button that dismisses a safety warning puts the chrome in argument with the
        # dialog's own purpose. Run Bot is still the default button — Enter runs — it just
        # does not advertise itself. See issue #89 before promoting it to PRIMARY.
        footer = dialog_footer(
            self,
            commit_text="Run Bot",
            commit_icon="play",
            commit_style=ActionStyle.TERTIARY,
        )
        layout.addLayout(footer.layout)
