"""Tests for AppliesToDialog — the only place group membership is edited.

Real widget tests (QApplication required): what is under test is the wiring —
which boxes start checked, what each row says it would take, and the guard that
stops OK turning an empty list into an unlabelled delete.
"""

from __future__ import annotations

import sys

import pytest
from PyQt6.QtWidgets import QApplication, QDialogButtonBox

from bartlebot.bot.schema import StateModel
from bartlebot.gui.sidebar.applies_to_dialog import AppliesToDialog


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def _exposed() -> list[StateModel]:
    return [
        StateModel(id="state_1", node_type="handle_combat", label="Tse-Tse Snaketail"),
        StateModel(id="state_2", node_type="handle_combat", label="Spectral Guardians"),
        StateModel(id="state_3", node_type="handle_combat", label="Defeat Balaur"),
    ]


def _dialog(chosen: list[str], describe=lambda sid: "(bot default)") -> AppliesToDialog:
    return AppliesToDialog("Scald Incindiate", _exposed(), chosen, describe)


def _ok(dlg: AppliesToDialog):
    return dlg._buttons.button(QDialogButtonBox.StandardButton.Ok)


def test_the_group_s_current_fights_start_checked(qapp):
    dlg = _dialog(["state_1", "state_2"])

    assert dlg.selected_ids() == ["state_1", "state_2"]


def test_every_exposed_fight_is_offered(qapp):
    dlg = _dialog(["state_1"])

    assert list(dlg._boxes) == ["state_1", "state_2", "state_3"]


def test_checking_a_fight_adds_it_to_the_selection(qapp):
    dlg = _dialog(["state_1"])

    dlg._boxes["state_3"].setChecked(True)

    assert dlg.selected_ids() == ["state_1", "state_3"]


def test_selection_follows_the_order_fights_were_listed_in(qapp):
    dlg = _dialog([])
    dlg._boxes["state_3"].setChecked(True)
    dlg._boxes["state_1"].setChecked(True)

    assert dlg.selected_ids() == ["state_1", "state_3"]


# ---------------------------------------------------------------------------
# "currently:" — what checking a box would take
# ---------------------------------------------------------------------------


def test_unowned_fights_say_what_they_are_bound_to(qapp):
    """Without this, checking a box silently destroys a config the user built."""
    dlg = _dialog(["state_1"], describe=lambda sid: {"state_2": "Other Strategy"}.get(sid, "(bot default)"))

    texts = [lbl.full_text() for lbl in dlg._current_lbls]
    assert "currently: Other Strategy" in texts
    assert "currently: (bot default)" in texts


def test_fights_this_group_already_owns_say_nothing(qapp):
    """A checked fight has nothing to be taken from."""
    dlg = _dialog(["state_1", "state_2", "state_3"])

    assert dlg._current_lbls == []


# ---------------------------------------------------------------------------
# The empty-selection guard
# ---------------------------------------------------------------------------


def test_ok_is_disabled_when_nothing_is_checked(qapp):
    """A group covering no fights is a deletion; there are labelled paths for that."""
    dlg = _dialog([])

    assert not _ok(dlg).isEnabled()


def test_ok_is_enabled_once_a_fight_is_checked(qapp):
    dlg = _dialog([])

    dlg._boxes["state_2"].setChecked(True)

    assert _ok(dlg).isEnabled()


def test_unchecking_the_last_fight_disables_ok_again(qapp):
    dlg = _dialog(["state_1"])
    assert _ok(dlg).isEnabled()

    dlg._boxes["state_1"].setChecked(False)

    assert not _ok(dlg).isEnabled()


def test_the_disabled_ok_says_why(qapp):
    dlg = _dialog([])

    assert "at least one" in _ok(dlg).toolTip()


def test_an_enabled_ok_carries_no_stale_reason(qapp):
    dlg = _dialog([])
    dlg._boxes["state_1"].setChecked(True)

    assert _ok(dlg).toolTip() == ""
