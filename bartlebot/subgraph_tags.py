"""The subgraph category vocabulary, and the one rule for filtering subgraphs by it (#172).

The shape of :mod:`bartlebot.strategy_tags`: a stored group with a fixed vocabulary,
normalized on load, beside a Source group that is worked out and never stored. Adding a
category is a code change, for the same reason it is for strategies and bot tags: a
library shared between users that drifts between ``travel`` / ``Travel`` / ``Moving``
cannot be filtered.

- **Source** (Official / Official (modified) / Custom) is *derived* from where the
  definition is, :meth:`~bartlebot.subgraph_store.SubgraphLibrary.origin`, and never
  stored. Its words are :data:`bartlebot.store_roots.SOURCE_LABELS`, so a filter chip
  always says what the pill on the card it matches says.
- **Category** is *stored*, in the definition file's ``tags``. It says what the subgraph
  does, in the node browser's own category words wherever one fits, because a subgraph is
  a node the user made and is added from the same dialog. **Team** is the one word the
  node browser has no tab for: bringing several players together, which is what the one
  shipped subgraph, Sync players, does, and what a node never does alone.

A subgraph may carry several categories. A filter ORs within a group and ANDs across
groups, as the strategy dialog's and the bot library's do.

Pure, and importable without Qt, so the rules can be tested alone.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from bartlebot.store_roots import LISTED_ORIGINS, SOURCE_LABELS

logger = logging.getLogger(__name__)

SOURCE = "Source"
CATEGORY = "Category"

#: The categories, in the order a picker offers them and a file stores them: roughly
#: the order a run reaches them, with Utility, the catch-all, last.
SUBGRAPH_CATEGORIES: tuple[str, ...] = (
    "Zone Entry",  # into a dungeon, a gauntlet or a zone, by its door or an object
    "Teleportation",  # across a world: coordinates, a marked location, a friend, a hub
    "Team",  # bringing several players to one zone, realm and area
    "Combat and Healing",  # a fight and recovering after it: potions, health and mana, wisps
    "Interaction",  # talking to an NPC, clicking through a menu, collecting reagents
    "Control Flow",  # choosing the next step: checks on health, zone or scaling, variables, branches
    "Utility",  # anything else: applying level scaling, selling, relogging, changing realm, waiting
)

#: The Source values, in the order a picker offers them.
SOURCE_TAGS: tuple[str, ...] = tuple(SOURCE_LABELS[o] for o in LISTED_ORIGINS)

#: Every group, in presentation order.
SUBGRAPH_TAG_GROUPS: dict[str, tuple[str, ...]] = {CATEGORY: SUBGRAPH_CATEGORIES, SOURCE: SOURCE_TAGS}

_CATEGORY_ORDER: dict[str, int] = {tag: i for i, tag in enumerate(SUBGRAPH_CATEGORIES)}


def subgraph_tag_group(tag: str) -> str:
    """The group *tag* belongs to, or an empty string if it is not in the vocabulary."""
    for group, tags in SUBGRAPH_TAG_GROUPS.items():
        if tag in tags:
            return group
    return ""


def normalize_subgraph_tags(raw: object) -> list[str]:
    """Coerce a definition file's raw ``tags`` into valid, ordered, de-duplicated categories.

    Tolerant, because this runs on every load and raising would make a hand-edited
    definition refuse to open, and every bot linking it lose its link. A tag outside
    :data:`SUBGRAPH_CATEGORIES`, a derived Source word written in by hand included, is
    dropped with a warning.
    """
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str):
            continue
        if entry not in _CATEGORY_ORDER:
            logger.warning("ignoring unknown subgraph category %r (not in SUBGRAPH_CATEGORIES)", entry)
            continue
        seen.add(entry)
    return sorted(seen, key=_CATEGORY_ORDER.__getitem__)


def subgraph_matches(categories: Iterable[str], source: str, chosen: Iterable[str]) -> bool:
    """Whether a subgraph with *categories*, whose Source word is *source*, passes *chosen*.

    *chosen* is every selected word, of either group. OR within a group, AND across
    groups: "Combat or Team, *and* Custom". Nothing chosen passes everything.
    """
    wanted: dict[str, set[str]] = {}
    for tag in chosen:
        wanted.setdefault(subgraph_tag_group(tag), set()).add(tag)
    have = {source, *categories}
    return all(not group_tags.isdisjoint(have) for group_tags in wanted.values())
