"""Asked on save when a bot carries no Rewards tag: what does a run of it yield?

A library filtered by Rewards never finds a bot with none, and nothing breaks to say so.
This is the one save-time question about tags. The graph-suggested ones are a reminder in
the header instead (#19), because a reminder can be ignored for free and a question needs
somewhere to remember the answer. This one is remembered for as long as the bot stays
open, by the controller and nowhere else: a decline written into the bot file would ship
one user's choice to everyone, and one written to the app settings would outlive the
reason for it.

The tags are the header's own ``+ Tag`` pills, made checkable, rather than a list of
checkboxes: the same outline means "not a tag yet" on both surfaces. Rewards the graph
suggests (a reagent node suggests Reagents) start checked, which is usually none of them.
"""

from __future__ import annotations

import enum

from PyQt6.QtWidgets import QDialog, QLabel, QVBoxLayout

from bartlebot.content.bot_tags import REWARDS, TAG_GROUPS
from bartlebot.gui.widgets.action_button import ActionButton, ActionStyle
from bartlebot.gui.widgets.dialog_footer import dialog_footer
from bartlebot.gui.widgets.flow_layout import flow_container
from bartlebot.gui.widgets.tag_pill import SuggestedTagPill


class RewardsChoice(enum.Enum):
    """How the author answered. Cancel is ``QDialog.Rejected`` and has no member."""

    TAGGED = "tagged"
    WITHOUT = "without"


class RewardsTagDialog(QDialog):
    """Pick one or more Rewards tags and save, save without, or cancel the save."""

    def __init__(self, suggested: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("No Rewards Tag")
        self.setMinimumWidth(420)
        self.choice: RewardsChoice | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        text = QLabel(
            "This bot has no Rewards tag, so filtering the library by what a run yields "
            "will never find it. What does a run of it yield?"
        )
        text.setWordWrap(True)
        layout.addWidget(text)

        host, flow = flow_container(spacing=6)
        self._pills: list[SuggestedTagPill] = []
        preset = set(suggested or [])
        for tag in TAG_GROUPS[REWARDS]:
            pill = SuggestedTagPill(tag, tooltip=f"Tag this bot {tag}")
            pill.setCheckable(True)
            pill.setChecked(tag in preset)
            pill.toggled.connect(self._refresh_commit)
            flow.addWidget(pill)
            self._pills.append(pill)
        layout.addWidget(host)

        without = ActionButton("save_done", "Save Without", style=ActionStyle.TERTIARY)
        without.setToolTip("Save as it is. You won't be asked again until the bot is reopened.")
        without.clicked.connect(self._save_without)
        footer = dialog_footer(
            self, commit_text="Save", commit_icon="save_done", on_commit=self._save_tagged, extras=[without]
        )
        self._commit = footer.commit
        layout.addLayout(footer.layout)
        self._refresh_commit()

    def selected_tags(self) -> list[str]:
        """The checked Rewards, in vocabulary order."""
        return [p.tag for p in self._pills if p.isChecked()]

    def _refresh_commit(self) -> None:
        self._commit.setEnabled(bool(self.selected_tags()))

    def _save_tagged(self) -> None:
        if not self.selected_tags():
            return
        self.choice = RewardsChoice.TAGGED
        self.accept()

    def _save_without(self) -> None:
        self.choice = RewardsChoice.WITHOUT
        self.accept()
