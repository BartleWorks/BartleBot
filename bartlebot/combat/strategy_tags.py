"""The combat-strategy tag vocabulary, and the one rule for filtering the library by it (#125).

Shaped like :mod:`bartlebot.bot_tags`: ordered groups, a fixed vocabulary, and a
selection that ORs within a group and ANDs across groups, as in ``filter_bot_metas``.
Adding a tag is a code change, for the same reason it is there.

Two kinds of group, and the difference is where the value comes from:

- **Source** (Official / Official (modified) / Custom) is *derived* from the working
  set's :meth:`~bartlebot.combat.strategy_workset.StrategyWorkingSet.status` and never
  stored. Stored, it would go stale on the first edit of an Official one, and a user copy
  would claim to be one. Its words are :data:`bartlebot.store_roots.SOURCE_LABELS`, so
  a filter chip always says what the pill on the card it matches says. The one
  exception is :data:`PROFILE_SOURCE`, for an entry that belongs to the active combat
  profile (#118): its pill carries the profile's own name, which a fixed vocabulary
  cannot, so the filter says what kind of place that is instead.
- **Role** (Hitter, Support, Healer, Solo) is *stored*, in the file's ``tags``, on Player
  Strategies and Deck Presets only. A duel strategy is a whole team, so it has no
  single role, and carries no role group.

Two filters sit beside the tags and are not tags, because their values are not a
vocabulary but a fact about the entry's shape:

- **Works with school X.** A Player Strategy whose school set is empty (any school)
  or contains X; a duel with at least one player slot that accepts X. The question a
  user brings is "what works with my Storm wizard", never "what requires one", which
  is why an unrestricted entry matches every school. Several schools OR, as tags do.
- **Team size N**, duels only: those whose ``min_players``–``max_players`` includes N.
  When set, it also limits the school check to slots 1..N, since a slot past the team
  is not one anybody will play.

Pure, and importable without Qt, so the rules can be tested alone.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from bartlebot.combat.strategy_store import StrategyKind
from bartlebot.shared.strategy_models import ROLE_TAGS, normalize_role_tags
from bartlebot.store_roots import LISTED_ORIGINS, SOURCE_LABELS

logger = logging.getLogger(__name__)

SOURCE = "Source"
ROLE = "Role"

#: The Source value of an entry that belongs to the active combat profile, not the library.
PROFILE_SOURCE = "Profile"

#: The Source values, in the order a picker offers them. Missing is not one: a missing
#: entry is never listed.
SOURCE_TAGS: tuple[str, ...] = (
    PROFILE_SOURCE,
    *(SOURCE_LABELS[o] for o in LISTED_ORIGINS),
)

#: Every group, in presentation order.
STRATEGY_TAG_GROUPS: dict[str, tuple[str, ...]] = {SOURCE: SOURCE_TAGS, ROLE: ROLE_TAGS}

#: The groups a file stores, and so the ones an editor offers. Source is derived, so it never is.
STORED_GROUPS: tuple[str, ...] = (ROLE,)
STORED_TAGS: tuple[str, ...] = tuple(t for g in STORED_GROUPS for t in STRATEGY_TAG_GROUPS[g])

#: Coerce a file's raw ``tags`` into valid, ordered, de-duplicated stored tags. The stored
#: group is Role alone, whose vocabulary is the shared library's, since its files carry it.
normalize_strategy_tags = normalize_role_tags

#: Which groups each tab offers. Duels carry no role.
KIND_GROUPS: dict[StrategyKind, tuple[str, ...]] = {
    StrategyKind.DUEL: (SOURCE,),
    StrategyKind.PLAYER: (SOURCE, ROLE),
    StrategyKind.DECK: (SOURCE, ROLE),
}

#: Which kinds are asked about school and team size. Decks joined the first in #174: a
#: deck is built around one school's spells, and its school is what someone looking for
#: one asks first.
SCHOOL_KINDS = frozenset({StrategyKind.DUEL, StrategyKind.PLAYER, StrategyKind.DECK})
TEAM_SIZE_KINDS = frozenset({StrategyKind.DUEL})

#: The team sizes a filter offers.
TEAM_SIZES: tuple[int, ...] = (1, 2, 3, 4)


def strategy_tag_group(tag: str) -> str:
    """The group *tag* belongs to, or an empty string if it is not in the vocabulary."""
    for group, tags in STRATEGY_TAG_GROUPS.items():
        if tag in tags:
            return group
    return ""


def team_label(n: int) -> str:
    """What the team-size picker row and its chip say."""
    return "Solo" if n == 1 else f"Team of {n}"


@dataclass(frozen=True)
class StrategyFacets:
    """What the filter asks of one entry — computed once per card, not per keystroke.

    ``slot_schools`` holds one set per player slot the entry covers, empty meaning
    "any school": a Player Strategy or a deck has one; a duel has one per slot up to its
    ``max_players``, where a slot no assignment covers is empty, since it falls back
    to the built-in default.
    """

    tags: frozenset[str] = frozenset()
    slot_schools: tuple[frozenset[str], ...] = ()
    player_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class StrategyFilter:
    """The facet selection, as the filter bar holds it. Empty passes everything."""

    tags: frozenset[str] = frozenset()
    schools: frozenset[str] = frozenset()
    team_size: int | None = None

    @property
    def is_empty(self) -> bool:
        return not self.tags and not self.schools and self.team_size is None


def works_with_school(slot_schools: Iterable[frozenset[str]], schools: Iterable[str]) -> bool:
    """Whether any slot accepts any of *schools*. A slot with no set accepts all of them."""
    wanted = set(schools)
    return any(not slot or not slot.isdisjoint(wanted) for slot in slot_schools)


def facets_match(facets: StrategyFacets, flt: StrategyFilter) -> bool:
    """Whether an entry passes the selection: OR within a group, AND across groups."""
    wanted: dict[str, set[str]] = {}
    for tag in flt.tags:
        wanted.setdefault(strategy_tag_group(tag), set()).add(tag)
    if any(group_tags.isdisjoint(facets.tags) for group_tags in wanted.values()):
        return False
    slots = facets.slot_schools
    if flt.team_size is not None and facets.player_range is not None:
        low, high = facets.player_range
        if not low <= flt.team_size <= high:
            return False
        slots = slots[: flt.team_size]
    return not flt.schools or works_with_school(slots, flt.schools)


@dataclass
class FacetCounts:
    """How many entries carry each value, for the pickers' right-hand column."""

    tags: dict[str, int] = field(default_factory=dict)
    schools: dict[str, int] = field(default_factory=dict)
    team_sizes: dict[int, int] = field(default_factory=dict)


def count_facets(all_facets: Iterable[StrategyFacets], schools: Iterable[str]) -> FacetCounts:
    """Per-value counts, each answering "how many would this value alone show"."""
    counts = FacetCounts()
    schools = tuple(schools)
    for facets in all_facets:
        for tag in facets.tags:
            counts.tags[tag] = counts.tags.get(tag, 0) + 1
        for school in schools:
            if facets.slot_schools and works_with_school(facets.slot_schools, (school,)):
                counts.schools[school] = counts.schools.get(school, 0) + 1
        if facets.player_range is not None:
            low, high = facets.player_range
            for n in TEAM_SIZES:
                if low <= n <= high:
                    counts.team_sizes[n] = counts.team_sizes.get(n, 0) + 1
    return counts
