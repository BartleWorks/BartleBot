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

from bartlebot.bot_tags import BOT_TAGS, TAG_GROUPS
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


def count_label(n: int) -> str:
    """The right-hand meta text for a tag used by *n* bots.

    Carries the unit: "49" beside a name reads as ambiguous until you work out what
    it counts, and the row has room for the word.
    """
    return f"{n} bot" if n == 1 else f"{n} bots"


def tag_sections(counts: dict[str, int] | None = None) -> list[Section]:
    """Build the popup's sections from the vocabulary.

    A tag with a zero count is still listed, dimmed. In the editor every tag must be
    offerable regardless of what the library happens to hold today, and in the
    library a dimmed "0 bots" is a clearer answer than a row that is missing.

    No descriptions: the names already say what the tags mean, and a second line
    under each one doubled the row height for nothing. What a description would have
    had to explain is a comment in bartlebot/bot_tags.py, where someone tagging a bot
    will actually read it.
    """
    sections: list[Section] = []
    for group, tags in TAG_GROUPS.items():
        rows = [
            PickerRow(
                key=tag,
                label=tag,
                meta=("" if counts is None else count_label(counts.get(tag, 0))),
                accent=group_accent(group),
                dim=(counts is not None and counts.get(tag, 0) == 0),
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
