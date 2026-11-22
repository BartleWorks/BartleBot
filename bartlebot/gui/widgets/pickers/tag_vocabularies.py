"""What BartleBot tags: the vocabularies of its bots and its subgraphs.

The tag picker, pills and chips are core's (``bartleby.qt.widgets.pickers.tag_picker`` and
``bartleby.qt.widgets.pills.tag_pill``, BartleBuddy #31, Stage 6), which take the vocabulary
they draw. Bot Info edits a bot's own tags and the library filters by them, with the same
one, so a tag reads the same on both.
"""

from __future__ import annotations

from bartleby.qt.widgets.pickers.tag_picker import TagVocabulary

from bartlebot.content.bot_tags import TAG_GROUPS
from bartlebot.content.subgraphs.subgraph_tags import CATEGORY, SUBGRAPH_CATEGORIES

#: Dot color per group. Two hues, so "is this a reward or an activity" is legible
#: on a single row without reading the section header above it. Activities is not
#: ``warning``: its gold swallowed the gold ring of the medallion leading each pill.
GROUP_ACCENT: dict[str, str] = {
    "Rewards": "accent",
    "Activities": "pill_activity_bg",
}

#: The bot tags. What each one means is a comment in ``bartlebot/content/bot_tags.py``, where
#: someone tagging a bot will actually read it.
BOT_VOCABULARY = TagVocabulary(TAG_GROUPS, GROUP_ACCENT, ("bot", "bots"))

#: A subgraph's categories (#172): what Bar B edits on a definition, and the Category
#: filter beside Source on every list of subgraphs. Source is not in it, because a file
#: never stores one; the filters take it from core's ``source_vocabulary``. Colored as the
#: strategy dialog's Role, the other stored group beside a derived Source.
SUBGRAPH_VOCABULARY = TagVocabulary(
    {CATEGORY: SUBGRAPH_CATEGORIES},
    {CATEGORY: "chip_neutral_bg"},
    ("subgraph", "subgraphs"),
)
