"""Tests for ElidedLabel — the widget that keeps long text from widening a sidebar."""

from __future__ import annotations

import sys

import pytest
from PyQt6.QtWidgets import QApplication, QLabel, QSizePolicy

from bartlebot.gui.widgets.elided_label import ElidedLabel, apply_muted_color

_LONG = "P1: {1} shatter@boss {2} empower… · P2–P3: {1} pass {2} pass · P4: {1} weakness@boss"


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def test_does_not_demand_the_full_text_width(qapp):
    """The whole point: a plain QLabel's minimum width is its full text width."""
    plain = QLabel(_LONG)
    elided = ElidedLabel(_LONG)

    assert plain.minimumSizeHint().width() > 400  # guards the premise
    assert elided.minimumSizeHint().width() < 50


def test_policy_is_shrinkable_so_the_minimum_hint_is_what_counts(qapp):
    """qSmartMinSize uses minimumSizeHint alone only for ShrinkFlag policies.

    Without ShrinkFlag it would take max(sizeHint, minimumSizeHint) instead, and
    the full text width would come straight back.
    """
    policy = ElidedLabel("x").sizePolicy().horizontalPolicy()

    assert policy.value & QSizePolicy.PolicyFlag.ShrinkFlag.value


def test_size_hint_still_asks_for_the_full_width(qapp):
    """Shrink under pressure, but take the natural width when the layout has room.

    An Ignored policy would also zero the minimum, but it discards the size hint
    too — a label with no stretch factor would then collapse to nothing even in
    a wide panel.
    """
    lbl = ElidedLabel(_LONG)

    assert lbl.sizeHint().width() > 400
    assert lbl.minimumSizeHint().width() < 50


def test_keeps_the_full_text_available(qapp):
    lbl = ElidedLabel(_LONG)

    assert lbl.full_text() == _LONG
    assert lbl.text() == _LONG  # so callers reading .text() still see everything
    assert lbl.toolTip() == _LONG


def test_set_text_updates_full_text_and_tooltip(qapp):
    lbl = ElidedLabel("first")
    lbl.setText("second and rather longer")

    assert lbl.full_text() == "second and rather longer"
    assert lbl.toolTip() == "second and rather longer"


def test_empty_text_is_handled(qapp):
    lbl = ElidedLabel()

    assert lbl.full_text() == ""
    assert lbl.toolTip() == ""


def test_none_text_does_not_crash(qapp):
    lbl = ElidedLabel("x")
    lbl.setText(None)

    assert lbl.full_text() == ""


def test_paints_without_error_at_a_narrow_width(qapp):
    """Elision happens in paintEvent, so exercise it rather than trusting the hint."""
    lbl = ElidedLabel(_LONG)
    lbl.resize(40, 16)
    lbl.grab()  # forces a real paint cycle


def test_apply_muted_color_uses_the_palette_not_a_stylesheet(qapp):
    """ElidedLabel paints its own text, so a QSS color rule would never reach it."""
    from bartlebot.gui.theme import THEME

    lbl = ElidedLabel("x")
    apply_muted_color(lbl)

    assert lbl.styleSheet() == ""
    assert lbl.palette().color(lbl.foregroundRole()).name().lower() == THEME.text_muted.lower()
