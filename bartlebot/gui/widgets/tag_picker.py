"""The bot-tag picker, built on :mod:`picker_popup` and shared by both surfaces.

Bot Info edits a bot's own tags; the library filters by them. Same vocabulary, same
grouping, same descriptions — so they share one adapter rather than each building
sections out of ``TAG_GROUPS`` and drifting apart.

The library additionally passes ``counts``, which fills the popup's right-hand slot
with how many bots carry each tag. That is the difference between guessing which tag
is worth clicking and being told, and it is meaningless in the editor, which is why
it is a parameter rather than something the widget works out for itself.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget

from bartlebot.bot_tags import BOT_TAGS, TAG_GROUPS, describe
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.picker_popup import PickerButton, PickerRow, Section

#: Dot colour per group. Two hues, so "is this a reward or an activity" is legible
#: on a single row without reading the section header above it.
GROUP_ACCENT: dict[str, str] = {
    "Rewards": "accent",
    "Activity": "warning",
}


def group_accent(group: str) -> str:
    """Resolve a group's dot colour against the live theme."""
    return getattr(THEME, GROUP_ACCENT.get(group, "text_muted"))


def tag_group(tag: str) -> str:
    """The group *tag* belongs to, or an empty string if it is not in the vocabulary."""
    for group, tags in TAG_GROUPS.items():
        if tag in tags:
            return group
    return ""


def tag_sections(counts: dict[str, int] | None = None) -> list[Section]:
    """Build the popup's sections from the vocabulary.

    A tag with a zero count is still listed. In the editor every tag must be
    offerable regardless of what the library happens to hold today, and in the
    library a visible "0 bots" is a clearer answer than a row that is missing.
    """
    sections: list[Section] = []
    for group, tags in TAG_GROUPS.items():
        rows = [
            PickerRow(
                key=tag,
                label=tag,
                description=describe(tag),
                meta=("" if counts is None else f"{counts.get(tag, 0)}"),
                accent=group_accent(group),
            )
            for tag in tags
        ]
        sections.append((group, rows))
    return sections


class TagPickerButton(PickerButton):
    """A :class:`PickerButton` preloaded with the bot-tag vocabulary."""

    def __init__(
        self,
        *,
        title: str = "Bot tags",
        placeholder: str = "—",
        counts: dict[str, int] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            title,
            placeholder=placeholder,
            search_placeholder="Filter tags…",
            parent=parent,
        )
        self.set_counts(counts)

    def set_counts(self, counts: dict[str, int] | None) -> None:
        """Rebuild the rows against fresh per-tag bot counts."""
        self.set_sections(tag_sections(counts))

    def tags(self) -> list[str]:
        """Selected tags, in ``BOT_TAGS`` order."""
        chosen = set(self.selection())
        return [t for t in BOT_TAGS if t in chosen]
