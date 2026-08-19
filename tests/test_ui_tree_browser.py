"""Tests for the UI Tree Browser's state columns.

The decision logic is extracted as module-level pure functions so most of it is
testable without a QApplication, per CLAUDE.md's convention for QWidget code.
The one widget-backed test covers the filter rule that is easy to get wrong:
an invisible parent must survive as a spine to its visible children.
"""

import sys

import pytest
from PyQt6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem

from bartlebot.gui.ui_tree_browser import (
    _COL_ELEMENT,
    _VISIBLE_ROLE,
    apply_filter,
    decode_flags,
    format_state,
    has_greyed_field,
    row_matches,
)

# Raw m_Flags bit values (wizwalker WindowFlags).
_VISIBLE_BIT = 1
_DISABLED_BIT = 2147483648
_NOCLIP_BIT = 2


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


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
# has_greyed_field
# ----------------------------------------------------------------------


@pytest.mark.parametrize("type_name", ["ControlButton", "ControlCheckBox", "BadgeButton", "SpellCheckBox"])
def test_has_greyed_field_true_for_button_classes(type_name):
    assert has_greyed_field(type_name) is True


@pytest.mark.parametrize("type_name", ["Window", "ControlText", "", "ControlSprite"])
def test_has_greyed_field_false_for_classes_without_it(type_name):
    """Reading offset 808 on these returns unrelated bytes, so they must be skipped."""
    assert has_greyed_field(type_name) is False


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


def _make_item(tree_or_parent, label: str, visible: bool | None):
    item = QTreeWidgetItem(tree_or_parent, [label, "", "", ""])
    item.setData(_COL_ELEMENT, _VISIBLE_ROLE, visible)
    return item


def test_hide_invisible_keeps_invisible_parent_of_visible_child(qapp):
    """The wndFriendsList case: is_visible() ignores parents, so the container
    reads False while its child button reads True.  Hiding the container would
    hide the very row the user is looking for."""
    tree = QTreeWidget()
    tree.setColumnCount(4)
    parent = _make_item(tree, "[wndFriendsList]  Window", False)
    child = _make_item(parent, "[btnFriendListClose]  ControlButton", True)

    apply_filter(tree.invisibleRootItem(), "", True)

    assert parent.isHidden() is False
    assert child.isHidden() is False
    assert parent.isExpanded() is True


def test_hide_invisible_hides_a_wholly_invisible_subtree(qapp):
    tree = QTreeWidget()
    tree.setColumnCount(4)
    parent = _make_item(tree, "[wndHidden]  Window", False)
    child = _make_item(parent, "[btnHidden]  ControlButton", False)

    apply_filter(tree.invisibleRootItem(), "", True)

    assert parent.isHidden() is True
    assert child.isHidden() is True


def test_text_filter_keeps_matching_child_and_its_parent(qapp):
    tree = QTreeWidget()
    tree.setColumnCount(4)
    parent = _make_item(tree, "[wndRoot]  Window", True)
    match = _make_item(parent, "[btnFriendListClose]  ControlButton", True)
    other = _make_item(parent, "[btnCombat]  ControlButton", True)

    apply_filter(tree.invisibleRootItem(), "friendlist", False)

    assert parent.isHidden() is False
    assert match.isHidden() is False
    assert other.isHidden() is True
