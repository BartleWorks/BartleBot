"""Which card a bot option is drawn in, and what a collapsed card says (#138).

Both option surfaces -- Option Values, where the user sets values, and Options, where the author
declares them -- draw a bot's options as a short column of folding cards rather than one
flat list. Atlantea carries thirteen options, and the Option Values tab ran 859px tall for them
against a 404px sidebar at 1080p; folding the groups nobody is working in is what gets
the rest onto the screen.

Four groups, always in this order, so an option sits in the same place in every bot:

==============  ======================================================  ===========
group           holds                                                   first run
==============  ======================================================  ===========
This dungeon    the author's own options, Challenge Mode                expanded
Team            Player Realms, Team-Up, Level Scaling Players, ...      expanded
Quicksell       Enable Quicksell as the header switch, the sell flags   collapsed
General         Safety Delay, Favorite Realm                            collapsed
==============  ======================================================  ===========

The group is declared once, on the option's ``BotOptionSpec``. An option no node
declares -- one the author wrote -- belongs to *This dungeon*, because that is the
only thing an author's own option can be about. A card with nothing in it is left out.

One option per group may *head* it (``BotOptionSpec.heads_group``): it is drawn as a
switch in the card's header rather than as a row, and turning it off disables every
row in the card. Enable Quicksell is the one there is. Nothing here decides how that
looks; this module only says which option it is.

Pure, like ``bot_options``: no Qt, no settings store, no client. The surfaces hand in
what they resolved and draw what comes back, so the grouping, the order and the
summaries are all testable without a widget.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

GROUP_DUNGEON = "dungeon"
GROUP_TEAM = "team"
GROUP_QUICKSELL = "quicksell"
GROUP_GENERAL = "general"

#: The order cards are drawn in. Fixed rather than following the graph: an option that
#: lands in a different place in every bot is one the user has to hunt for every time.
GROUP_ORDER: tuple[str, ...] = (GROUP_DUNGEON, GROUP_TEAM, GROUP_QUICKSELL, GROUP_GENERAL)

GROUP_TITLES: dict[str, str] = {
    GROUP_DUNGEON: "This dungeon",
    GROUP_TEAM: "Team",
    GROUP_QUICKSELL: "Quicksell",
    GROUP_GENERAL: "General",
}

#: The groups a user who has never folded anything sees collapsed. Quicksell holds the
#: tallest control on either surface, the keep-list table; General holds values the user
#: set once, in the app settings, and rarely revisits per bot.
FIRST_RUN_COLLAPSED: frozenset[str] = frozenset({GROUP_QUICKSELL, GROUP_GENERAL})

#: The ``general`` settings key holding ``{group: collapsed}``. Which cards are folded is
#: a property of the reader, like ``show_option_descriptions`` beside it: one answer for
#: every bot and both surfaces, and never written to a bot file.
COLLAPSED_SETTING = "option_group_collapsed"


@dataclass(frozen=True)
class OptionGroup:
    """One card's worth of options, in the order they are drawn.

    *header* is the option drawn as the card's switch, or ``None`` for a card whose
    header is only a title. It is never also in *members*.
    """

    id: str
    title: str
    header: Any
    members: tuple[Any, ...]

    @property
    def options(self) -> tuple[Any, ...]:
        """Every option on the card, the header's included."""
        return (self.header, *self.members) if self.header is not None else self.members


def group_of(spec) -> str:
    """The group an option declared by *spec* belongs to; *This dungeon* for no spec.

    A spec naming a group this build does not know is treated the same way, rather than
    dropping the option: a row with no card would be a value the user cannot reach.
    """
    group = getattr(spec, "group", "") if spec is not None else ""
    return group if group in GROUP_TITLES else GROUP_DUNGEON


def group_options(
    options: list,
    option_specs: Mapping[str, Any],
    *,
    is_tall: Callable[[Any], bool] = lambda _opt: False,
) -> list[OptionGroup]:
    """Sort a composed option list into cards, in ``GROUP_ORDER``.

    *option_specs* is ``{name: BotOptionSpec}`` -- ``bot_options.option_specs_for`` --
    and is where each option's group comes from. Within a card the composed order is
    kept, which is the order the bot's states ask for them, except that anything
    *is_tall* says is tall goes last: a 220px table between two spin boxes pushes the
    rest of its card out of sight.

    A header option is taken out of the member list. Only a bool may head a card, since
    a switch has two states; a non-bool claiming it stays an ordinary row.
    """
    buckets: dict[str, list] = {group: [] for group in GROUP_ORDER}
    headers: dict[str, Any] = {}
    for opt in options:
        spec = option_specs.get(opt.name)
        group = group_of(spec)
        if spec is not None and getattr(spec, "heads_group", False) and opt.type == "bool" and group not in headers:
            headers[group] = opt
            continue
        buckets[group].append(opt)

    out: list[OptionGroup] = []
    for group in GROUP_ORDER:
        members = buckets[group]
        ordered = [o for o in members if not is_tall(o)] + [o for o in members if is_tall(o)]
        header = headers.get(group)
        if not ordered and header is None:
            continue
        out.append(OptionGroup(group, GROUP_TITLES[group], header, tuple(ordered)))
    return out


def collapsed_groups(stored: Any) -> dict[str, bool]:
    """``{group: collapsed}`` for every group, from what ``COLLAPSED_SETTING`` holds.

    A group the user has never folded or unfolded falls back to ``FIRST_RUN_COLLAPSED``,
    and so does anything that is not a bool -- a hand-edited settings file is not a
    reason to open every card.
    """
    stored = stored if isinstance(stored, dict) else {}
    return {
        group: stored[group] if isinstance(stored.get(group), bool) else group in FIRST_RUN_COLLAPSED
        for group in GROUP_ORDER
    }


# ---------------------------------------------------------------------------
# What a collapsed card says
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SummaryPart:
    """One ``·``-separated piece of a collapsed card's summary line.

    *warning* parts are drawn in the warning color: a collapsed card is the one place a
    value can be out of sight, and a part that says something will be lost must not
    look like the parts beside it.
    """

    text: str
    warning: bool = False


def summary_text(parts: list[SummaryPart]) -> str:
    """The summary as one plain line, for a tooltip or a test."""
    return " · ".join(part.text for part in parts)


def group_summary(
    group: OptionGroup,
    values: Mapping[str, Any],
    *,
    keep_counts: tuple[int, int] | None = None,
    pets_sold: int = 0,
) -> list[SummaryPart]:
    """What *group*'s header says while it is collapsed.

    *values* maps an option name to what it resolved to -- the user's value on Option Values,
    the declaration on Options. A name missing from it, or holding ``None``, contributes
    nothing: the Options surface leaves out the user's own options altogether, and an
    author with no opinion has nothing to summarize.

    *keep_counts* is ``(kept, sellable)`` off the keep-list picker, and *pets_sold* is
    how many of the drop table's pets will be sold anyway -- both are facts only the
    picker has, so the surface passes them in.

    Returns an empty list when there is nothing to say, and the header then shows only
    its title.
    """
    names = {o.name for o in group.options}

    def value(name: str) -> Any:
        return values.get(name) if name in names else None

    if group.id == GROUP_DUNGEON:
        bools = [o for o in group.members if o.type == "bool"]
        if not bools:
            return []
        on = [o.name for o in bools if values.get(o.name) is True]
        return [SummaryPart(" · ".join(on) if on else "Nothing turned on")]

    if group.id == GROUP_TEAM:
        return _team_summary(value)
    if group.id == GROUP_QUICKSELL:
        return _quicksell_summary(group, value, keep_counts, pets_sold)
    if group.id == GROUP_GENERAL:
        return _general_summary(names, value)
    return []


def _team_summary(value: Callable[[str], Any]) -> list[SummaryPart]:
    # Imported here: the node modules pull in the whole node package, and this module
    # has to stay importable from a pure test.
    from bartlebot import player_realms  # noqa: PLC0415
    from bartlebot.nodes.zone_entry.enter_dungeon import OPTION_MINIMUM_TEAM_SIZE, OPTION_TEAM_UP  # noqa: PLC0415

    parts: list[SummaryPart] = []
    realms = value(player_realms.OPTION)
    if isinstance(realms, str) and realms:
        parts.append(SummaryPart(realms))
    team_up = value(OPTION_TEAM_UP)
    if team_up is True:
        size = value(OPTION_MINIMUM_TEAM_SIZE)
        parts.append(SummaryPart(f"Team-Up, {size}+ players" if isinstance(size, int) else "Team-Up"))
    elif team_up is False:
        parts.append(SummaryPart("no Team-Up"))
    scaling = value("Level Scaling Players")
    if isinstance(scaling, list):
        players = " ".join(f"P{p}" for p in scaling if isinstance(p, int) and not isinstance(p, bool))
        parts.append(SummaryPart(f"scaling {players}" if players else "no scaling"))
    return parts


def _quicksell_summary(
    group: OptionGroup,
    value: Callable[[str], Any],
    keep_counts: tuple[int, int] | None,
    pets_sold: int,
) -> list[SummaryPart]:
    from bartlebot.nodes.utility import quicksell  # noqa: PLC0415

    if group.header is not None and value(group.header.name) is False:
        return [SummaryPart("Off: nothing is sold")]
    parts: list[SummaryPart] = []
    sell_pets = value(quicksell.SELL_FLAG_OPTIONS["sell_pets"])
    if sell_pets is True:
        if pets_sold > 0:
            noun = "pet" if pets_sold == 1 else "pets"
            parts.append(SummaryPart(f"{pets_sold} {noun} will be sold", warning=True))
        else:
            parts.append(SummaryPart("pets sold"))
    elif sell_pets is False:
        parts.append(SummaryPart("pets kept"))
    if keep_counts is not None and quicksell.OPTION_KEEP_LIST in {o.name for o in group.members}:
        kept, sellable = keep_counts
        parts.append(SummaryPart(f"keeping {kept} of {sellable}"))
    return parts


def _general_summary(names: set[str], value: Callable[[str], Any]) -> list[SummaryPart]:
    from bartlebot.nodes.utility.change_realm import OPTION_FAVORITE_REALM  # noqa: PLC0415

    parts: list[SummaryPart] = []
    delay = value("Safety Delay")
    if isinstance(delay, (int, float)) and not isinstance(delay, bool):
        parts.append(SummaryPart(f"Delay {float(delay):.2f} s"))
    if OPTION_FAVORITE_REALM in names:
        realm = value(OPTION_FAVORITE_REALM)
        parts.append(SummaryPart(realm if isinstance(realm, str) and realm else "no favorite realm"))
    return parts
