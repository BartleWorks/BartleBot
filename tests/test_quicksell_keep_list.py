"""Tests for the quicksell keep-list logic shared by the node and the properties panel."""

from bartlebot.nodes.utility.quicksell import (
    NEVER_QUICKSOLD_CATEGORIES,
    OPTION_ENABLED,
    OPTION_KEEP_LIST,
    SELL_FLAG_DEFAULTS,
    SELL_FLAG_OPTIONS,
    UNMATCHABLE_CATEGORIES,
    category_keep_reasons,
    category_unmatchable_reasons,
    describe_config,
    drop_keep_states,
    effective_keep_list,
    matching_keyword,
    resolve_options,
    sell_flag,
    set_drops_in_keep_list,
    toggle_drop_in_keep_list,
)

# Both sell_* bools on, so no implied keywords unless a test asks for them.
_SELL_ALL = {"sell_socket_wrenches": True, "sell_skeleton_keys": True}


# ---------------------------------------------------------------------------
# effective_keep_list
# ---------------------------------------------------------------------------


def test_lowercases_and_strips_keywords():
    params = {"keep_list": ["  Amber ", "Boots Of Dawn"], **_SELL_ALL}
    assert effective_keep_list(params) == ["amber", "boots of dawn"]


def test_drops_blank_keywords():
    # An empty keyword is a substring of every name and would keep the whole backpack.
    params = {"keep_list": ["Amber", "", "   "], **_SELL_ALL}
    assert effective_keep_list(params) == ["amber"]


def test_missing_keep_list_yields_only_implied_keywords():
    assert effective_keep_list({}) == ["socket wrench", "skeleton key"]


def test_sell_bools_control_implied_keywords():
    params = {"keep_list": [], "sell_socket_wrenches": True, "sell_skeleton_keys": False}
    assert effective_keep_list(params) == ["skeleton key"]


# ---------------------------------------------------------------------------
# matching_keyword
# ---------------------------------------------------------------------------


def test_matches_case_insensitive_substring():
    assert matching_keyword("Amber Ring", ["amber"]) == "amber"


def test_returns_none_when_nothing_matches():
    assert matching_keyword("Boots of Dawn", ["amber", "socket wrench"]) is None


def test_returns_first_matching_keyword():
    assert matching_keyword("Amber Ring", ["ring", "amber"]) == "ring"


def test_empty_keyword_never_matches():
    assert matching_keyword("Boots of Dawn", [""]) is None


# ---------------------------------------------------------------------------
# drop_keep_states
# ---------------------------------------------------------------------------


def test_exact_name_is_kept_and_marked_exact():
    (state,) = drop_keep_states(["Amber"], {"keep_list": ["Amber"], **_SELL_ALL})
    assert (state.kept, state.exact, state.keyword) == (True, True, "amber")


def test_partial_match_is_kept_but_not_exact():
    # "Amber" also spares "Amber Ring" — the panel shows that row locked.
    _, ring = drop_keep_states(["Amber", "Amber Ring"], {"keep_list": ["Amber"], **_SELL_ALL})
    assert (ring.kept, ring.exact, ring.keyword) == (True, False, "amber")


def test_implied_keyword_keeps_item_without_an_exact_entry():
    params = {"keep_list": [], "sell_socket_wrenches": False, "sell_skeleton_keys": True}
    (state,) = drop_keep_states(["Socket Wrench"], params)
    assert (state.kept, state.exact, state.keyword) == (True, False, "socket wrench")


def test_unmatched_item_is_sold():
    (state,) = drop_keep_states(["Boots of Dawn"], {"keep_list": ["Amber"], **_SELL_ALL})
    assert (state.kept, state.exact, state.keyword) == (False, False, None)


def test_states_preserve_drop_order_and_names():
    drops = ["Cloak of Nine Winds", "Amber", "Boots of Dawn"]
    assert [s.name for s in drop_keep_states(drops, {"keep_list": [], **_SELL_ALL})] == drops


def test_empty_drop_table_yields_no_states():
    assert drop_keep_states([], {"keep_list": ["Amber"], **_SELL_ALL}) == []


# ---------------------------------------------------------------------------
# toggle_drop_in_keep_list
# ---------------------------------------------------------------------------


def test_toggle_on_appends_the_exact_name():
    assert toggle_drop_in_keep_list(["Amber"], "Boots of Dawn", True) == ["Amber", "Boots of Dawn"]


def test_toggle_on_does_not_duplicate_an_existing_entry():
    assert toggle_drop_in_keep_list(["amber"], "Amber", True) == ["Amber"]


def test_toggle_off_removes_the_entry_case_insensitively():
    assert toggle_drop_in_keep_list(["amber", "Boots of Dawn"], "AMBER", False) == ["Boots of Dawn"]


def test_toggle_off_leaves_a_broader_keyword_alone():
    # "hat" keeps "Witch Hat" by partial match; unticking the item must not delete it.
    assert toggle_drop_in_keep_list(["hat"], "Witch Hat", False) == ["hat"]


def test_toggle_off_missing_entry_is_a_no_op():
    assert toggle_drop_in_keep_list(["Amber"], "Boots of Dawn", False) == ["Amber"]


# ---------------------------------------------------------------------------
# set_drops_in_keep_list — the bulk form
# ---------------------------------------------------------------------------


def test_bulk_add_appends_every_name():
    assert set_drops_in_keep_list(["hat"], ["Amber", "Boots of Dawn"], True) == ["hat", "Amber", "Boots of Dawn"]


def test_bulk_add_does_not_duplicate_existing_entries():
    assert set_drops_in_keep_list(["amber"], ["Amber", "Cloak"], True) == ["Amber", "Cloak"]


def test_bulk_remove_drops_every_name_case_insensitively():
    assert set_drops_in_keep_list(["Amber", "hat", "Cloak"], ["AMBER", "cloak"], False) == ["hat"]


def test_bulk_remove_leaves_broader_keywords_alone():
    assert set_drops_in_keep_list(["hat"], ["Witch Hat"], False) == ["hat"]


def test_bulk_with_no_names_is_a_no_op():
    assert set_drops_in_keep_list(["Amber"], [], True) == ["Amber"]
    assert set_drops_in_keep_list(["Amber"], [], False) == ["Amber"]


def test_bulk_matches_repeated_singular_toggles():
    """The single-name and bulk forms must not disagree — the picker uses both."""
    names = ["Amber", "Boots of Dawn", "Cloak"]
    one_at_a_time = ["hat"]
    for name in names:
        one_at_a_time = toggle_drop_in_keep_list(one_at_a_time, name, True)
    assert one_at_a_time == set_drops_in_keep_list(["hat"], names, True)


# ---------------------------------------------------------------------------
# category_keep_reasons
#
# sell_pets is the one sell_* bool that is not a name match: the node deselects
# the whole Pet tab, so no keep-list keyword describes it. The picker needs to be
# told separately, or it shows a pet as "will be sold" while the config says the
# opposite.
# ---------------------------------------------------------------------------


def test_no_categories_are_spared_by_default():
    assert category_keep_reasons({}) == {}


def test_selling_pets_spares_nothing():
    assert category_keep_reasons({"sell_pets": True}) == {}


def test_not_selling_pets_spares_the_pets_category():
    assert set(category_keep_reasons({"sell_pets": False})) == {"Pets"}


def test_the_reason_names_the_param_that_causes_it():
    # The user has to know where to undo it, and it is not the keep list.
    assert category_keep_reasons({"sell_pets": False})["Pets"] == "Kept because Sell Pets is off, not by the keep list."


def test_an_omitted_param_uses_the_schema_default():
    # sell_pets defaults to True, unlike the other sell_* bools, so an absent
    # param must not read as "not selling".
    assert category_keep_reasons({"keep_list": []}) == {}


def test_category_flags_do_not_leak_into_the_keyword_list():
    # A spared category is not expressible as a keyword, so it must not appear as
    # one — the run loop would then match "pets" against item names.
    assert effective_keep_list({**_SELL_ALL, "sell_pets": False}) == []


# ---------------------------------------------------------------------------
# category_unmatchable_reasons
#
# A pet's quicksell row is the nickname its owner gave it, never the body type
# the drop table lists. No keyword bridges that, so the picker has to say so
# instead of showing a ticked pet as safe.
# ---------------------------------------------------------------------------


def test_pets_are_unmatchable_while_they_are_being_sold():
    assert set(category_unmatchable_reasons({"sell_pets": True})) == {"Pets"}


def test_pets_are_unmatchable_by_default():
    # sell_pets defaults to True, so an absent param is the selling case.
    assert set(category_unmatchable_reasons({})) == {"Pets"}


def test_nothing_is_unmatchable_once_the_bool_keeps_it():
    # The row is safely kept, so there is nothing to warn about — and warning
    # anyway would contradict the "Kept because Sell Pets is off" tooltip.
    assert category_unmatchable_reasons({"sell_pets": False}) == {}


def test_the_reason_names_the_mismatch_and_the_way_out():
    reason = category_unmatchable_reasons({})["Pets"]
    assert "nickname" in reason and "Sell Pets" in reason
    assert reason == UNMATCHABLE_CATEGORIES["Pets"]


def test_unmatchable_categories_are_all_sellable():
    # A category the node never sells needs no warning; it would only be noise.
    assert set(UNMATCHABLE_CATEGORIES).isdisjoint(NEVER_QUICKSOLD_CATEGORIES)


# ---------------------------------------------------------------------------
# resolve_options
#
# The five settings are bot options now, so the node asks for them by name rather
# than reading a params block.  This is the one place the option layer touches
# quicksell; everything above it sees the plain dict this produces.
# ---------------------------------------------------------------------------


def _answers(values: dict):
    """A get_option stand-in that knows *values* and nothing else."""
    return lambda name, default=None: values.get(name, default)


def test_the_options_collapse_to_the_dict_the_pure_helpers_read():
    params = resolve_options(
        _answers(
            {
                OPTION_ENABLED: True,
                OPTION_KEEP_LIST: ["Amber"],
                SELL_FLAG_OPTIONS["sell_pets"]: False,
                SELL_FLAG_OPTIONS["sell_socket_wrenches"]: True,
                SELL_FLAG_OPTIONS["sell_skeleton_keys"]: True,
            }
        )
    )
    assert params == {
        "enabled": True,
        "keep_list": ["Amber"],
        "sell_pets": False,
        "sell_socket_wrenches": True,
        "sell_skeleton_keys": True,
    }


def test_an_unanswered_option_falls_back_to_its_floor():
    # Nothing pure may go and read the settings, so these are the floor rather than
    # the answer: the four levels above them are get_option's business.
    params = resolve_options(_answers({}))
    assert params["enabled"] is True
    assert params["keep_list"] == []
    for key, floor in SELL_FLAG_DEFAULTS.items():
        assert params[key] is floor


def test_a_value_of_the_wrong_type_falls_back_rather_than_being_believed():
    # An override written under an older declaration, or a stub answering everything
    # with a number. Either way the fallback must be the floor, never the more
    # destructive reading of a truthy value.
    params = resolve_options(_answers(dict.fromkeys(SELL_FLAG_OPTIONS.values(), 1) | {OPTION_KEEP_LIST: "Amber"}))
    assert params["sell_pets"] is True
    assert params["sell_socket_wrenches"] is False
    assert params["keep_list"] == []


def test_enable_quicksell_off_survives_resolution():
    assert resolve_options(_answers({OPTION_ENABLED: False}))["enabled"] is False


def test_describe_config_names_every_answer_and_the_effective_keep_list():
    line = describe_config(
        {"enabled": True, "keep_list": ["Amber"], "sell_socket_wrenches": False, "sell_skeleton_keys": True}
    )
    assert "enabled=True" in line
    # The implied keyword is in it, because that is what the node will match on.
    assert "'amber'" in line and "'socket wrench'" in line
    assert "sell_socket_wrenches=False" in line


def test_an_unset_flag_reaching_pure_logic_uses_the_floor():
    assert sell_flag({}, "sell_pets") is True
    assert sell_flag({}, "sell_socket_wrenches") is False
    assert sell_flag({"sell_pets": False}, "sell_pets") is False
