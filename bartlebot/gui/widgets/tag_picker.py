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

from functools import lru_cache

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QWidget

from bartlebot import card_art
from bartlebot.bot_tags import BOT_TAGS, TAG_GROUPS, tag_group  # noqa: F401  (re-exported)
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.card_art_tile import art_icon
from bartlebot.gui.widgets.picker_popup import PickerButton, PickerRow, Section

#: Side of a tag's medallion wherever it is drawn. 16px was hard to read; the medallions
#: are 128px (two are 64px) game art, and 20px is the smallest that stays legible.
TAG_ICON_PX = 20


def tag_icon(tag: str) -> QIcon | None:
    """A tag's extracted medallion at :data:`TAG_ICON_PX`, or None -- the ordinary case.

    None on every install that has not run the extractor, and always for the Type tags,
    which have no game art. Every caller keeps its text form for that case.

    Cached per :func:`card_art.cache_generation`, so ``card_art.forget_cache`` is what
    makes new art visible here too. The lookup stats the file twice, and the library
    asks once per tag per card -- a few hundred stats a listing, which on Windows was a
    visible share of a keystroke in the search box.
    """
    return _tag_icon(tag, card_art.cache_generation())


@lru_cache(maxsize=64)
def _tag_icon(tag: str, _generation: int) -> QIcon | None:
    path = card_art.tag_icon_path(tag)
    return None if path is None else art_icon(path)


#: Dot colour per group. Two hues, so "is this a reward or an activity" is legible
#: on a single row without reading the section header above it. Activity is not
#: ``warning``: its gold swallowed the gold ring of the medallion leading each pill.
GROUP_ACCENT: dict[str, str] = {
    "Rewards": "accent",
    "Activity": "pill_activity_bg",
}


def group_accent(group: str) -> str:
    """Resolve a group's dot colour against the live theme."""
    return getattr(THEME, GROUP_ACCENT.get(group, "text_muted"))


def count_label(n: int) -> str:
    """The right-hand meta text for a tag used by *n* bots.

    Carries the unit: "49" beside a name reads as ambiguous until you work out what
    it counts, and the row has room for the word.
    """
    return f"{n} bot" if n == 1 else f"{n} bots"


def tag_sections(counts: dict[str, int] | None = None, group: str | None = None) -> list[Section]:
    """Build the popup's sections from the vocabulary, or from one *group* of it.

    A tag with a zero count is still listed, dimmed. In the editor every tag must be
    offerable regardless of what the library happens to hold today, and in the
    library a dimmed "0 bots" is a clearer answer than a row that is missing.

    No descriptions: the names already say what the tags mean, and a second line
    under each one doubled the row height for nothing. What a description would have
    had to explain is a comment in bartlebot/bot_tags.py, where someone tagging a bot
    will actually read it.

    *group* narrows the popup to one group's tags. The library opens one button per
    group, so that each is labelled with the facet it filters — "Rewards", "Activity"
    — and the vocabulary is legible without opening anything. Bot Info passes None
    and gets the whole thing, because tagging a bot is one decision across both.
    """
    sections: list[Section] = []
    for name, tags in TAG_GROUPS.items():
        if group is not None and name != group:
            continue
        rows = [
            PickerRow(
                key=tag,
                label=tag,
                meta=("" if counts is None else count_label(counts.get(tag, 0))),
                accent=group_accent(name),
                dim=(counts is not None and counts.get(tag, 0) == 0),
                icon=tag_icon(tag),
            )
            for tag in tags
        ]
        sections.append((name, rows))
    return sections


class TagPickerButton(PickerButton):
    """A :class:`PickerButton` preloaded with the bot-tag vocabulary.

    Pass *group* to offer only that group's tags; the default offers all of them.

    *caption* pins the button's text. A filter button says what is selected, because
    the selection *is* the filter and there is nowhere else to read it. An editor
    button sits above chips that already show the selection, so its text is free to
    say what pressing it does instead — "Add Rewards…" rather than "Rewards (2)".
    """

    #: Class-level default so ``PickerButton.__init__`` can call ``format_text``
    #: before the instance attribute is assigned.
    _group: str | None = None
    _caption: str | None = None

    def __init__(
        self,
        *,
        title: str = "Bot tags",
        placeholder: str = "—",
        counts: dict[str, int] | None = None,
        group: str | None = None,
        caption: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            title,
            placeholder=placeholder,
            search_placeholder="Filter tags…",
            parent=parent,
        )
        self._group = group
        self._caption = caption
        if caption is not None:
            self.setText(caption)
        self.set_counts(counts)

    @property
    def group(self) -> str | None:
        """The tag group this button offers, or None for the whole vocabulary."""
        return self._group

    def format_text(self, labels: list[str]) -> str:
        """Keep a grouped button labelled with its group, plus a count when active.

        A grouped picker is one of a row of facets, so its name has to stay legible
        whether or not anything is selected — that name is the only thing advertising
        the vocabulary to someone who has not opened it. The count is what says a
        filter is on without making the user read the chip bar.
        """
        if self._caption is not None:
            return self._caption
        if self._group is None:
            return super().format_text(labels)
        return self._group if not labels else f"{self._group} ({len(labels)})"

    def set_counts(self, counts: dict[str, int] | None) -> None:
        """Rebuild the rows against fresh per-tag bot counts."""
        self.set_sections(tag_sections(counts, self._group))

    def tags(self) -> list[str]:
        """Selected tags, in ``BOT_TAGS`` order."""
        chosen = set(self.selection())
        return [t for t in BOT_TAGS if t in chosen]
