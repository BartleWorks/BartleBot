"""The curated vocabulary of bot type tags, and the one rule for normalizing them.

A tag says what a bot is *for*; the world folder it lives in says where it runs.
The two are independent, which is why tags filter across the library rather than
subdividing it.

The vocabulary is fixed on purpose. A shipped library that drifts between
``reagent`` / ``Reagents`` / ``reagents`` cannot be filtered, and free-form tags in
files that everyone shares drift within a release. Adding a tag is a code change,
which is the point: it forces the question of whether an existing tag already covers
it.

Tags are deliberately *not* derived from ``drops[].category``. Nearly every boss
drops jewels and treasure cards, so a derived list would tag almost every bot with
almost every tag — the opposite of a filter. They record stated intent instead.

A few tags are *suggested* by the graph, and :func:`suggested_tags` is the whole list of
rules. They are the present-or-absent cases true of few bots — an ``enter_gauntlet`` node
means Gauntlet — and nothing broader: Boss against Mobs is two ``handle_combat`` nodes
with different monsters in front of them, which no rule can tell apart. A suggestion is
not a derived tag. The editor offers it, the author accepts it or does not, and once
accepted it is an ordinary tag in the file, removable and indistinguishable from one
typed by hand. Deriving and locking it would override an author who has a reagent node
in a combat bot and does not call it Collecting.

The cost of storing it is that it can go stale: delete the reagent node and Collecting
stays. The library test flags a *missing* suggested tag and never an extra one, because
a test cannot tell a stale tag from that author's judgment.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Ordered groups. "Rewards" is what a run yields, "Activities" is what it does, and a
# bot normally carries at least one of each. Validation is against the flat BOT_TAGS
# below — the grouping decides presentation order and nothing else.
TAG_GROUPS: dict[str, tuple[str, ...]] = {
    "Rewards": (
        "Gear",  # hats, robes, boots, wands, athames, amulets, rings, decks
        "Jewels",  # jewels and pins both
        "Reagents",
        "Spellements",  # the bot almost always needs a level-scaling node to get these
        "Animus",  # what a monstrology farm is actually after
        "Treasure Cards",
        "Snacks",
        "Seeds",
        "Mounts",
        "Pets",
        "Housing",
        "Gold",  # only where the gold alone is worth the run
    ),
    "Activities": (
        "Boss",  # one named boss, repeated
        "Mobs",  # a non-boss mob fight; replaces Boss rather than joining it
        "Dungeon",  # a full dungeon (more than just a single fight)
        "Challenge Mode",  # dungeons with Challenge Mode available
        "Gauntlet",  # housing gauntlets
        "Team-Up",  # the dungeon offers Team-Up; entering alone is always still an option
        "Collecting",  # a reagent-node route with no combat, unlike a boss that drops reagents
        "Training Points",  # a bot that completes training point quests
    ),
}

#: Every valid tag, in the order they are written to a bot file and shown in the UI.
BOT_TAGS: tuple[str, ...] = tuple(tag for group in TAG_GROUPS.values() for tag in group)

_TAG_ORDER: dict[str, int] = {tag: i for i, tag in enumerate(BOT_TAGS)}


def tag_group(tag: str) -> str:
    """The group *tag* belongs to, or an empty string if it is not in the vocabulary.

    Lives here rather than beside the picker because the grouping is vocabulary
    knowledge, and the library's filtering needs it as much as the widget does: a
    selection is ORed within a group and ANDed across groups, which cannot be
    decided without knowing which group each tag came from.
    """
    for group, tags in TAG_GROUPS.items():
        if tag in tags:
            return group
    return ""


def normalize_tags(raw: object) -> list[str]:
    """Coerce a bot file's raw ``tags`` value into a valid, ordered, de-duplicated list.

    Tolerant rather than strict, and deliberately so: this runs on every load, and a
    ``ValueError`` here would make a hand-edited bot fail to open at all rather than
    open with one tag missing. Same reasoning as ``accept_bare_drop_names`` and
    ``migrate_named_color`` in :mod:`bartlebot.bot.schema`.

    Used by both :class:`~bartlebot.bot.schema.BotConfig` and the library panel, which
    reads bot files outside Pydantic and needs the identical rule.
    """
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str):
            continue
        if entry not in _TAG_ORDER:
            logger.warning("ignoring unknown bot tag %r (not in BOT_TAGS)", entry)
            continue
        seen.add(entry)
    return sorted(seen, key=_TAG_ORDER.__getitem__)


#: The group a bot is asked about when it is saved without any tag from it.
REWARDS = "Rewards"


def _all_specs(specs=None):
    """The NodeSpec registry, imported late so this module stays importable alone."""
    if specs is not None:
        return specs
    from bartlebot.nodes.registry import NODE_SPECS  # noqa: PLC0415 - avoids an import cycle

    return NODE_SPECS


def suggested_tags(states, specs=None) -> list[str]:
    """The tags *states* suggest, in ``BOT_TAGS`` order.

    Pass the *composed* states: a ``handle_combat`` inside a linked subgraph is still
    combat, and is what keeps a bot that collects reagents between fights out of
    Collecting.

    The rules live beside the nodes that imply them, as ``NodeSpec.implies_tags`` and
    ``implies_tags_when``, so a new node that implies a tag never touches this module.
    The one rule no single node can know — Collecting needs the *absence* of combat — is
    written out here.
    """
    registry = _all_specs(specs)
    out: set[str] = set()
    node_types: set[str] = set()
    for state in states:
        node_types.add(state.node_type)
        spec = registry.get(state.node_type)
        if spec is None:
            continue
        out.update(spec.implies_tags)
        out.update(tag for tag, param in spec.implies_tags_when.items() if state.params.get(param))
    if "collect_reagent" in node_types and "handle_combat" not in node_types:
        out.add("Collecting")
    return [t for t in BOT_TAGS if t in out]


def wants_rewards_prompt(tags: list[str], is_template: bool) -> bool:
    """Whether saving a bot with *tags* should ask for a Rewards tag.

    A bot normally says what a run of it yields, and a library filtered by Rewards never
    finds one that does not. A template draft is exempt (*is_template*: the document has
    ``template_meta``): what a run yields depends on the dungeon it is turned into, so
    there is nothing true for one to say.
    """
    if any(tag_group(t) == REWARDS for t in tags):
        return False
    return not is_template
