"""Which card each bot option is drawn in, and what a collapsed card says (#138).

Pure: ``option_groups`` takes composed options and specs and returns cards, so none
of this needs a ``QApplication``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from bartlebot import option_groups as og
from bartlebot import player_realms
from bartlebot.bot.schema import BotConfig
from bartlebot.bot_options import compose_options, option_specs_for
from bartlebot.nodes.registry import NODE_SPECS
from bartlebot.nodes.utility import quicksell as qs
from bartlebot.nodes.utility.change_realm import OPTION_FAVORITE_REALM
from bartlebot.nodes.zone_entry.enter_dungeon import OPTION_MINIMUM_TEAM_SIZE, OPTION_TEAM_UP
from bartlebot.paths import project_root

_ROOT = project_root()


def _opt(name: str, type_: str = "bool"):
    return SimpleNamespace(name=name, type=type_)


def _spec(group: str = "", heads_group: bool = False):
    return SimpleNamespace(group=group, heads_group=heads_group)


def _all_option_specs():
    return [req for spec in NODE_SPECS.values() for req in spec.required_bot_options]


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


def test_every_node_option_declares_a_group():
    """An undeclared group lands in This dungeon, which is the author's card -- a node
    option there would read as something the bot's author chose to offer."""
    missing = sorted({req.name for req in _all_option_specs() if req.group not in og.GROUP_TITLES})

    assert missing == []


def test_every_declaration_of_one_option_agrees_on_its_card():
    """Safety Delay is declared by fifteen nodes, and the first state's spec answers --
    so one disagreeing declaration would move the row depending on the bot."""
    seen: dict[str, tuple[str, bool]] = {}
    clashes = []
    for req in _all_option_specs():
        placed = (req.group, req.heads_group)
        if seen.setdefault(req.name, placed) != placed:
            clashes.append(req.name)

    assert clashes == []


def test_only_a_bool_heads_a_card_and_only_one_per_card():
    heads = {(req.group, req.name, req.type) for req in _all_option_specs() if req.heads_group}

    assert all(type_ == "bool" for _group, _name, type_ in heads)
    assert len({group for group, _name, _type in heads}) == len({name for _group, name, _type in heads})


def test_enable_quicksell_heads_the_quicksell_card():
    heads = {req.name for req in _all_option_specs() if req.heads_group}

    assert heads == {qs.OPTION_ENABLED}


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def test_the_cards_follow_the_fixed_order_and_skip_empty_ones():
    options = [_opt("Safety Delay", "float"), _opt("Fail puzzle")]
    specs = {"Safety Delay": _spec(og.GROUP_GENERAL)}

    groups = og.group_options(options, specs)

    assert [g.id for g in groups] == [og.GROUP_DUNGEON, og.GROUP_GENERAL]
    assert [g.title for g in groups] == ["This dungeon", "General"]


def test_an_option_no_node_declares_belongs_to_this_dungeon():
    groups = og.group_options([_opt("Skip mobs")], {})

    assert groups[0].id == og.GROUP_DUNGEON
    assert [o.name for o in groups[0].members] == ["Skip mobs"]


def test_a_group_this_build_does_not_know_falls_back_to_this_dungeon():
    """A row with no card is a value the user cannot reach."""
    groups = og.group_options([_opt("Odd")], {"Odd": _spec("someday")})

    assert groups[0].id == og.GROUP_DUNGEON


def test_the_header_option_comes_out_of_the_member_list():
    options = [_opt("Enable"), _opt("Sell"), _opt("Keep", "drop_keep_list")]
    specs = {
        "Enable": _spec(og.GROUP_QUICKSELL, heads_group=True),
        "Sell": _spec(og.GROUP_QUICKSELL),
        "Keep": _spec(og.GROUP_QUICKSELL),
    }

    (group,) = og.group_options(options, specs)

    assert group.header.name == "Enable"
    assert [o.name for o in group.members] == ["Sell", "Keep"]
    assert [o.name for o in group.options] == ["Enable", "Sell", "Keep"]


def test_a_card_holding_only_its_header_is_still_drawn():
    """The Options tab leaves the user's own options out, but here the header is the
    one present -- turning quicksell off still has to be possible."""
    groups = og.group_options([_opt("Enable")], {"Enable": _spec(og.GROUP_QUICKSELL, heads_group=True)})

    assert [(g.id, g.header.name, g.members) for g in groups] == [(og.GROUP_QUICKSELL, "Enable", ())]


def test_a_non_bool_claiming_the_header_stays_a_row():
    """A switch has two states."""
    groups = og.group_options([_opt("Count", "int")], {"Count": _spec(og.GROUP_TEAM, heads_group=True)})

    assert groups[0].header is None
    assert [o.name for o in groups[0].members] == ["Count"]


def test_tall_controls_go_last_within_their_own_card():
    """Not last in the whole list: that is what separated the keep list from the other
    four quicksell rows, with two other cards' worth of options in between."""
    options = [_opt("Keep", "drop_keep_list"), _opt("Sell"), _opt("Delay", "float")]
    specs = {"Keep": _spec(og.GROUP_QUICKSELL), "Sell": _spec(og.GROUP_QUICKSELL), "Delay": _spec(og.GROUP_GENERAL)}

    groups = og.group_options(options, specs, is_tall=lambda o: o.type == "drop_keep_list")

    assert [[o.name for o in g.members] for g in groups] == [["Sell", "Keep"], ["Delay"]]


def test_atlantea_groups_as_the_design_says():
    """The shipped bot with the most options, end to end through ``compose_options``."""
    data = json.loads((_ROOT / "bots" / "13_aquila" / "atlantea.json").read_text(encoding="utf-8"))
    config = BotConfig.model_validate(data)

    groups = og.group_options(compose_options(config), option_specs_for(config))
    layout = {g.id: (g.header.name if g.header else None, {o.name for o in g.members}) for g in groups}

    assert [g.id for g in groups] == list(og.GROUP_ORDER)
    assert layout[og.GROUP_DUNGEON] == (None, {"Fail puzzle and defeat Teles", "Challenge Mode"})
    # A dungeon first, built from Dungeon Farmer, which does not offer Team-Up.
    assert layout[og.GROUP_TEAM] == (None, {player_realms.OPTION, "Level Scaling Players"})
    assert layout[og.GROUP_QUICKSELL] == (qs.OPTION_ENABLED, {*qs.SELL_FLAG_OPTIONS.values(), qs.OPTION_KEEP_LIST})
    assert layout[og.GROUP_GENERAL] == (None, {"Safety Delay", "Refill Potions", OPTION_FAVORITE_REALM})


# ---------------------------------------------------------------------------
# Fold state
# ---------------------------------------------------------------------------


def test_quicksell_and_general_start_collapsed():
    assert og.collapsed_groups(None) == {
        og.GROUP_DUNGEON: False,
        og.GROUP_TEAM: False,
        og.GROUP_QUICKSELL: True,
        og.GROUP_GENERAL: True,
    }


def test_a_stored_fold_wins_over_the_first_run_state():
    folds = og.collapsed_groups({og.GROUP_QUICKSELL: False, og.GROUP_TEAM: True})

    assert folds[og.GROUP_QUICKSELL] is False
    assert folds[og.GROUP_TEAM] is True
    assert folds[og.GROUP_GENERAL] is True


def test_a_stored_fold_that_is_not_a_bool_is_ignored():
    """A hand-edited settings file is not a reason to open every card."""
    assert og.collapsed_groups({og.GROUP_GENERAL: "no"})[og.GROUP_GENERAL] is True
    assert og.collapsed_groups(["not", "a", "dict"]) == og.collapsed_groups(None)


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------


def _group(group_id: str, *options, header=None):
    return og.OptionGroup(group_id, og.GROUP_TITLES[group_id], header, tuple(options))


def test_this_dungeon_names_the_check_boxes_that_are_on():
    group = _group(og.GROUP_DUNGEON, _opt("Fail puzzle"), _opt("Challenge Mode"))

    assert og.summary_text(og.group_summary(group, {"Fail puzzle": True, "Challenge Mode": False})) == "Fail puzzle"
    assert og.summary_text(og.group_summary(group, {"Fail puzzle": False, "Challenge Mode": False})) == (
        "Nothing turned on"
    )


def test_this_dungeon_says_nothing_when_it_holds_no_check_boxes():
    """Saying "Nothing turned on" would be a claim about options that cannot be turned on."""
    group = _group(og.GROUP_DUNGEON, _opt("Passes", "int"))

    assert og.group_summary(group, {"Passes": 2}) == []


def test_team_summarizes_realms_team_up_and_scaling():
    group = _group(
        og.GROUP_TEAM,
        _opt(player_realms.OPTION, "player_realms"),
        _opt(OPTION_TEAM_UP),
        _opt(OPTION_MINIMUM_TEAM_SIZE, "int"),
        _opt("Level Scaling Players", "player_list"),
    )
    values = {
        player_realms.OPTION: "Together",
        OPTION_TEAM_UP: False,
        OPTION_MINIMUM_TEAM_SIZE: 2,
        "Level Scaling Players": [2, 3, 4],
    }

    assert og.summary_text(og.group_summary(group, values)) == "Together · no Team-Up · scaling P2 P3 P4"
    values.update({OPTION_TEAM_UP: True, OPTION_MINIMUM_TEAM_SIZE: 3, "Level Scaling Players": []})
    assert og.summary_text(og.group_summary(group, values)) == "Together · Team-Up, 3+ players · no scaling"


def test_team_says_only_what_the_bot_has():
    group = _group(og.GROUP_TEAM, _opt(player_realms.OPTION, "player_realms"))

    assert og.summary_text(og.group_summary(group, {player_realms.OPTION: "Separate", OPTION_TEAM_UP: True})) == (
        "Separate"
    )


def _quicksell_group():
    return _group(
        og.GROUP_QUICKSELL,
        _opt(qs.SELL_FLAG_OPTIONS["sell_pets"]),
        _opt(qs.OPTION_KEEP_LIST, "drop_keep_list"),
        header=_opt(qs.OPTION_ENABLED),
    )


def test_quicksell_off_says_so_and_nothing_else():
    parts = og.group_summary(_quicksell_group(), {qs.OPTION_ENABLED: False}, keep_counts=(2, 58))

    assert og.summary_text(parts) == "Off: nothing is sold"


def test_quicksell_on_says_what_happens_to_pets_and_how_much_is_kept():
    values = {qs.OPTION_ENABLED: True, qs.SELL_FLAG_OPTIONS["sell_pets"]: False}

    parts = og.group_summary(_quicksell_group(), values, keep_counts=(2, 58))

    assert og.summary_text(parts) == "pets kept · keeping 2 of 58"
    assert not any(part.warning for part in parts)


def test_pets_that_will_be_sold_anyway_are_a_warning():
    """No keyword can spare a pet, so a collapsed card must not let this read like the
    parts beside it."""
    values = {qs.OPTION_ENABLED: True, qs.SELL_FLAG_OPTIONS["sell_pets"]: True}

    parts = og.group_summary(_quicksell_group(), values, keep_counts=(0, 58), pets_sold=2)

    assert parts[0] == og.SummaryPart("2 pets will be sold", warning=True)
    assert og.group_summary(_quicksell_group(), values, pets_sold=1)[0].text == "1 pet will be sold"
    assert og.group_summary(_quicksell_group(), values)[0] == og.SummaryPart("pets sold")


def test_general_summarizes_the_delay_and_the_realm():
    group = _group(og.GROUP_GENERAL, _opt("Safety Delay", "float"), _opt(OPTION_FAVORITE_REALM, "realm"))

    assert og.summary_text(og.group_summary(group, {"Safety Delay": 3, OPTION_FAVORITE_REALM: "Phoenix"})) == (
        "Delay 3.00 s · Phoenix"
    )
    assert og.summary_text(og.group_summary(group, {"Safety Delay": 1.5, OPTION_FAVORITE_REALM: ""})) == (
        "Delay 1.50 s · no favorite realm"
    )


def test_a_missing_or_null_value_contributes_nothing():
    """The Options tab has no value for the user's own options, and a declaration of
    None is an author with no opinion."""
    group = _group(og.GROUP_GENERAL, _opt("Safety Delay", "float"))

    assert og.group_summary(group, {}) == []
    assert og.group_summary(group, {"Safety Delay": None}) == []
