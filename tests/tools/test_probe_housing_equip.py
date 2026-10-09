"""Unit tests for the pure analysis in tools/probe_housing_equip.py.

The probe needs a live client, but its two analysis functions are what a reader will act on,
and acting on a wrong answer here changes somebody's equipped house. The rules are pinned
here so a misreported candidate is caught before it reaches handle_potions.
"""

from tools.probe_housing_equip import ABSENT, describe_rows, diff_states

_FIST = "FurnitureSpellbookPage/Item_1/fist"
_ROW = "FurnitureSpellbookPage/Item_1"


def test_reports_a_property_that_changed():
    before = {_FIST: {"is_visible": "True"}}
    after = {_FIST: {"is_visible": "False"}}

    assert diff_states(before, after) == [(_FIST, "is_visible", "True", "False")]


def test_ignores_properties_that_stayed_the_same():
    before = {_FIST: {"is_visible": "True", "alpha": "1.0"}}
    after = {_FIST: {"is_visible": "False", "alpha": "1.0"}}

    assert diff_states(before, after) == [(_FIST, "is_visible", "True", "False")]


def test_a_window_that_disappears_is_reported_not_dropped():
    """'Absent when unequipped' is the most useful answer the probe could give."""
    before = {_FIST: {"is_visible": "True"}}
    after: dict[str, dict[str, str]] = {}

    assert diff_states(before, after) == [(_FIST, "is_visible", "True", ABSENT)]


def test_fist_boolean_outranks_other_findings():
    """The hypothesis under test sorts first, and a bool beats a rectangle."""
    before = {
        _ROW: {"window_rectangle": "Rect(0,0,1,1)"},
        _FIST: {"is_visible": "True", "window_rectangle": "Rect(0,0,2,2)"},
    }
    after = {
        _ROW: {"window_rectangle": "Rect(0,0,9,9)"},
        _FIST: {"is_visible": "False", "window_rectangle": "Rect(0,0,8,8)"},
    }

    ordered = [(path, prop) for path, prop, _, _ in diff_states(before, after)]

    assert ordered[0] == (_FIST, "is_visible")
    assert ordered[1] == (_FIST, "window_rectangle")


def test_is_visible_is_recommended_over_is_control_grayed():
    """Both are booleans, so the tie must not be broken by alphabetical order.

    is_visible has a ready-made node_helpers predicate and an obvious meaning; recommending
    is_control_grayed instead just because 'c' sorts before 'v' would be a worse answer for
    no reason.
    """
    before = {_FIST: {"is_visible": "True", "is_control_grayed": "False"}}
    after = {_FIST: {"is_visible": "False", "is_control_grayed": "True"}}

    assert diff_states(before, after)[0][1] == "is_visible"


def test_no_changes_reports_nothing():
    state = {_FIST: {"is_visible": "True"}}

    assert diff_states(state, state) == []


def test_describe_rows_finds_the_name_label():
    dump = {
        "FurnitureSpellbookPage": {"maybe_text": "''"},
        _ROW: {"maybe_text": "''"},
        f"{_ROW}/ItemName": {"maybe_text": "'Red Barn Farm'"},
        "FurnitureSpellbookPage/Item_2/ItemName": {"maybe_text": "'Celestial Observatory'"},
    }

    assert describe_rows(dump) == [
        (f"{_ROW}/ItemName", "'Red Barn Farm'"),
        ("FurnitureSpellbookPage/Item_2/ItemName", "'Celestial Observatory'"),
    ]


def test_describe_rows_ignores_page_furniture():
    """Only Item_N subtrees count — a title bar with text is not a castle name."""
    dump = {
        "FurnitureSpellbookPage/TabBackground": {"maybe_text": "'Castles'"},
        f"{_ROW}/ItemName": {"maybe_text": "'Red Barn Farm'"},
    }

    assert describe_rows(dump) == [(f"{_ROW}/ItemName", "'Red Barn Farm'")]


def test_describe_rows_skips_empty_and_absent_text():
    dump = {
        _ROW: {"maybe_text": "''"},
        f"{_ROW}/fist": {"is_visible": "True"},
        f"{_ROW}/ItemName": {"maybe_text": "'Red Barn Farm'"},
    }

    assert describe_rows(dump) == [(f"{_ROW}/ItemName", "'Red Barn Farm'")]
