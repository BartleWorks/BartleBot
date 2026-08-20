"""Tests for the quicksell keep-list logic shared by the node and the properties panel."""

from bartlebot.nodes.utility.quicksell import (
    drop_keep_states,
    effective_keep_list,
    matching_keyword,
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
