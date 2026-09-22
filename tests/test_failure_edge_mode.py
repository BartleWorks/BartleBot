"""The failure-edge toggle's third state, and the setting it grew out of.

The toggle was a bool. It is now three modes, because after the default landed the
interesting question is not "failure edges or not" but "is this edge saying
anything the default does not" -- see docs/RETURN_ROUTING.md.
"""

from bartlebot.gui.canvas.bot_canvas import (
    _FAILURE_EDGE_CYCLE,
    _FAILURE_EDGE_ICONS,
    _FAILURE_EDGE_TOOLTIPS,
    FAILURE_EDGES_ALL,
    FAILURE_EDGES_HIDE_RECOVERY,
    FAILURE_EDGES_NONE,
    _stored_failure_edge_mode,
)


class _FakeSettings:
    def __init__(self, values: dict):
        self._values = values

    def get(self, _section, key, default=None):
        return self._values.get(key, default)


def test_a_saved_mode_is_used_as_is():
    sm = _FakeSettings({"failure_edge_mode": FAILURE_EDGES_HIDE_RECOVERY})
    assert _stored_failure_edge_mode(sm) == FAILURE_EDGES_HIDE_RECOVERY


def test_an_old_settings_file_with_edges_hidden_keeps_them_hidden():
    """The bool is all a pre-upgrade settings.json has, and False meant hidden."""
    sm = _FakeSettings({"show_failure_edges": False})
    assert _stored_failure_edge_mode(sm) == FAILURE_EDGES_NONE


def test_an_old_settings_file_with_edges_shown_shows_them_all():
    sm = _FakeSettings({"show_failure_edges": True})
    assert _stored_failure_edge_mode(sm) == FAILURE_EDGES_ALL


def test_an_unrecognised_mode_falls_back_rather_than_raising():
    """A hand-edited settings.json must not take the canvas down with it."""
    sm = _FakeSettings({"failure_edge_mode": "sideways", "show_failure_edges": True})
    assert _stored_failure_edge_mode(sm) == FAILURE_EDGES_ALL


def test_every_mode_has_a_glyph_and_a_tooltip():
    """The button carries no label, so the icon is the only thing reporting the state."""
    assert set(_FAILURE_EDGE_CYCLE) == set(_FAILURE_EDGE_ICONS) == set(_FAILURE_EDGE_TOOLTIPS)
    assert len(set(_FAILURE_EDGE_ICONS.values())) == len(_FAILURE_EDGE_CYCLE)


def test_the_cycle_returns_to_where_it_started():
    assert _FAILURE_EDGE_CYCLE[0] == FAILURE_EDGES_ALL
    assert len(_FAILURE_EDGE_CYCLE) == 3
