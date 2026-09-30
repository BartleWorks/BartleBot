"""Tests for the UI Tree Browser's state columns.

The decision logic is extracted as module-level pure functions so most of it is
testable without a QApplication, per CLAUDE.md's convention for QWidget code.
The one widget-backed test covers the filter rule that is easy to get wrong:
an invisible parent must survive as a spine to its visible children.
"""

import pytest
from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem

from bartlebot.gui.ui_tree_browser import (
    _COL_ELEMENT,
    _TEXT_ROLE,
    _VISIBLE_ROLE,
    apply_filter,
    decode_flags,
    format_state,
    format_text,
    has_grayed_field,
    row_matches,
    subtree_items,
)

# Raw m_Flags bit values (wizwalker WindowFlags).
_VISIBLE_BIT = 1
_DISABLED_BIT = 2147483648
_NOCLIP_BIT = 2


# ----------------------------------------------------------------------
# decode_flags
# ----------------------------------------------------------------------


def test_decode_flags_visible_and_enabled():
    assert decode_flags(_VISIBLE_BIT) == (True, True)


def test_decode_flags_disabled_only():
    assert decode_flags(_DISABLED_BIT) == (False, False)


def test_decode_flags_visible_but_disabled():
    assert decode_flags(_VISIBLE_BIT | _DISABLED_BIT) == (True, False)


def test_decode_flags_neither_bit_set():
    # Zero is a real value: hidden, but not explicitly disabled.
    assert decode_flags(0) == (False, True)


def test_decode_flags_ignores_unrelated_bits():
    assert decode_flags(_VISIBLE_BIT | _NOCLIP_BIT) == (True, True)


# ----------------------------------------------------------------------
# has_grayed_field
# ----------------------------------------------------------------------


@pytest.mark.parametrize("type_name", ["ControlButton", "ControlCheckBox", "BadgeButton", "SpellCheckBox"])
def test_has_grayed_field_true_for_button_classes(type_name):
    assert has_grayed_field(type_name) is True


@pytest.mark.parametrize("type_name", ["Window", "ControlText", "", "ControlSprite"])
def test_has_grayed_field_false_for_classes_without_it(type_name):
    """Reading offset 808 on these returns unrelated bytes, so they must be skipped."""
    assert has_grayed_field(type_name) is False


# ----------------------------------------------------------------------
# format_state
# ----------------------------------------------------------------------


def test_format_state_distinguishes_false_from_absent():
    assert format_state(True) == "true"
    assert format_state(False) == "false"
    assert format_state(None) == "—"
    assert format_state(None) != format_state(False)


# ----------------------------------------------------------------------
# row_matches
# ----------------------------------------------------------------------


def test_row_matches_no_filter_keeps_everything():
    assert row_matches("[wndFriendsList]  Window", False, "", False) is True


def test_row_matches_text_only():
    assert row_matches("[wndFriendsList]  Window", False, "friends", False) is True
    assert row_matches("[wndFriendsList]  Window", True, "combat", False) is False


def test_row_matches_text_is_case_insensitive_on_the_row():
    assert row_matches("[wndFriendsList]  Window", True, "friendslist", False) is True


def test_row_matches_hide_invisible_only():
    assert row_matches("[btnClose]  ControlButton", True, "", True) is True
    assert row_matches("[btnClose]  ControlButton", False, "", True) is False


def test_row_matches_hide_invisible_keeps_unknown_visibility():
    """A node whose flags read failed shows None; drop it only on a real False."""
    assert row_matches("[btnClose]  ControlButton", None, "", True) is True


def test_row_matches_both_criteria_must_hold():
    assert row_matches("[btnClose]  ControlButton", True, "btnclose", True) is True
    assert row_matches("[btnClose]  ControlButton", False, "btnclose", True) is False
    assert row_matches("[btnClose]  ControlButton", True, "friends", True) is False


# ----------------------------------------------------------------------
# The spine rule, against a real QTreeWidget
# ----------------------------------------------------------------------


def _make_item(tree_or_parent, label: str, visible: bool | None, window_text: str | None = None):
    """A tree row.  ``window_text=None`` is a row whose text has not been read yet."""
    item = QTreeWidgetItem(tree_or_parent, [label, window_text or "", "", "", ""])
    item.setData(_COL_ELEMENT, _VISIBLE_ROLE, visible)
    item.setData(_COL_ELEMENT, _TEXT_ROLE, window_text)
    return item


def test_hide_invisible_keeps_invisible_parent_of_visible_child(qapp):
    """The wndFriendsList case: is_visible() ignores parents, so the container
    reads False while its child button reads True.  Hiding the container would
    hide the very row the user is looking for."""
    tree = QTreeWidget()
    tree.setColumnCount(5)
    parent = _make_item(tree, "[wndFriendsList]  Window", False)
    child = _make_item(parent, "[btnFriendListClose]  ControlButton", True)

    apply_filter(tree.invisibleRootItem(), "", True)

    assert parent.isHidden() is False
    assert child.isHidden() is False
    assert parent.isExpanded() is True


def test_hide_invisible_hides_a_wholly_invisible_subtree(qapp):
    tree = QTreeWidget()
    tree.setColumnCount(5)
    parent = _make_item(tree, "[wndHidden]  Window", False)
    child = _make_item(parent, "[btnHidden]  ControlButton", False)

    apply_filter(tree.invisibleRootItem(), "", True)

    assert parent.isHidden() is True
    assert child.isHidden() is True


def test_text_filter_keeps_matching_child_and_its_parent(qapp):
    tree = QTreeWidget()
    tree.setColumnCount(5)
    parent = _make_item(tree, "[wndRoot]  Window", True)
    match = _make_item(parent, "[btnFriendListClose]  ControlButton", True)
    other = _make_item(parent, "[btnCombat]  ControlButton", True)

    apply_filter(tree.invisibleRootItem(), "friendlist", False)

    assert parent.isHidden() is False
    assert match.isHidden() is False
    assert other.isHidden() is True


# ----------------------------------------------------------------------
# format_text
# ----------------------------------------------------------------------


def test_format_text_shows_a_dash_for_windows_without_text():
    assert format_text("") == "—"


def test_format_text_leaves_unread_text_blank():
    """An unread row must not look like a row that carries no text."""
    assert format_text(None) == ""
    assert format_text(None) != format_text("")


def test_format_text_passes_short_text_through():
    assert format_text("Krokotopia") == "Krokotopia"


def test_format_text_truncates_a_paragraph():
    """Some windows hold whole lines of dialogue; the column must survive them."""
    rendered = format_text("x" * 200)
    assert len(rendered) < 200
    assert rendered.endswith("…")


# ----------------------------------------------------------------------
# Filtering on the Text column
#
# A menu of identically-named rows is findable by the label it displays and by
# nothing else, so the filter has to look at the window text too.
# ----------------------------------------------------------------------


def test_row_matches_finds_a_row_by_its_window_text():
    assert row_matches("[Row]  Window", True, "krokotopia", False, "Krokotopia") is True


def test_row_matches_still_rejects_a_row_matching_neither_column():
    assert row_matches("[Row]  Window", True, "mooshu", False, "Krokotopia") is False


def test_row_matches_ignores_text_column_when_empty():
    assert row_matches("[Row]  Window", True, "row", False, "") is True


def test_text_filter_finds_one_of_several_identically_named_rows(qapp):
    tree = QTreeWidget()
    tree.setColumnCount(5)
    parent = _make_item(tree, "[wndMenu]  Window", True)
    krok = _make_item(parent, "[Row]  Window", True, "Krokotopia")
    marley = _make_item(parent, "[Row]  Window", True, "Marleybone")

    apply_filter(tree.invisibleRootItem(), "marleybone", False)

    assert marley.isHidden() is False
    assert krok.isHidden() is True
    assert parent.isHidden() is False


# ----------------------------------------------------------------------
# subtree_items — what Load Text reads
#
# Text is read on demand because reading it for every node froze the dialog on
# load.  This is the bound on how much one Load Text press can cost.
# ----------------------------------------------------------------------


def test_subtree_items_collects_the_node_and_its_descendants(qapp):
    tree = QTreeWidget()
    tree.setColumnCount(5)
    parent = _make_item(tree, "[wndMenu]  Window", True)
    row = _make_item(parent, "[Row]  Window", True)
    label = _make_item(row, "[Label]  ControlText", True)

    items, truncated = subtree_items(parent)

    assert items == [parent, row, label]
    assert truncated is False


def test_subtree_items_excludes_siblings_of_the_selected_node(qapp):
    tree = QTreeWidget()
    tree.setColumnCount(5)
    root = _make_item(tree, "[root]  Window", True)
    chosen = _make_item(root, "[wndMenu]  Window", True)
    _make_item(root, "[wndOther]  Window", True)

    items, _ = subtree_items(chosen)

    assert items == [chosen]


def test_subtree_items_is_breadth_first(qapp):
    """The rows under the selected node matter more than one row's internals."""
    tree = QTreeWidget()
    tree.setColumnCount(5)
    parent = _make_item(tree, "[wndMenu]  Window", True)
    first = _make_item(parent, "[Row]  Window", True)
    deep = _make_item(first, "[Label]  ControlText", True)
    second = _make_item(parent, "[Row]  Window", True)

    items, _ = subtree_items(parent)

    assert items.index(second) < items.index(deep)


def test_subtree_items_stops_at_the_limit(qapp):
    """Selecting the root row must not walk the whole tree — that is the freeze."""
    tree = QTreeWidget()
    tree.setColumnCount(5)
    parent = _make_item(tree, "[root]  Window", True)
    for _ in range(20):
        _make_item(parent, "[Row]  Window", True)

    items, truncated = subtree_items(parent, limit=5)

    assert len(items) == 5
    assert truncated is True
