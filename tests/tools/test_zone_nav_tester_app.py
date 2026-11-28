"""Unit tests for the zone navigation tester: its geometry helper, Copy XYZ, and map reloading.

_find_clear_point (the live teleport-and-poll loop) is not covered here, the same way
app.py's other live-client glue (_read_sample, _maintain_speed) is not: it needs a real
client. retreat_points is the pure part factored out of it, so it is what gets tested.
Copy XYZ reads only the last sample, so a window with no client and no game data covers it.
Reloading is tested on a small made-up map in a temporary folder, never the shipped file.
"""

import json
import logging
import math
import os
from unittest.mock import MagicMock

import pytest
from PyQt6.QtTest import QTest

from bartlebot.game import zone_nav
from bartlebot.nodes import zone_navigation
from tools.zone_nav_tester import app, recorder


@pytest.fixture
def window(qapp):
    loggers = [logging.getLogger(name) for name in ("zone_nav_tester", "bartlebot")]
    before = [list(log.handlers) for log in loggers]
    built = app.TesterWindow(client=None, game=None)
    yield built
    # The log handlers write into this window's widgets, so they must not outlive it.
    for log, handlers in zip(loggers, before):
        log.handlers[:] = handlers
    # There is no client to unhook, so a close goes straight through.
    built._unhooked = True
    built.close()
    built.deleteLater()


def _sample(loading=False):
    return recorder.Sample(0.0, None if loading else "Test/Unmapped", (-14.876, -179.864, 89.2099), loading=loading)


def test_a_copied_position_is_written_as_zone_nav_json_writes_one():
    text = app.xyz_text((-14.876, -179.864, 89.2099))

    assert text == '{"x": -14.88, "y": -179.86, "z": 89.21}'
    assert json.loads(text) == {"x": -14.88, "y": -179.86, "z": 89.21}


def test_copy_xyz_puts_the_clients_position_on_the_clipboard(window, monkeypatch):
    clipboard = MagicMock()
    monkeypatch.setattr(app.QApplication, "clipboard", lambda: clipboard)
    assert not window.copy_xyz_button.isEnabled()

    window._show_sample(_sample())
    window.copy_xyz_button.click()

    clipboard.setText.assert_called_once_with('{"x": -14.88, "y": -179.86, "z": 89.21}')


def test_copy_xyz_is_off_while_the_client_loads(window):
    window._show_sample(_sample())
    window._show_sample(_sample(loading=True))

    assert not window.copy_xyz_button.isEnabled()


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


# ── Reloading the map ─────────────────────────────────────────────────────

HUB, STREET, ALLEY = "Mock/Hub", "Mock/Street", "Mock/Alley"


def _map_text(*zones, x=1.0):
    """A one-world map: the hub, `zones`, and a tentative walk from the hub to the first of them."""
    link = {"kind": "walk", "from": HUB, "to": zones[0], "xyz": {"x": x, "y": 0, "z": 0}}
    return zone_nav.dumps({"worlds": {"Mock": {"hub": HUB, "zones": {z: {} for z in (HUB, *zones)}, "links": [link]}}})


@pytest.fixture
def map_file(tmp_path, monkeypatch):
    path = tmp_path / "zone_nav.json"
    path.write_text(_map_text(STREET), encoding="utf-8")
    monkeypatch.setattr(zone_nav, "ZONE_NAV_PATH", path)
    zone_nav.load.cache_clear()
    yield path
    zone_nav.load.cache_clear()


@pytest.fixture
def mapped(map_file, window):
    """A window on `map_file`."""
    return window


def _zones_offered(window):
    return {window.zone_combo.itemData(i) for i in range(window.zone_combo.count())}


def _wait_for(condition):
    for _ in range(300):
        if condition():
            return True
        QTest.qWait(10)
    return False


def test_a_saved_edit_is_taken_up(map_file, mapped):
    assert _zones_offered(mapped) == {HUB, STREET}

    map_file.write_text(_map_text(STREET, ALLEY), encoding="utf-8")

    assert mapped.reload_map()
    assert _zones_offered(mapped) == {HUB, STREET, ALLEY}
    assert ALLEY in zone_nav.load().worlds["Mock"].zones


# A known flake (#349): the replace was refused once under -n auto (WinError 5, the file still
# open), and passes alone. See core's tests/CLAUDE.md, "Known flakes are marked".
@pytest.mark.flaky(reruns=2)
def test_the_file_is_watched_through_saves_that_replace_it(map_file, mapped):
    for zones in ((STREET, ALLEY), (STREET,), (ALLEY,)):
        scratch = map_file.with_suffix(".tmp")
        scratch.write_text(_map_text(*zones), encoding="utf-8")
        os.replace(scratch, map_file)

        expected = {HUB, *zones}
        assert _wait_for(lambda expected=expected: _zones_offered(mapped) == expected)


@pytest.mark.parametrize("bad", ['{"worlds":', '{"worlds": {}}', '{"worlds": {"Mock": []}}'])
def test_an_invalid_edit_leaves_the_map_in_use(map_file, mapped, bad):
    map_file.write_text(bad, encoding="utf-8")

    assert not mapped.reload_map()
    assert _zones_offered(mapped) == {HUB, STREET}
    assert STREET in zone_nav.load().worlds["Mock"].zones
    assert "invalid" in mapped.statusBar().currentMessage()

    map_file.write_text(_map_text(ALLEY), encoding="utf-8")

    assert mapped.reload_map()
    assert _zones_offered(mapped) == {HUB, ALLEY}
    assert "invalid" not in mapped.statusBar().currentMessage()


def test_undoing_an_invalid_edit_clears_the_warning(map_file, mapped):
    good = map_file.read_text(encoding="utf-8")
    map_file.write_text("{", encoding="utf-8")
    mapped.reload_map()

    map_file.write_text(good, encoding="utf-8")

    assert not mapped.reload_map()
    assert mapped.statusBar().currentMessage() == ""


def test_nothing_is_saved_over_an_invalid_edit(map_file, mapped):
    mapped.last_outcome = zone_navigation.Outcome(True, taken=list(zone_nav.load().links))
    map_file.write_text("{ half an edit", encoding="utf-8")

    mapped._mark_verified()

    assert map_file.read_text(encoding="utf-8") == "{ half an edit"


def test_a_preview_is_redone_on_the_edited_map(map_file, mapped):
    mapped.shown_zone = HUB
    mapped.game = MagicMock(neighbors=lambda _zone: set())
    mapped.tentative_check.setChecked(True)
    mapped.zone_combo.setCurrentIndex(mapped.zone_combo.findData(STREET))
    mapped.preview_button.click()
    assert "walk" in mapped.test_output.toPlainText()

    map_file.write_text(_map_text(ALLEY, STREET), encoding="utf-8")
    mapped.reload_map()

    assert "No route" in mapped.test_output.toPlainText()

    map_file.write_text(_map_text(ALLEY), encoding="utf-8")
    mapped.reload_map()

    assert "no longer on the map" in mapped.test_output.toPlainText()


@pytest.mark.parametrize(("x", "offered"), [(1.0, True), (2.0, False)])
def test_mark_verified_is_withdrawn_only_when_a_link_that_ran_is_edited(map_file, mapped, x, offered):
    mapped.last_outcome = zone_navigation.Outcome(True, taken=list(zone_nav.load().links))
    mapped.verify_button.setEnabled(True)

    # Either way the file changes: a zone is added, and x says whether the walk that ran moved.
    map_file.write_text(_map_text(STREET, ALLEY, x=x), encoding="utf-8")
    mapped.reload_map()

    assert mapped.verify_button.isEnabled() is offered
