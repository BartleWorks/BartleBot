"""Unit tests for the tab-indicator search in tools/probe_group_tab.py.

The probe itself needs a live game client, but its analysis is pure and is the part that
can actually mislead: GroupPageNumber was adopted on exactly this kind of reasoning and
turned out to be visible on every tab. A probe that reports a false candidate costs another
round of building on a wrong premise, so the selection rules are pinned here.
"""

from tools.probe_group_tab import ABSENT, find_candidates

GROUP = "Tab_Group"
RECHECK = "Tab_Group (recheck)"


def _snapshots(group: dict, recheck: dict | None = None, **others: dict) -> dict:
    """Build a snapshots mapping; recheck defaults to an identical second Group sample."""
    return {GROUP: group, RECHECK: recheck if recheck is not None else group, **others}


def test_reports_a_property_unique_to_the_group_tab():
    snaps = _snapshots(
        {"GroupLayout": {"is_visible": "True"}},
        Tab_Friends={"GroupLayout": {"is_visible": "False"}},
    )
    candidates, _ = find_candidates(snaps, GROUP, RECHECK)
    assert candidates == [("GroupLayout", "is_visible", "True")]


def test_rejects_a_property_shared_with_any_other_tab():
    """The GroupPageNumber failure: identical everywhere, so it discriminates nothing."""
    snaps = _snapshots(
        {"GroupLayout/GroupPageNumber": {"is_visible": "True"}},
        Tab_Friends={"GroupLayout/GroupPageNumber": {"is_visible": "True"}},
    )
    candidates, _ = find_candidates(snaps, GROUP, RECHECK)
    assert candidates == []


def test_rejects_a_property_that_matches_only_some_other_tabs():
    """Separating Group from Friends is not enough if Ignore looks like Group too.

    This is the subtle failure a two-state A/B probe cannot see, and the reason the sweep
    covers every tab rather than one representative other tab.
    """
    snaps = _snapshots(
        {"Page": {"is_visible": "True"}},
        Tab_Friends={"Page": {"is_visible": "False"}},
        Tab_Ignore={"Page": {"is_visible": "True"}},
    )
    candidates, _ = find_candidates(snaps, GROUP, RECHECK)
    assert candidates == []


def test_drops_pairs_that_moved_between_the_two_group_samples():
    """A value that drifts across one sweep is animation, not a tab indicator."""
    snaps = _snapshots(
        {"Page": {"alpha": "1.0"}},
        recheck={"Page": {"alpha": "0.98"}},
        Tab_Friends={"Page": {"alpha": "0.0"}},
    )
    candidates, unstable = find_candidates(snaps, GROUP, RECHECK)
    assert candidates == []
    assert ("Page", "alpha") in unstable


def test_a_window_present_only_on_the_group_tab_is_a_candidate():
    """Absence elsewhere is the strongest signal available, so it must not be skipped."""
    snaps = _snapshots(
        {"GroupOnlyThing": {"is_visible": "True"}},
        Tab_Friends={},
    )
    candidates, _ = find_candidates(snaps, GROUP, RECHECK)
    assert ("GroupOnlyThing", "is_visible", "True") in candidates


def test_a_window_absent_only_on_the_group_tab_is_a_candidate():
    """Absence ON the Group tab discriminates just as well, and reports as <absent>."""
    snaps = _snapshots(
        {},
        Tab_Friends={"FriendsOnlyThing": {"is_visible": "True"}},
    )
    candidates, _ = find_candidates(snaps, GROUP, RECHECK)
    assert ("FriendsOnlyThing", "is_visible", ABSENT) in candidates


def test_boolean_properties_are_ranked_above_the_rest():
    """The caller wants a predicate; a rectangle that happens to differ is a worse bet."""
    snaps = _snapshots(
        {"Page": {"window_rectangle": "Rect(0,0,10,10)", "is_visible": "True"}},
        Tab_Friends={"Page": {"window_rectangle": "Rect(0,0,20,20)", "is_visible": "False"}},
    )
    candidates, _ = find_candidates(snaps, GROUP, RECHECK)
    assert [prop for _, prop, _ in candidates] == ["is_visible", "window_rectangle"]


def test_a_getter_that_only_raises_on_other_tabs_still_counts():
    """A property readable only on the Group tab discriminates, however ugly."""
    snaps = _snapshots(
        {"Page": {"maybe_checked": "True"}},
        Tab_Friends={"Page": {"maybe_checked": "<ValueError>"}},
    )
    candidates, _ = find_candidates(snaps, GROUP, RECHECK)
    assert candidates == [("Page", "maybe_checked", "True")]


def test_missing_recheck_snapshot_falls_back_to_the_group_sample():
    """A sweep cut short still analyses, just without the stability filter."""
    snaps = {GROUP: {"Page": {"is_visible": "True"}}, "Tab_Friends": {"Page": {"is_visible": "False"}}}
    candidates, unstable = find_candidates(snaps, GROUP, RECHECK)
    assert candidates == [("Page", "is_visible", "True")]
    assert unstable == set()


def test_no_other_tabs_yields_nothing_rather_than_everything():
    """With only the Group samples, every pair is trivially "unique" — that must not pass."""
    snaps = _snapshots({"Page": {"is_visible": "True"}})
    candidates, _ = find_candidates(snaps, GROUP, RECHECK)
    assert candidates == []
