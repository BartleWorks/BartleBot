"""Unit tests for the zone navigation tester's pure geometry helper.

_find_clear_point (the live teleport-and-poll loop) is not covered here, the same way
app.py's other live-client glue (_read_sample, _maintain_speed) is not: it needs a real
client. retreat_points is the pure part factored out of it, so it is what gets tested.
"""

import math

from tools.zone_nav_tester import app


def test_first_point_is_the_original_candidate():
    points = app.retreat_points((10.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    assert points[0] == (10.0, 0.0, 0.0)


def test_each_point_is_one_step_further_than_the_last():
    points = app.retreat_points((10.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    assert len(points) == app.RESET_VERIFY_MAX_STEPS
    for a, b in zip(points, points[1:]):
        assert math.dist(a, b) == app.RESET_VERIFY_STEP


def test_every_point_lies_on_the_line_through_away_from_and_candidate():
    away_from, candidate = (100.0, -50.0, 5.0), (140.0, -50.0, 5.0)
    points = app.retreat_points(candidate, away_from)
    for x, y, z in points:
        assert y == away_from[1]
        assert z == away_from[2]
        assert x >= candidate[0]  # every point retreats further out, none come back in


def test_a_zero_length_candidate_does_not_divide_by_zero():
    """candidate == away_from would otherwise divide by a zero-length vector."""
    points = app.retreat_points((5.0, 5.0, 5.0), (5.0, 5.0, 5.0))
    assert len(points) == app.RESET_VERIFY_MAX_STEPS
